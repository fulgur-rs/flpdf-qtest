"""Execution contracts for the document-construction helper shims."""

from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


_ROOT = Path(__file__).parents[2]


class DocumentShimTest(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.target = Path(self._temporary.name) / "target"
        self.target.write_text(
            "#!/usr/bin/env bash\n"
            "printf 'argv0=%s\\n' \"$0\"\n"
            "printf 'args=%s\\n' \"$*\"\n"
            "printf 'stderr raw\\n' >&2\n"
            "exit 37\n",
            encoding="utf-8",
        )
        self.target.chmod(self.target.stat().st_mode | stat.S_IXUSR)

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def test_document_shims_forward_arguments_and_status(self) -> None:
        for shim_name, variable in (
            ("pdf_from_scratch", "FLPDF_TEST_FROM_SCRATCH_BIN"),
            ("test_many_nulls", "FLPDF_TEST_MANY_NULLS_BIN"),
        ):
            with self.subTest(shim=shim_name):
                env = os.environ.copy()
                env[variable] = str(self.target)
                completed = subprocess.run(
                    [str(_ROOT / "shim" / shim_name), "arg.pdf"],
                    env=env,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(completed.returncode, 37)
                self.assertEqual(completed.stdout, f"argv0={self.target}\nargs=arg.pdf\n")
                self.assertEqual(completed.stderr, "stderr raw\n")

    def test_document_shims_reject_unset_binary(self) -> None:
        for shim_name, variable in (
            ("pdf_from_scratch", "FLPDF_TEST_FROM_SCRATCH_BIN"),
            ("test_many_nulls", "FLPDF_TEST_MANY_NULLS_BIN"),
        ):
            with self.subTest(shim=shim_name):
                env = os.environ.copy()
                env.pop(variable, None)
                completed = subprocess.run(
                    [str(_ROOT / "shim" / shim_name)],
                    env=env,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(completed.returncode, 127)
                self.assertEqual(completed.stdout, "")
                self.assertEqual(
                    completed.stderr,
                    f"shim/{shim_name}: {variable} is not set\n",
                )
