"""Execution tests for qtest runner artifact freshness and failure paths."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


_ROOT = Path(__file__).parents[2]
_SENTINEL = "PRESEEDED SUCCESS SENTINEL\n"
_GENERATED = (
    "qtest.log",
    "qtest-results.xml",
    "TEST-qtest.xml",
    "qtest-summary.md",
    "qtest-metrics.jsonl",
    "qtest-parity-summary.md",
)


class RunExecutionTest(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.repo = Path(self._temporary.name)
        (self.repo / "scripts").mkdir()
        (self.repo / "shim").mkdir()
        (self.repo / "vendor" / "qtest" / "bin").mkdir(parents=True)
        (self.repo / "vendor" / "qpdf-qtest").mkdir(parents=True)
        for name in (
            "run.sh",
            "verify-allowlist.py",
            "verify-parity-manifest.py",
            "qtest_results.py",
        ):
            shutil.copy2(_ROOT / "scripts" / name, self.repo / "scripts" / name)
        (self.repo / "allowlist.txt").write_text(
            "valid:kept\n",
            encoding="utf-8",
        )
        (self.repo / "parity").mkdir()
        (self.repo / "parity" / "qtest-11.9.0.jsonl").write_text(
            '{"id":"valid 1","suite":"valid","category":"valid",'
            '"ordinal":1,"description":"kept","state":"passing",'
            '"rationale":null,"owner":null,"bead":null,'
            '"replacement_ref":null}\n',
            encoding="utf-8",
        )
        (self.repo / "vendor" / "qpdf-qtest" / "valid.test").touch()

        fake_binary = self.repo / "fake-flpdf"
        fake_binary.write_text("#!/usr/bin/env sh\nexit 0\n", encoding="utf-8")
        fake_binary.chmod(fake_binary.stat().st_mode | stat.S_IXUSR)
        self.fake_binary = fake_binary

        driver = self.repo / "vendor" / "qtest" / "bin" / "qtest-driver"
        driver.write_text(
            textwrap.dedent(
                r"""
                use strict;
                use warnings;

                if (grep { $_ eq "--version" } @ARGV) {
                    print "qtest-driver fake 1.0\n";
                    exit 0;
                }

                my $mode = $ENV{"FAKE_QTEST_MODE"} // "";
                if ($mode eq "missing-xml") {
                    print "driver stopped before XML\n";
                    exit 1;
                }

                open my $qtest_log, ">", "qtest.log" or die $!;
                print {$qtest_log} "fresh qtest log: $mode\n";
                close $qtest_log;

                open my $junit, ">", "TEST-qtest.xml" or die $!;
                print {$junit} "fresh junit\n";
                close $junit;

                open my $xml, ">", "qtest-results.xml" or die $!;
                if ($mode eq "malformed-xml") {
                    print {$xml} "<qtest-results>";
                    print "valid  1 (kept) ... PASSED\n";
                } elsif ($mode eq "invalid-only") {
                    print {$xml} <<'XML';
                <?xml version="1.0"?>
                <qtest-results version="1">
                 <testsuite file="/repo/invalid.test">
                  <testcase testid="invalid 1" description="partial" outcome="fail"/>
                 </testsuite>
                 <testsummary total-cases="0" passes="0" failures="0"
                  unexpected-passes="0" expected-failures="0"
                  missing-cases="0" extra-cases="0"/>
                </qtest-results>
                XML
                    print "invalid test 1 (partial) FAILED\n";
                } elsif ($mode eq "valid-only") {
                    print {$xml} <<'XML';
                <?xml version="1.0"?>
                <qtest-results version="1">
                 <testsuite file="/repo/valid.test">
                  <testcase testid="valid 1" description="kept" outcome="pass"/>
                  <testsummary total-cases="1" passes="1" failures="0"
                   unexpected-passes="0" expected-failures="0"
                   missing-cases="0" extra-cases="0"/>
                 </testsuite>
                 <testsummary total-cases="1" passes="1" failures="0"
                  unexpected-passes="0" expected-failures="0"
                  missing-cases="0" extra-cases="0"/>
                </qtest-results>
                XML
                    print "valid  1 (kept) ... PASSED\n";
                } else {
                    die "unexpected FAKE_QTEST_MODE: $mode";
                }
                close $xml;
                exit 0;
                """
            ).lstrip(),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def _preseed(self) -> None:
        for name in _GENERATED:
            (self.repo / name).write_text(_SENTINEL, encoding="utf-8")

    def _run(
        self,
        mode: str,
        *,
        empty: bool = False,
        full: bool = False,
        step_summary: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        self._preseed()
        if empty:
            (self.repo / "allowlist.txt").write_text("", encoding="utf-8")
        env = os.environ.copy()
        env.update(
            {
                "FLPDF_CLI_BIN": str(self.fake_binary),
                "FLPDF_TEST_COMPARE_BIN": str(self.fake_binary),
                "FLPDF_TEST_DRIVER_BIN": str(self.fake_binary),
                "FAKE_QTEST_MODE": mode,
            }
        )
        if full:
            env["QTEST_FULL"] = "1"
        if step_summary is not None:
            env["GITHUB_STEP_SUMMARY"] = str(step_summary)
        return subprocess.run(
            [str(self.repo / "scripts" / "run.sh")],
            cwd=self.repo,
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )

    def _assert_no_sentinel(self) -> None:
        for name in _GENERATED:
            path = self.repo / name
            if path.exists():
                self.assertNotIn(_SENTINEL.strip(), path.read_text(encoding="utf-8"))

    def test_missing_xml_cannot_reuse_preseeded_success_artifacts(self) -> None:
        completed = self._run("missing-xml", full=True)

        self.assertEqual(completed.returncode, 1)
        self.assertIn("qtest results XML not found", completed.stderr)
        self.assertIn(
            "driver stopped before XML",
            (self.repo / "harness.log").read_text(encoding="utf-8"),
        )
        self.assertFalse((self.repo / "qtest.log").exists())
        self.assertFalse((self.repo / "qtest-results.xml").exists())
        self.assertFalse((self.repo / "TEST-qtest.xml").exists())
        self.assertFalse((self.repo / "qtest-summary.md").exists())
        self.assertFalse((self.repo / "qtest-metrics.jsonl").exists())
        self._assert_no_sentinel()

    def test_parser_error_retains_current_failure_xml_without_stale_outputs(self) -> None:
        completed = self._run("malformed-xml", full=True)

        self.assertEqual(completed.returncode, 1)
        self.assertIn("verify-allowlist: malformed XML", completed.stderr)
        self.assertEqual(
            (self.repo / "qtest-results.xml").read_text(encoding="utf-8"),
            "<qtest-results>",
        )
        self.assertEqual(
            (self.repo / "TEST-qtest.xml").read_text(encoding="utf-8"),
            "fresh junit\n",
        )
        self.assertEqual(
            (self.repo / "qtest.log").read_text(encoding="utf-8"),
            "fresh qtest log: malformed-xml\n",
        )
        self.assertFalse((self.repo / "qtest-summary.md").exists())
        metrics = self.repo / "qtest-metrics.jsonl"
        self.assertTrue(not metrics.exists() or metrics.read_text(encoding="utf-8") == "")
        self._assert_no_sentinel()

    def test_empty_dry_run_replaces_preseeded_outputs_with_current_state(self) -> None:
        completed = self._run("unused", empty=True)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("Verdict: OK (empty allowlist)", completed.stdout)
        self.assertIn(
            "Verdict: OK (empty allowlist)",
            (self.repo / "qtest-summary.md").read_text(encoding="utf-8"),
        )
        self.assertFalse((self.repo / "qtest.log").exists())
        self.assertFalse((self.repo / "qtest-results.xml").exists())
        self.assertFalse((self.repo / "TEST-qtest.xml").exists())
        self.assertFalse((self.repo / "qtest-metrics.jsonl").exists())
        self.assertFalse((self.repo / "qtest-parity-summary.md").exists())
        self._assert_no_sentinel()

    def test_full_survey_writes_fresh_parity_and_step_summaries(self) -> None:
        step_summary = self.repo / "step-summary.md"
        step_summary.write_text(_SENTINEL, encoding="utf-8")

        completed = self._run(
            "valid-only", full=True, step_summary=step_summary
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        parity = (self.repo / "qtest-parity-summary.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("# qtest parity manifest", parity)
        self.assertIn("**Verdict: OK**", parity)
        summary = step_summary.read_text(encoding="utf-8")
        self.assertIn(_SENTINEL, summary)
        self.assertIn("# qtest-summary", summary)
        self.assertIn("# qtest parity manifest", summary)
        self._assert_no_sentinel()

    def test_partial_survey_fails_before_driver_or_manifest_validation(self) -> None:
        completed = self._run("valid-only")

        self.assertEqual(completed.returncode, 2)
        self.assertIn(
            "parity manifest validation requires QTEST_FULL=1", completed.stderr
        )
        self.assertEqual((self.repo / "harness.log").read_text(encoding="utf-8"), "")
        self._assert_no_sentinel()

    def test_manifest_operational_error_propagates_without_stale_summary(self) -> None:
        (self.repo / "parity" / "qtest-11.9.0.jsonl").write_text(
            "not json\n", encoding="utf-8"
        )

        completed = self._run("valid-only", full=True)

        self.assertEqual(completed.returncode, 1)
        self.assertIn("verify-parity-manifest:", completed.stderr)
        self.assertFalse((self.repo / "qtest-parity-summary.md").exists())
        self._assert_no_sentinel()

    def test_invalid_only_nonempty_run_is_not_reported_as_success(self) -> None:
        completed = self._run("invalid-only", full=True)

        self.assertEqual(completed.returncode, 1)
        self.assertIn(
            "verify-allowlist: no authoritative subtest results",
            completed.stderr,
        )
        self.assertIn(
            'testid="invalid 1"',
            (self.repo / "qtest-results.xml").read_text(encoding="utf-8"),
        )
        self.assertEqual(
            (self.repo / "qtest.log").read_text(encoding="utf-8"),
            "fresh qtest log: invalid-only\n",
        )
        self.assertFalse((self.repo / "qtest-summary.md").exists())
        metrics = self.repo / "qtest-metrics.jsonl"
        self.assertTrue(not metrics.exists() or metrics.read_text(encoding="utf-8") == "")
        self._assert_no_sentinel()
