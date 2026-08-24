"""Contracts for the qpdfjob-ctest helper route."""

from __future__ import annotations

import unittest
from pathlib import Path


_ROOT = Path(__file__).parents[2]


class QpdfJobWiringTest(unittest.TestCase):
    def test_qpdfjob_shim_and_binary_variable_are_declared(self) -> None:
        shim = _ROOT / "shim" / "qpdfjob-ctest"
        self.assertTrue(shim.is_file(), "qpdfjob-ctest shim must exist")
        source = shim.read_text(encoding="utf-8")
        self.assertIn("FLPDF_TEST_QPDFJOB_BIN", source)
        self.assertIn(
            'exec "$' + '{FLPDF_TEST_QPDFJOB_BIN}" "$@"',
            source,
        )

    def test_release_build_and_ci_export_the_qpdfjob_binary(self) -> None:
        run_source = (_ROOT / "scripts" / "run.sh").read_text(encoding="utf-8")
        workflow = (_ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("FLPDF_TEST_QPDFJOB_BIN", run_source)
        self.assertIn("--bin qpdfjob-ctest", run_source)
        self.assertIn("FLPDF_TEST_QPDFJOB_BIN", workflow)
        self.assertIn("--bin qpdfjob-ctest", workflow)
