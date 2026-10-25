#!/usr/bin/env python3
"""Draws the neofetch card at the top of my GitHub profile README.

The donut on the left is real math, not a gif: a torus gets rotated, projected
onto a grid of characters and shaded by how much it faces the light (same idea
as the classic donut.c). Every frame goes into one SVG and CSS flips between
them, so it spins on GitHub without any JavaScript.

The stats come from the GitHub GraphQL API.

    python3 scripts/neofetch.py --demo            fake stats, no token needed
    python3 scripts/neofetch.py --placeholder     dashes instead of stats
    GITHUB_TOKEN=xxx python3 scripts/neofetch.py  real stats

It writes dist/neofetch-dark.svg and dist/neofetch-light.svg. Only uses the
standard library, so there's nothing to pip install.
"""

from __future__ import annotations

import argparse
import math
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

from github_api import graphql

# ---------------------------------------------------------------------------
# stuff about me (edit these)
# ---------------------------------------------------------------------------

USERNAME = os.environ.get("GITHUB_USER", "zuromu")
NAME = "ahmad"
BIRTHDAY = date(2007, 8, 23)
SHOW_FULL_UPTIME = True  # False = only show years, so my exact birthday isn't public
SHOW_LINES_OF_CODE = True  # counts everything I've committed, lock files too, so it runs high
STARTED_UNI = date(2025, 8, 1)
TIMEZONE = "Asia/Jakarta"

# None means the value gets calculated below
SYSTEM = [
    ("OS", "macOS on a MacBook Pro"),
    ("Uptime", None),
    ("Host", "Universitas Indonesia (intl program)"),
    ("Kernel", None),
    ("Shell", "zsh"),
    ("Editor", "VS Code"),
]
LANGUAGES = [
    ("Languages.Programming", "Python, Java, C, JavaScript"),
    ("Languages.Low-Level", "MIPS and AVR assembly"),
    ("Languages.Real", "English, Bahasa Indonesia"),
]
EXTRA = [
    ("Working on", "swish-rl, a basketball game with RL"),
    ("Hobbies", "playing basketball"),
]

# ---------------------------------------------------------------------------
# layout
# ---------------------------------------------------------------------------

WIDTH = 960
LINE_CHARS = 58  # every info line is exactly this many characters
FONT = 'ui-monospace, SFMono-Regular, "SF Mono", Menlo, Monaco, Consolas, "Liberation Mono", "DejaVu Sans Mono", monospace'
INFO_X = 392
INFO_SIZE = 15
LINE_H = 20
DONUT_X = 24
DONUT_SIZE = 14
DONUT_COLS, DONUT_ROWS = 40, 22
DONUT_ROW_H = 17
FRAMES = 90
FPS = 15

THEMES = {
    "dark": {
        "bg": "#0d1117", "chrome": "#161b22", "border": "#30363d",
        "text": "#e6edf3", "muted": "#7d8590", "dots": "#3b424c",
        "key": "#ffc72c", "value": "#9ec5ff", "path": "#79a6ff",
        "add": "#3fb950", "del": "#f85149",
        "donut": ("#ffc72c", "#4f8cff"),
        "ansi": ["#484f58", "#ff7b72", "#3fb950", "#d29922",
                 "#58a6ff", "#bc8cff", "#39c5cf", "#b1bac4"],
    },
    "light": {
        "bg": "#ffffff", "chrome": "#f6f8fa", "border": "#d0d7de",
        "text": "#1f2328", "muted": "#59636e", "dots": "#c4cad1",
        "key": "#9a6700", "value": "#1d428a", "path": "#0969da",
        "add": "#1a7f37", "del": "#cf222e",
        "donut": ("#bf8700", "#1d428a"),
        "ansi": ["#24292f", "#cf222e", "#116329", "#4d2d00",
                 "#0969da", "#8250df", "#1b7c83", "#6e7781"],
    },
}

# ---------------------------------------------------------------------------
# the donut
# ---------------------------------------------------------------------------

SHADES = ".,-~:;=!*#$@"  # dark to bright


