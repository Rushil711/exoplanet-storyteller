"""Safe fallback story built directly from the archive record (always passes verifier.py).

    python template_story.py planet.json [--kid]
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from facts import fmt_value as f, star_color  # noqa: E402


def _article(word: str) -> str:
    return "an" if word[:1].lower() in "aeiou" else "a"


def template_story(rec: dict, kid: bool = False) -> str:
    g = rec.get
    name, host = g("pl_name"), g("hostname") or "its host star"
    color = star_color(g("st_teff"))
    s = []
    if kid:
        s.append(f"Meet {name}, a real planet far beyond our Solar System.")
        if g("st_teff") is not None:
            s.append(f"It circles {host}, {_article(color)} {color} star that is {f(g('st_teff'))} K on the outside.")
        else:
            s.append(f"It circles the star {host}. How hot that star is, is unknown.")
        if g("pl_orbper") is not None:
            s.append(f"A whole year there lasts just {f(g('pl_orbper'))} days.")
        if g("pl_rade") is not None:
            s.append(f"The planet is {f(g('pl_rade'))} times as wide as Earth.")
        else:
            s.append("How big it is, is still unknown.")
        if g("pl_eqt") is not None:
            s.append(f"Scientists estimate its temperature at {f(g('pl_eqt'))} K.")
        if g("sy_dist") is not None:
            s.append(f"It is {f(g('sy_dist'))} parsecs away, so far that its starlight takes a very long time to reach us.")
        s.append(f"People found it in {g('disc_year')} using the {g('discoverymethod')} method.")
        s.append("What it looks like up close is unknown, so it is waiting for you to explore it one day!")
        return " ".join(s)

    s.append(f"{name} is a real world, catalogued in the NASA Exoplanet Archive.")
    if g("st_teff") is not None:
        star = f"{_article(color)} {color} star whose surface glows at {f(g('st_teff'))} K"
        if g("st_spectype"):
            star += f", spectral type {g('st_spectype')}"
        s.append(f"It circles {host}, {star}.")
    else:
        s.append(f"It circles {host}; the temperature of that star is unknown.")
    orbit = []
    if g("pl_orbper") is not None:
        orbit.append(f"once every {f(g('pl_orbper'))} days")
    if g("pl_orbsmax") is not None:
        orbit.append(f"at a distance of {f(g('pl_orbsmax'))} AU")
    if orbit:
        s.append("The planet completes its orbit " + " ".join(orbit) + ".")
    size = f"{f(g('pl_rade'))} times Earth's radius" if g("pl_rade") is not None else "a radius that is unknown"
    mass = f"{f(g('pl_bmasse'))} Earth masses" if g("pl_bmasse") is not None else "a mass that is unknown"
    s.append(f"It measures {size}, with {mass}.")
    if g("pl_eqt") is not None:
        s.append(f"Its equilibrium temperature is {f(g('pl_eqt'))} K.")
    else:
        s.append("Its equilibrium temperature is unknown.")
    if g("pl_insol") is not None:
        s.append(f"It receives {f(g('pl_insol'))} times the starlight Earth receives.")
    if g("sy_dist") is not None:
        s.append(f"The system lies {f(g('sy_dist'))} parsecs from Earth.")
    n = g("sy_pnum")
    if n and n > 1:
        s.append(f"It shares its star with other worlds: {n} known planets in all.")
    fac = g("disc_facility")
    s.append(f"It was discovered in {g('disc_year')} by the {g('discoverymethod')} method"
             + (f", using {fac}." if fac else "."))
    s.append("Everything else about it, from its skies to what it is made of, is still unknown.")
    return " ".join(s)


if __name__ == "__main__":
    data = json.load(open(sys.argv[1]))
    rec = data[0] if isinstance(data, list) else data
    print(template_story(rec, kid="--kid" in sys.argv))
