# qtest Authoritative Result Set and Applicable Manifest Design

## Goal

Create an authoritative, machine-readable Linux x86_64 parity ledger for every
qtest subtest reported by the pinned qpdf 11.9.0 suite.

The work is delivered as two stacked layers:

1. `flpdf-25kg.1.2` makes qtest result accounting agree with qtest's own
   reported total without weakening result parsing or allowlist semantics.
2. `flpdf-25kg.1.1` checks in an explicit JSONL manifest that classifies every
   member of that authoritative result set and validates it in CI.

The manifest and its validators belong to `flpdf-qtest`, which owns the
vendored qtest corpus, runner, result artifacts, and qtest CI. The `flpdf`
repository remains the owner of the roadmap specification, Beads, Rust oracle
tests, and implementation work.

## Fixed Scope

- The qpdf and qtest oracle is 11.9.0, as recorded by
  `vendor/UPSTREAM_TAG`.
- The supported parity platform is Linux x86_64.
- Windows-only shell behavior is excluded.
- C and C++ ABI and symbol compatibility are excluded.
- Portable behavior reached through a C or platform-specific helper is
  represented by a Rust oracle test or assigned to a follow-up Bead.
- The deterministic-ID suite's `qpdf-ctest 19` row is an explicit portable
  writer-behavior exception: its Rust-native helper does not provide C ABI or
  symbol compatibility.
- The manifest is explicit: one JSON object per authoritative qtest subtest.
- A subtest's identity is qtest's XML `testid` (category and ordinal), not its
  description or enclosing `.test` filename.
- Passing counts are measurements. They are not used to prioritize
  implementation.

## Current Accounting Failure

The 2026-07-29 full survey recorded:

- qtest root summary total: 2,762;
- log parser result total: 2,790;
- ordinary passes: 191;
- unexpected failures: 2,568;
- expected failures: 3; and
- allowlist result: 39/39 passing with no regressions or missing entries.

The 28-result overcount is not caused by duplicate descriptions. Five
subsidiary suites emit testcase records before exiting without returning the
five counters required by `qtest-driver`:

- `c-api-key.test`;
- `completion.test`;
- `large-file.test`;
- `replace-input.test`; and
- `writer-version.test`.

For each such suite, `qtest-results.xml` contains the partial testcase records
but no child `<testsummary>`. The qtest parent reports the suite as having
invalid results and does not add those partial records to its root total. The
existing log parser nevertheless retains their status lines, producing the
2,790/2,762 drift.

The authoritative parser must reproduce qtest's accounting boundary rather
than deduplicating records that happen to look alike.

## Repository and Branch Structure

Implementation uses two stacked branches in `flpdf-qtest`:

1. `fix/flpdf-25kg-1-2-qtest-result-accounting`, based on
   `add-test-driver-shim` / PR #23;
2. a later `flpdf-25kg.1.1` manifest branch based on layer 1.

The first layer is based on PR #23 because the recorded full survey requires
the `test_driver` shim and `flpdf-test-driver` binary wiring introduced there.
The stack may be rebased or retargeted after its parent merges, but the two
Beads remain separate commits and PR review boundaries.

## Authoritative Result Model

Add `scripts/qtest_results.py` as the single parser shared by the allowlist and
manifest validators.

Each result contains:

- `suite`: the enclosing `.test` stem used for suite validity;
- `category`: the category prefix from qtest's XML `testid`;
- `ordinal`: the qtest subtest number;
- `description`: the human-readable qtest description;
- `outcome`: `pass`, `fail`, `unexpected-pass`, or `expected-fail`; and
- `id`: qtest's canonical `<category> <ordinal>` XML `testid`.

The XML `testid` is the identity. The category usually equals the `.test`
stem, but qpdf 11.9.0 has a verified exception:
`weak-cryptography.test` emits category `weak-cryptography-cryptography`.
Descriptions and suite filenames remain reviewable metadata but are not keys
because upstream legitimately repeats descriptions such as `check output`
within one suite and does not require category to equal suite stem.

### Inputs

The parser consumes both artifacts from one qtest run:

- `qtest-results.xml` supplies suite boundaries, testcase identities, actual
  pass/fail outcomes, child summaries, and the root summary.
- `harness.log` supplies the human status line, including the `(exp)` and
  `PASSED-UNEXP` markers required to distinguish expected failures and
  unexpected passes.

The XML `outcome` attribute alone is insufficient for expected failures:
qtest's `TestDriver.pm` writes the actual pass/fail outcome there, while the
root and child summaries count expected failures separately.

### Accounting algorithm

1. Parse the XML and reject malformed roots, suites, test IDs, ordinals, or
   integer counters.
2. Retain testcase identities only from suites with a child `<testsummary>`.
3. Parse and deduplicate log status lines by `category + ordinal`, retaining
   their descriptions and expectation markers.
4. Restrict log results to the valid suite set from XML.
5. Require exact identity equality between the retained XML and log results.
6. Require description and actual pass/fail agreement for every identity.
7. Combine the XML actual outcome with the log expectation marker to derive
   `pass`, `fail`, `unexpected-pass`, or `expected-fail`.
