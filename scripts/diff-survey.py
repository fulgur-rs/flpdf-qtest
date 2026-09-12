#!/usr/bin/env python3
"""diff-survey.py — judge one qtest survey against a caller-supplied baseline.

Usage:
    scripts/diff-survey.py <harness.log> <qtest-results.xml> \
        --baseline <baseline.jsonl> [--summary <path>] [--write-baseline <path>]

This is the comparator the reusable action runs for callers that do not own
this repository's data. `verify-allowlist.py` and `verify-parity-manifest.py`
judge a run against `allowlist.txt` and `parity/qtest-11.9.0.jsonl`, which are
this repository's parity ledger and acceptance subset. A consumer such as
flpdf keeps its own baseline beside the code that produces it, so the two
never have to land in a particular order.

The baseline lists every identity whose outcome is not `pass`; anything absent
is expected to pass. It also carries the run's total and per-suite subtest
counts, because a .test that dies early takes its remaining subtests with it —
that regression appears as absent identities rather than failing ones, and row
comparison alone cannot see it.

Exit codes:
    0  Run judged. Whether a regression fails the job is the caller's policy,
       selected with --fail-on; the default reports and stays green.
    1  --fail-on tripped, or a real error: artifacts could not be parsed, or
       the baseline could not be read.
    2  Argument / IO error.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from qtest_results import Outcome, ResultError, Result, RunResults, parse_run

SCHEMA = 1
KIND = "qtest-baseline"

#: Outcomes that mean flpdf did not do what the suite asked of it. An
#: `expected-fail` is declared by the .test script rather than by flpdf, so its
#: appearance is a corpus change and never counts as a regression. It is still
#: recorded in the baseline, and a later flip to `unexpected-pass` surfaces as
#: drift: which side of an EXPECT_FAILURE a case lands on is flpdf's doing.
_REGRESSION_OUTCOMES = (Outcome.FAIL, Outcome.UNEXPECTED_PASS)

FAIL_ON = ("none", "regression", "any")


class BaselineError(ValueError):
    """The baseline file is missing, malformed, or of an unknown schema."""


@dataclass(frozen=True)
class BaselineEntry:
    id: str
    suite: str
    category: str
    ordinal: int
    description: str
    outcome: str
    bead: str | None = None
    rationale: str | None = None

    @property
    def key(self) -> tuple[str, int]:
        return (self.category, self.ordinal)


@dataclass(frozen=True)
class Baseline:
    total: int
    suites: dict[str, int]
    entries: tuple[BaselineEntry, ...]

    @property
    def by_key(self) -> dict[tuple[str, int], BaselineEntry]:
        return {entry.key: entry for entry in self.entries}


@dataclass(frozen=True)
class Diff:
    #: Non-passing identities the baseline does not account for.
    regressions: tuple[Result, ...]
    #: Baseline rows the run now passes.
    improvements: tuple[BaselineEntry, ...]
    #: Shape changes: totals, per-suite counts, and vanished identities.
    drift: tuple[str, ...]


def _identity(category: str, ordinal: int) -> str:
    return f"{category} {ordinal}"


def _sort_key(entry: BaselineEntry | Result) -> tuple[str, int]:
    return (entry.category, entry.ordinal)


# --- baseline I/O ------------------------------------------------------------


def _require(
    path: Path,
    number: int,
    record: dict,
    field: str,
    kind: type,
    *,
    optional: bool = False,
) -> None:
    """Reject a field of the wrong JSON type. `bool` is excluded explicitly
    because it passes `isinstance(..., int)`."""
    if field not in record:
        if optional:
            return
        raise BaselineError(f"{path}:{number}: entry is missing {field!r}")
    value = record[field]
    if optional and value is None:
        return
    if not isinstance(value, kind) or isinstance(value, bool) is not (
        kind is bool
    ):
        raise BaselineError(
            f"{path}:{number}: {field} must be {kind.__name__}, "
            f"got {value!r}"
        )


def load_baseline(path: Path) -> Baseline:
    """Read a baseline file. The first non-blank line is the meta record."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise BaselineError(f"cannot read baseline {path}: {exc}") from exc

    records = []
    for number, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            records.append((number, json.loads(line)))
        except json.JSONDecodeError as exc:
            raise BaselineError(f"{path}:{number}: invalid JSON: {exc}") from exc

    if not records:
        raise BaselineError(f"{path}: baseline is empty")

    _, meta = records[0]
    if not isinstance(meta, dict) or meta.get("kind") != KIND:
        raise BaselineError(
            f"{path}: first line must be the {KIND!r} meta record"
        )
    if meta.get("schema") != SCHEMA:
        raise BaselineError(
            f"{path}: unsupported schema {meta.get('schema')!r}, "
            f"expected {SCHEMA}"
        )

    total = meta.get("total")
    suites = meta.get("suites")
    if not isinstance(total, int) or isinstance(total, bool):
        raise BaselineError(f"{path}: meta total must be an integer")
    if not isinstance(suites, dict) or not all(
        isinstance(count, int) and not isinstance(count, bool)
        for count in suites.values()
    ):
        raise BaselineError(f"{path}: meta suites must map suite to a count")

    entries = []
    for number, record in records[1:]:
        if not isinstance(record, dict):
            raise BaselineError(f"{path}:{number}: entry must be an object")
        # This file is hand-edited: accepting a quoted ordinal would make the
        # entry's key miss the run's, reporting a known failure as a
        # regression, and would make sorting a file that mixes the two raise
        # TypeError.
        _require(path, number, record, "ordinal", int)
        for field in ("id", "suite", "category", "description", "outcome"):
            _require(path, number, record, field, str)
        for field in ("bead", "rationale"):
            _require(path, number, record, field, str, optional=True)
        try:
            entry = BaselineEntry(
                id=record["id"],
                suite=record["suite"],
                category=record["category"],
                ordinal=record["ordinal"],
                description=record["description"],
                outcome=record["outcome"],
                bead=record.get("bead"),
                rationale=record.get("rationale"),
            )
        except KeyError as exc:
            raise BaselineError(
                f"{path}:{number}: entry is missing {exc.args[0]!r}"
            ) from exc
        identity = _identity(entry.category, entry.ordinal)
        if entry.id != identity:
            raise BaselineError(
                f"{path}:{number}: id must be {identity!r}, got {entry.id!r}"
            )
        entries.append(entry)

    return Baseline(total=total, suites=dict(suites), entries=tuple(entries))


