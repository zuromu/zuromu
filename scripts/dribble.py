#!/usr/bin/env python3
"""Draws a basketball dribbling across my contribution graph.

The ball bounces from one day I contributed to the next, in order, and each day
lights up when it lands. Quiet stretches get dribbled through in smaller
bounces. At the end it takes a shot, drops through the net, and settles.

It uses the same physics as swish-rl: gravity of 9.81 m/s², and bounces that
keep 80% of the ball's speed.

Why this works on GitHub without JavaScript: during each half of a bounce the
height is a quadratic in time (a parabola), and a CSS cubic-bezier easing can
draw any quadratic exactly. So every bounce becomes a couple of CSS keyframes
instead of hundreds of frames.

    python3 scripts/dribble.py --demo            made up graph, no token needed
    GITHUB_TOKEN=xxx python3 scripts/dribble.py  my real graph

It writes dist/dribble-dark.svg and dist/dribble-light.svg.
"""

from __future__ import annotations

import argparse
import math
import os
import random
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from github_api import graphql

USERNAME = os.environ.get("GITHUB_USER", "zuromu")

# ---------------------------------------------------------------------------
# physics (gravity and restitution are the same as swish-rl/src/swish_rl/physics.py)
# ---------------------------------------------------------------------------

GRAVITY = 9.81  # m/s²
PIXELS_PER_METER = 160.0  # swish-rl draws at 100, but everything here is tiny so it zooms in more
RESTITUTION = 0.8  # fraction of speed kept after a bounce
G = GRAVITY * PIXELS_PER_METER  # px/s²

# ---------------------------------------------------------------------------
# layout, in pixels
# ---------------------------------------------------------------------------

CELL = 11  # one day
PITCH = 15  # cell plus gap
LEFT = 16
TOP = 92  # room above the graph for the ball to fly
BALL_R = 8.0

DROP_HEIGHT = 80.0  # where the ball falls in from at the start
MAX_HOP = 120.0  # longer gaps get split into smaller dribbles
APEX_MIN, APEX_PER_PX, APEX_MAX = 5.0, 0.28, 30.0  # how high a hop goes for its length

HOOP_GAP = 48  # from the last column to the rim
HOOP_ROW = 5  # the hoop stands on the floor level of this row
RIM_R = 11.0
RIM_HEIGHT = 56.0  # height of the ball's bottom when it's centered in the rim
SHOT_APEX = 96.0  # top of the shot
SHOT_DISTANCE = 95.0  # dribbles to here before shooting
SETTLE_MIN = 1.2  # stop bouncing once a bounce would be lower than this

HOLD = 1.6  # seconds to show the full graph before it resets
FADE = 0.6

FONT = 'ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, "Liberation Mono", monospace'

THEMES = {
    "dark": {
        "empty": "#161b22", "levels": ["#173b72", "#2458b8", "#4f8cff", "#9ec5ff"],
        "label": "#7d8590", "ball": "#e8772e", "seam": "#2b1308", "shadow": "#000000",
        "shadow_opacity": 0.45, "rim": "#ff7b72", "net": "#c9d1d9", "board": "#8b949e",
        "pole": "#484f58",
    },
    "light": {
        "empty": "#ebedf0", "levels": ["#bcd2f9", "#82a8ef", "#3d74db", "#1d428a"],
        "label": "#59636e", "ball": "#e8772e", "seam": "#3b1a06", "shadow": "#1f2328",
        "shadow_opacity": 0.22, "rim": "#cf222e", "net": "#6e7781", "board": "#8c959f",
        "pole": "#afb8c1",
    },
}

# ---------------------------------------------------------------------------
# the contribution graph
# ---------------------------------------------------------------------------

LEVELS = {"NONE": 0, "FIRST_QUARTILE": 1, "SECOND_QUARTILE": 2,
          "THIRD_QUARTILE": 3, "FOURTH_QUARTILE": 4}

CALENDAR_QUERY = """
query($login: String!) {
  user(login: $login) {
    contributionsCollection {
      contributionCalendar {
        weeks { contributionDays { date weekday contributionLevel } }
      }
    }
  }
}
"""


@dataclass(frozen=True)
class Day:
    col: int  # week
    row: int  # 0 is Sunday, like on GitHub
    day: date
    level: int  # 0 to 4


