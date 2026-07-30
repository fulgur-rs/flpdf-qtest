"""Execution contracts for the qpdf character-encoding helper shims."""

from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


_ROOT = Path(__file__).parents[2]


class CharacterEncodingShimTest(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self._temporary.name)
        self.target = self.directory / "target"
        self.target.write_text(
            "#!/usr/bin/env bash\n"
            "printf 'stdout raw'\n"
            "printf 'stderr raw\\n' >&2\n"
            "printf ' <%s>' \"$@\"\n"
            "exit 37\n",
            encoding="utf-8",
        )
        self.target.chmod(self.target.stat().st_mode | stat.S_IXUSR)
        self.normalize = self.directory / "normalize.sed"
        self.normalize.write_text("s/raw/normalized/g\n", encoding="utf-8")

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def _run(
        self,
        shim_name: str,
        variable: str,
        *,
        configured: bool = True,
        normalize: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env.pop(variable, None)
        env.pop("FLPDF_QTEST_NORMALIZE", None)
        if configured:
            env[variable] = str(self.target)
        if normalize:
            env["FLPDF_QTEST_NORMALIZE"] = str(self.normalize)
        return subprocess.run(
            [str(_ROOT / "shim" / shim_name), "first", "raw value"],
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )

    def test_each_shim_forwards_argv_channels_and_status(self) -> None:
        for shim_name, variable in (
            (
                "test_pdf_doc_encoding",
                "FLPDF_TEST_PDF_DOC_ENCODING_BIN",
            ),
            ("test_pdf_unicode", "FLPDF_TEST_PDF_UNICODE_BIN"),
        ):
            with self.subTest(shim=shim_name):
                completed = self._run(shim_name, variable)
                self.assertEqual(completed.returncode, 37)
                self.assertEqual(
                    completed.stdout,
                    "stdout raw <first> <raw value>",
                )
                self.assertEqual(completed.stderr, "stderr normalized\n")

    def test_each_shim_leaves_stderr_raw_without_normalizer(self) -> None:
        for shim_name, variable in (
            (
                "test_pdf_doc_encoding",
                "FLPDF_TEST_PDF_DOC_ENCODING_BIN",
            ),
            ("test_pdf_unicode", "FLPDF_TEST_PDF_UNICODE_BIN"),
        ):
            with self.subTest(shim=shim_name):
                completed = self._run(
                    shim_name,
                    variable,
                    normalize=False,
                )
                self.assertEqual(completed.returncode, 37)
                self.assertEqual(completed.stderr, "stderr raw\n")

    def test_each_shim_rejects_an_unset_binary(self) -> None:
        for shim_name, variable in (
            (
                "test_pdf_doc_encoding",
                "FLPDF_TEST_PDF_DOC_ENCODING_BIN",
            ),
            ("test_pdf_unicode", "FLPDF_TEST_PDF_UNICODE_BIN"),
        ):
            with self.subTest(shim=shim_name):
                completed = self._run(
                    shim_name,
                    variable,
                    configured=False,
                )
                self.assertEqual(completed.returncode, 127)
                self.assertEqual(completed.stdout, "")
                self.assertEqual(
                    completed.stderr,
                    f"shim/{shim_name}: {variable} is not set\n",
                )
