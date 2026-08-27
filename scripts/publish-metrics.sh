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
new_line="${1:?usage: publish-metrics.sh <new-metrics-line.jsonl> [parity-line.jsonl]}"
# Optional: the parity ledger's own record for this run. Absent on older
# artifacts (the parity series only exists from 2026-07-30), so the parity
# history and its charts are simply left untouched when it is missing.
parity_line="${2:-}"

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
# Disable all git hooks in the throwaway clone. `${work}/.git/hooks/*` would
# otherwise be an arbitrary-code-execution surface for a compromised render
# (see Codex round-4): a malicious @fulgur-rs/chart-cli that reaches ${work}
# via /tmp enumeration could plant a pre-commit / pre-push / post-checkout /
# etc. hook that runs during the subsequent git operations. Pointing
# core.hooksPath at /dev/null makes git ignore any hook file it finds.
git -C "${work}" config core.hooksPath /dev/null
# Scope the bot identity to this throwaway clone — never touch global config.
git -C "${work}" config user.name "github-actions[bot]"
git -C "${work}" config user.email "41898282+github-actions[bot]@users.noreply.github.com"
git -C "${work}" remote add origin "${remote}"
if git -C "${work}" fetch -q --depth 1 origin "${branch}"; then
    git -C "${work}" checkout -q -b "${branch}" FETCH_HEAD
    parent_sha=$(git -C "${work}" rev-parse HEAD)
else
    echo "publish-metrics: ${branch} not found; bootstrapping orphan branch"
    git -C "${work}" checkout -q --orphan "${branch}"
    parent_sha=""
fi

# Strip auth from origin so any code that reads .git/config (e.g. the fulgur
# render below) cannot see GH_TOKEN. The authenticated URL is restored right
# before push.
git -C "${work}" remote set-url origin "${remote_public}"

# Generate all canonical artifacts inside a trust_dir that stays out of
# ${work} for the duration of the third-party render. The commit itself is
# assembled from git blob SHAs captured *before* any third-party code runs,
# so a compromised chart-cli that later tampers with ${work} (staging
# EVIL.txt, symlinking metrics.jsonl, planting hooks — all demonstrated by
# Codex) cannot affect what ends up on the metrics-data branch. The working
# tree is only used as scratch space for the render itself.
trust_dir="$(mktemp -d)"
trap '[[ -n "${work:-}" ]] && rm -rf "${work}"; [[ -n "${trust_dir:-}" ]] && rm -rf "${trust_dir}"' EXIT

# Seed metrics.jsonl from the previous branch content, if any.
if [[ -f "${work}/metrics.jsonl" ]]; then
    cp "${work}/metrics.jsonl" "${trust_dir}/metrics.jsonl"
fi
if [[ -f "${trust_dir}/metrics.jsonl" && -n "$(tail -c 1 "${trust_dir}/metrics.jsonl")" ]]; then
    echo "" >> "${trust_dir}/metrics.jsonl"
fi
cat "${new_line}" >> "${trust_dir}/metrics.jsonl"

# Same for the parity ledger's history, when this run produced one.
parity_ok=0
if [[ -s "${parity_line}" ]]; then
    if [[ -f "${work}/parity-metrics.jsonl" ]]; then
        cp "${work}/parity-metrics.jsonl" "${trust_dir}/parity-metrics.jsonl"
    fi
    if [[ -f "${trust_dir}/parity-metrics.jsonl" \
        && -n "$(tail -c 1 "${trust_dir}/parity-metrics.jsonl")" ]]; then
        echo "" >> "${trust_dir}/parity-metrics.jsonl"
    fi
    cat "${parity_line}" >> "${trust_dir}/parity-metrics.jsonl"
    parity_ok=1
elif [[ -f "${work}/parity-metrics.jsonl" ]]; then
    # No new record, but a history exists: carry it forward unchanged so the
    # branch keeps the file and its charts.
    cp "${work}/parity-metrics.jsonl" "${trust_dir}/parity-metrics.jsonl"
    parity_ok=1
fi

# Re-render the trend chart from the full history. --spec-output emits the
# Vega-Lite spec that fulgur-chart consumes below; the file is intermediate and
# not committed to the branch.
python3 "${repo_root}/scripts/plot-metrics.py" \
    --input "${trust_dir}/metrics.jsonl" \
    --output "${trust_dir}/trend.svg" \
    --spec-output "${trust_dir}/spec.json"
