"""Contracts for the qpdf-ctest test19 helper route."""

from __future__ import annotations

import unittest
from pathlib import Path


_ROOT = Path(__file__).parents[2]


class QpdfCtestWiringTest(unittest.TestCase):
    def test_qpdf_ctest_shim_declares_and_execs_its_binary(self) -> None:
        shim = _ROOT / "shim" / "qpdf-ctest"
        self.assertTrue(shim.is_file(), "qpdf-ctest shim must exist")
        source = shim.read_text(encoding="utf-8")
        self.assertIn("FLPDF_TEST_QPDF_CTEST_BIN", source)
        self.assertIn('exec "${FLPDF_TEST_QPDF_CTEST_BIN}" "$@"', source)

    def test_release_build_and_ci_export_qpdf_ctest(self) -> None:
        run_source = (_ROOT / "scripts" / "run.sh").read_text(encoding="utf-8")
        workflow = (_ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("FLPDF_TEST_QPDF_CTEST_BIN", run_source)
        self.assertIn("--bin qpdf-ctest", run_source)
        self.assertIn("FLPDF_TEST_QPDF_CTEST_BIN", workflow)
        self.assertIn("--bin qpdf-ctest", workflow)
