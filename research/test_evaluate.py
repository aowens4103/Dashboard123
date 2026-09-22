"""Tests for predicate evaluation and trip detection.

The property under test throughout: a predicate we cannot answer returns
UNKNOWN, never FALSE. Every way of failing to know something — missing series,
stale series, too little history, a divide by zero, a bug in a helper — has to
land on UNKNOWN, because FALSE reads as "not tripped" and would quietly
mark a thesis safe on the strength of data nobody has.

Run: python3 research/test_evaluate.py
"""

from __future__ import annotations

import datetime as dt
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).parent))
sys.path.insert(0, str(pathlib.Path(__file__).parent / "series"))

import store
from evaluate import Verdict, evaluate
from store import Observation

TODAY = dt.date(2026, 9, 22)
CAT = {
    "us10y": {"max_age_days": 4},
    "xlu": {"max_age_days": 4},
    "capex": {"max_age_days": 100},
}


def daily(values: list[float], *, end: dt.date = TODAY) -> list[Observation]:
    """Consecutive daily observations ending at `end`."""
    n = len(values)
    return [Observation((end - dt.timedelta(days=n - 1 - i)).isoformat(), v)
            for i, v in enumerate(values)]


def ev(predicate, requires, data, asof=TODAY):
    return evaluate(predicate, requires, CAT, asof=asof, series_data=data)


# --- the core property -----------------------------------------------------

def test_true_predicate_trips():
    r = ev("latest('us10y') > 5.0", ["us10y"], {"us10y": daily([5.1])})
    assert r.verdict is Verdict.TRIPPED, r


def test_false_predicate_does_not_trip():
    r = ev("latest('us10y') > 5.0", ["us10y"], {"us10y": daily([4.9])})
    assert r.verdict is Verdict.NOT_TRIPPED, r


def test_missing_series_is_unknown_not_false():
    r = ev("latest('us10y') > 5.0", ["us10y"], {"us10y": []})
    assert r.verdict is Verdict.UNKNOWN, r
    assert "no observations" in r.reason


def test_insufficient_history_is_unknown_not_false():
    # Eight points cannot answer a twenty-observation question. Returning
    # False here would read as "rates have not stayed down", which is a claim
    # the data does not support either way.
    r = ev("consecutive_below('us10y', 4.5, 20)", ["us10y"],
           {"us10y": daily([4.0] * 8)})
    assert r.verdict is Verdict.UNKNOWN, r
    assert "need 20 consecutive" in r.reason


def test_change_without_enough_history_is_unknown():
    r = ev("change('capex', 90) < -0.10", ["capex"], {"capex": daily([100.0, 95.0])})
    assert r.verdict is Verdict.UNKNOWN, r
    assert "history starts" in r.reason


def test_drawdown_with_one_point_is_unknown():
    r = ev("drawdown('xlu', 60) > 0.10", ["xlu"], {"xlu": daily([70.0])})
    assert r.verdict is Verdict.UNKNOWN, r


def test_divide_by_zero_is_unknown():
    r = ev("change('capex', 2) < 0", ["capex"], {"capex": daily([0.0, 0.0, 5.0])})
    assert r.verdict is Verdict.UNKNOWN, r
    assert "zero" in r.reason


def test_non_boolean_result_is_unknown():
    r = ev("latest('us10y')", ["us10y"], {"us10y": daily([5.0])})
    assert r.verdict is Verdict.UNKNOWN, r
    assert "not a boolean" in r.reason


def test_series_absent_from_catalog_is_unknown():
    r = evaluate("latest('ghost') > 1", ["ghost"], CAT, asof=TODAY,
                 series_data={"ghost": daily([2.0])})
    assert r.verdict is Verdict.UNKNOWN, r
    assert "not in catalog" in r.reason


def test_builtins_are_not_reachable():
    r = ev("__import__('os').getcwd() == '/'", ["us10y"], {"us10y": daily([5.0])})
    assert r.verdict is Verdict.UNKNOWN, r


# --- helper semantics ------------------------------------------------------

def test_consecutive_below_counts_observations():
    # Exactly 20 points, all below: answerable and true.
    r = ev("consecutive_below('us10y', 4.5, 20)", ["us10y"],
           {"us10y": daily([4.4] * 20)})
    assert r.verdict is Verdict.TRIPPED, r
    # One recent breach inside the window: answerable and false.
    r = ev("consecutive_below('us10y', 4.5, 20)", ["us10y"],
           {"us10y": daily([4.4] * 19 + [4.6])})
    assert r.verdict is Verdict.NOT_TRIPPED, r