def donut_frame(a: float, b: float) -> list[str]:
    """One frame of the spinning torus as rows of characters."""
    r1, r2, k2 = 1.0, 2.0, 5.0
    k1 = DONUT_COLS * k2 * 3 / (8 * (r1 + r2))
    # a character is about twice as tall as it is wide
    aspect = (0.6 * DONUT_SIZE) / DONUT_ROW_H

    chars = [[" "] * DONUT_COLS for _ in range(DONUT_ROWS)]
    depth = [[0.0] * DONUT_COLS for _ in range(DONUT_ROWS)]
    cos_a, sin_a, cos_b, sin_b = math.cos(a), math.sin(a), math.cos(b), math.sin(b)

    theta = 0.0
    while theta < 2 * math.pi:
        cos_t, sin_t = math.cos(theta), math.sin(theta)
        circle_x = r2 + r1 * cos_t
        circle_y = r1 * sin_t
        phi = 0.0
        while phi < 2 * math.pi:
            cos_p, sin_p = math.cos(phi), math.sin(phi)
            x = circle_x * (cos_b * cos_p + sin_a * sin_b * sin_p) - circle_y * cos_a * sin_b
            y = circle_x * (sin_b * cos_p - sin_a * cos_b * sin_p) + circle_y * cos_a * cos_b
            z = k2 + cos_a * circle_x * sin_p + circle_y * sin_a
            ooz = 1 / z
            col = int(DONUT_COLS / 2 + k1 * ooz * x)
            row = int(DONUT_ROWS / 2 - k1 * ooz * y * aspect)
            light = (cos_p * cos_t * sin_b - cos_a * cos_t * sin_p - sin_a * sin_t
                     + cos_b * (cos_a * sin_t - cos_t * sin_a * sin_p))
            if light > 0 and 0 <= col < DONUT_COLS and 0 <= row < DONUT_ROWS and ooz > depth[row][col]:
                depth[row][col] = ooz
                chars[row][col] = SHADES[min(int(light * 8), len(SHADES) - 1)]
            phi += 0.02
        theta += 0.07
    return ["".join(r).rstrip() for r in chars]


def donut_frames() -> list[list[str]]:
    # a turns twice while b turns once, so the last frame flows back into the first
    return [donut_frame(4 * math.pi * i / FRAMES, 2 * math.pi * i / FRAMES) for i in range(FRAMES)]


# ---------------------------------------------------------------------------
# GitHub stats
# ---------------------------------------------------------------------------

USER_QUERY = """
query($login: String!, $after: String) {
  user(login: $login) {
    id
    followers { totalCount }
    contributionsCollection { contributionCalendar { totalContributions } }
    repositories(ownerAffiliations: OWNER, isFork: false, first: 100, after: $after) {
      totalCount
      pageInfo { hasNextPage endCursor }
      nodes { nameWithOwner stargazerCount }
    }
    repositoriesContributedTo(first: 100, includeUserRepositories: false,
                              contributionTypes: [COMMIT, PULL_REQUEST, REPOSITORY]) {
      totalCount
      nodes { nameWithOwner }
    }
  }
}
"""

HISTORY_QUERY = """
query($owner: String!, $name: String!, $author: ID!, $after: String) {
  repository(owner: $owner, name: $name) {
    defaultBranchRef {
      target {
        ... on Commit {
          history(first: 100, after: $after, author: {id: $author}) {
            pageInfo { hasNextPage endCursor }
            nodes { additions deletions parents(first: 1) { totalCount } }
          }
        }
      }
    }
  }
}
"""


