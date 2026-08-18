# -*- coding: utf-8 -*-
"""
TCE LATAM v2.8.6.1 — final robustness path-compatibility hotfix

Run after:
    %run /content/tce_master_v2_8_4_source_locked_final.py
    %run /content/tce_v2_8_5_1_methodological_enhancement_fixed.py

This patch does NOT change the frozen primary cohort, outcome, or exposure.
It fixes path attribute compatibility with the v2.8.4 Paths dataclass:
  manuscript_support -> manuscript; figures_supplement -> figures_supp; root -> output.
It adds:
  1) corrected non-zero spline confidence intervals using a PSD-projected
     cluster-robust covariance matrix;
  2) formal between-hospital / within-hospital volume decomposition;
  3) country-specific region-adjusted volume sensitivity;
  4) case-mix gradients across prior-year volume quartiles;
  5) age-sex-only standardized mortality sensitivity;
  6) an overdispersion-aware, minimum-precision funnel plot.
"""

from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import statsmodels.api as sm
import statsmodels.formula.api as smf
from patsy import build_design_matrices
from scipy.stats import chi2, norm

V286_VERSION = "2.8.6.1-final-robustness-paths-hotfix"
DEFAULT_BASE_V286 = Path("/content/drive/MyDrive/Projeto_TCE_Multinacional")


def _require_runtime_v286() -> None:
    required = [
        "activate_v280", "PATHS", "VOLUME_COUNTRIES", "COUNTRY_ORDER",
        "COUNTRY_DISPLAY", "_prepare_volume_data_v280", "cohort_path_v280",
        "fit_glm_v280", "apply_fdr_by_family_v280", "save_table",
        "save_figure", "collect_memory", "_log",
    ]
    missing = [name for name in required if name not in globals()]
    if missing:
        raise RuntimeError(
            "Load tce_master_v2_8_4_source_locked_final.py before v2.8.6. "
            f"Missing: {missing}"
        )


def _activate_v286(base_dir: Path | str = DEFAULT_BASE_V286) -> None:
    _require_runtime_v286()
    activate_v280(Path(base_dir))


def _nearest_psd_v286(covariance: np.ndarray) -> Tuple[np.ndarray, Dict[str, float]]:
    """Symmetrize and project a covariance matrix to the PSD cone.

    Cluster-robust sandwich covariance matrices can be very slightly indefinite
    numerically. Clipping negative eigenvalues avoids invalid negative delta-
    method variances and the zero-width confidence intervals seen in v2.8.4.
    """
    cov = np.asarray(covariance, dtype=float)
    cov = 0.5 * (cov + cov.T)
    eigenvalues, eigenvectors = np.linalg.eigh(cov)
    max_eig = float(np.max(eigenvalues)) if len(eigenvalues) else 0.0
    floor = max(1e-12, max_eig * 1e-10)
    clipped = np.maximum(eigenvalues, floor)
    repaired = (eigenvectors * clipped) @ eigenvectors.T
    repaired = 0.5 * (repaired + repaired.T)
    diagnostics = {
        "minimum_eigenvalue_before": float(np.min(eigenvalues)) if len(eigenvalues) else np.nan,
        "minimum_eigenvalue_after": float(np.min(clipped)) if len(clipped) else np.nan,
        "negative_eigenvalues": int(np.sum(eigenvalues < 0)),
        "eigenvalue_floor": float(floor),
    }
    return repaired, diagnostics


def _broad_phenotype_v286(series: pd.Series) -> pd.Series:
    if "_broad_phenotype_v285" in globals():
        return _broad_phenotype_v285(series)
    s = series.astype("string").fillna("OTHER_OR_UNSPECIFIED")
    focal = {
        "SUBDURAL_HEMORRHAGE", "EPIDURAL_HEMORRHAGE",
        "TRAUMATIC_SUBARACHNOID_HEMORRHAGE", "FOCAL_BRAIN_INJURY",
        "OTHER_INTRACRANIAL_INJURY",
    }
    diffuse = {
        "DIFFUSE_BRAIN_INJURY", "TRAUMATIC_CEREBRAL_EDEMA",
        "INTRACRANIAL_INJURY_WITH_PROLONGED_COMA",
    }
    out = pd.Series("OTHER_OR_UNSPECIFIED", index=s.index, dtype="string")
    out.loc[s.isin(focal)] = "FOCAL_OR_HEMORRHAGIC"
    out.loc[s.isin(diffuse)] = "DIFFUSE_OR_EDEMA"
    out.loc[s.eq("CONCUSSION")] = "CONCUSSION"
    return out