if [[ ${parity_ok} -eq 1 ]]; then
    python3 "${repo_root}/scripts/plot-metrics.py" \
        --input "${trust_dir}/parity-metrics.jsonl" \
        --output "${trust_dir}/trend-parity.svg" \
        --spec-output "${trust_dir}/spec-parity.json" \
        --series parity
fi

# Capture blob SHAs *before* the render. `hash-object -w` reads each file
# (currently a regular file we just wrote — no symlink) and stores the
# corresponding blob under ${work}/.git/objects/. From this point on the
# commit content is pinned by SHA in a bash variable; if the render (or any
# same-UID process racing with us) later replaces trust_dir/metrics.jsonl
# with a symlink or rewrites it, the tree we push still points at the
# original bytes because we assemble it from these captured SHAs, not from
# the working tree at commit time.
metrics_blob=$(git -C "${work}" hash-object -w -- "${trust_dir}/metrics.jsonl")
trend_blob=$(git -C "${work}" hash-object -w -- "${trust_dir}/trend.svg")
parity_metrics_blob=""
parity_trend_blob=""
if [[ ${parity_ok} -eq 1 ]]; then
    parity_metrics_blob=$(git -C "${work}" hash-object -w -- "${trust_dir}/parity-metrics.jsonl")
    parity_trend_blob=$(git -C "${work}" hash-object -w -- "${trust_dir}/trend-parity.svg")
fi

# Also render via fulgur-chart (dogfooding — sibling project still under
# active development). Invoked via npx so no persistent install is needed:
# GitHub Actions ubuntu-latest ships with Node.js, and --yes auto-installs the
# prebuilt binary from optionalDependencies. Best-effort: if npx is missing or
# fulgur-chart fails on the current spec, we reuse the previous branch's
# trend-fulgur.svg blob (via parent_sha) and continue.
#
# Supply-chain hardening:
#   1. Pin to a reviewed version rather than `latest`. Bump the pin
#      deliberately after reviewing release notes; that bump is what
#      keeps this a real dogfooding target.
#   2. Run the render in an isolated scratch directory that contains
#      only a copy of the input spec — no ${work}, no .git/, no
#      canonical artifacts. A compromised chart-cli can still enumerate
#      /tmp and find ${work} or ${trust_dir}, but any tampering it does
#      there is inert because the commit is built from blob SHAs
#      captured above.
#   3. GH_TOKEN is unset in the shell and env -u is a belt-and-suspenders
#      scrub on the child.
FULGUR_CHART_CLI_VERSION="0.1.20"

# Render one spec via fulgur-chart. $1 = spec path, $2 = output basename.
# Echoes nothing; sets the named variable in $3 to 1 on success. Factored out
# so the allowlist and parity charts share one hardened invocation rather than
# two copies drifting apart.
render_fulgur() {
    local spec_path="$1" out_name="$2" __ok_var="$3"
    local render_dir
    printf -v "${__ok_var}" '%s' 0
    if [[ ! -s "${spec_path}" ]]; then
        return 0
    fi
    render_dir="$(mktemp -d)"
    cp "${spec_path}" "${render_dir}/spec.json"
    # 120s covers the cold-cache `npx --yes` install (registry fetch of the
    # pinned prebuilt binary) plus the actual render. If chart-cli or the
    # registry hangs, timeout kills it so the nightly job doesn't sit blocked
    # waiting on network I/O with no upper bound. --kill-after=5s escalates
    # to SIGKILL if the initial SIGTERM is ignored.
    if (cd "${render_dir}" && env -u GH_TOKEN \
            timeout --kill-after=5s 120s npx --yes \
            "@fulgur-rs/chart-cli@${FULGUR_CHART_CLI_VERSION}" \
            render spec.json -o "${out_name}" --dsl vegalite); then
        # Reject anything but a regular file (not a symlink, FIFO, socket,
        # or device) before touching the output. A compromised chart-cli
        # that mkfifo'd the output path would otherwise cause `head` to
        # block on the FIFO past the render timeout — the 120s cap above
        # only wraps npx, not this validation. `-f` is false for FIFOs and
        # sockets, and `! -L` rules out symlinks that could point at an
        # arbitrary slow file.
        if [[ -f "${render_dir}/${out_name}" && ! -L "${render_dir}/${out_name}" ]] \
            && head -c 5 "${render_dir}/${out_name}" 2>/dev/null | grep -q '<svg'; then
            cp "${render_dir}/${out_name}" "${trust_dir}/${out_name}"
            printf -v "${__ok_var}" '%s' 1
        else
            echo "publish-metrics: fulgur-chart output for ${out_name} was not a regular SVG file; keeping previous" >&2
        fi
    else
        echo "publish-metrics: fulgur-chart render failed for ${out_name}; keeping previous" >&2
    fi
    rm -rf "${render_dir}"
}

