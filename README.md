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
├── survey/
│   ├── latest/                # artifacts from the most recent run
│   ├── history/               # per-generation snapshots (local only)
│   └── findings/              # FINDINGS-*.md survey write-ups
└── .github/workflows/ci.yml   # push / PR / weekly / workflow_dispatch
```

## Outputs

Every survey artifact is written under `survey/latest/`. For every non-empty
qtest run, `harness.log` and `qtest-results.xml` are a required pair from the
same invocation. The log records qtest's human-readable
outcomes while the XML provides its authoritative per-subtest result set;
`verify-allowlist.py` reconciles both before it writes `qtest-summary.md` and
`qtest-metrics.jsonl`. `TEST-qtest.xml` is qtest's accompanying JUnit artifact.

Subsidiary suites without their own qtest summary are excluded from parsing,
exactly as qtest excludes them from its root summary. Do not combine the log or
XML from different runs: doing so is rejected as an inconsistent result set.

## Running locally

```bash
# Build all fourteen binaries the harness needs. Select them by binary name so an
# flpdf-side crate reorganization does not invalidate these instructions.
cd /path/to/flpdf
cargo build --release --features qpdf-zlib-compat \
  --bin flpdf --bin flpdf-test-compare --bin flpdf-test-driver \
  --bin qpdfjob-ctest --bin qpdf-ctest \
  --bin flpdf-test-pdf-doc-encoding --bin flpdf-test-pdf-unicode \
  --bin flpdf-test-unicode-filenames --bin test_xref --bin test_parsedoffset \
  --bin flpdf-test-large-file --bin pdf_from_scratch --bin test_many_nulls \
  --bin test_renumber

# Then drive qtest.
cd /path/to/flpdf-qtest
FLPDF_CLI_BIN=/path/to/flpdf/target/release/flpdf \
FLPDF_TEST_COMPARE_BIN=/path/to/flpdf/target/release/flpdf-test-compare \
FLPDF_TEST_DRIVER_BIN=/path/to/flpdf/target/release/flpdf-test-driver \
FLPDF_TEST_QPDFJOB_BIN=/path/to/flpdf/target/release/qpdfjob-ctest \
FLPDF_TEST_QPDF_CTEST_BIN=/path/to/flpdf/target/release/qpdf-ctest \
FLPDF_TEST_PDF_DOC_ENCODING_BIN=/path/to/flpdf/target/release/flpdf-test-pdf-doc-encoding \
FLPDF_TEST_PDF_UNICODE_BIN=/path/to/flpdf/target/release/flpdf-test-pdf-unicode \
FLPDF_TEST_UNICODE_FILENAMES_BIN=/path/to/flpdf/target/release/flpdf-test-unicode-filenames \
FLPDF_TEST_XREF_BIN=/path/to/flpdf/target/release/test_xref \
FLPDF_TEST_PARSED_OFFSET_BIN=/path/to/flpdf/target/release/test_parsedoffset \
FLPDF_TEST_LARGE_FILE_BIN=/path/to/flpdf/target/release/flpdf-test-large-file \
FLPDF_TEST_FROM_SCRATCH_BIN=/path/to/flpdf/target/release/pdf_from_scratch \
FLPDF_TEST_MANY_NULLS_BIN=/path/to/flpdf/target/release/test_many_nulls \
FLPDF_TEST_RENUMBER_BIN=/path/to/flpdf/target/release/test_renumber \
QTEST_FULL=1 \
  ./scripts/run.sh