def _patsy_safe_v286(frame: pd.DataFrame, categorical: Sequence[str], numeric: Sequence[str]) -> pd.DataFrame:
    out = frame.copy()
    for col in categorical:
        if col in out:
            out[col] = out[col].astype("string").astype(object)
    for col in numeric:
        if col in out:
            out[col] = pd.to_numeric(out[col], errors="coerce").astype(float)
    return out


def run_corrected_splines_v286(
    base_dir: Path | str = DEFAULT_BASE_V286,
    sample_size: int = 15000,
    grid_points: int = 50,
) -> Dict[str, str]:
    _activate_v286(base_dir)
    curves: List[pd.DataFrame] = []
    diagnostics: List[Dict[str, Any]] = []

    for country in VOLUME_COUNTRIES:
        _log(f"v2.8.6 corrected spline: {country}")
        data = _prepare_volume_data_v280(False)
        data = data[data["country"].eq(country)].copy()
        required = [
            "death_in_hospital", "log_lag_volume", "lag_volume", "age",
            "sex", "trauma_subtype", "year", "hospital_id",
        ]
        data = data.dropna(subset=required)
        data = data[data["death_in_hospital"].isin([0, 1])]
        data = _patsy_safe_v286(
            data,
            categorical=["sex", "trauma_subtype", "year", "hospital_id"],
            numeric=["death_in_hospital", "log_lag_volume", "lag_volume", "age"],
        )
        spline_formula = (
            "death_in_hospital ~ cr(log_lag_volume, df=4) + "
            "bs(age, df=4, degree=3, include_intercept=False) + "
            "C(sex) + C(trauma_subtype) + C(year)"
        )
        linear_formula = (
            "death_in_hospital ~ log_lag_volume + "
            "bs(age, df=4, degree=3, include_intercept=False) + "
            "C(sex) + C(trauma_subtype) + C(year)"
        )
        model = smf.glm(spline_formula, data=data, family=sm.families.Binomial(), missing="drop")
        labels = list(model.data.row_labels)
        groups = data.loc[labels, "hospital_id"].astype(str).to_numpy()
        fit = model.fit(cov_type="cluster", cov_kwds={"groups": groups}, maxiter=150)
        linear = smf.glm(linear_formula, data=data, family=sm.families.Binomial(), missing="drop").fit(maxiter=150)

        covariance_raw = np.asarray(fit.cov_params(), dtype=float)
        covariance, cov_diag = _nearest_psd_v286(covariance_raw)
        beta = np.asarray(fit.params, dtype=float)
        design_info = fit.model.data.design_info

        volume_values = pd.to_numeric(data["lag_volume"], errors="coerce").dropna()
        lower = max(1.0, float(volume_values.quantile(0.05)))
        upper = max(lower + 1.0, float(volume_values.quantile(0.95)))
        grid = np.unique(np.round(np.geomspace(lower, upper, grid_points), 3))
        sample = data.sample(n=min(sample_size, len(data)), random_state=286).copy()
        country_rows: List[Dict[str, Any]] = []
        for volume in grid:
            new_data = sample.copy()
            new_data["log_lag_volume"] = np.log1p(volume)
            design = np.asarray(
                build_design_matrices([design_info], new_data, return_type="dataframe")[0],
                dtype=float,
            )
            eta = design @ beta
            probability = 1.0 / (1.0 + np.exp(-np.clip(eta, -35, 35)))
            risk = float(np.mean(probability))
            gradient = np.mean((probability * (1.0 - probability))[:, None] * design, axis=0)
            variance = float(gradient @ covariance @ gradient)
            se = math.sqrt(max(0.0, variance))
            country_rows.append({
                "country": country,
                "hospital_volume": float(volume),
                "predicted_mortality": risk,
                "ci_low": max(0.0, risk - 1.96 * se),
                "ci_high": min(1.0, risk + 1.96 * se),
                "standardization_sample_n": int(len(sample)),
                "prediction_se": se,
            })

        lr = max(0.0, 2.0 * (float(fit.llf) - float(linear.llf)))
        df_difference = max(1, int(round(float(fit.df_model - linear.df_model))))
        p_nonlin = float(chi2.sf(lr, df_difference))
        curve = pd.DataFrame(country_rows)
        curve["nonlinearity_lr"] = lr
        curve["nonlinearity_df"] = df_difference
        curve["nonlinearity_p"] = p_nonlin
        curves.append(curve)
        widths = curve["ci_high"] - curve["ci_low"]
        diagnostics.append({
            "country": country,
            "n": int(fit.nobs),
            "hospitals": int(data["hospital_id"].nunique()),
            "nonlinearity_lr": lr,
            "nonlinearity_df": df_difference,
            "nonlinearity_p": p_nonlin,
            "zero_width_intervals": int((widths <= 1e-12).sum()),
            "minimum_ci_width": float(widths.min()),
            **cov_diag,
        })
        del data, sample, fit, linear, model
        collect_memory()

    result = pd.concat(curves, ignore_index=True, sort=False)
    diagnostic_frame = pd.DataFrame(diagnostics)
    save_table(result, PATHS.tables / "Table_7_Adjusted_volume_spline_predictions_v286")
    save_table(diagnostic_frame, PATHS.qc / "Corrected_spline_CI_diagnostics_v286")

    fig, axes = plt.subplots(1, 2, figsize=(13, 5.5), sharey=True)
    for ax, country in zip(axes, VOLUME_COUNTRIES):
        sub = result[result["country"].eq(country)].sort_values("hospital_volume")
        ax.plot(sub["hospital_volume"], 100 * sub["predicted_mortality"], linewidth=2.3)
        ax.fill_between(
            sub["hospital_volume"].to_numpy(dtype=float),
            (100 * sub["ci_low"]).to_numpy(dtype=float),
            (100 * sub["ci_high"]).to_numpy(dtype=float),
            alpha=0.2,
        )
        ax.set_xscale("log")
        p = float(sub["nonlinearity_p"].iloc[0])
        p_text = "<0.001" if p < 0.001 else f"{p:.3f}"
        ax.set_title(f"{COUNTRY_DISPLAY[country]}\nP for nonlinearity = {p_text}")
        ax.set_xlabel("Prior-year hospital TBI volume (log scale)")
        ax.grid(alpha=0.2)
    axes[0].set_ylabel("Adjusted predicted in-hospital mortality (%)")
    fig.suptitle("Adjusted mortality across prior-year hospital TBI volume", fontsize=14, fontweight="bold")
    save_figure(fig, PATHS.figures_main / "Figure_3_Adjusted_volume_splines_v286")

    return {
        "table": str(PATHS.tables / "Table_7_Adjusted_volume_spline_predictions_v286.csv"),
        "diagnostics": str(PATHS.qc / "Corrected_spline_CI_diagnostics_v286.csv"),
        "figure": str(PATHS.figures_main / "Figure_3_Adjusted_volume_splines_v286.png"),
    }


