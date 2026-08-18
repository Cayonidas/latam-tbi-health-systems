"""
TCE LATAM v2.8.5 — methodological enhancement patch

Run AFTER:
    %run /content/tce_master_v2_8_4_source_locked_final.py

This patch does not replace or alter the frozen v2.8.4 primary analysis.
It adds prespecified robustness and interpretability analyses before manuscript drafting:
1) hospital-ID continuity/source-boundary audit;
2) broad administrative phenotype coding-drift audit;
3) country-specific case-mix standardized mortality with absolute risk differences;
4) absolute volume contrasts (10th vs 90th percentile) from nonlinear models;
5) hospital-continuity and Mexico source-boundary volume sensitivities;
6) temporal holdout validation of mortality risk models;
7) geriatric-shift descriptive analysis.

Outputs are written to analysis_v284_final with v285 suffixes.
"""

import json
import math
import time
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

ENHANCEMENT_VERSION = "2.8.5.1-methodological-enhancement-dtype-hotfix"


def _require_v284_environment_v285() -> None:
    required = [
        "activate_v280", "cohort_path_v280", "load_cohort_columns_v280",
        "_prepare_volume_data_v280", "save_table", "collect_memory", "_log",
        "PATHS", "COUNTRY_ORDER", "VOLUME_COUNTRIES", "COUNTRY_DISPLAY",
        "sm", "smf", "build_design_matrices", "chi2",
        "resume_final_analysis_v284",
    ]
    missing = [name for name in required if name not in globals()]
    if missing:
        raise RuntimeError(
            "Load tce_master_v2_8_4_source_locked_final.py before this patch. "
            f"Missing symbols: {missing}"
        )


def _expit_v285(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(np.asarray(x, dtype=float), -35, 35)))


def _broad_phenotype_v285(series: pd.Series) -> pd.Series:
    text = series.astype("string")
    mapping = {
        "CONCUSSION": "CONCUSSION",
        "EPIDURAL_HEMORRHAGE": "EXTRA_AXIAL_HEMORRHAGE",
        "SUBDURAL_HEMORRHAGE": "EXTRA_AXIAL_HEMORRHAGE",
        "TRAUMATIC_SUBARACHNOID_HEMORRHAGE": "TRAUMATIC_SAH",
        "TRAUMATIC_CEREBRAL_EDEMA": "PARENCHYMAL_OR_SEVERE_PROXY",
        "DIFFUSE_BRAIN_INJURY": "PARENCHYMAL_OR_SEVERE_PROXY",
        "FOCAL_BRAIN_INJURY": "PARENCHYMAL_OR_SEVERE_PROXY",
        "INTRACRANIAL_INJURY_WITH_PROLONGED_COMA": "PARENCHYMAL_OR_SEVERE_PROXY",
        "OTHER_INTRACRANIAL_INJURY": "OTHER_OR_UNSPECIFIED",
        "UNSPECIFIED_INTRACRANIAL_INJURY": "OTHER_OR_UNSPECIFIED",
        "OTHER_OR_UNSPECIFIED": "OTHER_OR_UNSPECIFIED",
    }
    return text.map(mapping).fillna("OTHER_OR_UNSPECIFIED").astype("string")


def _patsy_safe_frame_v285(
    data: pd.DataFrame,
    *,
    categorical: Sequence[str] = (),
    numeric: Sequence[str] = (),
    integer: Sequence[str] = (),
) -> pd.DataFrame:
    """Return a formula-safe copy without pandas extension dtypes.

    Patsy/statsmodels may fail on pandas nullable dtypes such as Int64Dtype,
    BooleanDtype, and StringDtype. Required missing values must be removed
    before this helper is called. Categorical values are converted to plain
    Python strings stored in object dtype, continuous variables to float64,
    and complete integer variables (for example year) to NumPy int64.
    """
    out = data.copy()
    for column in categorical:
        if column not in out.columns:
            continue
        source = out[column]
        out[column] = source.map(
            lambda value: str(value) if pd.notna(value) else np.nan
        ).astype(object)
    for column in numeric:
        if column not in out.columns:
            continue
        out[column] = pd.to_numeric(out[column], errors="coerce").astype("float64")
    for column in integer:
        if column not in out.columns:
            continue
        converted = pd.to_numeric(out[column], errors="coerce")
        if converted.isna().any():
            raise ValueError(
                f"{column}: missing value remained before conversion to formula-safe int64"
            )
        out[column] = converted.astype("int64")
    return out


