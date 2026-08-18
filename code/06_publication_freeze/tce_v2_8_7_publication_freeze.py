from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

VERSION = "2.8.7-publication-freeze"
DEFAULT_BASE = Path("/content/drive/MyDrive/Projeto_TCE_Multinacional")


def _root(base_dir: Path | str) -> Path:
    return Path(base_dir) / "analysis_v284_final"


def _save_figure(fig: plt.Figure, stem: Path) -> None:
    stem.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(stem.with_suffix(".png"), dpi=300, bbox_inches="tight")
    fig.savefig(stem.with_suffix(".pdf"), bbox_inches="tight")
    plt.close(fig)


def _save_table(df: pd.DataFrame, stem: Path) -> None:
    stem.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(stem.with_suffix(".csv"), index=False)
    try:
        df.to_excel(stem.with_suffix(".xlsx"), index=False)
    except Exception:
        pass


def verify_tce_publication_freeze_v287(base_dir: Path | str = DEFAULT_BASE) -> Dict[str, Any]:
    root = _root(base_dir)
    required = {
        "primary_volume": root / "03_tables/Table_6_Final_hospital_volume_models.csv",
        "between_within": root / "03_tables/Table_12_Between_within_volume_decomposition_v286.csv",
        "absolute_contrasts": root / "03_tables/Table_11_Absolute_volume_contrasts_v285.csv",
        "case_mix_quartiles": root / "03_tables/Supplementary_Table_16_Case_mix_by_prior_volume_quartile_v286.csv",
        "centralization": root / "03_tables/Table_4_Centralization_metrics.csv",
        "heterogeneity": root / "03_tables/Table_8_Hospital_heterogeneity.csv",
        "standardized_mortality": root / "03_tables/Table_10_Case_mix_standardized_mortality_v285.csv",
        "holdout": root / "02_qc/Mortality_risk_temporal_holdout_validation_v285.csv",
        "v286_manifest": root / "08_manuscript_support/final_robustness_manifest_v286.json",
        "spline_diagnostics": root / "02_qc/Corrected_spline_CI_diagnostics_v286.csv",
    }
    checks = {name: path.exists() for name, path in required.items()}
    spline_retirement_required = False
    spline_reason = ""
    if checks["spline_diagnostics"]:
        diag = pd.read_csv(required["spline_diagnostics"])
        if "minimum_ci_width" in diag:
            spline_retirement_required = bool((pd.to_numeric(diag["minimum_ci_width"], errors="coerce") >= 0.99).any())
            if spline_retirement_required:
                spline_reason = "The v2.8.6 spline confidence bands span approximately 0%-100% and are non-informative."
    return {
        "version": VERSION,
        "all_required": all(checks.values()),
        "checks": checks,
        "spline_retirement_required": spline_retirement_required,
        "spline_reason": spline_reason,
        "output_root": str(root),
    }


