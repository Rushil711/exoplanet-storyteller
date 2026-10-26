"""Natural-language request -> validated SearchIntent -> ADQL built by code (never by the LLM).

Safety model:
  * The only thing that can reach TAP is `build_adql(intent)`.
  * `SearchIntent` is a pydantic model whose fields, operators and discovery methods are closed
    enums and whose values are finite floats, so no user or model text is ever interpolated.
  * Free text is parsed by deterministic rules first; the optional LLM parser only fills the same
    schema (structured JSON) and its JSON is validated before use.
"""
from __future__ import annotations

import json
import math
import re
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

NumField = Literal["pl_rade", "pl_bmasse", "pl_eqt", "pl_orbper", "pl_orbsmax", "pl_insol",
                   "st_teff", "st_rad", "sy_dist", "sy_pnum", "disc_year"]
Method = Literal["Transit", "Radial Velocity", "Microlensing", "Imaging", "Transit Timing Variations",
                 "Eclipse Timing Variations", "Orbital Brightness Modulation", "Pulsar Timing",
                 "Astrometry", "Pulsation Timing Variations", "Disk Kinematics"]
Op = Literal["<", "<=", ">", ">=", "=", "between"]

FIELD_WORDS = {
    "pl_rade": "radius (Earth radii)", "pl_bmasse": "mass (Earth masses)", "pl_eqt": "temperature (K)",
    "pl_orbper": "orbital period (days)", "pl_orbsmax": "orbit size (AU)", "pl_insol": "starlight (Earth = 1)",
    "st_teff": "star temperature (K)", "st_rad": "star radius (solar radii)", "sy_dist": "distance (parsecs)",
    "sy_pnum": "planets in system", "disc_year": "discovery year",
}
PC_PER_LY = 1 / 3.26156


class Filter(BaseModel):
    field: NumField
    op: Op
    value: float
    value2: float | None = None

    @field_validator("value", "value2")
    @classmethod
    def _finite(cls, v):
        if v is not None and (not math.isfinite(v) or abs(v) > 1e9):
            raise ValueError("value must be a finite number")
        return v

    @model_validator(mode="after")
    def _between(self):
        if self.op == "between":
            if self.value2 is None:
                raise ValueError("between needs value2")
            if self.value2 < self.value:
                self.value, self.value2 = self.value2, self.value
        return self

    def describe(self) -> str:
        w = FIELD_WORDS[self.field]
        f = lambda v: f"{v:g}"
        if self.op == "between":
            return f"{w} between {f(self.value)} and {f(self.value2)}"
        return f"{w} {self.op} {f(self.value)}"


class SearchIntent(BaseModel):
    filters: list[Filter] = Field(default_factory=list, max_length=8)
    method: Method | None = None
    sort_field: NumField | None = None
    sort_dir: Literal["asc", "desc"] = "desc"
    limit: int = Field(default=12, ge=1, le=25)
    notes: list[str] = Field(default_factory=list, max_length=8)

    @field_validator("notes")
    @classmethod
    def _short_notes(cls, v):
        return [re.sub(r"[^\w .,()'%<>=-]", "", n)[:120] for n in v]

    @property
    def is_empty(self) -> bool:
        return not self.filters and not self.method and not self.sort_field

    def describe(self) -> list[str]:
        out = [f.describe() for f in self.filters]
        if self.method:
            out.append(f"discovered by {self.method}")
        if self.sort_field:
            out.append(f"sorted by {FIELD_WORDS[self.sort_field]}, {'highest' if self.sort_dir == 'desc' else 'lowest'} first")
        return out


def _num(v: float) -> str:
    """Digits only. Whole numbers stay whole, so a year is `2020` and not `2020.0`."""
    v = float(v)
    if v.is_integer() and abs(v) < 1e15:
        return str(int(v))
    s = repr(v)
    assert re.fullmatch(r"-?\d+(\.\d+)?(e[-+]?\d+)?", s), s
    return s


