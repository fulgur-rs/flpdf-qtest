#!/usr/bin/env python3
"""verify-allowlist.py — judge authoritative qtest results against allowlist.txt.

Usage:
    scripts/verify-allowlist.py <qtest.log> <qtest.xml> <allowlist.txt> [--summary <path>]

Exit codes:
    0  Run completed. Policy failures (allowlisted entries that regressed
       or did not run) are captured in the summary's ``**Verdict: FAIL**``
       line and the --metrics JSON record, but they DO NOT gate the exit
       code — they are surfaced instead as a data point on the nightly
       trend chart. This keeps flpdf-side regressions from blocking the
       chart's update loop.
    1  Real error: qtest result artifacts could not be parsed or reconciled.
    2  Argument / IO error.

The companion summary and metrics outputs are written only after the paired
artifacts have been reconciled to a non-empty authoritative result set.
Operational errors are diagnosed on stderr and leave those outputs unwritten;
the runner clears stale generated artifacts before invoking this command.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from qtest_results import Outcome, Result, ResultError, parse_run


@dataclass(frozen=True)
class AllowlistEntry:
    test: str
    subtest: str | None  # None = whole-file entry

    def matches(self, r: Result) -> bool:
        if self.test != r.category:
            return False
        if self.subtest is None:
            return True
        return self.subtest == r.description


def parse_allowlist(path: Path) -> list[AllowlistEntry]:
    entries: list[AllowlistEntry] = []
    with path.open("r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            if ":" in line:
                test, _, sub = line.partition(":")
                entries.append(AllowlistEntry(test=test.strip(), subtest=sub.strip()))
            else:
                entries.append(AllowlistEntry(test=line, subtest=None))
    return entries


def _fmt(entries: Iterable[AllowlistEntry | Result]) -> list[str]:
    out: list[str] = []
    for e in entries:
        if isinstance(e, AllowlistEntry):
            out.append(e.test if e.subtest is None else f"{e.test}:{e.subtest}")
        else:
            out.append(f"{e.category}:{e.description}")
    return sorted(out)


@dataclass(frozen=True)
class Buckets:
    """The five outcome buckets of a run, judged against the allowlist."""

    expected_pass: list[Result]
    regressions: list[Result]
    missing: list[AllowlistEntry]
    unexpected_pass: list[Result]
    informational: list[Result]


def _bucket(results: list[Result], allowlist: list[AllowlistEntry]) -> Buckets:
    """Partition results into the five outcome buckets. Shared by judge()
    (Markdown rendering) and build_metrics() (time-series record)."""
    regressions: list[Result] = []
    missing: list[AllowlistEntry] = []
    unexpected_pass: list[Result] = []
    informational: list[Result] = []
    expected_pass: list[Result] = []

    for entry in allowlist:
        matched = [r for r in results if entry.matches(r)]
        if not matched:
            missing.append(entry)
            continue
        for r in matched:
            if r.outcome in (Outcome.PASS, Outcome.UNEXPECTED_PASS):
                expected_pass.append(r)
            else:
                regressions.append(r)

    for r in results:
        on_allowlist = any(e.matches(r) for e in allowlist)
        if on_allowlist:
            continue
        if r.outcome in (Outcome.PASS, Outcome.UNEXPECTED_PASS):
            unexpected_pass.append(r)
        else:
            informational.append(r)

    return Buckets(
        expected_pass=expected_pass,
        regressions=regressions,
        missing=missing,
        unexpected_pass=unexpected_pass,
        informational=informational,
    )


def build_metrics(
    results: list[Result],
    allowlist: list[AllowlistEntry],
    *,
    commit: str,
    timestamp: str,
) -> dict:
    """Build one time-series record for a run. Counts mirror the summary;
    ``commit`` (the flpdf SHA under test) and ``timestamp`` are supplied by
    the caller so this stays deterministic and testable."""
    b = _bucket(results, allowlist)
    regressions = len(b.regressions)
    missing = len(b.missing)
    return {
        "timestamp": timestamp,
        "flpdf_commit": commit,
        "total": len(results),
        "allowlist": len(allowlist),
        "expected_pass": len(b.expected_pass),
        "regressions": regressions,
        "missing": missing,
        "candidates": len(b.unexpected_pass),
        "informational": len(b.informational),
        "drift": False,
        "verdict": "OK" if not regressions and not missing else "FAIL",
    }


def judge(
    results: list[Result],
    allowlist: list[AllowlistEntry],
    *,
    include_candidates: bool = True,
) -> tuple[int, str]:
    """Judge results against the allowlist and render a Markdown summary.

    When ``include_candidates`` is False the long enumerated
    ``## Allowlist candidates`` block is omitted — used for the GitHub Job
    Summary headline, which keeps the counts, regressions, missing entries,
    and verdict but drops the (potentially thousands-long) candidate list.
    The candidate *count* line is unaffected.

    """
    b = _bucket(results, allowlist)
    expected_pass = b.expected_pass
    regressions = b.regressions
    missing = b.missing
    unexpected_pass = b.unexpected_pass
    informational = b.informational

    # Verdict is data, not a CI gate: regressions, missing allowlisted
    # entries flip verdict to FAIL in the summary and the --metrics record,
    # but the exit code stays 0 so the nightly still uploads its trend record.
    # Result-artifact parse errors are handled in main() and produce exit != 0.
    verdict = "OK" if not regressions and not missing else "FAIL"
    exit_code = 0

    lines: list[str] = []
    lines.append("# qtest-summary")
    lines.append("")
    lines.append(f"- Total subtests parsed: **{len(results)}**")
    lines.append(f"- Allowlisted entries: **{len(allowlist)}**")
    lines.append(f"- Expected pass (allowlist PASS): **{len(expected_pass)}**")
    lines.append(f"- **Regressions (allowlist FAIL)**: **{len(regressions)}**")
    lines.append(f"- **Missing (allowlist not run)**: **{len(missing)}**")
    lines.append(
        f"- Allowlist-candidates (non-allowlist PASS): **{len(unexpected_pass)}**"
    )
    lines.append(f"- Informational fails (non-allowlist FAIL): **{len(informational)}**")
    lines.append("")

    if regressions:
        lines.append("## Regressions (must fix or remove from allowlist)")
        for n in _fmt(regressions):
            lines.append(f"- {n}")
        lines.append("")
    if missing:
        lines.append("## Missing allowlisted entries (typo or upstream rename?)")
        for n in _fmt(missing):
            lines.append(f"- {n}")
        lines.append("")
    if unexpected_pass and include_candidates:
        lines.append("## Allowlist candidates (consider adding)")
        for n in _fmt(unexpected_pass):
            lines.append(f"- {n}")
        lines.append("")

    lines.append(f"**Verdict: {verdict}**")
    lines.append("")

    return exit_code, "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("log", type=Path, help="qtest-driver output log")
    ap.add_argument("xml", type=Path, help="qtest-driver XML result file")
    ap.add_argument("allowlist", type=Path, help="allowlist.txt")
    ap.add_argument(
        "--summary",
        type=Path,
        default=None,
        help="write the full Markdown summary to this path (always written)",
    )
    ap.add_argument(
        "--step-summary",
        type=Path,
        default=None,
        help=(
            "append a headline summary (counts + regressions + missing + "
            "verdict, without the long candidate list) to this path. Intended "
            "for a CI step summary such as $GITHUB_STEP_SUMMARY; appended, not "
            "overwritten."
        ),
    )
    ap.add_argument(
        "--metrics",
        type=Path,
        default=None,
        help=(
            "append one JSON time-series record (counts + verdict + metadata) "
            "to this path, for nightly trend collection. Appended, not "
            "overwritten."
        ),
    )
    ap.add_argument(
        "--commit",
        default="",
        help="flpdf commit SHA under test, recorded in the --metrics line",
    )
    ap.add_argument(
        "--timestamp",
        default="",
        help="ISO-8601 run timestamp recorded in the --metrics line",
    )
    args = ap.parse_args(argv)

    if not args.log.is_file():
        print(f"verify-allowlist: log not found: {args.log}", file=sys.stderr)
        return 2
    if not args.xml.is_file():
        print(f"verify-allowlist: XML not found: {args.xml}", file=sys.stderr)
        return 2
    if not args.allowlist.is_file():
        print(f"verify-allowlist: allowlist not found: {args.allowlist}", file=sys.stderr)
        return 2

    try:
        run = parse_run(args.log, args.xml)
        if not run.results:
            raise ResultError("no authoritative subtest results")
    except ResultError as exc:
        print(f"verify-allowlist: {exc}", file=sys.stderr)
        return 1
    results = list(run.results)
    allowlist = parse_allowlist(args.allowlist)
    exit_code, summary = judge(results, allowlist)
    sys.stdout.write(summary)
    if args.summary:
        args.summary.write_text(summary, encoding="utf-8")
    if args.step_summary:
        # Headline only: same counts/regressions/missing/verdict but without
        # the long candidate enumeration. Appended (GitHub convention) so
        # other steps' summaries are preserved.
        _, headline = judge(results, allowlist, include_candidates=False)
        # If a prior step left content without a trailing newline, separate it
        # so our `# qtest-summary` header starts on its own line.
        if args.step_summary.exists() and args.step_summary.stat().st_size > 0:
            headline = "\n" + headline
        with args.step_summary.open("a", encoding="utf-8") as fh:
            fh.write(headline)
    if args.metrics:
        # One JSON record per run, appended for nightly trend collection.
        record = build_metrics(
            results,
            allowlist,
            commit=args.commit,
            timestamp=args.timestamp,
        )
        with args.metrics.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, sort_keys=True) + "\n")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
