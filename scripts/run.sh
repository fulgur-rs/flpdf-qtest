#!/usr/bin/env bash
#
# scripts/run.sh — drive the qtest acceptance suite against flpdf-cli.
#
# Required env:
#   FLPDF_CLI_BIN  Absolute path to a built flpdf binary (the flpdf-cli crate
#                  builds a binary literally named `flpdf`). If unset, the
#                  script will look for ./flpdf/target/release/flpdf
#                  (matching the CI checkout layout) and finally fall back
#                  to a workspace-level `cargo build` if FLPDF_DIR is set.
#
# Optional env:
#   FLPDF_DIR      Absolute path to a flpdf checkout. If set and
#                  FLPDF_CLI_BIN is not, the script runs
#                  `cargo build --release -p flpdf-cli` there.
#   QTEST_TESTS    Space-separated list of .test stems to run. If unset,
#                  every .test stem mentioned in allowlist.txt is run, plus
#                  any --full=1 sentinel inclusion.
#   QTEST_FULL     When "1", run every *.test in vendor/qpdf-qtest/.
#
# Outputs:
#   harness.log         — full qtest-driver stdout+stderr captured by tee
#   qtest.log           — qtest-driver's own testlog (failure dumps).
#                         qtest-driver writes this in cwd unconditionally
#                         (`my $testlogfile = 'qtest.log';` upstream),
#                         which is why we MUST NOT name our tee target
#                         qtest.log — qtest-driver `unlink`s it, breaking
#                         the tee fd and silently losing our columnar
#                         status lines.
#   qtest-summary.md    — verify-allowlist.py judgment

set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
cd "${repo_root}"

# --- locate flpdf-cli --------------------------------------------------------

if [[ -z "${FLPDF_CLI_BIN:-}" ]]; then
    if [[ -n "${FLPDF_DIR:-}" ]]; then
        echo "==> Building flpdf-cli in ${FLPDF_DIR}"
        ( cd "${FLPDF_DIR}" && cargo build --release -p flpdf-cli )
        FLPDF_CLI_BIN="${FLPDF_DIR}/target/release/flpdf"
    elif [[ -x "${repo_root}/flpdf/target/release/flpdf" ]]; then
        FLPDF_CLI_BIN="${repo_root}/flpdf/target/release/flpdf"
    else
        echo "run.sh: cannot locate flpdf-cli (set FLPDF_CLI_BIN or FLPDF_DIR)" >&2
        exit 2
    fi
fi
export FLPDF_CLI_BIN

if [[ ! -x "${FLPDF_CLI_BIN}" ]]; then
    echo "run.sh: FLPDF_CLI_BIN=${FLPDF_CLI_BIN} is not executable" >&2
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

shim_bin="$(mktemp -d)"
trap 'rm -rf "${shim_bin}"' EXIT
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
        find "${repo_root}/vendor/qpdf-qtest" -maxdepth 1 -name '*.test' \
            -printf '%f\n' | sed 's/\.test$//' | sort -u
    )
elif [[ -n "${QTEST_TESTS:-}" ]]; then
    read -r -a stems <<< "${QTEST_TESTS}"
else
    mapfile -t stems < <(
        awk '
            { sub(/#.*/,""); gsub(/^[[:space:]]+|[[:space:]]+$/,""); }
            $0 == "" { next }
            { split($0, p, ":"); print p[1] }
        ' "${repo_root}/allowlist.txt" | sort -u
    )
fi

# --- run qtest-driver --------------------------------------------------------

log="${repo_root}/harness.log"
: > "${log}"

if [[ ${#stems[@]} -eq 0 ]]; then
    # Empty allowlist + no explicit TESTS — we still want CI to assert that
    # the harness boots, so dry-run qtest-driver with --version and write
    # an empty results section.
    echo "==> Allowlist is empty and no QTEST_TESTS set."
    {
        echo "qtest-driver dry run (allowlist empty)"
        perl "${repo_root}/vendor/qtest/bin/qtest-driver" --version
    } | tee -a "${log}"
else
    echo "==> Running qtest-driver on: ${stems[*]}"
    TESTS="${stems[*]}" \
    perl "${repo_root}/vendor/qtest/bin/qtest-driver" \
        -datadir "${repo_root}/vendor/qpdf-qtest" \
        -bindirs "${shim_bin}" \
        -stdout-tty=0 \
        2>&1 | tee -a "${log}" || true
fi

# --- verify against allowlist -----------------------------------------------

summary="${repo_root}/qtest-summary.md"

if [[ ${#stems[@]} -eq 0 ]]; then
    # No subtest lines will be present; emit a minimal summary directly.
    cat > "${summary}" <<EOF
# qtest-summary

- Allowlist is empty — no subtests required to pass.
- Harness bring-up verified: qtest-driver $(perl "${repo_root}/vendor/qtest/bin/qtest-driver" --version | head -n1).

**Verdict: OK (empty allowlist)**
EOF
    cat "${summary}"
    exit 0
fi

python3 "${repo_root}/scripts/verify-allowlist.py" \
    "${log}" "${repo_root}/allowlist.txt" \
    --summary "${summary}"
