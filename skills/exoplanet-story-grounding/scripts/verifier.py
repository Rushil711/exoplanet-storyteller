"""Grounding verifier for exoplanet stories (part of the exoplanet-story-grounding Agent Skill).

Extracts every number (digits, "3 million", compound number-words such as "one hundred
sixty-three"), spectral-class token, discovery-method mention and qualitative keyword from a
story and checks it against a NASA Exoplanet Archive `pscomppars` record.

Number statuses:
  exact        matches a data value at the precision shown, with a consistent unit/field
  rounded      matches a coarse rounding (1-3 significant figures / truncation) of a data value
  derived      matches a unit conversion of a data value (K->C, pc->ly, days->hours ...).
               Numerically right, but the skill forbids conversions because models get them wrong.
  text         digits that belong to a name (e.g. "55" in "55 Cancri")
  UNSUPPORTED  anything else: invented, or a real value attached to the wrong field or unit

Qualitative keywords (atmosphere, water, rocky, life ...) are claims no archive field supports.
They fail the check unless the sentence hedges them ("... is unknown").

Pure standard library so the skill runs anywhere:
    python verifier.py planet.json story.txt          # planet.json = record or [record, ...]
    python verifier.py planet.json 0 story.txt        # pick record by index
Exit code 0 = passed, 1 = failed, 2 = usage error.
"""
from __future__ import annotations

import json
import math
import re
import sys

# field -> (label shown to the model, unit as stored in the archive)
FIELD_INFO: dict[str, tuple[str, str]] = {
    "pl_name": ("Planet name", ""),
    "hostname": ("Host star", ""),
    "pl_rade": ("Planet radius", "Earth radii"),
    "pl_bmasse": ("Planet mass", "Earth masses"),
    "pl_eqt": ("Planet equilibrium temperature", "K"),
    "pl_orbper": ("Orbital period", "days"),
    "pl_orbsmax": ("Orbit semi-major axis", "AU"),
    "pl_insol": ("Starlight received (insolation)", "times the starlight Earth receives"),
    "st_teff": ("Host star effective temperature", "K"),
    "st_spectype": ("Host star spectral type", ""),
    "st_rad": ("Host star radius", "solar radii"),
    "sy_dist": ("Distance from Earth", "parsecs"),
    "sy_pnum": ("Known planets in the system", "planets"),
    "disc_year": ("Discovery year", ""),
    "discoverymethod": ("Discovery method", ""),
    "disc_facility": ("Discovery facility", ""),
}
FIELDS = list(FIELD_INFO)
NUMERIC_FIELDS = ["pl_rade", "pl_bmasse", "pl_eqt", "pl_orbper", "pl_orbsmax", "st_teff",
                  "st_rad", "sy_dist", "disc_year", "pl_insol", "sy_pnum"]

_UNITS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
          "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
          "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
          "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
          "seventy": 70, "eighty": 80, "ninety": 90}
_SCALES = {"hundred": 100, "thousand": 1e3, "million": 1e6, "billion": 1e9}
_NW = "|".join(sorted(list(_UNITS) + list(_SCALES), key=len, reverse=True))
NUMWORD_RUN = re.compile(rf"\b(?:{_NW})(?:(?:[\s-]+|\s+and\s+)(?:{_NW}))*\b", re.I)
DIGIT_RE = re.compile(r"(?<![\w.])(-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|-?\d+(?:\.\d+)?)"
                      r"(?:\s*(million|billion|thousand)\b)?")
MULT = {"million": 1e6, "billion": 1e9, "thousand": 1e3}

