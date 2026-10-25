"""'Earth versus this world': side-by-side rows from the archive record and Earth reference values."""
from __future__ import annotations

# Earth / Sun reference values (NASA Earth & Sun fact sheets; IAU 2015 nominal solar values).
EARTH = {
    "pl_rade": (1.0, "Earth radii"),
    "pl_bmasse": (1.0, "Earth masses"),
    "pl_eqt": (254.0, "K"),  # Earth's black-body temperature, NASA Earth Fact Sheet
    "pl_orbper": (365.256, "days"),
    "pl_orbsmax": (1.0, "AU"),
    "pl_insol": (1.0, "x Earth"),
    "st_teff": (5772.0, "K"),
    "st_rad": (1.0, "solar radii"),
    "sy_pnum": (8, "planets"),
}
LABELS = {
    "pl_rade": "Radius", "pl_bmasse": "Mass", "pl_eqt": "Equilibrium temperature",
    "pl_orbper": "Year length (orbital period)", "pl_orbsmax": "Distance from its star",
    "pl_insol": "Starlight received", "st_teff": "Star temperature", "st_rad": "Star radius",
    "sy_pnum": "Planets in the system",
}
SOURCES = "Earth values: NASA Earth Fact Sheet (black-body temperature 254 K), Sun: IAU nominal 5772 K."


def _fmt_ratio(r: float) -> str:
    if r >= 100:
        return f"{r:,.0f}"
    if r >= 10:
        return f"{r:.1f}"
    if r >= 1:
        return f"{r:.2f}".rstrip("0").rstrip(".")
    return f"{r:.3g}"


def _ratio_text(field: str, r: float) -> str:
    if 0.95 <= r <= 1.05:
        return "about the same as Earth" if not field.startswith("st_") else "about the same as the Sun"
    ref = "the Sun" if field.startswith("st_") else ("the Solar System" if field == "sy_pnum" else "Earth")
    shown = _fmt_ratio(r)
    return f"{shown}x {ref}" if r >= 1 else f"{shown}x {ref} (1/{_fmt_ratio(1 / r)})"


def compare_with_earth(rec: dict) -> dict:
    rows = []
    for field, (ev, unit) in EARTH.items():
        v = rec.get(field)
        ratio = (v / ev) if (v is not None and ev) else None
        rows.append({
            "field": field, "label": LABELS[field], "unit": unit, "planet": v, "earth": ev,
            "ratio": round(ratio, 4) if ratio is not None else None,
            "summary": _ratio_text(field, ratio) if ratio is not None else "unknown in the archive",
            "reference": "Sun" if field.startswith("st_") else ("Solar System" if field == "sy_pnum" else "Earth"),
        })
    dist = rec.get("sy_dist")
    extra = {"distance_pc": dist, "light_travel_years": round(dist * 3.26156, 1) if dist is not None else None}
    return {"planet": rec["pl_name"], "rows": rows, "distance": extra, "sources": SOURCES}
