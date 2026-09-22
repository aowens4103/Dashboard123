"""Thesis register: loading, validation and the falsifier predicate contract.

The register is the agent's memory. Everything it believes lives in
research/theses/*.yaml; everything it has observed lives in
research/evidence/*.jsonl. Nothing carries between runs except what is
committed here.

Falsifier predicates are evaluated by code, never by a model. This module
defines the predicate contract and enforces it at entry: a predicate that does
not parse, or that reaches for a series it did not declare, is rejected before
it can reach the register.
"""

from __future__ import annotations

import ast
import datetime as dt
import json
import pathlib
from dataclasses import dataclass, field

import yaml
from jsonschema import Draft7Validator

ROOT = pathlib.Path(__file__).parent
THESES_DIR = ROOT / "theses"
EVIDENCE_DIR = ROOT / "evidence"
CATALOG_PATH = ROOT / "series" / "catalog.yaml"
SCHEMA_PATH = ROOT / "schema" / "thesis.schema.json"

# The only callables a predicate may use. Each takes a series name as its
# first argument; the validator reads that name to check the declaration.
PREDICATE_HELPERS = {
    "latest": 1,            # latest(series) -> float
    "change": 2,            # change(series, days) -> fractional change
    "drawdown": 2,          # drawdown(series, window_days) -> positive fraction
    "consecutive_below": 3,  # consecutive_below(series, threshold, n) -> bool
    "consecutive_above": 3,  # consecutive_above(series, threshold, n) -> bool
}

ACTIVE_STATUSES = {"live", "on_watch"}


class RegisterError(Exception):
    """A register entry violates the contract. Nothing is written."""


@dataclass
class Finding:
    thesis_id: str
    level: str  # "error" or "warning"
    message: str

    def __str__(self) -> str:
        mark = "ERROR" if self.level == "error" else "warn "
        return f"  [{mark}] {self.thesis_id}: {self.message}"


@dataclass
class Register:
    theses: dict = field(default_factory=dict)
    catalog: dict = field(default_factory=dict)

    @property
    def active(self) -> dict:
        return {k: v for k, v in self.theses.items()
                if v["status"] in ACTIVE_STATUSES}


def _isoformat_dates(obj):
    """YAML parses bare dates into date objects; the schema wants ISO strings.

    Normalising on load means a thesis file can write 2026-10-20 unquoted, the
    way a person naturally would, without every consumer having to care.
    """
    if isinstance(obj, dict):
        return {k: _isoformat_dates(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_isoformat_dates(v) for v in obj]
    if isinstance(obj, (dt.date, dt.datetime)):
        return obj.isoformat()
    return obj


def load_catalog() -> dict:
    with open(CATALOG_PATH) as fh:
        return yaml.safe_load(fh) or {}


def load_theses() -> dict:
    theses = {}
    for path in sorted(THESES_DIR.glob("*.yaml")):
        with open(path) as fh:
            doc = _isoformat_dates(yaml.safe_load(fh))
        if not doc:
            raise RegisterError(f"{path.name} is empty")
        doc["_path"] = str(path.relative_to(ROOT.parent))
        doc["_stem"] = path.stem
        theses[doc.get("id", path.stem)] = doc
    return theses


def load_register() -> Register:
    return Register(theses=load_theses(), catalog=load_catalog())


def parse_predicate(predicate: str) -> ast.Expression:
    """Parse a predicate, or raise RegisterError with a usable message."""
    try:
        return ast.parse(predicate, mode="eval")
    except SyntaxError as exc:
        raise RegisterError(f"predicate does not parse: {exc.msg}") from exc


def predicate_series(predicate: str) -> set[str]:
    """Series names a predicate reaches for, read off its syntax tree.

    A predicate references series only as string literals in the first
    argument of a helper call. Anything else is a contract violation and is
    reported by check_predicate rather than silently ignored.
    """
    tree = parse_predicate(predicate)
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Name):
            continue
        if node.func.id not in PREDICATE_HELPERS:
            continue
        if node.args and isinstance(node.args[0], ast.Constant):
            if isinstance(node.args[0].value, str):
                names.add(node.args[0].value)
    return names


