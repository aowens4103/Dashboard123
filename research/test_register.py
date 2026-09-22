"""Tests for the register entry gate.

The gate only earns its place if it rejects bad entries, so these lean on the
failure paths: predicates that do not parse, that reach for series they did not
declare, or that smuggle in an undeclared input as a bare name.

Run: python3 research/test_register.py
"""

from __future__ import annotations

import copy
import datetime as dt

import register as reg

CATALOG = {"us10y": {}, "xlu": {}, "wti_front": {}}


def check(pred, declared, catalog=None):
    return reg.check_predicate(pred, declared, catalog if catalog is not None else CATALOG)


def test_good_predicate_passes():
    assert check("latest('us10y') > 5.0", ["us10y"]) == []
    assert check("consecutive_below('us10y', 4.5, 20) and drawdown('xlu', 60) > 0.1",
                 ["us10y", "xlu"]) == []


def test_syntax_error_is_caught():
    problems = check("latest('us10y') >", ["us10y"])
    assert any("does not parse" in p for p in problems), problems


def test_undeclared_series_is_caught():
    problems = check("latest('us10y') > latest('xlu')", ["us10y"])
    assert any("does not declare it" in p and "xlu" in p for p in problems), problems


def test_declared_but_unused_is_caught():
    problems = check("latest('us10y') > 5", ["us10y", "xlu"])
    assert any("never uses it" in p for p in problems), problems


def test_series_missing_from_catalog_is_caught():
    problems = check("latest('made_up') > 1", ["made_up"])
    assert any("not in series/catalog.yaml" in p for p in problems), problems


def test_unknown_helper_is_caught():
    problems = check("rolling_mean('us10y', 20) > 5", ["us10y"])
    assert any("unknown helper" in p for p in problems), problems


def test_bare_name_is_caught():
    # The failure this is really guarding: an undeclared input smuggled in
    # as a bare name rather than read through a helper.
    problems = check("us10y > 5.0", ["us10y"])
    assert any("bare name" in p for p in problems), problems


def test_attribute_access_is_blocked():
    problems = check("latest('us10y').real > 5", ["us10y"])
    assert any("attribute" in p for p in problems), problems


def test_wrong_arity_is_caught():
    problems = check("drawdown('xlu') > 0.1", ["xlu"])
    assert any("takes 2 arguments" in p for p in problems), problems


def test_series_name_must_be_literal():
    problems = check("latest(1) > 5", ["us10y"])
    assert any("string literal" in p for p in problems), problems


def _live_thesis():
    return {
        "id": "2026-09-example",
        "created": "2026-09-01",
        "statement": "A statement long enough to satisfy the schema minimum.",
        "mechanism": "A mechanism description comfortably past the forty character minimum length.",
        "status": "live",
        "confidence": 0.5,
        "confidence_history": [{"date": "2026-09-01", "value": 0.5, "reason": "initial"}],
        "falsifiers": [
            {"id": "f1", "predicate": "latest('us10y') < 3.0", "requires": ["us10y"],
             "registered": "2026-09-01", "rationale": "A rationale past twenty chars."},
            {"id": "f2", "predicate": "drawdown('xlu', 60) > 0.2", "requires": ["xlu"],
             "registered": "2026-09-01", "rationale": "Another rationale past twenty chars."},
        ],
        "expression": {"status": "ungated", "instrument": None},
        "horizon": "2027-01-01",
        "review": (dt.date.today() + dt.timedelta(days=30)).isoformat(),
        "authors_doubt": "A doubt statement that is comfortably past the forty character minimum.",
    }


def _validate_one(doc, stem=None):
    doc = copy.deepcopy(doc)
    doc["_stem"] = stem or doc["id"]
    doc["_path"] = "research/theses/x.yaml"
    register = reg.Register(theses={doc["id"]: doc}, catalog=CATALOG)
    return reg.validate(register)


def test_clean_thesis_validates():
    errors = [f for f in _validate_one(_live_thesis()) if f.level == "error"]
    assert errors == [], errors


def test_confidence_must_match_history():
    doc = _live_thesis()
    doc["confidence"] = 0.9
    errors = [f for f in _validate_one(doc) if f.level == "error"]
    assert any("does not match the last confidence_history" in f.message for f in errors), errors


def test_id_must_match_filename():
    errors = [f for f in _validate_one(_live_thesis(), stem="something-else")
              if f.level == "error"]
    assert any("does not match filename" in f.message for f in errors), errors


def test_single_falsifier_is_rejected():
    doc = _live_thesis()
    doc["falsifiers"] = doc["falsifiers"][:1]
    errors = [f for f in _validate_one(doc) if f.level == "error"]
    assert errors, "a thesis with one falsifier must not enter the register"


def test_missing_authors_doubt_is_rejected():
    doc = _live_thesis()
    del doc["authors_doubt"]
    errors = [f for f in _validate_one(doc) if f.level == "error"]
    assert any("authors_doubt" in f.message for f in errors), errors


def test_gated_expression_needs_an_instrument():
    doc = _live_thesis()
    doc["expression"] = {"status": "gated", "instrument": None}
    errors = [f for f in _validate_one(doc) if f.level == "error"]
    assert any("must name an instrument" in f.message for f in errors), errors


def test_passed_review_date_warns():
    doc = _live_thesis()
    doc["review"] = (dt.date.today() - dt.timedelta(days=1)).isoformat()
    warnings = [f for f in _validate_one(doc) if f.level == "warning"]
    assert any("has passed" in f.message for f in warnings), warnings


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