fulgur_ok=0
fulgur_parity_ok=0
if command -v npx >/dev/null; then
    render_fulgur "${trust_dir}/spec.json" trend-fulgur.svg fulgur_ok
    if [[ ${parity_ok} -eq 1 ]]; then
        render_fulgur "${trust_dir}/spec-parity.json" trend-parity-fulgur.svg fulgur_parity_ok
    fi
else
    echo "publish-metrics: npx not on PATH; skipping dogfood charts" >&2
fi

# Resolve trend-fulgur.svg's blob. When the render succeeded, hash the fresh
# output. When it did not, reuse the blob from the parent commit so the README
# section stays linked to a valid file. When neither is available (first
# bootstrap with a failing render), leave it empty and let the README section
# fall through.
fulgur_blob=""
if [[ ${fulgur_ok} -eq 1 ]]; then
    fulgur_blob=$(git -C "${work}" hash-object -w -- "${trust_dir}/trend-fulgur.svg")
elif [[ -n "${parent_sha}" ]]; then
    # --verify -q: without it, `git rev-parse <sha>:<missing-path>` prints
    # the literal revspec to stdout (before erroring on stderr and exiting
    # 128), which the `|| echo ""` fallback then captures as garbage into
    # fulgur_blob and breaks mktree. --verify -q keeps stdout empty on
    # miss so the || fallback resolves to the empty string as intended.
    fulgur_blob=$(git -C "${work}" rev-parse --verify -q "${parent_sha}:trend-fulgur.svg" 2>/dev/null || echo "")
fi

parity_fulgur_blob=""
if [[ ${fulgur_parity_ok} -eq 1 ]]; then
    parity_fulgur_blob=$(git -C "${work}" hash-object -w -- "${trust_dir}/trend-parity-fulgur.svg")
elif [[ -n "${parent_sha}" ]]; then
    parity_fulgur_blob=$(git -C "${work}" rev-parse --verify -q "${parent_sha}:trend-parity-fulgur.svg" 2>/dev/null || echo "")
fi

# Compose README section by section, hash the bytes directly via --stdin so no
# tampering vector along the trust_dir path can influence it, and record its
# blob SHA. A section is emitted only when its chart resolved to a blob, so the
# README never links a file the tree does not carry.
readme_text="# qtest nightly metrics

