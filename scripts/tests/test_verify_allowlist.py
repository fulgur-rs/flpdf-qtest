"""Unit tests for scripts/verify-allowlist.py.

Run with: python3 -m unittest scripts/tests/test_verify_allowlist.py
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import runpy
import sys
import tempfile
import textwrap
import unittest
from collections import defaultdict
from pathlib import Path
from unittest import mock
from xml.sax.saxutils import escape

_HERE = Path(__file__).resolve().parent
_QTEST_RESULTS_PATH = _HERE.parent / "qtest_results.py"
_VERIFY_PATH = _HERE.parent / "verify-allowlist.py"

_qtest_spec = importlib.util.spec_from_file_location("qtest_results", _QTEST_RESULTS_PATH)
assert _qtest_spec and _qtest_spec.loader, f"cannot load {_QTEST_RESULTS_PATH}"
qtest_results = importlib.util.module_from_spec(_qtest_spec)
sys.modules["qtest_results"] = qtest_results
_qtest_spec.loader.exec_module(qtest_results)

_verify_spec = importlib.util.spec_from_file_location("verify_allowlist", _VERIFY_PATH)
assert _verify_spec and _verify_spec.loader, f"cannot load {_VERIFY_PATH}"
verify_allowlist = importlib.util.module_from_spec(_verify_spec)
sys.modules["verify_allowlist"] = verify_allowlist
_verify_spec.loader.exec_module(verify_allowlist)


def _tmp(content: str, *, suffix: str = ".txt") -> Path:
    f = tempfile.NamedTemporaryFile(
        mode="w", suffix=suffix, delete=False, encoding="utf-8"
    )
    f.write(textwrap.dedent(content))
    f.close()
    return Path(f.name)


def _result(test: str, subtest: str, passed: bool, *, ordinal: int = 1):
    outcome = qtest_results.Outcome.PASS if passed else qtest_results.Outcome.FAIL
    return _outcome_result(test, subtest, outcome, ordinal=ordinal)


def _outcome_result(
    test: str,
    subtest: str,
    outcome: qtest_results.Outcome,
    *,
    ordinal: int = 1,
):
    return qtest_results.Result(
        suite=test,
        category=test,
        ordinal=ordinal,
        description=subtest,
        outcome=outcome,
    )


def _paired_artifacts(
    results: list[qtest_results.Result],
    *,
    invalid: list[qtest_results.Result] | None = None,
) -> tuple[Path, Path]:
    """Create matching qtest log/XML artifacts for one synthetic run."""
    invalid = invalid or []
    suites: dict[str, list[qtest_results.Result]] = defaultdict(list)
    for result in results:
        suites[result.suite].append(result)

    def counters(cases: list[qtest_results.Result]) -> tuple[int, int, int, int, int]:
        return (
            len(cases),
            sum(case.outcome is qtest_results.Outcome.PASS for case in cases),
            sum(case.outcome is qtest_results.Outcome.FAIL for case in cases),
            sum(
                case.outcome is qtest_results.Outcome.UNEXPECTED_PASS
                for case in cases
            ),
            sum(
                case.outcome is qtest_results.Outcome.EXPECTED_FAIL
                for case in cases
            ),
        )

    def summary(cases: list[qtest_results.Result]) -> str:
        total, passes, failures, xpasses, xfails = counters(cases)
        return (
            f'<testsummary total-cases="{total}" passes="{passes}" '
            f'failures="{failures}" unexpected-passes="{xpasses}" '
            f'expected-failures="{xfails}" missing-cases="0" extra-cases="0"/>'
        )

    xml_suites: list[str] = []
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
    for case in invalid:
        xml_suites.append(
            f'<testsuite file="/repo/{escape(case.suite)}.test">\n'
            f'<testcase testid="{escape(case.id)}" '
            f'description="{escape(case.description)}" outcome="pass"/>\n'
            "</testsuite>"
        )

    root_summary = summary(results)
    xml = _tmp(
        "<?xml version=\"1.0\"?>\n<qtest-results version=\"1\">\n"
        f"{'\n'.join(xml_suites)}\n{root_summary}\n</qtest-results>\n",
        suffix=".xml",
    )
    log_lines = []
    for case in [*results, *invalid]:
        status = {
            qtest_results.Outcome.PASS: "PASSED",
            qtest_results.Outcome.FAIL: "FAILED",
            qtest_results.Outcome.UNEXPECTED_PASS: "PASSED-UNEXP",
            qtest_results.Outcome.EXPECTED_FAIL: "FAILED (exp)",
        }[case.outcome]
        infix = " test" if not case.passed else ""
        log_lines.append(
            f"{case.category}{infix} {case.ordinal} ({case.description}) ... {status}"
        )
    return _tmp("\n".join(log_lines) + "\n", suffix=".log"), xml


class JudgeTest(unittest.TestCase):
    def _judge(self, results, allowlist_text, **kw):
        entries = verify_allowlist.parse_allowlist(_tmp(allowlist_text))
        return verify_allowlist.judge(results, entries, **kw)

    def test_empty_allowlist_with_failures_is_ok(self) -> None:
        exit_code, summary = self._judge([_result("arg-parsing", "x", False)], "")
        self.assertEqual(exit_code, 0)
        self.assertIn("Informational fails", summary)

    def test_allowlist_pass(self) -> None:
        exit_code, summary = self._judge(
            [_result("arg-parsing", "required argument", True)],
            "arg-parsing:required argument\n",
        )
        self.assertEqual(exit_code, 0)
        self.assertIn("Expected pass (allowlist PASS): **1**", summary)

    def test_allowlist_fail_is_regression_verdict_only(self) -> None:
        exit_code, summary = self._judge(
            [_result("arg-parsing", "required argument", False)],
            "arg-parsing:required argument\n",
        )
        self.assertEqual(exit_code, 0)
        self.assertIn("**Verdict: FAIL**", summary)
        self.assertIn("## Regressions", summary)
        self.assertIn("arg-parsing:required argument", summary)

    def test_missing_allowlist_entry_flags_verdict_fail(self) -> None:
        exit_code, summary = self._judge(
            [_result("arg-parsing", "something else", True)], "arg-parsing:gone\n"
        )
        self.assertEqual(exit_code, 0)
        self.assertIn("**Verdict: FAIL**", summary)
        self.assertIn("arg-parsing:gone", summary)

    def test_whole_file_allowlist(self) -> None:
        exit_code, summary = self._judge(
            [_result("arg-parsing", "a", True), _result("arg-parsing", "b", True, ordinal=2)],
            "arg-parsing\n",
        )
        self.assertEqual(exit_code, 0)
        self.assertIn("Expected pass (allowlist PASS): **2**", summary)

    def test_allowlist_parser_ignores_blank_and_comment_only_lines(self) -> None:
        entries = verify_allowlist.parse_allowlist(
            _tmp(
                """
                # comment

                arg-parsing:required argument  # trailing comment
                """
            )
        )

        self.assertEqual(
            entries,
            [
                verify_allowlist.AllowlistEntry(
                    test="arg-parsing",
                    subtest="required argument",
                )
            ],
        )

    def test_unexpected_pass_is_candidate(self) -> None:
        exit_code, summary = self._judge([_result("arg-parsing", "surprise", True)], "")
        self.assertEqual(exit_code, 0)
        self.assertIn("Allowlist candidates", summary)

    def test_allowlisted_unexpected_pass_is_expected_pass(self) -> None:
        exit_code, summary = self._judge(
            [
                _outcome_result(
                    "arg-parsing",
                    "unexpected",
                    qtest_results.Outcome.UNEXPECTED_PASS,
                )
            ],
            "arg-parsing:unexpected\n",
        )
        self.assertEqual(exit_code, 0)
        self.assertIn("Expected pass (allowlist PASS): **1**", summary)
        self.assertIn("Regressions (allowlist FAIL)**: **0**", summary)

    def test_allowlisted_expected_failure_is_regression(self) -> None:
        exit_code, summary = self._judge(
            [
                _outcome_result(
                    "arg-parsing",
                    "expected failure",
                    qtest_results.Outcome.EXPECTED_FAIL,
                )
            ],
            "arg-parsing:expected failure\n",
        )
        self.assertEqual(exit_code, 0)
        self.assertIn("Expected pass (allowlist PASS): **0**", summary)
        self.assertIn("Regressions (allowlist FAIL)**: **1**", summary)

    def test_non_allowlisted_unexpected_pass_is_candidate(self) -> None:
        exit_code, summary = self._judge(
            [
                _outcome_result(
                    "arg-parsing",
                    "unexpected",
                    qtest_results.Outcome.UNEXPECTED_PASS,
                )
            ],
            "",
        )
        self.assertEqual(exit_code, 0)
        self.assertIn("Allowlist-candidates (non-allowlist PASS): **1**", summary)
        self.assertIn("Informational fails (non-allowlist FAIL): **0**", summary)

    def test_non_allowlisted_expected_failure_is_informational(self) -> None:
        exit_code, summary = self._judge(
            [
                _outcome_result(
                    "arg-parsing",
                    "expected failure",
                    qtest_results.Outcome.EXPECTED_FAIL,
                )
            ],
            "",
        )
        self.assertEqual(exit_code, 0)
        self.assertIn("Allowlist-candidates (non-allowlist PASS): **0**", summary)
        self.assertIn("Informational fails (non-allowlist FAIL): **1**", summary)


class HeadlineSummaryTest(unittest.TestCase):
    def _judge(self, results, allowlist_text, **kw):
        entries = verify_allowlist.parse_allowlist(_tmp(allowlist_text))
        return verify_allowlist.judge(results, entries, **kw)

    def test_headline_omits_candidate_list_but_keeps_count(self) -> None:
        _exit, summary = self._judge(
            [_result("arg-parsing", "surprise", True)], "", include_candidates=False
        )
        self.assertIn("Allowlist-candidates (non-allowlist PASS): **1**", summary)
        self.assertNotIn("## Allowlist candidates", summary)
        self.assertNotIn("arg-parsing:surprise", summary)

    def test_headline_keeps_regressions_and_verdict(self) -> None:
        exit_code, summary = self._judge(
            [_result("arg-parsing", "req", False)],
            "arg-parsing:req\n",
            include_candidates=False,
        )
        self.assertEqual(exit_code, 0)
        self.assertIn("## Regressions", summary)
        self.assertIn("arg-parsing:req", summary)
        self.assertIn("**Verdict: FAIL**", summary)

    def test_headline_keeps_missing(self) -> None:
        _exit, summary = self._judge(
            [_result("arg-parsing", "other", True)],
            "arg-parsing:gone\n",
            include_candidates=False,
        )
        self.assertIn("## Missing", summary)
        self.assertIn("arg-parsing:gone", summary)

    def test_full_summary_default_keeps_candidates(self) -> None:
        _exit, summary = self._judge([_result("arg-parsing", "surprise", True)], "")
        self.assertIn("## Allowlist candidates", summary)
        self.assertIn("arg-parsing:surprise", summary)
        self.assertTrue(summary.endswith("\n"))


class MainTest(unittest.TestCase):
    """End-to-end behaviour using paired harness artifacts."""

    def _run(self, results, allowlist_text, *, prefill="", invalid=None, **flags):
        log, xml = _paired_artifacts(results, invalid=invalid)
        allowlist = _tmp(allowlist_text)
        full = _tmp("")
        step = _tmp(prefill)
        metrics = _tmp(prefill)
        argv = [str(log), str(xml), str(allowlist), "--summary", str(full)]
        if flags.pop("step_summary", False):
            argv += ["--step-summary", str(step)]
        if flags.pop("metrics", False):
            argv += ["--metrics", str(metrics)]
        for key, value in flags.items():
            argv += [f"--{key.replace('_', '-')}", value]
        with contextlib.redirect_stdout(io.StringIO()):
            rc = verify_allowlist.main(argv)
        return (
            rc,
            full.read_text(encoding="utf-8"),
            step.read_text(encoding="utf-8"),
            metrics.read_text(encoding="utf-8"),
        )

    def test_invalid_suite_allowlist_entry_is_missing(self) -> None:
        # A suite without its own summary is excluded by the shared parser.
        # The old log-only parser incorrectly accepted this as a passing entry.
        _rc, full, _step, _metrics = self._run(
            [_result("valid", "kept", True)],
            "invalid:partial\n",
            invalid=[_result("invalid", "partial", True)],
        )
        self.assertIn("## Missing", full)
        self.assertIn("invalid:partial", full)
        self.assertIn("**Verdict: FAIL**", full)

    def test_zero_authoritative_results_is_an_operational_error(self) -> None:
        log, xml = _paired_artifacts(
            [],
            invalid=[_result("invalid", "partial", True)],
        )
        allowlist = _tmp("invalid:partial\n")
        summary = _tmp("")
        metrics = _tmp("")
        stdout = io.StringIO()
        stderr = io.StringIO()

        with (
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            rc = verify_allowlist.main(
                [
                    str(log),
                    str(xml),
                    str(allowlist),
                    "--summary",
                    str(summary),
                    "--metrics",
                    str(metrics),
                ]
            )

        self.assertEqual(rc, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn(
            "verify-allowlist: no authoritative subtest results",
            stderr.getvalue(),
        )
        self.assertEqual(summary.read_text(encoding="utf-8"), "")
        self.assertEqual(metrics.read_text(encoding="utf-8"), "")

    def test_parser_error_is_prefixed_and_does_not_emit_summary(self) -> None:
        log = _tmp("sample 1 (ok) ... PASSED\n", suffix=".log")
        xml = _tmp("<not-results/>", suffix=".xml")
        allowlist = _tmp("")
        summary = _tmp("")
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            rc = verify_allowlist.main(
                [str(log), str(xml), str(allowlist), "--summary", str(summary)]
            )
        self.assertEqual(rc, 1)
        self.assertIn("verify-allowlist: malformed XML", stderr.getvalue())
        self.assertEqual(summary.read_text(encoding="utf-8"), "")

    def test_missing_input_artifacts_return_argument_io_error(self) -> None:
        log, xml = _paired_artifacts([_result("sample", "ok", True)])
        allowlist = _tmp("sample:ok\n")
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing"
            cases = (
                ([missing, xml, allowlist], "log not found"),
                ([log, missing, allowlist], "XML not found"),
                ([log, xml, missing], "allowlist not found"),
            )
            for paths, diagnostic in cases:
                with self.subTest(diagnostic=diagnostic):
                    stdout = io.StringIO()
                    stderr = io.StringIO()
                    with (
                        contextlib.redirect_stdout(stdout),
                        contextlib.redirect_stderr(stderr),
                    ):
                        rc = verify_allowlist.main([str(path) for path in paths])
                    self.assertEqual(rc, 2)
                    self.assertEqual(stdout.getvalue(), "")
                    self.assertIn(f"verify-allowlist: {diagnostic}", stderr.getvalue())

    def test_script_entrypoint_exits_with_main_result(self) -> None:
        log, xml = _paired_artifacts([_result("sample", "ok", True)])
        allowlist = _tmp("sample:ok\n")

        with (
            mock.patch.object(
                sys,
                "argv",
                [str(_VERIFY_PATH), str(log), str(xml), str(allowlist)],
            ),
            contextlib.redirect_stdout(io.StringIO()),
            self.assertRaises(SystemExit) as raised,
        ):
            runpy.run_path(str(_VERIFY_PATH), run_name="__main__")

        self.assertEqual(raised.exception.code, 0)

    def test_summary_help_matches_operational_error_contract(self) -> None:
        stdout = io.StringIO()

        with (
            contextlib.redirect_stdout(stdout),
            self.assertRaises(SystemExit) as raised,
        ):
            verify_allowlist.main(["--help"])

        help_text = " ".join(stdout.getvalue().split())
        self.assertEqual(raised.exception.code, 0)
        self.assertIn(
            "write the full Markdown summary after successful result "
            "reconciliation, including soft policy FAIL verdicts",
            help_text,
        )
        self.assertIn(
            "not written on operational parser errors",
            help_text,
        )
        self.assertNotIn("always written", help_text)

    def test_step_summary_is_headline_full_summary_is_complete(self) -> None:
        _rc, full, step, _metrics = self._run(
            [_result("arg-parsing", "surprise", True), _result("arg-parsing", "req", False, ordinal=2)],
            "arg-parsing:req\n",
            step_summary=True,
        )
        self.assertIn("## Allowlist candidates", full)
        self.assertIn("arg-parsing:surprise", full)
        self.assertIn("## Regressions", step)
        self.assertNotIn("## Allowlist candidates", step)
        self.assertNotIn("arg-parsing:surprise", step)

    def test_step_summary_appends_preserving_existing_content(self) -> None:
        _rc, _full, step, _metrics = self._run(
            [_result("arg-parsing", "a", True)],
            "",
            prefill="## previous step\n\n",
            step_summary=True,
        )
        self.assertIn("## previous step", step)
        self.assertIn("# qtest-summary", step)
        self.assertLess(step.index("## previous step"), step.index("# qtest-summary"))

    def test_step_summary_not_glued_to_existing_content_without_newline(self) -> None:
        _rc, _full, step, _metrics = self._run(
            [_result("arg-parsing", "a", True)],
            "",
            prefill="prior step output",
            step_summary=True,
        )
        self.assertNotIn("prior step output# qtest-summary", step)
        self.assertEqual(step[step.index("# qtest-summary") - 1], "\n")

    def test_metrics_flag_appends_one_json_line(self) -> None:
        _rc, _full, _step, text = self._run(
            [_result("arg-parsing", "ok", True)],
            "arg-parsing:ok\n",
            metrics=True,
            commit="deadbeef",
            timestamp="2026-06-05T12:00:00Z",
        )
        lines = [line for line in text.splitlines() if line.strip()]
        self.assertEqual(len(lines), 1)
        obj = json.loads(lines[0])
        self.assertEqual(obj["flpdf_commit"], "deadbeef")
        self.assertEqual(obj["timestamp"], "2026-06-05T12:00:00Z")
        self.assertEqual(obj["total"], 1)
        self.assertEqual(obj["expected_pass"], 1)
        self.assertFalse(obj["drift"])
        self.assertEqual(obj["verdict"], "OK")

    def test_metrics_flag_appends_to_existing_history(self) -> None:
        _rc, _full, _step, text = self._run(
            [_result("arg-parsing", "ok", True)],
            "arg-parsing:ok\n",
            prefill='{"old": "record"}\n',
            metrics=True,
            commit="c",
            timestamp="t",
        )
        lines = [line for line in text.splitlines() if line.strip()]
        self.assertEqual(len(lines), 2)
        self.assertEqual(json.loads(lines[0]), {"old": "record"})
        self.assertEqual(json.loads(lines[1])["flpdf_commit"], "c")


class MetricsRecordTest(unittest.TestCase):
    def _metrics(self, results, allowlist_text, **kw):
        entries = verify_allowlist.parse_allowlist(_tmp(allowlist_text))
        return verify_allowlist.build_metrics(results, entries, **kw)

    def test_record_has_counts_and_metadata(self) -> None:
        metrics = self._metrics(
            [
                _result("arg-parsing", "ok", True),
                _result("arg-parsing", "bad", False, ordinal=2),
                _result("other", "cand", True),
                _result("other", "info", False, ordinal=2),
            ],
            "arg-parsing:ok\narg-parsing:bad\n",
            commit="abc123",
            timestamp="2026-06-05T00:00:00Z",
        )
        self.assertEqual(metrics["flpdf_commit"], "abc123")
        self.assertEqual(metrics["timestamp"], "2026-06-05T00:00:00Z")
        self.assertEqual(metrics["total"], 4)
        self.assertEqual(metrics["allowlist"], 2)
        self.assertEqual(metrics["expected_pass"], 1)
        self.assertEqual(metrics["regressions"], 1)
        self.assertEqual(metrics["missing"], 0)
        self.assertEqual(metrics["candidates"], 1)
        self.assertEqual(metrics["informational"], 1)
        self.assertFalse(metrics["drift"])
        self.assertEqual(metrics["verdict"], "FAIL")

    def test_missing_entry_makes_verdict_fail(self) -> None:
        metrics = self._metrics(
            [_result("arg-parsing", "present", True)],
            "arg-parsing:gone\n",
            commit="x",
            timestamp="t",
        )
        self.assertEqual(metrics["missing"], 1)
        self.assertEqual(metrics["verdict"], "FAIL")

    def test_allowlisted_unexpected_pass_is_expected_pass(self) -> None:
        metrics = self._metrics(
            [
                _outcome_result(
                    "arg-parsing",
                    "unexpected",
                    qtest_results.Outcome.UNEXPECTED_PASS,
                )
            ],
            "arg-parsing:unexpected\n",
            commit="x",
            timestamp="t",
        )
        self.assertEqual(metrics["expected_pass"], 1)
        self.assertEqual(metrics["regressions"], 0)

    def test_allowlisted_expected_failure_is_regression(self) -> None:
        metrics = self._metrics(
            [
                _outcome_result(
                    "arg-parsing",
                    "expected failure",
                    qtest_results.Outcome.EXPECTED_FAIL,
                )
            ],
            "arg-parsing:expected failure\n",
            commit="x",
            timestamp="t",
        )
        self.assertEqual(metrics["expected_pass"], 0)
        self.assertEqual(metrics["regressions"], 1)

    def test_non_allowlisted_unexpected_pass_is_candidate(self) -> None:
        metrics = self._metrics(
            [
                _outcome_result(
                    "arg-parsing",
                    "unexpected",
                    qtest_results.Outcome.UNEXPECTED_PASS,
                )
            ],
            "",
            commit="x",
            timestamp="t",
        )
        self.assertEqual(metrics["candidates"], 1)
        self.assertEqual(metrics["informational"], 0)

    def test_non_allowlisted_expected_failure_is_informational(self) -> None:
        metrics = self._metrics(
            [
                _outcome_result(
                    "arg-parsing",
                    "expected failure",
                    qtest_results.Outcome.EXPECTED_FAIL,
                )
            ],
            "",
            commit="x",
            timestamp="t",
        )
        self.assertEqual(metrics["candidates"], 0)
        self.assertEqual(metrics["informational"], 1)


if __name__ == "__main__":
    unittest.main()