def render_baseline(baseline: Baseline) -> str:
    """Serialize a baseline. Entries are sorted so a row added or removed is a
    one-line diff rather than a reshuffle."""
    meta = {
        "schema": SCHEMA,
        "kind": KIND,
        "total": baseline.total,
        "suites": dict(sorted(baseline.suites.items())),
    }
    lines = [json.dumps(meta, separators=(",", ":"), sort_keys=True)]
    for entry in sorted(baseline.entries, key=_sort_key):
        lines.append(
            json.dumps(
                {
                    "id": entry.id,
                    "suite": entry.suite,
                    "category": entry.category,
                    "ordinal": entry.ordinal,
                    "description": entry.description,
                    "outcome": entry.outcome,
                    "bead": entry.bead,
                    "rationale": entry.rationale,
                },
                separators=(",", ":"),
                sort_keys=True,
            )
        )
    return "".join(line + "\n" for line in lines)


def build_baseline(
    run: RunResults, *, previous: Baseline | None
) -> Baseline:
    """Rebuild a baseline from a run, carrying annotations forward.

    `bead` and `rationale` are the human half of a baseline row: they say which
    issue tracks the failure and why it is accepted. Regenerating wholesale
    would drop them, so surviving rows keep theirs and only genuinely new rows
    arrive blank — which is what makes the diff of an accepted regression one
    reviewable line.
    """
    carried = previous.by_key if previous else {}
    entries = []
    for result in sorted(run.results, key=_sort_key):
        if result.outcome is Outcome.PASS:
            continue
        known = carried.get((result.category, result.ordinal))
        entries.append(
            BaselineEntry(
                id=_identity(result.category, result.ordinal),
                suite=result.suite,
                category=result.category,
                ordinal=result.ordinal,
                description=result.description,
                outcome=result.outcome.value,
                bead=known.bead if known else None,
                rationale=known.rationale if known else None,
            )
        )
    return Baseline(
        total=len(run.results),
        suites=dict(Counter(result.suite for result in run.results)),
        entries=tuple(entries),
    )


