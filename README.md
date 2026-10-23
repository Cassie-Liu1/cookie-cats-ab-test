# Cookie Cats A/B Test: Optimising the First Progression Gate

[English](README.md) | [简体中文](README.zh-CN.md)


The case study also translates retention effects into player counts, cross-checks retention with Fisher's exact test, and provides a prospective sample-size plan plus an unimplemented follow-up protocol. Experiment validity is assessed separately from statistical significance.

A retrospective A/B testing case study based on the Cookie Cats mobile game dataset. The existing experiment compares moving the first progression gate from level 30 (`gate_30`) to level 40 (`gate_40`). This project analyses the supplied assignments; it does not claim to have run the original game experiment or shipped a product change.

## Business question

Does delaying the first gate improve player engagement and retention?

## Findings

Results from the local CSV (90,189 players), with `gate_40` as treatment:

| Metric | gate_30 | gate_40 | Treatment minus control | Holm p-value |
|---|---:|---:|---:|---:|
| Day-1 retention | 44.8188% | 44.2283% | -0.5905 percentage points | 0.1004 |
| Day-7 retention | 19.0201% | 18.2000% | -0.8201 percentage points | 0.0047 |
| Mean game rounds | 52.4563 | 51.2988 | -1.1575 rounds | 0.1004* |

The day-7 difference has a marginal 95% bootstrap interval of [-1.3278, -0.3226] percentage points (5,000 resamples, seed 42). Day-1 retention and game-round distributions do not show significant differences at the 5% level after Holm correction. The data supports retaining `gate_30` rather than rolling out `gate_40` for retention, conditional on the original experiment's validity. It does not establish why the effect occurs, profitability, or long-term effects beyond day 7.

Under an assumed 50:50 allocation, the sample sizes are 44,700 vs. 45,489 and the SRM p-value is 0.008608. This does not trigger the configured 0.001 screen, but would trigger a 0.05 screen. The planned ratio must be checked against source experiment logs; this result is not evidence that assignment was perfectly balanced or free from bias.

The default decision status is `hold_for_unverified_allocation`: do not roll out `gate_40` until the original planned split and exposure logs are reviewed. Statistical findings remain conditional. `--allocation-verified` records an operator declaration only; it must not be used to bypass missing evidence. Even with that declaration, other design checks remain unresolved. `allocation_sensitivity.csv` shows several hypothetical planned splits; it must not be used to select the split that maximizes the p-value.

The treatment-only logistic model has mean five-fold ROC AUCs of 0.5030 (day 1) and 0.5068 (day 7), close to the prevalence baseline's 0.5. It illustrates group association, not a strong retention predictor. Day-7 treatment odds ratio is 0.9473 with a marginal 95% interval [0.9160, 0.9796]. Logistic coefficient p-values are unadjusted exploratory outputs; the retention decision uses the Holm-adjusted tests above.

*Game rounds use a Mann-Whitney U distribution comparison, not a test of mean difference.

![Retention effects](reports/retention_effects.png)

## Business interpretation and follow-up design

Per 10,000 comparable players, the observed day-7 difference translates to approximately 82 fewer returning players under `gate_40`, with a marginal bootstrap interval of about 32 to 133 fewer. This is a scale conversion conditional on experiment validity and transportability, not realized rollout loss or a revenue forecast.

Planning uses the observed control day-7 rate as an estimated baseline. Detecting a hypothetical improvement of 0.5 percentage points at 80% power with one preregistered primary metric, two-sided alpha 0.05 and 1:1 allocation requires approximately 97,679 players per arm (195,358 total). The full plan explores 0.25 / 0.5 / 1.0 percentage points and 80% / 90% power, plus a conservative three-test Bonferroni design. These are prospective scenarios, not post-hoc observed power. A seeded binary-experiment simulation checks the planning approximation.

![Prospective sample size](reports/sample_size_plan.png)

Before a new experiment: agree the business MDE, preregister the day-7 primary metric and analysis, use stable assignments and exposure logs, set a fixed sample horizon, wait for complete day-7 observations, and review missingness, cross-group exposure, SRM and interference. No experiment duration is inferred without an eligible-player arrival rate. This is a proposed protocol, not an implemented or registered experiment. Statistical significance is considered alongside effect uncertainty and business thresholds; nonsignificance does not establish equivalence.

## Dataset

The source dataset contains 90,189 players and five columns:

- `userid`: unique player identifier
- `version`: `gate_30` or `gate_40`
- `sum_gamerounds`: rounds played during the observation period
- `retention_1`: whether the player returned on day 1
- `retention_7`: whether the player returned on day 7

Obtain the CSV separately from the dataset source linked below, review its terms, and place it at `data/cookie_cats.csv`. It is intentionally not committed here. The legacy helper command below validates the local columns without downloading data.