def build_adql(intent: SearchIntent, columns: list[str]) -> str:
    intent = SearchIntent.model_validate(intent.model_dump())  # re-validate defensively
    for c in columns:
        if not re.fullmatch(r"[a-z_]+", c):
            raise ValueError(f"bad column {c}")
    where = []
    for f in intent.filters:
        if f.op == "between":
            where.append(f"{f.field} between {_num(f.value)} and {_num(f.value2)}")
        else:
            where.append(f"{f.field} {f.op} {_num(f.value)}")
    if intent.method:
        where.append(f"discoverymethod = '{intent.method}'")
    if intent.sort_field:
        where.append(f"{intent.sort_field} is not null")
    # No TOP. On this TAP service, TOP is applied before ORDER BY, so
    # "select top 5 ... order by pl_eqt desc" does not return the hottest rows.
    # Callers slice to intent.limit after the ordered result comes back.
    sql = f"select {','.join(columns)} from pscomppars"
    if where:
        sql += " where " + " and ".join(where)
    sql += f" order by {intent.sort_field or 'disc_year'} {intent.sort_dir if intent.sort_field else 'desc'}"
    return sql


def run_local(intent: SearchIntent, catalog: list[dict]) -> list[dict]:
    """Same semantics as build_adql, executed over the in-memory catalog (offline fallback)."""
    ops = {"<": lambda a, b: a < b, "<=": lambda a, b: a <= b, ">": lambda a, b: a > b,
           ">=": lambda a, b: a >= b, "=": lambda a, b: a == b}

    def ok(r):
        for f in intent.filters:
            v = r.get(f.field)
            if v is None:
                return False
            if f.op == "between":
                if not (f.value <= v <= f.value2):
                    return False
            elif not ops[f.op](v, f.value):
                return False
        if intent.method and r.get("discoverymethod") != intent.method:
            return False
        if intent.sort_field and r.get(intent.sort_field) is None:
            return False
        return True

    rows = [r for r in catalog if ok(r)]
    key = intent.sort_field or "disc_year"
    desc = intent.sort_dir == "desc" if intent.sort_field else True
    rows.sort(key=lambda r: (r.get(key) is None, -(r.get(key) or 0) if desc else (r.get(key) or 0), r["pl_name"]))
    return rows[: intent.limit]


# ---- deterministic rule parser ---------------------------------------------------------------
SUPERLATIVES = [
    (r"\b(hottest|warmest|most scorching)\b", "pl_eqt", "desc"),
    (r"\b(coldest|coolest|chilliest|frostiest)\b", "pl_eqt", "asc"),
    (r"\b(biggest|largest|widest|puffiest)\b", "pl_rade", "desc"),
    (r"\b(smallest|tiniest|littlest)\b", "pl_rade", "asc"),
    (r"\b(most massive|heaviest)\b", "pl_bmasse", "desc"),
    (r"\b(least massive|lightest)\b", "pl_bmasse", "asc"),
    (r"\b(closest|nearest)\b(?! (to|orbit)(s)? (its|the) star)", "sy_dist", "asc"),
    (r"\b(farthest|furthest|most distant|remotest)\b", "sy_dist", "desc"),
    (r"\b(shortest (year|orbit)|fastest orbit|quickest orbit)\b", "pl_orbper", "asc"),
    (r"\b(longest (year|orbit)|slowest orbit)\b", "pl_orbper", "desc"),
    (r"\b(closest|nearest|tightest) (orbit|to (its|the) star)\b", "pl_orbsmax", "asc"),
    (r"\b(newest|latest|most recent(ly)?( discovered)?)\b", "disc_year", "desc"),
    (r"\b(oldest|first|earliest)( known| discovered)?\b", "disc_year", "asc"),
    (r"\b(brightest|most (sun|star)light|most irradiated)\b", "pl_insol", "desc"),
    (r"\b(hottest star|bluest star)\b", "st_teff", "desc"),
]
METHODS = [
    (r"\b(transit(s|ing)?|dimming)\b(?! timing)", "Transit"),
    (r"\b(radial velocity|wobble|doppler)\b", "Radial Velocity"),
    (r"\bmicrolens(ing)?\b", "Microlensing"),
    (r"\b(direct(ly)? imag(ing|ed)|imaging|photographed|pictured)\b", "Imaging"),
    (r"\btransit timing\b", "Transit Timing Variations"),
    (r"\bpulsar\b", "Pulsar Timing"),
    (r"\bastrometry\b", "Astrometry"),
]
NUMBER = r"(\d+(?:\.\d+)?)"


