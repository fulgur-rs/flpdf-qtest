"""Execution contracts for the qpdf metadata helper shims."""

from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


_ROOT = Path(__file__).parents[2]


class MetadataShimTest(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.target = Path(self._temporary.name) / "target"
        self.target.write_text(
            "#!/usr/bin/env bash\n"
            "printf 'stdout %s\\n' \"$0\"\n"
            "printf 'args: %s\\n' \"$*\"\n"
            "printf 'stderr raw\\n' >&2\n"
            "exit 37\n",
            encoding="utf-8",
        )
        self.target.chmod(self.target.stat().st_mode | stat.S_IXUSR)

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def _run(
        self,
        shim_name: str,
        variable: str,
        *,
        configured: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env.pop(variable, None)
        if configured:
            env[variable] = str(self.target)
        return subprocess.run(
            [str(_ROOT / "shim" / shim_name), "input.pdf", "extra"],
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )

    def test_metadata_shims_forward_arguments_and_status(self) -> None:
        for shim_name, variable in (
            ("test_xref", "FLPDF_TEST_XREF_BIN"),
            ("test_parsedoffset", "FLPDF_TEST_PARSED_OFFSET_BIN"),
        ):
            with self.subTest(shim=shim_name):
                completed = self._run(shim_name, variable)
                self.assertEqual(completed.returncode, 37)
                self.assertIn("input.pdf extra", completed.stdout)
                self.assertEqual(completed.stderr, "stderr raw\n")

    def test_metadata_shims_reject_an_unset_binary(self) -> None:
        for shim_name, variable in (
            ("test_xref", "FLPDF_TEST_XREF_BIN"),
            ("test_parsedoffset", "FLPDF_TEST_PARSED_OFFSET_BIN"),
        ):
            with self.subTest(shim=shim_name):
                completed = self._run(shim_name, variable, configured=False)
                self.assertEqual(completed.returncode, 127)
                self.assertEqual(completed.stdout, "")
                self.assertEqual(
                    completed.stderr,
                    f"shim/{shim_name}: {variable} is not set\n",
                )
