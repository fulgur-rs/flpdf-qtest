# Nightly qtest metrics: record + visualize

Date: 2026-06-05

## Goal

Collect the nightly qtest run's headline numbers as a time series so trends
(regressions, allowlist candidates, totals) are visible over time, and render
a modern chart from that series. This is separate from the per-run GitHub Job
Summary added in PR #12 — that shows a single run; this shows history.

## Decisions

- **Storage**: a JSONL file (`metrics.jsonl`, one object per run) on a
  dedicated **orphan branch** `metrics-data`. No external infra, no secrets
  beyond the workflow's own `GITHUB_TOKEN`, history versioned in git.
- **Renderer**: **Vega-Lite** specs rendered by **`vl-convert-python`** (pip),
  called from a small Python script. Self-contained (Vega-Lite JS is inlined
  in the package — no browser, no network, no `cargo` build). Modern default
  aesthetics. The Rust `vl-convert` CLI is avoided (slow `cargo install`).
- **Surface**: commit the rendered `trend.svg` to the `metrics-data` branch and
  embed it in that branch's README. Add a one-line link to the chart in the
  nightly Job Summary. **Not** embedded in the main-branch README.
- **Format**: JSONL (named fields, append-only, robust to schema growth).

## Data flow

1. **Produce** — `scripts/verify-allowlist.py` gains `--metrics <path>`. It
   writes ONE JSON object (a single line) with the counts `judge()` already
   computes — `total`, `allowlist`, `expected_pass`, `regressions`, `missing`,
   `candidates`, `informational` — plus `verdict`, and metadata supplied by the
   caller via `--commit <sha>` and `--timestamp <iso8601>`. Metadata is passed
   in (not generated) so the emitter stays deterministic and unit-testable.
   Written even on drift/regression (the metric should record bad runs too).
2. **Persist (CI, nightly only)** — a workflow step, gated to
   `github.event_name == 'schedule'` with `permissions: contents: write`,
   checks out `metrics-data` (bootstrapping it as an orphan branch on first
   run), appends the metrics line, renders the chart, commits both, and pushes.
3. **Render** — `scripts/plot-metrics.py` reads `metrics.jsonl`, builds a
   Vega-Lite spec (multi-series line chart: regressions + candidates +
   allowlist, with total available), and calls `vl_convert.vegalite_to_svg(...)`
   to write
   `trend.svg`.
4. **Show** — `trend.svg` + a short README on `metrics-data`; the nightly Job
   Summary gets one extra line linking to the chart.

## CI integration

- The metrics/commit work runs ONLY on the nightly `schedule` trigger. PRs
  (especially from forks) have no write token and must not pollute history.
- First run bootstraps the orphan branch (`git switch --orphan metrics-data`),
  starting from an empty tree.
- Nightly cadence is daily and single-writer, so push contention is a
  non-issue; no locking needed.
- Metrics are **auxiliary**: failures in rendering or pushing must NOT fail the
  qtest job (the verdict from the acceptance suite is the real gate). The step
  is best-effort and logs on failure.

## Scope note

The nightly currently runs `run.sh`'s default **allowlist-scoped** stems, not a
full survey. Metrics therefore track that scope's numbers over time, which is
internally consistent. A full-survey trend would require setting
`QTEST_FULL=1` on the nightly — a separate decision, out of scope here.

## Testing (TDD)

- `verify-allowlist.py --metrics`: with explicit `--commit`/`--timestamp`,
  asserts the emitted JSON line has the expected fields/values; that it is
  written on a regression run; and that counts match the summary.
- `plot-metrics.py`: spec construction from a sample JSONL, and that
  `vl-convert` returns a non-empty SVG containing `<svg`.

## Out of scope (YAGNI)

- Interactive HTML / GitHub Pages dashboard (the same Vega-Lite spec can emit
  `vl2html` later if wanted).
- Alerting on thresholds.
- Full-survey metrics.