def _volume_decomposition_frame_v286(country: str) -> pd.DataFrame:
    data = _prepare_volume_data_v280(False)
    data = data[data["country"].eq(country)].copy()
    required = [
        "death_in_hospital", "log_lag_volume", "lag_volume", "age", "sex",
        "trauma_subtype", "year", "hospital_id", "hospital_region",
    ]
    data = data.dropna(subset=required)
    data = data[data["death_in_hospital"].isin([0, 1])].copy()

    hospital_year = data[["hospital_id", "year", "log_lag_volume"]].drop_duplicates(
        subset=["hospital_id", "year"]
    )
    history = hospital_year.groupby("hospital_id", observed=True).agg(
        hospital_mean_log_lag_volume=("log_lag_volume", "mean"),
        lagged_years=("year", "nunique"),
    ).reset_index()
    data = data.merge(history, on="hospital_id", how="left", validate="many_to_one")
    data = data[data["lagged_years"].ge(2)].copy()
    data["within_log_lag_volume"] = data["log_lag_volume"] - data["hospital_mean_log_lag_volume"]

    between_unique = history.loc[history["lagged_years"].ge(2), "hospital_mean_log_lag_volume"].astype(float)
    within_unique = hospital_year.merge(history, on="hospital_id", how="left")
    within_unique = within_unique[within_unique["lagged_years"].ge(2)].copy()
    within_unique["within"] = within_unique["log_lag_volume"] - within_unique["hospital_mean_log_lag_volume"]
    between_sd = float(between_unique.std(ddof=0))
    within_sd = float(within_unique["within"].std(ddof=0))
    if not np.isfinite(between_sd) or between_sd <= 0 or not np.isfinite(within_sd) or within_sd <= 0:
        raise RuntimeError(f"{country}: invalid between/within volume SD")
    data["between_volume_z"] = (
        data["hospital_mean_log_lag_volume"] - float(between_unique.mean())
    ) / between_sd
    data["within_volume_z"] = data["within_log_lag_volume"] / within_sd
    data["between_sd_log_volume"] = between_sd
    data["within_sd_log_volume"] = within_sd
    return data


