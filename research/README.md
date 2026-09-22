# Thesis Register

The research agent's memory. Everything it believes lives in `theses/`;
everything it has observed lives in `evidence/`. Nothing carries between runs
except what is committed here — a run that does not commit its findings did not
happen.

## Why files in git rather than a database

A database stores current state. Git stores *how state changed*, which is what
both calibration and record-keeping actually need:

- **Tamper-evident falsifier edits.** Loosening a falsifier is a commit with an
  author, a timestamp and a diff. The rule enforces itself rather than relying
  on discipline.
- **Reproducible records.** A commit hash pins exactly what was believed, on
  what evidence, on what date.
- **Review by diff.** What changed this week is `git log -p research/`.

## Layout

```
research/
  theses/*.yaml        one file per thesis; the unit of belief
  evidence/*.jsonl     append-only observations, one file per month
  series/catalog.yaml  named series the predicates evaluate against
  schema/              JSON Schema for a thesis entry
  register.py          loading, validation, the predicate contract
  test_register.py     tests for the entry gate
```

## The entry gate

```bash
python3 research/register.py     # validate every thesis
python3 research/test_register.py    # the entry gate
python3 research/test_series.py      # store and freshness
python3 research/test_evaluate.py    # predicate evaluation
python3 research/test_scenarios.py   # the real predicates, end to end
```

`test_scenarios.py` drives the falsifiers actually committed in `theses/`,
which catches what the unit tests cannot: a predicate that parses, passes the
gate, and then turns out to be unevaluable or to mean something other than
intended. One of its tests feeds every catalogued series a plausible value and
asserts that no committed falsifier comes back `unknown` — a predicate that
cannot evaluate even with all its inputs present is broken, and only running it
reveals that.

Errors block entry. Warnings do not. A thesis cannot enter the register if it:

- has fewer than two falsifiers, or more than four
- has a falsifier whose predicate does not parse
- has a predicate that reaches for a series it did not declare in `requires`
- references a series absent from `series/catalog.yaml`
- omits `authors_doubt`
- has a `confidence` that disagrees with the last `confidence_history` entry
- is `gated` for a position without naming an instrument

## The predicate contract

**Falsifier trips are detected by code, never by a model.** A model asked
"has this been falsified?" produces a plausible answer every time, including
when the data is missing or contradictory. A predicate either evaluates or it
errors.

Predicates are expressions over these helpers, and nothing else:

| Helper | Returns |
| --- | --- |
| `latest(series)` | most recent value |
| `change(series, days)` | fractional change over the window |
| `drawdown(series, window_days)` | decline from the window high, positive fraction |
| `consecutive_below(series, threshold, n)` | true if below for n consecutive observations |
| `consecutive_above(series, threshold, n)` | true if above for n consecutive observations |

The series name is always a string literal in the first argument. Bare names,
attribute access, subscripting and lambdas are rejected — they are how an
undeclared input gets smuggled past the gate.

```yaml
predicate: "consecutive_below('us10y', 4.5, 20) and drawdown('xlu', 60) > 0.10"
requires: [us10y, xlu]
```

A falsifier you cannot express this way is not a falsifier, it is a feeling.
That friction is deliberate and lands at entry, when the thinking is cheap to fix.

**Window semantics differ between helpers, deliberately.** `consecutive_below`
and `consecutive_above` count **observations**, so 20 means 20 trading days on
a daily series and 20 months on a monthly one. `change` and `drawdown` reach
back **calendar days**, so they behave sensibly on sparse series like quarterly
consensus estimates. Getting this backwards is the easiest way to write a
predicate that means something other than intended.

## Evaluation: three verdicts, never two

```bash
python3 research/detect.py            # evaluate every active falsifier
python3 research/detect.py --record   # also stamp trips and log evidence
```

| Verdict | Meaning |
| --- | --- |
| `tripped` | The predicate is true on data we trust. The thesis is falsified |
| `not_tripped` | The predicate is false on data we trust |
| `unknown` | We cannot say, and must not guess |

