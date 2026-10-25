"""Art direction derived from the archive record, plus a procedural SVG used in mock mode.

Each visual choice is returned with the field and value it came from, so the UI can show
"why the art looks like this".
"""
from __future__ import annotations

import hashlib
import math
import random
from html import escape


def blackbody_hex(teff: float | None) -> str:
    """Approximate sRGB color of a blackbody (Tanner Helland fit), clamped to stellar range."""
    if teff is None:
        return "#d8d2c4"
    t = max(1500.0, min(40000.0, teff)) / 100.0
    if t <= 66:
        r = 255.0
        g = 99.4708025861 * math.log(t) - 161.1195681661
        b = 0 if t <= 19 else 138.5177312231 * math.log(t - 10) - 305.0447927307
    else:
        r = 329.698727446 * ((t - 60) ** -0.1332047592)
        g = 288.1221695283 * ((t - 60) ** -0.0755148492)
        b = 255.0
    c = lambda v: max(0, min(255, int(round(v))))
    return f"#{c(r):02x}{c(g):02x}{c(b):02x}"


def size_class(rade):
    if rade is None:
        return None, "a planet of unknown size"
    if rade < 1.25:
        return "earth", "an Earth-sized planet"
    if rade < 2:
        return "super-earth", "a planet somewhat larger than Earth"
    if rade < 6:
        return "neptune", "a Neptune-sized planet"
    if rade < 15:
        return "jupiter", "a Jupiter-sized giant planet"
    return "giant", "a giant planet larger than Jupiter"


def temperature_mood(eqt):
    if eqt is None:
        return "muted, mysterious palette (temperature unknown)"
    if eqt >= 1500:
        return "incandescent, ember-and-furnace palette, heat shimmer"
    if eqt >= 700:
        return "scorching, glaring palette with heat haze"
    if eqt >= 350:
        return "hot, amber-tinted light"
    if eqt >= 180:
        return "soft, temperate light"
    return "frigid deep-blue twilight palette"


def star_in_sky(st_rad, orbsmax):
    """Angular size of the star relative to the Sun seen from Earth = R_star[Rsun] / a[AU]."""
    if st_rad is None or orbsmax is None or orbsmax <= 0:
        return None, "the host star shining nearby"
    ratio = st_rad / orbsmax
    if ratio > 8:
        return ratio, f"the star looms enormous in the sky, about {ratio:.0f} times wider than our Sun looks from Earth"
    if ratio > 1.5:
        return ratio, f"the star appears about {ratio:.1f} times wider than our Sun looks from Earth"
    if ratio > 0.6:
        return ratio, "the star appears about as large as our Sun does from Earth"
    return ratio, "the star is a small, intense disc in a dark sky"


def art_direction(rec: dict, star_color_name: str | None) -> dict:
    g = rec.get
    hexc = blackbody_hex(g("st_teff"))
    sclass, sdesc = size_class(g("pl_rade"))
    ratio, sky = star_in_sky(g("st_rad"), g("pl_orbsmax"))
    mood = temperature_mood(g("pl_eqt"))
    color_txt = f"{star_color_name} ({int(g('st_teff'))} K)" if g("st_teff") is not None else "of unknown temperature"
    derivation = [
        {"aspect": "Star color", "value": f"{star_color_name or 'unknown'} {hexc}", "field": "st_teff",
         "data": g("st_teff"), "why": "blackbody color of the star's effective temperature"},
        {"aspect": "Planet size", "value": sdesc, "field": "pl_rade", "data": g("pl_rade"),
         "why": "radius in Earth radii"},
        {"aspect": "Star size in the sky", "value": sky, "field": "st_rad / pl_orbsmax",
         "data": round(ratio, 2) if ratio else None, "why": "star radius divided by orbit distance"},
        {"aspect": "Mood", "value": mood, "field": "pl_eqt", "data": g("pl_eqt"), "why": "equilibrium temperature"},
    ]
    if (g("sy_pnum") or 0) > 1:
        derivation.append({"aspect": "Siblings", "value": f"{g('sy_pnum') - 1} other planets as faint points of light",
                           "field": "sy_pnum", "data": g("sy_pnum"), "why": "known planets in the system"})
    prompt = (
        f"Cinematic, scientifically grounded space art of the real exoplanet {g('pl_name')}: {sdesc} seen from "
        f"orbit, lit by its {color_txt} host star {g('hostname')}; {sky}. Mood: {mood}. "
        + (f"{g('sy_pnum') - 1} sibling planets as faint points of light. " if (g("sy_pnum") or 0) > 1 else "")
        + "Its surface and atmosphere are unknown, so render the planet as a softly shaded sphere with "
          "ambiguous, painterly detail rather than specific landforms, oceans or clouds. "
          "Deep starfield background, photorealistic lighting, no text, no labels, no spacecraft."
    )
    return {"prompt": prompt, "derivation": derivation, "star_hex": hexc, "size_class": sclass,
            "star_ratio": ratio, "mood": mood}


