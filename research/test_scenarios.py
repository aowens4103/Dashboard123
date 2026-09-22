"""End-to-end scenarios against the real register.

The evaluator tests use synthetic predicates. These drive the actual
falsifiers committed in research/theses/, so they catch the failure the unit
tests cannot: a real predicate that parses, passes the entry gate, and then
turns out to be unevaluable or to mean something other than intended.

Data is synthetic — no market feed is reachable from this environment — so
these prove the predicates behave as written, not that the world looks like
any of these scenarios.

Run: python3 research/test_scenarios.py
"""

from __future__ import annotations

import datetime as dt
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).parent))
sys.path.insert(0, str(pathlib.Path(__file__).parent / "series"))

import detect
import store
from evaluate import Verdict
from store import Observation

TODAY = dt.date(2026, 9, 22)


def _daily(values: list[float], *, end: dt.date = TODAY) -> list[Observation]:
    n = len(values)
    return [Observation((end - dt.timedelta(days=n - 1 - i)).isoformat(), v)
            for i, v in enumerate(values)]


def _seed(tmp: pathlib.Path, series: dict[str, list[Observation]]) -> None:
    store.DATA_DIR = tmp
    store.MANIFEST_PATH = tmp / "_manifest.json"
    for name, obs in series.items():
        store.write_series(name, obs, source="fixture", method="synthetic")


def _find(results, thesis, falsifier):
    for r in results:
        if r["thesis"] == thesis and r["falsifier"] == falsifier:
            return r
    raise AssertionError(f"{thesis}/{falsifier} not in results")


def test_duration_f1_trips_when_rates_fall_but_utilities_do_not_recover():
    """The real falsifier: rates down 20 sessions AND XLU still 10%+ off its high.

    This is the scenario that would kill the duration thesis — if the
    de-rating were a discount-rate effect, it could not survive rates falling.
    """
    with tempfile.TemporaryDirectory() as td:
        _seed(pathlib.Path(td), {
            "us10y": _daily([4.2] * 20),
            # Peak 100 inside the 60-day window, last 85 -> 15% drawdown.
            "xlu": _daily([100.0] + [92.0] * 28 + [85.0]),
        })
        r = _find(detect.run(asof=TODAY), "2026-09-duration-repricing", "f1")
        assert r["verdict"] is Verdict.TRIPPED, r
        assert "consecutive_below(us10y,4.5,20)" in r["inputs"], r["inputs"]
        assert "drawdown(xlu,60)" in r["inputs"], r["inputs"]


def test_duration_f1_holds_when_utilities_recover():
    """Same rate fall, but utilities recover — the thesis survives."""
    with tempfile.TemporaryDirectory() as td:
        _seed(pathlib.Path(td), {
            "us10y": _daily([4.2] * 20),
            "xlu": _daily([100.0] + [92.0] * 28 + [99.0]),  # 1% off the high
        })
        r = _find(detect.run(asof=TODAY), "2026-09-duration-repricing", "f1")
        assert r["verdict"] is Verdict.NOT_TRIPPED, r


def test_duration_f1_is_unknown_on_a_short_rate_history():
    """Rates fell, but only for eight sessions. Not an answer either way."""
    with tempfile.TemporaryDirectory() as td:
        _seed(pathlib.Path(td), {
            "us10y": _daily([4.2] * 8),
            "xlu": _daily([100.0] + [92.0] * 28 + [85.0]),
        })
        r = _find(detect.run(asof=TODAY), "2026-09-duration-repricing", "f1")
        assert r["verdict"] is Verdict.UNKNOWN, r
        assert "need 20 consecutive" in r["reason"], r


def test_oil_f1_trips_on_cheap_crude_with_the_split_intact():
    """Crude below 75 for 15 sessions while new lows still outnumber highs."""
    with tempfile.TemporaryDirectory() as td:
        _seed(pathlib.Path(td), {
            "wti_front": _daily([70.0] * 15),
            "spx_new_lows": _daily([30.0]),
            "spx_new_highs": _daily([7.0]),
        })
        r = _find(detect.run(asof=TODAY), "2026-09-oil-single-factor", "f1")
        assert r["verdict"] is Verdict.TRIPPED, r


def test_oil_f1_holds_when_the_split_closes():
    with tempfile.TemporaryDirectory() as td:
        _seed(pathlib.Path(td), {
            "wti_front": _daily([70.0] * 15),
            "spx_new_lows": _daily([5.0]),
            "spx_new_highs": _daily([40.0]),
        })
        r = _find(detect.run(asof=TODAY), "2026-09-oil-single-factor", "f1")
        assert r["verdict"] is Verdict.NOT_TRIPPED, r


def test_kshaped_f1_trips_on_three_months_of_recovery():
    """Low-income QSR traffic positive three months running."""
    with tempfile.TemporaryDirectory() as td:
        monthly = [
            Observation((TODAY - dt.timedelta(days=d)).isoformat(), v)
            for d, v in [(70, 0.01), (40, 0.02), (10, 0.015)]
        ]
        _seed(pathlib.Path(td), {"qsr_traffic_low_income_yoy": monthly})
        r = _find(detect.run(asof=TODAY), "2026-09-k-shaped-consumer", "f1")
        assert r["verdict"] is Verdict.TRIPPED, r


def test_kshaped_f1_holds_when_one_month_is_still_negative():
    with tempfile.TemporaryDirectory() as td:
        monthly = [
            Observation((TODAY - dt.timedelta(days=d)).isoformat(), v)
            for d, v in [(70, 0.01), (40, -0.03), (10, 0.015)]
        ]
        _seed(pathlib.Path(td), {"qsr_traffic_low_income_yoy": monthly})
        r = _find(detect.run(asof=TODAY), "2026-09-k-shaped-consumer", "f1")
        assert r["verdict"] is Verdict.NOT_TRIPPED, r