def run_between_within_and_region_models_v286(
    base_dir: Path | str = DEFAULT_BASE_V286,
) -> Dict[str, str]:
    _activate_v286(base_dir)
    decomposition_rows: List[Dict[str, Any]] = []
    region_rows: List[Dict[str, Any]] = []

    for country in VOLUME_COUNTRIES:
        _log(f"v2.8.6 between-within decomposition: {country}")
        data = _volume_decomposition_frame_v286(country)
        data = _patsy_safe_v286(
            data,
            categorical=["sex", "trauma_subtype", "year", "hospital_id", "hospital_region"],
            numeric=[
                "death_in_hospital", "age", "between_volume_z", "within_volume_z",
                "lag_volume_z_country_year",
            ],
        )
        base = (
            "bs(age, df=4, degree=3, include_intercept=False) + C(sex) + "
            "C(trauma_subtype) + C(year) + C(hospital_region)"
        )
        rows, _ = fit_glm_v280(
            data,
            "death_in_hospital ~ between_volume_z + within_volume_z + " + base,
            sm.families.Binomial(),
            f"Hybrid between-within hospital volume model: {COUNTRY_DISPLAY[country]}",
            "hospital_id",
            ["between_volume_z", "within_volume_z"],
            "Adjusted OR per 1-SD increase",
            "between_within_volume",
            "key interpretive sensitivity",
        )
        for row in rows:
            row["country"] = country
            row["hospitals"] = int(data["hospital_id"].nunique())
            row["between_sd_log_volume"] = float(data["between_sd_log_volume"].iloc[0])
            row["within_sd_log_volume"] = float(data["within_sd_log_volume"].iloc[0])
        decomposition_rows.extend(rows)

        _log(f"v2.8.6 region-adjusted volume sensitivity: {country}")
        region_model_rows, _ = fit_glm_v280(
            data,
            "death_in_hospital ~ lag_volume_z_country_year + " + base,
            sm.families.Binomial(),
            f"Region-adjusted prior-year hospital volume: {COUNTRY_DISPLAY[country]}",
            "hospital_id",
            ["lag_volume_z_country_year"],
            "OR per 1-SD increase in prior-year log volume",
            "region_adjusted_volume",
            "geographic-confounding sensitivity",
        )
        for row in region_model_rows:
            row["country"] = country
            row["hospitals"] = int(data["hospital_id"].nunique())
        region_rows.extend(region_model_rows)
        del data
        collect_memory()

    decomposition = apply_fdr_by_family_v280(pd.DataFrame(decomposition_rows))
    region = apply_fdr_by_family_v280(pd.DataFrame(region_rows))
    save_table(decomposition, PATHS.tables / "Table_12_Between_within_volume_decomposition_v286")
    save_table(region, PATHS.tables / "Supplementary_Table_15_Region_adjusted_volume_v286")
    return {
        "between_within": str(PATHS.tables / "Table_12_Between_within_volume_decomposition_v286.csv"),
        "region_adjusted": str(PATHS.tables / "Supplementary_Table_15_Region_adjusted_volume_v286.csv"),
    }