def mock_svg(rec: dict, direction: dict) -> str:
    """Data-driven placeholder render for mock mode (no Grok Imagine call)."""
    seed = int(hashlib.sha256(rec["pl_name"].encode()).hexdigest()[:8], 16)
    rnd = random.Random(seed)
    W, H = 1024, 768
    star = direction["star_hex"]
    ratio = direction["star_ratio"] or 1.0
    star_r = max(40, min(420, 60 * ratio ** 0.5))
    rade = rec.get("pl_rade") or 1.0
    pr = max(26, min(230, 34 * rade ** 0.55))
    eqt = rec.get("pl_eqt")
    if eqt is None:
        pc1, pc2 = "#6b6f80", "#2a2c36"
    elif eqt >= 1500:
        pc1, pc2 = "#ff9a4a", "#5a1608"
    elif eqt >= 700:
        pc1, pc2 = "#f0b070", "#4a2412"
    elif eqt >= 350:
        pc1, pc2 = "#d9b88a", "#3a2c22"
    elif eqt >= 180:
        pc1, pc2 = "#8fb3c9", "#1d2c3c"
    else:
        pc1, pc2 = "#9cc4ff", "#0f1a36"
    stars = "".join(
        f'<circle cx="{rnd.uniform(0, W):.0f}" cy="{rnd.uniform(0, H):.0f}" r="{rnd.uniform(0.4, 1.8):.1f}" '
        f'fill="#fff" opacity="{rnd.uniform(0.25, 0.95):.2f}"/>' for _ in range(220))
    sibs = ""
    for i in range(min(8, max(0, (rec.get("sy_pnum") or 1) - 1))):
        sibs += f'<circle cx="{rnd.uniform(380, 980):.0f}" cy="{rnd.uniform(60, 300):.0f}" r="{rnd.uniform(2, 4):.1f}" fill="{star}" opacity="0.8"/>'
    bands = "".join(
        f'<ellipse cx="0" cy="{(i - 3) * pr * 0.28:.1f}" rx="{pr * 1.05:.1f}" ry="{pr * 0.06:.1f}" fill="#fff" opacity="0.05"/>'
        for i in range(7)) if (rec.get("pl_rade") or 0) >= 6 else ""
    name = escape(rec["pl_name"])
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}">
<defs>
 <radialGradient id="sky" cx="18%" cy="22%" r="95%"><stop offset="0" stop-color="{star}" stop-opacity="0.35"/><stop offset="0.45" stop-color="#0b0f24"/><stop offset="1" stop-color="#03040b"/></radialGradient>
 <radialGradient id="star" cx="50%" cy="50%" r="50%"><stop offset="0" stop-color="#ffffff"/><stop offset="0.35" stop-color="{star}"/><stop offset="1" stop-color="{star}" stop-opacity="0"/></radialGradient>
 <radialGradient id="pl" cx="30%" cy="30%" r="80%"><stop offset="0" stop-color="{pc1}"/><stop offset="1" stop-color="{pc2}"/></radialGradient>
 <radialGradient id="shade" cx="85%" cy="85%" r="90%"><stop offset="0.3" stop-color="#000" stop-opacity="0"/><stop offset="1" stop-color="#000" stop-opacity="0.85"/></radialGradient>
 <clipPath id="pc"><circle cx="0" cy="0" r="{pr:.1f}"/></clipPath>
</defs>
<rect width="{W}" height="{H}" fill="url(#sky)"/>{stars}{sibs}
<circle cx="190" cy="170" r="{star_r * 2.2:.0f}" fill="url(#star)" opacity="0.55"/>
<circle cx="190" cy="170" r="{star_r * 0.62:.0f}" fill="url(#star)"/>
<g transform="translate(660 470)"><circle r="{pr * 1.12:.1f}" fill="{pc1}" opacity="0.08"/><circle r="{pr:.1f}" fill="url(#pl)"/><g clip-path="url(#pc)">{bands}</g><circle r="{pr:.1f}" fill="url(#shade)"/></g>
<rect x="24" y="{H - 64}" width="{W - 48}" height="40" rx="10" fill="#000" opacity="0.45"/>
<text x="44" y="{H - 38}" font-family="system-ui, sans-serif" font-size="18" fill="#e8ecff">MOCK RENDER (no Grok Imagine call) - {name} - drawn from archive data</text>
</svg>"""
