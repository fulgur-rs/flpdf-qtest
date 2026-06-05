#!/usr/bin/env python3
"""plot-metrics.py — render the nightly qtest metrics JSONL as a trend chart.

Usage:
    scripts/plot-metrics.py --input metrics.jsonl --output trend.svg

Reads the time-series records written by `verify-allowlist.py --metrics`
(one JSON object per line) and renders a Vega-Lite line chart to SVG via
vl-convert-python. vl_convert is imported lazily inside render_svg() so the
pure helpers (load_records / build_spec) stay importable without it.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Series we plot. These are the actionable trends: regressions should stay at
# zero, and candidates should trend down as they are promoted into allowlist.
_SERIES = ("regressions", "candidates")


def load_records(path: Path) -> list[dict]:
    """Parse a metrics JSONL file, skipping blank lines."""
    records: list[dict] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))
    return records


def build_spec(records: list[dict]) -> dict:
    """Build a Vega-Lite spec: a multi-series line chart of the metrics over
    time. Records are folded into long form (timestamp, metric, value) so each
    series gets its own colored line."""
    values: list[dict] = []
    for r in records:
        for metric in _SERIES:
            values.append(
                {
                    "timestamp": r["timestamp"],
                    "metric": metric,
                    "value": r.get(metric, 0),
                }
            )
    return {
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "title": "qtest nightly trend",
        "width": 720,
        "height": 320,
        "background": "white",
        "data": {"values": values},
        "mark": {"type": "line", "point": True, "interpolate": "monotone"},
        "encoding": {
            "x": {
                "field": "timestamp",
                "type": "temporal",
                "title": "date",
            },
            "y": {
                "field": "value",
                "type": "quantitative",
                "title": "subtests",
            },
            "color": {
                "field": "metric",
                "type": "nominal",
                "title": "metric",
                "scale": {"scheme": "tableau10"},
            },
        },
        "config": {
            "view": {"stroke": None},
            "axis": {"grid": True, "gridOpacity": 0.15},
        },
    }


def render_svg(spec: dict) -> str:
    """Render a Vega-Lite spec to an SVG string. Imports vl_convert lazily."""
    import vl_convert as vlc

    return vlc.vegalite_to_svg(spec)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", type=Path, required=True, help="metrics JSONL")
    ap.add_argument("--output", type=Path, required=True, help="output SVG path")
    args = ap.parse_args(argv)

    if not args.input.is_file():
        print(f"plot-metrics: input not found: {args.input}", file=sys.stderr)
        return 2
    records = load_records(args.input)
    if not records:
        print("plot-metrics: no records to plot", file=sys.stderr)
        return 1
    svg = render_svg(build_spec(records))
    args.output.write_text(svg, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