def fetch_stats(token: str) -> dict:
    user, repos, stars, after = None, [], 0, None
    while True:
        data = graphql(USER_QUERY, {"login": USERNAME, "after": after}, token)
        user = data["user"]
        page = user["repositories"]
        repos += [r["nameWithOwner"] for r in page["nodes"]]
        stars += sum(r["stargazerCount"] for r in page["nodes"])
        if not page["pageInfo"]["hasNextPage"]:
            break
        after = page["pageInfo"]["endCursor"]

    contributed = [r["nameWithOwner"] for r in user["repositoriesContributedTo"]["nodes"]]

    # commits and lines of code on the default branch of every repo I own or
    # helped with. counts everything (lock files too), so it's a vanity number
    commits = additions = deletions = 0
    for full_name in dict.fromkeys(repos + contributed):
        owner, name = full_name.split("/")
        after = None
        while True:
            data = graphql(HISTORY_QUERY, {"owner": owner, "name": name,
                                           "author": user["id"], "after": after}, token)
            repo = data["repository"]
            if repo is None or repo["defaultBranchRef"] is None:  # gone, private, or empty
                break
            history = repo["defaultBranchRef"]["target"]["history"]
            for c in history["nodes"]:
                if c["parents"]["totalCount"] > 1:  # skip merge commits so nothing is counted twice
                    continue
                commits += 1
                additions += c["additions"]
                deletions += c["deletions"]
            if not history["pageInfo"]["hasNextPage"]:
                break
            after = history["pageInfo"]["endCursor"]

    return {
        "repos": user["repositories"]["totalCount"],
        "contributed": user["repositoriesContributedTo"]["totalCount"],
        "stars": stars,
        "followers": user["followers"]["totalCount"],
        "contributions": user["contributionsCollection"]["contributionCalendar"]["totalContributions"],
        "commits": commits,
        "additions": additions,
        "deletions": deletions,
    }


DEMO_STATS = {"repos": 5, "contributed": 3, "stars": 4, "followers": 6, "contributions": 412,
              "commits": 274, "additions": 51382, "deletions": 9147}

# ---------------------------------------------------------------------------
# text helpers
# ---------------------------------------------------------------------------


def plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def uptime(today: date) -> str:
    years = today.year - BIRTHDAY.year
    months = today.month - BIRTHDAY.month
    days = today.day - BIRTHDAY.day
    if days < 0:
        months -= 1
        days += (today.replace(day=1) - timedelta(days=1)).day
    if months < 0:
        years -= 1
        months += 12
    if not SHOW_FULL_UPTIME:
        return plural(years, "year")
    text = f"{plural(years, 'year')}, {plural(months, 'month')}, {plural(days, 'day')}"
    if months == 0 and days == 0:
        text += " (birthday!)"
    return text


def semester(today: date) -> str:
    months = (today.year - STARTED_UNI.year) * 12 + today.month - STARTED_UNI.month
    return f"Computer Science, semester {min(max(months // 6 + 1, 1), 8)}"


# A line is a list of (text, css class) pieces


def leader(key: str, value: str, width: int, value_class: str = "v") -> list[tuple[str, str]]:
    """key: ....... value, padded with dots to exactly `width` characters."""
    gap = width - len(key) - 2 - len(value) - 1
    if gap < 2:
        raise ValueError(f'"{key}: {value}" is too long, keep it under {width - 4} characters')
    return [(key, "k"), (":", "w"), (" " + "." * gap + " ", "d"), (value, value_class)]


def section(title: str) -> list[tuple[str, str]]:
    return [("- ", "d"), (title, "w"), (" " + "-" * (LINE_CHARS - len(title) - 3), "d")]


def card_lines(stats: dict, today: date) -> list[list[tuple[str, str]] | str]:
    computed = {"Uptime": uptime(today), "Kernel": semester(today)}
    lines: list[list[tuple[str, str]] | str] = [
        [(NAME, "k"), ("@", "w"), (USERNAME, "k")],
        [("-" * LINE_CHARS, "d")],
    ]
    lines += [leader(k, v if v is not None else computed[k], LINE_CHARS) for k, v in SYSTEM]
    lines.append([])
    lines += [leader(k, v, LINE_CHARS) for k, v in LANGUAGES]
    lines.append([])
    lines += [leader(k, v, LINE_CHARS) for k, v in EXTRA]
    lines.append([])
    lines.append(section("GitHub Stats"))

    s = stats
    left, right = 34, LINE_CHARS - 34 - 3
    # stars and followers get fetched too, swap them in here once they're numbers worth showing
    if s is None:
        # before the first real run: dashes, so no made up numbers ever go public
        lines.append(leader("Repos", "-", left) + [(" | ", "d")] + leader("Commits", "-", right))
        lines.append(leader("Contributions", "-", LINE_CHARS))
        if SHOW_LINES_OF_CODE:
            lines.append(leader("Lines of Code", "-", LINE_CHARS))
    else:
        lines.append(leader("Repos", f"{s['repos']} {{Contributed: {s['contributed']}}}", left)
                     + [(" | ", "d")] + leader("Commits", f"{s['commits']:,}", right))
        lines.append(leader("Contributions", f"{s['contributions']:,} in the last year", LINE_CHARS))
        if SHOW_LINES_OF_CODE:
            loc = s["additions"] - s["deletions"]
            adds, dels = f"{s['additions']:,}++", f"{s['deletions']:,}--"
            value = f"{loc:,} ( {adds}, {dels} )"
            pieces = leader("Lines of Code", value, LINE_CHARS)[:-1]
            pieces += [(f"{loc:,}", "v"), (" ( ", "w"), (adds, "add"), (", ", "w"), (dels, "del"), (" )", "w")]
            lines.append(pieces)
    lines.append([])
    lines.append("palette")
    return lines


