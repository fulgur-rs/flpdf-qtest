"""Unit tests for scripts/verify-parity-manifest.py.

Run with: python3 -m unittest scripts/tests/test_verify_parity_manifest.py
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import runpy
import sys
import tempfile
import unittest
from collections import Counter, defaultdict
from pathlib import Path
from unittest import mock
from xml.sax.saxutils import escape

_HERE = Path(__file__).resolve().parent
_QTEST_RESULTS_PATH = _HERE.parent / "qtest_results.py"
_VERIFY_PATH = _HERE.parent / "verify-parity-manifest.py"

_qtest_spec = importlib.util.spec_from_file_location(
    "qtest_results", _QTEST_RESULTS_PATH
)
assert _qtest_spec and _qtest_spec.loader, f"cannot load {_QTEST_RESULTS_PATH}"
qtest_results = importlib.util.module_from_spec(_qtest_spec)
sys.modules["qtest_results"] = qtest_results
_qtest_spec.loader.exec_module(qtest_results)

_verify_spec = importlib.util.spec_from_file_location(
    "verify_parity_manifest", _VERIFY_PATH
)
assert _verify_spec and _verify_spec.loader, f"cannot load {_VERIFY_PATH}"
verify_manifest = importlib.util.module_from_spec(_verify_spec)
sys.modules["verify_parity_manifest"] = verify_manifest
_verify_spec.loader.exec_module(verify_manifest)


def _tmp(content: str, *, suffix: str = ".jsonl") -> Path:
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=suffix, delete=False, encoding="utf-8"
    ) as f:
        f.write(content)
        return Path(f.name)


def _result(
    category: str,
    ordinal: int,
    description: str,
    outcome: qtest_results.Outcome,
    *,
    suite: str | None = None,
) -> qtest_results.Result:
    return qtest_results.Result(
        suite=suite or category,
        category=category,
        ordinal=ordinal,
        description=description,
        outcome=outcome,
    )


Outcome = qtest_results.Outcome


def _run(results: list[qtest_results.Result]) -> qtest_results.RunResults:
    counts = Counter(r.outcome for r in results)
    return qtest_results.RunResults(
        results=tuple(results),
        summary=qtest_results.Summary(
            total=len(results),
            passes=counts[Outcome.PASS],
            failures=counts[Outcome.FAIL],
            unexpected_passes=counts[Outcome.UNEXPECTED_PASS],
            expected_failures=counts[Outcome.EXPECTED_FAIL],
        ),
        invalid_suites=(),
    )


def _entry(**overrides):
    value = {
        "id": "arg-parsing 1",
        "suite": "arg-parsing",
        "category": "arg-parsing",
        "ordinal": 1,
        "description": "required argument",
        "state": "passing",
        "rationale": None,
        "owner": None,
        "bead": None,
        "replacement_ref": None,
    }
    value.update(overrides)
    return value


def _manifest_entry(**overrides):
    return verify_manifest.ManifestEntry(**_entry(**overrides))


def _manifest_text(entries) -> str:
    return "".join(
        json.dumps(entry, separators=(",", ":")) + "\n" for entry in entries
    )


def _paired_artifacts(
    results: list[qtest_results.Result],
) -> tuple[Path, Path]:
    suites: dict[str, list[qtest_results.Result]] = defaultdict(list)
    for result in results:
        suites[result.suite].append(result)

    def summary(cases: list[qtest_results.Result]) -> str:
        counts = Counter(case.outcome for case in cases)
        return (
            f'<testsummary total-cases="{len(cases)}" '
            f'passes="{counts[Outcome.PASS]}" '
            f'failures="{counts[Outcome.FAIL]}" '
            f'unexpected-passes="{counts[Outcome.UNEXPECTED_PASS]}" '
            f'expected-failures="{counts[Outcome.EXPECTED_FAIL]}" '
            'missing-cases="0" extra-cases="0"/>'
        )

    xml_suites = []
    for suite, cases in suites.items():
        xml_cases = "\n".join(
            f'<testcase testid="{escape(case.id)}" '
            f'description="{escape(case.description)}" '
            f'outcome="{"pass" if case.passed else "fail"}"/>'
            for case in cases
        )
        xml_suites.append(
            f'<testsuite file="/repo/{escape(suite)}.test">\n'
            f"{xml_cases}\n{summary(cases)}\n</testsuite>"
        )
    xml = _tmp(
        '<?xml version="1.0"?>\n'
        '<qtest-results version="1">\n'
        f"{'\n'.join(xml_suites)}\n"
        f"{summary(results)}\n"
        "</qtest-results>\n",
        suffix=".xml",
    )
    statuses = {
        Outcome.PASS: "PASSED",
        Outcome.FAIL: "FAILED",
        Outcome.UNEXPECTED_PASS: "PASSED-UNEXP",
        Outcome.EXPECTED_FAIL: "FAILED (exp)",
    }
    log = _tmp(
        "".join(
            f"{case.category} {case.ordinal} ({case.description}) ... "
            f"{statuses[case.outcome]}\n"
            for case in results
        ),
        suffix=".log",
    )
    return log, xml


class LoadManifestTest(unittest.TestCase):
    def test_loads_exact_ordered_schema_in_file_order(self) -> None:
        first = _entry()
        second = _entry(
            id="arg-parsing 2",
            ordinal=2,
            description="second argument",
        )
        path = _tmp(
            json.dumps(first, separators=(",", ":"))
            + "\n"
            + json.dumps(second, separators=(",", ":"))
            + "\n"
        )

        entries = verify_manifest.load_manifest(path)

        self.assertEqual(entries, [_manifest_entry(), _manifest_entry(**second)])

    def test_rejects_blank_physical_line(self) -> None:
        path = _tmp(json.dumps(_entry()) + "\n\n")

        with self.assertRaisesRegex(
            ValueError, rf"{path}:2: blank lines are not allowed"
        ):
            verify_manifest.load_manifest(path)

    def test_rejects_invalid_json_with_physical_line(self) -> None:
        path = _tmp("{not-json}\n")

        with self.assertRaisesRegex(ValueError, rf"{path}:1: invalid JSON"):
            verify_manifest.load_manifest(path)

    def test_rejects_missing_unknown_or_misordered_fields(self) -> None:
        cases = []
        missing = _entry()
        del missing["owner"]
        cases.append(missing)
        unknown = _entry()
        unknown["unexpected"] = None
        cases.append(unknown)
        misordered = {"suite": "arg-parsing", **_entry()}
        cases.append(misordered)

        for value in cases:
            with self.subTest(fields=tuple(value)):
                path = _tmp(json.dumps(value) + "\n")
                with self.assertRaisesRegex(
                    ValueError, rf"{path}:1: fields must be id, suite"
                ):
                    verify_manifest.load_manifest(path)

    def test_rejects_duplicate_id_with_physical_line(self) -> None:
        line = json.dumps(_entry())
        path = _tmp(f"{line}\n{line}\n")

        with self.assertRaisesRegex(
            ValueError, rf"{path}:2: duplicate id 'arg-parsing 1'"
        ):
            verify_manifest.load_manifest(path)

    def test_rejects_duplicate_json_key_with_physical_line(self) -> None:
        path = _tmp(
            '{"id":"first","id":"arg-parsing 1",'
            '"suite":"arg-parsing","category":"arg-parsing","ordinal":1,'
            '"description":"required argument","state":"passing",'
            '"rationale":null,"owner":null,"bead":null,'
            '"replacement_ref":null}\n'
        )

        with self.assertRaisesRegex(
            ValueError, rf"{path}:1: duplicate JSON key 'id'"
        ):
            verify_manifest.load_manifest(path)

    def test_rejects_nonstring_required_fields(self) -> None:
        for field in ("id", "suite", "category", "description", "state"):
            with self.subTest(field=field):
                path = _tmp(json.dumps(_entry(**{field: False})) + "\n")
                with self.assertRaisesRegex(
                    ValueError,
                    rf"{path}:1: field {field!r} must be a string",
                ):
                    verify_manifest.load_manifest(path)

    def test_rejects_bool_float_and_nonpositive_ordinals(self) -> None:
        for ordinal in (True, 1.5, 0, -1):
            with self.subTest(ordinal=ordinal):
                path = _tmp(json.dumps(_entry(ordinal=ordinal)) + "\n")
                with self.assertRaisesRegex(
                    ValueError,
                    rf"{path}:1: field 'ordinal' must be a positive integer",
                ):
                    verify_manifest.load_manifest(path)

    def test_rejects_nonstring_nullable_fields(self) -> None:
        for field in ("rationale", "owner", "bead", "replacement_ref"):
            with self.subTest(field=field):
                path = _tmp(json.dumps(_entry(**{field: 1})) + "\n")
                with self.assertRaisesRegex(
                    ValueError,
                    rf"{path}:1: field {field!r} must be a string or null",
                ):
                    verify_manifest.load_manifest(path)

    def test_rejects_unhashable_category_before_validation(self) -> None:
        path = _tmp(json.dumps(_entry(category=[])) + "\n")

        with self.assertRaisesRegex(
            ValueError, rf"{path}:1: field 'category' must be a string"
        ):
            verify_manifest.load_manifest(path)


class StateContractTest(unittest.TestCase):
    def _validate(self, entry, outcome=Outcome.FAIL):
        run = _run(
            [_result("arg-parsing", 1, "required argument", outcome)]
        )
        return verify_manifest.validate_manifest(run, [entry])

    def test_accepts_passing_entry_for_ordinary_pass(self) -> None:
        validation = self._validate(_entry(), Outcome.PASS)

        self.assertEqual(validation.errors, ())

    def test_accepts_failing_entry_with_owned_rationale(self) -> None:
        entry = _entry(
            state="failing",
            rationale="output differs",
            owner="PDF parity",
            bead="flpdf-25kg.3",
        )

        self.assertEqual(self._validate(entry).errors, ())

    def test_accepts_blocked_entry_with_owned_rationale(self) -> None:
        entry = _entry(
            state="blocked",
            rationale="CLI option is absent",
            owner="CLI parity",
            bead="flpdf-25kg.4.2",
        )

        self.assertEqual(self._validate(entry).errors, ())

    def test_accepts_applicable_entry_with_owned_rationale(self) -> None:
        entry = _entry(
            state="applicable",
            rationale="failure boundary is not yet distinguished",
            owner="Parity inventory",
            bead="flpdf-25kg",
        )

        self.assertEqual(
            self._validate(entry, Outcome.EXPECTED_FAIL).errors, ()
        )

    def test_accepts_excluded_entry_with_typed_reference(self) -> None:
        entry = _entry(
            state="excluded",
            rationale="C ABI is outside the Rust boundary",
            replacement_ref="bead:flpdf-25kg.2.1",
        )

        self.assertEqual(self._validate(entry).errors, ())

    def test_accepts_represented_entry_with_rust_test_reference(self) -> None:
        entry = _entry(
            state="represented",
            rationale="portable behavior has a Rust oracle",
            replacement_ref=(
                "rust-test:flpdf:reader_tests:opens_minimal_pdf"
            ),
        )

        self.assertEqual(self._validate(entry).errors, ())

    def test_requires_owner_and_bead_for_blocked(self) -> None:
        entry = _entry(
            state="blocked",
            rationale="CLI option is absent",
            owner=None,
            bead=None,
        )

        validation = self._validate(entry)

        self.assertIn("blocked entry requires owner", validation.errors[0])
        self.assertTrue(
            any("blocked entry requires bead" in error for error in validation.errors)
        )

    def test_requires_nonempty_state_specific_fields(self) -> None:
        cases = (
            ("failing", {"rationale": "", "owner": "owner", "bead": "flpdf-x"}),
            ("applicable", {"rationale": "why", "owner": "", "bead": "flpdf-x"}),
            ("excluded", {"rationale": "why", "replacement_ref": ""}),
            ("represented", {"rationale": "", "replacement_ref": "rust-test:a:b:c"}),
        )
        for state, fields in cases:
            with self.subTest(state=state):
                validation = self._validate(_entry(state=state, **fields))
                self.assertTrue(validation.errors)

    def test_rejects_every_state_forbidden_field(self) -> None:
        valid = {
            "passing": _entry(),
            "failing": _entry(
                state="failing",
                rationale="output differs",
                owner="PDF parity",
                bead="flpdf-25kg.3",
            ),
            "blocked": _entry(
                state="blocked",
                rationale="CLI option is absent",
                owner="CLI parity",
                bead="flpdf-25kg.4",
            ),
            "applicable": _entry(
                state="applicable",
                rationale="boundary is not yet distinguished",
                owner="Parity inventory",
                bead="flpdf-25kg",
            ),
            "excluded": _entry(
                state="excluded",
                rationale="outside Rust parity",
                replacement_ref="scope:docs/scope.md#abi",
            ),
            "represented": _entry(
                state="represented",
                rationale="covered by a Rust oracle",
                replacement_ref="rust-test:flpdf:reader_tests:oracle",
            ),
        }
        forbidden = {
            "passing": ("rationale", "owner", "bead", "replacement_ref"),
            "failing": ("replacement_ref",),
            "blocked": ("replacement_ref",),
            "applicable": ("replacement_ref",),
            "excluded": ("owner", "bead"),
            "represented": ("owner", "bead"),
        }
        values = {
            "rationale": "stale rationale",
            "owner": "stale owner",
            "bead": "flpdf-stale",
            "replacement_ref": "scope:docs/stale.md#reference",
        }

        for state, fields in forbidden.items():
            for field in fields:
                for value in (values[field], ""):
                    with self.subTest(state=state, field=field, value=value):
                        entry = {**valid[state], field: value}
                        outcome = (
                            Outcome.PASS
                            if state == "passing"
                            else Outcome.FAIL
                        )
                        validation = self._validate(entry, outcome)
                        self.assertIn(
                            f"arg-parsing 1: {state} entry forbids {field}",
                            validation.errors,
                        )

    def test_promotion_to_passing_rejects_all_stale_metadata(self) -> None:
        entry = _entry(
            rationale="old failure rationale",
            owner="old owner",
            bead="flpdf-25kg.3",
            replacement_ref="scope:docs/old.md#classification",
        )

        validation = self._validate(entry, Outcome.PASS)

        self.assertEqual(
            validation.errors,
            (
                "arg-parsing 1: passing entry forbids rationale",
                "arg-parsing 1: passing entry forbids owner",
                "arg-parsing 1: passing entry forbids bead",
                "arg-parsing 1: passing entry forbids replacement_ref",
            ),
        )

    def test_rejects_unknown_state(self) -> None:
        validation = self._validate(_entry(state="unknown"))

        self.assertIn("unknown state 'unknown'", validation.errors[0])

    def test_rejects_invalid_bead_identifier(self) -> None:
        entry = _entry(
            state="failing",
            rationale="output differs",
            owner="PDF parity",
            bead="flpdf-UPPER",
        )

        validation = self._validate(entry)

        self.assertTrue(
            any("invalid bead 'flpdf-UPPER'" in error for error in validation.errors)
        )

    def test_accepts_every_excluded_reference_form(self) -> None:
        references = (
            "bead:flpdf-25kg.2.1",
            "rust-test:flpdf:reader_tests:opens_minimal_pdf",
            (
                "scope:docs/superpowers/specs/"
                "2026-07-29-qpdf-observable-parity-roadmap-design.md"
                "#api-boundary"
            ),
        )
        for reference in references:
            with self.subTest(reference=reference):
                entry = _entry(
                    state="excluded",
                    rationale="outside the direct qtest boundary",
                    replacement_ref=reference,
                )
                self.assertEqual(self._validate(entry).errors, ())

    def test_rejects_malformed_excluded_reference(self) -> None:
        entry = _entry(
            state="excluded",
            rationale="outside the direct qtest boundary",
            replacement_ref="issue:flpdf-25kg.2.1",
        )

        validation = self._validate(entry)

        self.assertTrue(
            any("invalid replacement_ref" in error for error in validation.errors)
        )

    def test_represented_allows_only_rust_test_reference(self) -> None:
        entry = _entry(
            state="represented",
            rationale="portable behavior is tracked",
            replacement_ref="bead:flpdf-25kg.2.1",
        )

        validation = self._validate(entry)

        self.assertTrue(
            any(
                "represented entry requires rust-test replacement_ref" in error
                for error in validation.errors
            )
        )


class IdentityAndOutcomeTest(unittest.TestCase):
    def test_rejects_lexically_sorted_but_numerically_unsorted_ordinals(
        self,
    ) -> None:
        run = _run(
            [
                _result("cases", 2, "second", Outcome.PASS),
                _result("cases", 10, "tenth", Outcome.PASS),
            ]
        )
        entries = [
            _entry(
                id="cases 10",
                suite="cases",
                category="cases",
                ordinal=10,
                description="tenth",
            ),
            _entry(
                id="cases 2",
                suite="cases",
                category="cases",
                ordinal=2,
                description="second",
            ),
        ]

        validation = verify_manifest.validate_manifest(run, entries)

        self.assertTrue(
            any(
                "manifest entries are not sorted" in error
                for error in validation.errors
            )
        )

    def test_accepts_numeric_ordinal_order_with_two_before_ten(self) -> None:
        run = _run(
            [
                _result("cases", 2, "second", Outcome.PASS),
                _result("cases", 10, "tenth", Outcome.PASS),
            ]
        )
        entries = [
            _entry(
                id="cases 2",
                suite="cases",
                category="cases",
                ordinal=2,
                description="second",
            ),
            _entry(
                id="cases 10",
                suite="cases",
                category="cases",
                ordinal=10,
                description="tenth",
            ),
        ]

        self.assertEqual(
            verify_manifest.validate_manifest(run, entries).errors, ()
        )

    def test_rejects_noncanonical_category_ordinal_order(self) -> None:
        run = _run(
            [
                _result("alpha", 2, "second", Outcome.PASS),
                _result("beta", 1, "beta", Outcome.PASS),
            ]
        )
        entries = [
            _entry(
                id="beta 1",
                suite="beta",
                category="beta",
                ordinal=1,
                description="beta",
            ),
            _entry(
                id="alpha 2",
                suite="alpha",
                category="alpha",
                ordinal=2,
                description="second",
            ),
        ]

        validation = verify_manifest.validate_manifest(run, entries)

        self.assertTrue(
            any(
                "manifest entries are not sorted" in error
                for error in validation.errors
            )
        )

    def test_rejects_duplicate_canonical_identity(self) -> None:
        run = _run([_result("arg-parsing", 1, "required argument", Outcome.PASS)])
        entries = [
            _entry(),
            _entry(id="alias 1"),
        ]

        validation = verify_manifest.validate_manifest(run, entries)

        self.assertTrue(
            any(
                "duplicate manifest identity 'arg-parsing 1'" in error
                for error in validation.errors
            )
        )

    def test_rejects_missing_manifest_identity(self) -> None:
        run = _run(
            [
                _result("arg-parsing", 1, "required argument", Outcome.PASS),
                _result("arg-parsing", 2, "optional argument", Outcome.PASS),
            ]
        )

        validation = verify_manifest.validate_manifest(run, [_entry()])

        self.assertTrue(
            any(
                "missing manifest identity 'arg-parsing 2'" in error
                for error in validation.errors
            )
        )

    def test_rejects_extra_manifest_identity(self) -> None:
        run = _run([_result("arg-parsing", 1, "required argument", Outcome.PASS)])
        extra = _entry(
            id="other 1",
            suite="other",
            category="other",
            description="extra",
        )

        validation = verify_manifest.validate_manifest(run, [_entry(), extra])

        self.assertTrue(
            any(
                "extra manifest identity 'other 1'" in error
                for error in validation.errors
            )
        )

    def test_rejects_id_category_ordinal_mismatch(self) -> None:
        run = _run([_result("arg-parsing", 1, "required argument", Outcome.PASS)])

        validation = verify_manifest.validate_manifest(
            run, [_entry(id="different 9")]
        )

        self.assertTrue(
            any(
                "different 9: id must be 'arg-parsing 1'" in error
                for error in validation.errors
            )
        )
        self.assertFalse(
            any("missing manifest identity" in error for error in validation.errors)
        )

    def test_rejects_suite_metadata_drift(self) -> None:
        run = _run(
            [
                _result(
                    "arg-parsing",
                    1,
                    "required argument",
                    Outcome.PASS,
                    suite="command-line",
                )
            ]
        )

        validation = verify_manifest.validate_manifest(run, [_entry()])

        self.assertTrue(
            any(
                "arg-parsing 1: suite drift: manifest 'arg-parsing', "
                "qtest 'command-line'" in error
                for error in validation.errors
            )
        )

    def test_rejects_description_drift(self) -> None:
        run = _run([_result("arg-parsing", 1, "renamed", Outcome.PASS)])

        validation = verify_manifest.validate_manifest(run, [_entry()])

        self.assertTrue(
            any(
                "arg-parsing 1: description drift" in error
                for error in validation.errors
            )
        )

    def test_passing_rejects_every_nonordinary_pass_outcome(self) -> None:
        for outcome in (
            Outcome.FAIL,
            Outcome.UNEXPECTED_PASS,
            Outcome.EXPECTED_FAIL,
        ):
            with self.subTest(outcome=outcome):
                run = _run(
                    [
                        _result(
                            "arg-parsing",
                            1,
                            "required argument",
                            outcome,
                        )
                    ]
                )

                validation = verify_manifest.validate_manifest(run, [_entry()])

                self.assertTrue(
                    any(
                        "passing entry has stale outcome" in error
                        for error in validation.errors
                    )
                )

    def test_blocked_rejects_ordinary_pass(self) -> None:
        run = _run(
            [_result("arg-parsing", 1, "required argument", Outcome.PASS)]
        )
        entry = _entry(
            state="blocked",
            rationale="CLI option is absent",
            owner="CLI parity",
            bead="flpdf-25kg.4",
        )

        validation = verify_manifest.validate_manifest(run, [entry])

        self.assertTrue(
            any(
                "blocked entry has stale outcome 'pass'" in error
                for error in validation.errors
            )
        )

    def test_failing_rejects_ordinary_pass(self) -> None:
        run = _run(
            [_result("arg-parsing", 1, "required argument", Outcome.PASS)]
        )
        entry = _entry(
            state="failing",
            rationale="output differs",
            owner="PDF parity",
            bead="flpdf-25kg.3",
        )

        validation = verify_manifest.validate_manifest(run, [entry])

        self.assertTrue(
            any(
                "failing entry has stale outcome 'pass'" in error
                for error in validation.errors
            )
        )

    def test_excluded_accepts_outcome_change(self) -> None:
        run = _run(
            [_result("arg-parsing", 1, "required argument", Outcome.PASS)]
        )
        entry = _entry(
            state="excluded",
            rationale="outside the Rust boundary",
            replacement_ref="scope:docs/scope.md#abi",
        )

        self.assertEqual(
            verify_manifest.validate_manifest(run, [entry]).errors, ()
        )

    def test_represented_accepts_outcome_change(self) -> None:
        run = _run(
            [
                _result(
                    "arg-parsing",
                    1,
                    "required argument",
                    Outcome.UNEXPECTED_PASS,
                )
            ]
        )
        entry = _entry(
            state="represented",
            rationale="covered by a Rust oracle",
            replacement_ref="rust-test:flpdf:reader_tests:oracle",
        )

        self.assertEqual(
            verify_manifest.validate_manifest(run, [entry]).errors, ()
        )

    def test_state_counts_sum_to_authoritative_total(self) -> None:
        results = [
            _result("cases", ordinal, f"case {ordinal}", outcome)
            for ordinal, outcome in enumerate(
                (
                    Outcome.EXPECTED_FAIL,
                    Outcome.FAIL,
                    Outcome.FAIL,
                    Outcome.FAIL,
                    Outcome.PASS,
                    Outcome.FAIL,
                ),
                start=1,
            )
        ]
        entries = [
            _entry(
                id=f"cases {ordinal}",
                suite="cases",
                category="cases",
                ordinal=ordinal,
                description=f"case {ordinal}",
                state=state,
                rationale=None if state == "passing" else "classified",
                owner=(
                    "Parity owner"
                    if state in ("applicable", "blocked", "failing")
                    else None
                ),
                bead=(
                    "flpdf-25kg.1"
                    if state in ("applicable", "blocked", "failing")
                    else None
                ),
                replacement_ref=(
                    "scope:docs/scope.md#excluded"
                    if state == "excluded"
                    else (
                        "rust-test:flpdf:reader_tests:oracle"
                        if state == "represented"
                        else None
                    )
                ),
            )
            for ordinal, state in enumerate(
                (
                    "applicable",
                    "blocked",
                    "excluded",
                    "failing",
                    "passing",
                    "represented",
                ),
                start=1,
            )
        ]

        validation = verify_manifest.validate_manifest(_run(results), entries)

        self.assertEqual(validation.errors, ())
        self.assertEqual(validation.total, 6)
        self.assertEqual(
            validation.counts,
            {state: 1 for state in sorted(verify_manifest.STATES)},
        )
        self.assertEqual(sum(validation.counts.values()), validation.total)


class SummaryAndMainTest(unittest.TestCase):
    def test_full_summary_includes_authoritative_and_every_state_count(self) -> None:
        run = _run(
            [
                _result("arg-parsing", 1, "required argument", Outcome.PASS),
                _result(
                    "arg-parsing",
                    2,
                    "known failure",
                    Outcome.EXPECTED_FAIL,
                ),
            ]
        )
        validation = verify_manifest.Validation(
            errors=(),
            counts={"passing": 1, "applicable": 1},
            total=2,
        )

        summary = verify_manifest.render_summary(run, validation)

        self.assertIn("- Authoritative total: **2**", summary)
        self.assertIn("- Ordinary passes: **1**", summary)
        self.assertIn("- Expected failures: **1**", summary)
        for state in verify_manifest.STATES:
            expected = 1 if state in ("passing", "applicable") else 0
            self.assertIn(f"- {state}: **{expected}**", summary)
        self.assertIn("**Verdict: OK**", summary)
        self.assertTrue(summary.endswith("\n"))

    def test_step_summary_omits_long_validation_identity_list(self) -> None:
        run = _run([_result("arg-parsing", 1, "required argument", Outcome.PASS)])
        validation = verify_manifest.Validation(
            errors=("missing manifest identity 'arg-parsing 1'",),
            counts={},
            total=1,
        )

        full = verify_manifest.render_summary(run, validation)
        step = verify_manifest.render_summary(
            run, validation, include_details=False
        )

        self.assertIn("missing manifest identity 'arg-parsing 1'", full)
        self.assertNotIn("arg-parsing 1", step)
        self.assertIn("- Validation errors: **1**", step)
        self.assertIn("**Verdict: FAIL**", step)

    def test_valid_cli_writes_full_and_step_summaries_and_exits_zero(self) -> None:
        result = _result(
            "arg-parsing", 1, "required argument", Outcome.PASS
        )
        log, xml = _paired_artifacts([result])
        manifest = _tmp(_manifest_text([_entry()]))
        full = _tmp("", suffix=".md")
        step = _tmp("", suffix=".md")
        stdout = io.StringIO()
        stderr = io.StringIO()

        with (
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            rc = verify_manifest.main(
                [
                    str(log),
                    str(xml),
                    str(manifest),
                    "--summary",
                    str(full),
                    "--step-summary",
                    str(step),
                ]
            )

        self.assertEqual(rc, 0)
        self.assertEqual(stderr.getvalue(), "")
        self.assertIn("**Verdict: OK**", stdout.getvalue())
        self.assertIn("- Authoritative total: **1**", full.read_text())
        for state in verify_manifest.STATES:
            self.assertIn(f"- {state}:", full.read_text())
        self.assertIn("**Verdict: OK**", step.read_text())

    def test_validation_error_exits_one_and_step_omits_identity_list(self) -> None:
        result = _result(
            "arg-parsing", 1, "required argument", Outcome.PASS
        )
        log, xml = _paired_artifacts([result])
        manifest = _tmp("")
        full = _tmp("", suffix=".md")
        step = _tmp("", suffix=".md")

        with contextlib.redirect_stdout(io.StringIO()):
            rc = verify_manifest.main(
                [
                    str(log),
                    str(xml),
                    str(manifest),
                    "--summary",
                    str(full),
                    "--step-summary",
                    str(step),
                ]
            )

        self.assertEqual(rc, 1)
        self.assertIn("missing manifest identity 'arg-parsing 1'", full.read_text())
        self.assertNotIn("arg-parsing 1", step.read_text())
        self.assertIn("**Verdict: FAIL**", step.read_text())

    def test_parser_error_exits_one_without_emitting_success_like_summary(self) -> None:
        log = _tmp("arg-parsing 1 (required argument) ... PASSED\n", suffix=".log")
        xml = _tmp("<not-results/>\n", suffix=".xml")
        manifest = _tmp(_manifest_text([_entry()]))
        full = _tmp("existing full\n", suffix=".md")
        step = _tmp("existing step\n", suffix=".md")
        stdout = io.StringIO()
        stderr = io.StringIO()

        with (
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            rc = verify_manifest.main(
                [
                    str(log),
                    str(xml),
                    str(manifest),
                    "--summary",
                    str(full),
                    "--step-summary",
                    str(step),
                ]
            )

        self.assertEqual(rc, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn(
            "verify-parity-manifest: malformed XML", stderr.getvalue()
        )
        self.assertEqual(full.read_text(), "existing full\n")
        self.assertEqual(step.read_text(), "existing step\n")

    def test_manifest_loader_error_exits_one_without_summary(self) -> None:
        log, xml = _paired_artifacts(
            [_result("arg-parsing", 1, "required argument", Outcome.PASS)]
        )
        manifest = _tmp("{bad json}\n")
        full = _tmp("", suffix=".md")
        stdout = io.StringIO()
        stderr = io.StringIO()

        with (
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            rc = verify_manifest.main(
                [str(log), str(xml), str(manifest), "--summary", str(full)]
            )

        self.assertEqual(rc, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("verify-parity-manifest:", stderr.getvalue())
        self.assertEqual(full.read_text(), "")

    def test_invalid_field_types_exit_one_without_typeerror_traceback(
        self,
    ) -> None:
        log, xml = _paired_artifacts(
            [_result("arg-parsing", 1, "required argument", Outcome.PASS)]
        )
        invalid_values = (
            _entry(id=False),
            _entry(suite=0),
            _entry(category=[]),
            _entry(description={}),
            _entry(state=1),
            _entry(ordinal=True),
            _entry(ordinal=1.5),
            _entry(ordinal=0),
            _entry(ordinal=-1),
            _entry(rationale=False),
            _entry(owner=[]),
            _entry(bead={}),
            _entry(replacement_ref=1),
        )

        for value in invalid_values:
            with self.subTest(value=value):
                manifest = _tmp(_manifest_text([value]))
                full = _tmp("existing full\n", suffix=".md")
                stdout = io.StringIO()
                stderr = io.StringIO()
                with (
                    contextlib.redirect_stdout(stdout),
                    contextlib.redirect_stderr(stderr),
                ):
                    rc = verify_manifest.main(
                        [
                            str(log),
                            str(xml),
                            str(manifest),
                            "--summary",
                            str(full),
                        ]
                    )

                self.assertEqual(rc, 1)
                self.assertEqual(stdout.getvalue(), "")
                self.assertIn(f"{manifest}:1:", stderr.getvalue())
                self.assertNotIn("Traceback", stderr.getvalue())
                self.assertNotIn("TypeError", stderr.getvalue())
                self.assertEqual(full.read_text(), "existing full\n")

    def test_duplicate_json_key_cli_error_has_no_traceback_or_summary(
        self,
    ) -> None:
        log, xml = _paired_artifacts(
            [_result("arg-parsing", 1, "required argument", Outcome.PASS)]
        )
        manifest = _tmp(
            '{"id":"first","id":"arg-parsing 1",'
            '"suite":"arg-parsing","category":"arg-parsing","ordinal":1,'
            '"description":"required argument","state":"passing",'
            '"rationale":null,"owner":null,"bead":null,'
            '"replacement_ref":null}\n'
        )
        full = _tmp("existing full\n", suffix=".md")
        stdout = io.StringIO()
        stderr = io.StringIO()

        with (
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            rc = verify_manifest.main(
                [
                    str(log),
                    str(xml),
                    str(manifest),
                    "--summary",
                    str(full),
                ]
            )

        self.assertEqual(rc, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn(
            f"{manifest}:1: duplicate JSON key 'id'", stderr.getvalue()
        )
        self.assertNotIn("Traceback", stderr.getvalue())
        self.assertNotIn("TypeError", stderr.getvalue())
        self.assertEqual(full.read_text(), "existing full\n")

    def test_zero_authoritative_results_is_an_operational_error(self) -> None:
        log, xml = _paired_artifacts([])
        manifest = _tmp("")
        full = _tmp("existing full\n", suffix=".md")
        step = _tmp("existing step\n", suffix=".md")
        stdout = io.StringIO()
        stderr = io.StringIO()

        with (
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            rc = verify_manifest.main(
                [
                    str(log),
                    str(xml),
                    str(manifest),
                    "--summary",
                    str(full),
                    "--step-summary",
                    str(step),
                ]
            )

        self.assertEqual(rc, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn(
            "verify-parity-manifest: no authoritative subtest results",
            stderr.getvalue(),
        )
        self.assertEqual(full.read_text(), "existing full\n")
        self.assertEqual(step.read_text(), "existing step\n")

    def test_script_entrypoint_exits_with_main_result(self) -> None:
        log, xml = _paired_artifacts(
            [_result("arg-parsing", 1, "required argument", Outcome.PASS)]
        )
        manifest = _tmp(_manifest_text([_entry()]))

        with (
            mock.patch.object(
                sys,
                "argv",
                [str(_VERIFY_PATH), str(log), str(xml), str(manifest)],
            ),
            contextlib.redirect_stdout(io.StringIO()),
            self.assertRaises(SystemExit) as raised,
        ):
            runpy.run_path(str(_VERIFY_PATH), run_name="__main__")

        self.assertEqual(raised.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