def test_consecutive_only_looks_at_the_tail():
    # A breach older than the window must not block the trip.
    r = ev("consecutive_below('us10y', 4.5, 5)", ["us10y"],
           {"us10y": daily([9.9, 9.9] + [4.0] * 5)})
    assert r.verdict is Verdict.TRIPPED, r


def test_drawdown_measures_from_window_peak():
    r = ev("drawdown('xlu', 60) > 0.10", ["xlu"],
           {"xlu": daily([100.0, 105.0, 90.0])})  # (105-90)/105 = 14.3%
    assert r.verdict is Verdict.TRIPPED, r
    r = ev("drawdown('xlu', 60) > 0.20", ["xlu"],
           {"xlu": daily([100.0, 105.0, 90.0])})
    assert r.verdict is Verdict.NOT_TRIPPED, r


def test_drawdown_window_excludes_old_peak():
    # The 200 is outside a 60-day window, so it must not set the peak.
    old = [Observation((TODAY - dt.timedelta(days=300)).isoformat(), 200.0)]
    recent = daily([100.0, 90.0])
    r = ev("drawdown('xlu', 60) > 0.20", ["xlu"], {"xlu": old + recent})
    assert r.verdict is Verdict.NOT_TRIPPED, r  # (100-90)/100 = 10%, not >20%


def test_change_uses_calendar_days_on_sparse_series():
    # Quarterly-ish points: 90 calendar days back, not 90 observations.
    obs = [
        Observation((TODAY - dt.timedelta(days=120)).isoformat(), 600.0),
        Observation((TODAY - dt.timedelta(days=95)).isoformat(), 600.0),
        Observation(TODAY.isoformat(), 500.0),
    ]
    r = ev("change('capex', 90) < -0.10", ["capex"], {"capex": obs})
    assert r.verdict is Verdict.TRIPPED, r  # (500-600)/600 = -16.7%


def test_inputs_are_captured_for_the_audit_trail():
    r = ev("latest('us10y') > 5.0", ["us10y"], {"us10y": daily([4.0, 5.1])})
    key = "latest(us10y)"
    assert key in r.inputs, r.inputs
    assert r.inputs[key]["value"] == 5.1
    assert r.inputs[key]["date"] == TODAY.isoformat()


# --- the freshness gate, against real files --------------------------------

def test_stale_stored_series_is_unknown_not_false():
    with tempfile.TemporaryDirectory() as td:
        store.DATA_DIR = pathlib.Path(td)
        store.MANIFEST_PATH = pathlib.Path(td) / "_manifest.json"
        # A value that would make the predicate FALSE if it were evaluated,
        # so a regression here shows up as a wrong answer, not a lucky one.
        store.write_series("us10y", [Observation("2026-01-01", 3.0)],
                           source="t", method="t")
        r = evaluate("latest('us10y') > 5.0", ["us10y"], CAT, asof=TODAY)
        assert r.verdict is Verdict.UNKNOWN, r
        assert "limit 4d" in r.reason


def test_fresh_stored_series_evaluates():
    with tempfile.TemporaryDirectory() as td:
        store.DATA_DIR = pathlib.Path(td)
        store.MANIFEST_PATH = pathlib.Path(td) / "_manifest.json"
        store.write_series("us10y", [Observation(TODAY.isoformat(), 5.2)],
                           source="t", method="t")
        r = evaluate("latest('us10y') > 5.0", ["us10y"], CAT, asof=TODAY)
        assert r.verdict is Verdict.TRIPPED, r


def test_one_stale_input_blocks_a_multi_series_predicate():
    with tempfile.TemporaryDirectory() as td:
        store.DATA_DIR = pathlib.Path(td)
        store.MANIFEST_PATH = pathlib.Path(td) / "_manifest.json"
        store.write_series("us10y", [Observation(TODAY.isoformat(), 4.0)],
                           source="t", method="t")
        store.write_series("xlu", [Observation("2026-01-01", 50.0)],
                           source="t", method="t")
        r = evaluate("consecutive_below('us10y', 4.5, 1) and drawdown('xlu', 60) > 0.1",
                     ["us10y", "xlu"], CAT, asof=TODAY)
        assert r.verdict is Verdict.UNKNOWN, r
        assert "xlu" in r.reason


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
