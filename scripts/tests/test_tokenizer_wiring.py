"""Contracts for the qpdf test_tokenizer helper route."""

from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


_ROOT = Path(__file__).parents[2]


class TokenizerWiringTest(unittest.TestCase):
    def test_test_tokenizer_shim_forwards_arguments_and_status(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "target"
            target.write_text(
                "#!/usr/bin/env bash\n"
                "printf 'args=%s\\n' \"$*\"\n"
                "printf 'stderr raw\\n' >&2\n"
                "exit 37\n",
                encoding="utf-8",
            )
            target.chmod(target.stat().st_mode | stat.S_IXUSR)

            environment = os.environ.copy()
            environment["FLPDF_TEST_TOKENIZER_BIN"] = str(target)
            completed = subprocess.run(
                [str(_ROOT / "shim" / "test_tokenizer"), "-maxlen", "50", "tokens.pdf"],
                env=environment,
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertEqual(completed.returncode, 37)
        self.assertEqual(
            completed.stdout,
            "args=-maxlen 50 tokens.pdf\n",
        )
        self.assertEqual(completed.stderr, "stderr raw\n")

    def test_test_tokenizer_shim_rejects_an_unset_binary(self) -> None:
        environment = os.environ.copy()
        environment.pop("FLPDF_TEST_TOKENIZER_BIN", None)

        completed = subprocess.run(
            [str(_ROOT / "shim" / "test_tokenizer")],
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(completed.returncode, 127)
        self.assertEqual(completed.stdout, "")
        self.assertEqual(
            completed.stderr,
            "shim/test_tokenizer: FLPDF_TEST_TOKENIZER_BIN is not set\n",
        )

    def test_test_tokenizer_shim_rejects_a_non_executable_binary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "target"
            target.write_text("not executable\n", encoding="utf-8")

            environment = os.environ.copy()
            environment["FLPDF_TEST_TOKENIZER_BIN"] = str(target)
            completed = subprocess.run(
                [str(_ROOT / "shim" / "test_tokenizer")],
                env=environment,
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertEqual(completed.returncode, 127)
        self.assertEqual(completed.stdout, "")
        self.assertEqual(
            completed.stderr,
            f"shim/test_tokenizer: FLPDF_TEST_TOKENIZER_BIN={target} is not executable\n",
        )

    def test_run_and_ci_declare_the_tokenizer_binary(self) -> None:
        run_source = (_ROOT / "scripts" / "run.sh").read_text(encoding="utf-8")
        workflow = (_ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn('if [[ -z "${FLPDF_TEST_TOKENIZER_BIN:-}" ]]', run_source)
        self.assertIn(
            'FLPDF_TEST_TOKENIZER_BIN="${FLPDF_DIR}/target/release/flpdf-test-tokenizer"',
            run_source,
        )
        self.assertIn(
            'FLPDF_TEST_TOKENIZER_BIN="${repo_root}/flpdf/target/release/flpdf-test-tokenizer"',
            run_source,
        )
        self.assertIn("export FLPDF_TEST_TOKENIZER_BIN", run_source)
        self.assertIn('"${FLPDF_TEST_TOKENIZER_BIN}"', run_source)
        self.assertIn("--bin flpdf-test-tokenizer", run_source)
        self.assertIn("FLPDF_TEST_TOKENIZER_BIN", workflow)
        self.assertIn("--bin flpdf-test-tokenizer", workflow)
