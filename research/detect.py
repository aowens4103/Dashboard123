"""Trip detection: evaluate every active thesis's falsifiers and report.

Run:
    python3 research/detect.py            # report only, changes nothing
    python3 research/detect.py --record   # also stamp trips and log evidence

Detection is code; interpretation is the agent. This module decides only
whether a predicate is true, false or unanswerable. It never changes a
thesis's status, confidence or falsifiers — a trip is a summons for the
interpret run, not a verdict on the thesis.

--record stamps `tripped: <date>` on the falsifier and appends an evidence
entry carrying the input values that caused it, so the trip is reconstructable
later from the repo alone. An already-stamped falsifier is not re-reported.
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
sys.path.insert(0, str(pathlib.Path(__file__).parent / "series"))

import register as reg  # noqa: E402
from evaluate import Verdict, evaluate  # noqa: E402

EVIDENCE_DIR = pathlib.Path(__file__).parent / "evidence"


def run(asof: dt.date | None = None) -> list[dict]:
    """Evaluate every falsifier on every active thesis."""
    asof = asof or dt.date.today()
    register = reg.load_register()
    results = []

    for tid, doc in sorted(register.theses.items()):
        if doc["status"] not in reg.ACTIVE_STATUSES:
            continue
        for fals in doc.get("falsifiers", []):
            already = fals.get("tripped")
            ev = evaluate(fals.get("predicate", ""), fals.get("requires", []),
                          register.catalog, asof=asof)
            results.append({
                "thesis": tid,
                "falsifier": fals.get("id"),
                "predicate": fals.get("predicate"),
                "verdict": ev.verdict,
                "reason": ev.reason,
                "inputs": ev.inputs,
                "already_tripped": already,
                "path": doc["_path"],
            })
    return results


def stamp_tripped(text: str, falsifier_id: str, date: str) -> str:
    """Add or update `tripped:` on one falsifier, changing nothing else.

    Deliberately a text edit rather than a YAML round-trip. Parsing and
    re-dumping rewrites block scalars as quoted strings and normalises numbers
    (0.70 becomes 0.7), turning a one-field stamp into a hundred-line diff.
    The register's audit value comes from diffs being readable, so the stamp
    has to leave every other byte alone.
    """
    lines = text.splitlines(keepends=True)
    start = None
    indent = ""
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.rstrip() == f"- id: {falsifier_id}":
            start = i
            indent = line[: len(line) - len(stripped)]
            break
    if start is None:
        raise KeyError(f"falsifier {falsifier_id!r} not found")

    key_indent = indent + "  "
    # The item ends at the next sibling "- " or any shallower key.
    end = len(lines)
    for j in range(start + 1, len(lines)):
        line = lines[j]
        if not line.strip():
            continue
        lead = len(line) - len(line.lstrip())
        if lead < len(indent) or (lead == len(indent) and line.lstrip().startswith("- ")):
            end = j
            break

    for j in range(start + 1, end):
        if lines[j].startswith(f"{key_indent}tripped:"):
            lines[j] = f"{key_indent}tripped: {date}\n"
            return "".join(lines)

    lines.insert(start + 1, f"{key_indent}tripped: {date}\n")
    return "".join(lines)


def record(results: list[dict], asof: dt.date) -> int:
    """Stamp new trips into their thesis files and append evidence."""
    new_trips = [r for r in results
                 if r["verdict"] is Verdict.TRIPPED and not r["already_tripped"]]
    if not new_trips:
        return 0

    root = pathlib.Path(__file__).parent.parent
    for trip in new_trips:
        path = root / trip["path"]
        path.write_text(stamp_tripped(path.read_text(), trip["falsifier"],
                                      asof.isoformat()))

    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    log = EVIDENCE_DIR / f"{asof.strftime('%Y-%m')}.jsonl"
    with open(log, "a") as fh:
        for trip in new_trips:
            fh.write(json.dumps({
                "ts": asof.isoformat(),
                "thesis": trip["thesis"],
                "direction": "against",
                "observation": (
                    f"Falsifier {trip['falsifier']} tripped: {trip['predicate']}"
                ),
                "inputs": trip["inputs"],
                "source": {"url": "internal:detect.py",
                           "retrieved_at": asof.isoformat()},
                "run": "detect",
            }, sort_keys=True) + "\n")
    return len(new_trips)


def main(argv: list[str]) -> int:
    asof = dt.date.today()
    results = run(asof)

    if not results:
        print("no active theses with falsifiers")
        return 0

    counts = {v: sum(1 for r in results if r["verdict"] is v) for v in Verdict}
    by_thesis: dict[str, list[dict]] = {}
    for r in results:
        by_thesis.setdefault(r["thesis"], []).append(r)

    for tid, rows in by_thesis.items():
        print(tid)
        for r in rows:
            mark = {Verdict.TRIPPED: "TRIPPED",
                    Verdict.NOT_TRIPPED: "ok     ",
                    Verdict.UNKNOWN: "unknown"}[r["verdict"]]
            stamped = " (already recorded)" if r["already_tripped"] else ""
            print(f"  [{mark}] {r['falsifier']}: {r['reason']}{stamped}")

    print(f"\n{counts[Verdict.TRIPPED]} tripped, "
          f"{counts[Verdict.NOT_TRIPPED]} not tripped, "
          f"{counts[Verdict.UNKNOWN]} unknown")

    if counts[Verdict.UNKNOWN]:
        print("UNKNOWN is not an all-clear. Those falsifiers were not evaluated; "
              "run coverage.py to see which inputs are missing or stale.")

    if "--record" in argv:
        n = record(results, asof)
        print(f"\nrecorded {n} new trip(s)" if n else "\nno new trips to record")
    elif counts[Verdict.TRIPPED]:
        print("Run with --record to stamp these trips and log the evidence.")

    # Exit 1 on a new trip so a scheduled run can notify on it.
    return 1 if any(r["verdict"] is Verdict.TRIPPED and not r["already_tripped"]
                    for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
