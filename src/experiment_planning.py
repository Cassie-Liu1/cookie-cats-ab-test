"""Business translation and prospective design, separate from observed significance."""

import numpy as np
import pandas as pd
from scipy.stats import fisher_exact
from statsmodels.stats.multitest import multipletests
from statsmodels.stats.power import NormalIndPower
from statsmodels.stats.proportion import proportion_effectsize


def business_impact(results: pd.DataFrame, cohort_size=10_000):
    """Scale observed risk differences; not a revenue or rollout forecast."""
    if not isinstance(cohort_size, int) or cohort_size <= 0:
        raise ValueError("cohort_size must be a positive integer")
    retention = results.loc[results.metric.isin(["retention_1", "retention_7"])].copy()
    return pd.DataFrame({
        "metric": retention.metric.to_numpy(),
        "hypothetical_players_per_policy": cohort_size,
        "delta_retained_players": cohort_size * retention.delta_gate40_minus_gate30.to_numpy(),
        "bootstrap_ci_low_players": cohort_size * retention.ci_low.to_numpy(),
        "bootstrap_ci_high_players": cohort_size * retention.ci_high.to_numpy(),
        "interpretation": "Scaled observed difference, conditional on experiment validity and transportability; not a forecast",
    })


def sample_size_plan(baseline_rate: float, mde_pp=(0.25, 0.5, 1.0), powers=(0.8, 0.9)):
    """Two-sided, equal-arm planning for a hypothetical retention increase.

    Cohen's h normal approximation; not post-hoc observed power. No runtime
    duration is inferred without a qualified-player arrival rate.
    """
    if not np.isfinite(baseline_rate) or not 0 < baseline_rate < 1:
        raise ValueError("baseline_rate must lie between 0 and 1")
    rows = []
    model = NormalIndPower()
    for pp in mde_pp:
        if not np.isfinite(pp) or pp <= 0 or baseline_rate + pp / 100 >= 1:
            raise ValueError("MDE must be positive and leave a feasible treatment probability")
        target = baseline_rate + pp / 100
        effect = abs(proportion_effectsize(target, baseline_rate))
        for power in powers:
            if not np.isfinite(power) or not 0 < power < 1:
                raise ValueError("target power must lie between 0 and 1")
            for family, alpha in [("one_preregistered_primary", 0.05),
                                  ("three_tests_bonferroni_design", 0.05 / 3)]:
                n = int(np.ceil(model.solve_power(effect_size=effect, power=power,
                                                  alpha=alpha, ratio=1.0, alternative="two-sided")))
                rows.append({"metric": "retention_7", "planning_baseline": baseline_rate,
                    "target_treatment_rate": target, "mde_percentage_points": pp,
                    "target_power": power, "design": family, "per_test_alpha": alpha,
                    "control_players": n, "treatment_players": n, "total_players": 2 * n,
                    "method": "Cohen h / NormalIndPower, two-sided, 1:1 allocation"})
    return pd.DataFrame(rows)


def exact_retention_check(df: pd.DataFrame):
    """Secondary two-sided Fisher checks, Holm-corrected over the retention pair."""
    rows = []
    for metric in ["retention_1", "retention_7"]:
        a = df.loc[df.version == "gate_30", metric].astype(int)
        b = df.loc[df.version == "gate_40", metric].astype(int)
        table = [[int(b.sum()), int(len(b) - b.sum())],
                 [int(a.sum()), int(len(a) - a.sum())]]
        odds_ratio, p = fisher_exact(table, alternative="two-sided")
        rows.append({"metric": metric, "treatment_retained": table[0][0],
            "treatment_not_retained": table[0][1], "control_retained": table[1][0],
            "control_not_retained": table[1][1], "odds_ratio": odds_ratio,
            "fisher_p_value": p})
    adjusted = multipletests([row["fisher_p_value"] for row in rows], method="holm")[1]
    for row, p in zip(rows, adjusted):
        row["fisher_p_holm_retention_pair"] = p
    return pd.DataFrame(rows)
