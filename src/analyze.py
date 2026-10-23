from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
import statsmodels.api as sm
from scipy.stats import chisquare, mannwhitneyu, norm, rankdata
from statsmodels.stats.multitest import multipletests
from statsmodels.stats.proportion import proportion_confint, proportions_ztest
from experiment_planning import business_impact, exact_retention_check, sample_size_plan


def bootstrap_delta(a: pd.Series, b: pd.Series, n_boot: int = 5000, seed: int = 42):
    rng = np.random.default_rng(seed)
    deltas = np.empty(n_boot)
    for i in range(n_boot):
        deltas[i] = rng.choice(b, len(b), replace=True).mean() - rng.choice(a, len(a), replace=True).mean()
    return deltas


def validate_data(df: pd.DataFrame):
    required = {"userid", "version", "sum_gamerounds", "retention_1", "retention_7"}
    missing = required.difference(df.columns)
    if missing:
        raise ValueError(f"Missing columns: {sorted(missing)}")

    if df[list(required)].isna().any().any():
        raise ValueError("required columns must not contain missing values")
    if df["userid"].duplicated().any():
        raise ValueError("Expected one row per player; duplicate userid values found")

    expected_groups = {"gate_30", "gate_40"}
    if set(df["version"].dropna().unique()) != expected_groups:
        raise ValueError(f"version must contain exactly {sorted(expected_groups)}")
    if df[["sum_gamerounds", "retention_1", "retention_7"]].isna().any().any():
        raise ValueError("metric columns must not contain missing values")
    for metric in ["retention_1", "retention_7"]:
        if not df[metric].isin([True, False, 0, 1]).all():
            raise ValueError(f"{metric} must be binary")
    rounds = pd.to_numeric(df["sum_gamerounds"], errors="coerce")
    if not (np.isfinite(rounds) & (rounds >= 0) & (rounds == np.floor(rounds))).all():
        raise ValueError("sum_gamerounds must contain finite, non-negative integers")


def allocation_check(df: pd.DataFrame, expected_control_share=0.5, alpha=0.001,
                     allocation_verified=False):
    """SRM screen under a declared planned allocation, not proof of randomization."""
    if not 0 < expected_control_share < 1 or not 0 < alpha < 1:
        raise ValueError("allocation share and SRM alpha must lie between 0 and 1")
    observed = df.version.value_counts().reindex(["gate_30", "gate_40"]).to_numpy()
    expected = len(df) * np.array([expected_control_share, 1 - expected_control_share])
    statistic, p = chisquare(observed, expected)
    return {"expected_control_share": expected_control_share, "observed_control": int(observed[0]),
            "observed_treatment": int(observed[1]), "chi2": float(statistic), "p_value": float(p),
            "alpha": alpha, "srm_flag": bool(p < alpha),
            "planned_ratio_verified": bool(allocation_verified),
            "limitation": "Planned allocation is assumed unless supplied; SRM cannot verify covariate balance or interference."}


def rate_difference(a, b):
    """Treatment minus control, with an unpooled normal-approximation 95% CI."""
    pa, pb = float(np.mean(a)), float(np.mean(b))
    delta = pb - pa
    se = np.sqrt(pa * (1 - pa) / len(a) + pb * (1 - pb) / len(b))
    margin = norm.ppf(0.975) * se
    return {"control_rate": pa, "treatment_rate": pb,
            "delta_gate40_minus_gate30": delta, "delta_percentage_points": 100 * delta,
            "relative_change": delta / pa if pa else np.nan,
            "analytic_ci_low": max(-1.0, delta - margin),
            "analytic_ci_high": min(1.0, delta + margin)}


def prediction_metrics(y, probability):
    probability = np.clip(probability, 1e-12, 1 - 1e-12)
    positive = y == 1
    n_pos, n_neg = positive.sum(), (~positive).sum()
    auc = ((rankdata(probability)[positive].sum() - n_pos * (n_pos + 1) / 2)
           / (n_pos * n_neg)) if n_pos and n_neg else np.nan
    return {"roc_auc": float(auc), "brier_score": float(np.mean((y - probability) ** 2)),
            "log_loss": float(-np.mean(y * np.log(probability) + (1 - y) * np.log(1 - probability)))}