Time series of the nightly qtest acceptance run, one JSON record per night in
\`metrics.jsonl\`. See the main branch for the harness itself.

## Trend (Vega-Lite via vl-convert)

![trend](trend.svg)
"
if [[ -n "${fulgur_blob}" ]]; then
    readme_text+="
## Trend (fulgur-chart — dogfooding)

Rendered from the same Vega-Lite spec via [fulgur-chart](https://github.com/fulgur-rs/fulgur-chart)'s
\`--dsl vegalite\` subset. Kept alongside the canonical chart while fulgur-chart is under active
development; discrepancies are expected and are the point.

![trend-fulgur](trend-fulgur.svg)
"
fi
if [[ -n "${parity_metrics_blob}" ]]; then
    readme_text+="
## Parity ledger trend (Vega-Lite via vl-convert)

Ledger state counts from \`parity-metrics.jsonl\`. \`passing\` should climb while
\`blocked\` and \`failing\` drain into it. \`excluded\` is omitted — it is a scope
decision, not progress. The series starts 2026-07-30, when the parity manifest
was introduced; nights before that have no parity data to plot.

![trend-parity](trend-parity.svg)
"
fi
if [[ -n "${parity_fulgur_blob}" ]]; then
    readme_text+="
## Parity ledger trend (fulgur-chart — dogfooding)

![trend-parity-fulgur](trend-parity-fulgur.svg)
"
fi
readme_blob=$(printf '%s' "${readme_text}" | git -C "${work}" hash-object -w --stdin)

# Assemble the tree from the captured blob SHAs. This is the pivotal step:
# `mktree` builds the tree object purely from these SHAs, never consulting
# the working tree or the index, so any state a compromised render left
# behind in ${work} (staged EVIL.txt, symlinked metrics.jsonl, planted
# hooks) has no way to influence what we are about to push.
# Pipe the tree spec directly into mktree — never through a disk file that
# a same-UID watcher spawned by the render could rewrite between write and
# read. (Codex demonstrated pushing an unlisted `zzz.txt` blob by mutating
# such an intermediate file.)
tree_sha=$({
    printf '100644 blob %s\tREADME.md\n' "${readme_blob}"
    printf '100644 blob %s\tmetrics.jsonl\n' "${metrics_blob}"
    printf '100644 blob %s\ttrend.svg\n' "${trend_blob}"
    if [[ -n "${fulgur_blob}" ]]; then
        printf '100644 blob %s\ttrend-fulgur.svg\n' "${fulgur_blob}"
    fi
    if [[ -n "${parity_metrics_blob}" ]]; then
        printf '100644 blob %s\tparity-metrics.jsonl\n' "${parity_metrics_blob}"
        printf '100644 blob %s\ttrend-parity.svg\n' "${parity_trend_blob}"
    fi
    if [[ -n "${parity_fulgur_blob}" ]]; then
        printf '100644 blob %s\ttrend-parity-fulgur.svg\n' "${parity_fulgur_blob}"
    fi
} | git -C "${work}" mktree)

# Skip when the resulting tree is identical to the parent commit's tree
# (e.g. the fulgur render failed and there's no new metrics record).
if [[ -n "${parent_sha}" ]]; then
    parent_tree=$(git -C "${work}" rev-parse "${parent_sha}^{tree}")
    if [[ "${tree_sha}" == "${parent_tree}" ]]; then
        echo "publish-metrics: nothing to commit"
        exit 0
    fi
fi

# Build the commit object from the tree + the parent (if any). Note that we
# never run `git commit`, which means: (a) no hooks are triggered even if
# something slipped past core.hooksPath, and (b) attacker-staged files in
# the index (`git -C ${work} add EVIL.txt` from a compromised render) cannot
# be committed — the index is not involved.
commit_args=("${tree_sha}")
[[ -n "${parent_sha}" ]] && commit_args=("${commit_args[@]}" -p "${parent_sha}")
commit_sha=$(printf 'metrics: nightly %s\n' "$(date -u +%Y-%m-%d)" | \
    git -C "${work}" commit-tree "${commit_args[@]}")

# Push directly with the authenticated URL on the command line — never
# reintroduce it into `.git/config` via `remote set-url origin`. Writing
# the token back to the throwaway clone's config after the untrusted
# render has run would give any same-UID watcher that outlived npx a
# window to read it (Codex round-5). Passing the URL as an argv token to
# `git push` still exposes it briefly via /proc/<push-pid>/cmdline for
# same-UID observers; fully hiding it would require a credential-helper
# file or an isolated push process (systemd-run / container), which is
# not addressed here — the residual is bounded by an ephemeral,
# repo-scoped job token. --no-verify bypasses any pre-push hook file
# (belt-and-suspenders — core.hooksPath already neutralises them).
git -C "${work}" push -q --no-verify "${remote}" "${commit_sha}:refs/heads/${branch}"
echo "publish-metrics: pushed to ${branch}"

# Link the chart from this job's Job Summary (nightly run only).
if [[ -n "${GITHUB_STEP_SUMMARY:-}" && -n "${GITHUB_REPOSITORY:-}" ]]; then
    {
        echo "## qtest nightly metrics"
        echo
        echo "Updated [trend chart](https://github.com/${GITHUB_REPOSITORY}/blob/${branch}/trend.svg) on the \`${branch}\` branch."
    } >> "${GITHUB_STEP_SUMMARY}"
fi
