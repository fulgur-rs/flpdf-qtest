#!/usr/bin/env bash
#
# scripts/run.sh — drive the qtest acceptance suite against flpdf-cli.
#
# Required env:
#   FLPDF_CLI_BIN           Absolute path to a built flpdf binary (the
#                           flpdf-cli crate builds a binary literally named
#                           `flpdf`). If unset, resolution order is:
#                             1. FLPDF_DIR is set → build there and use
#                                ${FLPDF_DIR}/target/release/flpdf. FLPDF_DIR
#                                is treated as an explicit dev-loop override
#                                and takes precedence over any pre-existing
#                                repo-layout artifact so that iterating on
#                                flpdf always uses fresh code.
#                             2. Otherwise, use ./flpdf/target/release/flpdf
#                                if it exists (matches the CI checkout layout).
#                             3. Otherwise, error out.
#   FLPDF_TEST_COMPARE_BIN  Absolute path to a built flpdf-test-compare
#                           binary (the Rust port of qpdf's compare-for-test,
#                           used by shim/qpdf-test-compare). Same resolution
#                           order as FLPDF_CLI_BIN.
#   FLPDF_TEST_DRIVER_BIN   Absolute path to a built flpdf-test-driver
#                           binary (the Rust port of qpdf's test_driver.cc,
#                           used by shim/test_driver). Same resolution
#                           order as FLPDF_CLI_BIN.
#
# Optional env:
#   FLPDF_DIR      Absolute path to a flpdf checkout. If set and any of
#                  FLPDF_CLI_BIN / FLPDF_TEST_COMPARE_BIN /
#                  FLPDF_TEST_DRIVER_BIN is not, the script runs
#                  `cargo build --release --bin flpdf --bin flpdf-test-compare --bin flpdf-test-driver`
#                  there and always uses those freshly-built binaries.
#   QTEST_FULL     When "1", run every *.test in vendor/qpdf-qtest/.
#
# Outputs:
#   harness.log         — full qtest-driver stdout+stderr captured by tee
#   qtest-results.xml   — qtest-driver's authoritative per-subtest results;
#                         consumed together with harness.log by
#                         verify-allowlist.py.
#   qtest.log           — qtest-driver's own testlog (failure dumps).
#                         qtest-driver writes this in cwd unconditionally
#                         (`my $testlogfile = 'qtest.log';` upstream),
#                         which is why we MUST NOT name our tee target
#                         qtest.log — qtest-driver `unlink`s it, breaking
#                         the tee fd and silently losing our columnar
#                         status lines.
#   qtest-summary.md    — verify-allowlist.py judgment
#   qtest-parity-summary.md — verify-parity-manifest.py judgment for the
#                              complete qtest survey only

set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
cd "${repo_root}"

# Clear every generated artifact before binary resolution or any other
# preflight check. A failed invocation must never leave a previous success
# looking current.
log="${repo_root}/harness.log"
qtest_log="${repo_root}/qtest.log"
qtest_xml="${repo_root}/qtest-results.xml"
qtest_junit="${repo_root}/TEST-qtest.xml"
summary="${repo_root}/qtest-summary.md"
metrics="${repo_root}/qtest-metrics.jsonl"
manifest="${repo_root}/parity/qtest-11.9.0.jsonl"
parity_summary="${repo_root}/qtest-parity-summary.md"
rm -f \
    "${log}" \
    "${qtest_log}" \
    "${qtest_xml}" \
    "${qtest_junit}" \
    "${summary}" \
    "${metrics}" \
    "${parity_summary}"

# --- locate flpdf-cli and flpdf-test-compare ---------------------------------
#
# Both binaries come from the same flpdf workspace, so if we're building
# from FLPDF_DIR do it in a single cargo invocation (they share the
# target dir and flpdf as a dependency; the second binary is essentially
# free once flpdf-cli's dependency graph is compiled).
#
# Select them with `--bin`, not `-p`. The binary names are what this harness
# actually consumes (FLPDF_CLI_BIN / FLPDF_TEST_COMPARE_BIN point at
# target/release/<bin>, and shim/qpdf-test-compare execs that path), whereas
# the package names are an flpdf-internal layout detail that can change
# without any observable difference here. Keeping this off package names
# means an flpdf-side crate reorganization cannot break this repository's CI.

