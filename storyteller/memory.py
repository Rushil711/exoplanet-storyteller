"""Per-session memory of planets already told, and what makes the next one new or different.

Highlights are computed from data (never by the model), e.g. "Hottest world so far this session
(1958 K, beating TRAPPIST-1 e at 249.7 K)". The model only receives a number-free version so its
story can reference earlier planets without quoting numbers the verifier would have to check.
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict

SUPERLATIVES = [
    ("pl_eqt", "hottest", "coldest", "K"),
    ("pl_rade", "biggest", "smallest", "Earth radii"),
    ("sy_dist", "farthest", "nearest", "parsecs"),
    ("pl_orbper", "longest year", "shortest year", "days"),
    ("disc_year", "most recently discovered", "earliest discovered", ""),
]


class SessionMemory:
    def __init__(self, max_sessions: int = 500, max_per_session: int = 50):
        self._s: OrderedDict[str, list[dict]] = OrderedDict()
        self._lock = threading.Lock()
        self.max_sessions = max_sessions
        self.max_per_session = max_per_session

    def told(self, session_id: str | None) -> list[dict]:
        if not session_id:
            return []
        with self._lock:
            return list(self._s.get(session_id, []))

    def remember(self, session_id: str | None, rec: dict, summary: dict) -> None:
        if not session_id:
            return
        with self._lock:
            items = self._s.setdefault(session_id, [])
            items[:] = [i for i in items if i["pl_name"] != rec["pl_name"]]
            items.append({"pl_name": rec["pl_name"], "record": rec, "ts": time.time(), **summary})
            del items[:-self.max_per_session]
            self._s.move_to_end(session_id)
            while len(self._s) > self.max_sessions:
                self._s.popitem(last=False)

    def highlights(self, session_id: str | None, rec: dict) -> list[dict]:
        prev = [i["record"] for i in self.told(session_id) if i["pl_name"] != rec["pl_name"]]
        if not prev:
            return []
        out = []
        for field, hi, lo, unit in SUPERLATIVES:
            v = rec.get(field)
            vals = [(p.get(field), p["pl_name"]) for p in prev if p.get(field) is not None]
            if v is None or not vals:
                continue
            top = max(vals)
            bot = min(vals)
            u = f" {unit}" if unit else ""
            if v > top[0]:
                out.append({"field": field, "kind": hi, "text": f"The {hi} world so far this session ({_f(v)}{u}, beating {top[1]} at {_f(top[0])}{u})"})
            elif v < bot[0]:
                out.append({"field": field, "kind": lo, "text": f"The {lo} world so far this session ({_f(v)}{u}, beating {bot[1]} at {_f(bot[0])}{u})"})
        same_host = [p["pl_name"] for p in prev if p.get("hostname") == rec.get("hostname")]
        if same_host:
            out.append({"field": "hostname", "kind": "sibling",
                        "text": f"Shares its star {rec.get('hostname')} with {', '.join(same_host)}, told earlier"})
        if rec.get("discoverymethod") and all(p.get("discoverymethod") != rec["discoverymethod"] for p in prev):
            out.append({"field": "discoverymethod", "kind": "method",
                        "text": f"First world this session found by {rec['discoverymethod']}"})
        return out

    def llm_context(self, session_id: str | None, rec: dict) -> tuple[str | None, list[str]]:
        """(number-free context sentence for the prompt, earlier planet names for name-masking)."""
        prev = [i for i in self.told(session_id) if i["pl_name"] != rec["pl_name"]]
        if not prev:
            return None, []
        names = [p["pl_name"] for p in prev[-5:]]
        hl = self.highlights(session_id, rec)
        facts = [h["kind"] for h in hl if h["kind"] not in ("sibling", "method")]
        sent = f"The listener already heard stories about: {', '.join(names)}."
        if facts:
            sent += f" Compared with those, this planet is the {', the '.join(facts)} so far."
        sib = [h for h in hl if h["kind"] == "sibling"]
        if sib:
            sent += f" It orbits the same star as {', '.join(n for n in names if n in sib[0]['text'])}."
        sent += " You may mention one contrast in words, without numbers for the other planets."
        return sent, [p["pl_name"] for p in prev] + [p["record"].get("hostname") for p in prev if p["record"].get("hostname")]


def _f(v) -> str:
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    if isinstance(v, float):
        return f"{v:.4g}" if abs(v) < 1000 else f"{v:.0f}"
    return str(v)
