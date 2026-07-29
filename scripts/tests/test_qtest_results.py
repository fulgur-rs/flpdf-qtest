"""Unit tests for scripts/qtest_results.py.

Run with: python3 -m unittest scripts/tests/test_qtest_results.py
"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_MODULE_PATH = _HERE.parent / "qtest_results.py"
_spec = importlib.util.spec_from_file_location("qtest_results", _MODULE_PATH)
assert _spec and _spec.loader
qtest_results = importlib.util.module_from_spec(_spec)
sys.modules["qtest_results"] = qtest_results
_spec.loader.exec_module(qtest_results)


def _tmp(suffix: str, content: str) -> Path:
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=suffix, delete=False, encoding="utf-8"
    ) as f:
        f.write(textwrap.dedent(content).lstrip())
        return Path(f.name)


def _xml(
    suites: str,
    *,
    total: int,
    passes: int,
    failures: int,
    xpasses: int = 0,
    xfails: int = 0,
) -> Path:
    return _tmp(
        ".xml",
        f"""\
        <?xml version="1.0"?>
        <qtest-results version="1">
        {textwrap.dedent(suites)}
         <testsummary total-cases="{total}" passes="{passes}"
          failures="{failures}" unexpected-passes="{xpasses}"
          expected-failures="{xfails}" missing-cases="0" extra-cases="0"/>
        </qtest-results>
        """,
    )


class ParseRunTest(unittest.TestCase):
    def test_parses_ordinary_pass(self) -> None:
        xml = _xml(
            """
             <testsuite file="/repo/sample.test">
              <testcase testid="sample 1" description="ok" outcome="pass"/>
              <testsummary total-cases="1" passes="1" failures="0"
               unexpected-passes="0" expected-failures="0"
               missing-cases="0" extra-cases="0"/>
             </testsuite>
            """,
            total=1,
            passes=1,
            failures=0,
        )
        log = _tmp(".log", "sample  1 (ok) ... PASSED\n")

        run = qtest_results.parse_run(log, xml)

        self.assertEqual(run.results[0].outcome, qtest_results.Outcome.PASS)
        self.assertEqual(run.summary.passes, 1)

    def test_parses_ordinary_failure(self) -> None:
        xml = _xml(
            """
             <testsuite file="/repo/sample.test">
              <testcase testid="sample 1" description="broken" outcome="fail"/>
              <testsummary total-cases="1" passes="0" failures="1"
               unexpected-passes="0" expected-failures="0"
               missing-cases="0" extra-cases="0"/>
             </testsuite>
            """,
            total=1,
            passes=0,
            failures=1,
        )
        log = _tmp(".log", "sample test 1 (broken) FAILED\n")

        run = qtest_results.parse_run(log, xml)

        self.assertEqual(run.results[0].outcome, qtest_results.Outcome.FAIL)
        self.assertEqual(run.summary.failures, 1)

    def test_parses_expected_failure(self) -> None:
        xml = _xml(
            """
             <testsuite file="/repo/sample.test">
              <testcase testid="sample 1" description="known" outcome="fail"/>
              <testsummary total-cases="1" passes="0" failures="0"
               unexpected-passes="0" expected-failures="1"
               missing-cases="0" extra-cases="0"/>
             </testsuite>
            """,
            total=1,
            passes=0,
            failures=0,
            xfails=1,
        )
        log = _tmp(".log", "sample  1 (known) ... FAILED (exp)\n")

        run = qtest_results.parse_run(log, xml)

        self.assertEqual(run.results[0].outcome, qtest_results.Outcome.EXPECTED_FAIL)
        self.assertEqual(run.summary.expected_failures, 1)

    def test_parses_unexpected_pass(self) -> None:
        xml = _xml(
            """
             <testsuite file="/repo/sample.test">
              <testcase testid="sample 1" description="surprise" outcome="pass"/>
              <testsummary total-cases="1" passes="0" failures="0"
               unexpected-passes="1" expected-failures="0"
               missing-cases="0" extra-cases="0"/>
             </testsuite>
            """,
            total=1,
            passes=0,
            failures=0,
            xpasses=1,
        )
        log = _tmp(".log", "sample  1 (surprise) ... PASSED-UNEXP\n")

        run = qtest_results.parse_run(log, xml)

        self.assertEqual(run.results[0].outcome, qtest_results.Outcome.UNEXPECTED_PASS)
        self.assertEqual(run.summary.unexpected_passes, 1)

    def test_deduplicates_repeated_identical_failure_headers(self) -> None:
        xml = _xml(
            """
             <testsuite file="/repo/sample.test">
              <testcase testid="sample 1" description="broken" outcome="fail"/>
              <testsummary total-cases="1" passes="0" failures="1"
               unexpected-passes="0" expected-failures="0"
               missing-cases="0" extra-cases="0"/>
             </testsuite>
            """,
            total=1,
            passes=0,
            failures=1,
        )
        log = _tmp(
            ".log",
            """
            sample test 1 (broken) FAILED
            sample test 1 (broken) FAILED
            """,
        )

        run = qtest_results.parse_run(log, xml)

        self.assertEqual(len(run.results), 1)

    def test_rejects_duplicate_xml_test_ids(self) -> None:
        xml = _xml(
            """
             <testsuite file="/repo/one.test">
              <testcase testid="sample 1" description="one" outcome="pass"/>
              <testsummary total-cases="1" passes="1" failures="0"
               unexpected-passes="0" expected-failures="0"
               missing-cases="0" extra-cases="0"/>
             </testsuite>
             <testsuite file="/repo/two.test">
              <testcase testid="sample 1" description="two" outcome="pass"/>
              <testsummary total-cases="1" passes="1" failures="0"
               unexpected-passes="0" expected-failures="0"
               missing-cases="0" extra-cases="0"/>
             </testsuite>
            """,
            total=2,
            passes=2,
            failures=0,
        )
        log = _tmp(".log", "sample  1 (one) ... PASSED\n")

        with self.assertRaisesRegex(qtest_results.ResultError, "duplicate XML"):
            qtest_results.parse_run(log, xml)

    def test_rejects_conflicting_repeated_log_identity(self) -> None:
        xml = _xml(
            """
             <testsuite file="/repo/sample.test">
              <testcase testid="sample 1" description="broken" outcome="fail"/>
              <testsummary total-cases="1" passes="0" failures="1"
               unexpected-passes="0" expected-failures="0"
               missing-cases="0" extra-cases="0"/>
             </testsuite>
            """,
            total=1,
            passes=0,
            failures=1,
        )
        log = _tmp(
            ".log",
            """
            sample test 1 (broken) FAILED
            sample test 1 (different) FAILED
            """,
        )

        with self.assertRaisesRegex(qtest_results.ResultError, "conflicting log"):
            qtest_results.parse_run(log, xml)

    def test_rejects_xml_only_identity(self) -> None:
        xml = _xml(
            """
             <testsuite file="/repo/sample.test">
              <testcase testid="sample 1" description="missing" outcome="pass"/>
              <testsummary total-cases="1" passes="1" failures="0"
               unexpected-passes="0" expected-failures="0"
               missing-cases="0" extra-cases="0"/>
             </testsuite>
            """,
            total=1,
            passes=1,
            failures=0,
        )
        log = _tmp(".log", "Total tests: 0\n")

        with self.assertRaisesRegex(qtest_results.ResultError, "XML/log identity"):
            qtest_results.parse_run(log, xml)

    def test_rejects_valid_log_only_identity(self) -> None:
        xml = _xml(
            """
             <testsuite file="/repo/sample.test">
              <testcase testid="sample 1" description="kept" outcome="pass"/>
              <testsummary total-cases="1" passes="1" failures="0"
               unexpected-passes="0" expected-failures="0"
               missing-cases="0" extra-cases="0"/>
             </testsuite>
            """,
            total=1,
            passes=1,
            failures=0,
        )
        log = _tmp(
            ".log",
            """
            sample  1 (kept) ... PASSED
            extra  1 (unexpected) ... PASSED
            """,
        )

        with self.assertRaisesRegex(qtest_results.ResultError, "XML/log identity"):
            qtest_results.parse_run(log, xml)

    def test_rejects_description_mismatch(self) -> None:
        xml = _xml(
            """
             <testsuite file="/repo/sample.test">
              <testcase testid="sample 1" description="expected" outcome="pass"/>
              <testsummary total-cases="1" passes="1" failures="0"
               unexpected-passes="0" expected-failures="0"
               missing-cases="0" extra-cases="0"/>
             </testsuite>
            """,
            total=1,
            passes=1,
            failures=0,
        )
        log = _tmp(".log", "sample  1 (seen) ... PASSED\n")

        with self.assertRaisesRegex(qtest_results.ResultError, "description"):
            qtest_results.parse_run(log, xml)

    def test_restores_qtest_utf8_byte_entities_for_umlaut_description(self) -> None:
        xml = _xml(
            """
             <testsuite file="/repo/unicode.test">
              <testcase testid="unicode 1" description="auto-&#xc3;&#xbc;" outcome="fail"/>
              <testsummary total-cases="1" passes="0" failures="1"
               unexpected-passes="0" expected-failures="0"
               missing-cases="0" extra-cases="0"/>
             </testsuite>
            """,
            total=1,
            passes=0,
            failures=1,
        )
        log = _tmp(".log", "unicode test 1 (auto-ü) FAILED\n")

        run = qtest_results.parse_run(log, xml)

        self.assertEqual(run.results[0].description, "auto-ü")

    def test_restores_qtest_utf8_byte_entities_for_multibyte_description(self) -> None:
        xml = _xml(
            """
             <testsuite file="/repo/unicode.test">
              <testcase testid="unicode 1" description="auto-&#xc3;&#xb6;&#xcf;&#x80;" outcome="fail"/>
              <testsummary total-cases="1" passes="0" failures="1"
               unexpected-passes="0" expected-failures="0"
               missing-cases="0" extra-cases="0"/>
             </testsuite>
            """,
            total=1,
            passes=0,
            failures=1,
        )
        log = _tmp(".log", "unicode test 1 (auto-öπ) FAILED\n")

        run = qtest_results.parse_run(log, xml)

        self.assertEqual(run.results[0].description, "auto-öπ")

    def test_preserves_ascii_natural_unicode_and_invalid_byte_descriptions(self) -> None:
        xml = _xml(
            """
             <testsuite file="/repo/descriptions.test">
              <testcase testid="ascii 1" description="plain ASCII" outcome="pass"/>
              <testcase testid="natural 1" description="already ü" outcome="pass"/>
              <testcase testid="invalid 1" description="invalid &#xff;" outcome="pass"/>
              <testsummary total-cases="3" passes="3" failures="0"
               unexpected-passes="0" expected-failures="0"
               missing-cases="0" extra-cases="0"/>
             </testsuite>
            """,
            total=3,
            passes=3,
            failures=0,
        )
        log = _tmp(
            ".log",
            """
            ascii  1 (plain ASCII) ... PASSED
            natural  1 (already ü) ... PASSED
            invalid  1 (invalid ÿ) ... PASSED
            """,
        )

        run = qtest_results.parse_run(log, xml)

        self.assertEqual(
            {result.id: result.description for result in run.results},
            {
                "ascii 1": "plain ASCII",
                "natural 1": "already ü",
                "invalid 1": "invalid ÿ",
            },
        )

    def test_rejects_xml_actual_outcome_mismatch(self) -> None:
        xml = _xml(
            """
             <testsuite file="/repo/sample.test">
              <testcase testid="sample 1" description="known" outcome="pass"/>
              <testsummary total-cases="1" passes="1" failures="0"
               unexpected-passes="0" expected-failures="0"
               missing-cases="0" extra-cases="0"/>
             </testsuite>
            """,
            total=1,
            passes=1,
            failures=0,
        )
        log = _tmp(".log", "sample  1 (known) ... FAILED (exp)\n")

        with self.assertRaisesRegex(qtest_results.ResultError, "outcome"):
            qtest_results.parse_run(log, xml)

    def test_rejects_child_summary_mismatch(self) -> None:
        xml = _xml(
            """
             <testsuite file="/repo/sample.test">
              <testcase testid="sample 1" description="ok" outcome="pass"/>
              <testsummary total-cases="1" passes="0" failures="1"
               unexpected-passes="0" expected-failures="0"
               missing-cases="0" extra-cases="0"/>
             </testsuite>
            """,
            total=1,
            passes=1,
            failures=0,
        )
        log = _tmp(".log", "sample  1 (ok) ... PASSED\n")

        with self.assertRaisesRegex(qtest_results.ResultError, "child"):
            qtest_results.parse_run(log, xml)

    def test_rejects_root_summary_mismatch(self) -> None:
        xml = _xml(
            """
             <testsuite file="/repo/sample.test">
              <testcase testid="sample 1" description="ok" outcome="pass"/>
              <testsummary total-cases="1" passes="1" failures="0"
               unexpected-passes="0" expected-failures="0"
               missing-cases="0" extra-cases="0"/>
             </testsuite>
            """,
            total=2,
            passes=2,
            failures=0,
        )
        log = _tmp(".log", "sample  1 (ok) ... PASSED\n")

        with self.assertRaisesRegex(qtest_results.ResultError, "root total"):
            qtest_results.parse_run(log, xml)

    def test_rejects_malformed_xml(self) -> None:
        xml = _tmp(".xml", "<qtest-results>")
        log = _tmp(".log", "")

        with self.assertRaisesRegex(qtest_results.ResultError, "malformed XML"):
            qtest_results.parse_run(log, xml)

    def test_rejects_invalid_xml_test_id(self) -> None:
        xml = _xml(
            """
             <testsuite file="/repo/sample.test">
              <testcase testid="not-an-id" description="bad" outcome="pass"/>
              <testsummary total-cases="1" passes="1" failures="0"
               unexpected-passes="0" expected-failures="0"
               missing-cases="0" extra-cases="0"/>
             </testsuite>
            """,
            total=1,
            passes=1,
            failures=0,
        )
        log = _tmp(".log", "")

        with self.assertRaisesRegex(qtest_results.ResultError, "invalid testid"):
            qtest_results.parse_run(log, xml)

    def test_rejects_missing_harness_log_as_result_error(self) -> None:
        xml = _xml(
            """
             <testsuite file="/repo/sample.test">
              <testcase testid="sample 1" description="ok" outcome="pass"/>
              <testsummary total-cases="1" passes="1" failures="0"
               unexpected-passes="0" expected-failures="0"
               missing-cases="0" extra-cases="0"/>
             </testsuite>
            """,
            total=1,
            passes=1,
            failures=0,
        )
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "missing.log"

            with self.assertRaisesRegex(qtest_results.ResultError, "harness log"):
                qtest_results.parse_run(log, xml)

    def test_rejects_unreadable_harness_log_as_result_error(self) -> None:
        xml = _xml(
            """
             <testsuite file="/repo/sample.test">
              <testcase testid="sample 1" description="ok" outcome="pass"/>
              <testsummary total-cases="1" passes="1" failures="0"
               unexpected-passes="0" expected-failures="0"
               missing-cases="0" extra-cases="0"/>
             </testsuite>
            """,
            total=1,
            passes=1,
            failures=0,
        )
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory)

            with self.assertRaisesRegex(qtest_results.ResultError, "harness log"):
                qtest_results.parse_run(log, xml)

    def test_discards_suite_with_only_nested_summary(self) -> None:
        xml = _xml(
            """
             <testsuite file="/repo/invalid.test">
              <testcase testid="invalid 1" description="partial" outcome="fail"/>
              <wrapper>
               <testsummary total-cases="1" passes="0" failures="1"
                unexpected-passes="0" expected-failures="0"
                missing-cases="0" extra-cases="0"/>
              </wrapper>
             </testsuite>
            """,
            total=0,
            passes=0,
            failures=0,
        )
        log = _tmp(".log", "invalid test 1 (partial) FAILED\n")

        run = qtest_results.parse_run(log, xml)

        self.assertEqual(run.results, ())
        self.assertEqual(run.invalid_suites, ("invalid",))

    def test_sorts_multiple_identities_by_category_and_ordinal(self) -> None:
        xml = _xml(
            """
             <testsuite file="/repo/sample.test">
              <testcase testid="beta 2" description="two" outcome="pass"/>
              <testcase testid="alpha 3" description="three" outcome="pass"/>
              <testcase testid="beta 1" description="one" outcome="pass"/>
              <testsummary total-cases="3" passes="3" failures="0"
               unexpected-passes="0" expected-failures="0"
               missing-cases="0" extra-cases="0"/>
             </testsuite>
            """,
            total=3,
            passes=3,
            failures=0,
        )
        log = _tmp(
            ".log",
            """
            beta  2 (two) ... PASSED
            alpha  3 (three) ... PASSED
            beta  1 (one) ... PASSED
            """,
        )

        run = qtest_results.parse_run(log, xml)

        self.assertEqual(
            [result.id for result in run.results],
            ["alpha 3", "beta 1", "beta 2"],
        )

    def test_uses_xml_testid_not_suite_stem_as_identity(self) -> None:
        xml = _xml(
            """
             <testsuite file="/repo/weak-cryptography.test">
              <testcase testid="weak-cryptography-cryptography 1"
               description="256-bit: no warning" outcome="pass"/>
              <testsummary total-cases="1" passes="1" failures="0"
               unexpected-passes="0" expected-failures="0"
               missing-cases="0" extra-cases="0"/>
             </testsuite>
            """,
            total=1,
            passes=1,
            failures=0,
        )
        log = _tmp(
            ".log",
            """
            weak-cryptography-cryptography  1 (256-bit: no warning) ... PASSED
            Total tests: 1
            Passes: 1
            Failures: 0
            Unexpected Passes: 0
            Expected Failures: 0
            """,
        )

        run = qtest_results.parse_run(log, xml)

        self.assertEqual(run.results[0].id, "weak-cryptography-cryptography 1")
        self.assertEqual(run.results[0].suite, "weak-cryptography")
        self.assertEqual(run.results[0].category, "weak-cryptography-cryptography")

    def test_discards_partial_cases_from_suite_without_child_summary(self) -> None:
        xml = _xml(
            """
             <testsuite file="/repo/valid.test">
              <testcase testid="valid 1" description="kept" outcome="pass"/>
              <testsummary total-cases="1" passes="1" failures="0"
               unexpected-passes="0" expected-failures="0"
               missing-cases="0" extra-cases="0"/>
             </testsuite>
             <testsuite file="/repo/invalid.test">
              <testcase testid="invalid 1" description="partial" outcome="fail"/>
             </testsuite>
            """,
            total=1,
            passes=1,
            failures=0,
        )
        log = _tmp(
            ".log",
            """
            valid  1 (kept) ... PASSED
            invalid test 1 (partial) FAILED
            Total tests: 1
            Passes: 1
            Failures: 0
            Unexpected Passes: 0
            Expected Failures: 0
            """,
        )

        run = qtest_results.parse_run(log, xml)

        self.assertEqual([r.id for r in run.results], ["valid 1"])
        self.assertEqual(run.invalid_suites, ("invalid",))
