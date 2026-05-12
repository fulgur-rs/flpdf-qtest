# flpdf-qtest

Acceptance-test harness for [flpdf](https://github.com/fulgur-rs/flpdf) built
on top of the upstream [qpdf](https://github.com/qpdf/qpdf) `qtest` suite.

Status: **Phase 0** — repository bootstrap. The vendored qtest tree is in
place but the runner, shim, and CI are not yet wired up.

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

Future phases will add `shim/`, `allowlist.txt`, `scripts/run.sh`,
`scripts/verify-allowlist.py`, `normalize/`, and `.github/workflows/ci.yml`.

## Re-vendoring

```bash
scripts/vendor-sync.sh v11.9.0
git add vendor && git commit -m "vendor: sync qpdf vXX.Y.Z"
```

The script downloads the qpdf source tarball for the requested tag, replaces
the contents of `vendor/qtest/` and `vendor/qpdf-qtest/`, and records the tag
in `vendor/UPSTREAM_TAG`. Do not patch `vendor/` locally — absorb divergence
via `shim/`, `normalize/`, or `allowlist.txt` instead.

## License

This repository's own contributions are dual-licensed under
[Apache-2.0](https://www.apache.org/licenses/LICENSE-2.0) and
[MIT](https://opensource.org/license/MIT). See [LICENSE.md](./LICENSE.md).

Vendored files retain their upstream licenses; see [NOTICE.md](./NOTICE.md).
