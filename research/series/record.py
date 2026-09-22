"""Record a manual observation, with provenance, into the series store.

Nine of the nineteen catalogued series have no free automated feed: forward
EPS consensus, hyperscaler financials, index concentration, QSR traffic. They
are updated by hand on a quarterly-ish rhythm, so they need a path in that
does not quietly weaken the provenance rule the automated feeds follow.

    python3 research/series/record.py top10_weight 0.372 2026-09-30 \
        --source "S&P Dow Jones factsheet" \
        --url https://example.com/factsheet.pdf

Both a source and a URL are required. A figure taken from a search summary
rather than a fetched page does not go in.
"""

from __future__ import annotations

import argparse
import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))

import yaml

from store import Observation, read_series, write_series

CATALOG_PATH = pathlib.Path(__file__).parent / "catalog.yaml"


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("series")
    ap.add_argument("value", type=float)
    ap.add_argument("date", help="observation date, YYYY-MM-DD (not today's date)")
    ap.add_argument("--source", required=True, help="who published it")
    ap.add_argument("--url", required=True, help="the page actually fetched")
    args = ap.parse_args(argv)

    with open(CATALOG_PATH) as fh:
        catalog = yaml.safe_load(fh) or {}

    if args.series not in catalog:
        print(f"{args.series!r} is not in catalog.yaml. Add it there first, "
              f"with a max_age_days.")
        return 2

    entry = catalog[args.series]
    if entry.get("ingest") == "auto":
        print(f"{args.series!r} is ingest: auto. Use ingest.py so the value "
              f"carries its real source, or change the catalog.")
        return 2

    try:
        obs_date = dt.date.fromisoformat(args.date)
    except ValueError:
        print(f"bad date {args.date!r}; use YYYY-MM-DD")
        return 2
    if obs_date > dt.date.today():
        print(f"observation date {args.date} is in the future")
        return 2

    existing = [o for o in read_series(args.series) if o.date != args.date]
    merged = existing + [Observation(args.date, args.value)]

    written = write_series(
        args.series, merged,
        source=f"manual:{args.source}",
        method=f"hand-entered from {args.url}",
    )
    print(f"{args.series}: {args.value} at {args.date} "
          f"({written['rows']} rows, latest {written['last_date']})")
    print("Commit the CSV and manifest so the value is in the audit trail.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
