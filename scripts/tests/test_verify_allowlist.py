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
        results = verify_allowlist.parse_log(log)
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
        results = verify_allowlist.parse_log(log)
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
        results = verify_allowlist.parse_log(log)
        self.assertEqual(len(results), 3)
        self.assertEqual([r.passed for r in results], [True, False, True])

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
        results = verify_allowlist.parse_log(log)
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


if __name__ == "__main__":
    unittest.main()
