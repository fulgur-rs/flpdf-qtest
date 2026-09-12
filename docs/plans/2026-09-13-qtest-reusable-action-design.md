# A reusable action that detects qtest regressions on flpdf pull requests

Date: 2026-09-13

## Problem

The qtest survey runs only in this repository, against whichever flpdf ref CI
resolves. flpdf is carrying a large backlog of performance, security, and
correctness work, and refactoring is in flight. A regression introduced by an
flpdf pull request is therefore found later — by the nightly sweep against
flpdf `main` — and attributing it costs a manual probe. `54dc57f` is the
shape: two `linearization` rows demoted, culprit located by probing `77a53b8b`
GOOD and `1b44e436` BAD to isolate `6b4891f5`.

Running the survey on the pull request itself removes that step: the change
under test is the change that regressed.

## What the gate must not do

It must not block. Minor regressions are fixed in the pull request; the rest
are accepted, filed as a bead, and fixed as follow-up work. A check that fails
the build would invert that operating model.

## Why not the parity ledger

The obvious baseline is `parity/qtest-11.9.0.jsonl`, and it is the wrong one.

`verify-parity-manifest.py` reports a validation error when a `failing` or
`blocked` row is observed to pass (`:253-264`), because for this repository
that means the ledger is stale. For an flpdf pull request it means the pull
request fixed something. The failure mode is not hypothetical: run
`34607752109` is the only qtest CI failure in the recent window, and all
twelve of its validation errors are `failing entry has stale outcome 'pass'`,
resolved by `#171` promoting the rows.

The ledger is also the churning half of this repository — 22 of the last 30
commits are promotions — so pinning an action to a ref that carries it forces
a constant stream of bump pull requests, and a late bump makes flpdf compare
against rows it has already fixed.

## Why not "any failure is a regression"

A healthy survey is not green. The nightly at flpdf `e1014d0b` records:

```
total             2811
pass              2760
fail                48
expected-fail        3
```

The 48 failures cluster in `c-api` (18), `c-api-object-handle` (13),
`c-api-page` (5), `c-api-stream` (5), `writer-version` (3), `c-api-check` (2),
`preserve-unref` (1), and `windows-shell-globbing` (1) — the rows the ledger
records as `excluded` or `represented`. A bare failure count would report 48
false positives on every run.

## Design: separate the action from its baseline

The action carries code and corpus. The baseline is data the caller supplies.

```
fulgur-rs/flpdf-qtest                 the action
├── action.yml                        composite action (new)
├── scripts/diff-survey.py            baseline comparator (new)
├── scripts/run.sh                    survey runner
├── vendor/qpdf-qtest/ vendor/qtest/  corpus and driver
├── shim/ normalize/
│
├── parity/qtest-11.9.0.jsonl         this repository's nightly only
└── allowlist.txt                     this repository's nightly only

fulgur-rs/flpdf                       the data
└── .github/qtest-baseline.jsonl      51 rows, owned by flpdf
```

Three consequences follow.

**No cross-repository ordering hazard.** The baseline lives beside the code
that produces it, so a pull request that changes behaviour updates its own
baseline. There is no window in which one repository has landed and the other
has not.

**Ref pinning becomes cheap.** The churn was data. What remains — corpus,
shims, scripts — changes when the corpus is re-vendored or a script is fixed.
flpdf pins by SHA, matching its convention for every other action.

**The ledger keeps its job.** `parity/qtest-11.9.0.jsonl` stays a parity
management ledger, with `rationale`, `owner`, `bead`, and `replacement_ref`
per row. flpdf's baseline is a mechanical tripwire. Different owners,
different granularity; keeping them separate is not duplication.

## Baseline format

JSONL. One `meta` line, then the non-passing rows sorted by category and
ordinal. Identities absent from the file are expected to pass.

```jsonl
{"schema":1,"kind":"qtest-baseline","total":2811,"suites":{"c-api":44,...}}
{"id":"c-api 1","suite":"c-api","category":"c-api","ordinal":1,
 "description":"...","outcome":"fail","bead":null,"rationale":null}
```

`total` and `suites` are carried because a `.test` that dies early takes its
remaining subtests with it. That regression appears as absent identities, not
as failing ones, so row comparison alone cannot see it.

`bead` and `rationale` are optional and preserved across regeneration. That is
what makes the follow-up workflow a file edit: accepting a regression adds one
row, and the bead identifier goes in it.

## Classification

| Observation | Class | Effect |
| --- | --- | --- |
| Non-passing identity absent from the baseline | regression | reported, emitted to JSON |
| Passing identity present in the baseline | improvement | reported |
| `total` or per-suite count changed | drift | reported with the suites that moved |
| Non-passing identity present in the baseline | known | ignored |

An improvement can never fail the run, by construction rather than by policy.

## Action interface

```yaml
inputs:
  flpdf-dir:        # default ${{ github.workspace }}
  baseline:         # path; empty runs the survey without comparing
  fail-on:          # none | regression | any    default none
  survey-dir:       # default ${{ runner.temp }}/qtest-survey
  upload-artifact:  # default true
  artifact-name:    # default qtest-results
outputs:
  regressions: improvements: drift:
  regressions-json:   # input to bd create
  baseline-out:       # regenerated baseline, annotations merged
  survey-dir: results-xml: harness-log:
```

`fail-on: none` is the default, so the check reports and stays green.
Regressions surface as `::warning` annotations and a Job Summary section. The
outputs exist so a caller can add a comment step, or switch to
`fail-on: regression`, without a change here.

## Changes to run.sh

Two environment variables, both defaulting to today's behaviour.

`QTEST_SURVEY_DIR` overrides `live_dir`. Under a remote action `repo_root` is
`_actions/fulgur-rs/flpdf-qtest/<sha>/`, outside the workspace, which is the
wrong place to write artifacts and collect them from.

`QTEST_VERIFY=0` skips the closing `verify-allowlist.py` and
`verify-parity-manifest.py` calls. Those are this repository's data gates; the
action compares against the caller's baseline instead. Defaulting to `1` keeps
the local loop and the nightly unchanged, and keeps one implementation of the
survey rather than two that drift.

The action passes `FLPDF_DIR` and lets `run.sh` build, which removes the
duplicated fifteen-`--bin` list from the workflow. With a warm cache the build
is a no-op; the cost is losing its separate step timing.

## Migration

`ci.yml` calls the action with `uses: ./`, so this repository's nightly
exercises the same code path flpdf will use. Assertions in
`test_run_contract.py` split by owner: build, environment, and run assertions
read `action.yml`; job shape, flpdf ref resolution, and the `publish-metrics`
job stay on `ci.yml`. The fifteen `--bin` and `FLPDF_*_BIN` workflow
assertions are deleted, not moved — the workflow no longer names them.

Merge order is qtest first, then flpdf. The reverse leaves every flpdf pull
request failing on a missing action.

## Out of scope

Creating beads automatically. It needs write scope on a pull-request-triggered
workflow and deduplication against existing beads, and the severity call —
fix now or file and follow up — is the author's. The action emits the JSON;
filing stays manual.