def _auc_rank_v285(y: pd.Series, score: pd.Series) -> float:
    yv = pd.to_numeric(y, errors="coerce").to_numpy(dtype=float)
    sv = pd.to_numeric(score, errors="coerce").to_numpy(dtype=float)
    valid = np.isfinite(yv) & np.isfinite(sv) & np.isin(yv, [0, 1])
    yv, sv = yv[valid], sv[valid]
    n1, n0 = int((yv == 1).sum()), int((yv == 0).sum())
    if n1 == 0 or n0 == 0:
        return np.nan
    ranks = pd.Series(sv).rank(method="average").to_numpy()
    return float((ranks[yv == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def _calibration_metrics_v285(y: pd.Series, p: pd.Series) -> Dict[str, float]:
    yv = pd.to_numeric(y, errors="coerce").astype(float)
    pv = pd.to_numeric(p, errors="coerce").astype(float).clip(1e-6, 1 - 1e-6)
    valid = yv.isin([0, 1]) & pv.notna()
    yv, pv = yv.loc[valid], pv.loc[valid]
    if len(yv) == 0:
        return {
            "auc": np.nan, "brier": np.nan,
            "calibration_intercept": np.nan, "calibration_slope": np.nan,
        }
    logit = np.log(pv / (1 - pv))
    try:
        intercept_fit = sm.GLM(
            yv, np.ones((len(yv), 1)), family=sm.families.Binomial(), offset=logit
        ).fit()
        intercept = float(intercept_fit.params[0])
    except Exception:
        intercept = np.nan
    try:
        slope_fit = sm.GLM(yv, sm.add_constant(logit), family=sm.families.Binomial()).fit()
        slope = float(np.asarray(slope_fit.params)[-1])
    except Exception:
        slope = np.nan
    return {
        "auc": _auc_rank_v285(yv, pv),
        "brier": float(np.mean((yv.to_numpy() - pv.to_numpy()) ** 2)),
        "calibration_intercept": intercept,
        "calibration_slope": slope,
    }


def audit_hospital_id_continuity_v285(base_dir: Path | str = DEFAULT_BASE) -> Dict[str, str]:
    activate_v280(base_dir)
    path = PATHS.data / "hospital_year_v280.parquet"
    hy = pd.read_parquet(path, columns=["country", "hospital_id", "year", "hospital_volume_year", "lag_volume"])
    hy["year"] = pd.to_numeric(hy["year"], errors="coerce").astype("Int64")

    transition_rows: List[Dict[str, Any]] = []
    history_rows: List[Dict[str, Any]] = []
    for country in VOLUME_COUNTRIES:
        sub = hy[hy["country"].eq(country)].copy()
        years = sorted(sub["year"].dropna().astype(int).unique())
        previous_ids: Optional[set[str]] = None
        previous_year: Optional[int] = None
        for year in years:
            current_ids = set(sub.loc[sub["year"].eq(year), "hospital_id"].dropna().astype(str))
            if previous_ids is None:
                continuing = set()
                new_ids = current_ids
                exited = set()
                retention = np.nan
                current_with_prior = np.nan
            else:
                continuing = current_ids & previous_ids
                new_ids = current_ids - previous_ids
                exited = previous_ids - current_ids
                retention = len(continuing) / len(previous_ids) if previous_ids else np.nan
                current_with_prior = len(continuing) / len(current_ids) if current_ids else np.nan
            transition_rows.append({
                "country": country,
                "previous_year": previous_year,
                "year": year,
                "current_hospitals": len(current_ids),
                "previous_hospitals": len(previous_ids) if previous_ids is not None else np.nan,
                "continuing_hospitals": len(continuing),
                "new_hospital_ids": len(new_ids),
                "exited_hospital_ids": len(exited),
                "previous_year_retention_pct": 100 * retention if np.isfinite(retention) else np.nan,
                "current_hospitals_with_prior_year_pct": 100 * current_with_prior if np.isfinite(current_with_prior) else np.nan,
                "source_boundary_flag": bool(country == "mexico" and year == 2018),
            })
            previous_ids, previous_year = current_ids, year

        for hospital_id, group in sub.groupby("hospital_id", observed=True):
            hyears = sorted(group["year"].dropna().astype(int).unique())
            diffs = np.diff(hyears) if len(hyears) > 1 else np.array([], dtype=int)
            contiguous_pairs = int((diffs == 1).sum())
            history_rows.append({
                "country": country,
                "hospital_id": hospital_id,
                "first_year": min(hyears) if hyears else np.nan,
                "last_year": max(hyears) if hyears else np.nan,
                "observed_years": len(hyears),
                "contiguous_pairs": contiguous_pairs,
                "has_gap": bool((diffs > 1).any()) if len(diffs) else False,
                "median_annual_volume": float(pd.to_numeric(group["hospital_volume_year"], errors="coerce").median()),
                "continuity_eligible_v285": bool(len(hyears) >= 4 and contiguous_pairs >= 3),
            })

    transitions = pd.DataFrame(transition_rows)
    history = pd.DataFrame(history_rows)
    summary = history.groupby("country", observed=True).agg(
        hospitals=("hospital_id", "size"),
        one_year_only=("observed_years", lambda x: int((x == 1).sum())),
        four_or_more_years=("observed_years", lambda x: int((x >= 4).sum())),
        continuity_eligible=("continuity_eligible_v285", "sum"),
        hospitals_with_gaps=("has_gap", "sum"),
        median_observed_years=("observed_years", "median"),
    ).reset_index()
    for col in ["one_year_only", "four_or_more_years", "continuity_eligible", "hospitals_with_gaps"]:
        summary[f"{col}_pct"] = 100 * summary[col] / summary["hospitals"]

    save_table(transitions, PATHS.qc / "Hospital_ID_year_transition_audit_v285")
    save_table(history, PATHS.qc / "Hospital_ID_history_v285")
    save_table(summary, PATHS.qc / "Hospital_ID_continuity_summary_v285")
    del hy
    collect_memory()
    return {
        "transitions": str(PATHS.qc / "Hospital_ID_year_transition_audit_v285.csv"),
        "history": str(PATHS.qc / "Hospital_ID_history_v285.csv"),
        "summary": str(PATHS.qc / "Hospital_ID_continuity_summary_v285.csv"),
    }


def run_coding_drift_v285(base_dir: Path | str = DEFAULT_BASE) -> Dict[str, str]:
    activate_v280(base_dir)
    data = load_cohort_columns_v280(COUNTRY_ORDER, ["country", "year", "trauma_subtype"])
    data["broad_phenotype"] = _broad_phenotype_v285(data["trauma_subtype"])
    counts = data.groupby(["country", "year", "broad_phenotype"], observed=True).size().rename("admissions").reset_index()
    counts["country_year_total"] = counts.groupby(["country", "year"], observed=True)["admissions"].transform("sum")
    counts["share_pct"] = 100 * counts["admissions"] / counts["country_year_total"]

    drift_rows: List[Dict[str, Any]] = []
    levels = sorted(counts["broad_phenotype"].astype(str).unique())
    epsilon = 1e-12
    for country, csub in counts.groupby("country", observed=True):
        reference = csub[csub["year"].eq(2019)].set_index("broad_phenotype")["share_pct"].reindex(levels, fill_value=0).to_numpy(dtype=float)
        reference = reference / max(reference.sum(), epsilon)
        for year, ysub in csub.groupby("year", observed=True):
            current = ysub.set_index("broad_phenotype")["share_pct"].reindex(levels, fill_value=0).to_numpy(dtype=float)
            current = current / max(current.sum(), epsilon)
            midpoint = 0.5 * (reference + current)
            kl1 = float(np.sum(np.where(reference > 0, reference * np.log((reference + epsilon) / (midpoint + epsilon)), 0)))
            kl2 = float(np.sum(np.where(current > 0, current * np.log((current + epsilon) / (midpoint + epsilon)), 0)))
            js = 0.5 * (kl1 + kl2)
            unspecified_share = float(current[levels.index("OTHER_OR_UNSPECIFIED")]) if "OTHER_OR_UNSPECIFIED" in levels else np.nan
            drift_rows.append({
                "country": country,
                "year": int(year),
                "reference_year": 2019,
                "jensen_shannon_divergence_vs_2019": js,
                "other_or_unspecified_share_pct": 100 * unspecified_share if np.isfinite(unspecified_share) else np.nan,
                "coding_drift_flag": bool(js >= 0.05),
            })
    drift = pd.DataFrame(drift_rows)
    save_table(counts, PATHS.qc / "Broad_phenotype_distribution_v285")
    save_table(drift, PATHS.qc / "Broad_phenotype_coding_drift_v285")
    del data
    collect_memory()
    return {
        "distribution": str(PATHS.qc / "Broad_phenotype_distribution_v285.csv"),
        "drift": str(PATHS.qc / "Broad_phenotype_coding_drift_v285.csv"),
    }


def _fit_country_standardization_v285(country: str) -> pd.DataFrame:
    columns = ["year", "age_band_common", "sex", "trauma_subtype", "death_in_hospital", "hospital_id"]
    data = pd.read_parquet(cohort_path_v280(country), columns=columns)
    data["death_in_hospital"] = pd.to_numeric(data["death_in_hospital"], errors="coerce")
    data["broad_phenotype"] = _broad_phenotype_v285(data["trauma_subtype"])
    required = ["year", "age_band_common", "sex", "broad_phenotype", "death_in_hospital"]
    data = data.dropna(subset=required)
    data = data[data["death_in_hospital"].isin([0, 1])].reset_index(drop=True)
    data = _patsy_safe_frame_v285(
        data,
        categorical=["age_band_common", "sex", "broad_phenotype"],
        numeric=["death_in_hospital"],
        integer=["year"],
    )
    formula = (
        "death_in_hospital ~ C(year) + C(age_band_common) + C(sex) + C(broad_phenotype)"
    )
    model = smf.glm(formula=formula, data=data, family=sm.families.Binomial(), missing="drop")
    fit_kwargs: Dict[str, Any] = {"maxiter": 150}
    if country in VOLUME_COUNTRIES and data["hospital_id"].notna().all() and data["hospital_id"].nunique() >= 2:
        fit_kwargs.update({"cov_type": "cluster", "cov_kwds": {"groups": data["hospital_id"].astype(str).to_numpy()}})
    else:
        fit_kwargs.update({"cov_type": "HC1"})
    fit = model.fit(**fit_kwargs)

    reference = data[data["year"].eq(2019)].groupby(
        ["age_band_common", "sex", "broad_phenotype"], observed=True
    ).size().rename("weight_n").reset_index()
    if reference.empty:
        raise RuntimeError(f"{country}: 2019 reference standardization population is empty")
    reference["weight"] = reference["weight_n"] / reference["weight_n"].sum()

    beta = np.asarray(fit.params, dtype=float)
    covariance = np.asarray(fit.cov_params(), dtype=float)
    design_info = fit.model.data.design_info
    prediction_cache: Dict[int, Dict[str, Any]] = {}
    for year in sorted(data["year"].astype(int).unique()):
        new_data = reference[["age_band_common", "sex", "broad_phenotype", "weight"]].copy()
        new_data["year"] = year
        design = np.asarray(build_design_matrices([design_info], new_data, return_type="dataframe")[0], dtype=float)
        probability = _expit_v285(design @ beta)
        weights = new_data["weight"].to_numpy(dtype=float)
        mean_probability = float(np.sum(weights * probability))
        gradient = np.sum((weights * probability * (1 - probability))[:, None] * design, axis=0)
        variance = float(gradient @ covariance @ gradient)
        prediction_cache[int(year)] = {
            "risk": mean_probability,
            "gradient": gradient,
            "se": math.sqrt(max(0.0, variance)),
        }

    ref = prediction_cache[2019]
    rows: List[Dict[str, Any]] = []
    for year, pred in prediction_cache.items():
        risk = pred["risk"]
        se = pred["se"]
        diff_grad = pred["gradient"] - ref["gradient"]
        diff = risk - ref["risk"]
        diff_se = math.sqrt(max(0.0, float(diff_grad @ covariance @ diff_grad)))
        if risk > 0 and ref["risk"] > 0:
            log_rr = math.log(risk / ref["risk"])
            rr_grad = pred["gradient"] / risk - ref["gradient"] / ref["risk"]
            rr_se = math.sqrt(max(0.0, float(rr_grad @ covariance @ rr_grad)))
            rr = math.exp(log_rr)
            rr_low, rr_high = math.exp(log_rr - 1.96 * rr_se), math.exp(log_rr + 1.96 * rr_se)
        else:
            rr = rr_low = rr_high = np.nan
        crude = float(data.loc[data["year"].eq(year), "death_in_hospital"].mean())
        rows.append({
            "country": country,
            "year": int(year),
            "standard_population": "Country-specific 2019 age-sex-broad-phenotype distribution",
            "admissions": int(data["year"].eq(year).sum()),
            "crude_mortality_pct": 100 * crude,
            "standardized_mortality_pct": 100 * risk,
            "standardized_ci_low_pct": 100 * max(0.0, risk - 1.96 * se),
            "standardized_ci_high_pct": 100 * min(1.0, risk + 1.96 * se),
            "risk_difference_vs_2019_pp": 100 * diff,
            "risk_difference_ci_low_pp": 100 * (diff - 1.96 * diff_se),
            "risk_difference_ci_high_pp": 100 * (diff + 1.96 * diff_se),
            "risk_ratio_vs_2019": rr,
            "risk_ratio_ci_low": rr_low,
            "risk_ratio_ci_high": rr_high,
            "model_n": int(fit.nobs),
        })
    del data, fit, model
    collect_memory()
    return pd.DataFrame(rows)


def run_standardized_mortality_v285(base_dir: Path | str = DEFAULT_BASE) -> str:
    activate_v280(base_dir)
    frames = []
    for country in COUNTRY_ORDER:
        _log(f"v2.8.5 standardized mortality: {country}")
        frames.append(_fit_country_standardization_v285(country))
    result = pd.concat(frames, ignore_index=True, sort=False)
    save_table(result, PATHS.tables / "Table_10_Case_mix_standardized_mortality_v285")
    return str(PATHS.tables / "Table_10_Case_mix_standardized_mortality_v285.csv")


def _absolute_prediction_contrast_v285(
    fit: Any,
    sample: pd.DataFrame,
    low_volume: float,
    high_volume: float,
) -> Dict[str, float]:
    design_info = fit.model.data.design_info
    beta = np.asarray(fit.params, dtype=float)
    covariance = np.asarray(fit.cov_params(), dtype=float)
    gradients: Dict[str, np.ndarray] = {}
    risks: Dict[str, float] = {}
    for label, volume in [("low", low_volume), ("high", high_volume)]:
        new_data = sample.copy()
        new_data["log_lag_volume"] = np.log1p(volume)
        design = np.asarray(build_design_matrices([design_info], new_data, return_type="dataframe")[0], dtype=float)
        probability = _expit_v285(design @ beta)
        risks[label] = float(np.mean(probability))
        gradients[label] = np.mean((probability * (1 - probability))[:, None] * design, axis=0)
    diff = risks["high"] - risks["low"]
    diff_grad = gradients["high"] - gradients["low"]
    diff_se = math.sqrt(max(0.0, float(diff_grad @ covariance @ diff_grad)))
    if risks["high"] > 0 and risks["low"] > 0:
        log_rr = math.log(risks["high"] / risks["low"])
        rr_grad = gradients["high"] / risks["high"] - gradients["low"] / risks["low"]
        rr_se = math.sqrt(max(0.0, float(rr_grad @ covariance @ rr_grad)))
        rr = math.exp(log_rr)
        rr_low, rr_high = math.exp(log_rr - 1.96 * rr_se), math.exp(log_rr + 1.96 * rr_se)
    else:
        rr = rr_low = rr_high = np.nan
    return {
        "predicted_low_volume_mortality_pct": 100 * risks["low"],
        "predicted_high_volume_mortality_pct": 100 * risks["high"],
        "risk_difference_high_vs_low_pp": 100 * diff,
        "risk_difference_ci_low_pp": 100 * (diff - 1.96 * diff_se),
        "risk_difference_ci_high_pp": 100 * (diff + 1.96 * diff_se),
        "risk_ratio_high_vs_low": rr,
        "risk_ratio_ci_low": rr_low,
        "risk_ratio_ci_high": rr_high,
    }


def run_volume_absolute_effects_v285(base_dir: Path | str = DEFAULT_BASE) -> str:
    activate_v280(base_dir)
    all_data = _prepare_volume_data_v280(False)
    all_data["broad_phenotype"] = _broad_phenotype_v285(all_data["trauma_subtype"])
    rows: List[Dict[str, Any]] = []
    for country in VOLUME_COUNTRIES:
        data = all_data[all_data["country"].eq(country)].copy()
        required = ["death_in_hospital", "log_lag_volume", "lag_volume", "age", "sex", "trauma_subtype", "broad_phenotype", "year", "hospital_id"]
        data = data.dropna(subset=required)
        data = data[data["death_in_hospital"].isin([0, 1])]
        data = _patsy_safe_frame_v285(
            data,
            categorical=["sex", "trauma_subtype", "broad_phenotype"],
            numeric=["death_in_hospital", "log_lag_volume", "lag_volume", "age"],
            integer=["year"],
        )
        low_volume = float(data["lag_volume"].quantile(0.10))
        high_volume = float(data["lag_volume"].quantile(0.90))
        sample = data.sample(n=min(20000, len(data)), random_state=285).copy()
        for specification, phenotype_term in [
            ("Detailed administrative TBI subtype", "C(trauma_subtype)"),
            ("Broad phenotype harmonization sensitivity", "C(broad_phenotype)"),
        ]:
            formula = (
                "death_in_hospital ~ cr(log_lag_volume, df=4) + "
                "bs(age, df=4, degree=3, include_intercept=False) + "
                f"C(sex) + {phenotype_term} + C(year)"
            )
            fit = smf.glm(formula, data=data, family=sm.families.Binomial()).fit(
                cov_type="cluster", cov_kwds={"groups": data["hospital_id"].astype(str).to_numpy()}, maxiter=150
            )
            contrast = _absolute_prediction_contrast_v285(fit, sample, low_volume, high_volume)
            rows.append({
                "country": country,
                "specification": specification,
                "low_volume_percentile": 10,
                "low_prior_year_volume": low_volume,
                "high_volume_percentile": 90,
                "high_prior_year_volume": high_volume,
                "model_n": int(fit.nobs),
                "hospitals": int(data["hospital_id"].nunique()),
                **contrast,
            })
            del fit
        del data, sample
        collect_memory()
    result = pd.DataFrame(rows)
    save_table(result, PATHS.tables / "Table_11_Absolute_volume_contrasts_v285")
    del all_data
    collect_memory()
    return str(PATHS.tables / "Table_11_Absolute_volume_contrasts_v285.csv")


def _fit_volume_sensitivity_row_v285(
    data: pd.DataFrame,
    country: str,
    analysis: str,
    phenotype: str = "detailed",
) -> Dict[str, Any]:
    working = data.copy()
    working["broad_phenotype"] = _broad_phenotype_v285(working["trauma_subtype"])
    phenotype_term = "C(trauma_subtype)" if phenotype == "detailed" else "C(broad_phenotype)"
    required = ["death_in_hospital", "lag_volume_z_country_year", "age", "sex", "year", "hospital_id"]
    required.append("trauma_subtype" if phenotype == "detailed" else "broad_phenotype")
    working = working.dropna(subset=required)
    working = working[working["death_in_hospital"].isin([0, 1])]
    working = _patsy_safe_frame_v285(
        working,
        categorical=["sex", "trauma_subtype", "broad_phenotype"],
        numeric=["death_in_hospital", "lag_volume_z_country_year", "age"],
        integer=["year"],
    )
    formula = (
        "death_in_hospital ~ lag_volume_z_country_year + "
        "bs(age, df=4, degree=3, include_intercept=False) + "
        f"C(sex) + {phenotype_term} + C(year)"
    )
    fit = smf.glm(formula, data=working, family=sm.families.Binomial()).fit(
        cov_type="cluster", cov_kwds={"groups": working["hospital_id"].astype(str).to_numpy()}, maxiter=150
    )
    term = "lag_volume_z_country_year"
    ci = fit.conf_int().loc[term]
    row = {
        "country": country,
        "analysis": analysis,
        "phenotype_specification": phenotype,
        "effect_measure": "OR per 1-SD increase in prior-year log volume",
        "estimate": float(np.exp(fit.params[term])),
        "ci_low": float(np.exp(ci.iloc[0])),
        "ci_high": float(np.exp(ci.iloc[1])),
        "p_value": float(fit.pvalues[term]),
        "n": int(fit.nobs),
        "hospitals": int(working["hospital_id"].nunique()),
    }
    del working, fit
    collect_memory()
    return row


def run_volume_boundary_and_continuity_sensitivities_v285(base_dir: Path | str = DEFAULT_BASE) -> str:
    activate_v280(base_dir)
    history_path = PATHS.qc / "Hospital_ID_history_v285.csv"
    if not history_path.exists():
        audit_hospital_id_continuity_v285(base_dir)
    history = pd.read_csv(history_path)
    all_data = _prepare_volume_data_v280(False)
    rows: List[Dict[str, Any]] = []
    for country in VOLUME_COUNTRIES:
        cdata = all_data[all_data["country"].eq(country)].copy()
        eligible_ids = set(history.loc[
            history["country"].eq(country) & history["continuity_eligible_v285"].astype(str).str.strip().str.lower().isin(["true", "1", "yes"]), "hospital_id"
        ].astype(str))
        continuity = cdata[cdata["hospital_id"].astype(str).isin(eligible_ids)].copy()
        for phenotype in ("detailed", "broad"):
            rows.append(_fit_volume_sensitivity_row_v285(
                continuity, country,
                "Hospitals with >=4 observed years and >=3 contiguous year-pairs",
                phenotype,
            ))
        if country == "mexico":
            annual_only = cdata[pd.to_numeric(cdata["year"], errors="coerce").ge(2019)].copy()
            boundary_excluded = cdata[~pd.to_numeric(cdata["year"], errors="coerce").eq(2018)].copy()
            consolidated_only = cdata[pd.to_numeric(cdata["year"], errors="coerce").between(2016, 2017)].copy()
            for label, subset in [
                ("Mexico annual-source-only lag/outcome window, 2019-2023", annual_only),
                ("Mexico excluding 2018 cross-source lag boundary", boundary_excluded),
                ("Mexico consolidated-source internal lag window, 2016-2017", consolidated_only),
            ]:
                for phenotype in ("detailed", "broad"):
                    if len(subset) >= 500:
                        rows.append(_fit_volume_sensitivity_row_v285(subset, country, label, phenotype))
            del annual_only, boundary_excluded, consolidated_only
        del cdata, continuity
        collect_memory()
    result = pd.DataFrame(rows)
    if not result.empty:
        result["fdr_q_value"] = np.nan
        for _, idx in result.groupby("country", observed=True).groups.items():
            p = result.loc[idx, "p_value"].astype(float)
            order = np.argsort(p.to_numpy())
            ranked = p.to_numpy()[order]
            m = len(ranked)
            adjusted = np.minimum.accumulate((ranked * m / np.arange(1, m + 1))[::-1])[::-1]
            adjusted = np.clip(adjusted, 0, 1)
            restored = np.empty(m)
            restored[order] = adjusted
            result.loc[idx, "fdr_q_value"] = restored
    save_table(result, PATHS.tables / "Supplementary_Table_13_Volume_continuity_and_source_boundary_v285")
    del all_data, history
    collect_memory()
    return str(PATHS.tables / "Supplementary_Table_13_Volume_continuity_and_source_boundary_v285.csv")


def run_temporal_holdout_validation_v285(base_dir: Path | str = DEFAULT_BASE) -> str:
    activate_v280(base_dir)
    rows: List[Dict[str, Any]] = []
    for country in VOLUME_COUNTRIES:
        columns = ["year", "age", "sex", "trauma_subtype", "death_in_hospital"]
        data = pd.read_parquet(cohort_path_v280(country), columns=columns)
        data["broad_phenotype"] = _broad_phenotype_v285(data["trauma_subtype"])
        data["year_centered"] = pd.to_numeric(data["year"], errors="coerce") - 2015
        data["death_in_hospital"] = pd.to_numeric(data["death_in_hospital"], errors="coerce")
        data = data.dropna(subset=["year", "age", "sex", "broad_phenotype", "death_in_hospital"])
        data = data[data["death_in_hospital"].isin([0, 1])]
        data = _patsy_safe_frame_v285(
            data,
            categorical=["sex", "broad_phenotype"],
            numeric=["death_in_hospital", "age", "year_centered"],
            integer=["year"],
        )
        train = data[data["year"].le(2020)].copy()
        test = data[data["year"].ge(2021)].copy()
        formula = (
            "death_in_hospital ~ bs(age, df=4, degree=3, include_intercept=False) + "
            "C(sex) + C(broad_phenotype) + year_centered"
        )
        fit = smf.glm(formula, data=train, family=sm.families.Binomial()).fit(maxiter=150)
        for split_name, subset in [("development_2015_2020", train), ("temporal_holdout_2021_2023", test)]:
            prediction = pd.Series(np.asarray(fit.predict(subset), dtype=float), index=subset.index)
            metrics = _calibration_metrics_v285(subset["death_in_hospital"], prediction)
            rows.append({
                "country": country,
                "split": split_name,
                "n": len(subset),
                "events": int(subset["death_in_hospital"].sum()),
                "observed_mortality_pct": 100 * float(subset["death_in_hospital"].mean()),
                "predicted_mortality_pct": 100 * float(prediction.mean()),
                **metrics,
                "formula": formula,
            })
        del data, train, test, fit
        collect_memory()
    result = pd.DataFrame(rows)
    save_table(result, PATHS.qc / "Mortality_risk_temporal_holdout_validation_v285")
    return str(PATHS.qc / "Mortality_risk_temporal_holdout_validation_v285.csv")


def run_geriatric_shift_v285(base_dir: Path | str = DEFAULT_BASE) -> str:
    activate_v280(base_dir)
    data = load_cohort_columns_v280(COUNTRY_ORDER, ["country", "year", "age_band_common", "death_in_hospital"])
    data["death_in_hospital"] = pd.to_numeric(data["death_in_hospital"], errors="coerce")
    rows: List[Dict[str, Any]] = []
    for (country, year), sub in data.groupby(["country", "year"], observed=True):
        age = sub["age_band_common"].astype("string")
        valid_death = sub["death_in_hospital"].isin([0, 1])
        older70 = age.isin(["70-79", "80+"])
        older80 = age.eq("80+")
        rows.append({
            "country": country,
            "year": int(year),
            "admissions": len(sub),
            "age_70plus_n": int(older70.sum()),
            "age_70plus_share_pct": 100 * float(older70.mean()),
            "age_80plus_n": int(older80.sum()),
            "age_80plus_share_pct": 100 * float(older80.mean()),
            "mortality_70plus_pct": 100 * float(sub.loc[older70 & valid_death, "death_in_hospital"].mean()) if (older70 & valid_death).any() else np.nan,
            "mortality_under70_pct": 100 * float(sub.loc[~older70 & valid_death, "death_in_hospital"].mean()) if (~older70 & valid_death).any() else np.nan,
        })
    result = pd.DataFrame(rows)
    save_table(result, PATHS.tables / "Supplementary_Table_14_Geriatric_shift_v285")
    del data
    collect_memory()
    return str(PATHS.tables / "Supplementary_Table_14_Geriatric_shift_v285.csv")


def write_sap_amendment_v285(base_dir: Path | str = DEFAULT_BASE) -> str:
    activate_v280(base_dir)
    text = f"""# Statistical analysis plan amendment — {ENHANCEMENT_VERSION}

Date generated: {time.strftime('%Y-%m-%d %H:%M:%S')}

## Status
This amendment is executed before the v2.8.4 final models are interpreted for manuscript claims.
It does not replace the frozen primary exposure, outcome, or primary model. The prior-year
hospital-volume model remains primary. All analyses below are secondary, sensitivity, diagnostic,
or interpretability analyses.

## Rationale
The v2.8.3 source audit expanded Mexico to 2015–2017 and Ecuador to 2015–2023. The source
transition between Mexico 2017 and 2018 and country differences in ICD-10 coding practice require
explicit robustness checks. Odds ratios alone are also difficult to interpret clinically in a cohort
exceeding one million admissions.

## Added analyses
1. Hospital identifier continuity and Mexico 2017–2018 source-boundary audit.
2. Broad administrative TBI phenotype sensitivity and coding-drift assessment.
3. Country-specific case-mix standardized mortality using each country's 2019 age-sex-phenotype
   distribution, with absolute risk differences versus 2019.
4. Standardized absolute mortality contrast between the 10th and 90th percentiles of prior-year
   hospital volume using nonlinear models.
5. Volume sensitivities restricted to hospitals with longitudinal continuity and Mexico source-
   homogeneous windows.
6. Temporal holdout validation of mortality risk models (development 2015–2020; validation 2021–2023).
7. Geriatric case-mix shift by country and year.

## Interpretation safeguards
- Cross-country mortality levels are not treated as rankings of quality because database coverage,
  referral patterns, and coding differ.
- Broad phenotype analyses test robustness to granular coding differences; they do not create a
  clinical severity score.
- Mexico source-boundary analyses are sensitivity checks, not post hoc exclusions from the primary cohort.
- Volume effects remain associational and may reflect referral of more severe cases to high-volume centers.
- Standardized absolute risks complement, but do not replace, the frozen primary odds-ratio model.
"""
    path = PATHS.manuscript / "Statistical_analysis_plan_amendment_v285.md"
    path.write_text(text, encoding="utf-8")
    return str(path)


def _existing_enhancement_outputs_v285() -> Dict[str, Any]:
    """Describe already completed enhancement artifacts for checkpoint resumption."""
    return {
        "hospital_id_continuity": {
            "transitions": str(PATHS.qc / "Hospital_ID_year_transition_audit_v285.csv"),
            "history": str(PATHS.qc / "Hospital_ID_history_v285.csv"),
            "summary": str(PATHS.qc / "Hospital_ID_continuity_summary_v285.csv"),
        },
        "coding_drift": {
            "distribution": str(PATHS.qc / "Broad_phenotype_distribution_v285.csv"),
            "drift": str(PATHS.qc / "Broad_phenotype_coding_drift_v285.csv"),
        },
        "standardized_mortality": str(PATHS.tables / "Table_10_Case_mix_standardized_mortality_v285.csv"),
        "absolute_volume_effects": str(PATHS.tables / "Table_11_Absolute_volume_contrasts_v285.csv"),
        "volume_continuity_boundary": str(PATHS.tables / "Supplementary_Table_13_Volume_continuity_and_source_boundary_v285.csv"),
        "temporal_holdout_validation": str(PATHS.qc / "Mortality_risk_temporal_holdout_validation_v285.csv"),
        "geriatric_shift": str(PATHS.tables / "Supplementary_Table_14_Geriatric_shift_v285.csv"),
        "sap_amendment": str(PATHS.manuscript / "Statistical_analysis_plan_amendment_v285.md"),
    }


def _artifact_complete_v285(value: Any) -> bool:
    if isinstance(value, Mapping):
        return bool(value) and all(Path(path).exists() for path in value.values())
    return Path(value).exists()


def run_methodological_enhancements_v285(
    base_dir: Path | str = DEFAULT_BASE,
    skip_existing: bool = True,
) -> Dict[str, Any]:
    """Run or resume only the secondary v2.8.5 enhancements.

    This function never reruns the frozen v2.8.4 primary models. With
    skip_existing=True, completed enhancement checkpoints are reused.
    """
    _require_v284_environment_v285()
    activate_v280(base_dir)
    missing = [country for country in COUNTRY_ORDER if not cohort_path_v280(country).exists()]
    if missing:
        raise FileNotFoundError(f"Missing prepared v2.8.4 cohort partitions: {missing}")

    expected = _existing_enhancement_outputs_v285()
    tasks = [
        ("hospital_id_continuity", audit_hospital_id_continuity_v285),
        ("coding_drift", run_coding_drift_v285),
        ("standardized_mortality", run_standardized_mortality_v285),
        ("absolute_volume_effects", run_volume_absolute_effects_v285),
        ("volume_continuity_boundary", run_volume_boundary_and_continuity_sensitivities_v285),
        ("temporal_holdout_validation", run_temporal_holdout_validation_v285),
        ("geriatric_shift", run_geriatric_shift_v285),
        ("sap_amendment", write_sap_amendment_v285),
    ]

    _log(f"Starting/resuming {ENHANCEMENT_VERSION}")
    outputs: Dict[str, Any] = {}
    for name, function in tasks:
        if skip_existing and _artifact_complete_v285(expected[name]):
            _log(f"v2.8.5.1 checkpoint reused: {name}")
            outputs[name] = expected[name]
            continue
        _log(f"v2.8.5.1 running: {name}")
        outputs[name] = function(base_dir)

    manifest = {
        "version": ENHANCEMENT_VERSION,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "base_output": str(PATHS.output),
        "primary_analysis_changed": False,
        "primary_models_rerun": False,
        "skip_existing": bool(skip_existing),
        "outputs": outputs,
    }
    manifest_path = PATHS.manuscript / "methodological_enhancement_manifest_v2851.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    outputs["manifest"] = str(manifest_path)
    _log(f"Completed {ENHANCEMENT_VERSION}")
    return outputs


# Preserve a reference to the source-locked v2.8.4 runner, but do not call it
# unless the user explicitly requests rerun_primary=True.
_resume_final_analysis_v284_before_v285 = resume_final_analysis_v284


def resume_final_analysis_v285(
    base_dir: Path | str = DEFAULT_BASE,
    run_models: bool = True,
    regenerate_tables: bool = True,
    regenerate_figures: bool = True,
    run_enhancements: bool = True,
    rerun_primary: bool = False,
    skip_existing_enhancements: bool = True,
) -> Dict[str, Any]:
    """Resume enhancements without repeating completed v2.8.4 models by default."""
    if rerun_primary:
        base_manifest: Dict[str, Any] = _resume_final_analysis_v284_before_v285(
            base_dir=base_dir,
            run_models=run_models,
            regenerate_tables=regenerate_tables,
            regenerate_figures=regenerate_figures,
        )
    else:
        activate_v280(base_dir)
        base_manifest = {
            "status": "SKIPPED_ALREADY_COMPLETED",
            "reason": "rerun_primary=False",
            "output_root": str(PATHS.output),
        }
    enhancement_outputs = (
        run_methodological_enhancements_v285(
            base_dir, skip_existing=skip_existing_enhancements
        )
        if run_enhancements else {}
    )
    return {
        "version": ENHANCEMENT_VERSION,
        "v284_manifest": base_manifest,
        "v285_enhancements": enhancement_outputs,
    }


def verify_tce_enhancement_v285(base_dir: Path | str = DEFAULT_BASE) -> Dict[str, Any]:
    _require_v284_environment_v285()
    activate_v280(base_dir)
    prepared = {country: cohort_path_v280(country).exists() for country in COUNTRY_ORDER}
    dtype_test = pd.DataFrame({
        "year": pd.Series([2019, 2020], dtype="Int64"),
        "outcome": pd.Series([0, 1], dtype="Int64"),
        "group": pd.Series(["A", "B"], dtype="string"),
    })
    dtype_test = _patsy_safe_frame_v285(
        dtype_test,
        categorical=["group"], numeric=["outcome"], integer=["year"],
    )
    dtype_hotfix_passed = (
        str(dtype_test["year"].dtype) == "int64"
        and str(dtype_test["outcome"].dtype) == "float64"
        and str(dtype_test["group"].dtype) == "object"
    )
    return {
        "version": ENHANCEMENT_VERSION,
        "v284_environment_loaded": True,
        "prepared_country_partitions": prepared,
        "all_prepared": all(prepared.values()),
        "dtype_hotfix_passed": bool(dtype_hotfix_passed),
        "primary_analysis_changed": False,
        "primary_models_will_rerun_by_default": False,
        "added_analysis_domains": [
            "hospital ID continuity", "coding drift", "standardized mortality",
            "absolute volume effects", "source-boundary sensitivity",
            "temporal validation", "geriatric shift",
        ],
    }


if __name__ == "__main__":
    print(
        "TCE LATAM v2.8.5.1 fixed patch loaded. Run verify_tce_enhancement_v285(), then "
        "run_methodological_enhancements_v285(skip_existing=True)."
    )
