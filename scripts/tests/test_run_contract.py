"""Static contracts for the qtest runner's paired result artifacts."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


_ROOT = Path(__file__).parents[2]


class RunContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.script = (_ROOT / "scripts" / "run.sh").read_text(encoding="utf-8")
        self.workflow = (_ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        self.gitignore = (_ROOT / ".gitignore").read_text(encoding="utf-8")

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

    def test_parity_verifier_emits_its_own_metrics_record(self) -> None:
        """The parity trend needs a time series of its own. verify-allowlist.py
        owns qtest-metrics.jsonl; the parity numbers go to a separate file so
        neither validator has to know about the other's schema."""
        self.assertIn(
            'parity_metrics="${live_dir}/qtest-parity-metrics.jsonl"', self.script
        )
        self.assertRegex(
            self.script,
            r'parity_args\+=\(\s*--metrics "\$\{parity_metrics\}"\s+'
            r'--commit "\$\{FLPDF_COMMIT:-\}"\s+'
            r'--timestamp ',
        )

    def test_parity_metrics_is_cleared_before_binary_preflight(self) -> None:
        clear = self.script.index("rm -f")
        binary_resolution = self.script.index('if [[ -z "${FLPDF_CLI_BIN:-}" ]]')
        self.assertIn(
            '"${parity_metrics}"', self.script[clear:binary_resolution]
        )

    def test_ci_uploads_the_parity_metrics_artifact(self) -> None:
        self.assertRegex(
            self.workflow,
            r'(?s)- name: Upload qtest artifacts.*?'
            r'survey/latest/qtest-parity-metrics\.jsonl',
        )

    def test_runner_exposes_no_subset_selection_interface(self) -> None:
        self.assertNotIn("QTEST_TESTS", self.script)

    def test_runner_resolves_exports_and_preflights_character_helpers(
        self,
    ) -> None:
        for variable, binary in (
            (
                "FLPDF_TEST_PDF_DOC_ENCODING_BIN",
                "flpdf-test-pdf-doc-encoding",
            ),
            ("FLPDF_TEST_PDF_UNICODE_BIN", "flpdf-test-pdf-unicode"),
            ("FLPDF_TEST_XREF_BIN", "test_xref"),
            ("FLPDF_TEST_PARSED_OFFSET_BIN", "test_parsedoffset"),
        ):
            self.assertIn(f'if [[ -z "${{{variable}:-}}" ]]', self.script)
            self.assertIn(
                f'{variable}="${{FLPDF_DIR}}/target/release/{binary}"',
                self.script,
            )
            self.assertIn(
                f'{variable}="${{repo_root}}/flpdf/target/release/{binary}"',
                self.script,
            )
            self.assertIn(f"export {variable}", self.script)
            self.assertIn(f'"${{{variable}}}"', self.script)
            self.assertRegex(
                self.script,
                rf"cargo build .*--bin {re.escape(binary)}",
            )
            self.assertIn(f"--bin {binary}", self.workflow)
            self.assertRegex(
                self.workflow,
                rf"{variable}:\s+\$\{{\{{ github\.workspace \}}\}}"
                rf"/flpdf/target/release/{re.escape(binary)}",
            )

    def test_runner_resolves_and_exports_qpdf_ctest(self) -> None:
        variable = "FLPDF_TEST_QPDF_CTEST_BIN"
        binary = "qpdf-ctest"
        self.assertIn(f'if [[ -z "${{{variable}:-}}" ]]', self.script)
        self.assertIn(
            f'{variable}="${{FLPDF_DIR}}/target/release/{binary}"',
            self.script,
        )
        self.assertIn(
            f'{variable}="${{repo_root}}/flpdf/target/release/{binary}"',
            self.script,
        )
        self.assertIn(f"export {variable}", self.script)
        self.assertIn(f'"${{{variable}}}"', self.script)
        self.assertRegex(
            self.script,
            rf"cargo build .*--bin {re.escape(binary)}",
        )
        self.assertIn(f"--bin {binary}", self.workflow)
        self.assertRegex(
            self.workflow,
            rf"{variable}:\s+\$\{{\{{ github\.workspace \}}\}}"
            rf"/flpdf/target/release/{re.escape(binary)}",
        )

    def _parity_ledger_section(self) -> str:
        readme = (_ROOT / "README.md").read_text(encoding="utf-8")
        match = re.search(
            r"(?ms)^## Parity ledger maintenance\n(?P<section>.*?)(?=^## |\Z)",
            readme,
        )
        self.assertIsNotNone(match, "README parity ledger heading is required")
        assert match is not None
        return match.group("section")

    def _assert_parity_ledger_state_field_contracts(self, section: str) -> None:
        rows = (
            "| `passing` | The authoritative run is an ordinary PASS. | "
            "`rationale`, `owner`, `bead`, and `replacement_ref` are `null`. |",
            "| `failing` | flpdf behavior was reached and differs from qpdf. | "
            "`rationale`, `owner`, and `bead` are required; "
            "`replacement_ref` is `null`. |",
            "| `blocked` | A known observation boundary prevented reaching the "
            "behavior. | `rationale`, `owner`, and `bead` are required; "
            "`replacement_ref` is `null`. |",
            "| `applicable` | The behavior is in scope but evidence cannot yet "
            "distinguish blocked from reached failure; expected-failure cases "
            "begin here. | `rationale`, `owner`, and `bead` are required; "
            "`replacement_ref` is `null`. |",
            "| `excluded` | The behavior is outside Linux x86_64 Rust parity, "
            "such as Windows shell or C/C++ ABI behavior. | `rationale` and "
            "`replacement_ref` are required; `owner` and `bead` are `null`. |",
            "| `represented` | The direct qtest route is outside the Rust "
            "boundary, but portable behavior has a Rust oracle test. | "
            "`rationale` and a Rust-test `replacement_ref` are required; "
            "`owner` and `bead` are `null`. |",
        )
        for row in rows:
            self.assertIn(row, section)

    def _assert_parity_ledger_maintenance_contract(self, section: str) -> None:
        self.assertIn("parity/qtest-11.9.0.jsonl", section)
        self.assertRegex(
            section,
            r"This\s+checked-in\s+ledger\s+is\s+owned\s+by\s+"
            r"`flpdf-qtest`\s+because\s+it\s+consumes\s+the\s+harness's\s+"
            r"same-run\s+qtest\s+artifacts\s+and\s+owns\s+the\s+CI\s+"
            r"validation\s+boundary\.",
        )
        self.assertIn("qtest XML `testid`", section)
        self.assertIn("weak-cryptography-cryptography 1", section)
        self.assertIn("suite is `weak-cryptography`", section)
        self.assertIn("`bead:flpdf-...`", section)
        self.assertIn("`rust-test:<package>:<target>:<test>`", section)
        self.assertIn("`scope:<document>#<section>`", section)
        self.assertIn("`bead:flpdf-25kg.2.1`", section)
        self.assertRegex(
            section,
            r"If a `blocked` or\n`failing` row becomes an ordinary PASS, "
            r"promote it to `passing`",
        )
        self.assertRegex(
            section,
            r"(?s)python3 scripts/verify-parity-manifest\.py \\\n+\s+"
            r"survey/latest/harness\.log survey/latest/qtest-results\.xml \\\n+\s+"
            r"parity/qtest-11\.9\.0\.jsonl",
        )
        self.assertRegex(
            section,
            r"`QTEST_FULL=1` is required for every non-empty full corpus run",
        )
        self.assertRegex(
            section,
            r"(?s)empty\s+allowlist[^.]*dry-run[^.]*no subtest result set",
        )
        self.assertRegex(
            section,
            r"Pass counts and state counts are measured survey output, not\s+"
            r"implementation\s+priorities\.",
        )
        self.assertRegex(
            section,
            r"`harness\.log`\s+and\s+`qtest-results\.xml`\s+must\s+come\s+"
            r"from\s+the\s+same\s+full\s+run\s+and\s+both\s+validators\s+"
            r"consume\s+that\s+pair\.",
        )
        self.assertRegex(
            section,
            r"A\s+parity\s+parser\s+error\s+produces\s+no\s+parity\s+"
            r"summary;\s+a\s+validation\s+error\s+produces\s+only\s+a\s+"
            r"FAIL\s+verdict\.\s+Neither\s+is\s+successful\s+update\s+"
            r"evidence\.",
        )
        self.assertRegex(
            section,
            r"A\s+partial\s+or\s+failed\s+run\s+is\s+not\s+"
            r"ledger-update\s+evidence\.",
        )
        self.assertIn(
            "keep the JSONL sorted by category and numeric ordinal", section
        )
        self.assertRegex(
            section,
            r"For\s+any\s+state\s+change,\s+update\s+the\s+required\s+"
            r"`owner`\s+and\s+`bead`\s+for\s+`applicable`,\s+`blocked`,\s+"
            r"or\s+`failing`,\s+or\s+the\s+`replacement_ref`\s+for\s+"
            r"`excluded`\s+or\s+`represented`\.",
        )
        self.assertRegex(
            section,
            r"The validator rejects stale `passing`, `blocked`, and `failing`\s+"
            r"classifications\.",
        )
        self.assertRegex(
            section,
            r"A `blocked` or `failing` row that becomes an ordinary PASS\s+"
            r"requires promotion to `passing`\.",
        )
        self.assertNotIn("QTEST_TESTS", section)

    def test_readme_parity_ledger_state_field_contracts(self) -> None:
        self._assert_parity_ledger_state_field_contracts(
            self._parity_ledger_section()
        )

    def test_readme_parity_ledger_maintenance_contract(self) -> None:
        self._assert_parity_ledger_maintenance_contract(
            self._parity_ledger_section()
        )

    def test_readme_parity_ledger_contracts_reject_prose_mutants(self) -> None:
        section = self._parity_ledger_section()
        state_check = self._assert_parity_ledger_state_field_contracts
        maintenance_check = self._assert_parity_ledger_maintenance_contract
        mutants = (
            (
                "passing-required-fields-inversion",
                "and `replacement_ref` are `null`.",
                "and `replacement_ref` are required.",
                state_check,
            ),
            (
                "failing-required-fields-deletion",
                "| `failing` |",
                "| `passing` |",
                state_check,
            ),
            (
                "blocked-observation-boundary-inversion",
                "observation boundary prevented",
                "observation boundary reached",
                state_check,
            ),
            (
                "applicable-state-inversion",
                "expected-failure cases begin here",
                "expected-failure cases are passing evidence",
                state_check,
            ),
            (
                "excluded-replacement-field-deletion",
                "| `excluded` |",
                "| `represented` |",
                state_check,
            ),
            (
                "represented-rust-test-inversion",
                "a Rust-test `replacement_ref`",
                "a Bead `replacement_ref`",
                state_check,
            ),
            (
                "bead-reference-grammar-deletion",
                "`bead:flpdf-...`",
                "`removed-bead-reference`",
                maintenance_check,
            ),
            (
                "rust-test-reference-grammar-deletion",
                "`rust-test:<package>:<target>:<test>`",
                "`removed-rust-test-reference`",
                maintenance_check,
            ),
            (
                "scope-reference-grammar-deletion",
                "`scope:<document>#<section>`",
                "`removed-scope-reference`",
                maintenance_check,
            ),
            (
                "c-api-bead-inversion",
                "`bead:flpdf-25kg.2.1`",
                "`bead:flpdf-25kg.2.2`",
                maintenance_check,
            ),
            (
                "ledger-ownership-inversion",
                "same-run qtest artifacts",
                "mixed-run qtest artifacts",
                maintenance_check,
            ),
            (
                "paired-artifact-consumer-deletion",
                "both validators consume that pair.",
                "only one validator consumes that pair.",
                maintenance_check,
            ),
            (
                "parser-validation-success-evidence-inversion",
                "Neither is successful update evidence.",
                "This is successful update evidence.",
                maintenance_check,
            ),
            (
                "failed-run-evidence-inversion",
                "failed run is not ledger-update evidence.",
                "failed run is ledger-update evidence.",
                maintenance_check,
            ),
            (
                "ledger-ordering-inversion",
                "sorted by category and numeric ordinal",
                "unsorted by category and numeric ordinal",
                maintenance_check,
            ),
            (
                "state-reference-update-inversion",
                "For any state change",
                "For no state change",
                maintenance_check,
            ),
            (
                "stale-state-rejection-inversion",
                "The validator rejects stale",
                "The validator accepts stale",
                maintenance_check,
            ),
            (
                "required-promotion-inversion",
                "requires promotion to `passing`.",
                "requires no promotion to `passing`.",
                maintenance_check,
            ),
            (
                "blocked-failing-promotion-inversion",
                "promote it to `passing`",
                "promote it to `failing`",
                maintenance_check,
            ),
            (
                "validator-argument-order-inversion",
                "survey/latest/harness.log survey/latest/qtest-results.xml",
                "survey/latest/qtest-results.xml survey/latest/harness.log",
                maintenance_check,
            ),
            (
                "nonempty-full-run-inversion",
                "is required for every non-empty full corpus run",
                "is optional for every non-empty full corpus run",
                maintenance_check,
            ),
            (
                "empty-allowlist-dry-run-inversion",
                "dry-run; it executes no subtests",
                "full run; it executes every subtest",
                maintenance_check,
            ),
            (
                "xml-identity-inversion",
                "qtest XML `testid`",
                "qtest description",
                maintenance_check,
            ),
            (
                "suite-stem-example-inversion",
                "suite is `weak-cryptography`",
                "suite is `weak-cryptography-cryptography`",
                maintenance_check,
            ),
            (
                "pass-count-priority-inversion",
                "not implementation\npriorities.",
                "implementation priorities.",
                maintenance_check,
            ),
            (
                "subset-interface-addition",
                "",
                "QTEST_TESTS",
                maintenance_check,
            ),
        )

        for name, source, replacement, check in mutants:
            with self.subTest(mutant=name):
                mutant = section.replace(source, replacement, 1)
                self.assertNotEqual(mutant, section)
                with self.assertRaises(AssertionError):
                    check(mutant)

    def test_nonempty_run_requires_qtest_result_xml_before_verification(self) -> None:
        self.assertIn('qtest_xml="${live_dir}/qtest-results.xml"', self.script)
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

    def test_every_generated_artifact_is_cleared_before_binary_preflight(
        self,
    ) -> None:
        clear = self.script.index("rm -f")
        binary_resolution = self.script.index(
            'if [[ -z "${FLPDF_CLI_BIN:-}" ]]'
        )
        generated = (
            "${log}",
            "${qtest_log}",
            "${qtest_xml}",
            "${qtest_junit}",
            "${summary}",
            "${metrics}",
            "${parity_summary}",
        )

        self.assertLess(clear, binary_resolution)
        clear_block = self.script[clear:binary_resolution]
        for artifact in generated:
            self.assertIn(f'"{artifact}"', clear_block)

    def test_driver_runs_from_the_live_artifact_directory(self) -> None:
        """qtest-driver hardcodes qtest.log / qtest-results.xml / TEST-qtest.xml
        relative to cwd, so the runner must invoke it from survey/latest."""
        self.assertIn('live_dir="${repo_root}/survey/latest"', self.script)
        self.assertRegex(
            self.script,
            r'\(\s*cd "\$\{live_dir\}" &&[^)]*qtest-driver',
        )

    def test_driver_receives_only_the_isolated_qtest_datadir(self) -> None:
        self.assertIn(
            'qtest_source="${repo_root}/vendor/qpdf-qtest"',
            self.script,
        )
        self.assertIn(
            'qtest_datadir="${run_tmp}/qpdf-qtest"',
            self.script,
        )
        self.assertIn(
            'cp -a --reflink=auto "${qtest_source}" "${qtest_datadir}"',
            self.script,
        )
        self.assertIn('-datadir "${qtest_datadir}"', self.script)
        self.assertNotIn(
            '-datadir "${repo_root}/vendor/qpdf-qtest"',
            self.script,
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
            r'survey/latest/harness\.log\s+survey/latest/qtest\.log\s+'
            r'survey/latest/qtest-results\.xml\s+'
            r'survey/latest/TEST-qtest\.xml\s+'
            r'survey/latest/qtest-summary\.md\s+'
            r'survey/latest/qtest-metrics\.jsonl\s+'
            r'survey/latest/qtest-parity-summary\.md',
        )

    def test_gitignore_lists_parity_summary_artifact(self) -> None:
        ignored = {
            line.strip()
            for line in self.gitignore.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
        self.assertIn("survey/latest/", ignored)

    @property
    def publish(self) -> str:
        return (_ROOT / "scripts" / "publish-metrics.sh").read_text(encoding="utf-8")

    def test_publish_accepts_and_appends_the_parity_series(self) -> None:
        """The parity trend needs its own history file on metrics-data, fed by
        a second argument so the allowlist path is unchanged when it is absent."""
        self.assertIn("parity_line=", self.publish)
        self.assertIn("parity-metrics.jsonl", self.publish)

    def test_publish_renders_both_charts_with_both_renderers(self) -> None:
        """Four SVGs: each series rendered by vl-convert and by fulgur-chart."""
        for svg in (
            "trend.svg",
            "trend-fulgur.svg",
            "trend-parity.svg",
            "trend-parity-fulgur.svg",
        ):
            self.assertIn(svg, self.publish, svg)
        self.assertRegex(self.publish, r"--series\s+parity")

    def test_publish_pins_chart_cli_version(self) -> None:
        """The dogfood renderer stays pinned; an unpinned npx would pull
        whatever is latest at render time into the nightly job."""
        self.assertRegex(
            self.publish, r'FULGUR_CHART_CLI_VERSION="[0-9]+\.[0-9]+\.[0-9]+"'
        )
        self.assertIn(
            '"@fulgur-rs/chart-cli@${FULGUR_CHART_CLI_VERSION}"', self.publish
        )

    def test_ci_passes_the_parity_metrics_to_publish(self) -> None:
        self.assertRegex(
            self.workflow,
            r"publish-metrics\.sh\s+artifacts/qtest-metrics\.jsonl\s+"
            r"artifacts/qtest-parity-metrics\.jsonl",
        )

    def test_ci_publishes_scheduled_metrics_after_qtest_failure(self) -> None:
        publish_metrics = self.workflow.split("  publish-metrics:", maxsplit=1)[1]
        self.assertRegex(
            publish_metrics,
            r"(?m)^    if: \$\{\{ always\(\) && "
            r"github\.event_name == 'schedule' \}\}$",
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