need_build=0
if [[ -z "${FLPDF_CLI_BIN:-}" ]]; then
    if [[ -n "${FLPDF_DIR:-}" ]]; then
        need_build=1
        FLPDF_CLI_BIN="${FLPDF_DIR}/target/release/flpdf"
    elif [[ -x "${repo_root}/flpdf/target/release/flpdf" ]]; then
        FLPDF_CLI_BIN="${repo_root}/flpdf/target/release/flpdf"
    else
        echo "run.sh: cannot locate flpdf-cli (set FLPDF_CLI_BIN or FLPDF_DIR)" >&2
        exit 2
    fi
fi

if [[ -z "${FLPDF_TEST_COMPARE_BIN:-}" ]]; then
    if [[ -n "${FLPDF_DIR:-}" ]]; then
        need_build=1
        FLPDF_TEST_COMPARE_BIN="${FLPDF_DIR}/target/release/flpdf-test-compare"
    elif [[ -x "${repo_root}/flpdf/target/release/flpdf-test-compare" ]]; then
        FLPDF_TEST_COMPARE_BIN="${repo_root}/flpdf/target/release/flpdf-test-compare"
    else
        echo "run.sh: cannot locate flpdf-test-compare (set FLPDF_TEST_COMPARE_BIN or FLPDF_DIR)" >&2
        exit 2
    fi
fi

if [[ -z "${FLPDF_TEST_DRIVER_BIN:-}" ]]; then
    if [[ -n "${FLPDF_DIR:-}" ]]; then
        need_build=1
        FLPDF_TEST_DRIVER_BIN="${FLPDF_DIR}/target/release/flpdf-test-driver"
    elif [[ -x "${repo_root}/flpdf/target/release/flpdf-test-driver" ]]; then
        FLPDF_TEST_DRIVER_BIN="${repo_root}/flpdf/target/release/flpdf-test-driver"
    else
        echo "run.sh: cannot locate flpdf-test-driver (set FLPDF_TEST_DRIVER_BIN or FLPDF_DIR)" >&2
        exit 2
    fi
fi

if [[ ${need_build} -eq 1 ]]; then
    echo "==> Building flpdf, flpdf-test-compare and flpdf-test-driver in ${FLPDF_DIR}"
    ( cd "${FLPDF_DIR}" && cargo build --release --bin flpdf --bin flpdf-test-compare --bin flpdf-test-driver )
fi

export FLPDF_CLI_BIN
export FLPDF_TEST_COMPARE_BIN
export FLPDF_TEST_DRIVER_BIN

for bin in "${FLPDF_CLI_BIN}" "${FLPDF_TEST_COMPARE_BIN}" "${FLPDF_TEST_DRIVER_BIN}"; do
    if [[ ! -x "${bin}" ]]; then
        echo "run.sh: ${bin} is not executable" >&2
        exit 2
    fi
done

# --- isolate the vendored qtest datadir -------------------------------------
#
# qtest suites may write relative output beneath their datadir. Run every
# invocation against a disposable copy so one survey cannot modify the
# vendored corpus or affect the next survey's inputs.

if ! run_tmp="$(mktemp -d)"; then
    echo "run.sh: cannot create temporary qtest run directory" >&2
    exit 2
fi
trap 'rm -rf "${run_tmp}"' EXIT

qtest_source="${repo_root}/vendor/qpdf-qtest"
qtest_datadir="${run_tmp}/qpdf-qtest"
if ! cp -a --reflink=auto "${qtest_source}" "${qtest_datadir}"; then
    echo \
        "run.sh: failed to create isolated qtest datadir from ${qtest_source} (copy/reflink failed; check available space)" \
        >&2
    exit 2
fi

# --- prepare shim PATH -------------------------------------------------------
#
# Copy every executable in shim/ — not just qpdf — so any qpdf-side helper
# a .test invokes (fix-qdf, zlib-flate, etc.) is intercepted. Hosts where
# the qpdf apt package is installed ship /usr/bin/fix-qdf and
# /usr/bin/zlib-flate, which would otherwise silently shadow the test and
# produce spurious PASSes that don't reflect flpdf. The stubs in shim/
# fail loudly, so survey numbers from local runs match CI.

