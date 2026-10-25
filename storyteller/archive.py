"""NASA Exoplanet Archive access: live TAP queries with a bundled-snapshot fallback.

The catalog (all ~6,451 rows of `pscomppars`, 16 columns) is loaded from the bundled snapshot
at startup and refreshed from TAP in the background, so autocomplete never waits on the network.
A planet's story always uses a fresh live record when TAP is reachable.
"""
from __future__ import annotations

import datetime as dt
import gzip
import hashlib
import json
import re
import threading
import time
import unicodedata
from pathlib import Path

import httpx

from .logs import get_logger

log = get_logger("archive")
NAME_RE = re.compile(r"^[A-Za-z0-9 .+\-'()/_]{1,64}$")


class ArchiveError(Exception):
    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.user_message = message
        self.status = status


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).lower()
    return re.sub(r"[^a-z0-9]", "", s)


class ArchiveClient:
    def __init__(self, fields: list[str], snapshot_path: Path, tap_url: str, timeout: float = 30.0,
                 offline: bool = False, cache_dir: Path | None = None, transport: httpx.BaseTransport | None = None):
        self.fields = fields
        self.tap_url = tap_url
        self.timeout = timeout
        self.offline = offline
        self.cache_dir = cache_dir
        self._http = httpx.Client(timeout=timeout, transport=transport, headers={"User-Agent": "exoplanet-storyteller/1.0"})
        self._lock = threading.Lock()
        self.catalog_source = "bundled snapshot"
        self.catalog_updated: str | None = None
        self._load_catalog(self._read_snapshot(snapshot_path))
        self.last_tap_ok: bool | None = None

    # ---- catalog ---------------------------------------------------------------------------
    @staticmethod
    def _read_snapshot(path: Path) -> list[dict]:
        opener = gzip.open if str(path).endswith(".gz") else open
        with opener(path, "rt", encoding="utf-8") as fh:
            return json.load(fh)

    def _load_catalog(self, rows: list[dict]) -> None:
        rows = [r for r in rows if r.get("pl_name")]
        rows.sort(key=lambda r: r["pl_name"])
        with self._lock:
            self.catalog = rows
            self.by_name = {r["pl_name"]: r for r in rows}
            self._norm_index = {_norm(r["pl_name"]): r["pl_name"] for r in rows}

    def refresh_catalog(self) -> bool:
        """Replace the snapshot with a live full-table download (~2.5 MB, a few seconds)."""
        if self.offline:
            return False
        cached = self.cache_dir / "catalog.json" if self.cache_dir else None
        if cached and cached.exists() and time.time() - cached.stat().st_mtime < 86400:
            rows = json.loads(cached.read_text())
            self._load_catalog(rows)
            self.catalog_source = "NASA TAP (cached today)"
            self.catalog_updated = dt.datetime.fromtimestamp(cached.stat().st_mtime).isoformat(timespec="minutes")
            return True
        try:
            rows = self.tap(f"select {','.join(self.fields)} from pscomppars order by pl_name")
        except ArchiveError:
            return False
        if len(rows) < 1000:
            return False
        self._load_catalog(rows)
        self.catalog_source = "NASA TAP (live)"
        self.catalog_updated = dt.datetime.now().isoformat(timespec="minutes")
        if cached:
            cached.parent.mkdir(parents=True, exist_ok=True)
            cached.write_text(json.dumps(rows))
        log.info("catalog_refreshed", rows=len(rows))
        return True

    def resolve_name(self, name: str) -> str | None:
        name = (name or "").strip()
        if name in self.by_name:
            return name
        return self._norm_index.get(_norm(name))

    def suggest(self, q: str, limit: int = 8) -> list[dict]:
        nq = _norm(q)
        if not nq:
            return []
        starts, contains = [], []
        for r in self.catalog:
            n = _norm(r["pl_name"])
            h = _norm(r.get("hostname") or "")
            if n.startswith(nq) or h.startswith(nq):
                starts.append(r)
            elif nq in n:
                contains.append(r)
            if len(starts) >= limit:
                break
        out = (starts + contains)[:limit]
        return [{"pl_name": r["pl_name"], "hostname": r.get("hostname"), "disc_year": r.get("disc_year"),
                 "discoverymethod": r.get("discoverymethod"), "sy_dist": r.get("sy_dist")} for r in out]

    def planet_of_the_day(self, day: dt.date | None = None) -> dict:
        """Deterministic daily pick among planets with the core story fields filled in."""
        day = day or dt.date.today()
        core = ("pl_rade", "pl_eqt", "pl_orbper", "st_teff", "sy_dist")
        pool = [r for r in self.catalog if all(r.get(k) is not None for k in core)]
        idx = int(hashlib.sha256(day.isoformat().encode()).hexdigest(), 16) % len(pool)
        return pool[idx]

    # ---- TAP ------------------------------------------------------------------------------------
    def tap(self, adql: str) -> list[dict]:
        if self.offline:
            raise ArchiveError("Archive is in offline mode (ARCHIVE_OFFLINE=1)", 503)
        t0 = time.perf_counter()
        try:
            r = self._http.get(self.tap_url, params={"query": adql, "format": "json"})
            r.raise_for_status()
            rows = r.json()
        except httpx.TimeoutException:
            self.last_tap_ok = False
            log.warning("tap_timeout", adql=adql[:200])
            raise ArchiveError("The NASA Exoplanet Archive took too long to answer.", 504)
        except (httpx.HTTPError, ValueError) as e:
            self.last_tap_ok = False
            log.warning("tap_error", adql=adql[:200], error=str(e)[:200])
            raise ArchiveError("The NASA Exoplanet Archive could not be reached.", 502)
        self.last_tap_ok = True
        log.info("tap_query", adql=adql[:300], rows=len(rows), ms=round((time.perf_counter() - t0) * 1000))
        if not isinstance(rows, list):
            raise ArchiveError("Unexpected response from the NASA Exoplanet Archive.", 502)
        return rows

    def fetch_planet(self, name: str) -> tuple[dict, str]:
        """Returns (record, source) where source is 'NASA TAP (live)' or 'bundled snapshot'."""
        resolved = self.resolve_name(name)
        if not resolved:
            if not NAME_RE.match(name or ""):
                raise ArchiveError(f"'{name}' is not a valid planet name.", 400)
            resolved = name.strip()
        if not self.offline:
            literal = resolved.replace("'", "''")
            try:
                rows = self.tap(f"select {','.join(self.fields)} from pscomppars where pl_name = '{literal}'")
                if rows:
                    rec = {k: rows[0].get(k) for k in self.fields}
                    return rec, "NASA TAP (live)"
            except ArchiveError:
                pass
        if resolved in self.by_name:
            return dict(self.by_name[resolved]), "bundled snapshot"
        raise ArchiveError(f"No confirmed planet named '{name}' in the NASA Exoplanet Archive.", 404)

    def status(self) -> dict:
        return {"catalog_rows": len(self.catalog), "catalog_source": self.catalog_source,
                "catalog_updated": self.catalog_updated, "offline": self.offline, "last_tap_ok": self.last_tap_ok}
