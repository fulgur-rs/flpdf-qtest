# qtest Applicable Manifest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Check in and continuously validate one explicit classification row for every authoritative qtest 11.9.0 subtest reported on Linux x86_64.

**Architecture:** `verify-parity-manifest.py` validates a sorted JSONL ledger against `qtest_results.parse_run`. A one-time bootstrap expands the approved outcome, scope, observation-boundary, and phase-ownership rules into explicit rows; CI runs the full survey and rejects identity, schema, ownership, or stale-outcome drift.

**Tech Stack:** Python 3 standard library, JSON Lines, `unittest`, Bash, GitHub Actions, Beads references.

## Global Constraints

- Start from the pushed and reviewed `flpdf-25kg.1.2` result-accounting branch.
- Create a new stacked branch for `flpdf-25kg.1.1`; do not add manifest work to Layer A.
- qpdf/qtest 11.9.0 and Linux x86_64 define the manifest boundary.
- The manifest contains one explicit row per authoritative result and no wildcard rules.
- Canonical identity is exact XML `testid` (`category + ordinal`); suite stem is separate metadata.
- Every `excluded` or `represented` entry has a rationale and typed replacement reference.
- Every `blocked`, `failing`, or `applicable` entry has a rationale, owner, and Bead.
- Initial C API replacement references may point to `bead:flpdf-25kg.2.1`.
- Raw pass count is measured output, not implementation priority.
- PR-time CI runs the full qtest corpus.
- Use RED → GREEN → REFACTOR for validator and workflow behavior.

---

### Task 0: Start the manifest stack layer

**Files:**
- No tracked file changes

**Interfaces:**
- Consumes: reviewed `fix/flpdf-25kg-1-2-qtest-result-accounting`
- Produces: `feat/flpdf-25kg-1-1-qtest-manifest`

- [ ] **Step 1: Confirm Layer A is clean and pushed**

```bash
git status --short
git branch --show-current
git rev-parse fix/flpdf-25kg-1-2-qtest-result-accounting
git rev-parse origin/fix/flpdf-25kg-1-2-qtest-result-accounting
```

Expected: clean status, the two SHAs match, and Layer A verification evidence
has been recorded on `flpdf-25kg.1.2`.

- [ ] **Step 2: Create the dependent branch**

```bash
git switch -c feat/flpdf-25kg-1-1-qtest-manifest
```

Expected: the new branch starts at the reviewed Layer A tip.

- [ ] **Step 3: Verify the inherited baseline**

```bash
python3 -m unittest discover -s scripts/tests -p 'test_*.py'
```

Expected: all Layer A tests pass before manifest changes.

---

### Task 1: Validate explicit manifest entries

**Files:**
- Create: `scripts/verify-parity-manifest.py`
- Create: `scripts/tests/test_verify_parity_manifest.py`

**Interfaces:**
- Consumes: `qtest_results.RunResults`, `parity/qtest-11.9.0.jsonl`
- Produces: `ManifestEntry`, `Validation`, `load_manifest`, `validate_manifest`, `render_summary`, CLI exit status

- [ ] **Step 1: Write schema and reference RED tests**

Load the script with the existing `importlib.util` pattern, import
`Counter` from `collections`, and define test helpers that construct
authoritative results:

```python
def _result(
    category: str,
    ordinal: int,
    description: str,
    outcome: qtest_results.Outcome,
    *,
    suite: str | None = None,
) -> qtest_results.Result:
    return qtest_results.Result(
        suite=suite or category,
        category=category,
        ordinal=ordinal,
        description=description,
        outcome=outcome,
    )


Outcome = qtest_results.Outcome


def _run(results: list[qtest_results.Result]) -> qtest_results.RunResults:
    counts = Counter(r.outcome for r in results)
    return qtest_results.RunResults(
        results=tuple(results),
        summary=qtest_results.Summary(
            total=len(results),
            passes=counts[Outcome.PASS],
            failures=counts[Outcome.FAIL],
            unexpected_passes=counts[Outcome.UNEXPECTED_PASS],
            expected_failures=counts[Outcome.EXPECTED_FAIL],
        ),
        invalid_suites=(),
    )


def _entry(**overrides):
    value = {
        "id": "arg-parsing 1",
        "suite": "arg-parsing",
        "category": "arg-parsing",
        "ordinal": 1,
        "description": "required argument",
        "state": "passing",
        "rationale": None,
        "owner": None,
        "bead": None,
        "replacement_ref": None,
    }
    value.update(overrides)
    return value
```