# unit pattern (matched right after the number) -> (raw fields allowed, conversions allowed)
UNIT_RULES: list[tuple[str, tuple[list[str], list[str]]]] = [
    (r"^\s*(solar radi|times (the )?(sun'?s|solar) (radius|size)|suns?\b)", (["st_rad"], ["pct"])),
    (r"^\s*(earth[- ]radi|times (that of |the size of )?earth'?s? (radius|size|width)|times (as wide|as big|bigger|larger|the size) (as|than)? ?earth)",
     (["pl_rade"], [])),
    (r"^\s*(times? (the )?(radius|size)|radii)", (["pl_rade", "st_rad"], [])),
    (r"^\s*(earth[- ]mass|earth masses|times (that of )?(earth'?s? )?mass|times (as massive|heavier) (as|than) earth)",
     (["pl_bmasse"], [])),
    (r"^\s*(days?|d\b|earth days)", (["pl_orbper"], [])),
    (r"^\s*(hours?|hrs?)", ([], ["days->hours"])),
    (r"^\s*(minutes?)", ([], ["days->min"])),
    (r"^\s*(years?)\b(?! ago)", ([], ["days->years"])),
    (r"^\s*(weeks?)", ([], ["days->weeks"])),
    (r"^\s*(k\b|kelvin|degrees kelvin|degrees k\b|°\s?k)", (["pl_eqt", "st_teff"], [])),
    (r"^\s*(°\s?c|degrees c\b|celsius|degrees celsius|degrees centigrade)", ([], ["K->C"])),
    (r"^\s*(°\s?f|degrees f\b|fahrenheit|degrees fahrenheit)", ([], ["K->F"])),
    (r"^\s*(degrees below freezing|degrees under freezing)", ([], ["below-freezing"])),
    (r"^\s*(light[- ]?years?|ly\b)", ([], ["pc->ly"])),
    (r"^\s*(parsecs?|pc\b)", (["sy_dist"], [])),
    (r"^\s*(au\b|astronomical units?|a\.u\.)", (["pl_orbsmax"], [])),
    (r"^\s*(million (km|kilomet))", ([], ["AU->Mkm"])),
    (r"^\s*(km|kilometers|kilometres)", ([], ["AU->km", "Re->km", "Rsun->km", "pc->km"])),
    (r"^\s*(miles?|mi\b)", ([], ["AU->mi", "Re->mi"])),
    (r"^\s*(times (the |more |as much )?(sunlight|light|solar|insolation|energy|radiation|flux|starlight)|x earth|earth[- ]flux)",
     (["pl_insol"], [])),
    (r"^\s*((known |other |sibling )?(planets?|worlds?))\b", (["sy_pnum"], [])),
    (r"^\s*(%|percent)", ([], ["pct"])),
]

STAR_CONTEXT = re.compile(r"\b(star|stars|sun|suns|stellar|host|dwarf|giant star)\b", re.I)
HEDGE = re.compile(r"\b(unknown|not known|isn't known|is not known|no one knows|nobody knows|"
                   r"don't know|do not know|doesn't know|uncertain|a mystery|mysterious|"
                   r"no data|not measured|unmeasured|cannot say|can't say|if it has|whether)\b", re.I)

QUAL_HARD = ["habitable zone", "habitable", "habitability", "liquid water", "ocean", "oceans",
             "atmosphere", "atmospheres", "oxygen", "nitrogen", "carbon dioxide", "methane",
             "life", "lifeforms", "inhabitants", "inhabited", "civilization", "water", "clouds",
             "haze", "volcano", "volcanoes", "lava", "molten", "rain", "storms", "winds",
             "magnetic", "moon", "moons", "rings", "rocky", "terrestrial", "gas giant", "ice",
             "iron", "diamond", "earth-like", "earthlike", "tidally locked"]
QUAL_SOFT = ["reddish", "hue", "temperate", "comfortable", "warm", "cool", "dense", "alien",
             "frozen", "icy", "scorching", "surface"]

METHOD_TERMS = {
    "Transit": ["transit", "transits", "transiting", "dimming"],
    "Radial Velocity": ["radial velocity", "wobble", "doppler"],
    "Microlensing": ["microlensing", "gravitational lens"],
    "Imaging": ["direct imaging", "directly imaged", "photographed"],
    "Transit Timing Variations": ["transit timing"],
    "Pulsar Timing": ["pulsar timing"],
    "Astrometry": ["astrometry"],
}

OK_STATUSES = {"exact", "rounded", "text", "hedged"}
UNIT_FIELD_NAMES = {"pl_rade": "radi", "pl_bmasse": "mass", "pl_eqt": "k", "st_teff": "k", "pl_orbper": "day",
                    "pl_orbsmax": "au", "sy_dist": "parsec", "st_rad": "solar", "pl_insol": "times", "sy_pnum": "planet"}