# --- comparison --------------------------------------------------------------


def diff_run(run: RunResults, baseline: Baseline) -> Diff:
    known = baseline.by_key
    run_by_key = {
        (result.category, result.ordinal): result for result in run.results
    }

    regressions = tuple(
        result
        for result in sorted(run.results, key=_sort_key)
        if result.outcome in _REGRESSION_OUTCOMES
        and (result.category, result.ordinal) not in known
    )

    passed = {
        (result.category, result.ordinal)
        for result in run.results
        if result.outcome is Outcome.PASS
    }
    improvements = tuple(
        entry
        for entry in sorted(baseline.entries, key=_sort_key)
        if entry.key in passed
    )

    drift: list[str] = []
    if len(run.results) != baseline.total:
        drift.append(f"total: {baseline.total} -> {len(run.results)}")

    observed = Counter(result.suite for result in run.results)
    for suite in sorted(set(observed) | set(baseline.suites)):
        before = baseline.suites.get(suite, 0)
        after = observed.get(suite, 0)
        if before != after:
            drift.append(f"{suite}: {before} -> {after} subtests")

    for entry in sorted(baseline.entries, key=_sort_key):
        result = run_by_key.get(entry.key)
        if result is None:
            drift.append(f"{entry.id}: no longer reported by the suite")
        elif (
            result.outcome is not Outcome.PASS
            and result.outcome.value != entry.outcome
        ):
            # A PASS is already reported as an improvement; saying it twice
            # would double-count the same move.
            drift.append(
                f"{entry.id}: outcome {entry.outcome} -> {result.outcome.value}"
            )

    return Diff(
        regressions=regressions,
        improvements=improvements,
        drift=tuple(drift),
    )


def regressions_json(diff: Diff) -> list[dict]:
    """The payload a follow-up issue is filed from."""
    return [
        {
            "id": _identity(result.category, result.ordinal),
            "suite": result.suite,
            "category": result.category,
            "ordinal": result.ordinal,
            "description": result.description,
            "outcome": result.outcome.value,
        }
        for result in diff.regressions
    ]


def exit_code(diff: Diff, *, fail_on: str) -> int:
    if fail_on == "none":
        return 0
    if fail_on == "regression":
        return 1 if diff.regressions else 0
    if fail_on == "any":
        # Everything render_summary calls a FAIL. An improvement is not one,
        # so no policy can make it fail a run.
        return 1 if (diff.regressions or diff.drift) else 0
    raise ValueError(f"unknown --fail-on {fail_on!r}")


# --- rendering ---------------------------------------------------------------

#: Enough rows to act on without burying the headline when a whole suite moves.
_DETAIL_LIMIT = 50


def _bullets(lines: list[str]) -> list[str]:
    if len(lines) <= _DETAIL_LIMIT:
        return lines
    hidden = len(lines) - _DETAIL_LIMIT
    return lines[:_DETAIL_LIMIT] + [f"- ... and {hidden} more"]


def render_summary(diff: Diff, *, include_details: bool = True) -> str:
    """Render the Markdown judgment. Regressions lead: they are the only class
    that asks the reader to do something."""
    verdict = "FAIL" if (diff.regressions or diff.drift) else "OK"
    lines = [
        "# qtest-survey-diff",
        "",
        f"- **Regressions**: **{len(diff.regressions)}**",
        f"- Improvements: {len(diff.improvements)}",
        f"- Drift: {len(diff.drift)}",
        "",
    ]

    if diff.regressions and include_details:
        lines.extend(("## Regressions", ""))
        lines.extend(
            _bullets(
                [
                    f"- {_identity(r.category, r.ordinal)}"
                    f" ({r.description}) — {r.outcome.value}"
                    for r in diff.regressions
                ]
            )
        )
        lines.append("")

    if diff.improvements and include_details:
        lines.extend(
            (
                "## Improvements",
                "",
                "These rows pass now. Drop them from the baseline.",
                "",
            )
        )
        lines.extend(
            _bullets(
                [f"- {e.id} ({e.description})" for e in diff.improvements]
            )
        )
        lines.append("")

    if diff.drift and include_details:
        lines.extend(("## Drift", ""))
        lines.extend(_bullets([f"- {message}" for message in diff.drift]))
        lines.append("")

    lines.extend((f"**Verdict: {verdict}**", ""))
    return "\n".join(lines)