Add one acceptance test per state:

```python
def test_accepts_passing_entry_for_ordinary_pass(self) -> None:
    run = _run([_result("arg-parsing", 1, "required argument", Outcome.PASS)])
    validation = verify_manifest.validate_manifest(run, [_entry()])
    self.assertEqual(validation.errors, ())

def test_requires_owner_and_bead_for_blocked(self) -> None:
    run = _run([_result("arg-parsing", 1, "required argument", Outcome.FAIL)])
    entry = _entry(
        state="blocked",
        rationale="CLI option is absent",
        owner=None,
        bead=None,
    )
    validation = verify_manifest.validate_manifest(run, [entry])
    self.assertIn("blocked entry requires owner", validation.errors[0])
```

Cover all accepted replacement forms:

```text
bead:flpdf-25kg.2.1
rust-test:flpdf:reader_tests:opens_minimal_pdf
scope:docs/superpowers/specs/2026-07-29-qpdf-observable-parity-roadmap-design.md#api-boundary
```

- [ ] **Step 2: Run the focused tests to verify RED**

```bash
python3 -m unittest scripts/tests/test_verify_parity_manifest.py -v
```

Expected: import failure because the validator does not exist.

- [ ] **Step 3: Implement the manifest data model and loader**

Create:

```python
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from qtest_results import Outcome, ResultError, RunResults, parse_run

STATES = frozenset(
    {"applicable", "excluded", "represented", "blocked", "passing", "failing"}
)
FIELDS = (
    "id",
    "suite",
    "category",
    "ordinal",
    "description",
    "state",
    "rationale",
    "owner",
    "bead",
    "replacement_ref",
)


@dataclass(frozen=True)
class ManifestEntry:
    id: str
    suite: str
    category: str
    ordinal: int
    description: str
    state: str
    rationale: str | None
    owner: str | None
    bead: str | None
    replacement_ref: str | None


@dataclass(frozen=True)
class Validation:
    errors: tuple[str, ...]
    counts: dict[str, int]
    total: int


def load_manifest(path: Path) -> list[ManifestEntry]:
    entries: list[ManifestEntry] = []
    seen: set[str] = set()
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw:
            raise ValueError(f"{path}:{lineno}: blank lines are not allowed")
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ValueError(f"{path}:{lineno}: invalid JSON: {e.msg}") from e
        if not isinstance(value, dict) or tuple(value) != FIELDS:
            raise ValueError(
                f"{path}:{lineno}: fields must be {', '.join(FIELDS)} in order"
            )
        entry = ManifestEntry(**value)
        if entry.id in seen:
            raise ValueError(f"{path}:{lineno}: duplicate id {entry.id!r}")
        seen.add(entry.id)
        entries.append(entry)
    return entries
```

`load_manifest` reads each non-empty line, requires exactly `FIELDS`, rejects
invalid JSON and duplicate identities with `path:line` diagnostics, and
returns entries in file order. Blank lines are rejected so physical line
number equals entry number.

- [ ] **Step 4: Implement state-field contracts and reference validation**

Implement only the state-field and replacement-reference behavior covered by
the initial RED tests:

| State | Required |
|---|---|
| `passing` | no extra ownership fields |
| `failing` | rationale, owner, bead |
| `blocked` | rationale, owner, bead |
| `applicable` | rationale, owner, bead |
| `excluded` | rationale, replacement_ref |
| `represented` | rationale, `rust-test:` replacement_ref |