def conversions(rec: dict) -> list[tuple[float, str, str]]:
    """(value, field, label) for the unit conversions a story might use."""
    out: list[tuple[float, str, str]] = []

    def add(v, f, label):
        if v is not None:
            out.append((v, f, label))

    for f in ("pl_eqt", "st_teff"):
        t = rec.get(f)
        if t is not None:
            add(t - 273.15, f, "K->C")
            add((t - 273.15) * 9 / 5 + 32, f, "K->F")
    p = rec.get("pl_orbper")
    if p is not None:
        add(p * 24, "pl_orbper", "days->hours")
        add(p * 24 * 60, "pl_orbper", "days->min")
        add(p / 365.25, "pl_orbper", "days->years")
        add(p / 7, "pl_orbper", "days->weeks")
    d = rec.get("sy_dist")
    if d is not None:
        add(d * 3.26156, "sy_dist", "pc->ly")
        add(d * 3.0857e13, "sy_dist", "pc->km")
    a = rec.get("pl_orbsmax")
    if a is not None:
        add(a * 149.598e6, "pl_orbsmax", "AU->km")
        add(a * 92.956e6, "pl_orbsmax", "AU->mi")
        add(a * 149.598, "pl_orbsmax", "AU->Mkm")
    r = rec.get("pl_rade")
    if r is not None:
        add(r * 6371, "pl_rade", "Re->km")
        add(r * 3959, "pl_rade", "Re->mi")
    sr = rec.get("st_rad")
    if sr is not None:
        add(sr * 695700, "st_rad", "Rsun->km")
    for f in ("pl_rade", "pl_bmasse", "pl_insol", "st_rad"):
        if rec.get(f) is not None:
            add(rec[f] * 100, f, "pct")
    if rec.get("pl_eqt") is not None:
        add(273.15 - rec["pl_eqt"], "pl_eqt", "below-freezing")
    return out


def decimals(s: str) -> int:
    s = s.replace(",", "")
    return len(s.split(".")[1]) if "." in s else 0


def sigfig_round(v: float, n: int) -> float:
    if v == 0:
        return 0
    return round(v, -int(math.floor(math.log10(abs(v)))) + (n - 1))


def match_value(x: float, s: str, v) -> str | None:
    """'exact' | 'rounded' | None for story number x (written as s) against data value v."""
    if v is None:
        return None
    dec = decimals(s)
    if abs(round(v, dec) - x) < 10 ** -(dec + 3):
        sig = len(re.sub(r"\D", "", s).strip("0"))
        return "exact" if (abs(x - v) <= 0.01 * abs(v) or sig >= 2) else "rounded"
    for n in (1, 2, 3):
        if abs(sigfig_round(v, n) - x) < 1e-9 * max(1, abs(x)):
            return "rounded"
    if dec == 0 and abs(int(v) - x) < 1e-9:
        return "rounded"
    return None


def words_to_number(phrase: str) -> float | None:
    total, current, seen = 0.0, 0.0, False
    for w in re.split(r"[\s-]+", phrase.lower()):
        if w == "and" or not w:
            continue
        if w in _UNITS:
            current += _UNITS[w]
            seen = True
        elif w == "hundred":
            current = (current or 1) * 100
            seen = True
        elif w in _SCALES:
            total += (current or 1) * _SCALES[w]
            current = 0
            seen = True
    return total + current if seen else None


def text_digits(rec: dict) -> set[float]:
    s = " ".join(str(rec.get(k) or "") for k in ("pl_name", "hostname", "st_spectype", "disc_facility"))
    return {float(m) for m in re.findall(r"\d+(?:\.\d+)?", s)}


def mask_names(story: str, rec: dict, extra_names=()) -> str:
    """Blank out names (same length, so offsets stay valid) so their digits aren't checked."""
    names = [rec.get(k) for k in ("pl_name", "hostname", "disc_facility")] + list(extra_names)
    for v in sorted({n for n in names if n}, key=len, reverse=True):
        story = re.sub(re.escape(v), lambda m: " " * len(m.group(0)), story, flags=re.I)
    return story


