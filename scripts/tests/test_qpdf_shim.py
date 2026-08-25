"""Execution contracts for the qpdf compatibility shim."""

from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


_ROOT = Path(__file__).parents[2]


class QpdfShimTest(unittest.TestCase):
    def test_normalization_preserves_qpdf_stdout_stderr_order(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            target = directory / "target"
            target.write_text(
                "#!/usr/bin/env bash\n"
                "printf 'warning raw\\n' >&2\n"
                "printf 'output raw\\n'\n"
                "exit 37\n",
                encoding="utf-8",
            )
            target.chmod(target.stat().st_mode | stat.S_IXUSR)
            normalize = directory / "normalize.sed"
            normalize.write_text(
                "s/^warning raw$/warning normalized/\n",
                encoding="utf-8",
            )

            environment = os.environ.copy()
            environment["FLPDF_CLI_BIN"] = str(target)
            environment["FLPDF_QTEST_NORMALIZE"] = str(normalize)
            completed = subprocess.run(
                [str(_ROOT / "shim" / "qpdf"), "input.pdf"],
                env=environment,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )

        self.assertEqual(completed.returncode, 37)
        self.assertEqual(completed.stdout, "warning normalized\noutput raw\n")
