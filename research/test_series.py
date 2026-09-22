"""Tests for the series store and coverage report.

The behaviour under test is the one the whole monitor rests on: a stale series
must be distinguishable from a fresh one and from a missing one, so a
falsifier over stale data reports BLIND rather than "not tripped".

Run: python3 research/test_series.py
"""

from __future__ import annotations

import datetime as dt
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).parent))
sys.path.insert(0, str(pathlib.Path(__file__).parent / "series"))

import store
from store import Freshness, Observation

TODAY = dt.date(2026, 9, 22)


def _isolate(tmp: pathlib.Path):
    store.DATA_DIR = tmp
    store.MANIFEST_PATH = tmp / "_manifest.json"


def _obs(days_ago: int, value: float = 1.0) -> Observation:
    return Observation((TODAY - dt.timedelta(days=days_ago)).isoformat(), value)


def test_missing_series_is_missing():
    with tempfile.TemporaryDirectory() as td:
        _isolate(pathlib.Path(td))
        st = store.status("nope", {"max_age_days": 4}, asof=TODAY)
        assert st.freshness is Freshness.MISSING, st
        assert not st.usable


def test_recent_series_is_fresh():
    with tempfile.TemporaryDirectory() as td:
        _isolate(pathlib.Path(td))
        store.write_series("us10y", [_obs(5), _obs(1, 5.02)],
                           source="FRED:DGS10", method="test")
        st = store.status("us10y", {"max_age_days": 4}, asof=TODAY)
        assert st.freshness is Freshness.FRESH, st
        assert st.age_days == 1 and st.rows == 2


def test_old_series_is_stale_not_fresh():
    with tempfile.TemporaryDirectory() as td:
        _isolate(pathlib.Path(td))
        store.write_series("qsr", [_obs(400)], source="manual", method="test")
        st = store.status("qsr", {"max_age_days": 100}, asof=TODAY)
        assert st.freshness is Freshness.STALE, st
        assert not st.usable
        assert "400d old" in st.detail


def test_freshly_fetched_but_old_observation_is_still_stale():
    # The failure this guards: fetched this morning, newest point six months
    # old. Measuring age from retrieval would call that fresh.
    with tempfile.TemporaryDirectory() as td:
        _isolate(pathlib.Path(td))
        store.write_series("capex", [_obs(180)], source="manual", method="test",
                           retrieved_at=dt.datetime.now(dt.timezone.utc).isoformat())
        st = store.status("capex", {"max_age_days": 100}, asof=TODAY)
        assert st.freshness is Freshness.STALE, st


def test_missing_max_age_is_not_treated_as_fresh():
    with tempfile.TemporaryDirectory() as td:
        _isolate(pathlib.Path(td))
        store.write_series("x", [_obs(0)], source="manual", method="test")
        st = store.status("x", {}, asof=TODAY)
        assert st.freshness is Freshness.STALE, st
        assert "no max_age_days" in st.detail


def test_provenance_is_mandatory():
    with tempfile.TemporaryDirectory() as td:
        _isolate(pathlib.Path(td))
        for bad in ({"source": "", "method": "m"}, {"source": "s", "method": ""}):
            try:
                store.write_series("x", [_obs(0)], **bad)
            except ValueError:
                continue
            raise AssertionError(f"write_series accepted {bad} without provenance")


def test_manifest_records_provenance():
    with tempfile.TemporaryDirectory() as td:
        _isolate(pathlib.Path(td))
        store.write_series("us10y", [_obs(2), _obs(0)],
                           source="FRED:DGS10", method="fred/observations")
        entry = store.read_manifest()["us10y"]
        assert entry["source"] == "FRED:DGS10"
        assert entry["method"] == "fred/observations"
        assert entry["rows"] == 2
        assert entry["last_date"] == TODAY.isoformat()
        assert entry["retrieved_at"]


def test_blank_values_are_gaps_not_zeros():
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        _isolate(tmp)
        (tmp / "g.csv").write_text("date,value\n2026-09-20,\n2026-09-21,5.0\n")
        rows = store.read_series("g")
        assert len(rows) == 1 and rows[0].value == 5.0, rows


def test_observations_are_sorted_on_read():
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        _isolate(tmp)
        (tmp / "s.csv").write_text("date,value\n2026-09-21,2\n2026-09-19,1\n")
        rows = store.read_series("s")
        assert [r.date for r in rows] == ["2026-09-19", "2026-09-21"]


def test_coverage_marks_stale_falsifier_blind():
    import coverage
    with tempfile.TemporaryDirectory() as td:
        _isolate(pathlib.Path(td))
        store.write_series("us10y", [_obs(1)], source="FRED", method="test")
        store.write_series("xlu", [_obs(300)], source="stooq", method="test")

        catalog = {"us10y": {"max_age_days": 4}, "xlu": {"max_age_days": 4}}
        statuses = store.status_all(catalog, asof=TODAY)
        thesis = {"falsifiers": [
            {"id": "f1", "requires": ["us10y"]},
            {"id": "f2", "requires": ["us10y", "xlu"]},
        ]}
        cov = coverage.falsifier_coverage(thesis, statuses)
        assert cov[0]["monitored"] is True, cov[0]
        # f2 is blind on xlu alone; a fresh us10y must not rescue it.
        assert cov[1]["monitored"] is False, cov[1]
        assert any("xlu" in b for b in cov[1]["blockers"]), cov[1]


def main() -> int:
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = []
    for name, fn in tests:
        try:
            fn()
            print(f"  pass  {name}")
        except AssertionError as exc:
            failed.append(name)
            print(f"  FAIL  {name}: {exc}")
    print(f"\n{len(tests) - len(failed)}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
