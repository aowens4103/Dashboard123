"""Predicate evaluation: the five helpers, and a verdict that can be UNKNOWN.

Falsifier trips are detected here, by code, never by a model. The design rule
this module exists to enforce is that a predicate over data we do not have
returns UNKNOWN, not FALSE. A monitor that reports "not tripped" because its
inputs are dead is worse than no monitor, so every path that cannot produce a
real answer produces UNKNOWN with a reason attached.

Window semantics differ between helpers, deliberately, and this is the one
thing to get right when writing a predicate:

  consecutive_below / consecutive_above   n = OBSERVATIONS
      Counts stored points, so 20 means 20 trading days on a daily series and
      20 months on a monthly one.

  change / drawdown                       window = CALENDAR DAYS
      Reaches back a real duration, so it behaves sensibly on sparse series
      like quarterly consensus estimates where observation counts are tiny.

Insufficient history is UNKNOWN, never FALSE: asking for 20 consecutive
observations from a series holding 8 is an unanswered question, not a
negative answer.
"""

from __future__ import annotations

import datetime as dt
import pathlib
import sys
from dataclasses import dataclass, field
from enum import Enum

sys.path.insert(0, str(pathlib.Path(__file__).parent / "series"))

from store import Freshness, Observation, read_series, status  # noqa: E402


class Verdict(str, Enum):
    TRIPPED = "tripped"          # the predicate is true; the thesis is falsified
    NOT_TRIPPED = "not_tripped"  # the predicate is false, on data we trust
    UNKNOWN = "unknown"          # we cannot say, and must not guess


class Unanswerable(Exception):
    """A helper cannot produce a value. Propagates to UNKNOWN, never to False."""


@dataclass
class Evaluation:
    verdict: Verdict
    reason: str
    # Values the helpers actually read, so a trip carries its own evidence.
    inputs: dict = field(default_factory=dict)


def _observations(name: str, series_data: dict[str, list[Observation]]) -> list[Observation]:
    obs = series_data.get(name)
    if not obs:
        raise Unanswerable(f"{name}: no observations")
    return obs


def _at_or_before(obs: list[Observation], target: dt.date) -> Observation | None:
    """Newest observation at or before target, or None if history starts later."""
    candidates = [o for o in obs if dt.date.fromisoformat(o.date) <= target]
    return candidates[-1] if candidates else None


class Helpers:
    """The predicate namespace. Every method records what it read."""

    def __init__(self, series_data: dict[str, list[Observation]], asof: dt.date):
        self._data = series_data
        self._asof = asof
        self.inputs: dict = {}

    def _record(self, key: str, value) -> None:
        self.inputs[key] = value

    def latest(self, name: str) -> float:
        obs = _observations(name, self._data)
        self._record(f"latest({name})", {"value": obs[-1].value, "date": obs[-1].date})
        return obs[-1].value

    def change(self, name: str, days: int) -> float:
        obs = _observations(name, self._data)
        last = obs[-1]
        target = dt.date.fromisoformat(last.date) - dt.timedelta(days=int(days))
        base = _at_or_before(obs, target)
        if base is None:
            raise Unanswerable(
                f"{name}: history starts {obs[0].date}, need a point at or before "
                f"{target.isoformat()}"
            )
        if base.value == 0:
            raise Unanswerable(f"{name}: base value at {base.date} is zero")
        result = (last.value - base.value) / abs(base.value)
        self._record(f"change({name},{days})", {
            "from": {"value": base.value, "date": base.date},
            "to": {"value": last.value, "date": last.date},
            "result": result,
        })
        return result

    def drawdown(self, name: str, window_days: int) -> float:
        obs = _observations(name, self._data)
        last = obs[-1]
        cutoff = dt.date.fromisoformat(last.date) - dt.timedelta(days=int(window_days))
        window = [o for o in obs if dt.date.fromisoformat(o.date) >= cutoff]
        if len(window) < 2:
            raise Unanswerable(
                f"{name}: {len(window)} observation(s) in the last {window_days}d, need 2+"
            )
        peak = max(o.value for o in window)
        if peak <= 0:
            raise Unanswerable(f"{name}: window peak is {peak}, cannot express a drawdown")
        result = (peak - last.value) / peak
        self._record(f"drawdown({name},{window_days})", {
            "peak": peak, "latest": last.value, "date": last.date, "result": result,
        })
        return result

    def _consecutive(self, name: str, threshold: float, n: int, *, below: bool) -> bool:
        obs = _observations(name, self._data)
        n = int(n)
        if n <= 0:
            raise Unanswerable(f"{name}: n must be positive, got {n}")
        if len(obs) < n:
            raise Unanswerable(
                f"{name}: {len(obs)} observation(s) stored, need {n} consecutive"
            )
        tail = obs[-n:]
        ok = all((o.value < threshold) if below else (o.value > threshold) for o in tail)
        fn = "consecutive_below" if below else "consecutive_above"
        self._record(f"{fn}({name},{threshold},{n})", {
            "window": [{"date": o.date, "value": o.value} for o in tail],
            "result": ok,
        })
        return ok

    def consecutive_below(self, name: str, threshold: float, n: int) -> bool:
        return self._consecutive(name, threshold, n, below=True)

    def consecutive_above(self, name: str, threshold: float, n: int) -> bool:
        return self._consecutive(name, threshold, n, below=False)


def evaluate(predicate: str, requires: list[str], catalog: dict, *,
             asof: dt.date | None = None,
             series_data: dict[str, list[Observation]] | None = None) -> Evaluation:
    """Evaluate one falsifier predicate to a tri-state verdict."""
    asof = asof or dt.date.today()

    # Freshness gate first: a stale input makes the question unanswerable,
    # whatever the numbers would have said.
    blockers = []
    for name in requires:
        entry = catalog.get(name)
        if entry is None:
            blockers.append(f"{name}: not in catalog")
            continue
        if series_data is not None and name in series_data:
            continue  # caller supplied data and vouches for it (tests, backtests)
        st = status(name, entry, asof=asof)
        if st.freshness is not Freshness.FRESH:
            blockers.append(f"{name}: {st.detail}")
    if blockers:
        return Evaluation(Verdict.UNKNOWN, "; ".join(blockers))

    data = series_data if series_data is not None else {
        name: read_series(name) for name in requires
    }
    helpers = Helpers(data, asof)
    namespace = {
        "latest": helpers.latest,
        "change": helpers.change,
        "drawdown": helpers.drawdown,
        "consecutive_below": helpers.consecutive_below,
        "consecutive_above": helpers.consecutive_above,
    }

    try:
        result = eval(predicate, {"__builtins__": {}}, namespace)  # noqa: S307
    except Unanswerable as exc:
        return Evaluation(Verdict.UNKNOWN, str(exc), helpers.inputs)
    except ZeroDivisionError:
        return Evaluation(Verdict.UNKNOWN, "division by zero", helpers.inputs)
    except Exception as exc:
        # An evaluator bug must not read as "not tripped".
        return Evaluation(Verdict.UNKNOWN,
                          f"evaluation error: {type(exc).__name__}: {exc}",
                          helpers.inputs)

    if not isinstance(result, bool):
        return Evaluation(
            Verdict.UNKNOWN,
            f"predicate returned {type(result).__name__}, not a boolean",
            helpers.inputs)

    return Evaluation(Verdict.TRIPPED if result else Verdict.NOT_TRIPPED,
                      "predicate true" if result else "predicate false",
                      helpers.inputs)