For `excluded`, allow `bead:`, `rust-test:`, or `scope:`. For `represented`,
allow only `rust-test:`. Validate Beads with `^flpdf-[a-z0-9]+(?:\.[0-9]+)*$`.
Defer identity-set, metadata-drift, ordering, counts, and stale-outcome
validation until after the next RED step.

- [ ] **Step 5: Add RED tests for identity-set and stale-outcome errors**

Add separate tests for:

- unsorted `(category, ordinal)` order;
- duplicate identity;
- missing manifest identity;
- extra manifest identity;
- `id/category/ordinal` mismatch;
- suite mismatch;
- description drift;
- `passing → fail`;
- `blocked → pass`;
- `failing → pass`;
- excluded outcome changes accepted;
- represented outcome changes accepted; and
- state counts summing to the authoritative total.

- [ ] **Step 6: Run RED, complete validation, and verify GREEN**

```bash
python3 -m unittest scripts/tests/test_verify_parity_manifest.py -v
```

Expected before completion: the first new identity or transition test fails.
Complete only the required validation branches:

```python
identity = f"{entry.category} {entry.ordinal}"
if entry.id != identity:
    errors.append(f"{entry.id}: id must be {identity!r}")
```

Enforce these forbidden outcome transitions:

| State | Forbidden outcome transition |
|---|---|
| `passing` | any outcome except ordinary `pass` |
| `failing` | ordinary `pass` |
| `blocked` | ordinary `pass` |
| `applicable` | none |
| `excluded` | none |
| `represented` | none |

Also implement the tested identity-set, metadata-drift, ordering, and count
checks, then rerun until all pass.

- [ ] **Step 7: Implement summary and CLI tests**

Test this invocation:

```text
verify-parity-manifest.py harness.log qtest-results.xml parity/qtest-11.9.0.jsonl --summary parity-summary.md --step-summary step.md
```

Pin:

- exit 0 for a valid manifest;
- exit 1 for parser or validation errors;
- full summary includes total and every state count;
- step summary omits long identity lists;
- writes no successful-looking summary when `parse_run` raises `ResultError`.

- [ ] **Step 8: Run the validator and shared-parser tests**

```bash
python3 -m unittest \
  scripts/tests/test_qtest_results.py \
  scripts/tests/test_verify_parity_manifest.py -v
```

Expected: all pass.

- [ ] **Step 9: Commit the validator**

```bash
git add scripts/verify-parity-manifest.py scripts/tests/test_verify_parity_manifest.py
git commit -m "feat(qtest): validate explicit parity manifest"
```

---

### Task 2: Create the initial explicit 11.9.0 ledger

**Files:**
- Create: `parity/qtest-11.9.0.jsonl`
- Generated input only: ignored `harness.log`, `qtest-results.xml`

**Interfaces:**
- Consumes: two stable authoritative full-survey runs from Layer A
- Produces: one explicit validated row per authoritative qtest result

- [ ] **Step 1: Run two full surveys from one recorded flpdf build**

Use the same commands and isolated flpdf worktree as Layer A Task 4. Preserve
the two paired artifacts under:

```text
/tmp/flpdf-25kg-1-1-run1/harness.log
/tmp/flpdf-25kg-1-1-run1/qtest-results.xml
/tmp/flpdf-25kg-1-1-run2/harness.log
/tmp/flpdf-25kg-1-1-run2/qtest-results.xml
```

Expected: `parse_run` returns identical ordered `(id, outcome)` sequences and
the same root total for both runs.

- [ ] **Step 2: Define exhaustive suite ownership**

Use these exact default ownership groups when an entry is not assigned a
narrower Bead:

| Bead | Suites |
|---|---|
| `flpdf-25kg.2` | `compare-pdfs`, `completion`, `library-version`, `progress-reporting` |
| `flpdf-25kg.3` | `basic-parsing`, `bound-checks`, `dangling-refs`, `error-condition`, `exceptions`, `extensions-dictionary`, `from-scratch`, `get-xref`, `invalid-objects`, `keep-files-open`, `large-file`, `merge-dictionary`, `multiple-indirection`, `mutability`, `name-number-trees`, `numbers-and-strings`, `object-copying`, `object-handle-api`, `page-api`, `page-errors`, `pages-tree`, `parsed-offset`, `parsing`, `positive-p`, `replace-input`, `signature-dictionary`, `specific-bugs`, `swap-and-replace`, `tokenizer`, `type-checks`, `xref-errors` |
| `flpdf-25kg.4` | `character-encoding`, `check-encryption`, `cleartext-metadata`, `compression-level`, `custom-pipeline`, `decode-levels`, `decode-parameters`, `disable-filter-on-write`, `encryption`, `encryption-parameters`, `filter-abbreviations`, `image-optimization`, `inline-images`, `precheck-streams`, `specialized-filter`, `stream-data`, `stream-line-terminators`, `stream-replacements`, `token-filters`, `unicode-password`, `weak-cryptography` |
| `flpdf-25kg.5` | `appearance-streams`, `arg-parsing`, `attachments`, `coalesce-contents`, `collate`, `content-preservation`, `copy-annotations`, `copy-foreign-objects`, `extraction`, `final-version`, `fix-qdf`, `flatten-annotations`, `form-xobject`, `interactive-form`, `json`, `merge-and-split`, `name-normalization`, `outlines`, `output-redirection`, `overwrite-self`, `page-labels`, `page-without-contents`, `qpdf-json`, `qpdfjob`, `rotate-pages`, `specific-file`, `split-pages`, `unicode-filenames` |
| `flpdf-25kg.6` | `deterministic-id`, `incremental`, `linearization`, `linearize-pass1`, `many-nulls`, `newline-before-endstream`, `object-stream`, `pclm`, `preserve-unref`, `renumber-objects`, `writer-version`, `xref-streams` |

Before generating rows, assert that this table plus the C-API and Windows
exclusion suites equals the exact set of vendored `*.test` suite stems.
Invalid suites do not produce manifest rows in this baseline, but retaining
their ownership mapping makes a later transition to valid results explicit.
Do not use a default catch-all.

C-API suites are:

```text
c-api
c-api-check
c-api-key
c-api-object-handle
c-api-page
c-api-stream
```

The Windows exclusion suite is:

```text
windows-shell-globbing
```

- [ ] **Step 3: Expand the approved state rules into JSONL**

Use the first stable run and each testcase's command/actual output from XML.
Apply this exact precedence:

1. C-API suite or testcase command beginning `qpdf-ctest ` → `excluded`,
   rationale `C/C++ ABI route is outside Rust parity`, replacement
   `bead:flpdf-25kg.2.1`. This includes direct `qpdf-ctest` invocations embedded
   in otherwise portable suites.
2. Windows suite → `excluded`, rationale `Windows shell glob expansion is
   outside the Linux x86_64 gate`, replacement
   `scope:docs/superpowers/specs/2026-07-29-qpdf-observable-parity-roadmap-design.md#supported-platform`.
3. ordinary `Outcome.PASS` → `passing`.
4. `Outcome.EXPECTED_FAIL` → `applicable`, owned by the suite's phase Bead,
   rationale `Upstream EXPECT_FAILURE is not direct parity evidence`.
5. actual output containing `flpdf-qtest shim:` → `blocked`, owner
   `Mitsuru Hayasaka`, Bead `flpdf-egzr`.
6. command beginning `test_driver ` with actual output matching
   `^invalid test [0-9]+` → `blocked`, owner `Mitsuru Hayasaka`, Bead
   `flpdf-25kg.2.2`.