def test_credit_f1_trips_on_a_tech_spread_blowout():
    """Tech IG spread 60bp+ past the broad index."""
    with tempfile.TemporaryDirectory() as td:
        _seed(pathlib.Path(td), {
            "tech_ig_oas": _daily([1.95]),
            "ig_oas": _daily([1.20]),  # 75bp premium
        })
        r = _find(detect.run(asof=TODAY), "2026-09-ai-credit-financing", "f1")
        assert r["verdict"] is Verdict.TRIPPED, r


def test_every_real_predicate_is_evaluable_given_its_inputs():
    """No committed falsifier is dead on arrival.

    Feeds every catalogued series a plausible value and asserts that no
    falsifier comes back UNKNOWN for a reason other than history depth. A
    predicate that cannot evaluate even with all its inputs present is broken,
    and the entry gate cannot catch that — only running it can.
    """
    import register as reg
    plausible = {
        "us10y": 5.0, "wti_front": 92.0, "hy_oas": 3.6, "ig_oas": 1.1,
        "xlu": 41.0, "xlp": 70.0, "spx_new_highs": 7.0, "spx_new_lows": 30.0,
        "new_low_nonbond_share": 0.45, "wti_us10y_corr_1m": 0.96,
        "top10_weight": 0.372, "capweight_eqweight_pe_premium": 0.30,
        "mag7_eps_growth_fwd": 0.228, "sp493_eps_growth_fwd": 0.121,
        "hyperscaler_capex_fwd_est": 602.0, "hyperscaler_fcf_ttm": -12.0,
        "hyperscaler_lt_debt": 98.0, "tech_ig_oas": 1.4,
        "qsr_traffic_low_income_yoy": -0.09,
    }
    catalog = reg.load_catalog()
    missing = set(catalog) - set(plausible)
    assert not missing, f"catalog grew; add plausible values for {sorted(missing)}"

    with tempfile.TemporaryDirectory() as td:
        # 300 days of flat history: deep enough for every window in the register.
        _seed(pathlib.Path(td), {k: _daily([v] * 300) for k, v in plausible.items()})
        broken = [
            (r["thesis"], r["falsifier"], r["reason"])
            for r in detect.run(asof=TODAY) if r["verdict"] is Verdict.UNKNOWN
        ]
        assert not broken, f"unevaluable falsifiers: {broken}"


def test_stamp_changes_exactly_one_line():
    """A trip stamp must not rewrite the file.

    Parsing and re-dumping the YAML produced a 120-line diff, converted block
    scalars to quoted strings and turned 0.70 into 0.7. The register's audit
    value is that a diff is readable, so this is pinned.
    """
    import difflib
    from detect import stamp_tripped

    path = (pathlib.Path(__file__).parent / "theses"
            / "2026-09-duration-repricing.yaml")
    before = path.read_text()
    after = stamp_tripped(before, "f1", "2026-09-22")
    changed = [l for l in difflib.unified_diff(before.splitlines(),
                                               after.splitlines(), lineterm="")
               if l.startswith(("+", "-")) and not l.startswith(("+++", "---"))]
    assert changed == ["+    tripped: 2026-09-22"], changed


def test_stamp_is_idempotent():
    from detect import stamp_tripped
    path = (pathlib.Path(__file__).parent / "theses"
            / "2026-09-oil-single-factor.yaml")
    once = stamp_tripped(path.read_text(), "f2", "2026-09-22")
    twice = stamp_tripped(once, "f2", "2026-09-22")
    assert once == twice
    # Re-stamping with a later date updates in place rather than duplicating.
    later = stamp_tripped(once, "f2", "2026-10-01")
    assert later.count("tripped:") == once.count("tripped:")
    assert "tripped: 2026-10-01" in later


def test_stamp_targets_the_right_falsifier():
    import yaml
    from detect import stamp_tripped
    path = (pathlib.Path(__file__).parent / "theses"
            / "2026-09-k-shaped-consumer.yaml")
    doc = yaml.safe_load(stamp_tripped(path.read_text(), "f2", "2026-09-22"))
    by_id = {f["id"]: f for f in doc["falsifiers"]}
    # YAML parses a bare date into a date object; register.load_theses
    # normalises it back to an ISO string, so compare as text here.
    assert str(by_id["f2"].get("tripped")) == "2026-09-22", by_id["f2"]
    assert by_id["f1"].get("tripped") in (None, ""), by_id["f1"]


def test_stamped_file_still_passes_the_entry_gate():
    """A stamp must not break schema validation."""
    import tempfile as tf, shutil, yaml
    import register as reg
    from detect import stamp_tripped

    src_dir = pathlib.Path(__file__).parent
    with tf.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        shutil.copytree(src_dir / "theses", tmp / "theses")
        target = tmp / "theses" / "2026-09-duration-repricing.yaml"
        target.write_text(stamp_tripped(target.read_text(), "f1", "2026-09-22"))

        old_dir = reg.THESES_DIR
        try:
            reg.THESES_DIR = tmp / "theses"  # noqa: F841
            findings = reg.validate(reg.Register(theses=reg.load_theses(),
                                                 catalog=reg.load_catalog()))
        finally:
            reg.THESES_DIR = old_dir
        errors = [f for f in findings if f.level == "error"]
        assert not errors, errors
        doc = yaml.safe_load(target.read_text())
        assert doc["confidence"] == 0.70, "numeric precision was altered"


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
