#!/usr/bin/env bash
#
# scripts/publish-metrics.sh — append a run's metrics line to the metrics-data
# history branch, re-render the trend chart, and push.
#
# Usage:
#   scripts/publish-metrics.sh <new-metrics-line.jsonl>
#
# Designed to run ONLY on the nightly schedule (gated in CI) and to be
# best-effort: the caller wraps it in continue-on-error so a failure here never
# affects the qtest verdict.
#
# Required env:
#   GH_TOKEN            token with contents:write on this repo
#   GITHUB_REPOSITORY   owner/repo (set by GitHub Actions)
#
# It works in a throwaway clone (NOT the populated CI workspace) so it never
# collides with the flpdf checkout or build artifacts. The metrics-data branch
# is an orphan branch holding only metrics.jsonl, trend.svg, and a README; it
# is bootstrapped on first run if absent.

set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
new_line="${1:?usage: publish-metrics.sh <new-metrics-line.jsonl>}"

if [[ ! -s "${new_line}" ]]; then
    echo "publish-metrics: no metrics line to publish (${new_line} empty/absent)"
    exit 0
fi

branch="metrics-data"
# PUBLISH_METRICS_REMOTE overrides the push target (used by tests against a
# local bare repo); otherwise build the authenticated GitHub URL from CI env.
if [[ -n "${PUBLISH_METRICS_REMOTE:-}" ]]; then
    remote="${PUBLISH_METRICS_REMOTE}"
else
    : "${GH_TOKEN:?publish-metrics: GH_TOKEN is required}"
    : "${GITHUB_REPOSITORY:?publish-metrics: GITHUB_REPOSITORY is required}"
    remote="https://x-access-token:${GH_TOKEN}@github.com/${GITHUB_REPOSITORY}.git"
fi
work="$(mktemp -d)"
trap '[[ -n "${work:-}" ]] && rm -rf "${work}"' EXIT

# Fresh repo; fetch the existing branch tip, or start an orphan on first run.
git -C "${work}" init -q
# Scope the bot identity to this throwaway clone — never touch global config.
git -C "${work}" config user.name "github-actions[bot]"
git -C "${work}" config user.email "41898282+github-actions[bot]@users.noreply.github.com"
git -C "${work}" remote add origin "${remote}"
if git -C "${work}" fetch -q --depth 1 origin "${branch}"; then
    git -C "${work}" checkout -q -b "${branch}" FETCH_HEAD
else
    echo "publish-metrics: ${branch} not found; bootstrapping orphan branch"
    git -C "${work}" checkout -q --orphan "${branch}"
fi

# Append this run's record to the historical series. If the existing file
# lacks a trailing newline (manual edit, conflict resolution), add one first so
# the last existing record and this one don't merge into one corrupt line.
if [[ -f "${work}/metrics.jsonl" && -n "$(tail -c 1 "${work}/metrics.jsonl")" ]]; then
    echo "" >> "${work}/metrics.jsonl"
fi
cat "${new_line}" >> "${work}/metrics.jsonl"

# Re-render the trend chart from the full history. --spec-output emits the
# Vega-Lite spec that fulgur-chart consumes below; the file is intermediate and
# not committed to the branch.
python3 "${repo_root}/scripts/plot-metrics.py" \
    --input "${work}/metrics.jsonl" \
    --output "${work}/trend.svg" \
    --spec-output "${work}/spec.json"

# Also render via fulgur-chart (dogfooding — sibling project still under
# active development). Invoked via npx so no persistent install is needed:
# GitHub Actions ubuntu-latest ships with Node.js, and --yes auto-installs the
# prebuilt binary from optionalDependencies. Best-effort: if npx is missing or
# fulgur-chart fails on the current spec, keep the previous trend-fulgur.svg
# on the branch and continue.
fulgur_ok=0
if command -v npx >/dev/null; then
    if npx --yes @fulgur-rs/chart-cli render "${work}/spec.json" \
        -o "${work}/trend-fulgur.svg" --dsl vegalite; then
        fulgur_ok=1
    else
        echo "publish-metrics: fulgur-chart render failed; keeping previous trend-fulgur.svg" >&2
        rm -f "${work}/trend-fulgur.svg"
    fi
else
    echo "publish-metrics: npx not on PATH; skipping dogfood chart" >&2
fi

# Regenerate the README each run so both charts are always referenced.
cat > "${work}/README.md" <<'EOF'
# qtest nightly metrics

Time series of the nightly qtest acceptance run, one JSON record per night in
`metrics.jsonl`. See the main branch for the harness itself.

## Trend (Vega-Lite via vl-convert)

![trend](trend.svg)

## Trend (fulgur-chart — dogfooding)

Rendered from the same Vega-Lite spec via [fulgur-chart](https://github.com/fulgur-rs/fulgur-chart)'s
`--dsl vegalite` subset. Kept alongside the canonical chart while fulgur-chart is under active
development; discrepancies are expected and are the point.

![trend-fulgur](trend-fulgur.svg)
EOF

git -C "${work}" add metrics.jsonl trend.svg README.md
if [[ ${fulgur_ok} -eq 1 && -f "${work}/trend-fulgur.svg" ]]; then
    git -C "${work}" add trend-fulgur.svg
fi
if git -C "${work}" diff --cached --quiet; then
    echo "publish-metrics: nothing to commit"
    exit 0
fi
git -C "${work}" commit -q -m "metrics: nightly $(date -u +%Y-%m-%d)"
git -C "${work}" push -q origin "HEAD:${branch}"
echo "publish-metrics: pushed to ${branch}"

# Link the chart from this job's Job Summary (nightly run only).
if [[ -n "${GITHUB_STEP_SUMMARY:-}" && -n "${GITHUB_REPOSITORY:-}" ]]; then
    {
        echo "## qtest nightly metrics"
        echo
        echo "Updated [trend chart](https://github.com/${GITHUB_REPOSITORY}/blob/${branch}/trend.svg) on the \`${branch}\` branch."
    } >> "${GITHUB_STEP_SUMMARY}"
fi
