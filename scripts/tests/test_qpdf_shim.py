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

    def test_normalization_preserves_merged_order_past_sed_buffering(self) -> None:
        # A filter that buffers each stream independently only reveals the
        # reordering once stdout is large enough to be emitted in blocks
        # before EOF. qtest fixtures such as linearization "dump
        # linearization" expect the stderr warnings ahead of the stdout dump.
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            target = directory / "target"
            target.write_text(
                "#!/usr/bin/env bash\n"
                "printf 'warning raw\\n' >&2\n"
                "for i in $(seq 1 20000); do printf 'data line %d\\n' \"$i\"; done\n"
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

        lines = completed.stdout.splitlines()
        self.assertEqual(completed.returncode, 37)
        self.assertEqual(len(lines), 20001)
        self.assertEqual(lines[0], "warning normalized")

    def test_normalization_keeps_stdout_separate_and_unfiltered(self) -> None:
        # qpdf writes payload to stdout and diagnostics to stderr; tests such
        # as progress-reporting "progress report to stdout" only pass when the
        # shim keeps the descriptors apart and leaves stdout byte-identical.
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            target = directory / "target"
            target.write_text(
                "#!/usr/bin/env bash\n"
                "printf 'warning raw\\n' >&2\n"
                "printf 'warning raw\\n'\n"
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
                stderr=subprocess.PIPE,
                text=True,
            )

        self.assertEqual(completed.returncode, 37)
        self.assertEqual(completed.stdout, "warning raw\n")
        self.assertEqual(completed.stderr, "warning normalized\n")

    def test_normalization_drains_split_stderr_before_exiting(self) -> None:
        # When the descriptors differ the filter runs asynchronously; if the
        # shim exits without waiting for it, diagnostics are still in flight
        # when qtest reads the file it just redirected them into.
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            target = directory / "target"
            target.write_text(
                "#!/usr/bin/env bash\n"
                "for i in $(seq 1 20000); do printf 'flpdf: note %d\\n' \"$i\" >&2; done\n"
                "printf 'payload\\n'\n"
                "exit 37\n",
                encoding="utf-8",
            )
            target.chmod(target.stat().st_mode | stat.S_IXUSR)
            normalize = directory / "normalize.sed"
            # Enough rules that the filter lags well behind the command's
            # exit; a shim that does not wait truncates what qtest reads.
            normalize.write_text(
                "s/^flpdf:/qpdf:/\n"
                + "".join(f"s/never match {i}//\n" for i in range(400)),
                encoding="utf-8",
            )
            out_file = directory / "out"
            err_file = directory / "err"

            environment = os.environ.copy()
            environment["FLPDF_CLI_BIN"] = str(target)
            environment["FLPDF_QTEST_NORMALIZE"] = str(normalize)
            with out_file.open("wb") as out, err_file.open("wb") as err:
                completed = subprocess.run(
                    [str(_ROOT / "shim" / "qpdf"), "input.pdf"],
                    env=environment,
                    check=False,
                    stdout=out,
                    stderr=err,
                )

            errors = err_file.read_text(encoding="utf-8").splitlines()
            payload = out_file.read_text(encoding="utf-8")

        self.assertEqual(completed.returncode, 37)
        self.assertEqual(payload, "payload\n")
        self.assertEqual(len(errors), 20000)
        self.assertEqual(errors[-1], "qpdf: note 20000")

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