7. actual output containing `unexpected argument` → `blocked`, owner
   `Mitsuru Hayasaka`, suite-phase Bead, rationale naming the option from the
   output.
8. actual output containing `input file .* not found`, `No such file or
   directory`, or `unable to run command` → `blocked`, owner
   `Mitsuru Hayasaka`, suite-phase Bead, rationale `Prerequisite output or
   helper is unavailable`.
9. a remaining ordinary failure with a command or file comparison →
   `failing`, owner `Mitsuru Hayasaka`, suite-phase Bead, rationale
   `Reached qtest comparison differs from qpdf 11.9.0`.
10. any remaining result → `applicable`, owner `Mitsuru Hayasaka`,
    suite-phase Bead, rationale `Applicable behavior requires narrower
    root-cause triage`.

Write compact JSON with:

```python
json.dumps(entry, ensure_ascii=False, separators=(",", ":"))
```

Sort by `(category, ordinal)`. The checked-in file contains no rule comments or
group defaults.

- [ ] **Step 4: Verify the generated manifest**

```bash
python3 scripts/verify-parity-manifest.py \
  /tmp/flpdf-25kg-1-1-run1/harness.log \
  /tmp/flpdf-25kg-1-1-run1/qtest-results.xml \
  parity/qtest-11.9.0.jsonl \
  --summary /tmp/flpdf-25kg-1-1-manifest-summary.md
```

Expected:

- exit 0;
- manifest total equals qtest root total;
- every state count is present;
- no missing, extra, duplicate, unsorted, unowned, or stale entries.

- [ ] **Step 5: Review every suite boundary**

Render a bounded table with one row per suite:

```text
suite | total | applicable | excluded | represented | blocked | passing | failing | bead set
```

For each suite, compare the row total to its child XML summary and inspect:

- the first entry;
- the last entry;
- every entry whose state differs from the suite's dominant state; and
- every distinct Bead and rationale.

Correct the explicit JSONL rows, not the bootstrap precedence, and rerun the
validator after each suite group.

- [ ] **Step 6: Validate against the second run**

Run the same manifest command against run 2.

Expected: exit 0 and identical state counts.

- [ ] **Step 7: Commit the explicit ledger**

```bash
git add parity/qtest-11.9.0.jsonl
git commit -m "data(qtest): classify applicable 11.9.0 subtests"
```

---

### Task 3: Wire the permanent full-survey manifest gate

**Files:**
- Modify: `scripts/run.sh`
- Modify: `scripts/tests/test_run_contract.py`
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Invokes: allowlist and manifest validators against one paired run
- Produces: `qtest-parity-summary.md`

- [ ] **Step 1: Add manifest runner RED assertions**

Extend `test_run_contract.py` to require:

```python
self.assertIn('QTEST_FULL', script)
self.assertRegex(
    script,
    r'parity_args=\\(\\s*"\\$\\{log\\}"\\s+"\\$\\{qtest_xml\\}"\\s+'
    r'"\\$\\{manifest\\}"',
)
self.assertRegex(
    script,
    r'verify-parity-manifest\\.py"\\s+"\\$\\{parity_args\\[@\\]\\}"',
)
```

Also read `.github/workflows/ci.yml` and assert the qtest step sets:

```yaml
QTEST_FULL: "1"
```

- [ ] **Step 2: Run the contract tests to verify RED**

```bash
python3 -m unittest scripts/tests/test_run_contract.py -v
```

Expected: manifest invocation and full-survey environment assertions fail.

- [ ] **Step 3: Invoke the manifest validator from `run.sh`**

Define:

```bash
manifest="${repo_root}/parity/qtest-11.9.0.jsonl"
parity_summary="${repo_root}/qtest-parity-summary.md"
```

After allowlist verification succeeds, run:

```bash
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
```

Reject attempts to run manifest validation without `QTEST_FULL=1`; an
allowlist-scoped identity subset can never validate the full ledger.