def _between_within_figure(root: Path) -> Dict[str, str]:
    table = pd.read_csv(root / "03_tables/Table_12_Between_within_volume_decomposition_v286.csv")
    table = table[table["term"].isin(["between_volume_z", "within_volume_z"])].copy()
    label_map = {
        ("brasil", "between_volume_z"): "Brazil — between hospitals",
        ("brasil", "within_volume_z"): "Brazil — within hospital",
        ("mexico", "between_volume_z"): "Mexico — between hospitals",
        ("mexico", "within_volume_z"): "Mexico — within hospital",
    }
    table["display"] = [label_map.get((c, t), f"{c}: {t}") for c, t in zip(table["country"], table["term"])]
    order = [
        "Brazil — between hospitals", "Brazil — within hospital",
        "Mexico — between hospitals", "Mexico — within hospital",
    ]
    table["display"] = pd.Categorical(table["display"], categories=order, ordered=True)
    table = table.sort_values("display").reset_index(drop=True)

    est = pd.to_numeric(table["estimate"], errors="coerce").to_numpy(float)
    lo = pd.to_numeric(table["ci_low"], errors="coerce").to_numpy(float)
    hi = pd.to_numeric(table["ci_high"], errors="coerce").to_numpy(float)
    if not (np.isfinite(est).all() and np.isfinite(lo).all() and np.isfinite(hi).all() and (lo > 0).all()):
        raise RuntimeError("Invalid between-within estimates or confidence intervals")

    y = np.arange(len(table))[::-1]
    fig, ax = plt.subplots(figsize=(9.5, 5.3))
    ax.errorbar(est, y, xerr=[est - lo, hi - est], fmt="o", capsize=4, linewidth=1.5)
    ax.axvline(1.0, linestyle="--", linewidth=1)
    ax.set_yticks(y)
    ax.set_yticklabels(table["display"].astype(str))
    ax.set_xlabel("Adjusted odds ratio per 1-SD increase in prior-year log hospital TBI volume")
    ax.set_title("Between-hospital and within-hospital volume associations")
    ax.grid(axis="x", alpha=0.2)
    left = max(0.85, float(lo.min()) - 0.05)
    right = float(hi.max()) + 0.08
    ax.set_xlim(left, right)
    for yi, e, l, h in zip(y, est, lo, hi):
        ax.text(right - 0.01, yi, f"OR {e:.2f} ({l:.2f}–{h:.2f})", ha="right", va="center", fontsize=9)
    fig.tight_layout()
    stem = root / "04_figures_main/Figure_3_Between_within_volume_decomposition_v287"
    _save_figure(fig, stem)
    return {"png": str(stem.with_suffix('.png')), "pdf": str(stem.with_suffix('.pdf'))}


def _case_mix_figure(root: Path) -> Dict[str, str]:
    data = pd.read_csv(root / "03_tables/Supplementary_Table_16_Case_mix_by_prior_volume_quartile_v286.csv")
    data = data[data["country"].isin(["brasil", "mexico"])].copy()
    q_order = ["Q1", "Q2", "Q3", "Q4"]
    data["prior_volume_quartile"] = pd.Categorical(data["prior_volume_quartile"], q_order, ordered=True)
    data = data.sort_values(["country", "prior_volume_quartile"])

    fig, axes = plt.subplots(1, 2, figsize=(12.2, 5.2), sharey=False)
    for ax, country, title in zip(axes, ["brasil", "mexico"], ["Brazil", "Mexico"]):
        sub = data[data["country"].eq(country)].copy()
        x = np.arange(len(sub))
        ax.plot(x, sub["mortality_pct"], marker="o", label="In-hospital mortality")
        ax.plot(x, sub["structural_phenotype_pct"], marker="o", label="Structural phenotype")
        ax.plot(x, sub["unspecified_phenotype_pct"], marker="o", label="Unspecified phenotype")
        ax.set_xticks(x)
        ax.set_xticklabels(sub["prior_volume_quartile"].astype(str))
        ax.set_xlabel("Prior-year hospital-volume quartile")
        ax.set_ylabel("Admissions (%)")
        ax.set_title(title)
        ax.grid(alpha=0.2)
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=3, frameon=False)
    fig.suptitle("Observed case mix and mortality across hospital-volume quartiles", fontweight="bold")
    fig.subplots_adjust(bottom=0.20, top=0.85)
    stem = root / "05_figures_supplement/Supplementary_Figure_5_Case_mix_by_volume_v287"
    _save_figure(fig, stem)
    return {"png": str(stem.with_suffix('.png')), "pdf": str(stem.with_suffix('.pdf'))}