def parse_rules(text: str) -> SearchIntent:
    t = " " + text.lower().strip() + " "
    filters: list[Filter] = []
    notes: list[str] = []
    sort_field, sort_dir = None, "desc"

    def add(field, op, v, v2=None):
        if not any(f.field == field for f in filters):
            filters.append(Filter(field=field, op=op, value=v, value2=v2))

    # habitability-style requests become a temperature/size proxy, and say so
    if re.search(r"liquid water|habitable|goldilocks|could (hold|have|support) (life|water)|might (hold|have|support)|temperate|life", t):
        add("pl_eqt", "between", 180, 310)
        add("pl_rade", "<=", 2.0)
        notes.append("Proxy: equilibrium temperature 180-310 K and radius up to 2 Earth radii. The archive has no water data.")
        sort_field, sort_dir = "sy_dist", "asc"
    if re.search(r"earth[- ]?(sized|size|like|twin)", t):
        add("pl_rade", "between", 0.8, 1.25)
    if re.search(r"super[- ]?earths?", t):
        add("pl_rade", "between", 1.25, 2.0)
    if re.search(r"(mini[- ]?)?neptunes?|neptune[- ]like", t):
        add("pl_rade", "between", 2.0, 6.0)
    if re.search(r"jupiters?\b|gas giants?|giant planets?|\bgiants?\b", t):
        add("pl_rade", ">=", 8.0)
    if re.search(r"red dwarfs?|m[- ]dwarfs?|cool stars?|dim stars?", t):
        add("st_teff", "<", 3900)
    if re.search(r"sun[- ]?like|solar[- ]type|like (the|our) sun", t):
        add("st_teff", "between", 5300, 6000)
    if re.search(r"(hot|blue|bright) stars?", t) and "hottest star" not in t:
        add("st_teff", ">", 7500)

    # years
    m = re.search(r"\b(?:in|during)? ?the (\d{2,4})s\b", t)
    if m:
        y = int(m.group(1))
        y = y + 1900 if y < 100 else y
        add("disc_year", "between", y, y + 9)
    m = re.search(r"between (\d{4}) and (\d{4})", t)
    if m:
        add("disc_year", "between", float(m.group(1)), float(m.group(2)))
    m = re.search(r"\b(?:before|prior to) (\d{4})\b", t)
    if m:
        add("disc_year", "<", float(m.group(1)))
    m = re.search(r"\b(?:after|since) (\d{4})\b", t)
    if m:
        add("disc_year", ">", float(m.group(1)) - (1 if "since" in m.group(0) else 0))
    m = re.search(r"\b(?:in|from|of|during) (\d{4})\b", t) or re.search(r"\b(\d{4}) discover", t)
    if m and 1988 <= int(m.group(1)) <= 2100:
        add("disc_year", "=", float(m.group(1)))

    # distances
    m = re.search(rf"(?:within|closer than|less than|under|nearer than) {NUMBER} ?(light[- ]?years?|ly|parsecs?|pc)\b", t)
    if m:
        v = float(m.group(1)) * (PC_PER_LY if m.group(2).startswith(("l", "ly")) else 1)
        add("sy_dist", "<", round(v, 3))
        if m.group(2).startswith("l"):
            notes.append(f"{m.group(1)} light-years converted to {round(v, 3)} parsecs for the query.")
    m = re.search(rf"(?:farther|further|more) than {NUMBER} ?(light[- ]?years?|ly|parsecs?|pc)\b", t)
    if m:
        v = float(m.group(1)) * (PC_PER_LY if m.group(2).startswith("l") else 1)
        add("sy_dist", ">", round(v, 3))

    # temperatures and periods
    m = re.search(rf"(?:hotter|warmer) than {NUMBER} ?(k|kelvin)\b", t)
    if m:
        add("pl_eqt", ">", float(m.group(1)))
    m = re.search(rf"(?:colder|cooler) than {NUMBER} ?(k|kelvin)\b", t)
    if m:
        add("pl_eqt", "<", float(m.group(1)))
    m = re.search(rf"(?:year|orbit|period)s? (?:of |is |lasting |shorter than |under |less than )+{NUMBER} ?days?", t) \
        or re.search(rf"(?:orbits?|circles?) (?:its star )?in (?:less than|under) {NUMBER} ?days?", t)
    if m:
        add("pl_orbper", "<", float(m.group(1)))
    if re.search(r"less than (a|one) day|under (a|one) day|ultra[- ]short", t):
        add("pl_orbper", "<", 1.0)
    m = re.search(r"\b(\d{1,2}|seven|eight|six|five|four|three|two) (?:known )?planets\b", t)
    words = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8}
    if m:
        add("sy_pnum", "=", float(words.get(m.group(1), m.group(1)) if m.group(1) in words else m.group(1)))
    elif re.search(r"multi[- ]?planet|siblings|crowded system|many planets", t):
        add("sy_pnum", ">=", 4)

    method = None
    for pat, name in sorted(METHODS, key=lambda p: -len(p[0])):
        if re.search(pat, t):
            method = name
            if name == "Transit Timing Variations":
                break
    if re.search(r"\btransit timing\b", t):
        method = "Transit Timing Variations"

    for pat, field, d in SUPERLATIVES:
        if re.search(pat, t):
            sort_field, sort_dir = field, d
            break
    limit = 12
    m = re.search(r"\b(?:top|first) (\d{1,2})\b", t)
    if m:
        limit = max(1, min(25, int(m.group(1))))
    if re.search(r"\b(the|a|one) (\w+ ){0,3}(planet|world|exoplanet)\b", t) and sort_field and "planets" not in t:
        limit = 5
    return SearchIntent(filters=filters, method=method, sort_field=sort_field, sort_dir=sort_dir,
                        limit=limit, notes=notes)


