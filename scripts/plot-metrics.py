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

# Series we plot. These are the actionable trends: regressions and missing
# should stay at zero (both flip verdict to FAIL — regressions are
# allowlisted subtests that started failing, missing are allowlisted subtests
# that stopped appearing in the log, typically an upstream rename or a typo),
# candidates should trend down as they are promoted into allowlist, and
# allowlist (the size of the allowlist itself) should trend up as candidates
# get promoted.
ALLOWLIST_SERIES = ("regressions", "missing", "candidates", "allowlist")

# The parity ledger's own trend. passing should climb, blocked and failing
# should drain into it, and applicable should stay near zero -- it is the
# state for evidence that cannot yet tell a blocked observation from a
# reached failure, so a growing applicable means triage is falling behind.
# excluded is deliberately left out: it is a scope decision, not progress.
PARITY_SERIES = ("passing", "blocked", "failing", "applicable")

_SERIES_BY_NAME = {"allowlist": ALLOWLIST_SERIES, "parity": PARITY_SERIES}
_TITLE_BY_NAME = {
    "allowlist": "qtest nightly trend",
    "parity": "qtest parity trend",
}


def load_records(path: Path) -> list[dict]:
    """Parse a metrics JSONL file, skipping blank lines. Plotting is
    best-effort: an unparseable line (manual edit, stray conflict marker)
    is warned about and skipped rather than crashing the whole render."""
    records: list[dict] = []
    with path.open("r", encoding="utf-8") as fh:
        for line_no, raw in enumerate(fh, 1):
            line = raw.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as e:
                print(
                    f"plot-metrics: skipping invalid JSON on line {line_no}: {e}",
                    file=sys.stderr,
                )
    return records


def build_spec(
    records: list[dict],
    series: tuple[str, ...] = ALLOWLIST_SERIES,
    title: str = "qtest nightly trend",
) -> dict:
    """Build a Vega-Lite spec: a multi-series line chart of the metrics over
    time. Records are folded into long form (timestamp, metric, value) so each
    series gets its own colored line.

    ``series`` selects which fields to plot, so one renderer serves both the
    allowlist trend and the parity trend rather than duplicating the spec."""
    values: list[dict] = []
    for r in records:
        timestamp = r.get("timestamp")
        if not timestamp:
            continue  # a record with no timestamp can't be placed on the axis
        for metric in series:
            value = r.get(metric)
            values.append(
                {
                    "timestamp": timestamp,
                    "metric": metric,
                    "value": value if value is not None else 0,
                }
            )
    return {
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "title": title,
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
    ap.add_argument(
        "--series",
        choices=sorted(_SERIES_BY_NAME),
        default="allowlist",
        help="which metric family to plot (default: allowlist)",
    )
    ap.add_argument(
        "--title",
        default=None,
        help="chart title (default: derived from --series)",
    )
    ap.add_argument(
        "--spec-output",
        type=Path,
        default=None,
        help="also write the Vega-Lite spec as JSON to this path "
        "(used to feed the same spec to fulgur-chart for dogfooding)",
    )
    args = ap.parse_args(argv)

    if not args.input.is_file():
        print(f"plot-metrics: input not found: {args.input}", file=sys.stderr)
        return 2
    records = load_records(args.input)
    if not records:
        print("plot-metrics: no records to plot", file=sys.stderr)
        return 1
    series = _SERIES_BY_NAME[args.series]
    title = args.title or _TITLE_BY_NAME[args.series]
    spec = build_spec(records, series=series, title=title)
    if args.spec_output is not None:
        args.spec_output.write_text(json.dumps(spec, indent=2), encoding="utf-8")
    svg = render_svg(spec)
    args.output.write_text(svg, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