def _core_findings_table(root: Path) -> pd.DataFrame:
    vol = pd.read_csv(root / "03_tables/Table_6_Final_hospital_volume_models.csv")
    bw = pd.read_csv(root / "03_tables/Table_12_Between_within_volume_decomposition_v286.csv")
    absd = pd.read_csv(root / "03_tables/Table_11_Absolute_volume_contrasts_v285.csv")
    central = pd.read_csv(root / "03_tables/Table_4_Centralization_metrics.csv")
    hetero = pd.read_csv(root / "03_tables/Table_8_Hospital_heterogeneity.csv")
    std = pd.read_csv(root / "03_tables/Table_10_Case_mix_standardized_mortality_v285.csv")
    hold = pd.read_csv(root / "02_qc/Mortality_risk_temporal_holdout_validation_v285.csv")

    rows: List[Dict[str, Any]] = []
    def add(domain: str, country: str, measure: str, estimate: Any, ci: str = "", interpretation: str = "") -> None:
        rows.append({"domain": domain, "country": country, "measure": measure, "estimate": estimate, "confidence_interval": ci, "interpretation": interpretation})

    p = vol[vol["analysis"].eq("Primary mortality: prior-year hospital TBI volume")].iloc[0]
    add("Primary volume", "Brazil + Mexico", "Adjusted OR per 1-SD prior-year log volume", f"{p.estimate:.3f}", f"{p.ci_low:.3f}–{p.ci_high:.3f}", "Between-center association; not causal")
    for country in ["brasil", "mexico"]:
        display = country.title()
        b = bw[(bw.country.eq(country)) & (bw.term.eq("between_volume_z"))].iloc[0]
        w = bw[(bw.country.eq(country)) & (bw.term.eq("within_volume_z"))].iloc[0]
        add("Between-within", display, "Between-hospital OR", f"{b.estimate:.3f}", f"{b.ci_low:.3f}–{b.ci_high:.3f}", "Persistent between-center differences")
        add("Between-within", display, "Within-hospital OR", f"{w.estimate:.3f}", f"{w.ci_low:.3f}–{w.ci_high:.3f}", "Change within the same hospital")
        a = absd[(absd.country.eq(country)) & absd.specification.str.startswith("Detailed")].iloc[0]
        add("Absolute contrast", display, "P90 vs P10 adjusted mortality difference", f"{a.risk_difference_high_vs_low_pp:.2f} pp", f"{a.risk_difference_ci_low_pp:.2f}–{a.risk_difference_ci_high_pp:.2f} pp", "Model-based contrast")
        c = central[(central.country.eq(country)) & (central.year.eq(2023))].iloc[0]
        add("Centralization", display, "Top 10% hospital admission share in 2023", f"{c.top_10pct_share_pct:.1f}%", "", "Descriptive concentration")
        h = hetero[hetero.country.eq(country)].iloc[0]
        add("Heterogeneity", display, "Median rate ratio", f"{h.median_rate_ratio_approx:.2f}", "", "Residual between-hospital heterogeneity")
        hv = hold[(hold.country.eq(country)) & hold.split.str.startswith("temporal_holdout")].iloc[0]
        add("Validation", display, "Temporal holdout AUC", f"{hv.auc:.3f}", "", "Moderate discrimination; no hospital ranking")

    for country, display in [("brasil", "Brazil"), ("mexico", "Mexico"), ("chile", "Chile"), ("equador", "Ecuador")]:
        y2021 = std[(std.country.eq(country)) & (std.year.eq(2021))]
        if not y2021.empty:
            r = y2021.iloc[0]
            add("Pandemic", display, "2021 standardized mortality difference vs 2019", f"{r.risk_difference_vs_2019_pp:.2f} pp", f"{r.risk_difference_ci_low_pp:.2f}–{r.risk_difference_ci_high_pp:.2f} pp", "Country-specific 2019 standard population")
    return pd.DataFrame(rows)