# ---- optional LLM parser (Gemma fills the schema; we validate) ---------------------------------
LLM_INTENT_SCHEMA = {
    "type": "object",
    "properties": {
        "filters": {"type": "array", "items": {"type": "object", "properties": {
            "field": {"type": "string", "enum": list(FIELD_WORDS)},
            "op": {"type": "string", "enum": ["<", "<=", ">", ">=", "=", "between"]},
            "value": {"type": "number"}, "value2": {"type": "number"}},
            "required": ["field", "op", "value"]}},
        "method": {"type": ["string", "null"]},
        "sort_field": {"type": ["string", "null"], "enum": list(FIELD_WORDS) + [None]},
        "sort_dir": {"type": "string", "enum": ["asc", "desc"]},
    },
    "required": ["filters", "sort_field", "sort_dir"],
}
LLM_INTENT_PROMPT = """Map the user's request for exoplanets onto search filters over these archive fields
(units as stored): {fields}.
Discovery methods: {methods}.
Use only these fields. Temperatures are kelvin, distances parsecs (1 light-year = 0.3066 parsecs).
Return JSON with filters (field, op, value[, value2]), method or null, sort_field or null, sort_dir.

Request: {request}"""


def parse_with_llm(text: str, llm) -> SearchIntent:
    """Ask the LLM for a structured intent; raises ValueError if the output does not validate."""
    prompt = LLM_INTENT_PROMPT.format(
        fields=", ".join(f"{k} = {v}" for k, v in FIELD_WORDS.items()),
        methods=", ".join(Method.__args__), request=text[:300])
    res = llm.generate(system="You translate requests into JSON search filters. Output JSON only.",
                       prompt=prompt, schema=LLM_INTENT_SCHEMA, temperature=0.0, context={"purpose": "search"})
    try:
        data = json.loads(res.text)
        if data.get("method") not in Method.__args__:
            data["method"] = None
        data = {k: data.get(k) for k in ("filters", "method", "sort_field", "sort_dir") if k in data}
        data["notes"] = ["Interpreted by Gemma, then validated against the field whitelist."]
        return SearchIntent.model_validate(data)
    except (json.JSONDecodeError, ValidationError, TypeError, AttributeError) as e:
        raise ValueError(f"LLM intent did not validate: {e}") from e
