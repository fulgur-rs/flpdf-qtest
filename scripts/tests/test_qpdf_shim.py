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
    def test_qpdf_identity_is_forwarded_to_flpdf(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "target"
            target.write_text(
                "#!/usr/bin/env bash\n"
                "printf '%s\\n' \"${FLPDF_PROGNAME:-unset}\"\n",
                encoding="utf-8",
            )
            target.chmod(target.stat().st_mode | stat.S_IXUSR)

            environment = os.environ.copy()
            environment["FLPDF_CLI_BIN"] = str(target)
            environment.pop("FLPDF_PROGNAME", None)
            environment.pop("FLPDF_QTEST_NORMALIZE", None)
            completed = subprocess.run(
                [str(_ROOT / "shim" / "qpdf"), "input.pdf"],
                env=environment,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "qpdf\n")
        self.assertEqual(completed.stderr, "")

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

    def test_test_driver_forwards_raw_stderr_to_preserve_merged_order(self) -> None:
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
            environment["FLPDF_TEST_DRIVER_BIN"] = str(target)
            environment["FLPDF_QTEST_NORMALIZE"] = str(normalize)
            completed = subprocess.run(
                [str(_ROOT / "shim" / "test_driver"), "3", "input.pdf"],
                env=environment,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )

        self.assertEqual(completed.returncode, 37)
        self.assertEqual(completed.stdout, "warning raw\noutput raw\n")
