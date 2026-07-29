# qtest Authoritative Result Accounting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `flpdf-qtest` derive exactly the same valid subtest set and four outcome counts as qtest 11.9.0, eliminating the reproducible 2,790-versus-2,762 overcount without weakening allowlist matching.

**Architecture:** A new `scripts/qtest_results.py` module combines qtest's structured XML suite boundary with human log expectation markers. `verify-allowlist.py` consumes this authoritative model, while `run.sh` supplies the paired artifacts from one invocation.

**Tech Stack:** Python 3 standard library (`dataclasses`, `enum`, `re`, `xml.etree.ElementTree`), `unittest`, Bash, qtest 11.9.0 XML/log artifacts.

## Global Constraints

- Work on `fix/flpdf-25kg-1-2-qtest-result-accounting`, based on `add-test-driver-shim` / PR #23.
- qpdf/qtest 11.9.0 is the accounting oracle.
- Canonical subtest identity is the exact XML `testid`: category plus ordinal.
- The enclosing `.test` suite is a separate validity boundary and may differ from the category.
- Partial testcase records from a suite without child `<testsummary>` do not enter qtest's root total.
- Descriptions are metadata and existing allowlist selectors, never deduplication keys.
- Preserve the existing soft-fail allowlist verdict policy and metrics schema.
- Use RED → GREEN → REFACTOR for every production-code change.
- Push this layer independently before starting `flpdf-25kg.1.1`.

---

### Task 1: Parse qtest's authoritative result set

**Files:**
- Create: `scripts/qtest_results.py`
- Create: `scripts/tests/test_qtest_results.py`

**Interfaces:**
- Produces: `Outcome`, `Result`, `Summary`, `RunResults`, `parse_run(log_path, xml_path)`
- Consumes: paired `harness.log` and `qtest-results.xml` from one qtest invocation

- [ ] **Step 1: Write the result-model and valid-suite RED tests**

Create `scripts/tests/test_qtest_results.py` with an import helper matching the repository's existing script-test pattern and these helpers:

```python
from __future__ import annotations

import importlib.util
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_MODULE_PATH = _HERE.parent / "qtest_results.py"
_spec = importlib.util.spec_from_file_location("qtest_results", _MODULE_PATH)
assert _spec and _spec.loader
qtest_results = importlib.util.module_from_spec(_spec)
sys.modules["qtest_results"] = qtest_results
_spec.loader.exec_module(qtest_results)


def _tmp(suffix: str, content: str) -> Path:
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=suffix, delete=False, encoding="utf-8"
    ) as f:
        f.write(textwrap.dedent(content).lstrip())
        return Path(f.name)


def _xml(suites: str, *, total: int, passes: int, failures: int,
         xpasses: int = 0, xfails: int = 0) -> Path:
    return _tmp(
        ".xml",
        f"""\
        <?xml version="1.0"?>
        <qtest-results version="1">
        {textwrap.dedent(suites)}
         <testsummary total-cases="{total}" passes="{passes}"
          failures="{failures}" unexpected-passes="{xpasses}"
          expected-failures="{xfails}" missing-cases="0" extra-cases="0"/>
        </qtest-results>
        """,
    )
```

Add tests that pin identity and invalid-suite filtering:

```python
class ParseRunTest(unittest.TestCase):
    def test_uses_xml_testid_not_suite_stem_as_identity(self) -> None:
        xml = _xml(
            """
             <testsuite file="/repo/weak-cryptography.test">
              <testcase testid="weak-cryptography-cryptography 1"
               description="256-bit: no warning" outcome="pass"/>
              <testsummary total-cases="1" passes="1" failures="0"
               unexpected-passes="0" expected-failures="0"
               missing-cases="0" extra-cases="0"/>
             </testsuite>
            """,
            total=1,
            passes=1,
            failures=0,
        )
        log = _tmp(
            ".log",
            """
            weak-cryptography-cryptography  1 (256-bit: no warning) ... PASSED
            Total tests: 1
            Passes: 1
            Failures: 0
            Unexpected Passes: 0
            Expected Failures: 0
            """,
        )

        run = qtest_results.parse_run(log, xml)

        self.assertEqual(run.results[0].id, "weak-cryptography-cryptography 1")
        self.assertEqual(run.results[0].suite, "weak-cryptography")
        self.assertEqual(run.results[0].category, "weak-cryptography-cryptography")

    def test_discards_partial_cases_from_suite_without_child_summary(self) -> None:
        xml = _xml(
            """
             <testsuite file="/repo/valid.test">
              <testcase testid="valid 1" description="kept" outcome="pass"/>
              <testsummary total-cases="1" passes="1" failures="0"
               unexpected-passes="0" expected-failures="0"
               missing-cases="0" extra-cases="0"/>
             </testsuite>
             <testsuite file="/repo/invalid.test">
              <testcase testid="invalid 1" description="partial" outcome="fail"/>
             </testsuite>
            """,
            total=1,
            passes=1,
            failures=0,
        )
        log = _tmp(
            ".log",
            """
            valid  1 (kept) ... PASSED
            invalid test 1 (partial) FAILED
            Total tests: 1
            Passes: 1
            Failures: 0
            Unexpected Passes: 0
            Expected Failures: 0
            """,
        )

        run = qtest_results.parse_run(log, xml)

        self.assertEqual([r.id for r in run.results], ["valid 1"])
        self.assertEqual(run.invalid_suites, ("invalid",))
```

