# flpdf-qtest

Acceptance-test harness for [flpdf](https://github.com/fulgur-rs/flpdf) built
on top of the upstream [qpdf](https://github.com/qpdf/qpdf) `qtest` suite.

Status: **Phase 1** — the harness boots end-to-end (shim, runner, allowlist
verifier, CI) with an empty `allowlist.txt`. No subtests are required to
pass yet; informational failures are logged but do not fail CI.

## What this repository does

`flpdf` aims to be a Rust-native qpdf-equivalent. The
[`flpdf` repository](https://github.com/fulgur-rs/flpdf) already covers
unit/integration tests and a writer-level "compat matrix" that compares
flpdf's output PDFs against qpdf's output PDFs. This repository adds a third
layer — qpdf's own end-to-end test suite — by:

1. Vendoring qpdf's `qtest/` framework and `qpdf/qtest/` test corpus
   (`.test` files plus input PDFs and expected output fixtures).
2. Putting `flpdf-cli` on `PATH` under the name `qpdf` via a small shim, so
   the existing `.test` driver scripts invoke flpdf transparently.
3. Tracking which tests must pass in `allowlist.txt`. Tests on the allowlist
   that fail block CI; tests not on the allowlist that fail are informational
   only. The allowlist grows as flpdf's qpdf-CLI compatibility grows.

See [`docs/plans/2026-05-13-qtest-acceptance-design.md`](./docs/plans/2026-05-13-qtest-acceptance-design.md)
for the full design.

## Layout

```
flpdf-qtest/
├── docs/plans/                # design docs
├── vendor/
│   ├── qtest/                 # qtest framework (Artistic 2.0)
│   ├── qpdf-qtest/            # .test files + fixtures (Apache 2.0)
│   └── UPSTREAM_TAG           # which qpdf release vendor/ corresponds to
├── scripts/
│   └── vendor-sync.sh         # re-vendor at a given qpdf tag
├── LICENSE.md                 # our own work: MIT OR Apache-2.0
└── NOTICE.md                  # vendored licenses and attributions
```

```
flpdf-qtest/
├── shim/qpdf                  # PATH shim that delegates to flpdf-cli
├── shim/fix-qdf               # PATH stub: fail-loud shadow for host helpers
├── shim/zlib-flate            #   (see "PATH shadowing" below)
├── scripts/run.sh             # build + run qtest + verify allowlist
├── scripts/verify-allowlist.py
├── allowlist.txt              # tests required to pass (empty at Phase 1)
├── normalize/stderr-rules.sed # stderr prefix / wording normalization
└── .github/workflows/ci.yml   # push / PR / weekly / workflow_dispatch
```

## Outputs

For every non-empty qtest run, `harness.log` and `qtest-results.xml` are a
required pair from the same invocation. The log records qtest's human-readable
outcomes while the XML provides its authoritative per-subtest result set;
`verify-allowlist.py` reconciles both before it writes `qtest-summary.md` and
`qtest-metrics.jsonl`. `TEST-qtest.xml` is qtest's accompanying JUnit artifact.

Subsidiary suites without their own qtest summary are excluded from parsing,
exactly as qtest excludes them from its root summary. Do not combine the log or
XML from different runs: doing so is rejected as an inconsistent result set.

## Running locally

```bash
# Build all three binaries the harness needs. Select them by binary name so an
# flpdf-side crate reorganization does not invalidate these instructions.
cd /path/to/flpdf
cargo build --release --bin flpdf --bin flpdf-test-compare --bin flpdf-test-driver

# Then drive qtest.
cd /path/to/flpdf-qtest
FLPDF_CLI_BIN=/path/to/flpdf/target/release/flpdf \
FLPDF_TEST_COMPARE_BIN=/path/to/flpdf/target/release/flpdf-test-compare \
FLPDF_TEST_DRIVER_BIN=/path/to/flpdf/target/release/flpdf-test-driver \
QTEST_FULL=1 \
  ./scripts/run.sh
```

A full run leaves `harness.log`, `qtest-results.xml`, and `TEST-qtest.xml` in
the repository root. Keep `harness.log` and `qtest-results.xml` together when
inspecting or sharing a result: both validators derive their verdicts from
that paired artifact set.

Useful env knobs:

- `QTEST_FULL=1` — run every `*.test` in `vendor/qpdf-qtest/`; required for
  every non-empty run and for parity validation.
- `FLPDF_DIR=/path/to/flpdf` — if any of `FLPDF_CLI_BIN`,
  `FLPDF_TEST_COMPARE_BIN`, or `FLPDF_TEST_DRIVER_BIN` is unset, build
  `flpdf`, `flpdf-test-compare`, and `flpdf-test-driver` in that checkout,
  using the built path for each binary whose environment variable is unset.

## Parity ledger maintenance

`parity/qtest-11.9.0.jsonl` is this repository's explicit parity ledger. It
lives in `flpdf-qtest` because this harness owns the qpdf 11.9.0 full-corpus
observation, the paired result artifacts, and the classification boundary;
the `flpdf` repository owns the implementation work those rows reference.
There is exactly one JSON object for every authoritative qtest subtest, sorted
by category and numeric ordinal. It is not an allowlist and has no wildcard,
suite-wide default, or implicit catch-all.

### Full-run scope

`QTEST_FULL=1` is required for every non-empty full corpus run. It runs the
full corpus and validates both the acceptance allowlist and the complete
parity ledger. A non-empty allowlist without `QTEST_FULL=1` is rejected before
qtest starts, since a subset cannot validate the ledger. With an empty
allowlist, leaving `QTEST_FULL` unset performs only the supported qtest-driver
dry-run; it executes no subtests and therefore has no subtest result set or
parity-manifest validation.

### Identity and fields

The canonical identity is qtest XML `testid`: `<category> <ordinal>`. The
enclosing `.test` suite stem is separate metadata, not part of that identity.
For example, `weak-cryptography-cryptography 1` has category
`weak-cryptography-cryptography` while its suite is `weak-cryptography`.
Each row records `id`, `suite`, `category`, `ordinal`, `description`, `state`,
`rationale`, `owner`, `bead`, and `replacement_ref`; descriptions are checked
for drift but are not identities.

The six states have these contracts:

| State | Meaning | Required fields |
| --- | --- | --- |
| `passing` | The authoritative run is an ordinary PASS. | No state-specific fields. |
| `failing` | flpdf behavior was reached and differs from qpdf. | `rationale`, `owner`, `bead` |
| `blocked` | A known observation boundary prevented reaching the behavior. | `rationale`, `owner`, `bead` |
| `applicable` | The behavior is in scope but evidence cannot yet distinguish blocked from reached failure; expected-failure cases begin here. | `rationale`, `owner`, `bead` |
| `excluded` | The behavior is outside Linux x86_64 Rust parity, such as Windows shell or C/C++ ABI behavior. | `rationale`, `replacement_ref` |
| `represented` | The direct qtest route is outside the Rust boundary, but portable behavior has a Rust oracle test. | `rationale`, `replacement_ref` naming a Rust test |

When present, `replacement_ref` is exactly one typed reference:
`bead:flpdf-...`, `rust-test:<package>:<target>:<test>`, or
`scope:<document>#<section>`. C API rows temporarily use
`bead:flpdf-25kg.2.1` while that inventory maps them to a concrete Rust oracle
test or fixed ABI-only scope reference.

### Validate and update

After a full run, validate the paired artifacts and ledger explicitly:

```bash
python3 scripts/verify-parity-manifest.py \
  harness.log qtest-results.xml parity/qtest-11.9.0.jsonl
```

`scripts/run.sh` already runs this validator after the allowlist verifier, so
the command is useful for inspecting an existing full-run artifact pair. A
parse, schema, identity, ordering, required-field, or stale-outcome error is
an operational failure: keep the paired artifacts, correct the cause, and
rerun the full command rather than treating a partial run as evidence.

When qtest or flpdf changes, run the full corpus, classify every changed row,
and keep the JSONL sorted by category and numeric ordinal. If a `blocked` or
`failing` row becomes an ordinary PASS, promote it to `passing` in the same
update; the validator deliberately rejects the stale classification. Update
the linked Bead, Rust-test, or scope reference when ownership or replacement
coverage changes. `excluded` and `represented` are scope classifications, so
an outcome change alone does not promote them.

Pass counts and state counts are measured survey output, not implementation
priorities. Use root-cause evidence and the referenced Bead or Rust test to
choose work; record the resulting full-run measurements with the update.

## Re-vendoring

```bash
scripts/vendor-sync.sh v11.9.0
git add vendor && git commit -m "vendor: sync qpdf vXX.Y.Z"
```

The script downloads the qpdf source tarball for the requested tag, replaces
the contents of `vendor/qtest/` and `vendor/qpdf-qtest/`, and records the tag
in `vendor/UPSTREAM_TAG`. Do not patch `vendor/` locally — absorb divergence
via `shim/`, `normalize/`, or `allowlist.txt` instead.

## PATH shadowing

`scripts/run.sh` copies every executable in `shim/` to the front of `PATH`,
not just `qpdf`. This is deliberate: several `vendor/qpdf-qtest/*.test`
files invoke qpdf-side helpers (`fix-qdf`, `zlib-flate`, etc.) directly,
and on hosts where the `qpdf` apt package is installed those helpers live
at `/usr/bin/fix-qdf` etc. Without shadowing them, those subtests would
silently route to the host binaries and report PASS without ever calling
flpdf — disagreeing with CI (which has no qpdf package) and inflating
local survey numbers.

The stubs in `shim/` for these helpers fail loudly (`exit 127` with a
descriptive stderr message), so any subtest that depended on them is
recorded as a real failure. If flpdf grows an equivalent of one of these
helpers in the future, replace the stub with a delegating shim like
`shim/qpdf`.

## License

This repository's own contributions are dual-licensed under
[Apache-2.0](https://www.apache.org/licenses/LICENSE-2.0) and
[MIT](https://opensource.org/license/MIT). See [LICENSE.md](./LICENSE.md).

Vendored files retain their upstream licenses; see [NOTICE.md](./NOTICE.md).
