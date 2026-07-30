"""Unit tests for scripts/qtest_results.py.

Run with: python3 -m unittest scripts/tests/test_qtest_results.py
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import textwrap
import unittest
import xml.etree.ElementTree as ET
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


def _tmp_bytes(suffix: str, content: bytes) -> Path:
    with tempfile.NamedTemporaryFile(mode="wb", suffix=suffix, delete=False) as f:
        f.write(content)
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
    def test_result_exposes_legacy_allowlist_aliases(self) -> None:
        result = qtest_results.Result(
            suite="suite",
            category="category",
            ordinal=3,
            description="description",
            outcome=qtest_results.Outcome.PASS,
        )

        self.assertEqual(result.test, "category")
        self.assertEqual(result.subtest, "description")

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

    def test_restores_qtest_del_and_utf8_byte_entities(self) -> None:
        xml = _xml(
            """
             <testsuite file="/repo/unicode.test">
              <testcase testid="unicode 1" description="&#x7f;&#xc3;&#xbc;" outcome="fail"/>
              <testsummary total-cases="1" passes="0" failures="1"
               unexpected-passes="0" expected-failures="0"
               missing-cases="0" extra-cases="0"/>
             </testsuite>
            """,
            total=1,
            passes=0,
            failures=1,
        )
        log = _tmp(".log", "unicode test 1 (\x7fü) FAILED\n")

        run = qtest_results.parse_run(log, xml)

        self.assertEqual(run.results[0].description, "\x7fü")

    def test_restores_qtest_noncharacter_byte_entities_without_rewriting_xml(self) -> None:
        xml = _xml(
            """
             <testsuite file="/repo/unicode.test">
              <testcase testid="unicode 1" description="&#xef;&#xbf;&#xbe;" outcome="fail"/>
              <testsummary total-cases="1" passes="0" failures="1"
               unexpected-passes="0" expected-failures="0"
               missing-cases="0" extra-cases="0"/>
             </testsuite>
            """,
            total=1,
            passes=0,
            failures=1,
        )
        log = _tmp(".log", "unicode test 1 (\ufffe) FAILED\n")

        run = qtest_results.parse_run(log, xml)

        self.assertEqual(run.results[0].description, "\ufffe")

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

    def test_restores_zero_padded_mixed_case_qtest_byte_entities(self) -> None:
        xml = _xml(
            """
             <testsuite file="/repo/unicode.test">
              <testcase testid="unicode 1" description="auto-&#x00C3;&#x0bC;" outcome="fail"/>
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

    def test_restores_only_testcase_description_attributes(self) -> None:
        raw = (
            b'<qtest-results><testcase note=\'description="auto-&#xc3;&#xbc;"\' '
            b'testid="auto-&#xc3;&#xbc; 1" '
            b'description="auto-&#xc3;&#xbc;" outcome="pass"/>'
            b'<metadata description="auto-&#xc3;&#xbc;"/></qtest-results>'
        )

        xml = _tmp_bytes(".xml", raw)
        provenance = qtest_results._collect_description_provenance(xml)

        self.assertEqual(xml.read_bytes(), raw)
        self.assertEqual(
            provenance,
            [
                qtest_results._DescriptionProvenance(
                    testid="auto-Ã¼ 1", description="auto-ü"
                )
            ],
        )

    def test_decodes_all_supported_xml_attribute_entities(self) -> None:
        decoded = qtest_results._decode_xml_attribute(
            b"&amp;&apos;&gt;&lt;&quot;&#65;&#x42;&#x110000;"
        )

        self.assertEqual(decoded, "&'><\"AB&#x110000;")

    def test_rejects_non_utf8_raw_xml_attribute(self) -> None:
        with self.assertRaisesRegex(qtest_results.ResultError, "malformed XML attribute"):
            qtest_results._decode_xml_attribute(b"\xff")

    def test_does_not_restore_low_byte_numeric_entity_as_qtest_utf8(self) -> None:
        self.assertIsNone(qtest_results._restore_qtest_description(b"&#x41;"))

    def test_parses_spaced_attributes_and_rejects_malformed_provenance(self) -> None:
        self.assertEqual(
            qtest_results._testcase_attributes(
                b'<testcase testid \t= \n"sample 1" />'
            ),
            {b"testid": b"sample 1"},
        )
        malformed = (
            b"<testcase ?",
            b'<testcase testid "sample 1">',
            b"<testcase testid=sample>",
            b'<testcase testid="sample 1>',
            b"<testcase",
        )
        for tag in malformed:
            with self.subTest(tag=tag):
                with self.assertRaisesRegex(
                    qtest_results.ResultError,
                    "malformed testcase provenance",
                ):
                    qtest_results._testcase_attributes(tag)

    def test_rejects_nonpositive_provenance_chunk_size(self) -> None:
        xml = _tmp_bytes(".xml", b"<qtest-results/>")

        with self.assertRaisesRegex(ValueError, "chunk_size must be positive"):
            qtest_results._collect_description_provenance(xml, chunk_size=0)

    def test_collects_case_without_description_as_none(self) -> None:
        xml = _tmp_bytes(
            ".xml",
            b'<qtest-results><testcase testid="sample 1"/></qtest-results>',
        )

        provenance = qtest_results._collect_description_provenance(xml)

        self.assertEqual(
            provenance,
            [
                qtest_results._DescriptionProvenance(
                    testid="sample 1",
                    description=None,
                )
            ],
        )

    def test_ignores_longer_element_name_with_testcase_prefix(self) -> None:
        xml = _tmp_bytes(
            ".xml",
            b'<qtest-results><testcases testid="not a case"/></qtest-results>',
        )

        self.assertEqual(qtest_results._collect_description_provenance(xml), [])

    def test_wraps_provenance_xml_open_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing.xml"

            with self.assertRaisesRegex(qtest_results.ResultError, "malformed XML"):
                qtest_results._collect_description_provenance(missing)

    def test_collects_description_provenance_across_chunk_and_quote_boundaries(self) -> None:
        raw = (
            b'<qtest-results><testcase note=\'a > b\' '
            b'testid="unicode 1" description="auto-&#xc3;&#xbc;" '
            b'outcome="fail"/></qtest-results>'
        )
        xml = _tmp_bytes(".xml", raw)

        provenance = qtest_results._collect_description_provenance(xml, chunk_size=7)

        self.assertEqual(
            provenance,
            [
                qtest_results._DescriptionProvenance(
                    testid="unicode 1", description="auto-ü"
                )
            ],
        )

    def test_collects_multiple_testcase_tags_in_order(self) -> None:
        raw = (
            b'<qtest-results><testcase testid="first 1" '
            b'description="&#xc3;&#xbc;"/><testcase testid="second 2" '
            b'description="&#xc3;&#xb6;"/></qtest-results>'
        )
        xml = _tmp_bytes(".xml", raw)

        provenance = qtest_results._collect_description_provenance(xml, chunk_size=11)

        self.assertEqual(
            provenance,
            [
                qtest_results._DescriptionProvenance(
                    testid="first 1", description="ü"
                ),
                qtest_results._DescriptionProvenance(
                    testid="second 2", description="ö"
                ),
            ],
        )

    def test_ignores_eof_unfinished_testcase_tag_until_xml_parse(self) -> None:
        xml = _tmp_bytes(
            ".xml",
            b'<qtest-results><testcase testid="unicode 1" '
            b'description="&#xc3;&#xbc;"',
        )

        provenance = qtest_results._collect_description_provenance(xml, chunk_size=7)

        self.assertEqual(provenance, [])

    def test_long_quoted_description_prepass_completes_in_a_subprocess(self) -> None:
        payload = b"x" * (16 * 1024 * 1024)
        raw = (
            b'<qtest-results><testcase testid="unicode 1" description="'
            + payload
            + b'&#xc3;&#xbc;"/></qtest-results>'
        )
        code = """
import importlib.util
import sys
from pathlib import Path

spec = importlib.util.spec_from_file_location("qtest_results_subprocess", sys.argv[1])
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
provenance = module._collect_description_provenance(Path(sys.argv[2]), chunk_size=257)
assert len(provenance) == 1
assert provenance[0].testid == "unicode 1"
assert provenance[0].description.endswith("ü")
"""
        with tempfile.TemporaryDirectory() as directory:
            xml = Path(directory) / "long-tag.xml"
            xml.write_bytes(raw)
            completed = subprocess.run(
                [sys.executable, "-c", code, str(_MODULE_PATH), str(xml)],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_rejects_description_provenance_identity_mismatch(self) -> None:
        root = ET.fromstring(
            '<qtest-results><testcase testid="actual 1" description="plain"/>'
            "</qtest-results>"
        )

        with self.assertRaisesRegex(qtest_results.ResultError, "provenance mismatch"):
            qtest_results._apply_description_provenance(
                root,
                [
                    qtest_results._DescriptionProvenance(
                        testid="other 1", description="restored"
                    )
                ],
            )

    def test_rejects_description_provenance_case_count_mismatch(self) -> None:
        root = ET.fromstring(
            '<qtest-results><testcase testid="actual 1" description="plain"/>'
            "</qtest-results>"
        )

        with self.assertRaisesRegex(qtest_results.ResultError, "testcase count"):
            qtest_results._apply_description_provenance(root, [])

    def test_rejects_restored_description_missing_from_xml_tree(self) -> None:
        root = ET.fromstring('<qtest-results><testcase testid="actual 1"/>'
                             "</qtest-results>")

        with self.assertRaisesRegex(qtest_results.ResultError, "description at"):
            qtest_results._apply_description_provenance(
                root,
                [
                    qtest_results._DescriptionProvenance(
                        testid="actual 1",
                        description="restored",
                    )
                ],
            )

    def test_rejects_missing_xml_testcase_attribute(self) -> None:
        case = ET.fromstring('<testcase testid="sample 1" outcome="pass"/>')

        with self.assertRaisesRegex(qtest_results.ResultError, "invalid testcase"):
            qtest_results._parse_xml_case(case, "sample")

    def test_rejects_non_actual_xml_outcome(self) -> None:
        case = ET.fromstring(
            '<testcase testid="sample 1" description="bad" '
            'outcome="unexpected-pass"/>'
        )

        with self.assertRaisesRegex(qtest_results.ResultError, "actual outcome"):
            qtest_results._parse_xml_case(case, "sample")

    def test_preserves_literal_latin1_looking_utf8_descriptions(self) -> None:
        xml = _xml(
            """
             <testsuite file="/repo/literal.test">
              <testcase testid="literal 1" description="literal Ã¼" outcome="pass"/>
              <testcase testid="literal 2" description="literal Â£" outcome="pass"/>
              <testsummary total-cases="2" passes="2" failures="0"
               unexpected-passes="0" expected-failures="0"
               missing-cases="0" extra-cases="0"/>
             </testsuite>
            """,
            total=2,
            passes=2,
            failures=0,
        )
        log = _tmp(
            ".log",
            """
            literal  1 (literal Ã¼) ... PASSED
            literal  2 (literal Â£) ... PASSED
            """,
        )

        run = qtest_results.parse_run(log, xml)

        self.assertEqual(
            [result.description for result in run.results],
            ["literal Ã¼", "literal Â£"],
        )

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

    def test_rejects_invalid_root_summary_counter(self) -> None:
        xml = _tmp(
            ".xml",
            """
            <qtest-results>
             <testsummary total-cases="not-an-integer" passes="0" failures="0"
              unexpected-passes="0" expected-failures="0"
              missing-cases="0" extra-cases="0"/>
            </qtest-results>
            """,
        )
        log = _tmp(".log", "")

        with self.assertRaisesRegex(qtest_results.ResultError, "invalid root summary"):
            qtest_results.parse_run(log, xml)

    def test_rejects_root_child_missing_case_counter_mismatch(self) -> None:
        xml = _xml(
            """
             <testsuite file="/repo/sample.test">
              <testcase testid="sample 1" description="ok" outcome="pass"/>
              <testsummary total-cases="1" passes="1" failures="0"
               unexpected-passes="0" expected-failures="0"
               missing-cases="1" extra-cases="0"/>
             </testsuite>
            """,
            total=1,
            passes=1,
            failures=0,
        )
        log = _tmp(".log", "sample  1 (ok) ... PASSED\n")

        with self.assertRaisesRegex(qtest_results.ResultError, "root missing cases"):
            qtest_results.parse_run(log, xml)

    def test_rejects_missing_root_summary(self) -> None:
        xml = _tmp(".xml", "<qtest-results/>")
        log = _tmp(".log", "")

        with self.assertRaisesRegex(qtest_results.ResultError, "invalid root summary"):
            qtest_results.parse_run(log, xml)

    def test_rejects_testsuite_without_file(self) -> None:
        xml = _xml(
            "<testsuite></testsuite>",
            total=0,
            passes=0,
            failures=0,
        )
        log = _tmp(".log", "")

        with self.assertRaisesRegex(qtest_results.ResultError, "invalid testsuite"):
            qtest_results.parse_run(log, xml)

    def test_rejects_multiple_child_summaries(self) -> None:
        xml = _xml(
            """
             <testsuite file="/repo/sample.test">
              <testsummary total-cases="0" passes="0" failures="0"
               unexpected-passes="0" expected-failures="0"
               missing-cases="0" extra-cases="0"/>
              <testsummary total-cases="0" passes="0" failures="0"
               unexpected-passes="0" expected-failures="0"
               missing-cases="0" extra-cases="0"/>
             </testsuite>
            """,
            total=0,
            passes=0,
            failures=0,
        )
        log = _tmp(".log", "")

        with self.assertRaisesRegex(qtest_results.ResultError, "child summary"):
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

    def test_excludes_exact_recorded_invalid_suite_partial_counts(self) -> None:
        invalid_counts = {
            "c-api-key": 2,
            "completion": 0,
            "large-file": 20,
            "replace-input": 2,
            "writer-version": 4,
        }
        invalid_xml: list[str] = []
        invalid_log: list[str] = []
        for suite, count in invalid_counts.items():
            cases = []
            for ordinal in range(1, count + 1):
                cases.append(
                    f'<testcase testid="{suite} {ordinal}" '
                    f'description="partial {ordinal}" outcome="fail"/>'
                )
                invalid_log.append(
                    f"{suite} test {ordinal} (partial {ordinal}) FAILED"
                )
            invalid_xml.append(
                f'<testsuite file="/repo/{suite}.test">\n'
                f"{'\n'.join(cases)}\n"
                "</testsuite>"
            )
        xml = _xml(
            """
             <testsuite file="/repo/valid.test">
              <testcase testid="valid 1" description="kept" outcome="pass"/>
              <testsummary total-cases="1" passes="1" failures="0"
               unexpected-passes="0" expected-failures="0"
               missing-cases="0" extra-cases="0"/>
             </testsuite>
            """
            + "\n".join(invalid_xml),
            total=1,
            passes=1,
            failures=0,
        )
        log = _tmp(
            ".log",
            "valid  1 (kept) ... PASSED\n" + "\n".join(invalid_log) + "\n",
        )

        run = qtest_results.parse_run(log, xml)

        suites = ET.parse(xml).getroot().findall("testsuite")
        partial_counts = {
            Path(suite.attrib["file"]).stem: len(suite.findall("./testcase"))
            for suite in suites
            if suite.find("./testsummary") is None
        }
        self.assertEqual(
            partial_counts,
            {
                "c-api-key": 2,
                "completion": 0,
                "large-file": 20,
                "replace-input": 2,
                "writer-version": 4,
            },
        )
        self.assertEqual(sum(partial_counts.values()), 28)
        self.assertEqual([result.id for result in run.results], ["valid 1"])
        self.assertEqual(run.summary.total, 1)
        self.assertEqual(
            run.invalid_suites,
            (
                "c-api-key",
                "completion",
                "large-file",
                "replace-input",
                "writer-version",
            ),
        )

    def test_preserves_same_description_as_distinct_ordinal_identities(self) -> None:
        xml = _xml(
            """
             <testsuite file="/repo/duplicate-description.test">
              <testcase testid="duplicate-description 1"
               description="check output" outcome="pass"/>
              <testcase testid="duplicate-description 2"
               description="check output" outcome="fail"/>
              <testsummary total-cases="2" passes="1" failures="1"
               unexpected-passes="0" expected-failures="0"
               missing-cases="0" extra-cases="0"/>
             </testsuite>
            """,
            total=2,
            passes=1,
            failures=1,
        )
        log = _tmp(
            ".log",
            """
            duplicate-description  1 (check output) ... PASSED
            duplicate-description test 2 (check output) FAILED
            """,
        )

        run = qtest_results.parse_run(log, xml)

        self.assertEqual(
            [(result.id, result.description, result.outcome) for result in run.results],
            [
                (
                    "duplicate-description 1",
                    "check output",
                    qtest_results.Outcome.PASS,
                ),
                (
                    "duplicate-description 2",
                    "check output",
                    qtest_results.Outcome.FAIL,
                ),
            ],
        )
