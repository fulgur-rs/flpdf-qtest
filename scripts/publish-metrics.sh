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
# remote_public keeps the token out of .git/config while we run third-party
# code (npx fulgur-chart) in the same working tree — see the render step
# below. It is swapped back to `remote` right before push.
if [[ -n "${PUBLISH_METRICS_REMOTE:-}" ]]; then
    remote="${PUBLISH_METRICS_REMOTE}"
    remote_public="${PUBLISH_METRICS_REMOTE}"
else
    : "${GH_TOKEN:?publish-metrics: GH_TOKEN is required}"
    : "${GITHUB_REPOSITORY:?publish-metrics: GITHUB_REPOSITORY is required}"
    remote="https://x-access-token:${GH_TOKEN}@github.com/${GITHUB_REPOSITORY}.git"
    remote_public="https://github.com/${GITHUB_REPOSITORY}.git"
    # Token is now captured in ${remote}; drop it from the shell's
    # runtime environment so the fulgur render subshell (and everything
    # after it) does not inherit it. Git still authenticates against the
    # remote because the token is embedded in ${remote}'s URL — no env
    # var is required past this point.
    #
    # Residual: Linux keeps /proc/$$/environ pointing at bash's exec-time
    # env range, and `unset` does not deterministically rewrite it, so a
    # same-UID attacker walking the process tree (e.g. reading
    # /proc/$PPID/environ from a compromised chart-cli render) could
    # still recover the token during the script's lifetime. Fully closing
    # this would require re-execing bash to reset the range, which we do
    # not do here — the exposure is bounded by an ephemeral, repo-scoped
    # job token, and the layered defenses above (version pin, isolated
    # render dir, scrubbed auth remote, GH_TOKEN removed from render env)
    # reduce what a compromised package would successfully extract.
    unset GH_TOKEN
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

# Strip auth from origin so any code that reads .git/config (e.g. the fulgur
# render below) cannot see GH_TOKEN. The authenticated URL is restored right
# before push.
git -C "${work}" remote set-url origin "${remote_public}"

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

# Snapshot the canonical artifacts before invoking third-party code. Same-UID
# processes on the same /tmp can locate ${work} by scanning for /tmp/*/.git and
# tamper with the files we are about to stage — moving the render to a sibling
# mktemp dir is cosmetic, since chart-cli would still run under the same user
# and could find and edit ${work}/metrics.jsonl or trend.svg before the `git
# add` below. The full fix is OS-level isolation (a distinct uid or `unshare`
# namespaces); pending that, this hash pair is a fail-close tripwire that
# refuses to push if either file changed while the render was running.
pre_metrics_sha=$(sha256sum "${work}/metrics.jsonl" | awk '{print $1}')
pre_trend_sha=$(sha256sum "${work}/trend.svg" | awk '{print $1}')

# Also render via fulgur-chart (dogfooding — sibling project still under
# active development). Invoked via npx so no persistent install is needed:
# GitHub Actions ubuntu-latest ships with Node.js, and --yes auto-installs the
# prebuilt binary from optionalDependencies. Best-effort: if npx is missing or
# fulgur-chart fails on the current spec, keep the previous trend-fulgur.svg
# on the branch and continue.
#
# Supply-chain hardening:
#   1. Pin to a reviewed version rather than `latest`. Bump the pin
#      deliberately after reviewing release notes; that bump is what
#      keeps this a real dogfooding target.
#   2. Run the render inside an isolated scratch directory that contains
#      only a copy of the input spec — no ${work}, no .git/, no metrics
#      or README files. A compromised chart-cli package therefore has no
#      neighbouring artifacts to tamper with before the later `git add`
#      stages them.
#   3. GH_TOKEN was already dropped from the environment at the top of
#      the script; env -u is a belt-and-suspenders scrub on the child.
#   4. On failure, restore the previously-tracked trend-fulgur.svg (if
#      any) from the index instead of leaving the working tree with an
#      unstaged deletion — otherwise the README's conditional link would
#      quietly drop even though the SVG still exists on the branch.
FULGUR_CHART_CLI_VERSION="0.1.20"
fulgur_ok=0
if command -v npx >/dev/null; then
    render_dir="$(mktemp -d)"
    cp "${work}/spec.json" "${render_dir}/spec.json"
    if (cd "${render_dir}" && env -u GH_TOKEN npx --yes \
            "@fulgur-rs/chart-cli@${FULGUR_CHART_CLI_VERSION}" \
            render spec.json -o trend-fulgur.svg --dsl vegalite); then
        if head -c 5 "${render_dir}/trend-fulgur.svg" 2>/dev/null | grep -q '<svg'; then
            cp "${render_dir}/trend-fulgur.svg" "${work}/trend-fulgur.svg"
            fulgur_ok=1
        else
            echo "publish-metrics: fulgur-chart output was not SVG; keeping previous" >&2
        fi
    else
        echo "publish-metrics: fulgur-chart render failed; keeping previous trend-fulgur.svg" >&2
    fi
    rm -rf "${render_dir}"
    if [[ ${fulgur_ok} -eq 0 ]]; then
        # Restore the tracked version so the README's conditional section
        # continues to link it. If the file was never tracked (fresh
        # bootstrap), checkout errors and we fall through to rm -f, which
        # clears any partial write left in ${work}.
        git -C "${work}" checkout -- trend-fulgur.svg 2>/dev/null || \
            rm -f "${work}/trend-fulgur.svg"
    fi
