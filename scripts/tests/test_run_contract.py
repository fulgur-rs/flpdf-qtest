"""Static contracts for the qtest runner's paired result artifacts."""

from __future__ import annotations

import unittest
from pathlib import Path


_ROOT = Path(__file__).parents[2]


class RunContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.script = (_ROOT / "scripts" / "run.sh").read_text(encoding="utf-8")
        self.workflow = (_ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )

    def test_allowlist_verifier_receives_paired_log_and_xml(self) -> None:
        self.assertRegex(
            self.script,
            r'verify_args=\(\s*"\$\{log\}"\s+"\$\{qtest_xml\}"\s+'
            r'"\$\{repo_root\}/allowlist\.txt"',
        )
        self.assertRegex(
            self.script,
            r'verify-allowlist\.py"\s+"\$\{verify_args\[@\]\}"',
        )

    def test_manifest_verifier_receives_paired_full_survey_artifacts(self) -> None:
        self.assertIn("QTEST_FULL", self.script)
        self.assertRegex(
            self.script,
            r'parity_args=\(\s*"\$\{log\}"\s+"\$\{qtest_xml\}"\s+'
            r'"\$\{manifest\}"',
        )
        self.assertRegex(
            self.script,
            r'verify-parity-manifest\.py"\s+"\$\{parity_args\[@\]}"',
        )
        self.assertRegex(
            self.script,
            r'if \[\[ "\$\{QTEST_FULL:-0\}" != "1" \]\]; then\s+'
            r'echo "run\.sh: parity manifest validation requires QTEST_FULL=1" >&2\s+'
            r'exit 2\s+fi',
        )

    def test_nonempty_run_requires_qtest_result_xml_before_verification(self) -> None:
        self.assertIn('qtest_xml="${repo_root}/qtest-results.xml"', self.script)
        run_section = self.script.split(
            "# --- run qtest-driver", maxsplit=1
        )[1].split("# --- verify against allowlist", maxsplit=1)[0]
        empty_run, nonempty_run = run_section.split("else", maxsplit=1)
        guard = 'if [[ ! -f "${qtest_xml}" ]]; then'

        self.assertNotIn(guard, empty_run)
        self.assertIn(guard, nonempty_run)
        self.assertLess(
            nonempty_run.index('perl "${repo_root}/vendor/qtest/bin/qtest-driver"'),
            nonempty_run.index(guard),
        )
        self.assertRegex(
            nonempty_run,
            r'if \[\[ ! -f "\$\{qtest_xml\}" \]\]; then\s+'
            r'echo "run\.sh: qtest results XML not found: \$\{qtest_xml\}" >&2\s+'
            r'exit 1\s+fi',
        )

    def test_generated_result_artifacts_are_cleared_before_driver_execution(self) -> None:
        run_section = self.script.split(
            "# --- run qtest-driver", maxsplit=1
        )[1].split("# --- verify against allowlist", maxsplit=1)[0]
        driver = 'perl "${repo_root}/vendor/qtest/bin/qtest-driver"'
        clear = (
            'rm -f "${qtest_log}" "${qtest_xml}" "${qtest_junit}" '
            '"${summary}" "${metrics}"'
        )

        self.assertIn(clear, run_section)
        self.assertLess(run_section.index(clear), run_section.index(driver))

    def test_verifier_keeps_summary_and_metrics_outputs(self) -> None:
        self.assertRegex(
            self.script,
            r'"\$\{repo_root\}/allowlist\.txt"\s+--summary "\$\{summary\}"',
        )
        self.assertRegex(
            self.script,
            r'verify_args\+=\(\s+--metrics "\$\{metrics\}"\s+'
            r'--commit "\$\{FLPDF_COMMIT:-\}"\s+'
            r'--timestamp "\$\(date -u \+%Y-%m-%dT%H:%M:%SZ\)"',
        )

    def test_ci_uploads_both_structured_qtest_artifacts(self) -> None:
        self.assertRegex(
            self.workflow,
            r'(?s)- name: Upload qtest artifacts.*?path: \|\s+'
            r'harness\.log\s+qtest\.log\s+qtest-results\.xml\s+'
            r'TEST-qtest\.xml\s+qtest-summary\.md\s+qtest-metrics\.jsonl\s+'
            r'qtest-parity-summary\.md',
        )

    def test_ci_full_survey_uploads_parity_summary(self) -> None:
        qtest_step = self.workflow.split(
            "- name: Run qtest acceptance suite", maxsplit=1
        )[1].split("- name: Upload qtest artifacts", maxsplit=1)[0]
        self.assertRegex(qtest_step, r'QTEST_FULL:\s+"1"')
        self.assertRegex(
            self.workflow,
            r'(?s)qtest:\s+runs-on: ubuntu-latest\s+timeout-minutes: 30',
        )
        self.assertIn(
            "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02",
            self.workflow,
        )
        self.assertRegex(
            self.workflow,
            r'(?s)- name: Upload qtest artifacts.*?qtest-parity-summary\.md',
        )