```

The qtest corpus includes strict file comparisons of re-filtered streams.
`qpdf-zlib-compat` selects the classic libz backend used by the pinned qpdf
11.9.0 oracle; ordinary flpdf production builds remain on the default
pure-Rust backend.

A full run leaves `harness.log`, `qtest-results.xml`, and `TEST-qtest.xml` in
`survey/latest/`. Keep `harness.log` and `qtest-results.xml` together when
inspecting or sharing a result: both validators derive their verdicts from
that paired artifact set.

Useful env knobs:

- `QTEST_FULL=1` — run every `*.test` in `vendor/qpdf-qtest/`; required for
  every non-empty run and for parity validation.
- `FLPDF_DIR=/path/to/flpdf` — if any of `FLPDF_CLI_BIN`,
  `FLPDF_TEST_COMPARE_BIN`, `FLPDF_TEST_DRIVER_BIN`,
  `FLPDF_TEST_QPDFJOB_BIN`, or `FLPDF_TEST_QPDF_CTEST_BIN`,
  `FLPDF_TEST_PDF_DOC_ENCODING_BIN`, `FLPDF_TEST_PDF_UNICODE_BIN`,
  `FLPDF_TEST_UNICODE_FILENAMES_BIN`, `FLPDF_TEST_XREF_BIN`,
  `FLPDF_TEST_PARSED_OFFSET_BIN`, `FLPDF_TEST_LARGE_FILE_BIN`,
  `FLPDF_TEST_FROM_SCRATCH_BIN`, `FLPDF_TEST_MANY_NULLS_BIN`, or
  `FLPDF_TEST_RENUMBER_BIN` is unset, build all fourteen binaries in that
  checkout, using the built path for each binary whose environment variable is
  unset.

## Parity ledger maintenance

`parity/qtest-11.9.0.jsonl` is this repository's explicit parity ledger. This
checked-in ledger is owned by `flpdf-qtest` because it consumes the harness's
same-run qtest artifacts and owns the CI validation boundary. The `flpdf`
repository owns the implementation work those rows reference.
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

`harness.log` and `qtest-results.xml` must come from the same full run and
both validators consume that pair. Do not combine artifacts from different
runs.

### Identity and fields

The canonical identity is qtest XML `testid`: `<category> <ordinal>`. The
enclosing `.test` suite stem is separate metadata, not part of that identity.
For example, `weak-cryptography-cryptography 1` has category
`weak-cryptography-cryptography` while its suite is `weak-cryptography`.
Each row records `id`, `suite`, `category`, `ordinal`, `description`, `state`,
`rationale`, `owner`, `bead`, and `replacement_ref`; descriptions are checked
for drift but are not identities.

The six states have these contracts:

| State | Meaning | Field contract |
| --- | --- | --- |
| `passing` | The authoritative run is an ordinary PASS. | `rationale`, `owner`, `bead`, and `replacement_ref` are `null`. |
| `failing` | flpdf behavior was reached and differs from qpdf. | `rationale`, `owner`, and `bead` are required; `replacement_ref` is `null`. |
| `blocked` | A known observation boundary prevented reaching the behavior. | `rationale`, `owner`, and `bead` are required; `replacement_ref` is `null`. |
| `applicable` | The behavior is in scope but evidence cannot yet distinguish blocked from reached failure; expected-failure cases begin here. | `rationale`, `owner`, and `bead` are required; `replacement_ref` is `null`. |
| `excluded` | The behavior is outside Linux x86_64 Rust parity, such as Windows shell or C/C++ ABI behavior. | `rationale` and `replacement_ref` are required; `owner` and `bead` are `null`. |
| `represented` | The direct qtest route is outside the Rust boundary, but portable behavior has a Rust oracle test. | `rationale` and a Rust-test `replacement_ref` are required; `owner` and `bead` are `null`. |

When present, `replacement_ref` is exactly one typed reference:
`bead:flpdf-...`, `rust-test:<package>:<target>:<test>`, or
`scope:<document>#<section>`. C API rows temporarily use
`bead:flpdf-25kg.2.1` while that inventory mapped them to a concrete Rust oracle
test or fixed ABI-only scope reference. The completed qpdf-ctest mapping is
recorded in [`docs/qpdf-ctest-inventory.md`](docs/qpdf-ctest-inventory.md).

### Validate and update

After a full run, validate the paired artifacts and ledger explicitly:

```bash
python3 scripts/verify-parity-manifest.py \
  survey/latest/harness.log survey/latest/qtest-results.xml \
  parity/qtest-11.9.0.jsonl
```

`scripts/run.sh` already runs this validator after the allowlist verifier, so
the command is useful for inspecting an existing full-run artifact pair. A
parity parser error produces no parity summary; a validation error produces
only a FAIL verdict. Neither is successful update evidence. A partial or
failed run is not ledger-update evidence. Keep the paired artifacts, correct
the cause, and rerun the full command.

When qtest or flpdf changes, run the full corpus, classify every changed row,
and keep the JSONL sorted by category and numeric ordinal. If a `blocked` or
`failing` row becomes an ordinary PASS, promote it to `passing` in the same
update. The validator rejects stale `passing`, `blocked`, and `failing`
classifications. A `blocked` or `failing` row that becomes an ordinary PASS
requires promotion to `passing`. For any state change, update the required
`owner` and `bead` for `applicable`, `blocked`, or `failing`, or the
`replacement_ref` for `excluded` or `represented`. `excluded` and
`represented` are scope classifications, so an outcome change alone does not
promote them.

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

Unsupported helpers in `shim/` fail loudly (`exit 127` with a descriptive
stderr message), so dependent subtests are recorded as real failures.
Supported helpers delegate to Rust binaries: `test_driver`, `qpdfjob-ctest`,
`test_pdf_doc_encoding`, `test_pdf_unicode`, `test_unicode_filenames`,
`test_xref`, `test_parsedoffset`, and `test_large_file` route to
`flpdf-qtest-tools`, and `test_renumber` routes there as well; `fix-qdf`
routes to `flpdf`.

## License

This repository's own contributions are dual-licensed under
[Apache-2.0](https://www.apache.org/licenses/LICENSE-2.0) and
[MIT](https://opensource.org/license/MIT). See [LICENSE.md](./LICENSE.md).

Vendored files retain their upstream licenses; see [NOTICE.md](./NOTICE.md).
