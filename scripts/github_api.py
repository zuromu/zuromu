"""Tiny helper for GitHub's GraphQL API, shared by the scripts in this folder."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

API = "https://api.github.com/graphql"


def graphql(query: str, variables: dict, token: str, *, allow_not_found: bool = False) -> dict:
    """Run one query and return its data. Retries a couple of times if GitHub hiccups."""
    body = json.dumps({"query": query, "variables": variables}).encode()
    request = urllib.request.Request(API, data=body, headers={
        "Authorization": f"bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": "zuromu-profile",
    })
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                data = json.load(response)
            break
        except urllib.error.HTTPError as err:
            # GitHub sometimes 502s on bigger queries, so just try again
            if err.code in (502, 503, 504) and attempt < 2:
                time.sleep(5 * (attempt + 1))
                continue
            raise
    errors = data.get("errors")
    if errors:
        # GitHub puts NOT_FOUND in errors alongside null data for private or missing repos
        if allow_not_found and data.get("data") is not None and all(e.get("type") == "NOT_FOUND" for e in errors):
            return data["data"]
        raise RuntimeError(f"GitHub API error: {errors}")
    return data["data"]