def _sentence_bounds(story: str) -> list[tuple[int, int]]:
    bounds, start = [], 0
    for m in re.finditer(r"[.!?](?:\s+|$)|\n+", story):
        bounds.append((start, m.end()))
        start = m.end()
    if start < len(story):
        bounds.append((start, len(story)))
    return bounds


def _sentence_at(story: str, bounds, pos: int) -> str:
    for a, b in bounds:
        if a <= pos < b:
            return story[a:b]
    return story


_CLAUSE_SPLIT = re.compile(r"[,;:()\u2014\u2013]|\s-\s|\b(?:but|though|although|while|yet)\b", re.I)


def _clause_at(story: str, bounds, pos: int) -> str:
    """The clause around `pos`, so a hedge elsewhere in the sentence doesn't excuse a claim."""
    for a, b in bounds:
        if a <= pos < b:
            start = a
            for m in _CLAUSE_SPLIT.finditer(story, a, b):
                if m.end() <= pos:
                    start = m.end()
                elif m.start() > pos:
                    return story[start:m.start()]
            return story[start:b]
    return story


# archive field behind each conversion label, for feedback
CONV_FIELD = {"K->C": "pl_eqt", "K->F": "pl_eqt", "below-freezing": "pl_eqt", "days->hours": "pl_orbper",
              "days->min": "pl_orbper", "days->years": "pl_orbper", "days->weeks": "pl_orbper",
              "pc->ly": "sy_dist", "AU->Mkm": "pl_orbsmax"}


def _numbers(masked: str):
    """Yield (start, end, text, value) for digits and number-word runs."""
    for m in DIGIT_RE.finditer(masked):
        s = m.group(1)
        x = float(s.replace(",", ""))
        if m.group(2):
            x *= MULT[m.group(2).lower()]
        yield m.start(), m.end(), m.group(0), s, x
    for m in NUMWORD_RUN.finditer(masked):
        phrase = m.group(0)
        if re.fullmatch(r"(one|zero|hundred|thousand|million|billion)", phrase, re.I):
            continue  # "one of the ...", "thousands of": too ambiguous to be a claim
        x = words_to_number(phrase)
        if x is not None:
            yield m.start(), m.end(), phrase, str(int(x)) if x == int(x) else str(x), x


def _spec_match(tok: str, data_tok: str) -> bool:
    if tok == data_tok:
        return True
    if "." not in tok and data_tok.split(".")[0] == tok:
        return True
    return "." not in data_tok and tok.split(".")[0] == data_tok