def run_case_mix_by_volume_v286(
    base_dir: Path | str = DEFAULT_BASE_V286,
) -> str:
    _activate_v286(base_dir)
    data = _prepare_volume_data_v280(False)
    data = data.dropna(subset=["country", "year", "hospital_id", "lag_volume", "age", "sex", "trauma_subtype"])
    data = data[data["country"].isin(VOLUME_COUNTRIES)].copy()
    data["broad_phenotype"] = _broad_phenotype_v286(data["trauma_subtype"])
    percentile = data.groupby(["country", "year"], observed=True)["lag_volume"].rank(method="average", pct=True)
    data["prior_volume_quartile"] = pd.cut(
        percentile,
        bins=[0.0, 0.25, 0.50, 0.75, 1.0000001],
        labels=["Q1", "Q2", "Q3", "Q4"],
        include_lowest=True,
    )
    data["male"] = data["sex"].astype("string").str.lower().eq("male")
    data["age_70plus"] = pd.to_numeric(data["age"], errors="coerce").ge(70)
    data["structural_phenotype"] = ~data["broad_phenotype"].isin(["CONCUSSION", "OTHER_OR_UNSPECIFIED"])
    data["unspecified_phenotype"] = data["broad_phenotype"].eq("OTHER_OR_UNSPECIFIED")
    data["subdural"] = data["trauma_subtype"].astype("string").eq("SUBDURAL_HEMORRHAGE")

    rows: List[Dict[str, Any]] = []
    for (country, quartile), sub in data.groupby(["country", "prior_volume_quartile"], observed=True):
        death = pd.to_numeric(sub["death_in_hospital"], errors="coerce")
        los = pd.to_numeric(sub["los_days"], errors="coerce")
        lag = pd.to_numeric(sub["lag_volume"], errors="coerce")
        row = {
            "country": country,
            "prior_volume_quartile": str(quartile),
            "admissions": int(len(sub)),
            "hospitals": int(sub["hospital_id"].nunique()),
            "median_prior_year_volume": float(lag.median()),
            "age_median": float(pd.to_numeric(sub["age"], errors="coerce").median()),
            "age_70plus_pct": 100 * float(sub["age_70plus"].mean()),
            "male_pct": 100 * float(sub["male"].mean()),
            "structural_phenotype_pct": 100 * float(sub["structural_phenotype"].mean()),
            "unspecified_phenotype_pct": 100 * float(sub["unspecified_phenotype"].mean()),
            "subdural_pct": 100 * float(sub["subdural"].mean()),
            "mortality_pct": 100 * float(death.mean()),
            "los_median": float(los.median()),
            "icu_any_pct": np.nan,
            "primary_acute_surgery_pct": np.nan,
        }
        if country == "brasil":
            icu = pd.to_numeric(sub["icu_any"], errors="coerce")
            surgery = pd.to_numeric(sub["primary_acute_surgery"], errors="coerce")
            row["icu_any_pct"] = 100 * float(icu.mean()) if icu.notna().any() else np.nan
            row["primary_acute_surgery_pct"] = 100 * float(surgery.mean()) if surgery.notna().any() else np.nan
        rows.append(row)
    result = pd.DataFrame(rows).sort_values(["country", "prior_volume_quartile"])
    save_table(result, PATHS.tables / "Supplementary_Table_16_Case_mix_by_prior_volume_quartile_v286")
    del data
    collect_memory()
    return str(PATHS.tables / "Supplementary_Table_16_Case_mix_by_prior_volume_quartile_v286.csv")


