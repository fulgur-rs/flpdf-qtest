"""Unit tests for scripts/plot-metrics.py.

Run with: python3 -m unittest scripts.tests.test_plot_metrics

The SVG-rendering test requires vl-convert-python; the pure-function tests
(load_records / build_spec) do not, because plot-metrics.py imports vl_convert
lazily inside render_svg.
"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PLOT_PATH = _HERE.parent / "plot-metrics.py"

_spec = importlib.util.spec_from_file_location("plot_metrics", _PLOT_PATH)
assert _spec and _spec.loader, f"cannot load {_PLOT_PATH}"
plot_metrics = importlib.util.module_from_spec(_spec)
sys.modules["plot_metrics"] = plot_metrics
_spec.loader.exec_module(plot_metrics)

try:
    import vl_convert as _vlc  # noqa: F401

    _HAS_VLC = True
except ImportError:
    # Only a missing install disables the render tests; a broken/incompatible
    # vl_convert should surface as a real error, not be silently skipped.
    _HAS_VLC = False

_SAMPLE = textwrap.dedent(
    """\
    {"timestamp":"2026-06-03T00:00:00Z","flpdf_commit":"a","total":100,"expected_pass":9,"regressions":2,"missing":0,"candidates":70,"informational":19,"verdict":"FAIL"}

    {"timestamp":"2026-06-04T00:00:00Z","flpdf_commit":"b","total":101,"expected_pass":10,"regressions":1,"missing":0,"candidates":68,"informational":22,"verdict":"FAIL"}
    """
)


def _tmp(content: str, suffix: str = ".jsonl") -> Path:
    f = tempfile.NamedTemporaryFile(
        mode="w", suffix=suffix, delete=False, encoding="utf-8"
    )
    f.write(content)
    f.close()
    return Path(f.name)


class LoadRecordsTest(unittest.TestCase):
    def test_parses_lines_and_skips_blanks(self) -> None:
        recs = plot_metrics.load_records(_tmp(_SAMPLE))
        self.assertEqual(len(recs), 2)
        self.assertEqual(recs[0]["flpdf_commit"], "a")
        self.assertEqual(recs[1]["regressions"], 1)


class LoadRecordsRobustnessTest(unittest.TestCase):
    def test_skips_invalid_json_lines(self) -> None:
        content = (
            '{"timestamp":"2026-06-03T00:00:00Z","regressions":1,"candidates":5}\n'
            "not json at all\n"
            "<<<<<<< HEAD\n"
            '{"timestamp":"2026-06-04T00:00:00Z","regressions":0,"candidates":4}\n'
        )
        recs = plot_metrics.load_records(_tmp(content))
        self.assertEqual(len(recs), 2)
        self.assertEqual(recs[0]["regressions"], 1)
        self.assertEqual(recs[1]["candidates"], 4)


class BuildSpecTest(unittest.TestCase):
    def test_spec_is_vegalite_with_series(self) -> None:
        recs = plot_metrics.load_records(_tmp(_SAMPLE))
        spec = plot_metrics.build_spec(recs)
        self.assertIn("vega-lite", spec["$schema"])
        values = spec["data"]["values"]
        self.assertTrue(values)
        metrics = {v["metric"] for v in values}
        self.assertIn("regressions", metrics)
        self.assertIn("missing", metrics)
        self.assertIn("candidates", metrics)
        self.assertIn("allowlist", metrics)

    def test_spec_includes_allowlist_series(self) -> None:
        recs = [
            {
                "timestamp": "2026-06-04T00:00:00Z",
                "regressions": 0,
                "candidates": 4,
                "allowlist": 12,
            }
        ]
        values = plot_metrics.build_spec(recs)["data"]["values"]
        by_metric = {v["metric"]: v["value"] for v in values}
        self.assertEqual(by_metric["allowlist"], 12)

    def test_skips_records_without_timestamp(self) -> None:
        recs = [
            {"regressions": 1, "candidates": 2},  # no timestamp -> dropped
            {"timestamp": "2026-06-04T00:00:00Z", "regressions": 0, "candidates": 1},
        ]
        values = plot_metrics.build_spec(recs)["data"]["values"]
        # Only the timestamped record contributes (one row per series).
        self.assertEqual(len(values), len(plot_metrics._SERIES))
        self.assertTrue(all(v["timestamp"] for v in values))

    def test_missing_metric_defaults_to_zero(self) -> None:
        recs = [{"timestamp": "2026-06-04T00:00:00Z", "regressions": 3}]
        values = plot_metrics.build_spec(recs)["data"]["values"]
        by_metric = {v["metric"]: v["value"] for v in values}
        self.assertEqual(by_metric["regressions"], 3)
        self.assertEqual(by_metric["candidates"], 0)


@unittest.skipUnless(_HAS_VLC, "vl-convert-python not installed")
class RenderSvgTest(unittest.TestCase):
    def test_render_returns_svg(self) -> None:
        recs = plot_metrics.load_records(_tmp(_SAMPLE))
        svg = plot_metrics.render_svg(plot_metrics.build_spec(recs))
        self.assertTrue(svg.lstrip().startswith("<svg"))


@unittest.skipUnless(_HAS_VLC, "vl-convert-python not installed")
class MainTest(unittest.TestCase):
    def test_main_writes_svg_file(self) -> None:
        jsonl = _tmp(_SAMPLE)
        out = _tmp("", suffix=".svg")
        rc = plot_metrics.main(["--input", str(jsonl), "--output", str(out)])
        self.assertEqual(rc, 0)
        self.assertTrue(out.read_text(encoding="utf-8").lstrip().startswith("<svg"))

    def test_main_writes_spec_output(self) -> None:
        jsonl = _tmp(_SAMPLE)
        out = _tmp("", suffix=".svg")
        spec_out = _tmp("", suffix=".json")
        rc = plot_metrics.main(
            [
                "--input", str(jsonl),
                "--output", str(out),
                "--spec-output", str(spec_out),
            ]
        )
        self.assertEqual(rc, 0)
        import json as _json
        spec = _json.loads(spec_out.read_text(encoding="utf-8"))
        self.assertIn("vega-lite", spec["$schema"])
        metrics = {v["metric"] for v in spec["data"]["values"]}
        self.assertEqual(metrics, set(plot_metrics._SERIES))


if __name__ == "__main__":
    unittest.main()
