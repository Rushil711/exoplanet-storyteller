"""Render a pscomppars record as the FACTS block the story rules refer to.

    python facts.py planet.json
"""
from __future__ import annotations

import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from verifier import FIELD_INFO  # noqa: E402

# (upper bound of st_teff in K, color name) - coarse blackbody bins used for the story and the art
STAR_COLORS = [
    (3500, "deep red"),
    (4000, "red-orange"),
    (5200, "orange"),
    (6000, "yellow-white"),
    (7500, "white"),
    (10000, "blue-white"),
    (math.inf, "blue"),
]


def star_color(teff) -> str | None:
    if teff is None:
        return None
    for bound, name in STAR_COLORS:
        if teff < bound:
            return name
    return None


def fmt_value(v) -> str:
    """Human-friendly rendering that the verifier still accepts as exact."""
    if v is None:
        return "unknown"
    if isinstance(v, str):
        return v
    if isinstance(v, bool):
        return str(v)
    if float(v).is_integer():
        return str(int(v))
    a = abs(v)
    if a >= 1000:
        return str(round(v))
    if a >= 100:
        return f"{v:.1f}".rstrip("0").rstrip(".")
    if a >= 1:
        return f"{v:.2f}".rstrip("0").rstrip(".")
    digits = max(2, -int(math.floor(math.log10(a))) + 2)
    return f"{v:.{digits}f}".rstrip("0").rstrip(".")


def facts_lines(rec: dict, raw: bool = True) -> list[str]:
    lines = []
    for field, (label, unit) in FIELD_INFO.items():
        v = rec.get(field)
        if v is None:
            val = "unknown"
            if field == "st_spectype":
                val = "unknown (describe the star by its temperature)"
        else:
            val = (str(int(v)) if isinstance(v, float) and v.is_integer() else str(v)) if raw else fmt_value(v)
            if unit:
                val = f"{val} {unit}"
        lines.append(f"- {label} [{field}]: {val}")
    color = star_color(rec.get("st_teff"))
    lines.append(f"- Host star color (derived from its temperature) [star_color]: {color or 'unknown'}")
    return lines


def facts_block(rec: dict) -> str:
    return "\n".join(facts_lines(rec))


if __name__ == "__main__":
    data = json.load(open(sys.argv[1]))
    print(facts_block(data[0] if isinstance(data, list) else data))