def _fmt(v) -> str:
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def verify(story: str, rec: dict, extra_names=()) -> dict:
    """Check a story against one archive record. `extra_names` are other planet names the
    story may mention (e.g. planets told earlier in the session) whose digits are not claims."""
    results = []
    masked = mask_names(story, rec, extra_names)
    bounds = _sentence_bounds(story)
    lname_digits = text_digits(rec)

    # spectral types like "M4.5+/-0.5 V" are checked as one token below, not as numbers
    masked_num = mask_names(masked, {}, [rec["st_spectype"]] if rec.get("st_spectype") else [])
    spans = sorted(_numbers(masked_num), key=lambda t: t[0])
    for start, end, text, s, x in spans:
        after = masked_num[end:end + 48].lower()
        half = re.match(r"^\s*and a half", after)
        if half:
            x += 0.5
            s = str(x)
            end += len(half.group(0))
            text = story[start:end]
            after = after[len(half.group(0)):]
        rule, unit = None, ""
        for pat, rl in UNIT_RULES:
            um = re.match(pat, after)
            if um:
                rule, unit = rl, story[end + um.start():end + um.end()].strip()
                break
        raw_fields = list(rule[0]) if rule else list(NUMERIC_FIELDS)
        conv_ok = rule[1] if rule else None
        sentence = _sentence_at(story, bounds, start)
        star_ctx = bool(STAR_CONTEXT.search(sentence))
        if not star_ctx:
            # a temperature or radius with no star in the sentence is about the planet
            if raw_fields == ["pl_eqt", "st_teff"]:
                raw_fields = ["pl_eqt"]
            if raw_fields == ["pl_rade", "st_rad"]:
                raw_fields = ["pl_rade"]
        status, via = None, None
        for f in raw_fields:
            r = match_value(x, s, rec.get(f))
            if r:
                status, via = r, f
                break
        if not status and rule is None and x in lname_digits:
            status, via = "text", "name"
        if not status:
            for v, f, label in conversions(rec):
                if conv_ok is not None and label not in conv_ok:
                    continue
                if not star_ctx and f == "st_teff" and label in ("K->C", "K->F"):
                    continue
                if v and (abs(x - v) / abs(v) <= 0.02 or abs(sigfig_round(v, 1) - x) < 1e-9
                          or abs(sigfig_round(v, 2) - x) < 1e-9 or abs(round(v, decimals(s)) - x) < 1e-9):
                    status, via = "derived", f + ":" + label
                    break
        field = via.split(":")[0] if via and via != "name" else None
        expected = None
        if status is None and rule:
            exp_fields = raw_fields or sorted({CONV_FIELD[c] for c in rule[1] if c in CONV_FIELD})
            if exp_fields:
                expected = {f: rec.get(f) for f in exp_fields}
        results.append({
            "kind": "number", "text": text, "value": x, "unit": unit, "unit_ctx": after[:20].strip(),
            "status": status or "UNSUPPORTED", "via": via, "field": field,
            "data_value": rec.get(field) if field else None,
            "expected": expected, "start": start, "end": end,
        })

    spec = rec.get("st_spectype")
    for sm in re.finditer(r"\b([OBAFGKM]\d(?:\.\d)?)(?![-\w])\s?(?:V|IV|III|II|I)?\b", masked):
        tok = sm.group(1)
        ok = bool(spec) and any(_spec_match(tok, t) for t in re.findall(r"[OBAFGKM]\d(?:\.\d)?", spec))
        results.append({
            "kind": "spectral", "text": tok, "value": None, "unit_ctx": "spectral class",
            "status": "exact" if ok else "UNSUPPORTED", "field": "st_spectype", "data_value": spec,
            "via": "st_spectype" if ok else ("st_spectype is unknown" if not spec else f"data={spec}"),
            "expected": None, "start": sm.start(1), "end": sm.end(1),
        })

    low_masked = masked.lower()
    method = rec.get("discoverymethod")
    for mname, terms in METHOD_TERMS.items():
        for term in terms:
            for mm in re.finditer(r"\b" + re.escape(term) + r"\b", low_masked):
                ok = mname == method or (mname == "Transit" and method and "Transit" in method)
                results.append({
                    "kind": "method", "text": story[mm.start():mm.end()], "value": None,
                    "unit_ctx": "discovery method", "status": "exact" if ok else "UNSUPPORTED",
                    "field": "discoverymethod", "data_value": method,
                    "via": "discoverymethod" if ok else f"data={method}",
                    "expected": None, "start": mm.start(), "end": mm.end(),
                })

    qual_hard, hedged, qual_soft = [], [], []
    taken: list[tuple[int, int]] = []
    for w in sorted(QUAL_HARD, key=len, reverse=True):
        for qm in re.finditer(r"\b" + re.escape(w) + r"\b", low_masked):
            if any(a <= qm.start() < b for a, b in taken):
                continue
            taken.append((qm.start(), qm.end()))
            is_hedged = bool(HEDGE.search(_clause_at(story, bounds, qm.start())))
            (hedged if is_hedged else qual_hard).append(w)
            results.append({
                "kind": "qualitative", "text": story[qm.start():qm.end()], "value": None,
                "unit_ctx": "claim", "status": "hedged" if is_hedged else "UNSUPPORTED",
                "field": None, "data_value": None,
                "via": "stated as unknown" if is_hedged else "no archive field supports this",
                "expected": None, "start": qm.start(), "end": qm.end(),
            })
    for w in QUAL_SOFT:
        if re.search(r"\b" + re.escape(w) + r"\b", low_masked):
            qual_soft.append(w)

    results.sort(key=lambda r: r["start"])
    numbers = [r for r in results if r["kind"] in ("number", "spectral")]
    unsup_num = [r for r in numbers if r["status"] == "UNSUPPORTED"]
    method_bad = [r for r in results if r["kind"] == "method" and r["status"] == "UNSUPPORTED"]
    n_derived = sum(r["status"] == "derived" for r in numbers)
    weights = {"exact": 1.0, "rounded": 1.0, "text": 1.0, "hedged": 1.0, "derived": 0.5, "UNSUPPORTED": 0.0}
    scored = [r for r in results if r["status"] != "text"]
    score = round(100 * sum(weights[r["status"]] for r in scored) / len(scored)) if scored else 100
    passed = not unsup_num and not n_derived and not qual_hard and not method_bad
    return {
        "claims": results,
        "numbers": numbers,
        "n_numbers": len(numbers),
        "n_unsupported": len(unsup_num),
        "n_derived": n_derived,
        "qualitative_flags": sorted(set(qual_hard)),
        "hedged_flags": sorted(set(hedged)),
        "soft_flags": qual_soft,
        "method_flags": [r["text"] for r in method_bad],
        "pass_lenient": not unsup_num,
        "pass_strict": not unsup_num and not n_derived,
        "pass_strict_incl_qual": passed,
        "passed": passed,
        "score": score,
    }