def _age_sex_standardization_country_v286(country: str) -> pd.DataFrame:
    data = pd.read_parquet(
        cohort_path_v280(country),
        columns=["year", "age_band_common", "sex", "death_in_hospital", "hospital_id"],
    )
    data["death_in_hospital"] = pd.to_numeric(data["death_in_hospital"], errors="coerce")
    data = data.dropna(subset=["year", "age_band_common", "sex", "death_in_hospital"])
    data = data[data["death_in_hospital"].isin([0, 1])].reset_index(drop=True)
    data = _patsy_safe_v286(
        data,
        categorical=["age_band_common", "sex", "hospital_id"],
        numeric=["death_in_hospital", "year"],
    )
    data["year"] = pd.to_numeric(data["year"], errors="coerce").astype(int)
    formula = "death_in_hospital ~ C(year) + C(age_band_common) + C(sex)"
    model = smf.glm(formula, data=data, family=sm.families.Binomial(), missing="drop")
    if country in VOLUME_COUNTRIES and data["hospital_id"].notna().all() and data["hospital_id"].nunique() >= 2:
        labels = list(model.data.row_labels)
        groups = data.loc[labels, "hospital_id"].astype(str).to_numpy()
        fit = model.fit(cov_type="cluster", cov_kwds={"groups": groups}, maxiter=150)
    else:
        fit = model.fit(cov_type="HC1", maxiter=150)

    covariance, _ = _nearest_psd_v286(np.asarray(fit.cov_params(), dtype=float))
    beta = np.asarray(fit.params, dtype=float)
    design_info = fit.model.data.design_info
    reference = data[data["year"].eq(2019)].groupby(
        ["age_band_common", "sex"], observed=True
    ).size().rename("weight_n").reset_index()
    if reference.empty:
        raise RuntimeError(f"{country}: empty 2019 age-sex standard population")
    reference["weight"] = reference["weight_n"] / reference["weight_n"].sum()

    cache: Dict[int, Dict[str, Any]] = {}
    for year in sorted(data["year"].unique()):
        new_data = reference[["age_band_common", "sex", "weight"]].copy()
        new_data["year"] = int(year)
        design = np.asarray(
            build_design_matrices([design_info], new_data, return_type="dataframe")[0],
            dtype=float,
        )
        p = 1.0 / (1.0 + np.exp(-np.clip(design @ beta, -35, 35)))
        w = new_data["weight"].to_numpy(dtype=float)
        risk = float(np.sum(w * p))
        gradient = np.sum((w * p * (1 - p))[:, None] * design, axis=0)
        cache[int(year)] = {"risk": risk, "gradient": gradient}

    ref = cache[2019]
    rows: List[Dict[str, Any]] = []
    for year, pred in cache.items():
        gradient = pred["gradient"]
        risk = pred["risk"]
        se = math.sqrt(max(0.0, float(gradient @ covariance @ gradient)))
        diff = risk - ref["risk"]
        diff_gradient = gradient - ref["gradient"]
        diff_se = math.sqrt(max(0.0, float(diff_gradient @ covariance @ diff_gradient)))
        rows.append({
            "country": country,
            "year": int(year),
            "standard_population": "Country-specific 2019 age-sex distribution",
            "admissions": int((data["year"] == year).sum()),
            "crude_mortality_pct": 100 * float(data.loc[data["year"].eq(year), "death_in_hospital"].mean()),
            "age_sex_standardized_mortality_pct": 100 * risk,
            "standardized_ci_low_pct": 100 * max(0.0, risk - 1.96 * se),
            "standardized_ci_high_pct": 100 * min(1.0, risk + 1.96 * se),
            "risk_difference_vs_2019_pp": 100 * diff,
            "risk_difference_ci_low_pp": 100 * (diff - 1.96 * diff_se),
            "risk_difference_ci_high_pp": 100 * (diff + 1.96 * diff_se),
            "model_n": int(fit.nobs),
        })
    del data, fit, model
    collect_memory()
    return pd.DataFrame(rows)


def run_age_sex_standardized_mortality_v286(
    base_dir: Path | str = DEFAULT_BASE_V286,
) -> str:
    _activate_v286(base_dir)
    frames: List[pd.DataFrame] = []
    for country in COUNTRY_ORDER:
        _log(f"v2.8.6 age-sex mortality standardization: {country}")
        frames.append(_age_sex_standardization_country_v286(country))
    result = pd.concat(frames, ignore_index=True, sort=False)
    broad_path = PATHS.tables / "Table_10_Case_mix_standardized_mortality_v285.csv"
    if broad_path.exists():
        broad = pd.read_csv(broad_path)[["country", "year", "standardized_mortality_pct", "risk_difference_vs_2019_pp"]]
        broad = broad.rename(columns={
            "standardized_mortality_pct": "age_sex_phenotype_standardized_mortality_pct",
            "risk_difference_vs_2019_pp": "age_sex_phenotype_risk_difference_vs_2019_pp",
        })
        result = result.merge(broad, on=["country", "year"], how="left", validate="one_to_one")
        result["age_sex_minus_age_sex_phenotype_pp"] = (
            result["age_sex_standardized_mortality_pct"]
            - result["age_sex_phenotype_standardized_mortality_pct"]
        )
    save_table(result, PATHS.tables / "Supplementary_Table_17_Age_sex_standardized_mortality_v286")
    return str(PATHS.tables / "Supplementary_Table_17_Age_sex_standardized_mortality_v286.csv")