8. Verify each retained suite's testcase count and all four derived outcome
   counters against its child summary.
9. Verify the retained aggregate total and all four derived outcome counters
   against the XML root summary.

Any mismatch is an operational error. The parser must not return a partial
authoritative result set.

## Allowlist Integration

`scripts/verify-allowlist.py` delegates result parsing to
`scripts/qtest_results.py`. It continues to support the existing allowlist
syntax and judgment:

- allowlisted PASS;
- allowlisted FAIL regression;
- missing allowlisted entry;
- non-allowlisted PASS candidate; and
- informational non-allowlisted failure.

The command-line entry point changes to receive both the log and XML artifacts.
`scripts/run.sh` passes the two files produced by the same qtest invocation.

The accounting fix must preserve:

- all 39 existing allowlist entries;
- 39/39 passing;
- zero allowlist regressions;
- zero missing entries; and
- candidate and informational categorization for the authoritative set.

Description-based allowlist matching remains for compatibility. Canonical
identity is added to the internal result model so duplicate descriptions are
not discarded during accounting or manifest validation.

## Explicit JSONL Manifest

The checked-in ledger is:

```text
parity/qtest-11.9.0.jsonl
```

It contains exactly one compact JSON object per authoritative result, sorted
by category and numeric ordinal. A representative entry is:

```json
{"id":"appearance-streams 1","suite":"appearance-streams","category":"appearance-streams","ordinal":1,"description":"generate appearances and flatten (need-appearances)","state":"blocked","rationale":"CLI option is not implemented","owner":"Mitsuru Hayasaka","bead":"flpdf-25kg.5","replacement_ref":null}
```

Every entry has these fields:

- `id`: exact qtest XML `<category> <ordinal>` test ID;
- `suite`: enclosing qtest suite stem;
- `category`: qtest test ID category;
- `ordinal`: positive qtest subtest number;
- `description`: exact description from the baseline;
- `state`: one of the six states below;
- `rationale`: non-empty when required by the state;
- `owner`: owner name when required by the state;
- `bead`: `flpdf-...` issue ID when required by the state; and
- `replacement_ref`: typed reference or `null`.

No wildcard, range, inherited default, or implicit catch-all is allowed in the
checked-in manifest. Suite-level rules may be used to bootstrap the initial
file, but the expanded JSONL is the only runtime authority.

## State Semantics

### `passing`

The current authoritative run reports an ordinary PASS. An upstream expected
failure is not classified as passing because it is not parity evidence.

Required run invariant: the corresponding result remains an ordinary PASS.

### `failing`

The test reaches the flpdf production behavior under examination and differs
from qpdf in exit status, stdout, stderr, side files, structure, or bytes.

Required fields:

- `rationale`;
- `owner`; and
- `bead`.

Required run invariant: the result does not become an ordinary PASS without a
manifest update.

### `blocked`

The test does not reach the production behavior under examination because of
a missing helper, unsupported `test_driver` ID, absent CLI option, or other
known observation boundary.

Required fields:

- `rationale`;
- `owner`; and
- `bead`.

Required run invariant: the result does not become an ordinary PASS without a
manifest update.

### `excluded`

The behavior is outside the fixed Linux x86_64 Rust parity boundary, such as
Windows shell behavior or C/C++ ABI and symbol compatibility.

Required fields:

- `rationale`; and
- `replacement_ref`.

The observed outcome does not automatically change the scope classification.

### `represented`

The direct qtest route is outside the Rust boundary, but its portable behavior
is covered by a Rust oracle test.

Required fields:

- `rationale`; and
- `replacement_ref` naming a concrete Rust test.

The observed outcome does not automatically change the representation
classification.

### `applicable`

The behavior is in scope, but the current evidence cannot safely distinguish
blocked observation from a reached behavior failure. Upstream
`EXPECT_FAILURE` entries also start here because their qtest success status is
not proof that flpdf matches qpdf.

Required fields:

- `rationale`;
- `owner`; and
- `bead`.

This is an owned, reviewable state, not an unassigned default.

## Reference Forms

`replacement_ref`, when present, uses exactly one of:

- `bead:flpdf-...`;
- `rust-test:<package>:<target>:<test>`; or
- `scope:<document>#<section>`.

C API entries initially use `bead:flpdf-25kg.2.1`. That follow-up inventory
replaces each provisional reference with a concrete Rust oracle test or a
fixed ABI-only scope reference. This avoids a dependency cycle: the manifest
unblocks the mapping task, so the mapping task cannot be required to finish
before the initial manifest.

## Initial Classification

Generate the initial manifest from two independent full surveys of one flpdf
build after the accounting fix. Abort if their identity sets or outcomes
differ.

Apply classification in this order:

1. exclusions for Windows-only and ABI/symbol-only behavior, except the
   deterministic-ID `qpdf-ctest 19` portable writer-behavior adapter;