else
    echo "publish-metrics: npx not on PATH; skipping dogfood chart" >&2
fi

# Verify the tripwire snapshotted above: if either canonical artifact was
# rewritten by chart-cli (or any other same-UID process that raced with us),
# refuse to publish. This makes the demonstrated /tmp-enumeration attack
# fail-close instead of quietly landing tampered content on the branch.
post_metrics_sha=$(sha256sum "${work}/metrics.jsonl" | awk '{print $1}')
post_trend_sha=$(sha256sum "${work}/trend.svg" | awk '{print $1}')
if [[ "${pre_metrics_sha}" != "${post_metrics_sha}" \
      || "${pre_trend_sha}" != "${post_trend_sha}" ]]; then
    echo "publish-metrics: SECURITY — canonical artifacts changed during fulgur render, refusing to push" >&2
    echo "publish-metrics:   metrics.jsonl: ${pre_metrics_sha} -> ${post_metrics_sha}" >&2
    echo "publish-metrics:   trend.svg:     ${pre_trend_sha} -> ${post_trend_sha}" >&2
    exit 1
fi

# Regenerate the README each run: the canonical Vega-Lite section is always
# present; the fulgur-chart section is only appended when trend-fulgur.svg
# exists in the working tree (either freshly rendered this run, or already
# tracked on the branch from a prior successful run). This avoids linking a
# non-existent image on the initial bootstrap when npx is missing / fulgur
# fails before any trend-fulgur.svg has ever been committed.
cat > "${work}/README.md" <<'EOF'
# qtest nightly metrics

Time series of the nightly qtest acceptance run, one JSON record per night in
`metrics.jsonl`. See the main branch for the harness itself.

## Trend (Vega-Lite via vl-convert)

![trend](trend.svg)
EOF
if [[ -f "${work}/trend-fulgur.svg" ]]; then
    cat >> "${work}/README.md" <<'EOF'

## Trend (fulgur-chart — dogfooding)

Rendered from the same Vega-Lite spec via [fulgur-chart](https://github.com/fulgur-rs/fulgur-chart)'s
`--dsl vegalite` subset. Kept alongside the canonical chart while fulgur-chart is under active
development; discrepancies are expected and are the point.

![trend-fulgur](trend-fulgur.svg)
EOF
fi

git -C "${work}" add metrics.jsonl trend.svg README.md
if [[ ${fulgur_ok} -eq 1 && -f "${work}/trend-fulgur.svg" ]]; then
    git -C "${work}" add trend-fulgur.svg
fi
if git -C "${work}" diff --cached --quiet; then
    echo "publish-metrics: nothing to commit"
    exit 0
fi
git -C "${work}" commit -q -m "metrics: nightly $(date -u +%Y-%m-%d)"
# Restore the authenticated remote for push (was swapped to public above so
# GH_TOKEN was not visible during the fulgur render).
git -C "${work}" remote set-url origin "${remote}"
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