shim_bin="${run_tmp}/shim"
mkdir "${shim_bin}"
for shim in "${repo_root}"/shim/*; do
    [[ -f "${shim}" && -x "${shim}" ]] || continue
    name="$(basename "${shim}")"
    cp "${shim}" "${shim_bin}/${name}"
done

export PATH="${shim_bin}:${PATH}"
export FLPDF_QPDF_COMPAT=1
export FLPDF_QTEST_NORMALIZE="${repo_root}/normalize/stderr-rules.sed"

# --- decide which .test stems to run ----------------------------------------

if [[ "${QTEST_FULL:-0}" == "1" ]]; then
    mapfile -t stems < <(
        find "${qtest_datadir}" -maxdepth 1 -name '*.test' \
            -printf '%f\n' | sed 's/\.test$//' | sort -u
    )
else
    # This read distinguishes the supported empty-allowlist bring-up probe
    # from a nonempty partial survey, which is rejected before execution.
    mapfile -t stems < <(
        awk '
            { sub(/#.*/,""); gsub(/^[[:space:]]+|[[:space:]]+$/,""); }
            $0 == "" { next }
            { split($0, p, ":"); print p[1] }
        ' "${repo_root}/allowlist.txt" | sort -u
    )
fi

if [[ "${QTEST_FULL:-0}" == "1" && ${#stems[@]} -eq 0 ]]; then
    echo "run.sh: full qtest corpus contains no vendored .test suites" >&2
    exit 2
fi

# --- run qtest-driver --------------------------------------------------------

: > "${log}"

if [[ ${#stems[@]} -eq 0 ]]; then
    # Empty allowlist — we still want CI to assert that the harness boots, so
    # dry-run qtest-driver with --version and write an empty results section.
    echo "==> Allowlist is empty."
    {
        echo "qtest-driver dry run (allowlist empty)"
        perl "${repo_root}/vendor/qtest/bin/qtest-driver" --version
    } | tee -a "${log}"
else
    if [[ "${QTEST_FULL:-0}" != "1" ]]; then
        echo "run.sh: parity manifest validation requires QTEST_FULL=1" >&2
        exit 2
    fi

    echo "==> Running qtest-driver on: ${stems[*]}"
    TESTS="${stems[*]}" \
    perl "${repo_root}/vendor/qtest/bin/qtest-driver" \
        -datadir "${qtest_datadir}" \
        -bindirs "${shim_bin}" \
        -stdout-tty=0 \
        2>&1 | tee -a "${log}" || true

    if [[ ! -f "${qtest_xml}" ]]; then
        echo "run.sh: qtest results XML not found: ${qtest_xml}" >&2
        exit 1
    fi
fi

# --- verify against allowlist -----------------------------------------------

if [[ ${#stems[@]} -eq 0 ]]; then
    # No subtest lines will be present; emit a minimal summary directly.
    cat > "${summary}" <<EOF
# qtest-summary

- Allowlist is empty — no subtests required to pass.
- Harness bring-up verified: qtest-driver $(perl "${repo_root}/vendor/qtest/bin/qtest-driver" --version | head -n1).

**Verdict: OK (empty allowlist)**
EOF
    cat "${summary}"
    # Surface the same minimal summary in the CI Job Summary (append). If a
    # prior step left content without a trailing newline, separate it first so
    # our header starts on its own line.
    if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
        [[ -s "${GITHUB_STEP_SUMMARY}" ]] && echo "" >> "${GITHUB_STEP_SUMMARY}"
        cat "${summary}" >> "${GITHUB_STEP_SUMMARY}"
    fi
    exit 0
fi

# Under GitHub Actions, also append a headline (counts + regressions, no long
# candidate list) to the run's Job Summary. Locally GITHUB_STEP_SUMMARY is
# unset, so this is a no-op and only the full qtest-summary.md is written.
verify_args=(
    "${log}"
    "${qtest_xml}"
    "${repo_root}/allowlist.txt"
    --summary "${summary}"
)
if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
    verify_args+=( --step-summary "${GITHUB_STEP_SUMMARY}" )
fi

# Always emit a single metrics line for this run (harmless locally). CI decides
# whether to persist it to the metrics-data history. FLPDF_COMMIT is the SHA of
# the flpdf checkout under test, supplied by CI; empty locally.
: > "${metrics}"
verify_args+=(
    --metrics "${metrics}"
    --commit "${FLPDF_COMMIT:-}"
    --timestamp "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
)

python3 "${repo_root}/scripts/verify-allowlist.py" "${verify_args[@]}"

# The allowlist is intentionally a small acceptance subset. Only a full qtest
# survey can be compared to the complete parity ledger, and this runs after
# the allowlist verifier so its existing verdict remains the first gate.
parity_args=(
    "${log}"
    "${qtest_xml}"
    "${manifest}"
    --summary "${parity_summary}"
)
if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
    parity_args+=( --step-summary "${GITHUB_STEP_SUMMARY}" )
fi
python3 "${repo_root}/scripts/verify-parity-manifest.py" "${parity_args[@]}"