## Reproduce locally

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python scripts/download_data.py
MPLBACKEND=Agg .venv/bin/python src/analyze.py --input data/cookie_cats.csv --output reports
MPLBACKEND=Agg .venv/bin/python -m unittest discover -s tests -v
```

The analysis creates summary tables and plots under `reports/`. Bootstrap resamples, seed, CV folds, the expected control allocation and the SRM threshold are configurable via `--n-boot`, `--seed`, `--cv-folds`, `--expected-control-share` and `--srm-alpha`. Defaults are 5,000 / 42 / 5 / 0.5 / 0.001. Output files are regenerated by each run.


| Output | Purpose |
|---|---|
| `group_summary.csv` | Sample sizes and group metrics |
| `test_results.csv` | Absolute and relative changes, Wilson group CIs, analytic and bootstrap difference CIs, z-tests and Holm p-values |
| `bootstrap_retention_*.csv` | Reproducible empirical-bootstrap draws |
| `analysis_audit.json` | Data-quality checks, assumed allocation/SRM screen, settings, recommendation and limitations |
| `logistic_coefficients.csv` | Treatment-only logistic coefficients and odds-ratio intervals |
| `logistic_cv_folds.csv`, `logistic_cv_summary.csv` | Five-fold validation against a training-prevalence baseline |
| `game_rounds_by_group.png`, `retention_effects.png` | Distribution and effect-size charts |
| `business_impact.csv` | Observed retention differences scaled to 10,000 comparable players |
| `retention_exact_check.csv` | Secondary Fisher exact tests, Holm-corrected over the retention pair |
| `sample_size_plan.csv`, `sample_size_plan.png` | Prospective MDE / power / multiplicity scenarios |
| `allocation_sensitivity.csv` | SRM results under hypothetical planned splits, not a recovered allocation |

## Methods

### Notebook reproduction


- data-quality checks and duplicate-player checks
- sample-ratio mismatch (SRM) chi-square screen against an explicitly assumed 50:50 allocation
- group-level descriptive statistics
- distribution visualisation for game rounds
- Mann–Whitney U test for the skewed count metric
- 5,000-resample stratified bootstrap confidence intervals for retention-rate differences
- two-proportion z-tests and Holm correction across the three reported metrics
- unpooled normal-approximation intervals for retention differences alongside bootstrap intervals
- treatment-only binomial logistic regression and five-fold validation stratified by assignment/outcome
- optional common-weight stratification and Simpson-reversal screening for declared pre-treatment columns

All retention tests are two-sided so harm as well as benefit can be detected. The three-metric Holm family is an analysis choice for this retrospective case study, not a claim of original preregistration. Group Wilson intervals and effect intervals are marginal 95% intervals, not simultaneous or multiplicity-adjusted intervals. Bootstrap sign fractions describe the resampling distribution; they are not posterior probabilities or p-values.

Fisher's exact test is a secondary robustness check with its own two-retention Holm family; it does not replace the primary three-metric tests or repair design flaws. Prospective planning uses Cohen's h and a normal approximation. Implementation references: [SciPy Fisher exact test](https://docs.scipy.org/doc/scipy-1.10.1/reference/generated/scipy.stats.fisher_exact.html) and [statsmodels sample-size solver](https://www.statsmodels.org/stable/generated/statsmodels.stats.power.NormalIndPower.solve_power.html).

The treatment-only model is deliberately limited. `sum_gamerounds` is measured after assignment over an observation window that may overlap the retention outcomes, so it is excluded from total-effect adjustment and prospective prediction. `userid` is an identifier, not a feature. Cross-validation estimates predictive performance and does not independently validate randomization, prove causality or identify behavioral mechanisms. No hyperparameter search is claimed for this unregularized single-feature model.

The original five-column CSV has no eligible pre-treatment strata. With an enriched dataset, `--strata baseline_segment` (repeatable) computes subgroup effects and a pooled-common-weight difference, flags missing group overlap and screens for a direction reversal in every stratum. The caller must verify that such columns were measured before assignment. These outputs are exploratory, without subgroup significance claims or proof of heterogeneity; a Simpson screen alone cannot diagnose the original experiment. Synthetic tests demonstrate the implementation, not a finding in the Cookie Cats data.

## Portfolio highlights

This project demonstrates experimental design, metric selection, non-parametric inference, bootstrap uncertainty estimation, data visualisation, and translating statistical evidence into a product recommendation.

## Source material

The analysis was reconstructed from owner-supplied teaching materials. Dataset reference: https://www.kaggle.com/datasets/yufengsui/mobile-games-ab-testing (DataCamp project 184). The listed dataset license is Unknown; no redistribution rights are granted here.


The legacy `scripts/download_data.py` command now only validates a manually obtained CSV; it no longer downloads from a personal Drive.


Legacy teaching notebooks remain local pending authorship and publication-permission confirmation. The published analysis uses the executable Python pipeline and aggregate reports. Raw player records are not distributed.
