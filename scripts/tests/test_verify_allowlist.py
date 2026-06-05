"""Unit tests for scripts/verify-allowlist.py.

Run with: python3 -m unittest scripts/tests/test_verify_allowlist.py
"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_VERIFY_PATH = _HERE.parent / "verify-allowlist.py"

_spec = importlib.util.spec_from_file_location("verify_allowlist", _VERIFY_PATH)
assert _spec and _spec.loader, f"cannot load {_VERIFY_PATH}"
verify_allowlist = importlib.util.module_from_spec(_spec)
sys.modules["verify_allowlist"] = verify_allowlist
_spec.loader.exec_module(verify_allowlist)


def _tmp(content: str) -> Path:
    f = tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False, encoding="utf-8"
    )
    f.write(textwrap.dedent(content))
    f.close()
    return Path(f.name)


class ParseLogTest(unittest.TestCase):
    def test_columnar_pass_format(self) -> None:
        log = _tmp(
            """
            Running vendor/qpdf-qtest/arg-parsing.test
            arg-parsing  1 (required argument)                             ... PASSED
            arg-parsing  2 (required argument with choices)                ... PASSED
            """
        )
        results, _total = verify_allowlist.parse_log(log)
        self.assertEqual(len(results), 2)
        self.assertTrue(all(r.passed for r in results))
        self.assertEqual(results[0].test, "arg-parsing")
        self.assertEqual(results[0].subtest, "required argument")

    def test_testlog_fail_format(self) -> None:
        log = _tmp(
            """
            Running vendor/qpdf-qtest/deterministic-id.test
            deterministic-id test 1 (deterministic ID: linearize/ostream=nn) FAILED
            cwd: /tmp/whatever
            command: qpdf -deterministic-id ...
            deterministic-id test 2 (compare files) FAILED
            """
        )
        results, _total = verify_allowlist.parse_log(log)
        self.assertEqual(len(results), 2)
        self.assertTrue(all(not r.passed for r in results))
        self.assertEqual(results[0].subtest, "deterministic ID: linearize/ostream=nn")
        self.assertEqual(results[1].subtest, "compare files")

    def test_mixed_pass_and_fail(self) -> None:
        log = _tmp(
            """
            arg-parsing  1 (required argument)                  ... PASSED
            deterministic-id test 1 (compare files) FAILED
            arg-parsing  2 (required argument with choices)     ... PASSED
            """
        )
        results, _total = verify_allowlist.parse_log(log)
        self.assertEqual(len(results), 3)
        self.assertEqual([r.passed for r in results], [True, False, True])

    def test_columnar_expected_failure_format(self) -> None:
        log = _tmp(
            """
            split-pages 14 (check output (01-10))                          ... FAILED (exp)
            split-pages 15 (check output (11-20))                          ... FAILED (exp)

            TESTS COMPLETE.  Summary:
            Total tests: 2
            Expected Failures: 2
            """
        )
        results, total = verify_allowlist.parse_log(log)
        self.assertEqual(len(results), 2)
        self.assertEqual(total, 2)
        self.assertTrue(all(not r.passed for r in results))
        self.assertEqual(results[0].subtest, "check output (01-10)")

    def test_parses_total_summary(self) -> None:
        log = _tmp(
            """
            arg-parsing  1 (a)                  ... PASSED
            arg-parsing  2 (b)                  ... PASSED

            TESTS COMPLETE.  Summary:
            Total tests: 2
            Passes: 2
            Failures: 0
            """
        )
        results, total = verify_allowlist.parse_log(log)
        self.assertEqual(len(results), 2)
        self.assertEqual(total, 2)

    def test_total_summary_missing_returns_none(self) -> None:
        log = _tmp("""
            arg-parsing  1 (a)                  ... PASSED
            """)
        _results, total = verify_allowlist.parse_log(log)
        self.assertIsNone(total)

    def test_dedupes_repeated_lines(self) -> None:
        # qtest-driver can dump the same subtest header more than once when
        # both stdout status and testlog excerpt are interleaved.
        log = _tmp(
            """
            deterministic-id test 1 (compare files) FAILED
            cwd: /tmp/x
            deterministic-id test 1 (compare files) FAILED
            """
        )
        results, _total = verify_allowlist.parse_log(log)
        self.assertEqual(len(results), 1)


class JudgeTest(unittest.TestCase):
    def _judge(self, results, allowlist_text):
        al = _tmp(allowlist_text)
        entries = verify_allowlist.parse_allowlist(al)
        return verify_allowlist.judge(results, entries)

    def test_empty_allowlist_with_failures_is_ok(self) -> None:
        results = [
            verify_allowlist.Result("arg-parsing", "x", False),
        ]
        exit_code, summary = self._judge(results, "")
        self.assertEqual(exit_code, 0)
        self.assertIn("Informational fails", summary)

    def test_allowlist_pass(self) -> None:
        results = [
            verify_allowlist.Result("arg-parsing", "required argument", True),
        ]
        exit_code, summary = self._judge(
            results, "arg-parsing:required argument\n"
        )
        self.assertEqual(exit_code, 0)
        self.assertIn("Expected pass (allowlist PASS): **1**", summary)

    def test_allowlist_fail_is_regression(self) -> None:
        results = [
            verify_allowlist.Result("arg-parsing", "required argument", False),
        ]
        exit_code, _ = self._judge(
            results, "arg-parsing:required argument\n"
        )
        self.assertEqual(exit_code, 1)

    def test_missing_allowlist_entry_fails(self) -> None:
        # Allowlist entry never appeared in results — typo / upstream rename.
        results = [
            verify_allowlist.Result("arg-parsing", "something else", True),
        ]
        exit_code, summary = self._judge(
            results, "arg-parsing:gone\n"
        )
        self.assertEqual(exit_code, 1)
        self.assertIn("Missing", summary)

    def test_whole_file_allowlist(self) -> None:
        results = [
            verify_allowlist.Result("arg-parsing", "a", True),
            verify_allowlist.Result("arg-parsing", "b", True),
        ]
        exit_code, summary = self._judge(results, "arg-parsing\n")
        self.assertEqual(exit_code, 0)
        self.assertIn("Expected pass (allowlist PASS): **2**", summary)

    def test_unexpected_pass_is_candidate(self) -> None:
        results = [
            verify_allowlist.Result("arg-parsing", "surprise", True),
        ]
        exit_code, summary = self._judge(results, "")
        self.assertEqual(exit_code, 0)
        self.assertIn("Allowlist candidates", summary)


class HeadlineSummaryTest(unittest.TestCase):
    """The headline summary (include_candidates=False) drops only the long
    enumerated candidate list — everything that flips the verdict stays."""

    def _judge(self, results, allowlist_text, **kw):
        al = _tmp(allowlist_text)
        entries = verify_allowlist.parse_allowlist(al)
        return verify_allowlist.judge(results, entries, **kw)

    def test_headline_omits_candidate_list_but_keeps_count(self) -> None:
        results = [
            verify_allowlist.Result("arg-parsing", "surprise", True),
        ]
        _exit, summary = self._judge(results, "", include_candidates=False)
        # The headline count line is still present...
        self.assertIn("Allowlist-candidates (non-allowlist PASS): **1**", summary)
        # ...but the long enumerated section and its entries are dropped.
        self.assertNotIn("## Allowlist candidates", summary)
        self.assertNotIn("arg-parsing:surprise", summary)

    def test_headline_keeps_regressions_and_verdict(self) -> None:
        results = [verify_allowlist.Result("arg-parsing", "req", False)]
        exit_code, summary = self._judge(
            results, "arg-parsing:req\n", include_candidates=False
        )
        self.assertEqual(exit_code, 1)
        self.assertIn("## Regressions", summary)
        self.assertIn("arg-parsing:req", summary)
        self.assertIn("**Verdict: FAIL**", summary)

    def test_headline_keeps_missing(self) -> None:
        results = [verify_allowlist.Result("arg-parsing", "other", True)]
        _exit, summary = self._judge(
            results, "arg-parsing:gone\n", include_candidates=False
        )
        self.assertIn("## Missing", summary)
        self.assertIn("arg-parsing:gone", summary)

    def test_full_summary_default_keeps_candidates(self) -> None:
        # The default (full) summary is unchanged — candidates listed.
        results = [verify_allowlist.Result("arg-parsing", "surprise", True)]
        _exit, summary = self._judge(results, "")
        self.assertIn("## Allowlist candidates", summary)
        self.assertIn("arg-parsing:surprise", summary)


class StepSummaryFileTest(unittest.TestCase):
    """End-to-end main() behaviour for the --step-summary flag."""

    def _run(self, log_text, allowlist_text, *, prefill=""):
        log = _tmp(log_text)
        allowlist = _tmp(allowlist_text)
        full = _tmp("")
        step = _tmp(prefill)
        rc = verify_allowlist.main(
            [
                str(log),
                str(allowlist),
                "--summary",
                str(full),
                "--step-summary",
                str(step),
            ]
        )
        return rc, full.read_text(encoding="utf-8"), step.read_text(encoding="utf-8")

    def test_step_summary_is_headline_full_summary_is_complete(self) -> None:
        log = """
            arg-parsing  1 (surprise)                  ... PASSED
            arg-parsing test 2 (req) FAILED
            """
        _rc, full, step = self._run(log, "arg-parsing:req\n")
        # Full artifact keeps the long candidate list.
        self.assertIn("## Allowlist candidates", full)
        self.assertIn("arg-parsing:surprise", full)
        # Step summary is the headline: regression yes, candidate list no.
        self.assertIn("## Regressions", step)
        self.assertNotIn("## Allowlist candidates", step)
        self.assertNotIn("arg-parsing:surprise", step)

    def test_step_summary_appends_preserving_existing_content(self) -> None:
        log = "arg-parsing  1 (a)                  ... PASSED\n"
        _rc, _full, step = self._run(log, "", prefill="## previous step\n\n")
        self.assertIn("## previous step", step)
        self.assertIn("# qtest-summary", step)
        # Existing content comes first; appended, not overwritten.
        self.assertLess(step.index("## previous step"), step.index("# qtest-summary"))

    def test_step_summary_not_glued_to_existing_content_without_newline(
        self,
    ) -> None:
        # A prior step may have written content with no trailing newline.
        # The headline header must not glue onto that last line.
        log = "arg-parsing  1 (a)                  ... PASSED\n"
        _rc, _full, step = self._run(log, "", prefill="prior step output")
        self.assertNotIn("prior step output# qtest-summary", step)
        # The header begins on its own line.
        idx = step.index("# qtest-summary")
        self.assertEqual(step[idx - 1], "\n")

    def test_step_summary_inherits_drift_warning(self) -> None:
        # Total tests summary disagrees with the parsed count -> drift.
        log = """
            arg-parsing  1 (a)                  ... PASSED
            arg-parsing  2 (b)                  ... PASSED

            TESTS COMPLETE.  Summary:
            Total tests: 5
            """
        rc, full, step = self._run(log, "")
        self.assertEqual(rc, 1)
        self.assertIn("⚠️", step)
        self.assertIn("⚠️", full)


if __name__ == "__main__":
    unittest.main()