- [ ] **Step 2: Run the focused tests to verify RED**

Run:

```bash
python3 -m unittest scripts/tests/test_qtest_results.py -v
```

Expected: import failure because `scripts/qtest_results.py` does not exist.

- [ ] **Step 3: Implement the minimal result model and XML/log join**

Create `scripts/qtest_results.py` with these public types:

```python
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class ResultError(ValueError):
    pass


class Outcome(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    UNEXPECTED_PASS = "unexpected-pass"
    EXPECTED_FAIL = "expected-fail"


@dataclass(frozen=True)
class Result:
    suite: str
    category: str
    ordinal: int
    description: str
    outcome: Outcome

    @property
    def id(self) -> str:
        return f"{self.category} {self.ordinal}"

    @property
    def test(self) -> str:
        return self.category

    @property
    def subtest(self) -> str:
        return self.description

    @property
    def passed(self) -> bool:
        return self.outcome in (Outcome.PASS, Outcome.UNEXPECTED_PASS)


@dataclass(frozen=True)
class Summary:
    total: int
    passes: int
    failures: int
    unexpected_passes: int
    expected_failures: int


@dataclass(frozen=True)
class RunResults:
    results: tuple[Result, ...]
    summary: Summary
    invalid_suites: tuple[str, ...]
```

Implement only the minimum `parse_run(log_path: Path, xml_path: Path) ->
RunResults` behavior required by the two initial tests:

1. parse the XML root;
2. identify each suite stem from `Path(file).stem`;
3. treat a suite with no direct child `testsummary` as invalid;
4. split each retained `testid` with `rsplit(" ", 1)`;
5. parse ordinary `PASSED` and `FAILED` log status lines;
6. filter log identities by valid XML identities; and
7. return results sorted by `(category, ordinal)`.

Define `ResultError` now, but defer the other outcome branches, deduplication,
drift checks, and counter validation until after the expanded RED tests.

- [ ] **Step 4: Run the focused tests to verify GREEN**

Run:

```bash
python3 -m unittest scripts/tests/test_qtest_results.py -v
```

Expected: the two tests pass.

- [ ] **Step 5: Add RED tests for all four outcomes and hard failures**

Add separate tests for:

- ordinary pass;
- unexpected failure;
- `FAILED (exp)`;
- `PASSED-UNEXP`;
- repeated identical failure headers;
- duplicate XML test IDs;
- conflicting repeated log identities;
- XML-only identity;
- valid log-only identity;
- description mismatch;
- XML actual-outcome mismatch;
- child summary mismatch; and
- root summary mismatch.

The expected-failure assertion must be:

```python
self.assertEqual(run.results[0].outcome, qtest_results.Outcome.EXPECTED_FAIL)
self.assertEqual(run.summary.expected_failures, 1)
```

Each mismatch test uses:

```python
with self.assertRaisesRegex(qtest_results.ResultError, "root total"):
    qtest_results.parse_run(log, xml)
```

- [ ] **Step 6: Run the expanded tests to verify RED, then complete parsing**

Run:

```bash
python3 -m unittest scripts/tests/test_qtest_results.py -v
```

Expected before completion: the first newly added unsupported outcome or mismatch test fails.

Implement only the branches required by those tests:

1. parse `FAILED (exp)` and `PASSED-UNEXP`;
2. deduplicate repeated log lines only when the same identity, description,
   actual outcome, and expectation marker agree;
3. join XML actual outcome with the log expectation marker;
4. compare child and root counters; and
5. raise `ResultError` for malformed XML, invalid IDs, duplicate identities,
   conflicting repeated log records, XML/log identity drift, description
   drift, outcome drift, or count drift.

Then rerun the same command until all pass.

- [ ] **Step 7: Commit the authoritative parser**

```bash
git add scripts/qtest_results.py scripts/tests/test_qtest_results.py
git commit -m "fix(qtest): derive authoritative result set"
```

---

### Task 2: Migrate allowlist judgment to the shared parser

