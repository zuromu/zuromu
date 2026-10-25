#!/usr/bin/env python3
"""A free throw anyone can take from my profile.

Someone opens an issue titled "shoot 7" (any power from 1 to 10). The
free-throw workflow runs this script, which simulates the shot with real
physics, draws it as an animated SVG, updates the scoreboard in README.md and
writes the reply for the issue.

The physics: gravity, ball size and floor bounce are the same as swish-rl, the
hoop is regulation size (rim 3.05 m up, 4.19 m from the free-throw line), and
the ball can hit the front rim, back rim and backboard. Every shot wobbles a
little, like a real one, so the right power usually goes in but not always.

    python3 scripts/freethrow.py --demo 7     preview a shot in dist/, changes nothing else
    python3 scripts/freethrow.py shoot        what the workflow runs (reads TITLE, SHOOTER, ISSUE)
    python3 scripts/freethrow.py render       draws the court if there's no picture yet
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "free-throws.json"
DIST = ROOT / "dist"
README = ROOT / "README.md"
START, END = "<!-- freethrow:start -->", "<!-- freethrow:end -->"
PROFILE = "https://github.com/zuromu"
COOLDOWN = 30  # seconds between shots from the same person

# ---------------------------------------------------------------------------
# physics, in meters and seconds (y points up, the free-throw line is x = 0)
# ---------------------------------------------------------------------------

GRAVITY = 9.81  # same as swish-rl
BALL_R = 0.12  # same as swish-rl
FLOOR_BOUNCE = 0.8  # same as swish-rl's RESTITUTION
RIM_BOUNCE, BOARD_BOUNCE = 0.55, 0.6  # rims and boards are deader than the floor

RIM_X = 4.19  # center of the rim, from the free-throw line
RIM_HEIGHT = 3.05
RIM_R = 0.2286  # inner radius, an 18 inch rim
RIM_TUBE = 0.01  # thickness of the rim itself
BOARD_X = RIM_X + 0.381  # face of the backboard
BOARD_BOTTOM, BOARD_TOP = 2.90, 3.95

RELEASE = (0.0, 2.10)
ANGLE = 52.0  # degrees, a normal free-throw arc
# release speed in m/s for power 1 to 10. 7 is the sweet spot, 6 and 8 sometimes
# rattle in, and the rest miss in different ways (short, front rim, back rim, board, long)
SPEEDS = [5.5, 6.0, 6.4, 6.7, 6.92, 7.04, 7.18, 7.36, 8.1, 8.9]
WOBBLE_SPEED, WOBBLE_ANGLE = 0.007, 0.7  # standard deviation: fraction of speed, degrees

DT = 1 / 600
MAX_TIME = 3.4
LEFT_M, RIGHT_M, TOP_M = -0.9, BOARD_X + 0.75, 4.95  # what the picture shows, in meters

RESULTS = {
    # result: (made it?, short label for the picture, reply)
    "swish": (True, "swish", "Swish! Power {p} went straight in without touching the rim."),
    "rattle": (True, "rattled in", "In! Power {p} hit the rim and rattled in. Counts."),
    "bank": (True, "bank shot", "In off the backboard with power {p}. Counts."),
    "front": (False, "front rim", "Off the front rim with power {p}, a bit short. Try a little more."),
    "back": (False, "back rim", "Off the back rim with power {p}, a bit long. Try a little less."),
    "board": (False, "off the board", "Off the backboard with power {p}, too long. Try less."),
    "short": (False, "airball", "Airball, power {p} was way short. Try more."),
    "long": (False, "airball", "Airball, power {p} sailed long. Try less."),
}


@dataclass
class Shot:
    power: int
    speed: float
    angle: float
    result: str = ""
    made_at: float | None = None
    floor_at: float | None = None  # first time it hits the floor
    path: list[tuple[float, float, float]] = field(default_factory=list)  # (t, x, y)
    end: float = 0.0


def simulate(power: int, seed: int | None) -> Shot:
    rng = random.Random(seed)
    speed = SPEEDS[power - 1] * (1 + rng.gauss(0, WOBBLE_SPEED))
    angle = ANGLE + rng.gauss(0, WOBBLE_ANGLE)
    shot = Shot(power, speed, angle)

    x, y = RELEASE
    vx, vy = speed * math.cos(math.radians(angle)), speed * math.sin(math.radians(angle))
    rim_points = {"front": (RIM_X - RIM_R - RIM_TUBE, RIM_HEIGHT), "back": (RIM_X + RIM_R + RIM_TUBE, RIM_HEIGHT)}
    first_touch, t, next_sample = None, 0.0, 0.0
    touch_before_make, came_down_at = None, None  # where it first fell back through rim height

    while t < MAX_TIME:
        if t >= next_sample:
            shot.path.append((t, x, y))
            next_sample += 1 / 60
        # semi-implicit Euler, same as swish-rl: velocity first, then position
        vy -= GRAVITY * DT
        px, py = x, y
        x, y = x + vx * DT, y + vy * DT
        t += DT

        for name, (cx, cy) in rim_points.items():
            dx, dy = x - cx, y - cy
            dist = math.hypot(dx, dy)
            if dist < BALL_R + RIM_TUBE and dx * vx + dy * vy < 0:
                nx, ny = dx / dist, dy / dist
                vn = vx * nx + vy * ny
                tx, ty = vx - vn * nx, vy - vn * ny
                vx, vy = 0.85 * tx - RIM_BOUNCE * vn * nx, 0.85 * ty - RIM_BOUNCE * vn * ny
                x, y = cx + nx * (BALL_R + RIM_TUBE), cy + ny * (BALL_R + RIM_TUBE)
                first_touch = first_touch or name

        if vx > 0 and x + BALL_R > BOARD_X and px < BOARD_X and BOARD_BOTTOM - BALL_R < y < BOARD_TOP + BALL_R:
            x, vx, vy = BOARD_X - BALL_R, -vx * BOARD_BOUNCE, vy * 0.9
            first_touch = first_touch or "board"

        if y - BALL_R < 0 and vy < 0:
            y, vy, vx = BALL_R, -vy * FLOOR_BOUNCE, vx * 0.9
            shot.floor_at = shot.floor_at or t

        if shot.made_at is None and py > RIM_HEIGHT >= y and vy < 0:
            came_down_at = x if came_down_at is None else came_down_at
            if RIM_X - RIM_R < x < RIM_X + RIM_R:
                shot.made_at, touch_before_make = t, first_touch
                vx, vy = 0.0, vy * 0.6  # the net catches it, so it drops straight down

        if x > RIGHT_M + 0.3 or x < LEFT_M - 0.3 or (y <= BALL_R + 0.001 and abs(vy) < 0.4 and t > 1):
            break

    shot.path.append((t, x, y))
    shot.end = t
    if shot.made_at is not None:
        shot.result = {"board": "bank", "front": "rattle", "back": "rattle"}.get(touch_before_make, "swish")
    elif first_touch:
        shot.result = first_touch
    else:  # airball: short if it never got up to the rim, or came down before reaching it
        peak = max(y for _, _, y in shot.path)
        short = peak < RIM_HEIGHT or (came_down_at is not None and came_down_at < RIM_X)
        shot.result = "short" if short else "long"
    return shot


# ---------------------------------------------------------------------------
# the picture
# ---------------------------------------------------------------------------

PPM = 88  # pixels per meter
WIDTH = round((RIGHT_M - LEFT_M) * PPM)
FLOOR_Y = round(TOP_M * PPM)
HEIGHT = FLOOR_Y + 40
FONT = 'ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, "Liberation Mono", monospace'

THEMES = {
    "dark": {"fg": "#e6edf3", "muted": "#7d8590", "line": "#30363d", "figure": "#484f58",
             "ball": "#e8772e", "seam": "#2b1308", "rim": "#ff7b72", "net": "#c9d1d9",
             "board": "#8b949e", "trail": "#ffc72c", "made": "#ffc72c"},
    "light": {"fg": "#1f2328", "muted": "#59636e", "line": "#d0d7de", "figure": "#afb8c1",
              "ball": "#e8772e", "seam": "#3b1a06", "rim": "#cf222e", "net": "#6e7781",
              "board": "#8c959f", "trail": "#9a6700", "made": "#9a6700"},
}


def X(x: float) -> float:
    return (x - LEFT_M) * PPM


def Y(y: float) -> float:
    return FLOOR_Y - y * PPM


def pct(t: float, period: float) -> str:
    return f"{100 * t / period:.3f}%"


def ball_markup(theme: dict) -> str:
    r = BALL_R * PPM
    seams = (f"M{-r + .8:.1f} 0H{r - .8:.1f}M0 {-r + .8:.1f}V{r - .8:.1f}"
             f"M{-.62 * r:.2f} {-.78 * r:.2f}Q{-.05 * r:.2f} 0 {-.62 * r:.2f} {.78 * r:.2f}"
             f"M{.62 * r:.2f} {-.78 * r:.2f}Q{.05 * r:.2f} 0 {.62 * r:.2f} {.78 * r:.2f}")
    return (f'<circle r="{r:.1f}" fill="{theme["ball"]}"/>'
            f'<path d="{seams}" fill="none" stroke="{theme["seam"]}" stroke-width="1.2" stroke-linecap="round"/>')


def court_markup(theme: dict) -> str:
    rim_l, rim_r, rim_y = X(RIM_X - RIM_R), X(RIM_X + RIM_R), Y(RIM_HEIGHT)
    board_x = X(BOARD_X)
    pole_x = board_x + 0.32 * PPM
    s = theme
    parts = [
        # floor and the free-throw line
        f'<path d="M0 {FLOOR_Y + .5}H{WIDTH}" stroke="{s["line"]}" stroke-width="1"/>',
        f'<path d="M{X(0):.1f} {FLOOR_Y - 4}V{FLOOR_Y + 5}" stroke="{s["muted"]}" stroke-width="1.5"/>',
        # a simple shooter, follow-through pose
        f'<g stroke="{s["figure"]}" stroke-width="5" stroke-linecap="round" stroke-linejoin="round" fill="none">'
        f'<path d="M{X(-0.22):.1f} {Y(0.02):.1f}L{X(-0.12):.1f} {Y(0.95):.1f}L{X(-0.02):.1f} {Y(0.02):.1f}'
        f'M{X(-0.12):.1f} {Y(0.95):.1f}L{X(-0.1):.1f} {Y(1.45):.1f}'
        f'M{X(-0.1):.1f} {Y(1.4):.1f}L{X(-0.03):.1f} {Y(1.75):.1f}L{X(0.02):.1f} {Y(1.98):.1f}"/>'
        f'</g><circle cx="{X(-0.1):.1f}" cy="{Y(1.62):.1f}" r="{0.11 * PPM:.1f}" fill="{s["figure"]}"/>',
        # pole, backboard, rim, net
        f'<path d="M{board_x + 4:.1f} {Y(3.2):.1f}H{pole_x:.1f}V{FLOOR_Y:.1f}" fill="none" '
        f'stroke="{s["figure"]}" stroke-width="5" stroke-linejoin="round"/>',
        f'<rect x="{board_x:.1f}" y="{Y(BOARD_TOP):.1f}" width="4.5" height="{(BOARD_TOP - BOARD_BOTTOM) * PPM:.1f}" '
        f'rx="1" fill="{s["board"]}"/>',
        f'<path d="M{rim_r:.1f} {rim_y:.1f}H{board_x:.1f}" stroke="{s["rim"]}" stroke-width="2"/>',
    ]
    return "".join(parts)


def net_markup(theme: dict) -> str:
    rim_l, rim_r, rim_y = X(RIM_X - RIM_R), X(RIM_X + RIM_R), Y(RIM_HEIGHT)
    cx, depth = X(RIM_X), 0.42 * PPM
    strands = "".join(
        f"M{cx + f * (rim_r - rim_l) / 2:.1f} {rim_y + 1:.1f}L{cx + f * 0.5 * (rim_r - rim_l) / 2:.1f} {rim_y + depth:.1f}"
        for f in (-1, -0.5, 0, 0.5, 1))
    zig = ""
    for row, shrink in ((0.35, 0.82), (0.7, 0.64)):
        y = rim_y + depth * row
        half = (rim_r - rim_l) / 2 * shrink
        pts = [(cx - half + i * half / 2, y + (3 if i % 2 else 0)) for i in range(5)]
        zig += "M" + "L".join(f"{px:.1f} {py:.1f}" for px, py in pts)
    return (f'<g id="net" class="a"><path d="{strands}{zig}" fill="none" stroke="{theme["net"]}" '
            'stroke-width="1.1" stroke-linecap="round" opacity=".85"/></g>'
            f'<path d="M{rim_l:.1f} {rim_y:.1f}H{rim_r:.1f}" stroke="{theme["rim"]}" stroke-width="3" '
            'stroke-linecap="round"/>')


def build_svg(theme: dict, shot: Shot | None, caption: str) -> str:
    s = theme
    css = [f"text{{font-family:{FONT};fill:{s['muted']};font-size:12px}}",
           ".a{animation-iteration-count:infinite;animation-timing-function:linear}",
           "@media (prefers-reduced-motion:reduce){.a{animation:none!important}}"]
    body = [court_markup(s)]

    if shot is None:
        bx, by = X(RELEASE[0]), Y(RELEASE[1])
        body.append(f'<g transform="translate({bx:.1f} {by:.1f})">{ball_markup(s)}</g>')
        body.append(net_markup(s))
        label = ""
    else:
        period = shot.end + 1.8
        css.append(f".a{{animation-duration:{period:.3f}s}}")
        last_t, lx, ly = shot.path[-1]

        # the ball, moving through the simulated points, with backspin
        frames = "".join(f"{pct(t, period)}{{transform:translate({X(x):.1f}px,{Y(y):.1f}px)}}"
                         for t, x, y in shot.path)
        css.append(f"@keyframes fly{{{frames}100%{{transform:translate({X(lx):.1f}px,{Y(ly):.1f}px)}}}}")
        spin = -540 * shot.end
        css.append(f"@keyframes spin{{0%{{transform:rotate(0deg)}}{pct(shot.end, period)},100%"
                   f"{{transform:rotate({spin:.0f}deg)}}}}")
        css.append(f"@keyframes show{{0%,{pct(shot.end + 1.2, period)}{{opacity:1}}{pct(period - 0.1, period)},"
                   "100%{opacity:0}}")
        css.append(f"#ball{{transform:translate({X(lx):.1f}px,{Y(ly):.1f}px);animation-name:fly,show}}")
        css.append(f"#spin{{transform-box:fill-box;transform-origin:center;transform:rotate({spin:.0f}deg);"
                   "animation-name:spin}")

        # the arc of the shot, drawn as the ball moves: up to the net for a make,
        # up to the first bounce on the floor for a miss
        cut = shot.made_at + 0.18 if shot.made_at is not None else (shot.floor_at or shot.end)
        points, lengths, total = [], [], 0.0
        for t, x, y in shot.path:
            if t > cut:
                break
            if points:
                total += math.hypot(X(x) - points[-1][0], Y(y) - points[-1][1])
            points.append((X(x), Y(y)))
            lengths.append((t, total))
        trail = "".join(f"{pct(t, period)}{{stroke-dashoffset:{total - d:.1f};opacity:.75}}" for t, d in lengths)
        css.append(f"@keyframes trail{{{trail}{pct(shot.end + 1.2, period)}{{stroke-dashoffset:0;opacity:.75}}"
                   f"{pct(period - 0.1, period)},100%{{stroke-dashoffset:0;opacity:0}}}}")
        css.append(f"#trail{{stroke-dasharray:{total:.1f};stroke-dashoffset:0;opacity:.75;animation-name:trail}}")
        poly = " ".join(f"{px:.1f},{py:.1f}" for px, py in points)
        body.append(f'<polyline id="trail" class="a" points="{poly}" fill="none" stroke="{s["trail"]}" '
                    'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>')
        body.append(f'<g id="ball" class="a"><g id="spin" class="a">{ball_markup(s)}</g></g>')

        # the net swishes if it went in
        net_frames = "0%{transform:scale(1,1)}100%{transform:scale(1,1)}"
        if shot.made_at is not None:
            m = shot.made_at
            net_frames = (f"0%,{pct(m, period)}{{transform:scale(1,1)}}{pct(m + 0.08, period)}"
                          f"{{transform:scale(.88,1.25)}}{pct(m + 0.22, period)}{{transform:scale(1.04,.95)}}"
                          f"{pct(m + 0.36, period)},100%{{transform:scale(1,1)}}")
        css.append(f"@keyframes swish{{{net_frames}}}")
        css.append("#net{transform-box:fill-box;transform-origin:50% 0;animation-name:swish}")
        body.append(net_markup(s))

        # the result, showing up once the shot is decided
        made, word, _ = RESULTS[shot.result]
        reveal = shot.made_at + 0.2 if shot.made_at is not None else min(shot.end, 1.6)
        css.append(f"@keyframes reveal{{0%,{pct(reveal, period)}{{opacity:0}}{pct(reveal + 0.15, period)},"
                   f"{pct(period - 0.1, period)}{{opacity:1}}100%{{opacity:0}}}}")
        css.append("#result{animation-name:reveal}")
        color = s["made"] if made else s["muted"]
        label = (f'<text id="result" class="a" x="14" y="34" '
                 f'style="fill:{color};font-size:22px;font-weight:600">{word}</text>')

    body.append(label)
    body.append(f'<text x="10" y="{HEIGHT - 12}">{caption}</text>')
    title = "The last free throw someone took on my profile" if shot else "Waiting for the first free throw"
    return "".join([
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" '
        f'viewBox="0 0 {WIDTH} {HEIGHT}" role="img" aria-labelledby="ft-title">',
        f'<title id="ft-title">{title}</title>',
        f"<style>{''.join(css)}</style>",
        "".join(body),
        "</svg>\n",
    ])


def caption_for(entry: dict | None) -> str:
    if not entry:
        return "no shots yet, be the first"
    return f"{entry['shooter']} took a {entry['power']}"


# ---------------------------------------------------------------------------
# the scoreboard
# ---------------------------------------------------------------------------


def load() -> dict:
    if DATA.exists():
        return json.loads(DATA.read_text(encoding="utf-8"))
    return {"shots": []}


def save(data: dict) -> None:
    DATA.parent.mkdir(parents=True, exist_ok=True)
    DATA.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")


def picture_name(count: int, theme: str) -> str:
    # a new file name every shot, so GitHub never shows an old cached picture
    return f"shot-{count}-{theme}.svg"


def draw(data: dict) -> None:
    shots = data["shots"]
    last = shots[-1] if shots else None
    shot = simulate(last["power"], last["issue"]) if last else None
    DIST.mkdir(exist_ok=True)
    for old in DIST.glob("shot-*.svg"):
        old.unlink()
    for name, theme in THEMES.items():
        (DIST / picture_name(len(shots), name)).write_text(
            build_svg(theme, shot, caption_for(last)), encoding="utf-8")


def scoreboard(data: dict) -> str:
    shots = data["shots"]
    n = len(shots)
    pictures = (
        "<picture>\n"
        f'  <source media="(prefers-color-scheme: dark)" srcset="dist/{picture_name(n, "dark")}">\n'
        f'  <source media="(prefers-color-scheme: light)" srcset="dist/{picture_name(n, "light")}">\n'
        f'  <img alt="the last free throw someone took on my profile" src="dist/{picture_name(n, "light")}">\n'
        "</picture>"
    )
    if not shots:
        return f"{pictures}\n\nNo shots yet, be the first."
    makes = sum(1 for s in shots if RESULTS[s["result"]][0])
    swishes = sum(1 for s in shots if s["result"] == "swish")
    rows = "\n".join(
        f"| [{s['shooter']}](https://github.com/{s['shooter']}) | {s['power']} | {RESULTS[s['result']][1]} |"
        for s in reversed(shots[-5:]))
    total = f"{makes} made out of {n} {'shot' if n == 1 else 'shots'} so far"
    if swishes:
        total += f", {swishes} of them {'a swish' if swishes == 1 else 'swishes'}"
    return f"{pictures}\n\n{total}.\n\n| shooter | power | result |\n|---|---|---|\n{rows}"


def update_readme(block: str) -> None:
    text = README.read_text(encoding="utf-8")
    if START not in text or END not in text:
        sys.exit(f"README.md needs {START} and {END} where the scoreboard should go.")
    before, rest = text.split(START, 1)
    _, after = rest.split(END, 1)
    README.write_text(f"{before}{START}\n{block}\n{END}{after}", encoding="utf-8")


def reply(text: str) -> None:
    path = os.environ.get("REPLY_FILE")
    if path:
        Path(path).write_text(text + "\n", encoding="utf-8")
    print(text)


def shoot() -> None:
    title = os.environ.get("TITLE", "")
    shooter = os.environ.get("SHOOTER", "")
    issue = int(os.environ.get("ISSUE", "0"))

    if not re.match(r"\s*shoot(\s|$)", title, re.IGNORECASE):
        print("not a shot, leaving this issue alone")  # e.g. "shooting star idea"
        return
    match = re.fullmatch(r"\s*shoot\s+(\d{1,2})\s*", title, re.IGNORECASE)
    power = int(match.group(1)) if match else 0
    if not 1 <= power <= 10 or not re.fullmatch(r"[A-Za-z0-9-]{1,39}(\[bot\])?", shooter):
        reply("Couldn't read that shot. Open a new issue titled `shoot 7`, "
              "with any power from 1 to 10.")
        return

    data = load()
    now = datetime.now(timezone.utc)
    mine = [s for s in data["shots"] if s["shooter"] == shooter and s["issue"] != issue]
    if mine and (now - datetime.fromisoformat(mine[-1]["at"].replace("Z", "+00:00"))).total_seconds() < COOLDOWN:
        reply("One shot at a time, give it a minute and try again.")
        return
    if any(s["issue"] == issue for s in data["shots"]):  # already counted, e.g. on a re-run
        entry = next(s for s in data["shots"] if s["issue"] == issue)
    else:
        shot = simulate(power, issue)
        entry = {"issue": issue, "shooter": shooter, "power": power, "result": shot.result,
                 "at": now.strftime("%Y-%m-%dT%H:%M:%SZ")}
        data["shots"].append(entry)
        save(data)
        draw(data)
        update_readme(scoreboard(data))

    made = sum(1 for s in data["shots"] if RESULTS[s["result"]][0])
    reply(f"{RESULTS[entry['result']][2].format(p=entry['power'])}\n\n"
          f"That's {made} made out of {len(data['shots'])} on the scoreboard: {PROFILE}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Free throws on my profile.")
    parser.add_argument("command", nargs="?", choices=["shoot", "render"], help="what to do")
    parser.add_argument("--demo", type=int, metavar="POWER", help="preview a shot at this power")
    parser.add_argument("--seed", type=int, default=1, help="which wobble to use with --demo")
    parser.add_argument("--out", default="dist", help="folder for --demo pictures")
    args = parser.parse_args()

    if args.demo:
        shot = simulate(args.demo, args.seed)
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        for name, theme in THEMES.items():
            (out / f"shot-demo-{name}.svg").write_text(
                build_svg(theme, shot, f"demo shot, power {args.demo}"), encoding="utf-8")
        print(f"power {args.demo}: {shot.result} ({shot.speed:.2f} m/s at {shot.angle:.1f} degrees), "
              f"wrote {out}/shot-demo-dark.svg")
    elif args.command == "shoot":
        shoot()
    elif args.command == "render":
        data = load()
        if not (DIST / picture_name(len(data["shots"]), "dark")).exists():
            draw(data)
            print("drew the court")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