def render_annotations(diff: Diff) -> str:
    """One GitHub annotation per regression, so the class shows on the run
    without the reader opening the Job Summary."""
    lines = []
    for result in diff.regressions:
        message = (
            f"{_identity(result.category, result.ordinal)} "
            f"({result.description}) is {result.outcome.value}"
            " and is not in the baseline"
        )
        lines.append(
            f"::warning title=qtest regression::{message.replace(chr(10), ' ')}"
        )
    return "\n".join(lines)


# --- entry point -------------------------------------------------------------


def _write(path: Path | None, text: str) -> None:
    if path is not None:
        path.write_text(text, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path, help="qtest harness output log")
    parser.add_argument("xml", type=Path, help="qtest XML result file")
    parser.add_argument(
        "--baseline",
        type=Path,
        default=None,
        help="baseline JSONL to judge the run against",
    )
    parser.add_argument(
        "--write-baseline",
        type=Path,
        default=None,
        help="write a regenerated baseline, carrying annotations forward",
    )
    parser.add_argument("--summary", type=Path, default=None)
    parser.add_argument(
        "--step-summary",
        type=Path,
        default=None,
        help="append a headline without the per-row detail",
    )
    parser.add_argument(
        "--regressions-json",
        type=Path,
        default=None,
        help="write the regression list a follow-up issue is filed from",
    )
    parser.add_argument(
        "--annotations",
        type=Path,
        default=None,
        help="write GitHub workflow annotations (default: stdout under CI)",
    )
    parser.add_argument(
        "--github-output",
        type=Path,
        default=None,
        help="append regressions/improvements/drift counts for an action",
    )
    parser.add_argument("--fail-on", choices=FAIL_ON, default="none")
    args = parser.parse_args(argv)

    for path in (args.log, args.xml):
        if not path.is_file():
            print(f"diff-survey: not found: {path}", file=sys.stderr)
            return 2

    try:
        run = parse_run(args.log, args.xml)
        if not run.results:
            raise ResultError("no authoritative subtest results")
    except (ResultError, OSError, ValueError) as exc:
        print(f"diff-survey: {exc}", file=sys.stderr)
        return 1

    previous: Baseline | None = None
    if args.baseline is not None:
        try:
            previous = load_baseline(args.baseline)
        except BaselineError as exc:
            print(f"diff-survey: {exc}", file=sys.stderr)
            return 1

    if args.write_baseline is not None:
        _write(
            args.write_baseline,
            render_baseline(build_baseline(run, previous=previous)),
        )

    if previous is None:
        # Nothing to judge against: the caller only wanted the survey, or is
        # generating a first baseline.
        return 0

    diff = diff_run(run, previous)
    summary = render_summary(diff)
    sys.stdout.write(summary)
    _write(args.summary, summary)

    if args.step_summary is not None:
        headline = render_summary(diff, include_details=False)
        if args.step_summary.exists() and args.step_summary.stat().st_size > 0:
            headline = "\n" + headline
        with args.step_summary.open("a", encoding="utf-8") as stream:
            stream.write(headline)

    if args.regressions_json is not None:
        _write(
            args.regressions_json,
            json.dumps(regressions_json(diff), indent=2, sort_keys=True) + "\n",
        )

    annotations = render_annotations(diff)
    if args.annotations is not None:
        _write(args.annotations, annotations + "\n" if annotations else "")
    elif annotations:
        print(annotations)

    if args.github_output is not None:
        with args.github_output.open("a", encoding="utf-8") as stream:
            stream.write(f"regressions={len(diff.regressions)}\n")
            stream.write(f"improvements={len(diff.improvements)}\n")
            stream.write(f"drift={'true' if diff.drift else 'false'}\n")

    return exit_code(diff, fail_on=args.fail_on)


if __name__ == "__main__":
    sys.exit(main())
