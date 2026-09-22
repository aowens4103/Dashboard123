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
python3 research/register.py        # validate every thesis
cd research && python3 test_register.py   # test the gate itself
```

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

## Staleness and missing data

Every series declares `max_age_days`. A run that cannot get data fresher than
that **fails loudly and stops** — it never substitutes a worse source. The
failure this guards against is a monitor quietly decaying into sentiment
tracking while still looking like measurement.

Nine of the nineteen catalogued series are `ingest: manual` today. Those are
genuinely not free, and a predicate depending on one is only as live as its
last manual update.

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

Six theses seeded from the September 2026 breadth research, all `ungated`:
no position may be taken on any of them until an expression is checked and
gated. Predicate evaluation itself is stage 3 and is not built yet; the
helpers above are the contract that evaluator will implement.
