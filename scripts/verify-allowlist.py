#!/usr/bin/env python3
"""verify-allowlist.py — judge a qtest-driver log against allowlist.txt.

Usage:
    scripts/verify-allowlist.py <qtest.log> <allowlist.txt> [--summary <path>]

Exit codes:
    0  No allowlist regression and no missing allowlisted entries.
    1  At least one allowlisted entry failed, or an allowlisted entry did
       not run, or the log could not be parsed.
    2  Argument / IO error.

The companion summary file (Markdown) is always written when --summary is
given, regardless of exit code, so CI can upload it as an artifact.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

# qtest-driver emits two distinct subtest-result line shapes:
#
#   PASS (columnar, padded with dots):
#     "arg-parsing  1 (required argument)                             ... PASSED"
#
#   FAIL (testlog dump header, no dots, literal " test " infix):
#     "deterministic-id test 1 (deterministic ID: ...) FAILED"
#
# Both are captured here. The subtest description uses a greedy capture
# constrained by requiring the trailing PASSED/FAILED suffix.
_RESULT_RE_PASS = re.compile(
    r"^(?P<test>[A-Za-z0-9][A-Za-z0-9_+.-]*)\s+"
    r"(?P<num>\d+)\s+"
    r"\((?P<name>.+)\)\s+"
    r"\.\.\.\s+"
    r"(?P<result>PASSED|FAILED)(?:\s+\(exp\))?\s*$"
)
_RESULT_RE_FAIL = re.compile(
    r"^(?P<test>[A-Za-z0-9][A-Za-z0-9_+.-]*)\s+test\s+"
    r"(?P<num>\d+)\s+"
    r"\((?P<name>.+)\)\s+"
    r"(?P<result>FAILED)\s*$"
)

# qtest-driver appends a "Total tests: N" summary line. We cross-check it
# against the count of subtest result lines we parsed, so a regression that
# silently drops result lines (e.g. log fd getting stolen by qtest-driver's
# own testlog write) surfaces as an explicit error rather than as an empty
# allowlist-candidates section.
_SUMMARY_TOTAL_RE = re.compile(r"^Total tests:\s*(?P<n>\d+)\s*$")


@dataclass(frozen=True)
class Result:
    test: str
    subtest: str
    passed: bool


def parse_log(path: Path) -> tuple[list[Result], int | None]:
    """Parse a qtest-driver log.

    Returns:
        (results, total_tests_summary). `total_tests_summary` is the
        qtest-driver "Total tests: N" line if present, else None. Callers
        should compare it against `len(results)` to detect parsing drift.
    """
    out: list[Result] = []
    seen: set[tuple[str, str, str]] = set()
    total_from_summary: int | None = None
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            stripped = line.rstrip("\n")
            m_total = _SUMMARY_TOTAL_RE.match(stripped)
            if m_total:
                total_from_summary = int(m_total["n"])
                continue
            m = _RESULT_RE_PASS.match(stripped) or _RESULT_RE_FAIL.match(stripped)
            if not m:
                continue
            key = (m["test"], m["num"], m["name"].strip())
            # qtest-driver emits per-subtest details to both a testlog dump
            # and a columnar status line; dedupe on (test, num, name).
            if key in seen:
                continue
            seen.add(key)
            out.append(
                Result(
                    test=m["test"],
                    subtest=m["name"].strip(),
                    passed=(m["result"] == "PASSED"),
                )
            )
    return out, total_from_summary


@dataclass(frozen=True)
class AllowlistEntry:
    test: str
    subtest: str | None  # None = whole-file entry

    def matches(self, r: Result) -> bool:
        if self.test != r.test:
            return False
        if self.subtest is None:
            return True
        return self.subtest == r.subtest


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
            out.append(f"{e.test}:{e.subtest}")
    return sorted(out)


def judge(
    results: list[Result], allowlist: list[AllowlistEntry]
) -> tuple[int, str]:
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
            if r.passed:
                expected_pass.append(r)
            else:
                regressions.append(r)

    for r in results:
        on_allowlist = any(e.matches(r) for e in allowlist)
        if on_allowlist:
            continue
        if r.passed:
            unexpected_pass.append(r)
        else:
            informational.append(r)

    exit_code = 0 if not regressions and not missing else 1

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
    if unexpected_pass:
        lines.append("## Allowlist candidates (consider adding)")
        for n in _fmt(unexpected_pass):
            lines.append(f"- {n}")
        lines.append("")

    verdict = "OK" if exit_code == 0 else "FAIL"
    lines.append(f"**Verdict: {verdict}**")
    lines.append("")

    return exit_code, "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("log", type=Path, help="qtest-driver output log")
    ap.add_argument("allowlist", type=Path, help="allowlist.txt")
    ap.add_argument(
        "--summary",
        type=Path,
        default=None,
        help="write Markdown summary to this path (always written)",
    )
    args = ap.parse_args(argv)

    if not args.log.is_file():
        print(f"verify-allowlist: log not found: {args.log}", file=sys.stderr)
        return 2
    if not args.allowlist.is_file():
        print(f"verify-allowlist: allowlist not found: {args.allowlist}", file=sys.stderr)
        return 2

    results, total_from_summary = parse_log(args.log)
    allowlist = parse_allowlist(args.allowlist)
    if not results:
        print("verify-allowlist: no subtest results parsed from log", file=sys.stderr)
        return 1

    drift_msg = None
    if total_from_summary is not None and total_from_summary != len(results):
        # Parser missed lines OR captured extras. This guards against the
        # "tee writes to an orphaned inode" class of bug — see harness.log
        # rationale in scripts/run.sh — and any future regex regression.
        drift_msg = (
            f"verify-allowlist: parsed {len(results)} subtest results "
            f"but qtest summary reported {total_from_summary}"
        )
        print(drift_msg, file=sys.stderr)

    exit_code, summary = judge(results, allowlist)
    if drift_msg:
        # Make the drift visible in the summary artifact too.
        summary = f"> ⚠️ {drift_msg}\n\n{summary}"
        exit_code = max(exit_code, 1)
    sys.stdout.write(summary)
    if args.summary:
        args.summary.write_text(summary, encoding="utf-8")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
