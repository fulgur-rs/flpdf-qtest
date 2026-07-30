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
    "harness.log",
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
            '{"id":"outside 1","suite":"outside","category":"outside",'
            '"ordinal":1,"description":"not allowlisted","state":"passing",'
            '"rationale":null,"owner":null,"bead":null,'
            '"replacement_ref":null}\n'
            '{"id":"valid 1","suite":"valid","category":"valid",'
            '"ordinal":1,"description":"kept","state":"passing",'
            '"rationale":null,"owner":null,"bead":null,'
            '"replacement_ref":null}\n',
            encoding="utf-8",
        )
        (self.repo / "vendor" / "qpdf-qtest" / "qpdf").mkdir()
        (self.repo / "vendor" / "qpdf-qtest" / "valid.test").touch()
        (self.repo / "vendor" / "qpdf-qtest" / "outside.test").touch()

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
                my $datadir = "";
                for (my $i = 0; $i < scalar(@ARGV); ++$i) {
                    if ($ARGV[$i] eq "-datadir") {
                        $datadir = $ARGV[$i + 1] // "";
                    }
                }
                die "missing -datadir" if $datadir eq "";
                open my $received_datadir, ">", "received-datadir.txt" or die $!;
                print {$received_datadir} $datadir;
                close $received_datadir;

                if ($mode eq "missing-xml") {
                    print "driver stopped before XML\n";
                    exit 1;
                }

                open my $tests, ">", "received-tests.txt" or die $!;
                print {$tests} ($ENV{"TESTS"} // "");
                close $tests;

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
                } elsif ($mode eq "valid-only" || $mode eq "datadir-side-effect") {
                    my $polluted = -e "$datadir/qpdf/-";
                    if ($mode eq "datadir-side-effect") {
                        open my $side_effect, ">", "$datadir/qpdf/-" or die $!;
                        print {$side_effect} "generated by qtest\n";
                        close $side_effect;
                    }
                    my $valid_outcome = $polluted ? "fail" : "pass";
                    my $valid_passes = $polluted ? 0 : 1;
                    my $valid_failures = $polluted ? 1 : 0;
                    my $root_passes = $polluted ? 1 : 2;
                    my $root_failures = $polluted ? 1 : 0;
                    print {$xml} <<'XML';
                <?xml version="1.0"?>
                <qtest-results version="1">
                 <testsuite file="/repo/outside.test">
                  <testcase testid="outside 1" description="not allowlisted" outcome="pass"/>
                  <testsummary total-cases="1" passes="1" failures="0"
                   unexpected-passes="0" expected-failures="0"
                   missing-cases="0" extra-cases="0"/>
                 </testsuite>
                 <testsuite file="/repo/valid.test">
                XML
                    print {$xml}
                        qq{  <testcase testid="valid 1" description="kept" }
                        . qq{outcome="$valid_outcome"/>\n};
                    print {$xml}
                        qq{  <testsummary total-cases="1" }
                        . qq{passes="$valid_passes" failures="$valid_failures"\n};
                    print {$xml} <<'XML';
                   unexpected-passes="0" expected-failures="0"
                   missing-cases="0" extra-cases="0"/>
                 </testsuite>
                XML
                    print {$xml}
                        qq{ <testsummary total-cases="2" passes="$root_passes" }
                        . qq{failures="$root_failures"\n};
                    print {$xml} <<'XML';
                  unexpected-passes="0" expected-failures="0"
                  missing-cases="0" extra-cases="0"/>
                </qtest-results>
                XML
                    print "outside  1 (not allowlisted) ... PASSED\n";
                    print "valid  1 (kept) ... "
                        . ($polluted ? "FAILED\n" : "PASSED\n");
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
        env_overrides: dict[str, str | None] | None = None,
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
        for name, value in (env_overrides or {}).items():
            if value is None:
                env.pop(name, None)
            else:
                env[name] = value
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

    def _assert_generated_absent(self) -> None:
        for name in _GENERATED:
            self.assertFalse((self.repo / name).exists(), name)

    def test_missing_binary_clears_every_preseeded_artifact(self) -> None:
        completed = self._run(
            "unused",
            env_overrides={"FLPDF_CLI_BIN": None},
        )

        self.assertEqual(completed.returncode, 2)
        self.assertIn("cannot locate flpdf-cli", completed.stderr)
        self._assert_generated_absent()

    def test_nonexecuting_binary_clears_every_preseeded_artifact(self) -> None:
        nonexecuting = self.repo / "nonexecuting-flpdf"
        nonexecuting.write_text("not executable\n", encoding="utf-8")

        completed = self._run(
            "unused",
            env_overrides={"FLPDF_CLI_BIN": str(nonexecuting)},
        )

        self.assertEqual(completed.returncode, 2)
        self.assertIn(f"{nonexecuting} is not executable", completed.stderr)
        self._assert_generated_absent()

    def test_isolated_datadir_copy_failure_is_diagnostic_and_fail_closed(
        self,
    ) -> None:
        fake_tools = self.repo / "fake-tools"
        fake_tools.mkdir()
        failing_cp = fake_tools / "cp"
        failing_cp.write_text(
            "#!/usr/bin/env sh\n"
            "for argument do target=$argument; done\n"
            "mkdir -p \"$target\"\n"
            "echo partial > \"$target/partial-copy\"\n"
            "printf '%s' \"$target\" > received-copy-datadir.txt\n"
            "echo 'cp: write error: No space left on device' >&2\n"
            "exit 1\n",
            encoding="utf-8",
        )
        failing_cp.chmod(failing_cp.stat().st_mode | stat.S_IXUSR)

        completed = self._run(
            "valid-only",
            full=True,
            env_overrides={
                "PATH": f"{fake_tools}:{os.environ['PATH']}",
            },
        )

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("No space left on device", completed.stderr)
        self.assertIn("failed to create isolated qtest datadir", completed.stderr)
        self.assertIn("available space", completed.stderr)
        self.assertFalse((self.repo / "received-tests.txt").exists())
        partial_datadir = Path(
            (self.repo / "received-copy-datadir.txt").read_text(
                encoding="utf-8"
            )
        )
        self.assertFalse(partial_datadir.exists())
        self._assert_generated_absent()

    def test_full_run_with_zero_vendored_stems_is_operational_error(self) -> None:
        (self.repo / "vendor" / "qpdf-qtest" / "valid.test").unlink()
        (self.repo / "vendor" / "qpdf-qtest" / "outside.test").unlink()

        completed = self._run("unused", full=True)

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn(
            "full qtest corpus contains no vendored .test suites",
            completed.stderr,
        )
        self.assertNotIn("Verdict: OK", completed.stdout)
        self.assertFalse((self.repo / "received-tests.txt").exists())
        self._assert_generated_absent()

    def test_each_run_uses_a_fresh_isolated_datadir(self) -> None:
        source_side_effect = (
            self.repo / "vendor" / "qpdf-qtest" / "qpdf" / "-"
        )

        first = self._run("datadir-side-effect", full=True)
        first_datadir = Path(
            (self.repo / "received-datadir.txt").read_text(encoding="utf-8")
        )
        first_inputs = (
            (self.repo / "received-tests.txt").read_text(encoding="utf-8"),
            (self.repo / "qtest-results.xml").read_text(encoding="utf-8"),
        )
        first_classification = (
            (self.repo / "qtest-summary.md").read_text(encoding="utf-8"),
            (self.repo / "qtest-parity-summary.md").read_text(encoding="utf-8"),
        )

        second = self._run("datadir-side-effect", full=True)
        second_datadir = Path(
            (self.repo / "received-datadir.txt").read_text(encoding="utf-8")
        )
        second_inputs = (
            (self.repo / "received-tests.txt").read_text(encoding="utf-8"),
            (self.repo / "qtest-results.xml").read_text(encoding="utf-8"),
        )
        second_classification = (
            (self.repo / "qtest-summary.md").read_text(encoding="utf-8"),
            (self.repo / "qtest-parity-summary.md").read_text(encoding="utf-8"),
        )

        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(first_inputs, second_inputs)
        self.assertEqual(first_classification, second_classification)
        self.assertNotEqual(first_datadir, second_datadir)
        self.assertFalse(first_datadir.exists())
        self.assertFalse(second_datadir.exists())
        self.assertFalse(source_side_effect.exists())

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
        self.assertEqual(
            (self.repo / "received-tests.txt").read_text(encoding="utf-8"),
            "outside valid",
        )
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