def feedback(result: dict, rec: dict) -> list[str]:
    """Concrete, data-backed rewrite instructions for every failed claim."""
    out = []
    for c in result["claims"]:
        if c["status"] == "derived":
            f = c["field"]
            unit = FIELD_INFO.get(f, ("", ""))[1]
            out.append(f'"{c["text"]} {c.get("unit", "")}" is a unit conversion of {f}. '
                       f'Do not convert units; use the archive value: {_fmt(rec.get(f))} {unit}.'.replace(" \"", "\""))
        elif c["status"] != "UNSUPPORTED":
            continue
        elif c["kind"] == "number":
            if c.get("expected"):
                parts = []
                for f, v in c["expected"].items():
                    label, unit = FIELD_INFO.get(f, (f, ""))
                    parts.append(f"{label} ({f}) = {_fmt(v)} {unit}".strip() if v is not None
                                 else f"{label} ({f}) is unknown")
                conv = "" if any(f in UNIT_FIELD_NAMES and UNIT_FIELD_NAMES[f] in c["unit"].lower() for f in c["expected"]) \
                    else " Do not convert units."
                out.append(f'"{c["text"]} {c["unit"]}" does not match the data: ' + "; ".join(parts)
                           + "." + conv + " Copy the number and unit exactly from FACTS, or say it is unknown.")
            else:
                out.append(f'"{c["text"]}" does not match any value in FACTS. Remove it or copy a value from FACTS exactly.')
        elif c["kind"] == "spectral":
            spec = rec.get("st_spectype")
            out.append(f'Spectral type "{c["text"]}" is wrong: ' +
                       (f"the archive says {spec}." if spec else "the spectral type is unknown; describe the star by its temperature instead."))
        elif c["kind"] == "method":
            out.append(f'"{c["text"]}" implies the wrong discovery method; the archive says {rec.get("discoverymethod")}.')
        elif c["kind"] == "qualitative":
            out.append(f'"{c["text"]}" is not in the data. Remove it, or say explicitly that it is unknown.')
    seen, uniq = set(), []
    for line in out:
        if line not in seen:
            seen.add(line)
            uniq.append(line)
    return uniq


def _main(argv: list[str]) -> int:
    if len(argv) not in (3, 4):
        print(__doc__, file=sys.stderr)
        return 2
    data = json.load(open(argv[1]))
    if len(argv) == 4:
        rec = data[int(argv[2])]
        story_path = argv[3]
    else:
        rec = data[0] if isinstance(data, list) else data
        story_path = argv[2]
    story = open(story_path).read()
    res = verify(story, rec)
    res["feedback"] = feedback(res, rec)
    print(json.dumps(res, indent=1))
    return 0 if res["passed"] else 1


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