def logistic_analysis(df, out, folds=5, seed=42):
    """Treatment-only association model; observation-window rounds are excluded."""
    if folds < 2:
        raise ValueError("CV requires at least two folds")
    treatment = (df.version == "gate_40").astype(int).to_numpy()
    design = np.column_stack([np.ones(len(df)), treatment])
    coefficient_rows, cv_rows = [], []
    for metric in ["retention_1", "retention_7"]:
        y = df[metric].astype(int).to_numpy()
        cell_counts = pd.Series(treatment * 2 + y).value_counts()
        if len(cell_counts) != 4 or cell_counts.min() < folds:
            raise ValueError(f"{metric}: each treatment/outcome cell needs at least {folds} players for CV")
        fit = sm.GLM(y, design, family=sm.families.Binomial()).fit()
        interval = fit.conf_int()
        for j, name in enumerate(["intercept", "gate_40"]):
            coefficient_rows.append({"metric": metric, "feature": name,
                "coefficient": fit.params[j], "odds_ratio": np.exp(fit.params[j]),
                "odds_ratio_ci_low": np.exp(interval[j, 0]),
                "odds_ratio_ci_high": np.exp(interval[j, 1]), "p_value": fit.pvalues[j],
                "interpretation": "Unadjusted association; no behavioral mechanism identified"})
        rng = np.random.default_rng(seed)
        fold_ids = np.empty(len(df), dtype=int)
        # Stratify jointly by assignment and outcome; every player is held out once.
        for cell in range(4):
            indices = rng.permutation(np.flatnonzero(treatment * 2 + y == cell))
            fold_ids[indices] = np.arange(len(indices)) % folds
        for fold in range(folds):
            test = fold_ids == fold
            train = ~test
            model = sm.GLM(y[train], design[train], family=sm.families.Binomial()).fit()
            for name, pred in [("treatment_only_logistic", model.predict(design[test])),
                               ("training_prevalence_baseline", np.full(test.sum(), y[train].mean()))]:
                cv_rows.append({"metric": metric, "model": name, "fold": fold + 1,
                    "train_players": int(train.sum()), "validation_players": int(test.sum()),
                    **prediction_metrics(y[test], pred)})
    coefficients, cv = pd.DataFrame(coefficient_rows), pd.DataFrame(cv_rows)
    coefficients.to_csv(out / "logistic_coefficients.csv", index=False)
    cv.to_csv(out / "logistic_cv_folds.csv", index=False)
    cv.groupby(["metric", "model"])[["roc_auc", "brier_score", "log_loss"]].agg(
        ["mean", "std"]).to_csv(out / "logistic_cv_summary.csv")


def stratified_analysis(df, columns):
    """Common-weight descriptive effects for declared pre-treatment attributes."""
    rows, diagnostics = [], []
    forbidden = {"userid", "version", "sum_gamerounds", "retention_1", "retention_7"}
    for column in columns:
        if column in forbidden:
            raise ValueError(f"{column} cannot serve as a pre-treatment stratum")
        if column not in df or df[column].isna().any():
            raise ValueError(f"stratum {column} must exist and have no missing values")
        if df[column].nunique() > 50:
            raise ValueError("strata must have at most 50 levels")
        for metric in ["retention_1", "retention_7"]:
            effects, weights = [], []
            for level, group in df.groupby(column, observed=True):
                a = group.loc[group.version == "gate_30", metric].astype(int)
                b = group.loc[group.version == "gate_40", metric].astype(int)
                row = {"stratum_column": column, "stratum": str(level), "metric": metric,
                    "control_players": len(a), "treatment_players": len(b),
                    "pooled_weight": len(group) / len(df)}
                if len(a) and len(b):
                    effect = rate_difference(a, b)
                    row.update(effect)
                    effects.append(effect["delta_gate40_minus_gate30"])
                    weights.append(row["pooled_weight"])
                rows.append(row)
            a = df.loc[df.version == "gate_30", metric].astype(int)
            b = df.loc[df.version == "gate_40", metric].astype(int)
            overall = rate_difference(a, b)["delta_gate40_minus_gate30"]
            overlap = len(effects) == df[column].nunique()
            standardized = float(np.dot(effects, weights)) if overlap else None
            diagnostics.append({"stratum_column": column, "metric": metric,
                "overall_delta": overall, "common_weight_delta": standardized,
                "complete_overlap": overlap,
                "opposite_direction_strata": sum(d * overall < 0 for d in effects),
                "simpson_reversal_candidate": bool(overlap and effects and
                    all(d * overall < 0 for d in effects)),
                "limitation": "Exploratory descriptive screen; reversal is not proof of confounding or heterogeneity."})
    return pd.DataFrame(rows), diagnostics


