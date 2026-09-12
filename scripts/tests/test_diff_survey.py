"""Unit tests for scripts/diff-survey.py.

Run with: python3 -m unittest scripts/tests/test_diff_survey.py
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_QTEST_RESULTS_PATH = _HERE.parent / "qtest_results.py"
_DIFF_PATH = _HERE.parent / "diff-survey.py"

_qtest_spec = importlib.util.spec_from_file_location(
    "qtest_results", _QTEST_RESULTS_PATH
)
assert _qtest_spec and _qtest_spec.loader, f"cannot load {_QTEST_RESULTS_PATH}"
qtest_results = importlib.util.module_from_spec(_qtest_spec)
sys.modules["qtest_results"] = qtest_results
_qtest_spec.loader.exec_module(qtest_results)

_diff_spec = importlib.util.spec_from_file_location("diff_survey", _DIFF_PATH)
assert _diff_spec and _diff_spec.loader, f"cannot load {_DIFF_PATH}"
diff_survey = importlib.util.module_from_spec(_diff_spec)
sys.modules["diff_survey"] = diff_survey
_diff_spec.loader.exec_module(diff_survey)

Outcome = qtest_results.Outcome


def _tmp(content: str, *, suffix: str = ".jsonl") -> Path:
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=suffix, delete=False, encoding="utf-8"
    ) as handle:
        handle.write(content)
        return Path(handle.name)


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


def _baseline_text(
    entries: list[dict], *, total: int, suites: dict[str, int]
) -> str:
    meta = {
        "schema": diff_survey.SCHEMA,
        "kind": "qtest-baseline",
        "total": total,
        "suites": suites,
    }
    lines = [json.dumps(meta, separators=(",", ":"), sort_keys=True)]
    lines.extend(
        json.dumps(entry, separators=(",", ":"), sort_keys=True)
        for entry in entries
    )
    return "".join(line + "\n" for line in lines)


def _entry(**overrides) -> dict:
    value = {
        "id": "c-api 1",
        "suite": "c-api",
        "category": "c-api",
        "ordinal": 1,
        "description": "check output",
        "outcome": "fail",
        "bead": None,
        "rationale": None,
    }
    value.update(overrides)
    return value


class DiffRunTest(unittest.TestCase):
    """The classification a caller acts on: regression, improvement, drift."""

    def test_run_matching_the_baseline_reports_nothing(self) -> None:
        run = _run(
            [
                _result("c-api", 1, "check output", Outcome.FAIL),
                _result("arg-parsing", 1, "required argument", Outcome.PASS),
            ]
        )
        baseline = diff_survey.load_baseline(
            _tmp(
                _baseline_text(
                    [_entry()], total=2, suites={"c-api": 1, "arg-parsing": 1}
                )
            )
        )

        diff = diff_survey.diff_run(run, baseline)

        self.assertEqual(diff.regressions, ())
        self.assertEqual(diff.improvements, ())
        self.assertEqual(diff.drift, ())

    def test_failure_absent_from_the_baseline_is_a_regression(self) -> None:
        run = _run(
            [
                _result("c-api", 1, "check output", Outcome.FAIL),
                _result("linearization", 23, "check lin-special", Outcome.FAIL),
            ]
        )
        baseline = diff_survey.load_baseline(
            _tmp(
                _baseline_text(
                    [_entry()], total=2, suites={"c-api": 1, "linearization": 1}
                )
            )
        )

        diff = diff_survey.diff_run(run, baseline)

        self.assertEqual(
            [(r.category, r.ordinal) for r in diff.regressions],
            [("linearization", 23)],
        )
        self.assertEqual(diff.improvements, ())

    def test_baseline_row_that_now_passes_is_an_improvement(self) -> None:
        run = _run([_result("c-api", 1, "check output", Outcome.PASS)])
        baseline = diff_survey.load_baseline(
            _tmp(_baseline_text([_entry()], total=1, suites={"c-api": 1}))
        )

        diff = diff_survey.diff_run(run, baseline)

        self.assertEqual(diff.regressions, ())
        self.assertEqual(
            [(e.category, e.ordinal) for e in diff.improvements],
            [("c-api", 1)],
        )

    def test_vanished_baseline_row_is_drift_not_an_improvement(self) -> None:
        """A .test that dies early takes its remaining subtests with it. That
        must never read as the suite having been fixed."""
        run = _run([_result("c-api", 2, "second check", Outcome.FAIL)])
        baseline = diff_survey.load_baseline(
            _tmp(
                _baseline_text(
                    [_entry(), _entry(id="c-api 2", ordinal=2,
                                      description="second check")],
                    total=2,
                    suites={"c-api": 2},
                )
            )
        )

        diff = diff_survey.diff_run(run, baseline)

        self.assertEqual(diff.improvements, ())
        self.assertTrue(diff.drift)
        self.assertIn("c-api", "\n".join(diff.drift))

    def test_suite_count_change_is_reported_with_both_counts(self) -> None:
        run = _run(
            [
                _result("c-api", 1, "check output", Outcome.FAIL),
                _result("c-api", 2, "second check", Outcome.PASS),
            ]
        )
        baseline = diff_survey.load_baseline(
            _tmp(_baseline_text([_entry()], total=3, suites={"c-api": 3}))
        )

        diff = diff_survey.diff_run(run, baseline)

        drift = "\n".join(diff.drift)
        self.assertIn("c-api", drift)
        self.assertIn("3", drift)
        self.assertIn("2", drift)

    def test_expected_failure_in_the_baseline_stays_known(self) -> None:
        run = _run(
            [_result("split-pages", 14, "check output", Outcome.EXPECTED_FAIL)]
        )
        baseline = diff_survey.load_baseline(
            _tmp(
                _baseline_text(
                    [
                        _entry(
                            id="split-pages 14",
                            suite="split-pages",
                            category="split-pages",
                            ordinal=14,
                            outcome="expected-fail",
                        )
                    ],
                    total=1,
                    suites={"split-pages": 1},
                )
            )
        )

        diff = diff_survey.diff_run(run, baseline)

        self.assertEqual(diff.regressions, ())
        self.assertEqual(diff.improvements, ())
        self.assertEqual(diff.drift, ())

    def test_new_expected_failure_is_not_a_regression(self) -> None:
        """EXPECT_FAILURE is declared by the .test script, not by flpdf, so an
        expected failure appearing is a corpus change rather than a defect."""
        run = _run(
            [_result("split-pages", 14, "check output", Outcome.EXPECTED_FAIL)]
        )
        baseline = diff_survey.load_baseline(
            _tmp(_baseline_text([], total=1, suites={"split-pages": 1}))
        )

        diff = diff_survey.diff_run(run, baseline)

        self.assertEqual(diff.regressions, ())

    def test_a_changed_recorded_outcome_is_drift(self) -> None:
        """EXPECT_FAILURE is declared by the .test script, but which side of it
        a case lands on is flpdf's doing: an `expected-fail` row turning into
        `unexpected-pass` means the behaviour moved. Re-vendoring the corpus
        shifts recorded outcomes the same way."""
        run = _run(
            [_result("split-pages", 14, "check output", Outcome.UNEXPECTED_PASS)]
        )
        baseline = diff_survey.load_baseline(
            _tmp(
                _baseline_text(
                    [
                        _entry(
                            id="split-pages 14",
                            suite="split-pages",
                            category="split-pages",
                            ordinal=14,
                            outcome="expected-fail",
                        )
                    ],
                    total=1,
                    suites={"split-pages": 1},
                )
            )
        )

        diff = diff_survey.diff_run(run, baseline)

        self.assertEqual(diff.regressions, ())
        self.assertEqual(diff.improvements, ())
        self.assertEqual(
            diff.drift,
            ("split-pages 14: outcome expected-fail -> unexpected-pass",),
        )

    def test_a_baseline_row_that_passes_is_only_an_improvement(self) -> None:
        """The outcome changed, but PASS is already reported as an
        improvement; saying it twice would double-count."""
        run = _run([_result("c-api", 1, "check output", Outcome.PASS)])
        baseline = diff_survey.load_baseline(
            _tmp(_baseline_text([_entry()], total=1, suites={"c-api": 1}))
        )

        diff = diff_survey.diff_run(run, baseline)

        self.assertEqual(len(diff.improvements), 1)
        self.assertEqual(diff.drift, ())

    def test_unexpected_pass_absent_from_the_baseline_is_a_regression(
        self,
    ) -> None:
        run = _run(
            [_result("c-api", 9, "check output", Outcome.UNEXPECTED_PASS)]
        )
        baseline = diff_survey.load_baseline(
            _tmp(_baseline_text([], total=1, suites={"c-api": 1}))
        )

        diff = diff_survey.diff_run(run, baseline)

        self.assertEqual(
            [(r.category, r.ordinal) for r in diff.regressions], [("c-api", 9)]
        )


class BaselineIoTest(unittest.TestCase):
    def test_load_rejects_a_file_without_a_meta_line(self) -> None:
        path = _tmp(
            json.dumps(_entry(), separators=(",", ":")) + "\n"
        )
        with self.assertRaises(diff_survey.BaselineError):
            diff_survey.load_baseline(path)

    def test_load_rejects_an_unknown_schema(self) -> None:
        text = _baseline_text([], total=0, suites={})
        text = text.replace('"schema":1', '"schema":99')
        with self.assertRaises(diff_survey.BaselineError):
            diff_survey.load_baseline(_tmp(text))

    def test_load_rejects_a_non_integer_ordinal(self) -> None:
        """The baseline is hand-edited -- accepting a quoted ordinal here
        makes its key miss the run's, reporting a known failure as a
        regression, and makes sorting a mixed file raise TypeError."""
        text = _baseline_text([_entry()], total=1, suites={"c-api": 1})
        with self.assertRaises(diff_survey.BaselineError):
            diff_survey.load_baseline(_tmp(text.replace('"ordinal":1', '"ordinal":"1"')))

    def test_load_rejects_a_boolean_ordinal(self) -> None:
        text = _baseline_text([_entry()], total=1, suites={"c-api": 1})
        with self.assertRaises(diff_survey.BaselineError):
            diff_survey.load_baseline(_tmp(text.replace('"ordinal":1', '"ordinal":true')))

    def test_load_rejects_a_non_string_description(self) -> None:
        text = _baseline_text([_entry()], total=1, suites={"c-api": 1})
        with self.assertRaises(diff_survey.BaselineError):
            diff_survey.load_baseline(
                _tmp(text.replace('"description":"check output"', '"description":7'))
            )

    def test_load_rejects_a_non_string_bead(self) -> None:
        text = _baseline_text([_entry()], total=1, suites={"c-api": 1})
        with self.assertRaises(diff_survey.BaselineError):
            diff_survey.load_baseline(
                _tmp(text.replace('"bead":null', '"bead":7'))
            )

    def test_render_round_trips_through_load(self) -> None:
        original = diff_survey.load_baseline(
            _tmp(
                _baseline_text(
                    [_entry(bead="flpdf-mwyo", rationale="C API unimplemented")],
                    total=4,
                    suites={"c-api": 4},
                )
            )
        )

        reloaded = diff_survey.load_baseline(
            _tmp(diff_survey.render_baseline(original))
        )

        self.assertEqual(reloaded, original)

    def test_entries_are_written_sorted_by_category_and_ordinal(self) -> None:
        baseline = diff_survey.build_baseline(
            _run(
                [
                    _result("c-api", 10, "tenth", Outcome.FAIL),
                    _result("c-api", 2, "second", Outcome.FAIL),
                    _result("arg-parsing", 1, "first", Outcome.FAIL),
                ]
            ),
            previous=None,
        )

        self.assertEqual(
            [(e.category, e.ordinal) for e in baseline.entries],
            [("arg-parsing", 1), ("c-api", 2), ("c-api", 10)],
        )


class BuildBaselineTest(unittest.TestCase):
    def test_build_records_every_non_passing_identity(self) -> None:
        baseline = diff_survey.build_baseline(
            _run(
                [
                    _result("c-api", 1, "fails", Outcome.FAIL),
                    _result("c-api", 2, "passes", Outcome.PASS),
                    _result("split-pages", 14, "xfail", Outcome.EXPECTED_FAIL),
                ]
            ),
            previous=None,
        )

        self.assertEqual(
            [(e.category, e.ordinal, e.outcome) for e in baseline.entries],
            [("c-api", 1, "fail"), ("split-pages", 14, "expected-fail")],
        )
        self.assertEqual(baseline.total, 3)
        self.assertEqual(baseline.suites, {"c-api": 2, "split-pages": 1})

    def test_build_preserves_annotations_of_surviving_rows(self) -> None:
        previous = diff_survey.load_baseline(
            _tmp(
                _baseline_text(
                    [_entry(bead="flpdf-mwyo", rationale="known")],
                    total=1,
                    suites={"c-api": 1},
                )
            )
        )

        baseline = diff_survey.build_baseline(
            _run(
                [
                    _result("c-api", 1, "check output", Outcome.FAIL),
                    _result("c-api", 2, "new failure", Outcome.FAIL),
                ]
            ),
            previous=previous,
        )

        kept, added = baseline.entries
        self.assertEqual((kept.bead, kept.rationale), ("flpdf-mwyo", "known"))
        self.assertEqual((added.bead, added.rationale), (None, None))

    def test_build_drops_rows_that_now_pass(self) -> None:
        previous = diff_survey.load_baseline(
            _tmp(_baseline_text([_entry()], total=1, suites={"c-api": 1}))
        )

        baseline = diff_survey.build_baseline(
            _run([_result("c-api", 1, "check output", Outcome.PASS)]),
            previous=previous,
        )

        self.assertEqual(baseline.entries, ())

    def test_build_refreshes_a_stale_description(self) -> None:
        previous = diff_survey.load_baseline(
            _tmp(
                _baseline_text(
                    [_entry(description="old wording")],
                    total=1,
                    suites={"c-api": 1},
                )
            )
        )

        baseline = diff_survey.build_baseline(
            _run([_result("c-api", 1, "new wording", Outcome.FAIL)]),
            previous=previous,
        )

        self.assertEqual(baseline.entries[0].description, "new wording")


class RegressionsJsonTest(unittest.TestCase):
    def test_json_carries_what_an_issue_needs(self) -> None:
        run = _run(
            [_result("linearization", 23, "check lin-special", Outcome.FAIL)]
        )
        baseline = diff_survey.load_baseline(
            _tmp(_baseline_text([], total=1, suites={"linearization": 1}))
        )

        payload = diff_survey.regressions_json(
            diff_survey.diff_run(run, baseline)
        )

        self.assertEqual(
            payload,
            [
                {
                    "id": "linearization 23",
                    "suite": "linearization",
                    "category": "linearization",
                    "ordinal": 23,
                    "description": "check lin-special",
                    "outcome": "fail",
                }
            ],
        )


class MainTest(unittest.TestCase):
    def test_fail_on_none_stays_green_with_regressions(self) -> None:
        diff = diff_survey.Diff(
            regressions=(
                _result("linearization", 23, "check lin-special", Outcome.FAIL),
            ),
            improvements=(),
            drift=(),
        )
        self.assertEqual(diff_survey.exit_code(diff, fail_on="none"), 0)

    def test_fail_on_regression_reports_regressions(self) -> None:
        diff = diff_survey.Diff(
            regressions=(
                _result("linearization", 23, "check lin-special", Outcome.FAIL),
            ),
            improvements=(),
            drift=(),
        )
        self.assertEqual(diff_survey.exit_code(diff, fail_on="regression"), 1)

    def test_fail_on_regression_ignores_improvements_and_drift(self) -> None:
        diff = diff_survey.Diff(
            regressions=(),
            improvements=(
                diff_survey.BaselineEntry(**_entry()),
            ),
            drift=("c-api: 3 -> 2",),
        )
        self.assertEqual(diff_survey.exit_code(diff, fail_on="regression"), 0)

    def test_fail_on_any_still_ignores_improvements(self) -> None:
        """`any` means everything the summary calls a FAIL. An improvement is
        not one, so no policy can make it fail a run."""
        diff = diff_survey.Diff(
            regressions=(),
            improvements=(diff_survey.BaselineEntry(**_entry()),),
            drift=(),
        )
        self.assertEqual(diff_survey.exit_code(diff, fail_on="any"), 0)
        self.assertIn("**Verdict: OK**", diff_survey.render_summary(diff))

    def test_fail_on_any_reports_drift(self) -> None:
        diff = diff_survey.Diff(
            regressions=(), improvements=(), drift=("c-api: 3 -> 2",)
        )
        self.assertEqual(diff_survey.exit_code(diff, fail_on="any"), 1)


class SummaryTest(unittest.TestCase):
    def test_summary_leads_with_regressions(self) -> None:
        diff = diff_survey.Diff(
            regressions=(
                _result("linearization", 23, "check lin-special", Outcome.FAIL),
            ),
            improvements=(diff_survey.BaselineEntry(**_entry()),),
            drift=(),
        )

        summary = diff_survey.render_summary(diff)

        self.assertLess(
            summary.index("linearization 23"), summary.index("c-api 1")
        )

    def test_summary_states_the_verdict_when_clean(self) -> None:
        diff = diff_survey.Diff(regressions=(), improvements=(), drift=())

        self.assertIn("**Verdict: OK**", diff_survey.render_summary(diff))

    def test_annotations_are_emitted_per_regression(self) -> None:
        diff = diff_survey.Diff(
            regressions=(
                _result("linearization", 23, "check lin-special", Outcome.FAIL),
                _result("c-api", 4, "check output", Outcome.FAIL),
            ),
            improvements=(),
            drift=(),
        )

        lines = diff_survey.render_annotations(diff).splitlines()

        self.assertEqual(len(lines), 2)
        self.assertTrue(all(line.startswith("::warning ") for line in lines))
        self.assertIn("linearization 23", lines[0])


if __name__ == "__main__":
    unittest.main()
