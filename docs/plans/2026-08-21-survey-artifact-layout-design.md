# Survey artifact layout: move run outputs under `survey/`

Date: 2026-08-21

## Problem

Every survey run writes seven artifacts to the repository root, and the
operational practice of snapshotting each generation (`*.fullsurvey-<sha>`)
plus recording the analysis (`FINDINGS-*.md`) accumulates there too. The root
currently holds 303 untracked files against 8 tracked entries, so `git status`
is unreadable and the repository layout no longer shows what the project is.

## Layout

```
survey/
├── latest/    # the seven artifacts run.sh regenerates on every run
├── history/   # per-generation snapshots (~261 files today)
└── findings/  # FINDINGS-*.md (42 today)
```

The split follows the distinction that already governs how these files are
used. `latest/` is a same-run set: `harness.log` and `qtest-results.xml` must
come from one invocation or both validators reject the pair. `history/` is
append-only evidence, consulted for cross-generation diffs. `findings/` is
prose for humans.

## Constraint: qtest-driver writes to cwd

`vendor/qtest/bin/qtest-driver` hardcodes its output names:

```perl
my $testlogfile  = 'qtest.log';
my $testxmlfile  = 'qtest-results.xml';
my $testjunitfile = "TEST-qtest$junit_suffix.xml";
```

There is no output-directory option — `-junit-suffix` only alters the JUnit
file's name — and `vendor/` must not be patched locally. So three of the seven
artifacts cannot simply be redirected.

Two ways out were considered:

1. **Run the driver with cwd set to `survey/latest/`.** Chosen.
2. Let it write to the root and `mv` afterwards. Rejected: the root is dirty
   for the duration of the run, and an interrupted run strands files there —
   which is exactly what the existing "clear every generated artifact before
   preflight" invariant exists to prevent.

Changing cwd is safe. The driver captures `my $cwd = getcwd()` once at startup,
and `run.sh` already passes `-datadir` and `-bindirs` as absolute paths derived
from a `mktemp -d` directory, so nothing else in the invocation is
cwd-relative.

## Changes

### `scripts/run.sh`

Three edits:

1. Point the artifact path variables at `${repo_root}/survey/latest/` and
   `mkdir -p` it. The existing `rm -f` of every artifact before any preflight
   check stays: a failed invocation must never leave a previous success looking
   current.
2. Invoke qtest-driver from a subshell that `cd`s into `survey/latest` first.
3. Nothing else. Both validators receive their paths through these variables,
   so they follow automatically.

### `.gitignore`

Replace the exact-name root patterns with `survey/latest/` and
`survey/history/`. `survey/findings/` stays untracked and unignored, matching
how `FINDINGS-*.md` is handled today; the move alone collapses 42 `??` lines to
one. Whether findings should be committed is a separate decision, deliberately
not bundled here.

### `.github/workflows/ci.yml`

Rewrite the seven `upload-artifact` paths under `survey/latest/`.

`actions/upload-artifact@v4` roots the artifact at the least common ancestor of
the paths it is given. All seven share `survey/latest`, so the uploaded
artifact keeps the same flat shape it has today and the downstream
`publish-metrics.sh artifacts/qtest-metrics.jsonl` needs no change. This
depends on documented v4 behavior rather than something reproducible locally,
so confirm it on the first nightly after merge.

### Tests

`scripts/tests/test_run_execution.py` carries most of the surface. Its
module-level `_ARTIFACTS` list drives `_preseed`, so retargeting those seven
entries at `survey/latest/` covers the bulk; roughly ten direct
`self.repo / "<name>"` assertions follow.

The fake qtest-driver this test installs opens `qtest.log`,
`qtest-results.xml` and `TEST-qtest.xml` by relative path, so it inherits the
cwd change and the suite verifies the new behavior rather than merely
tolerating it.

`scripts/tests/test_run_contract.py` needs two edits: the exact-match assertion
on `qtest_xml="${repo_root}/qtest-results.xml"` and the regex over the `rm -f`
list.

### README

Four sections reference the root paths: the layout tree, "Outputs", "Running
locally", and the `verify-parity-manifest.py` example under "Parity ledger
maintenance".

## Migrating what exists

310 files move, classified against the working tree. They fall into two
groups git treats differently: 303 are untracked, and the 7 live artifacts are
ignored by the current root patterns.

| Destination | Pattern | Count | Git status |
| --- | --- | --- | --- |
| `survey/findings/` | `FINDINGS-*.md` | 42 | untracked |
| `survey/history/` | `*.{fullsurvey,baseline,scope}-*` | 179 | untracked |
| `survey/history/` | `run-*.out` | 82 | untracked |
| `survey/latest/` | the seven live artifacts | 7 | ignored |

Neither group is in the index, so this is a plain `mv`, not `git mv`. The
migration must run in the primary checkout, since a fresh worktree would not
contain these files at all.

## Verification

1. `python3 -m pytest scripts/tests/` passes.
2. A real full survey writes all seven files to `survey/latest/` and both
   validators reach a verdict from that pair.
3. No artifact is left at the repository root.
4. Re-running `verify-parity-manifest.py` against the migrated live pair still
   reports the verdict it reported before the move.