def main(input_path: str, output_dir: str, expected_control_share=0.5,
         srm_alpha=0.001, n_boot=5000, seed=42, cv_folds=5, strata=(),
         allocation_verified=False):
    if n_boot < 100:
        raise ValueError("Use at least 100 bootstrap resamples")
    df = pd.read_csv(input_path)
    validate_data(df)
    allocation = allocation_check(df, expected_control_share, srm_alpha, allocation_verified)
    strata_frame, strata_diagnostics = stratified_analysis(df, strata)
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    summary = df.groupby("version").agg(
        players=("userid", "size"),
        median_rounds=("sum_gamerounds", "median"),
        mean_rounds=("sum_gamerounds", "mean"),
        day1_retention=("retention_1", "mean"),
        day7_retention=("retention_7", "mean"),
    )
    summary.to_csv(out / "group_summary.csv")

    control = df.loc[df.version == "gate_30", "sum_gamerounds"]
    treatment = df.loc[df.version == "gate_40", "sum_gamerounds"]
    u_stat, p_value = mannwhitneyu(control, treatment, alternative="two-sided")

    results = [{"metric": "sum_gamerounds", "method": "Mann-Whitney U", "statistic": u_stat, "p_value": p_value}]
    for metric in ["retention_1", "retention_7"]:
        a = df.loc[df.version == "gate_30", metric].astype(int)
        b = df.loc[df.version == "gate_40", metric].astype(int)
        deltas = bootstrap_delta(a, b, n_boot=n_boot, seed=seed)
        low, high = np.quantile(deltas, [0.025, 0.975])
        z, p = proportions_ztest([b.sum(), a.sum()], [len(b), len(a)])
        control_ci = proportion_confint(a.sum(), len(a), method="wilson")
        treatment_ci = proportion_confint(b.sum(), len(b), method="wilson")
        results.append({"metric": metric, "method": "two-proportion z-test + stratified bootstrap",
            **rate_difference(a, b), "ci_low": low, "ci_high": high,
            "control_ci_low": control_ci[0], "control_ci_high": control_ci[1],
            "treatment_ci_low": treatment_ci[0], "treatment_ci_high": treatment_ci[1],
            "bootstrap_fraction_delta_below_zero": float((deltas < 0).mean()),
            "z_stat": z, "p_value": p})
        pd.Series(deltas, name="delta").to_csv(out / f"bootstrap_{metric}.csv", index=False)
    adjusted = multipletests([row["p_value"] for row in results], method="holm")[1]
    for row, adjusted_p in zip(results, adjusted):
        row["p_value_holm"] = adjusted_p
    result_frame = pd.DataFrame(results)
    result_frame.to_csv(out / "test_results.csv", index=False)
    impact = business_impact(result_frame)
    impact.to_csv(out / "business_impact.csv", index=False)
    baseline = float(summary.loc["gate_30", "day7_retention"])
    planning = sample_size_plan(baseline)
    planning.to_csv(out / "sample_size_plan.csv", index=False)
    exact_retention_check(df).to_csv(out / "retention_exact_check.csv", index=False)
    allocation_sensitivity = pd.DataFrame([
        {"assumed_control_share": share,
         "p_value": allocation_check(df, share, srm_alpha)["p_value"],
         "srm_at_0_001": allocation_check(df, share, srm_alpha)["srm_flag"]}
        for share in [0.49, 0.495, 0.5, 0.505, 0.51]])
    allocation_sensitivity.to_csv(out / "allocation_sensitivity.csv", index=False)
    logistic_analysis(df, out, folds=cv_folds, seed=seed)
    if strata:
        strata_frame.to_csv(out / "stratified_retention.csv", index=False)
    d7 = next(row for row in results if row["metric"] == "retention_7")
    decision = ("Investigate sample-ratio mismatch before interpreting treatment effects"
                if allocation["srm_flag"] else
                "Do not roll out gate_40; verify planned allocation and exposure logs. Retention findings are conditional."
                if not allocation["planned_ratio_verified"] else
                "Retain gate_30: gate_40 reduces day-7 retention"
                if d7["p_value_holm"] < 0.05 and d7["delta_gate40_minus_gate30"] < 0 else
                "No significant day-7 harm demonstrated; this does not establish benefit")
    audit = {"input": Path(input_path).name, "players": len(df),
        "data_sha256": hashlib.sha256(Path(input_path).read_bytes()).hexdigest(),
        "package_versions": {name: importlib.metadata.version(name) for name in
                             ["numpy", "pandas", "scipy", "statsmodels", "matplotlib", "seaborn"]},
        "missing_required_values": 0, "duplicate_userids": 0,
        "allocation_check": allocation, "bootstrap_resamples": n_boot, "seed": seed,
        "cv_folds": cv_folds, "hypothesis": "Two-sided equality of retention rates",
        "multiplicity": "Holm over game rounds, day-1 retention and day-7 retention",
        "intervals": "95% marginal intervals, not multiplicity-adjusted",
        "strata": strata_diagnostics, "recommendation": decision,
        "decision_status": "hold_for_srm" if allocation["srm_flag"] else
            "hold_for_unverified_allocation" if not allocation["planned_ratio_verified"] else
            "conditional_on_other_design_checks",
        "prospective_planning": "Uses control day-7 retention as an estimated baseline; hypothetical MDEs, not observed power",
        "limitations": ["Retrospective analysis of an existing assignment; no new randomization performed",
            "No planned allocation log or pre-treatment covariates in the original CSV",
            "No session-duration, payment, social, progression or exposure-log fields",
            "sum_gamerounds is post-treatment and excluded from causal adjustment and prediction",
            "Treatment-only logistic CV assesses prediction, not causal identification or mechanisms",
            "No live dashboard, A/A experiment or product rollout performed",
            "SRM does not establish randomization, no contamination or no network interference",
            "Bootstrap sign fractions are not posterior probabilities or hypothesis-test p-values"]}
    (out / "analysis_audit.json").write_text(json.dumps(audit, indent=2, ensure_ascii=False), encoding="utf-8")

    sns.set_theme(style="whitegrid")
    plt.figure(figsize=(10, 5))
    sns.boxplot(data=df, x="version", y="sum_gamerounds", showfliers=False)
    plt.title("Game rounds by experiment group")
    plt.tight_layout()
    plt.savefig(out / "game_rounds_by_group.png", dpi=160)
    plt.close()
    retention = result_frame[result_frame.metric.isin(["retention_1", "retention_7"])]
    fig, ax = plt.subplots(figsize=(8, 4))
    delta = retention.delta_percentage_points.to_numpy()
    ax.errorbar(delta, [0, 1], xerr=np.vstack([
        delta - 100 * retention.ci_low.to_numpy(),
        100 * retention.ci_high.to_numpy() - delta]), fmt="o", capsize=5)
    ax.axvline(0, color="gray", linestyle="--")
    ax.set_yticks([0, 1], ["Day 1", "Day 7"])
    ax.set_xlabel("gate_40 minus gate_30 (percentage points)")
    ax.set_title("Retention differences: marginal 95% bootstrap intervals")
    fig.tight_layout()
    fig.savefig(out / "retention_effects.png", dpi=160)
    plt.close(fig)
    fig, ax = plt.subplots(figsize=(8, 4))
    for power, group in planning.loc[planning.design == "one_preregistered_primary"].groupby("target_power"):
        ax.plot(group.mde_percentage_points, group.total_players, marker="o", label=f"Power {power:.0%}")
    ax.set(xlabel="Hypothetical day-7 improvement (percentage points)",
           ylabel="Total players (1:1 allocation)",
           title="Prospective sample size: two-sided alpha = 0.05")
    ax.legend()
    fig.tight_layout()
    fig.savefig(out / "sample_size_plan.png", dpi=160)
    plt.close(fig)
    print(summary)
    print(result_frame.to_string(index=False))
    print("Allocation screen:", allocation)
    print("Recommendation:", decision)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="data/cookie_cats.csv")
    parser.add_argument("--output", default="reports")
    parser.add_argument("--expected-control-share", type=float, default=0.5)
    parser.add_argument("--srm-alpha", type=float, default=0.001)
    parser.add_argument("--n-boot", type=int, default=5000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--cv-folds", type=int, default=5)
    parser.add_argument("--allocation-verified", action="store_true",
                        help="Only use after confirming the planned split in source experiment logs")
    parser.add_argument("--strata", action="append", default=[],
                        help="Declared pre-treatment categorical column; repeat for multiple columns")
    args = parser.parse_args()
    main(args.input, args.output, args.expected_control_share, args.srm_alpha,
         args.n_boot, args.seed, args.cv_folds, args.strata, args.allocation_verified)
