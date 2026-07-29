#!/usr/bin/env python3
"""Validate the explicit qtest parity manifest against authoritative results."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from qtest_results import Outcome, ResultError, RunResults, parse_run

STATES = frozenset(
    {"applicable", "excluded", "represented", "blocked", "passing", "failing"}
)
FIELDS = (
    "id",
    "suite",
    "category",
    "ordinal",
    "description",
    "state",
    "rationale",
    "owner",
    "bead",
    "replacement_ref",
)

_BEAD_RE = re.compile(r"^flpdf-[a-z0-9]+(?:\.[0-9]+)*$")
_RUST_TEST_RE = re.compile(r"^rust-test:[^:\s]+:[^:\s]+:[^:\s]+$")
_SCOPE_RE = re.compile(r"^scope:[^#\s]+#[^#\s]+$")


@dataclass(frozen=True)
class ManifestEntry:
    id: str
    suite: str
    category: str
    ordinal: int
    description: str
    state: str
    rationale: str | None
    owner: str | None
    bead: str | None
    replacement_ref: str | None


@dataclass(frozen=True)
class Validation:
    errors: tuple[str, ...]
    counts: dict[str, int]
    total: int


def load_manifest(path: Path) -> list[ManifestEntry]:
    entries: list[ManifestEntry] = []
    seen: set[str] = set()
    for lineno, raw in enumerate(
        path.read_text(encoding="utf-8").splitlines(), 1
    ):
        if not raw:
            raise ValueError(f"{path}:{lineno}: blank lines are not allowed")
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"{path}:{lineno}: invalid JSON: {exc.msg}"
            ) from exc
        if not isinstance(value, dict) or tuple(value) != FIELDS:
            raise ValueError(
                f"{path}:{lineno}: fields must be {', '.join(FIELDS)} in order"
            )
        entry = ManifestEntry(**value)
        if entry.id in seen:
            raise ValueError(f"{path}:{lineno}: duplicate id {entry.id!r}")
        seen.add(entry.id)
        entries.append(entry)
    return entries


def _as_entry(entry: ManifestEntry | dict[str, object]) -> ManifestEntry:
    if isinstance(entry, ManifestEntry):
        return entry
    return ManifestEntry(**entry)


def _present(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _valid_replacement_ref(reference: str) -> bool:
    if reference.startswith("bead:"):
        return _BEAD_RE.fullmatch(reference.removeprefix("bead:")) is not None
    return (
        _RUST_TEST_RE.fullmatch(reference) is not None
        or _SCOPE_RE.fullmatch(reference) is not None
    )


def validate_manifest(
    run: RunResults,
    entries: list[ManifestEntry | dict[str, object]],
) -> Validation:
    manifest = [_as_entry(entry) for entry in entries]
    errors: list[str] = []
    manifest_keys = [(entry.category, entry.ordinal) for entry in manifest]
    sorted_keys = sorted(manifest_keys)
    if manifest_keys != sorted_keys:
        errors.append(
            "manifest entries are not sorted by category and numeric ordinal"
        )

    duplicate_keys = [
        key for key, count in Counter(manifest_keys).items() if count > 1
    ]
    for category, ordinal in sorted(duplicate_keys):
        errors.append(
            f"duplicate manifest identity {f'{category} {ordinal}'!r}"
        )

    run_by_key = {
        (result.category, result.ordinal): result for result in run.results
    }
    manifest_key_set = set(manifest_keys)
    run_key_set = set(run_by_key)
    for category, ordinal in sorted(run_key_set - manifest_key_set):
        errors.append(
            f"missing manifest identity {f'{category} {ordinal}'!r}"
        )
    for category, ordinal in sorted(manifest_key_set - run_key_set):
        errors.append(
            f"extra manifest identity {f'{category} {ordinal}'!r}"
        )
    if len(manifest) != run.summary.total:
        errors.append(
            f"manifest total {len(manifest)} differs from "
            f"authoritative total {run.summary.total}"
        )

    for entry in manifest:
        identity = f"{entry.category} {entry.ordinal}"
        if entry.id != identity:
            errors.append(f"{entry.id}: id must be {identity!r}")

        if entry.state not in STATES:
            errors.append(f"{entry.id}: unknown state {entry.state!r}")
            continue

        required = {
            "failing": ("rationale", "owner", "bead"),
            "blocked": ("rationale", "owner", "bead"),
            "applicable": ("rationale", "owner", "bead"),
            "excluded": ("rationale", "replacement_ref"),
            "represented": ("rationale", "replacement_ref"),
        }.get(entry.state, ())
        for field in required:
            if not _present(getattr(entry, field)):
                errors.append(f"{entry.id}: {entry.state} entry requires {field}")

        if entry.bead is not None and (
            not isinstance(entry.bead, str)
            or _BEAD_RE.fullmatch(entry.bead) is None
        ):
            errors.append(f"{entry.id}: invalid bead {entry.bead!r}")

        reference = entry.replacement_ref
        if entry.state == "represented" and _present(reference):
            if _RUST_TEST_RE.fullmatch(reference) is None:
                errors.append(
                    f"{entry.id}: represented entry requires "
                    "rust-test replacement_ref"
                )
        elif reference is not None and (
            not isinstance(reference, str)
            or not _valid_replacement_ref(reference)
        ):
            errors.append(f"{entry.id}: invalid replacement_ref {reference!r}")

        result = run_by_key.get((entry.category, entry.ordinal))
        if result is None:
            continue
        if entry.suite != result.suite:
            errors.append(
                f"{identity}: suite drift: manifest {entry.suite!r}, "
                f"qtest {result.suite!r}"
            )
        if entry.description != result.description:
            errors.append(
                f"{identity}: description drift: manifest "
                f"{entry.description!r}, qtest {result.description!r}"
            )
        if entry.state == "passing" and result.outcome is not Outcome.PASS:
            errors.append(
                f"{identity}: passing entry has stale outcome "
                f"{result.outcome.value!r}"
            )
        if (
            entry.state in ("blocked", "failing")
            and result.outcome is Outcome.PASS
        ):
            errors.append(
                f"{identity}: {entry.state} entry has stale outcome "
                f"{result.outcome.value!r}"
            )

    return Validation(
        errors=tuple(errors),
        counts=dict(Counter(entry.state for entry in manifest)),
        total=run.summary.total,
    )


def render_summary(
    run: RunResults,
    validation: Validation,
    *,
    include_details: bool = True,
) -> str:
    lines = [
        "# qtest parity manifest",
        "",
        f"- Authoritative total: **{validation.total}**",
        f"- Ordinary passes: **{run.summary.passes}**",
        f"- Expected failures: **{run.summary.expected_failures}**",
        f"- Validation errors: **{len(validation.errors)}**",
        "",
        "## State counts",
        "",
    ]
    for state in sorted(STATES):
        lines.append(f"- {state}: **{validation.counts.get(state, 0)}**")
    lines.append("")

    if validation.errors and include_details:
        lines.extend(("## Validation errors", ""))
        lines.extend(f"- {error}" for error in validation.errors)
        lines.append("")

    verdict = "FAIL" if validation.errors else "OK"
    lines.extend((f"**Verdict: {verdict}**", ""))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path, help="qtest harness output log")
    parser.add_argument("xml", type=Path, help="qtest XML result file")
    parser.add_argument("manifest", type=Path, help="explicit parity JSONL manifest")
    parser.add_argument(
        "--summary",
        type=Path,
        default=None,
        help="write the full validation summary after successful parsing",
    )
    parser.add_argument(
        "--step-summary",
        type=Path,
        default=None,
        help="append a bounded validation headline without identity lists",
    )
    args = parser.parse_args(argv)

    try:
        run = parse_run(args.log, args.xml)
        if not run.results:
            raise ResultError("no authoritative subtest results")
        entries = load_manifest(args.manifest)
    except (ResultError, OSError, ValueError) as exc:
        print(f"verify-parity-manifest: {exc}", file=sys.stderr)
        return 1

    validation = validate_manifest(run, entries)
    summary = render_summary(run, validation)
    sys.stdout.write(summary)
    if args.summary:
        args.summary.write_text(summary, encoding="utf-8")
    if args.step_summary:
        headline = render_summary(run, validation, include_details=False)
        with args.step_summary.open("a", encoding="utf-8") as stream:
            stream.write(headline)
    return 1 if validation.errors else 0


if __name__ == "__main__":
    sys.exit(main())