2. represented behavior with already verified Rust oracle tests;
3. ordinary qtest passes;
4. known observation blockers;
5. reached production failures; and
6. owned applicable entries whose evidence is insufficient for a narrower
   state.

Bootstrap rules are expanded to individual rows. Review is performed
suite-by-suite using:

- total entries;
- count by state;
- assigned owner and Bead;
- representative entries; and
- every exception to the suite's dominant classification.

Failing and blocked entries should use the narrowest existing root-cause Bead.
When no narrower issue exists, they use the responsible Phase epic rather
than creating duplicate implementation issues during the inventory.

## Manifest Validator

Add `scripts/verify-parity-manifest.py`. It consumes the shared authoritative
result model and the JSONL file.

It rejects:

- malformed JSON or unknown fields;
- missing or extra fields;
- unknown states;
- invalid IDs or reference forms;
- duplicate identities;
- non-canonical ordering;
- mismatch between `id`, `category`, and `ordinal`;
- missing or extra entries relative to qtest;
- description drift;
- missing state-specific rationale, owner, Bead, or replacement reference;
- a `passing` entry that is no longer an ordinary PASS;
- a `blocked` or `failing` entry that became an ordinary PASS without ledger
  promotion; and
- manifest totals that differ from qtest's root total.

It renders:

- authoritative total;
- count by state;
- ordinary passes and expected failures;
- stale outcome classifications;
- missing or extra identities; and
- an overall verdict.

The full report is uploaded as an artifact. A bounded headline is appended to
the GitHub Job Summary.

## CI and Runner Integration

PR, push, manual, and scheduled qtest jobs run the full corpus with
`QTEST_FULL=1`. The job order is:

1. run Python unit tests;
2. build `flpdf`, `flpdf-test-compare`, and `flpdf-test-driver`;
3. execute the full qtest survey;
4. verify the existing allowlist;
5. verify the parity manifest; and
6. upload the log, XML, allowlist summary, manifest summary, and metrics.

`scripts/run.sh` remains responsible for one qtest invocation and passes its
paired log/XML artifacts to both validators. It must not rerun qtest for the
manifest.

The manifest is intentionally sensitive to flpdf changes:

- a passing entry that regresses fails validation;
- a blocked or failing entry that starts passing requires promotion; and
- new or removed qtest identities require an explicit manifest change.

Excluded and represented entries retain their scope classification across
ordinary run-outcome changes.

## Tests

### Result accounting tests

Synthetic XML/log pairs cover:

- a valid suite whose counters match;
- an invalid suite that emits partial testcases without a child summary;
- the prior 28-result overcount shape;
- duplicate descriptions with distinct ordinals;
- duplicate identities;
- XML-only and log-only identities;
- description disagreement;
- pass, fail, unexpected-pass, and expected-fail accounting;
- child summary count disagreement; and
- root summary count disagreement.

The regression fixture must fail under the old log-only parser for the
expected 2,790-versus-2,762 reason before implementation.

### Allowlist regression tests

Retain and adapt all current allowlist tests. Add focused checks that:

- duplicate descriptions remain separately counted;
- invalid-suite partial results cannot satisfy an allowlist entry; and
- the 39-entry semantics do not change when parsing is delegated.

### Manifest tests

Synthetic authoritative results and JSONL files cover:

- all valid states and reference forms;
- state-specific required fields;
- malformed and unknown data;
- duplicate and unsorted entries;
- missing and extra identities;
- description drift;
- passing regression;
- blocked/failing promotion;
- excluded/represented outcome changes; and
- exact state-count rendering.

## Verification

Layer 1 completes only after:

- focused parser and allowlist tests pass;
- the full Python suite passes;
- two independent surveys of the same flpdf build report the same identity
  set and outcomes;
- parser count equals qtest root total on both runs;
- both runs retain 39/39 allowlist entries with zero regressions and zero
  missing entries; and
- the layer is reviewed and pushed independently.

Layer 2 completes only after:

- focused manifest tests pass;
- the full Python suite passes;
- the explicit manifest has exactly one row per authoritative result;
- every row satisfies its state contract;
- state counts sum exactly to qtest's root total on both independent runs;
- CI performs the full survey and manifest validation; and
- the layer is reviewed and pushed independently.

## Documentation

Update `README.md` with:

- the full-survey parity ledger purpose;
- the six state definitions;
- the canonical XML `testid` identity rule and separate suite-validity
  boundary;
- local allowlist and manifest verification commands;
- the entry update and promotion workflow; and
- the provisional C API mapping reference.

Generated qtest artifacts remain ignored and uploaded by CI. Only the explicit
JSONL manifest, validators, tests, runner/CI wiring, and documentation are
checked in.

## Non-goals

- Porting `qpdf-ctest`;
- implementing missing qpdf behavior;
- fixing failures to increase raw qtest pass count;
- adding Windows parity;
- changing qpdf's vendored test files;
- weakening exact output comparisons;
- inferring identity from descriptions; or
- replacing the explicit manifest with wildcard rules.
