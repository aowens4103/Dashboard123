"""Series store: values on disk, with provenance and an honest freshness state.

One CSV per series (date,value) plus a manifest recording where each series
came from and when it was fetched. CSV because git diffs it legibly, and the
whole point of keeping the register in git is that changes are reviewable.

The freshness state exists so a predicate can return UNKNOWN rather than
FALSE. A monitor that reports "no trips" while half its inputs are dead is
worse than no monitor, so staleness is a first-class state here, never a
silent default.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import pathlib
from dataclasses import dataclass
from enum import Enum

DATA_DIR = pathlib.Path(__file__).parent / "data"
MANIFEST_PATH = DATA_DIR / "_manifest.json"


class Freshness(str, Enum):
    FRESH = "fresh"      # present and within max_age_days
    STALE = "stale"      # present but the latest observation is too old
    MISSING = "missing"  # never ingested


@dataclass(frozen=True)
class SeriesStatus:
    name: str
    freshness: Freshness
    last_date: str | None
    age_days: int | None
    max_age_days: int | None
    rows: int
    detail: str

    @property
    def usable(self) -> bool:
        return self.freshness is Freshness.FRESH


@dataclass(frozen=True)
class Observation:
    date: str
    value: float


def _path_for(name: str) -> pathlib.Path:
    return DATA_DIR / f"{name}.csv"


def read_manifest() -> dict:
    if not MANIFEST_PATH.exists():
        return {}
    with open(MANIFEST_PATH) as fh:
        return json.load(fh)


def write_manifest(manifest: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    # Sorted and newline-terminated so successive ingests produce clean diffs.
    with open(MANIFEST_PATH, "w") as fh:
        json.dump(manifest, fh, indent=2, sort_keys=True)
        fh.write("\n")


def read_series(name: str) -> list[Observation]:
    path = _path_for(name)
    if not path.exists():
        return []
    out: list[Observation] = []
    with open(path, newline="") as fh:
        for row in csv.DictReader(fh):
            raw = (row.get("value") or "").strip()
            if not raw:
                continue  # a gap in the source, not a zero
            try:
                out.append(Observation(row["date"], float(raw)))
            except (ValueError, KeyError):
                continue
    out.sort(key=lambda o: o.date)
    return out


def write_series(name: str, observations: list[Observation], *,
                 source: str, method: str, retrieved_at: str | None = None) -> dict:
    """Write a series and record its provenance. Returns the manifest entry.

    Provenance is not optional: source and method are required arguments
    precisely so a series cannot be written without saying where it came from.
    """
    if not source or not method:
        raise ValueError(f"{name}: source and method are required for provenance")

    observations = sorted(observations, key=lambda o: o.date)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(_path_for(name), "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["date", "value"])
        for obs in observations:
            writer.writerow([obs.date, obs.value])

    entry = {
        "source": source,
        "method": method,
        "retrieved_at": retrieved_at or dt.datetime.now(dt.timezone.utc)
                                          .replace(microsecond=0).isoformat(),
        "rows": len(observations),
        "first_date": observations[0].date if observations else None,
        "last_date": observations[-1].date if observations else None,
    }
    manifest = read_manifest()
    manifest[name] = entry
    write_manifest(manifest)
    return entry


def status(name: str, catalog_entry: dict, *, asof: dt.date | None = None,
           manifest: dict | None = None) -> SeriesStatus:
    """Freshness of one series, measured on the latest observation date.

    Age is measured from the observation, not from when we last asked. A
    series fetched this morning whose newest point is six months old is stale;
    pretending otherwise is how a dead input passes for a live one.
    """
    asof = asof or dt.date.today()
    manifest = read_manifest() if manifest is None else manifest
    max_age = catalog_entry.get("max_age_days")
    observations = read_series(name)

    if not observations:
        return SeriesStatus(name, Freshness.MISSING, None, None, max_age, 0,
                            "never ingested")

    last_date = observations[-1].date
    try:
        age = (asof - dt.date.fromisoformat(last_date)).days
    except ValueError:
        return SeriesStatus(name, Freshness.MISSING, last_date, None, max_age,
                            len(observations), f"unparseable last date {last_date!r}")

    if max_age is None:
        return SeriesStatus(name, Freshness.STALE, last_date, age, None,
                            len(observations),
                            "no max_age_days declared in catalog")

    if age > max_age:
        return SeriesStatus(name, Freshness.STALE, last_date, age, max_age,
                            len(observations),
                            f"{age}d old, limit {max_age}d")

    return SeriesStatus(name, Freshness.FRESH, last_date, age, max_age,
                        len(observations), f"{age}d old, limit {max_age}d")


def status_all(catalog: dict, *, asof: dt.date | None = None) -> dict[str, SeriesStatus]:
    manifest = read_manifest()
    return {name: status(name, entry, asof=asof, manifest=manifest)
            for name, entry in catalog.items()}
