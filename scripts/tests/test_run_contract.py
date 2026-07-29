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
            r'TEST-qtest\.xml\s+qtest-summary\.md\s+qtest-metrics\.jsonl',
        )


if __name__ == "__main__":
    unittest.main()
