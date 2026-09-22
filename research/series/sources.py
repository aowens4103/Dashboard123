"""Source adapters for automated series, plus a reachability preflight.

UNVERIFIED AGAINST LIVE APIs. These were written in an environment whose
egress proxy blocks every market data host (FRED, Yahoo, Stooq, Alpha Vantage,
Finnhub, Portfolio123, FMP, Nasdaq all refused CONNECT). The request shapes
follow each provider's documented API, but no call has been made end to end.
Run `python3 research/series/ingest.py --preflight` somewhere with egress
before trusting any of it.

The design rule this file exists to serve: a fetch that fails, fails loudly
and writes nothing. It never falls back to a worse source, and it never
returns a partial series that would look complete to the store.
"""

from __future__ import annotations

import datetime as dt
import os
import urllib.error
import urllib.request

from store import Observation

PREFLIGHT_HOSTS = {
    "FRED": "https://api.stlouisfed.org/",
    "stooq": "https://stooq.com/",
}


class SourceError(Exception):
    """A fetch failed. Nothing is written; the caller reports and stops."""


def _require_key(env_var: str, source: str) -> str:
    key = os.getenv(env_var, "").strip()
    if not key:
        raise SourceError(
            f"{source}: {env_var} is not set. Add it to .env; see services/api_keys.py"
        )
    return key


def fetch_fred(series_id: str, *, start: str = "2016-01-01") -> list[Observation]:
    """Fetch a FRED series as observations. Raises SourceError on any failure."""
    key = _require_key("FRED_API_KEY", "FRED")
    url = (
        "https://api.stlouisfed.org/fred/series/observations"
        f"?series_id={series_id}&api_key={key}&file_type=json"
        f"&observation_start={start}"
    )
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            if resp.status != 200:
                raise SourceError(f"FRED {series_id}: HTTP {resp.status}")
            import json
            payload = json.load(resp)
    except urllib.error.URLError as exc:
        raise SourceError(f"FRED {series_id}: {exc.reason}") from exc
    except Exception as exc:
        raise SourceError(f"FRED {series_id}: {exc}") from exc

    rows = payload.get("observations")
    if not rows:
        raise SourceError(f"FRED {series_id}: response contained no observations")

    out: list[Observation] = []
    for row in rows:
        raw = row.get("value", ".")
        if raw in (".", "", None):
            continue  # FRED's explicit no-observation marker
        try:
            out.append(Observation(row["date"], float(raw)))
        except (ValueError, KeyError):
            continue
    if not out:
        raise SourceError(f"FRED {series_id}: no usable observations after parsing")
    return out


def fetch_stooq_close(symbol: str) -> list[Observation]:
    """Daily closes from Stooq's CSV endpoint. No key required.

    Used for ETF closes (XLU, XLP). Stooq suffixes US tickers with '.us'.
    """
    url = f"https://stooq.com/q/d/l/?s={symbol.lower()}.us&i=d"
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            if resp.status != 200:
                raise SourceError(f"stooq {symbol}: HTTP {resp.status}")
            text = resp.read().decode("utf-8", "replace")
    except urllib.error.URLError as exc:
        raise SourceError(f"stooq {symbol}: {exc.reason}") from exc

    lines = [l for l in text.splitlines() if l.strip()]
    if len(lines) < 2 or not lines[0].lower().startswith("date"):
        raise SourceError(f"stooq {symbol}: unexpected response header {lines[:1]}")

    header = [h.strip().lower() for h in lines[0].split(",")]
    try:
        d_idx, c_idx = header.index("date"), header.index("close")
    except ValueError as exc:
        raise SourceError(f"stooq {symbol}: missing date or close column") from exc

    out: list[Observation] = []
    for line in lines[1:]:
        parts = line.split(",")
        if len(parts) <= max(d_idx, c_idx):
            continue
        try:
            out.append(Observation(parts[d_idx], float(parts[c_idx])))
        except ValueError:
            continue
    if not out:
        raise SourceError(f"stooq {symbol}: no usable rows")
    return out


# Maps catalog series name -> (callable, kwargs, source label, method label).
# Only series marked ingest: auto in the catalog appear here.
AUTO_SOURCES: dict[str, tuple] = {
    "us10y":     (fetch_fred, {"series_id": "DGS10"},        "FRED:DGS10",        "fred/series/observations"),
    "wti_front": (fetch_fred, {"series_id": "DCOILWTICO"},   "FRED:DCOILWTICO",   "fred/series/observations"),
    "hy_oas":    (fetch_fred, {"series_id": "BAMLH0A0HYM2"}, "FRED:BAMLH0A0HYM2", "fred/series/observations"),
    "ig_oas":    (fetch_fred, {"series_id": "BAMLC0A0CM"},   "FRED:BAMLC0A0CM",   "fred/series/observations"),
    "xlu":       (fetch_stooq_close, {"symbol": "XLU"},      "stooq:XLU",         "stooq/q/d/l csv"),
    "xlp":       (fetch_stooq_close, {"symbol": "XLP"},      "stooq:XLP",         "stooq/q/d/l csv"),
}


def preflight() -> dict[str, str]:
    """Report whether each source host is reachable. Diagnostic only."""
    results: dict[str, str] = {}
    for label, url in PREFLIGHT_HOSTS.items():
        try:
            req = urllib.request.Request(url, method="HEAD")
            with urllib.request.urlopen(req, timeout=15) as resp:
                results[label] = f"reachable (HTTP {resp.status})"
        except urllib.error.HTTPError as exc:
            # An HTTP error still proves the host answered.
            results[label] = f"reachable (HTTP {exc.code})"
        except Exception as exc:
            results[label] = f"UNREACHABLE ({type(exc).__name__}: {exc})"
    results["FRED_API_KEY"] = "set" if os.getenv("FRED_API_KEY", "").strip() else "NOT SET"
    return results