def _write_freeze_note(root: Path, checks: Dict[str, Any]) -> str:
    text = f"""# Final analytic and publication freeze — {VERSION}

Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

## Analytic status
The primary v2.8.4 cohort, outcome, exposure, and models remain unchanged. The v2.8.5.1 and v2.8.6.1 analyses are retained as prespecified methodological and interpretive sensitivities.

## Main inferential message
The conventional between-center association links greater prior-year hospital TBI volume with higher in-hospital mortality. In the hybrid decomposition, the between-hospital component remains positive, whereas the within-hospital component is protective in Brazil and null in Mexico. This pattern is compatible with residual referral and severity selection and does not support a causal claim that increasing a hospital's TBI volume worsens mortality.

## Retired output
The following files must not be used in the manuscript, supplement, abstract, or presentations:
- `Table_7_Adjusted_volume_spline_predictions.csv`
- `Table_7_Adjusted_volume_spline_predictions_v286.csv`
- `Figure_3_Adjusted_volume_splines.png/pdf`
- `Figure_3_Adjusted_volume_splines_v286.png/pdf`

Reason: the v2.8.6 cluster-covariance repair produced non-informative 0%-100% confidence bands. The point estimates are not required for the central conclusions and the spline analysis is analytically retired rather than repeatedly repaired.

## Final main-figure roster
1. Cohort flow.
2. Annual mortality by country.
3. Between-hospital versus within-hospital volume decomposition (`Figure_3_Between_within_volume_decomposition_v287`).
4. Primary and sensitivity hospital-volume forest plot.
5. Centralization trends.

The original raw funnel plot should not be used. The overdispersion-adjusted funnel plot remains supplementary and descriptive only.

## Analytic stopping rule
No additional hypothesis-generating correlations should be added before manuscript submission. Further analyses should be limited to reviewer-requested checks, reproducibility verification, or correction of an identified error.

## Interpretation boundaries
- Do not describe the between-center volume association as causal.
- Do not rank hospitals or countries by quality.
- Do not label the administrative cohort as clinically confirmed severe TBI.
- Do not infer harm from decompressive surgery analyses.
- Chile and Ecuador contribute individual-level temporal analyses but not hospital-volume inference.
"""
    path = root / "08_manuscript_support/Final_analytic_and_publication_freeze_v287.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return str(path)


def run_tce_publication_freeze_v287(base_dir: Path | str = DEFAULT_BASE) -> Dict[str, Any]:
    check = verify_tce_publication_freeze_v287(base_dir)
    if not check["all_required"]:
        missing = [k for k, v in check["checks"].items() if not v]
        raise FileNotFoundError(f"Missing required outputs: {missing}")
    root = _root(base_dir)
    between_fig = _between_within_figure(root)
    case_fig = _case_mix_figure(root)
    findings = _core_findings_table(root)
    finding_stem = root / "03_tables/Table_13_Core_findings_for_manuscript_v287"
    _save_table(findings, finding_stem)
    freeze_note = _write_freeze_note(root, check)
    roster = pd.DataFrame([
        {"order": 1, "role": "main", "file": "Figure_1_Cohort_flow", "status": "USE"},
        {"order": 2, "role": "main", "file": "Figure_2_Annual_mortality", "status": "USE"},
        {"order": 3, "role": "main", "file": "Figure_3_Between_within_volume_decomposition_v287", "status": "USE"},
        {"order": 4, "role": "main", "file": "Figure_4_Hospital_volume_forest_plot", "status": "USE"},
        {"order": 5, "role": "main", "file": "Figure_6_Centralization_trends", "status": "USE"},
        {"order": 1, "role": "supplement", "file": "Supplementary_Figure_1_Annual_event_study", "status": "USE"},
        {"order": 2, "role": "supplement", "file": "Supplementary_Figure_2_Risk_standardized_mortality", "status": "USE"},
        {"order": 3, "role": "supplement", "file": "Supplementary_Figure_4_Overdispersion_adjusted_funnel_v286", "status": "USE_DESCRIPTIVE_ONLY"},
        {"order": 4, "role": "supplement", "file": "Supplementary_Figure_5_Case_mix_by_volume_v287", "status": "USE"},
        {"order": 0, "role": "retired", "file": "Figure_3_Adjusted_volume_splines_v286", "status": "DO_NOT_USE"},
        {"order": 0, "role": "retired", "file": "Figure_5_Risk_adjusted_funnel_plots", "status": "DO_NOT_USE"},
    ])
    roster_stem = root / "08_manuscript_support/Final_figure_roster_v287"
    _save_table(roster, roster_stem)
    manifest = {
        "version": VERSION,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "primary_analysis_changed": False,
        "new_inferential_models": False,
        "spline_retired": True,
        "outputs": {
            "between_within_figure": between_fig,
            "case_mix_figure": case_fig,
            "core_findings_table": str(finding_stem.with_suffix('.csv')),
            "freeze_note": freeze_note,
            "figure_roster": str(roster_stem.with_suffix('.csv')),
        },
    }
    manifest_path = root / "08_manuscript_support/publication_freeze_manifest_v287.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    manifest["manifest"] = str(manifest_path)
    return manifest


if __name__ == "__main__":
    print(verify_tce_publication_freeze_v287())
