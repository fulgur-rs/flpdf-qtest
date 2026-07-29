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
)


class RunExecutionTest(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.repo = Path(self._temporary.name)
        (self.repo / "scripts").mkdir()
        (self.repo / "shim").mkdir()
        (self.repo / "vendor" / "qtest" / "bin").mkdir(parents=True)
        (self.repo / "vendor" / "qpdf-qtest").mkdir(parents=True)
        for name in ("run.sh", "verify-allowlist.py", "qtest_results.py"):
            shutil.copy2(_ROOT / "scripts" / name, self.repo / "scripts" / name)
        (self.repo / "allowlist.txt").write_text(
            "valid:kept\n",
            encoding="utf-8",
        )

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

    def _run(self, mode: str, *, empty: bool = False) -> subprocess.CompletedProcess[str]:
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
        completed = self._run("missing-xml")

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
        completed = self._run("malformed-xml")

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
        self._assert_no_sentinel()

    def test_invalid_only_nonempty_run_is_not_reported_as_success(self) -> None:
        completed = self._run("invalid-only")

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
