"""Coverage report: which theses can actually be monitored right now.

This is the answer to the flag raised at the end of stage 1. Nine of the
nineteen catalogued series are manual, so a naive monitor would evaluate a
handful of falsifiers, find no trips, and report an all-clear that was mostly
an artifact of dead inputs.

A falsifier is MONITORED only if every series it requires is present and
within its max_age_days. Otherwise it is BLIND, and a blind falsifier is
reported as such rather than counted as "not tripped". The distinction between
false and unknown is the whole point.

Run: python3 research/coverage.py
"""

from __future__ import annotations

import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
sys.path.insert(0, str(pathlib.Path(__file__).parent / "series"))

import register as reg
from store import Freshness, status_all


def falsifier_coverage(thesis: dict, statuses: dict) -> list[dict]:
    """Per falsifier: monitored, or blind with the reason."""
    out = []
    for fals in thesis.get("falsifiers", []):
        blockers = []
        for name in fals.get("requires", []):
            st = statuses.get(name)
            if st is None:
                blockers.append(f"{name}: not in catalog")
            elif st.freshness is Freshness.MISSING:
                blockers.append(f"{name}: never ingested")
            elif st.freshness is Freshness.STALE:
                blockers.append(f"{name}: {st.detail}")
        out.append({
            "id": fals.get("id"),
            "monitored": not blockers,
            "blockers": blockers,
            "requires": fals.get("requires", []),
        })
    return out


def build(asof: dt.date | None = None) -> dict:
    register = reg.load_register()
    statuses = status_all(register.catalog, asof=asof)

    theses = {}
    for tid, doc in sorted(register.theses.items()):
        if doc["status"] not in reg.ACTIVE_STATUSES:
            continue
        coverage = falsifier_coverage(doc, statuses)
        monitored = sum(1 for c in coverage if c["monitored"])
        theses[tid] = {
            "status": doc["status"],
            "confidence": doc["confidence"],
            "falsifiers": coverage,
            "monitored": monitored,
            "total": len(coverage),
            # A thesis with no monitored falsifier cannot be falsified by the
            # system at all, whatever its register entry claims.
            "blind": monitored == 0,
        }
    return {"theses": theses, "series": statuses}


def main() -> int:
    report = build()
    statuses, theses = report["series"], report["theses"]

    catalog = reg.load_catalog()
    by_state = {f: [n for n, s in statuses.items() if s.freshness is f]
                for f in Freshness}
    print("SERIES")
    print(f"  fresh   {len(by_state[Freshness.FRESH]):3}")
    print(f"  stale   {len(by_state[Freshness.STALE]):3}")
    print(f"  missing {len(by_state[Freshness.MISSING]):3}")

    # Manual series are the standing liability: nothing refreshes them but a
    # person, so they are listed by name rather than folded into a count.
    manual = sorted(n for n, e in catalog.items() if e.get("ingest") == "manual")
    needing = [n for n in manual if not statuses[n].usable]
    if manual:
        print(f"\nMANUAL SERIES ({len(needing)} of {len(manual)} need a hand update)")
        for name in manual:
            st = statuses[name]
            mark = "ok   " if st.usable else "DUE  "
            print(f"  [{mark}] {name:32} {st.detail}")

    print("\nTHESES")
    fully = partly = blind = 0
    for tid, info in theses.items():
        if info["blind"]:
            mark, blind = "BLIND ", blind + 1
        elif info["monitored"] < info["total"]:
            mark, partly = "partial", partly + 1
        else:
            mark, fully = "ok    ", fully + 1
        print(f"  [{mark}] {tid}  ({info['monitored']}/{info['total']} falsifiers monitored)")
        for cov in info["falsifiers"]:
            if not cov["monitored"]:
                print(f"            {cov['id']} blind: {'; '.join(cov['blockers'])}")

    total = len(theses)
    print(f"\n{fully} fully monitored, {partly} partial, {blind} blind, of {total} active")
    if blind:
        print("\nA BLIND thesis cannot be falsified by this system. Either ingest "
              "its series, or move it to on_watch so the register stops implying "
              "it is being tracked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