**Files:**
- Modify: `scripts/verify-allowlist.py`
- Modify: `scripts/tests/test_verify_allowlist.py`

**Interfaces:**
- Consumes: `qtest_results.parse_run(log_path, xml_path)`
- Preserves: `parse_allowlist`, `_bucket`, `judge`, `build_metrics`, existing Markdown and JSON schemas

- [ ] **Step 1: Add a synthetic paired-artifact helper to allowlist tests**

Import the shared result model and replace direct `verify_allowlist.Result(...)`
construction with:

```python
def _result(test: str, subtest: str, passed: bool, *, ordinal: int = 1):
    outcome = (
        qtest_results.Outcome.PASS if passed else qtest_results.Outcome.FAIL
    )
    return qtest_results.Result(
        suite=test,
        category=test,
        ordinal=ordinal,
        description=subtest,
        outcome=outcome,
    )
```

Update end-to-end `main()` tests so `_run` creates a matching XML artifact and
calls:

```python
verify_allowlist.main(
    [
        str(log),
        str(xml),
        str(allowlist),
        "--summary",
        str(full),
    ]
)
```

- [ ] **Step 2: Write the invalid-suite allowlist RED test**

Add a test where `invalid 1` appears in both log and XML but the enclosing XML
suite has no child summary. Put `invalid:partial` on the allowlist and assert
that it is reported missing rather than passing.

- [ ] **Step 3: Run focused allowlist tests to verify RED**

Run:

```bash
python3 -m unittest scripts/tests/test_verify_allowlist.py -v
```

Expected: failures because `verify-allowlist.py` still accepts only log and
allowlist paths and still owns the old `Result` parser.

- [ ] **Step 4: Remove the log parser and consume `RunResults`**

In `scripts/verify-allowlist.py`:

- import `Outcome`, `Result`, and `parse_run` from `qtest_results`;
- delete `_RESULT_RE_PASS`, `_RESULT_RE_FAIL`, `_SUMMARY_TOTAL_RE`, and
  `parse_log`;
- change positional arguments to `log`, `xml`, and `allowlist`;
- call `run = parse_run(args.log, args.xml)`;
- use `results = list(run.results)`;
- remove the old drift flag and warning path because count disagreement is now
  a hard `ResultError`;
- preserve the existing allowlist-regression soft verdict; and
- retain metrics keys, setting `"drift": false` for every successful parse.

Catch `ResultError`, prefix its parser diagnostic with `verify-allowlist:`,
print it to stderr, and return exit code 1 without emitting a misleading
successful summary.

- [ ] **Step 5: Run allowlist tests to verify GREEN**

Run:

```bash
python3 -m unittest scripts/tests/test_verify_allowlist.py -v
```

Expected: all migrated allowlist, headline, step-summary, and metrics tests pass.

- [ ] **Step 6: Run both focused modules**

```bash
python3 -m unittest \
  scripts/tests/test_qtest_results.py \
  scripts/tests/test_verify_allowlist.py -v
```

Expected: all pass.

- [ ] **Step 7: Commit the allowlist migration**

```bash
git add scripts/verify-allowlist.py scripts/tests/test_verify_allowlist.py
git commit -m "refactor(qtest): share authoritative result parsing"
```

---

### Task 3: Wire paired artifacts through the runner

**Files:**
- Modify: `scripts/run.sh`
- Modify: `.github/workflows/ci.yml`
- Modify: `README.md`

**Interfaces:**
- Produces: paired `harness.log` and `qtest-results.xml`
- Invokes: `verify-allowlist.py harness.log qtest-results.xml allowlist.txt`

- [ ] **Step 1: Add a runner contract RED test**

Create `scripts/tests/test_run_contract.py` that reads `scripts/run.sh` and
asserts the argument array contains all three ordered positional arguments and
the active invocation expands that array:

```python
class RunContractTest(unittest.TestCase):
    def test_allowlist_verifier_receives_paired_log_and_xml(self) -> None:
        script = (Path(__file__).parents[1] / "run.sh").read_text(encoding="utf-8")
        self.assertRegex(
            script,
            r'verify_args=\\(\\s*"\\$\\{log\\}"\\s+"\\$\\{qtest_xml\\}"\\s+'
            r'"\\$\\{repo_root\\}/allowlist\\.txt"',
        )
        self.assertRegex(
            script,
            r'verify-allowlist\\.py"\\s+"\\$\\{verify_args\\[@\\]\\}"',
        )
```

- [ ] **Step 2: Run the contract test to verify RED**

```bash
python3 -m unittest scripts/tests/test_run_contract.py -v
```

Expected: failure because `qtest_xml` and the three-argument invocation do not
exist.

- [ ] **Step 3: Wire `qtest-results.xml` into `run.sh`**

