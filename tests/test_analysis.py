import sys
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from analyze import (allocation_check, bootstrap_delta, logistic_analysis,
                     prediction_metrics, rate_difference, stratified_analysis, validate_data, main)
from experiment_planning import business_impact, exact_retention_check, sample_size_plan
from scipy.stats import norm


def sample_data():
    return pd.DataFrame({"userid": np.arange(40), "version": ["gate_30"] * 20 + ["gate_40"] * 20,
        "sum_gamerounds": np.arange(40), "retention_1": [0, 1] * 20,
        "retention_7": [0, 0, 0, 1] * 10})


class AnalysisTests(unittest.TestCase):
    def test_rejects_missing_assignments_and_invalid_rounds(self):
        for column, value in [("userid", None), ("version", None),
                              ("sum_gamerounds", -1), ("sum_gamerounds", 0.5),
                              ("sum_gamerounds", np.inf), ("retention_1", 2)]:
            with self.subTest(column=column, value=value):
                frame = sample_data()
                frame[column] = frame[column].astype(object)
                frame.loc[0, column] = value
                with self.assertRaises(ValueError):
                    validate_data(frame)

    def test_rejects_cross_group_duplicate_user(self):
        frame = sample_data()
        frame.loc[20, "userid"] = 0
        with self.assertRaisesRegex(ValueError, "duplicate"):
            validate_data(frame)

    def test_allocation_uses_declared_ratio(self):
        frame = sample_data()
        self.assertFalse(allocation_check(frame)["srm_flag"])
        self.assertTrue(allocation_check(frame, expected_control_share=0.9)["srm_flag"])
        self.assertFalse(allocation_check(frame)["planned_ratio_verified"])
        self.assertTrue(allocation_check(frame, allocation_verified=True)["planned_ratio_verified"])

    def test_business_impact_keeps_negative_effect_and_scales_interval(self):
        frame = pd.DataFrame([{"metric": "retention_7", "delta_gate40_minus_gate30": -0.008,
                              "ci_low": -0.013, "ci_high": -0.003}])
        result = business_impact(frame).iloc[0]
        self.assertEqual(result.delta_retained_players, -80)
        self.assertEqual(result.bootstrap_ci_low_players, -130)
        self.assertEqual(result.bootstrap_ci_high_players, -30)
        with self.assertRaises(ValueError):
            business_impact(frame, cohort_size=0)

    def test_prospective_design_in_simulated_binary_experiment(self):
        # Check approximate planning against the actual pooled two-proportion test.
        plan = sample_size_plan(0.19, mde_pp=[0.5], powers=[0.8])
        n = int(plan.loc[plan.design == "one_preregistered_primary", "control_players"].iloc[0])
        rng = np.random.default_rng(42)
        a, b = rng.binomial(n, 0.19, 100000), rng.binomial(n, 0.195, 100000)
        pooled = (a + b) / (2 * n)
        z = (b / n - a / n) / np.sqrt(pooled * (1 - pooled) * 2 / n)
        reject_fraction = (2 * norm.sf(np.abs(z)) < 0.05).mean()
        self.assertAlmostEqual(reject_fraction, 0.8, delta=0.01)

    def test_design_tradeoffs_and_infeasible_inputs(self):
        plan = sample_size_plan(0.19)
        ordinary = plan[plan.design == "one_preregistered_primary"]
        for _, group in ordinary.groupby("target_power"):
            self.assertTrue((np.diff(group.total_players.to_numpy()) < 0).all())
        for _, group in plan.groupby(["mde_percentage_points", "target_power"]):
            counts = group.set_index("design").total_players
            self.assertGreater(counts["three_tests_bonferroni_design"], counts["one_preregistered_primary"])
        self.assertTrue((plan.total_players == 2 * plan.control_players).all())
        for baseline, mde, power in [(0, [0.5], [0.8]), (0.99, [2], [0.8]),
                                     (0.19, [0], [0.8]), (0.19, [0.5], [1])]:
            with self.assertRaises(ValueError):
                sample_size_plan(baseline, mde, power)

    def test_exact_check_balanced_outcomes_has_no_difference(self):
        results = exact_retention_check(sample_data())
        np.testing.assert_allclose(results.odds_ratio, 1)
        np.testing.assert_allclose(results.fisher_p_value, 1)
        np.testing.assert_allclose(results.fisher_p_holm_retention_pair, 1)

    def test_full_pipeline_respects_unverified_allocation_and_writes_design_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input.csv"
            sample_data().to_csv(source, index=False)
            for verified, expected_status in [(False, "hold_for_unverified_allocation"),
                                               (True, "conditional_on_other_design_checks")]:
                with contextlib.redirect_stdout(io.StringIO()):
                    main(str(source), str(root / "reports"), n_boot=100, allocation_verified=verified)
                audit = json.loads((root / "reports/analysis_audit.json").read_text())
                self.assertEqual(audit["decision_status"], expected_status)
                self.assertEqual(len(audit["data_sha256"]), 64)
                plan = pd.read_csv(root / "reports/sample_size_plan.csv")
                self.assertEqual(len(plan), 12)
                self.assertTrue((root / "reports/business_impact.csv").is_file())
                self.assertTrue((root / "reports/retention_exact_check.csv").is_file())

    def test_bootstrap_direction_seed_and_analytic_interval(self):
        a = pd.Series([0] * 50 + [1] * 50)
        b = pd.Series([0] * 75 + [1] * 25)
        draws = bootstrap_delta(a, b, n_boot=500)
        np.testing.assert_array_equal(draws, bootstrap_delta(a, b, n_boot=500))
        result = rate_difference(a, b)
        self.assertEqual(result["delta_percentage_points"], -25)
        self.assertLess(result["analytic_ci_high"], 0)
        self.assertAlmostEqual(draws.mean(), -0.25, delta=0.02)

    def test_auc_with_ties(self):
        result = prediction_metrics(np.array([0, 1, 0, 1]), np.full(4, 0.5))
        self.assertEqual(result["roc_auc"], 0.5)
        self.assertEqual(result["brier_score"], 0.25)

    def test_logistic_is_treatment_only_and_cv_counts_each_player_once(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            logistic_analysis(sample_data(), out, folds=5)
            coefficients = pd.read_csv(out / "logistic_coefficients.csv")
            self.assertEqual(set(coefficients.feature), {"intercept", "gate_40"})
            np.testing.assert_allclose(coefficients.loc[coefficients.feature == "gate_40", "odds_ratio"], 1)
            cv = pd.read_csv(out / "logistic_cv_folds.csv")
            self.assertTrue((cv.train_players + cv.validation_players == 40).all())
            self.assertTrue((cv.groupby(["metric", "model"]).validation_players.sum() == 40).all())

    def test_simpson_screen_and_common_weight_effect(self):
        frames = []
        for version, stratum, n, positives in [("gate_30", "low", 100, 10),
            ("gate_30", "high", 900, 810), ("gate_40", "low", 900, 180),
            ("gate_40", "high", 100, 95)]:
            frames.append(pd.DataFrame({"version": [version] * n, "baseline_segment": [stratum] * n,
                "retention_1": [1] * positives + [0] * (n - positives),
                "retention_7": [1] * positives + [0] * (n - positives)}))
        frame = pd.concat(frames, ignore_index=True)
        rows, diagnostics = stratified_analysis(frame, ["baseline_segment"])
        self.assertEqual(len(rows), 4)
        for diagnostic in diagnostics:
            self.assertTrue(diagnostic["simpson_reversal_candidate"])
            self.assertLess(diagnostic["overall_delta"], 0)
            self.assertAlmostEqual(diagnostic["common_weight_delta"], 0.075)

    def test_strata_exclude_outcomes_and_require_overlap(self):
        frame = sample_data()
        with self.assertRaises(ValueError):
            stratified_analysis(frame, ["sum_gamerounds"])
        frame["baseline_segment"] = frame.version
        _, diagnostics = stratified_analysis(frame, ["baseline_segment"])
        self.assertTrue(all(not row["complete_overlap"] for row in diagnostics))
        self.assertTrue(all(row["common_weight_delta"] is None for row in diagnostics))


if __name__ == "__main__":
    unittest.main()
