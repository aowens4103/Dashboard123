"""Ingest automated series into the store.

Run:
    python3 research/series/ingest.py --preflight   # check reachability first
    python3 research/series/ingest.py               # ingest every auto series
    python3 research/series/ingest.py us10y xlu     # ingest named series

A failed fetch writes nothing and returns a non-zero exit. Partial ingest is
reported per series so one dead source does not silently mask the others.
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))

import yaml

from sources import AUTO_SOURCES, SourceError, preflight
from store import write_series

CATALOG_PATH = pathlib.Path(__file__).parent / "catalog.yaml"


def load_catalog() -> dict:
    with open(CATALOG_PATH) as fh:
        return yaml.safe_load(fh) or {}


def ingest_one(name: str) -> tuple[bool, str]:
    if name not in AUTO_SOURCES:
        return False, "no adapter; this series is manual or derived"
    fetch, kwargs, source, method = AUTO_SOURCES[name]
    try:
        observations = fetch(**kwargs)
    except SourceError as exc:
        return False, str(exc)
    except Exception as exc:  # an adapter bug must not look like a data gap
        return False, f"adapter error: {type(exc).__name__}: {exc}"
    entry = write_series(name, observations, source=source, method=method)
    return True, f"{entry['rows']} rows, latest {entry['last_date']}"


def main(argv: list[str]) -> int:
    if "--preflight" in argv:
        print("Preflight:")
        results = preflight()
        for label, state in sorted(results.items()):
            print(f"  {label:16} {state}")
        unreachable = [k for k, v in results.items() if "UNREACHABLE" in v or v == "NOT SET"]
        if unreachable:
            print(f"\nNot ready: {', '.join(sorted(unreachable))}")
            return 1
        print("\nAll sources reachable.")
        return 0

    catalog = load_catalog()
    requested = [a for a in argv if not a.startswith("-")]
    auto = [n for n, e in catalog.items() if e.get("ingest") == "auto"]
    targets = requested or auto

    unknown = [t for t in targets if t not in catalog]
    if unknown:
        print(f"not in catalog: {', '.join(unknown)}")
        return 2

    failures = 0
    for name in targets:
        ok, detail = ingest_one(name)
        print(f"  {'ok  ' if ok else 'FAIL'}  {name:28} {detail}")
        if not ok:
            failures += 1

    print(f"\n{len(targets) - failures}/{len(targets)} ingested")
    if failures:
        print("Nothing was written for the failures above. "
              "Fix the source or the key; do not substitute another feed.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