Define:

```bash
qtest_xml="${repo_root}/qtest-results.xml"
```

After qtest completes, require the file to exist for non-empty runs. Replace
the old `verify_args` positional setup with:

```bash
verify_args=(
    "${log}"
    "${qtest_xml}"
    "${repo_root}/allowlist.txt"
    --summary "${summary}"
)
```

Keep the empty-allowlist dry-run path unchanged because it does not execute
subtests and therefore has no authoritative result set.

- [ ] **Step 4: Upload the structured result artifact**

Add these paths to the workflow artifact list:

```yaml
qtest-results.xml
TEST-qtest.xml
```

Do not enable the full survey in this layer; Layer B owns the permanent
full-survey manifest gate.

- [ ] **Step 5: Document the paired parser**

Update README's outputs and local-running sections to state that
`qtest-results.xml` and `harness.log` are a required pair and that invalid
subsidiary suites are excluded exactly as qtest excludes them from its root
summary.

- [ ] **Step 6: Run the contract and full Python tests**

```bash
python3 -m unittest discover -s scripts/tests -p 'test_*.py'
```

Expected: all tests pass.

- [ ] **Step 7: Commit runner and documentation wiring**

```bash
git add scripts/run.sh scripts/tests/test_run_contract.py .github/workflows/ci.yml README.md
git commit -m "ci(qtest): verify paired result artifacts"
```

---

### Task 4: Reproduce and close the 28-result drift

**Files:**
- No tracked file changes expected
- Generated artifacts: ignored `harness.log`, `qtest-results.xml`, `qtest-summary.md`, `qtest-metrics.jsonl`

**Interfaces:**
- Verifies: `flpdf-25kg.1.2` acceptance criteria

- [ ] **Step 1: Build the exact flpdf survey branch in isolation**

Create an isolated flpdf worktree at the current
`origin/feat/flpdf-n9t0-2-test-driver` head:

```bash
git -C /home/ubuntu/flpdf fetch origin feat/flpdf-n9t0-2-test-driver
git -C /home/ubuntu/flpdf worktree add --detach \
  /tmp/flpdf-n9t0-2-survey \
  origin/feat/flpdf-n9t0-2-test-driver
git -C /tmp/flpdf-n9t0-2-survey rev-parse HEAD
```

Record the printed SHA, then run:

```bash
cd /tmp/flpdf-n9t0-2-survey
cargo build --release \
  --bin flpdf \
  --bin flpdf-test-compare \
  --bin flpdf-test-driver
```

Expected: all three binaries are produced from one recorded commit.

- [ ] **Step 2: Run the first full survey**

From the `flpdf-qtest` worktree:

```bash
QTEST_FULL=1 \
FLPDF_CLI_BIN=/tmp/flpdf-n9t0-2-survey/target/release/flpdf \
FLPDF_TEST_COMPARE_BIN=/tmp/flpdf-n9t0-2-survey/target/release/flpdf-test-compare \
FLPDF_TEST_DRIVER_BIN=/tmp/flpdf-n9t0-2-survey/target/release/flpdf-test-driver \
FLPDF_COMMIT="$(git -C /tmp/flpdf-n9t0-2-survey rev-parse HEAD)" \
./scripts/run.sh
```

Expected:

- parser total equals qtest root total;
- total is 2,762 for the recorded 2026-07-29 corpus/build;
- allowlist is 39/39;
- regressions are 0;
- missing entries are 0.

Copy the four ignored outputs to a fresh `/tmp/flpdf-25kg-1-2-run1/`
directory for comparison.

- [ ] **Step 3: Run the second independent full survey**

Repeat the exact command without reusing the first run's qtest temp directory.
Copy outputs to `/tmp/flpdf-25kg-1-2-run2/`.

Expected: the same total and allowlist judgment.

- [ ] **Step 4: Compare stable result identities and outcomes**

Use `scripts/qtest_results.py` through a small read-only invocation to render
`id<TAB>outcome` for both pairs, then compare them with `diff -u`.

Expected: no identity or outcome differences.

- [ ] **Step 5: Run final static verification**

```bash
python3 -m unittest discover -s scripts/tests -p 'test_*.py'
git diff --check
git status --short
```

Expected: all tests pass, no whitespace errors, and only intentional tracked
changes are present.

- [ ] **Step 6: Record evidence and push Layer A**

Add a Bead comment to `flpdf-25kg.1.2` containing:

- flpdf SHA;
- flpdf-qtest SHA;
- both qtest totals;
- both parser totals;
- both allowlist results; and
- focused/full test commands.

Then push:

```bash
git push
bd dolt push
```

Do not close `flpdf-25kg.1.1`; Layer B remains outstanding.