Everything that could make an answer unreliable lands on `unknown`: a missing
series, a stale series, too little history for the window, a divide by zero, a
non-boolean result, or a bug in a helper. **`unknown` is never `not_tripped`** —
that collapse would mark a thesis safe on the strength of data nobody has, and
it is the single failure this layer exists to prevent.

`detect.py` exits non-zero on a new trip, so a scheduled run can notify on it.
Detection is all it does: it never changes a thesis's status, confidence or
falsifiers. A trip is a summons for the interpret run, not a verdict.

`--record` stamps `tripped: <date>` on the falsifier and appends an evidence
entry carrying the input values that caused it, so a trip is reconstructable
from the repo alone. The stamp is a surgical text edit, not a YAML round-trip:
re-dumping rewrites block scalars and turns `0.70` into `0.7`, which would make
a one-field change a hundred-line diff and destroy the point of keeping this in
git. There is a test pinning the stamp to exactly one changed line.

## Staleness, and why coverage is reported

Every series declares `max_age_days`, measured on the latest **observation**,
not on when it was last fetched. A series pulled this morning whose newest
point is six months old is stale; measuring from retrieval is how a dead input
passes for a live one.

A stale or missing input makes a falsifier **BLIND**, never "not tripped". The
distinction between false and unknown is the point: a monitor that reports no
trips while half its inputs are dead is worse than no monitor.

```bash
python3 research/coverage.py          # which theses can actually be monitored
```

Coverage reports three states per thesis — fully monitored, partial, or blind —
and lists manual series that need a hand update. A thesis with no monitored
falsifier cannot be falsified by this system whatever its register entry says,
so it should either get its series ingested or move to `on_watch`.

## Getting data in

```bash
python3 research/series/ingest.py --preflight   # are the sources reachable?
python3 research/series/ingest.py               # every ingest: auto series
python3 research/series/record.py <series> <value> <date> \
    --source "<publisher>" --url "<page actually fetched>"
```

Six of nineteen series are `ingest: auto` (FRED and Stooq). Four are `derived`
and need the breadth computation that does not exist yet. Nine are `manual`:
forward EPS consensus, hyperscaler financials, index concentration and QSR
traffic have no free automated feed, so they are hand-entered on a
quarterly-ish rhythm. `record.py` requires both a source and a fetched URL, so
the manual path does not weaken the provenance rule the automated feeds follow.

**The source adapters are unverified against live APIs.** They were written in
a container whose egress proxy blocks every market data host. Run
`ingest.py --preflight` somewhere with network access before trusting them.

## Adding a thesis

1. Copy an existing file in `theses/`. The id is `YYYY-MM-slug` and must match
   the filename stem.
2. Write the statement as one falsifiable claim, not a topic.
3. Write two to four falsifiers as predicates, declaring every series used.
4. Add any new series to `series/catalog.yaml` with a `max_age_days`.
5. Fill `authors_doubt` honestly. It is the cheapest antidote to fluent
   overconfidence and it is mandatory.
6. Leave `expression.status: ungated` until the instrument, size band, funding
   source and liquidity are actually checked.
7. Run the validator. Commit only when it passes.

## Evidence entries

Append to `evidence/YYYY-MM.jsonl`. Never rewrite an entry — a superseded
observation gets a new one. Every entry carries provenance:

```json
{"ts":"2026-09-21","thesis":"2026-09-duration-repricing","direction":"for",
 "observation":"...","source":{"url":"...","retrieved_at":"2026-09-21"},
 "run":"weekly-synthesis"}
```

`direction` is `for`, `against` or `context`. A figure without a fetched source
URL does not go in — a search-result summary is not a source.

## Status

Six theses seeded from the September 2026 breadth research. Five are
`ungated` — no position may be taken until an expression is checked and gated.
The oil thesis is `diagnostic`: it carries no position by design and
permanently, because its value is arguing against acting on the Hindenburg
cluster rather than expressing a trade.

All six are currently **blind**: no series has been ingested, so every
falsifier evaluates to `unknown`. The evaluator is built and tested against
fixtures — 59 tests pass — but it has never run against real market data,
because every data host is blocked from the build environment. Treat "works on
fixtures" as exactly that claim and no more.