def fetch_calendar(token: str) -> list[Day]:
    data = graphql(CALENDAR_QUERY, {"login": USERNAME}, token)
    weeks = data["user"]["contributionsCollection"]["contributionCalendar"]["weeks"]
    return [Day(col, d["weekday"], date.fromisoformat(d["date"]), LEVELS[d["contributionLevel"]])
            for col, week in enumerate(weeks) for d in week["contributionDays"]]


def demo_calendar(seed: int = 7) -> list[Day]:
    """A made up year with a busier stretch in the middle."""
    rng = random.Random(seed)
    end = date.today()
    start = end - timedelta(days=364)
    start -= timedelta(days=(start.weekday() + 1) % 7)  # back to a Sunday
    days, d = [], start
    while d <= end:
        offset = (d - start).days
        busy = 0.4 if 120 < offset < 250 else 0.12
        level = rng.choice([1, 1, 2, 2, 3, 4]) if rng.random() < busy else 0
        days.append(Day(offset // 7, (d.weekday() + 1) % 7, d, level))
        d += timedelta(days=1)
    return days


def cell_center(day: Day) -> tuple[float, float]:
    return LEFT + day.col * PITCH + CELL / 2, TOP + day.row * PITCH + CELL / 2


# ---------------------------------------------------------------------------
# planning the ball's path
# ---------------------------------------------------------------------------


def rise_time(height: float) -> float:
    """Seconds to fall from rest through `height` (same as rising to it)."""
    return math.sqrt(2 * height / G)


def lerp(p: tuple[float, float], q: tuple[float, float], f: float) -> tuple[float, float]:
    return p[0] + (q[0] - p[0]) * f, p[1] + (q[1] - p[1]) * f


@dataclass
class Segment:
    """A stretch of time where the height follows one quadratic curve."""

    t0: float
    t1: float
    g0: tuple[float, float]  # point on the floor under the ball at t0
    g1: tuple[float, float]
    z0: float  # height of the ball's bottom above the floor
    z1: float
    a: float  # progress = a*s + (1 - a)*s², with s going 0 to 1 over the segment
    impact: float = 0.0  # speed when it hits the floor at t1, 0 if it doesn't


class Route:
    def __init__(self) -> None:
        self.t = 0.0
        self.segments: list[Segment] = []
        self.lit: dict[int, float] = {}  # cell index -> time it lights up
        self.rim_time = 0.0

    @property
    def here(self) -> tuple[float, float]:
        return self.segments[-1].g1

    def add(self, duration, g1, z0, z1, a, impact=0.0) -> None:
        g0 = self.here if self.segments else g1
        self.segments.append(Segment(self.t, self.t + duration, g0, g1, z0, z1, a, impact))
        self.t += duration

    def drop(self, g, height: float) -> None:
        fall = rise_time(height)
        self.add(fall, g, height, 0.0, a=0.0, impact=G * fall)

    def hop(self, g1, apex: float) -> None:
        half = rise_time(apex)
        self.add(half, lerp(self.here, g1, 0.5), 0.0, apex, a=2.0)  # slowing down on the way up
        self.add(half, g1, apex, 0.0, a=0.0, impact=G * half)  # speeding up on the way down

    def dribble_to(self, g1) -> None:
        start = self.here
        dist = math.dist(start, g1)
        hops = max(1, math.ceil(dist / MAX_HOP))
        apex = min(APEX_MAX, APEX_MIN + APEX_PER_PX * dist / hops)
        for k in range(1, hops + 1):
            self.hop(lerp(start, g1, k / hops), apex)

    def shoot(self, rim_floor) -> None:
        up, down = rise_time(SHOT_APEX), rise_time(SHOT_APEX - RIM_HEIGHT)
        self.add(up, lerp(self.here, rim_floor, up / (up + down)), 0.0, SHOT_APEX, a=2.0)
        self.add(down, rim_floor, SHOT_APEX, RIM_HEIGHT, a=0.0)
        self.rim_time = self.t
        # through the net: already moving down at speed v when it passes the rim
        v = G * down
        fall = (-v + math.sqrt(v * v + 2 * G * RIM_HEIGHT)) / G
        self.add(fall, rim_floor, RIM_HEIGHT, 0.0, a=v * fall / RIM_HEIGHT, impact=v + G * fall)

    def settle(self) -> None:
        v = self.segments[-1].impact * RESTITUTION
        while v * v / (2 * G) >= SETTLE_MIN:
            apex, half = v * v / (2 * G), v / G
            self.add(half, self.here, 0.0, apex, a=2.0)
            self.add(half, self.here, apex, 0.0, a=0.0, impact=v)
            v *= RESTITUTION


def plan(days: list[Day], rim_floor: tuple[float, float]) -> Route:
    path = Route()
    active = sorted((i for i, d in enumerate(days) if d.level), key=lambda i: days[i].day)
    shot_spot = (rim_floor[0] - SHOT_DISTANCE, rim_floor[1])

    if active:
        path.drop(cell_center(days[active[0]]), DROP_HEIGHT)
        path.lit[active[0]] = path.t
        for i in active[1:]:
            path.dribble_to(cell_center(days[i]))
            path.lit[i] = path.t
        if path.here[0] < shot_spot[0]:
            path.dribble_to(shot_spot)
    else:
        path.drop(shot_spot, DROP_HEIGHT)

    path.shoot(rim_floor)
    path.settle()
    return path


# ---------------------------------------------------------------------------
# drawing
# ---------------------------------------------------------------------------


def bezier(a: float) -> str:
    """CSS easing for progress = a*s + (1 - a)*s², which is exact for a quadratic."""
    return f"cubic-bezier(.3333,{a / 3:.4f},.6667,{(1 + a) / 3:.4f})"


def keyframes(name: str, frames: list[tuple[float, str]], period: float) -> str:
    body = "".join(f"{100 * t / period:.3f}%{{{css}}}" for t, css in frames)
    return f"@keyframes {name}{{{body}}}"


def month_labels(days: list[Day]) -> list[tuple[int, str]]:
    """Like GitHub: each month's name sits over the week that has its 1st day."""
    starts = sorted((d.col, d.day.strftime("%b")) for d in days if d.day.day == 1)
    labels: list[tuple[int, str]] = []
    for col, name in starts:
        if labels and col - labels[-1][0] < 3:
            labels.pop()  # too close to fit both, keep the newer month
        labels.append((col, name))
    return labels


def build_svg(days: list[Day], theme: dict) -> str:
    cols = max(d.col for d in days) + 1
    grid_right = LEFT + (cols - 1) * PITCH + CELL
    hoop_x = grid_right + HOOP_GAP
    floor_y = TOP + HOOP_ROW * PITCH + CELL / 2
    rim_y = floor_y - RIM_HEIGHT - BALL_R  # ball center when it's in the rim
    width = math.ceil(hoop_x + RIM_R + 30)
    height = TOP + 6 * PITCH + CELL + 22

    path = plan(days, (hoop_x, floor_y))
    segs = path.segments
    rest = path.t
    fade_start = rest + HOLD
    period = fade_start + FADE
    end = period - 0.05

    # where the ball is on the floor, moving in straight lines between keyframes
    ground = [(s.t0, f"transform:translate({s.g0[0]:.2f}px,{s.g0[1]:.2f}px)") for s in segs]
    last = segs[-1].g1
    ground += [(rest, f"transform:translate({last[0]:.2f}px,{last[1]:.2f}px)"),
               (period, f"transform:translate({last[0]:.2f}px,{last[1]:.2f}px)")]

    # how high it is, plus its shadow, which shrinks and fades as it goes up
    lift, shadow = [], []
    for s in segs:
        ease = f";animation-timing-function:{bezier(s.a)}"
        lift.append((s.t0, f"transform:translateY({-s.z0:.2f}px){ease}"))
        k = min(s.z0, 100.0) / 100.0
        shadow.append((s.t0, f"transform:scale({1 - 0.55 * k:.3f});opacity:{1 - 0.6 * k:.3f}{ease}"))
    lift += [(rest, "transform:translateY(0px)"), (period, "transform:translateY(0px)")]
    shadow += [(rest, "transform:scale(1);opacity:1"), (period, "transform:scale(1);opacity:1")]

    # spin, as if it's rolling forward over the ground it covers
    spin, angle = [], 0.0
    for s in segs:
        spin.append((s.t0, f"transform:rotate({angle:.1f}deg)"))
        angle += math.degrees(math.dist(s.g0, s.g1) / BALL_R)
    spin += [(rest, f"transform:rotate({angle:.1f}deg)"), (period, f"transform:rotate({angle:.1f}deg)")]

    # squash on every landing, harder for harder landings
    squash, eps = [(0.0, "transform:scale(1,1)")], 0.035
    for s in segs:
        if s.impact:
            amount = 0.05 + 0.2 * min(1.0, s.impact / 400)
            squash += [(s.t1 - eps, "transform:scale(1,1)"),
                       (s.t1, f"transform:scale({1 + amount:.3f},{1 - amount:.3f})"),
                       (s.t1 + eps, "transform:scale(1,1)")]
    squash.append((period, "transform:scale(1,1)"))

    show = [(0.0, "opacity:0"), (0.15, "opacity:1"), (fade_start, "opacity:1"),
            (end, "opacity:0"), (period, "opacity:0")]

    t = path.rim_time
    net = [(0.0, "transform:scale(1,1)"), (t - 0.01, "transform:scale(1,1)"),
           (t + 0.08, "transform:scale(.86,1.28)"), (t + 0.2, "transform:scale(1.04,.95)"),
           (t + 0.34, "transform:scale(1,1)"), (period, "transform:scale(1,1)")]

    css = [
        f"text{{font-family:{FONT};font-size:10px;fill:{theme['label']}}}",
        f".a{{animation-duration:{period:.3f}s;animation-iteration-count:infinite;"
        "animation-timing-function:linear}",
        ".c{transform-box:fill-box;transform-origin:center}",
        f"#ground{{transform:translate({last[0]:.2f}px,{last[1]:.2f}px);animation-name:ground,show}}",
        "#lift{animation-name:lift}",
        "#shadow{transform-box:fill-box;transform-origin:center;animation-name:shadow}",
        "#squash{transform-box:fill-box;transform-origin:50% 100%;animation-name:squash}",
        f"#spin{{transform-box:fill-box;transform-origin:center;transform:rotate({angle:.1f}deg);"
        "animation-name:spin}",
        "#net{transform-box:fill-box;transform-origin:50% 0;animation-name:net}",
        keyframes("ground", ground, period),
        keyframes("lift", lift, period),
        keyframes("shadow", shadow, period),
        keyframes("spin", spin, period),
        keyframes("squash", squash, period),
        keyframes("show", show, period),
        keyframes("net", net, period),
    ]

    cells = []
    for i, d in enumerate(days):
        x, y = LEFT + d.col * PITCH, TOP + d.row * PITCH
        color = theme["levels"][d.level - 1] if d.level else theme["empty"]
        if i in path.lit:
            lit = path.lit[i]
            empty = f"fill:{theme['empty']};transform:scale(1)"
            on = f"fill:{color};transform:scale(1)"
            css.append(keyframes(f"k{i}", [
                (0.0, empty), (lit - 0.004, empty), (lit, f"fill:{color};transform:scale(1.5)"),
                (lit + 0.22, on), (fade_start, on), (end, empty), (period, empty)], period))
            css.append(f"#k{i}{{animation-name:k{i}}}")
            cells.append(f'<rect id="k{i}" class="c a" x="{x}" y="{y}" width="{CELL}" '
                         f'height="{CELL}" rx="2" fill="{color}"/>')
        else:
            cells.append(f'<rect x="{x}" y="{y}" width="{CELL}" height="{CELL}" rx="2" fill="{color}"/>')

    css.append("@media (prefers-reduced-motion:reduce){.a{animation:none!important}}")

    months = "".join(f'<text x="{LEFT + col * PITCH}" y="{TOP + 6 * PITCH + CELL + 15}">{name}</text>'
                     for col, name in month_labels(days))

    r = BALL_R
    seams = (f"M{-r + .6:.1f} 0H{r - .6:.1f}M0 {-r + .6:.1f}V{r - .6:.1f}"
             f"M{-.62 * r:.2f} {-.78 * r:.2f}Q{-.05 * r:.2f} 0 {-.62 * r:.2f} {.78 * r:.2f}"
             f"M{.62 * r:.2f} {-.78 * r:.2f}Q{.05 * r:.2f} 0 {.62 * r:.2f} {.78 * r:.2f}")
    ball = (f'<g id="spin" class="a"><circle cx="0" cy="0" r="{r}" fill="{theme["ball"]}"/>'
            f'<path d="{seams}" fill="none" stroke="{theme["seam"]}" stroke-width="1" '
            'stroke-linecap="round"/></g>')

    hx, ry = hoop_x, rim_y
    board_x = hx + RIM_R + 2
    pole_x = hx + RIM_R + 14
    hoop_back = (
        f'<path d="M{board_x + 2.5:.1f} {ry - 6:.1f}H{pole_x:.1f}V{floor_y + 3:.1f}" fill="none" '
        f'stroke="{theme["pole"]}" stroke-width="3" stroke-linejoin="round"/>'
        f'<rect x="{pole_x - 6:.1f}" y="{floor_y + 1:.1f}" width="12" height="4" rx="2" fill="{theme["pole"]}"/>'
        f'<rect x="{board_x:.1f}" y="{ry - 26:.1f}" width="3.5" height="34" rx="1" fill="{theme["board"]}"/>'
        f'<path d="M{hx - RIM_R:.1f} {ry:.1f}A{RIM_R} 3.2 0 0 1 {hx + RIM_R:.1f} {ry:.1f}'
        f'M{hx + RIM_R:.1f} {ry:.1f}H{board_x:.1f}" fill="none" stroke="{theme["rim"]}" stroke-width="1.6"/>'
    )
    strands = "".join(
        f"M{hx + top_dx:.1f} {ry + 1.5:.1f}L{hx + top_dx * 0.55:.1f} {ry + 17:.1f}"
        for top_dx in (-10, -5, 0, 5, 10))
    zigzag = (f"M{hx - 9.2:.1f} {ry + 7:.1f}L{hx - 4.5:.1f} {ry + 9:.1f}L{hx:.1f} {ry + 7:.1f}"
              f"L{hx + 4.5:.1f} {ry + 9:.1f}L{hx + 9.2:.1f} {ry + 7:.1f}"
              f"M{hx - 7.5:.1f} {ry + 13:.1f}L{hx - 3.5:.1f} {ry + 15:.1f}L{hx:.1f} {ry + 13:.1f}"
              f"L{hx + 3.5:.1f} {ry + 15:.1f}L{hx + 7.5:.1f} {ry + 13:.1f}")
    hoop_front = (
        f'<g id="net" class="a"><path d="{strands}{zigzag}" fill="none" stroke="{theme["net"]}" '
        'stroke-width="1" stroke-linecap="round" opacity=".85"/></g>'
        f'<path d="M{hx - RIM_R:.1f} {ry:.1f}A{RIM_R} 3.2 0 0 0 {hx + RIM_R:.1f} {ry:.1f}" fill="none" '
        f'stroke="{theme["rim"]}" stroke-width="2"/>'
    )

    return "".join([
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" aria-labelledby="dribble-title">',
        '<title id="dribble-title">A basketball dribbling across my contribution graph</title>',
        f"<style>{''.join(css)}</style>",
        "".join(cells), months, hoop_back,
        '<g id="ground" class="a">',
        f'<ellipse id="shadow" class="a" cx="0" cy="0" rx="{r * 0.95:.2f}" ry="{r * 0.42:.2f}" '
        f'fill="{theme["shadow"]}" fill-opacity="{theme["shadow_opacity"]}"/>',
        f'<g id="lift" class="a"><g id="squash" class="a"><g transform="translate(0 {-r})">{ball}</g></g></g>',
        "</g>",
        hoop_front,
        "</svg>\n",
    ])


def main() -> None:
    parser = argparse.ArgumentParser(description="Draw the dribbling ball for my profile README.")
    parser.add_argument("--demo", action="store_true", help="use a made up graph, no token needed")
    parser.add_argument("--out", default="dist", help="folder to write the SVGs to (default: dist)")
    args = parser.parse_args()

    if args.demo:
        days = demo_calendar()
    else:
        token = os.environ.get("GITHUB_TOKEN")
        if not token:
            sys.exit("Set GITHUB_TOKEN, or run with --demo to use a made up graph.")
        days = fetch_calendar(token)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for name, theme in THEMES.items():
        (out / f"dribble-{name}.svg").write_text(build_svg(days, theme), encoding="utf-8")
    active = sum(1 for d in days if d.level)
    print(f"drew the dribble into {out}/ ({active} days with contributions)")


if __name__ == "__main__":
    main()