def run_overdispersion_funnel_v286(
    base_dir: Path | str = DEFAULT_BASE_V286,
    minimum_expected_deaths: float = 5.0,
) -> Dict[str, str]:
    _activate_v286(base_dir)
    diagnostic_rows: List[Dict[str, Any]] = []
    fig, axes = plt.subplots(1, 2, figsize=(13, 5.7), sharey=True)
    for ax, country in zip(axes, VOLUME_COUNTRIES):
        path = PATHS.data / f"hospital_risk_standardized_{country}_v280.parquet"
        data = pd.read_parquet(
            path,
            columns=["observed_deaths", "expected_deaths", "smr", "risk_standardized_mortality"],
        )
        for col in ["observed_deaths", "expected_deaths", "smr", "risk_standardized_mortality"]:
            data[col] = pd.to_numeric(data[col], errors="coerce")
        data = data.dropna(subset=["observed_deaths", "expected_deaths", "smr"])
        data = data[data["expected_deaths"].ge(minimum_expected_deaths)].copy()
        if data.empty:
            raise RuntimeError(f"{country}: no hospitals meet expected-death threshold")
        pearson = ((data["observed_deaths"] - data["expected_deaths"]) ** 2 / data["expected_deaths"]).sum()
        phi = max(1.0, float(pearson / max(1, len(data) - 1)))
        grid = np.geomspace(float(data["expected_deaths"].min()), float(data["expected_deaths"].max()), 250)
        ax.scatter(data["expected_deaths"], data["smr"], s=13, alpha=0.35)
        for z, linestyle, label in [(1.959964, "--", "95% limits"), (3.090232, ":", "99.8% limits")]:
            half = z * np.sqrt(phi / grid)
            lower = np.maximum(0.0, 1.0 - half)
            upper = 1.0 + half
            ax.plot(grid, lower, linestyle=linestyle, color="black", linewidth=1, label=label)
            ax.plot(grid, upper, linestyle=linestyle, color="black", linewidth=1)
        ax.axhline(1.0, color="black", linewidth=1)
        ax.set_xscale("log")
        upper_display = max(2.0, float(data["smr"].quantile(0.995)) * 1.10)
        ax.set_ylim(0, upper_display)
        ax.set_xlabel("Expected in-hospital deaths (log scale)")
        ax.set_title(f"{COUNTRY_DISPLAY[country]}\nquasi-Poisson dispersion = {phi:.2f}")
        ax.grid(alpha=0.15)
        diagnostic_rows.append({
            "country": country,
            "minimum_expected_deaths": minimum_expected_deaths,
            "eligible_hospitals": int(len(data)),
            "quasi_poisson_dispersion": phi,
            "median_smr": float(data["smr"].median()),
            "smr_p95": float(data["smr"].quantile(0.95)),
            "smr_p995": float(data["smr"].quantile(0.995)),
        })
    axes[0].set_ylabel("Observed-to-expected mortality ratio")
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles[:2], labels[:2], loc="lower center", ncol=2, frameon=False)
    fig.suptitle("Hospital mortality funnel plots with overdispersion adjustment", fontsize=14, fontweight="bold")
    fig.subplots_adjust(bottom=0.18)
    save_figure(fig, PATHS.figures_supp / "Supplementary_Figure_4_Overdispersion_adjusted_funnel_v286")
    diagnostics = pd.DataFrame(diagnostic_rows)
    save_table(diagnostics, PATHS.qc / "Funnel_overdispersion_diagnostics_v286")
    return {
        "figure": str(PATHS.figures_supp / "Supplementary_Figure_4_Overdispersion_adjusted_funnel_v286.png"),
        "diagnostics": str(PATHS.qc / "Funnel_overdispersion_diagnostics_v286.csv"),
    }