- [ ] **Step 4: Make CI run the full corpus**

Add to the qtest acceptance step:

```yaml
QTEST_FULL: "1"
```

Add `qtest-parity-summary.md` to uploaded artifacts. Keep the existing
30-minute timeout and pinned action SHAs.

- [ ] **Step 5: Run contract and full Python tests**

```bash
python3 -m unittest discover -s scripts/tests -p 'test_*.py'
```

Expected: all pass.

- [ ] **Step 6: Commit CI wiring**

```bash
git add scripts/run.sh scripts/tests/test_run_contract.py .github/workflows/ci.yml
git commit -m "ci(qtest): gate full survey with parity manifest"
```

---

### Task 4: Document ledger maintenance

**Files:**
- Modify: `README.md`

**Interfaces:**
- Documents: scope, identity, state contracts, update commands, staged C API mapping

- [ ] **Step 1: Add a documentation contract RED test**

In `test_run_contract.py`, assert README contains:

```text
parity/qtest-11.9.0.jsonl
weak-cryptography-cryptography
bead:flpdf-25kg.2.1
QTEST_FULL=1
```

- [ ] **Step 2: Run the focused contract test to verify RED**

```bash
python3 -m unittest scripts/tests/test_run_contract.py -v
```

Expected: at least the first new README contract assertion fails.

- [ ] **Step 3: Add the parity-ledger section**

Document:

- why the ledger is in `flpdf-qtest`;
- XML `testid` identity versus suite stem;
- all six states;
- state-specific required fields;
- the three replacement-reference forms;
- `bead:flpdf-25kg.2.1` as the temporary C API mapping;
- promotion when blocked/failing starts passing;
- full local validation command; and
- the fact that pass counts are measurements.

- [ ] **Step 4: Run the full Python suite**

```bash
python3 -m unittest discover -s scripts/tests -p 'test_*.py'
```

Expected: all pass.

- [ ] **Step 5: Commit documentation**

```bash
git add README.md scripts/tests/test_run_contract.py
git commit -m "docs(qtest): explain parity ledger maintenance"
```

---

### Task 5: Verify and publish Layer B

**Files:**
- No additional tracked files expected

**Interfaces:**
- Verifies: `flpdf-25kg.1.1` acceptance criteria

- [ ] **Step 1: Run static quality checks**

```bash
python3 -m unittest discover -s scripts/tests -p 'test_*.py'
git diff --check
```

Expected: all tests pass and no whitespace errors.

- [ ] **Step 2: Run two independent full manifest surveys**

Run `scripts/run.sh` twice with `QTEST_FULL=1` and the same three flpdf
binaries. Preserve both paired artifact sets in distinct `/tmp` directories.

Expected on both runs:

- qtest total equals parser total;
- manifest state counts sum to that total;
- allowlist remains 39/39 with zero regressions and missing entries;
- manifest has zero missing, extra, duplicate, unowned, or stale entries; and
- ordered `(id, outcome)` sequences match.

- [ ] **Step 3: Verify the branch diff is scoped**

```bash
git status --short
git diff --stat fix/flpdf-25kg-1-2-qtest-result-accounting...HEAD
git log --oneline fix/flpdf-25kg-1-2-qtest-result-accounting..HEAD
```

Expected: only validator, tests, explicit manifest, runner/CI, and README
changes belonging to Layer B.

- [ ] **Step 4: Record Bead evidence**

Comment on `flpdf-25kg.1.1` with:

- qpdf tag;
- flpdf commit;
- flpdf-qtest commit;
- total and state counts;
- both-run allowlist results;
- both-run manifest verdicts; and
- exact test/survey commands.

- [ ] **Step 5: Push Git and Beads**

```bash
git push -u origin feat/flpdf-25kg-1-1-qtest-manifest
bd dolt push
```

Do not close the Bead until the branch is reviewed, CI is green, and every
acceptance criterion has fresh evidence.