def check_predicate(predicate: str, declared: list[str], catalog: dict) -> list[str]:
    """Return a list of problems with a predicate. Empty means it passes."""
    problems: list[str] = []

    try:
        tree = parse_predicate(predicate)
    except RegisterError as exc:
        return [str(exc)]

    # Only the declared helpers may be called, with the right arity.
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name):
                problems.append("only plain helper calls are allowed")
                continue
            fname = node.func.id
            if fname not in PREDICATE_HELPERS:
                problems.append(
                    f"unknown helper {fname!r}; allowed: "
                    + ", ".join(sorted(PREDICATE_HELPERS))
                )
                continue
            expected = PREDICATE_HELPERS[fname]
            if len(node.args) != expected:
                problems.append(
                    f"{fname}() takes {expected} arguments, got {len(node.args)}"
                )
            if not node.args or not isinstance(node.args[0], ast.Constant) \
                    or not isinstance(node.args[0].value, str):
                problems.append(
                    f"{fname}() needs a series name as a string literal first"
                )
        elif isinstance(node, ast.Name):
            # Bare names are how a predicate smuggles in an undeclared input.
            if node.id not in PREDICATE_HELPERS:
                problems.append(
                    f"bare name {node.id!r}; series must be read via a helper"
                )
        elif isinstance(node, (ast.Attribute, ast.Subscript, ast.Lambda)):
            problems.append("attribute, subscript and lambda are not allowed")

    used = predicate_series(predicate)
    declared_set = set(declared)

    for name in sorted(used - declared_set):
        problems.append(f"uses series {name!r} but does not declare it in requires")
    for name in sorted(declared_set - used):
        problems.append(f"declares series {name!r} but never uses it")
    for name in sorted(used | declared_set):
        if name not in catalog:
            problems.append(f"series {name!r} is not in series/catalog.yaml")

    return problems


def validate(register: Register | None = None) -> list[Finding]:
    """Validate every thesis. Errors block entry; warnings do not."""
    register = register or load_register()
    with open(SCHEMA_PATH) as fh:
        validator = Draft7Validator(json.load(fh))

    findings: list[Finding] = []
    today = dt.date.today()

    for tid, doc in sorted(register.theses.items()):
        body = {k: v for k, v in doc.items() if not k.startswith("_")}

        for err in sorted(validator.iter_errors(body), key=lambda e: list(e.path)):
            where = ".".join(str(p) for p in err.path) or "(root)"
            findings.append(Finding(tid, "error", f"{where}: {err.message}"))

        if doc.get("id") != doc["_stem"]:
            findings.append(Finding(
                tid, "error",
                f"id {doc.get('id')!r} does not match filename {doc['_stem']!r}"))

        history = doc.get("confidence_history") or []
        if history and "confidence" in doc:
            if abs(history[-1]["value"] - doc["confidence"]) > 1e-9:
                findings.append(Finding(
                    tid, "error",
                    "confidence does not match the last confidence_history entry"))
            dates = [h["date"] for h in history]
            if dates != sorted(dates):
                findings.append(Finding(
                    tid, "error", "confidence_history is not in date order"))

        for fals in doc.get("falsifiers", []):
            fid = fals.get("id", "?")
            for problem in check_predicate(
                fals.get("predicate", ""), fals.get("requires", []), register.catalog
            ):
                findings.append(Finding(tid, "error", f"falsifier {fid}: {problem}"))

        # The investable-expression gate: an active thesis needs a real one.
        expression = doc.get("expression") or {}
        if doc.get("status") in ACTIVE_STATUSES:
            if expression.get("status") == "ungated":
                findings.append(Finding(
                    tid, "warning",
                    "active but expression is ungated; no position may be taken"))
            elif not expression.get("instrument"):
                findings.append(Finding(
                    tid, "error",
                    "gated expression must name an instrument"))

        review = doc.get("review")
        if review and doc.get("status") in ACTIVE_STATUSES:
            review_date = dt.date.fromisoformat(review)
            if review_date < today:
                findings.append(Finding(
                    tid, "warning",
                    f"review date {review} has passed; re-argue or kill"))

    return findings


def main() -> int:
    register = load_register()
    findings = validate(register)
    errors = [f for f in findings if f.level == "error"]
    warnings = [f for f in findings if f.level == "warning"]

    print(f"register: {len(register.theses)} theses "
          f"({len(register.active)} active), "
          f"{len(register.catalog)} series in catalog")

    for finding in findings:
        print(finding)

    if errors:
        print(f"\nFAILED: {len(errors)} error(s), {len(warnings)} warning(s)")
        return 1
    print(f"\nOK: 0 errors, {len(warnings)} warning(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
