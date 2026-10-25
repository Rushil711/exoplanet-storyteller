"""Fetch one planet's record from the NASA Exoplanet Archive (TAP sync, table pscomppars).

    python fetch_planet.py "TRAPPIST-1 e" > planet.json

Standard library only. Prints a JSON list with one record; exits 1 if the planet is unknown.
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from verifier import FIELDS  # noqa: E402

TAP_URL = "https://exoplanetarchive.ipac.caltech.edu/TAP/sync"
NAME_RE = re.compile(r"^[A-Za-z0-9 .+\-'()/_]{1,64}$")


def adql_for(name: str) -> str:
    if not NAME_RE.match(name):
        raise ValueError(f"not a valid planet name: {name!r}")
    literal = name.replace("'", "''")
    return f"select {','.join(FIELDS)} from pscomppars where pl_name = '{literal}'"


def fetch(name: str, timeout: float = 60) -> list[dict]:
    url = TAP_URL + "?" + urllib.parse.urlencode({"query": adql_for(name), "format": "json"})
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.load(resp)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    rows = fetch(sys.argv[1])
    if not rows:
        sys.exit(f"planet not found in pscomppars: {sys.argv[1]}")
    print(json.dumps(rows, indent=1))