def _write_sap_amendment_v286() -> str:
    text = f"""# Statistical analysis plan amendment — {V286_VERSION}

Date generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

## Status
This final amendment does not alter the frozen v2.8.4 primary cohort, outcome,
exposure, or primary hospital-volume model. It is restricted to diagnostic,
interpretive, geographic-confounding, coding-robustness, and figure-repair analyses.

## Added analyses
1. Repaired spline confidence intervals after numerical non-positive-semidefiniteness
   of the cluster-robust covariance matrix produced zero-width intervals in v2.8.4.
2. Hybrid decomposition of prior-year volume into between-hospital mean volume and
   within-hospital deviations from each hospital's own mean.
3. Country-specific region-adjusted prior-year volume sensitivity.
4. Case-mix characteristics across country-year prior-volume quartiles.
5. Age-sex-only direct mortality standardization to test dependence on administrative
   phenotype coding.
6. Overdispersion-adjusted funnel plots restricted to hospitals with at least five
   expected deaths; these remain descriptive and are not quality rankings.

## Interpretation safeguards
- Between-hospital volume coefficients can reflect referral, selective triage,
  unmeasured injury severity, and hospital role.
- Within-hospital estimates are less vulnerable to stable hospital confounding but
  remain observational and noncausal.
- Funnel plots and risk-standardized mortality must not be used to publicly rank
  individual hospitals because physiologic and imaging severity are unavailable.
- No additional subgroup fishing is planned after this amendment.
"""
    target = PATHS.manuscript / "Statistical_analysis_plan_amendment_v286.md"
    target.write_text(text, encoding="utf-8")
    return str(target)


def verify_tce_final_robustness_v286(base_dir: Path | str = DEFAULT_BASE_V286) -> Dict[str, Any]:
    _activate_v286(base_dir)
    checks = {
        "prepared_brazil": cohort_path_v280("brasil").exists(),
        "prepared_mexico": cohort_path_v280("mexico").exists(),
        "prepared_chile": cohort_path_v280("chile").exists(),
        "prepared_ecuador": cohort_path_v280("equador").exists(),
        "primary_volume_results": (PATHS.tables / "Table_6_Final_hospital_volume_models.csv").exists(),
        "v285_manifest": (PATHS.manuscript / "methodological_enhancement_manifest_v2851.json").exists(),
    }
    result = {
        "version": V286_VERSION,
        "all_ready": bool(all(checks.values())),
        "checks": checks,
        "primary_analysis_changed": False,
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return result


def run_tce_final_robustness_v286(
    base_dir: Path | str = DEFAULT_BASE_V286,
    skip_existing: bool = True,
) -> Dict[str, Any]:
    _activate_v286(base_dir)
    _log(f"Starting {V286_VERSION}")
    outputs: Dict[str, Any] = {}

    tasks = [
        (
            "corrected_splines",
            PATHS.tables / "Table_7_Adjusted_volume_spline_predictions_v286.csv",
            lambda: run_corrected_splines_v286(base_dir),
        ),
        (
            "between_within_region",
            PATHS.tables / "Table_12_Between_within_volume_decomposition_v286.csv",
            lambda: run_between_within_and_region_models_v286(base_dir),
        ),
        (
            "case_mix_by_volume",
            PATHS.tables / "Supplementary_Table_16_Case_mix_by_prior_volume_quartile_v286.csv",
            lambda: run_case_mix_by_volume_v286(base_dir),
        ),
        (
            "age_sex_standardization",
            PATHS.tables / "Supplementary_Table_17_Age_sex_standardized_mortality_v286.csv",
            lambda: run_age_sex_standardized_mortality_v286(base_dir),
        ),
        (
            "overdispersion_funnel",
            PATHS.qc / "Funnel_overdispersion_diagnostics_v286.csv",
            lambda: run_overdispersion_funnel_v286(base_dir),
        ),
    ]
    for name, checkpoint, fn in tasks:
        if skip_existing and checkpoint.exists():
            _log(f"v2.8.6 checkpoint reused: {name}")
            outputs[name] = str(checkpoint)
        else:
            _log(f"v2.8.6 running: {name}")
            outputs[name] = fn()
    outputs["sap_amendment"] = _write_sap_amendment_v286()

    manifest = {
        "version": V286_VERSION,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "base_output": str(PATHS.output),
        "primary_analysis_changed": False,
        "skip_existing": bool(skip_existing),
        "outputs": outputs,
    }
    manifest_path = PATHS.manuscript / "final_robustness_manifest_v286.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    outputs["manifest"] = str(manifest_path)
    _log(f"Completed {V286_VERSION}")
    return outputs


if __name__ == "__main__":
    print(
        "Load the v2.8.4 master and v2.8.5.1 hotfix first, then run "
        "verify_tce_final_robustness_v286() and run_tce_final_robustness_v286()."
    )
