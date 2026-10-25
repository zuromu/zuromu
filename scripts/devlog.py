#!/usr/bin/env python3
"""Keeps the devlog list in my README up to date.

For every project in SOURCES it reads the devlog folder, takes each post's title
(its first "# " heading) and the day it was first committed, and writes the list
between the devlog markers in README.md. New posts show up on the next nightly run.

    GITHUB_TOKEN=xxx python3 scripts/devlog.py   update README.md for real
    python3 scripts/devlog.py --demo             just print a made up list
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from github_api import graphql

USERNAME = os.environ.get("GITHUB_USER", "zuromu")
TIMEZONE = "Asia/Jakarta"

# (repo, folder with the posts). add more projects here as they get devlogs
SOURCES = [
    ("swish-rl", "docs/devlog"),
]

README = Path(__file__).resolve().parent.parent / "README.md"
START, END = "<!-- devlog:start -->", "<!-- devlog:end -->"

FOLDER_QUERY = """
query($owner: String!, $name: String!, $expr: String!) {
  repository(owner: $owner, name: $name) {
    url
    defaultBranchRef { name }
    object(expression: $expr) {
      ... on Tree { entries { name object { ... on Blob { text } } } }
    }
  }
}
"""

FIRST_COMMIT_QUERY = """
query($owner: String!, $name: String!, $path: String!) {
  repository(owner: $owner, name: $name) {
    defaultBranchRef { target { ... on Commit {
      history(first: 100, path: $path) { nodes { committedDate } }
    } } }
  }
}
"""


def title_of(text: str, filename: str) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return filename.removesuffix(".md").replace("-", " ")


def fetch_posts(token: str) -> list[dict]:
    posts = []
    for repo, folder in SOURCES:
        data = graphql(FOLDER_QUERY, {"owner": USERNAME, "name": repo, "expr": f"HEAD:{folder}"}, token)
        repository = data["repository"]
        if not repository or not repository["object"]:  # repo is private or gone, or no devlog yet
            continue
        branch = repository["defaultBranchRef"]["name"]
        for entry in repository["object"]["entries"]:
            if not entry["name"].endswith(".md") or not entry["object"]:
                continue
            path = f"{folder}/{entry['name']}"
            history = graphql(FIRST_COMMIT_QUERY, {"owner": USERNAME, "name": repo, "path": path}, token)
            dates = [n["committedDate"]
                     for n in history["repository"]["defaultBranchRef"]["target"]["history"]["nodes"]]
            posts.append({
                "title": title_of(entry["object"]["text"], entry["name"]),
                "url": f"{repository['url']}/blob/{branch}/{path}",
                "repo": repo,
                "date": min(dates) if dates else None,
            })
    return posts


def demo_posts() -> list[dict]:
    return [
        {"title": "M2: Court, hoop and shooting", "url": "https://example.com/m2", "repo": "swish-rl",
         "date": "2026-11-20T10:00:00Z"},
        {"title": "M1: Physics sandbox", "url": "https://example.com/m1", "repo": "swish-rl",
         "date": "2026-10-25T10:00:00Z"},
    ]


def nice_date(iso: str | None) -> str:
    if not iso:
        return ""
    when = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(ZoneInfo(TIMEZONE))
    return f", {when:%b} {when.day}, {when.year}"


def render(posts: list[dict]) -> str:
    if not posts:
        return "- nothing yet"
    posts = sorted(posts, key=lambda p: (p["date"] or "", p["title"]), reverse=True)
    return "\n".join(
        f"- [{p['title'].replace('[', '(').replace(']', ')')}]({p['url']}) in {p['repo']}{nice_date(p['date'])}"
        for p in posts)


def update_readme(block: str) -> bool:
    text = README.read_text(encoding="utf-8")
    if START not in text or END not in text:
        sys.exit(f"README.md needs {START} and {END} where the list should go.")
    before, rest = text.split(START, 1)
    _, after = rest.split(END, 1)
    new = f"{before}{START}\n{block}\n{END}{after}"
    if new == text:
        return False
    README.write_text(new, encoding="utf-8")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Update the devlog list in README.md.")
    parser.add_argument("--demo", action="store_true", help="print a made up list, change nothing")
    args = parser.parse_args()

    if args.demo:
        print(render(demo_posts()))
        return
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        sys.exit("Set GITHUB_TOKEN, or run with --demo to just print a made up list.")
    posts = fetch_posts(token)
    changed = update_readme(render(posts))
    print(f"found {len(posts)} devlog posts, README {'updated' if changed else 'already up to date'}")


if __name__ == "__main__":
    main()