# ---------------------------------------------------------------------------
# SVG
# ---------------------------------------------------------------------------


def build_svg(theme: dict, frames: list[list[str]], lines: list, timestamp: str) -> str:
    title_h = 34
    prompt_y = title_h + 32
    info_y = prompt_y + 36
    block_top = info_y - INFO_SIZE
    block_h = len(lines) * LINE_H
    donut_h = DONUT_ROWS * DONUT_ROW_H
    donut_top = block_top + (block_h - donut_h) // 2 + DONUT_SIZE
    last_y = info_y + (len(lines) - 1) * LINE_H
    end_prompt_y = last_y + 40
    height = end_prompt_y + 26

    out_start = 1.2  # when the neofetch output shows up (after the typing)
    step = 1 / FPS
    period = FRAMES * step

    css = f"""
text {{ font-family: {FONT}; white-space: pre; }}
.t {{ font-size: {INFO_SIZE}px; }}
.n {{ font-size: {DONUT_SIZE}px; fill: url(#glaze); }}
.k {{ fill: {theme['key']}; font-weight: 600; }}
.v {{ fill: {theme['value']}; }}
.w {{ fill: {theme['text']}; }}
.d {{ fill: {theme['dots']}; }}
.m {{ fill: {theme['muted']}; }}
.p {{ fill: {theme['path']}; }}
.add {{ fill: {theme['add']}; }}
.del {{ fill: {theme['del']}; }}
.f {{ visibility: hidden; animation: spin {period:.3f}s step-end infinite; }}
.f0 {{ visibility: visible; }}
.show {{ animation: show 0.2s ease-out both; }}
.type {{ animation: type 0.01s linear both; }}
.cursor {{ animation: blink 1.1s step-end infinite; }}
@keyframes spin {{ 0% {{ visibility: visible; }} {100 / FRAMES:.4f}% {{ visibility: hidden; }} 100% {{ visibility: hidden; }} }}
@keyframes show {{ from {{ opacity: 0; }} to {{ opacity: 1; }} }}
@keyframes type {{ from {{ fill-opacity: 0; }} to {{ fill-opacity: 1; }} }}
@keyframes blink {{ 50% {{ fill-opacity: 0; }} }}
@media (prefers-reduced-motion: reduce) {{ * {{ animation: none !important; }} }}
"""

    def prompt(y: int, extra: str = "") -> str:
        return (f'<text class="t" x="{DONUT_X}" y="{y}">'
                f'<tspan class="k">{NAME}@{USERNAME}</tspan><tspan class="p"> ~ </tspan>'
                f'<tspan class="w">% </tspan>{extra}</text>')

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{height}" '
        f'viewBox="0 0 {WIDTH} {height}" role="img" aria-labelledby="card-title">',
        f'<title id="card-title">{NAME}@{USERNAME} running neofetch</title>',
        f"<!-- last drawn {timestamp} -->",
        f"<style>{css}</style>",
        "<defs>",
        f'<clipPath id="window"><rect width="{WIDTH}" height="{height}" rx="10"/></clipPath>',
        f'<linearGradient id="glaze" gradientUnits="userSpaceOnUse" x1="{DONUT_X}" y1="{donut_top - DONUT_SIZE}" '
        f'x2="{DONUT_X + 0.6 * DONUT_SIZE * DONUT_COLS:.0f}" y2="{donut_top + donut_h}">'
        f'<stop offset="0.15" stop-color="{theme["donut"][0]}"/><stop offset="0.5" stop-color="{theme["text"]}"/>'
        f'<stop offset="0.85" stop-color="{theme["donut"][1]}"/>'
        "</linearGradient>",
        "</defs>",
        '<g clip-path="url(#window)">',
        f'<rect width="{WIDTH}" height="{height}" fill="{theme["bg"]}"/>',
        f'<rect width="{WIDTH}" height="{title_h}" fill="{theme["chrome"]}"/>',
        f'<rect y="{title_h}" width="{WIDTH}" height="1" fill="{theme["border"]}"/>',
        "</g>",
        f'<rect x="0.5" y="0.5" width="{WIDTH - 1}" height="{height - 1}" rx="10" fill="none" stroke="{theme["border"]}"/>',
    ]
    for i, color in enumerate(("#ff5f57", "#febc2e", "#28c840")):
        parts.append(f'<circle cx="{22 + i * 20}" cy="{title_h / 2}" r="6" fill="{color}"/>')
    parts.append(f'<text class="m" x="{WIDTH / 2}" y="{title_h / 2 + 4.5}" text-anchor="middle" '
                 f'font-size="13">{NAME}@{USERNAME}: ~ (zsh)</text>')

    # typing "neofetch"
    typed = "".join(f'<tspan class="w type" style="animation-delay:{0.35 + i * 0.08:.2f}s">{c}</tspan>'
                    for i, c in enumerate("neofetch"))
    parts.append(prompt(prompt_y, typed))

    # the donut
    parts.append(f'<g class="show" style="animation-delay:{out_start}s">')
    for i, frame in enumerate(frames):
        rows = "".join(f'<tspan x="{DONUT_X}" y="{donut_top + r * DONUT_ROW_H}">{escape(row)}</tspan>'
                       for r, row in enumerate(frame) if row.strip())
        cls = "n f f0" if i == 0 else "n f"
        parts.append(f'<text class="{cls}" style="animation-delay:{i * step:.4f}s">{rows}</text>')
    parts.append("</g>")

    # the info on the right, printed line by line
    for i, line in enumerate(lines):
        y = info_y + i * LINE_H
        delay = f"animation-delay:{out_start + 0.03 * i:.2f}s"
        if line == "palette":
            parts.append(f'<g class="show" style="{delay}">')
            for j, color in enumerate(theme["ansi"]):
                parts.append(f'<rect x="{INFO_X + j * 30}" y="{y - 12}" width="26" height="14" rx="2" fill="{color}"/>')
            parts.append("</g>")
            continue
        if not line:
            continue
        spans = "".join(f'<tspan class="{cls}">{escape(text)}</tspan>' for text, cls in line)
        parts.append(f'<text class="t show" x="{INFO_X}" y="{y}" style="{delay}">{spans}</text>')

    # back to the prompt, with a blinking cursor
    end = out_start + 0.03 * len(lines) + 0.2
    parts.append(f'<g class="show" style="animation-delay:{end:.2f}s">'
                 + prompt(end_prompt_y, '<tspan class="w cursor">█</tspan>') + "</g>")
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Draw the neofetch card for my profile README.")
    parser.add_argument("--demo", action="store_true", help="use made up stats, no token needed")
    parser.add_argument("--placeholder", action="store_true", help="show dashes instead of stats")
    parser.add_argument("--out", default="dist", help="folder to write the SVGs to (default: dist)")
    args = parser.parse_args()

    if args.placeholder:
        stats = None
    elif args.demo:
        stats = DEMO_STATS
    else:
        token = os.environ.get("GITHUB_TOKEN")
        if not token:
            sys.exit("Set GITHUB_TOKEN, or run with --demo to use made up numbers.")
        stats = fetch_stats(token)

    now = datetime.now(ZoneInfo(TIMEZONE))
    lines = card_lines(stats, now.date())
    frames = donut_frames()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for name, theme in THEMES.items():
        svg = build_svg(theme, frames, lines, now.strftime("%Y-%m-%d %H:%M %Z"))
        (out / f"neofetch-{name}.svg").write_text(svg, encoding="utf-8")
    if stats is None:
        print(f"drew a placeholder card into {out}/")
    else:
        print(f"drew the card into {out}/ ({stats['commits']:,} commits, {stats['stars']:,} stars)")


if __name__ == "__main__":
    main()
