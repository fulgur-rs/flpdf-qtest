# Notices and Third-Party Licenses

## Repository scope

This repository contains an acceptance-test harness for
[flpdf](https://github.com/fulgur-rs/flpdf) that re-uses the test suite from
upstream [qpdf](https://github.com/qpdf/qpdf). It bundles a vendored copy of
qpdf's `qtest` framework and `qpdf/qtest` test corpus so the tests can run
without a qpdf source checkout.

## Vendored components

The currently vendored upstream version is recorded in
[`vendor/UPSTREAM_TAG`](./vendor/UPSTREAM_TAG). It is refreshed by running
`scripts/vendor-sync.sh <qpdf-tag>`.

### `vendor/qtest/`

A pristine copy of the `qtest/` directory from the qpdf source tree.

qtest is a Perl-based test framework distributed under the terms of
[version 2.0 of the Artistic license](https://opensource.org/licenses/Artistic-2.0).
qtest is authored by Jay Berkenbilt; see
<https://qtest.sourceforge.io> for upstream details.

### `vendor/qpdf-qtest/`

A pristine copy of the `qpdf/qtest/` directory from the qpdf source tree. It
contains qpdf-specific `.test` driver files, Perl helpers
(`qpdf_test_helpers.pm`), and a `qpdf/` subdirectory of input PDFs and expected
output fixtures.

qpdf is:

> Copyright (c) 2005-2021 Jay Berkenbilt,
> 2022-2026 Jay Berkenbilt and Manfred Holger

and is licensed under the [Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0).
Versions of qpdf prior to version 7 were released under the Artistic License
2.0; at the user's option the same terms may apply to those earlier files.

The full qpdf NOTICE (including third-party attributions for Rijndael and
sphlib that appear in the qpdf source distribution but are **not** included
here) is reproduced upstream at
<https://github.com/qpdf/qpdf/blob/main/NOTICE.md>.

## Re-vendoring

`vendor/` is treated as a pristine mirror. Do not patch it locally; absorb any
divergence between flpdf and qpdf via the `shim/`, `normalize/`, or
`allowlist.txt` mechanisms documented in the design plan
([`docs/plans/2026-05-13-qtest-acceptance-design.md`](./docs/plans/2026-05-13-qtest-acceptance-design.md)).
