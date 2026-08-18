from __future__ import annotations

"""
TCE Multinational Master v2.8.0 — corrected final Q1-oriented low-memory analytic suite.

Purpose
-------
Rebuild the complete analysis from validated country-level intermediate checkpoints
without loading the 123-column multinational CDM or several full-size copies into RAM.

Core design
-----------
1. Brazil and Mexico are read from existing clean intermediate Parquets.
2. Chile 2015–2023 is re-read from official annual CSVs in chunks, with grouped-age
   parsing corrected (including 85+ / 90+ and day/month infant groups).
3. Ecuador is read from its clean intermediate Parquet.
4. A lean canonical Parquet is written separately for each country.
5. Hospital-year volume is calculated only for countries with stable hospital IDs
   (Brazil and Mexico), then attached one country at a time.
6. Tables, models, and figures load only the columns needed for each task.
7. All manuscript-facing outputs and figure labels are in English.
8. The main runner returns a manifest of paths, not large DataFrames.

This is an analysis rebuild. It intentionally does not rerun raw Brazil/Mexico ingestion.
"""

import gc
import json
import math
import os
import re
import shutil
import sys
import time
import unicodedata
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

try:
    import pyarrow.parquet as pq
except Exception as exc:  # pragma: no cover - runtime guard for Colab
    raise ImportError("pyarrow is required. Install with: !pip -q install pyarrow") from exc

try:
    import matplotlib.pyplot as plt
except Exception as exc:  # pragma: no cover
    raise ImportError("matplotlib is required") from exc

try:
    import statsmodels.api as sm
    import statsmodels.formula.api as smf
except Exception as exc:  # pragma: no cover
    raise ImportError("statsmodels is required") from exc

try:
    from scipy.stats import norm
except Exception as exc:  # pragma: no cover
    raise ImportError("scipy is required") from exc


VERSION = "2.8.0"
DEFAULT_BASE = Path(os.environ.get("TCE_BASE_DIR", "/content/drive/MyDrive/Projeto_TCE_Multinacional"))
PRIMARY_YEARS = tuple(range(2015, 2024))
CHILE_SOURCE_YEARS = tuple(range(2015, 2024))
COUNTRY_ORDER = ("brasil", "mexico", "chile", "equador")
VOLUME_COUNTRIES = ("brasil", "mexico")
COUNTRY_DISPLAY = {"brasil": "Brazil", "mexico": "Mexico", "chile": "Chile", "equador": "Ecuador"}

# Primary pooled sample is harmonized at age >=20 because Chile is released in grouped ages.
# A sensitivity sample retains exact-age 18–19-year-old admissions in Brazil/Mexico/Ecuador.
PRIMARY_MIN_AGE = 20
SENSITIVITY_MIN_AGE = 18


@dataclass(frozen=True)
class Paths:
    base: Path
    raw: Path
    intermediate: Path
    output: Path
    data: Path
    qc: Path
    tables: Path
    figures_main: Path
    figures_supp: Path
    models: Path
    logs: Path
    manuscript: Path


def make_paths(base_dir: Path | str = DEFAULT_BASE) -> Paths:
    base = Path(base_dir)
    output = base / "analysis_v260"
    paths = Paths(
        base=base,
        raw=base / "00_raw",
        intermediate=base / "01_intermediate",
        output=output,
        data=output / "01_data",
        qc=output / "02_qc",
        tables=output / "03_tables",
        figures_main=output / "04_figures_main",
        figures_supp=output / "05_figures_supplement",
        models=output / "06_models",
        logs=output / "07_logs",
        manuscript=output / "08_manuscript_support",
    )
    for folder in paths.__dict__.values():
        if isinstance(folder, Path):
            folder.mkdir(parents=True, exist_ok=True)
    return paths


PATHS = make_paths()


def _log(message: str, level: str = "INFO") -> None:
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"{timestamp} | {level:<7} | {message}"
    print(line, flush=True)
    try:
        PATHS.logs.mkdir(parents=True, exist_ok=True)
        with (PATHS.logs / "pipeline_v260.log").open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except Exception:
        pass


def _rss_gb() -> float:
    try:
        import psutil
        return psutil.Process(os.getpid()).memory_info().rss / 1024**3
    except Exception:
        try:
            import resource
            value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            # Linux reports KiB.
            return float(value) / 1024**2
        except Exception:
            return float("nan")


def log_memory(stage: str) -> None:
    value = _rss_gb()
    text = f"{value:.2f} GB" if np.isfinite(value) else "unavailable"
    _log(f"[MEMORY] {stage}: RSS={text}")


def collect_memory(*objects: Any) -> None:
    # Local references are released by the caller; this function closes figures and runs GC.
    try:
        plt.close("all")
    except Exception:
        pass
    gc.collect()


def release_legacy_notebook_objects_v260() -> List[str]:
    """Delete only well-known large result variables left by previous notebook runs."""
    removed: List[str] = []
    for name in (
        "df_cdm", "df_main", "df_surg", "df_dc", "model_output", "advanced",
        "latam", "df_chile", "df_equador", "results_v14", "volume_cohort",
        "country_dfs", "hospital_year", "hy", "native",
    ):
        if name in globals():
            try:
                del globals()[name]
                removed.append(name)
            except Exception:
                pass
    collect_memory()
    _log(f"Released legacy notebook objects: {removed or 'none'}")
    return removed


def reset_analysis_v260(base_dir: Path | str = DEFAULT_BASE) -> Path:
    """Remove only the isolated v2.6 analysis directory. Raw/intermediate are untouched."""
    global PATHS
    base = Path(base_dir)
    target = base / "analysis_v260"
    if target.exists():
        shutil.rmtree(target)
    PATHS = make_paths(base)
    _log(f"Clean v2.6 output directory prepared: {target}")
    return target


# -----------------------------------------------------------------------------
# Shared data utilities
# -----------------------------------------------------------------------------

TEXT_COLUMNS = {
    "country", "record_id", "hospital_id", "hospital_region", "residence_region",
    "hospital_area", "residence_area", "sex", "dx_main", "dx_secondary",
    "trauma_subtype", "external_cause", "procedure_code_raw", "procedure_code_norm",
    "procedure_group", "procedure_mapping_confidence", "age_group_raw",
    "age_band_common", "insurance_type", "beneficiary_type", "ethnicity",
    "facility_class", "facility_type", "facility_entity", "facility_sector",
    "discharge_specialty", "nationality", "source_file", "source_dataset",
}

FLOAT_COLUMNS = {
    "age", "age_lower", "age_upper", "los_days", "icu_days", "cost_local_currency",
    "bed_total_available", "bed_icu_normal", "bed_emergency_normal",
}

INTEGER_COLUMNS = {
    "year", "month", "death_in_hospital", "icu_any", "transfer_proxy",
    "age_exact_available", "hospital_volume_eligible", "stable_hospital_id",
    "primary_acute_surgery", "any_surgical_intervention", "facility_capacity_linked",
    "primary_sample_20plus", "sensitivity_sample_18plus",
}

LEAN_COLUMNS = [
    "country", "record_id", "year", "month", "age", "age_lower", "age_upper",
    "age_exact_available", "age_group_raw", "age_band_common", "sex", "dx_main",
    "dx_secondary", "trauma_subtype", "death_in_hospital", "los_days", "hospital_id",
    "stable_hospital_id", "hospital_volume_eligible", "hospital_region",
    "residence_region", "hospital_area", "residence_area", "transfer_proxy",
    "icu_any", "icu_days", "procedure_code_raw", "procedure_code_norm",
    "procedure_group", "procedure_mapping_confidence", "primary_acute_surgery",
    "any_surgical_intervention", "external_cause", "insurance_type",
    "beneficiary_type", "ethnicity", "facility_class", "facility_type",
    "facility_entity", "facility_sector", "discharge_specialty", "nationality",
    "facility_capacity_linked", "bed_total_available", "bed_icu_normal",
    "bed_emergency_normal", "cost_local_currency", "primary_sample_20plus",
    "sensitivity_sample_18plus", "source_file", "source_dataset",
]

VOLUME_COLUMNS = [
    "hospital_volume_year", "log_volume", "volume_z_country_year",
    "volume_quartile", "volume_decile", "lag_volume", "log_lag_volume",
    "lag_volume_z_country_year",
]

PROCEDURE_MAP = {
    "0403010020": ("DECOMPRESSIVE_CODED", "HIGH", 1),
    "0403010039": ("DECOMPRESSIVE_CODED", "HIGH", 1),
    "0403010268": ("ACUTE_CRANIAL_SURGERY", "HIGH", 1),
    "0403010276": ("ACUTE_CRANIAL_SURGERY", "HIGH", 1),
    "0403010284": ("ACUTE_CRANIAL_SURGERY", "HIGH", 1),
    "0403010292": ("ACUTE_CRANIAL_SURGERY", "HIGH", 1),
    "0403010306": ("ACUTE_CRANIAL_SURGERY", "HIGH", 1),
    "0403010314": ("CHRONIC_SDH_SURGERY", "HIGH", 0),
    "0403010349": ("ICP_MONITORING_OR_TREPANATION", "HIGH", 0),
    "0415020077": ("GENERIC_NEUROSURGERY", "MODERATE", 0),
    "0415010012": ("GENERIC_MULTIPLE_PROCEDURES", "LOW", 0),
}


def parquet_columns(path: Path) -> List[str]:
    return list(pq.ParquetFile(path).schema.names)


def read_parquet_selected(path: Path, requested: Sequence[str]) -> pd.DataFrame:
    available = set(parquet_columns(path))
    selected = [column for column in requested if column in available]
    if not selected:
        raise ValueError(f"No requested columns found in {path}")
    return pd.read_parquet(path, columns=selected, engine="pyarrow")


def as_string(series: pd.Series) -> pd.Series:
    out = series.astype("string")
    return out.replace({"": pd.NA, "nan": pd.NA, "None": pd.NA, "<NA>": pd.NA, "NaT": pd.NA})


def as_float(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").astype("float64")


def as_nullable_int(series: pd.Series, column: str = "") -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce")
    values = numeric.to_numpy(dtype="float64", na_value=np.nan)
    finite = np.isfinite(values)
    rounded = np.rint(values)
    valid_integer = np.isclose(values, rounded, atol=1e-9, rtol=0, equal_nan=True)
    invalid = finite & ~valid_integer
    if invalid.any():
        _log(f"{column}: {int(invalid.sum())} non-integral value(s) converted to missing", "WARNING")
        rounded[invalid] = np.nan
    return pd.Series(pd.array(rounded, dtype="Int64"), index=series.index)


def normalize_dx(series: pd.Series) -> pd.Series:
    return (
        as_string(series)
        .str.upper()
        .str.replace(".", "", regex=False)
        .str.replace(r"\s+", "", regex=True)
    )


def classify_tbi_subtype(dx: Any) -> str:
    code = "" if pd.isna(dx) else re.sub(r"\W", "", str(dx).upper())
    mapping = {
        "S060": "CONCUSSION",
        "S061": "TRAUMATIC_CEREBRAL_EDEMA",
        "S062": "DIFFUSE_BRAIN_INJURY",
        "S063": "FOCAL_BRAIN_INJURY",
        "S064": "EPIDURAL_HEMORRHAGE",
        "S065": "SUBDURAL_HEMORRHAGE",
        "S066": "TRAUMATIC_SUBARACHNOID_HEMORRHAGE",
        "S067": "INTRACRANIAL_INJURY_WITH_PROLONGED_COMA",
        "S068": "OTHER_INTRACRANIAL_INJURY",
        "S069": "UNSPECIFIED_INTRACRANIAL_INJURY",
    }
    return mapping.get(code[:4], "OTHER_OR_UNSPECIFIED")


def exact_age_band(age: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(age, errors="coerce")
    return pd.cut(
        numeric,
        bins=[19, 29, 49, 69, 79, np.inf],
        labels=["20-29", "30-49", "50-69", "70-79", "80+"],
        right=True,
    ).astype("string")


def normalize_sex(series: pd.Series) -> pd.Series:
    text = as_string(series).str.strip().str.upper()
    result = pd.Series(pd.NA, index=series.index, dtype="string")
    male = text.isin(["1", "M", "MALE", "MASCULINO", "HOMBRE", "HOMEM"])
    female = text.isin(["2", "F", "FEMALE", "FEMENINO", "MUJER", "MULHER"])
    result.loc[male] = "Male"
    result.loc[female] = "Female"
    result.loc[text.notna() & ~(male | female)] = "Other/unknown"
    return result


def normalize_procedure_code(series: pd.Series) -> pd.Series:
    # The primary code is used when a pipe-delimited field contains multiple entries.
    first = as_string(series).str.split("|", n=1).str[0]
    digits = first.str.replace(r"\.0+$", "", regex=True).str.replace(r"\D", "", regex=True)
    return digits.where(digits.ne(""), pd.NA).str.zfill(10)


def apply_procedure_mapping(frame: pd.DataFrame) -> pd.DataFrame:
    if "procedure_code_raw" not in frame:
        frame["procedure_code_raw"] = pd.NA
    frame["procedure_code_norm"] = normalize_procedure_code(frame["procedure_code_raw"])
    frame["procedure_group"] = "UNCLASSIFIED"
    frame["procedure_mapping_confidence"] = "NA"
    frame["primary_acute_surgery"] = pd.Series(0, index=frame.index, dtype="Int64")
    for code, (group, confidence, primary) in PROCEDURE_MAP.items():
        mask = frame["procedure_code_norm"].eq(code)
        if mask.any():
            frame.loc[mask, "procedure_group"] = group
            frame.loc[mask, "procedure_mapping_confidence"] = confidence
            frame.loc[mask, "primary_acute_surgery"] = int(primary)
    return frame


def write_parquet(frame: pd.DataFrame, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False, engine="pyarrow", compression="zstd")
    return path


def save_table(frame: pd.DataFrame, stem: Path) -> Tuple[Path, Path]:
    stem.parent.mkdir(parents=True, exist_ok=True)
    csv_path = stem.with_suffix(".csv")
    xlsx_path = stem.with_suffix(".xlsx")
    frame.to_csv(csv_path, index=False, encoding="utf-8-sig")
    try:
        with pd.ExcelWriter(xlsx_path, engine="openpyxl") as writer:
            frame.to_excel(writer, sheet_name="Results", index=False)
    except Exception as exc:
        _log(f"Could not save {xlsx_path.name}: {exc}", "WARNING")
    return csv_path, xlsx_path


# -----------------------------------------------------------------------------
# Chile rebuild — chunked and corrected
# -----------------------------------------------------------------------------

CHILE_ALIASES: Dict[str, Sequence[str]] = {
    "sex": ("SEXO",),
    "age_group_raw": ("GRUPO_EDAD", "GRUPO ETARIO", "GRUPO_ETARIO"),
    "ethnicity": ("ETNIA",),
    "nationality": ("GLOSA_PAIS_ORIGEN", "PAIS_ORIGEN"),
    "residence_region": ("GLOSA_REGION_RESIDENCIA", "REGION_RESIDENCIA"),
    "residence_area": ("GLOSA_COMUNA_RESIDENCIA", "COMUNA_RESIDENCIA"),
    "insurance_type": ("GLOSA_PREVISION", "PREVISION"),
    "year": ("ANO_EGRESO", "AÑO_EGRESO"),
    "dx_main": ("DIAG1", "DIAGNOSTICO_PRINCIPAL"),
    "dx_secondary": ("DIAG2", "DIAGNOSTICO_SECUNDARIO"),
    "los_days": ("DIAS_ESTADA", "DIAS_ESTAD", "DIAS_ESTANCIA"),
    "discharge_condition": ("CONDICION_EGRESO", "COND_EGR"),
    "any_surgical_intervention": ("INTERV_Q", "GLOSA_INTERV_Q_PPAL"),
    "procedure_code_raw": ("PROCED", "GLOSA_PROCED_PPAL"),
    "source_dataset": ("PERTENENCIA_ESTABLECIMIENTO_SALUD", "PERTENENCIA_ESTABLECIMIENTO_SALU"),
}


def ascii_text(value: Any) -> str:
    if pd.isna(value):
        return ""
    text = str(value).strip().lower().replace("–", "-").replace("—", "-")
    text = "".join(char for char in unicodedata.normalize("NFKD", text) if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", text).strip()


def parse_chile_age_label(value: Any) -> Tuple[float, float, float, Optional[str], str]:
    text = ascii_text(value)
    if not text:
        return np.nan, np.nan, np.nan, None, "EMPTY"

    # Infant units must be handled before generic number ranges.
    if any(token in text for token in ("dia", "dias", "mes", "meses")):
        return 0.0, 0.0, 0.0, "<1", "INFANT_DAYS_OR_MONTHS"
    if "menor" in text:
        return 0.0, 0.0, 0.0, "<1", "INFANT_UNDER_ONE"

    # Open-ended labels: 85 A MAS, 90 Y MAS, 90+, including mojibake such as m�s.
    open_match = re.search(r"(\d{1,3})\s*(?:a|y|o)?\s*(?:mas|ms|m.s|\+)", text)
    if open_match:
        lower = float(open_match.group(1))
        if 0 <= lower <= 120:
            band = "80+" if lower >= 80 else f"{int(lower)}+"
            return lower, 110.0, lower + 5.0, band, "OPEN_ENDED"

    range_match = re.search(r"(\d{1,3})\s*(?:a|al|hasta|[-_/])\s*(\d{1,3})", text)
    if range_match:
        lower, upper = float(range_match.group(1)), float(range_match.group(2))
        if 0 <= lower <= upper <= 120:
            midpoint = (lower + upper) / 2.0
            if lower >= 80:
                band = "80+"
            elif lower >= 70:
                band = "70-79"
            elif lower >= 50:
                band = "50-69"
            elif lower >= 30:
                band = "30-49"
            elif lower >= 20:
                band = "20-29"
            else:
                band = f"{int(lower)}-{int(upper)}"
            return lower, upper, midpoint, band, "CLOSED_RANGE"

    exact_match = re.fullmatch(r"(?:edad\s*)?(\d{1,3})(?:\s*anos?)?", text)
    if exact_match:
        exact = float(exact_match.group(1))
        if 0 <= exact <= 120:
            if exact >= 80:
                band = "80+"
            elif exact >= 70:
                band = "70-79"
            elif exact >= 50:
                band = "50-69"
            elif exact >= 30:
                band = "30-49"
            elif exact >= 20:
                band = "20-29"
            else:
                band = f"{int(exact)}"
            return exact, exact, exact, band, "EXACT"

    return np.nan, np.nan, np.nan, None, f"UNPARSED:{text[:50]}"


def map_chile_death(series: pd.Series) -> pd.Series:
    text = as_string(series).map(ascii_text)
    numeric = pd.to_numeric(text, errors="coerce")
    result = pd.Series(pd.NA, index=series.index, dtype="Int64")
    result.loc[numeric.eq(1)] = 0
    result.loc[numeric.eq(2)] = 1
    result.loc[text.str.contains(r"falle|muert|defun|death", na=False)] = 1
    result.loc[text.str.contains(r"vivo|alta", na=False)] = 0
    return result


def map_chile_surgery(series: pd.Series) -> pd.Series:
    text = as_string(series).map(ascii_text)
    numeric = pd.to_numeric(text, errors="coerce")
    result = pd.Series(pd.NA, index=series.index, dtype="Int64")
    # For coded INTERV_Q, 1 is generally yes and 2/no is no. Textual releases are handled too.
    result.loc[numeric.eq(1)] = 1
    result.loc[numeric.eq(2)] = 0
    result.loc[text.str.contains(r"si|cirug|quirurg", na=False)] = 1
    result.loc[text.str.contains(r"no|sin", na=False)] = 0
    return result


def find_chile_annual_csv(raw_chile: Path, year: int) -> Path:
    patterns = [
        f"**/EGRESOS_{year}/EGRE_DATOS_ABIERTOS_{year}.csv",
        f"**/EGRESOS_{year}/EGR_DATOS_ABIERTO_{year}.csv",
        f"**/EGRESOS_{year}/EGRESOS_{year}.csv",
    ]
    candidates: List[Path] = []
    for pattern in patterns:
        candidates.extend(raw_chile.glob(pattern))
    candidates = [path for path in candidates if path.is_file() and path.stat().st_size > 10_000_000]
    if not candidates:
        raise FileNotFoundError(f"Chile {year}: annual patient-level CSV not found under {raw_chile}")
    return max(candidates, key=lambda path: path.stat().st_size)


def _detect_csv(path: Path) -> Tuple[str, str, List[str]]:
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with path.open("r", encoding=encoding, errors="strict") as handle:
                header = handle.readline()
            separator = ";" if header.count(";") >= header.count(",") else ","
            columns = [column.strip().lstrip("\ufeff") for column in header.rstrip("\n\r").split(separator)]
            return encoding, separator, columns
        except Exception:
            continue
    with path.open("r", encoding="latin-1", errors="replace") as handle:
        header = handle.readline()
    separator = ";" if header.count(";") >= header.count(",") else ","
    return "latin-1", separator, [column.strip().lstrip("\ufeff") for column in header.rstrip().split(separator)]


def _alias_lookup(columns: Sequence[str], aliases: Mapping[str, Sequence[str]]) -> Dict[str, str]:
    normalized = {ascii_text(column).replace(" ", "_"): column for column in columns}
    lookup: Dict[str, str] = {}
    for canonical, options in aliases.items():
        for option in options:
            key = ascii_text(option).replace(" ", "_")
            if key in normalized:
                lookup[canonical] = normalized[key]
                break
    return lookup


def rebuild_chile_intermediate_v260(base_dir: Path | str = DEFAULT_BASE, force: bool = False) -> Path:
    """Rebuild Chile 2015–2023 in chunks and write a compact corrected checkpoint."""
    global PATHS
    PATHS = make_paths(base_dir)
    output = PATHS.intermediate / "chile" / "chile_clean_v260.parquet"
    if output.exists() and not force:
        _log(f"Chile v2.6 checkpoint reused: {output}")
        return output

    output.parent.mkdir(parents=True, exist_ok=True)
    raw_chile = PATHS.raw / "chile"
    yearly_paths: List[Path] = []
    audit_rows: List[Dict[str, Any]] = []

    for year in CHILE_SOURCE_YEARS:
        source = find_chile_annual_csv(raw_chile, year)
        encoding, separator, columns = _detect_csv(source)
        lookup = _alias_lookup(columns, CHILE_ALIASES)
        required = {"sex", "age_group_raw", "year", "dx_main", "los_days", "discharge_condition"}
        missing = sorted(required - set(lookup))
        if missing:
            raise RuntimeError(f"Chile {year}: missing required columns {missing}; columns={columns}")

        usecols = sorted(set(lookup.values()))
        retained_chunks: List[pd.DataFrame] = []
        raw_rows = 0
        s06_rows = 0
        adult_rows = 0
        unparsed_adult_candidates = 0

        for chunk in pd.read_csv(
            source,
            sep=separator,
            encoding=encoding,
            encoding_errors="replace",
            dtype=str,
            usecols=usecols,
            chunksize=150_000,
            low_memory=True,
            on_bad_lines="skip",
        ):
            raw_rows += len(chunk)
            dx = normalize_dx(chunk[lookup["dx_main"]])
            mask_s06 = dx.str.startswith("S06", na=False)
            if not mask_s06.any():
                del chunk, dx
                continue
            source_subset = chunk.loc[mask_s06].copy()
            s06_rows += len(source_subset)

            age_parsed = source_subset[lookup["age_group_raw"]].map(parse_chile_age_label)
            parsed = pd.DataFrame(
                age_parsed.tolist(),
                columns=["age_lower", "age_upper", "age", "age_band_common", "age_parse_method"],
                index=source_subset.index,
            )
            adult = pd.to_numeric(parsed["age_lower"], errors="coerce").ge(PRIMARY_MIN_AGE)
            unparsed_adult_candidates += int(parsed["age_lower"].isna().sum())
            if not adult.any():
                del chunk, source_subset, parsed, dx
                continue

            source_subset = source_subset.loc[adult].copy()
            parsed = parsed.loc[adult]
            out = pd.DataFrame(index=source_subset.index)
            out["country"] = "chile"
            out["record_id"] = [f"chile-{year}-{index}" for index in source_subset.index]
            out["year"] = int(year)
            out["month"] = pd.NA
            out["age"] = parsed["age"].astype(float)
            out["age_lower"] = parsed["age_lower"].astype(float)
            out["age_upper"] = parsed["age_upper"].astype(float)
            out["age_exact_available"] = 0
            out["age_group_raw"] = as_string(source_subset[lookup["age_group_raw"]])
            out["age_band_common"] = as_string(parsed["age_band_common"])
            out["sex"] = normalize_sex(source_subset[lookup["sex"]])
            out["dx_main"] = normalize_dx(source_subset[lookup["dx_main"]])
            out["dx_secondary"] = (
                normalize_dx(source_subset[lookup["dx_secondary"]]) if "dx_secondary" in lookup else pd.NA
            )
            out["trauma_subtype"] = out["dx_main"].map(classify_tbi_subtype).astype("string")
            out["death_in_hospital"] = map_chile_death(source_subset[lookup["discharge_condition"]])
            out["los_days"] = pd.to_numeric(source_subset[lookup["los_days"]], errors="coerce")
            out["hospital_id"] = pd.NA
            out["stable_hospital_id"] = 0
            out["hospital_volume_eligible"] = 0
            out["hospital_region"] = pd.NA
            out["residence_region"] = (
                as_string(source_subset[lookup["residence_region"]]) if "residence_region" in lookup else pd.NA
            )
            out["residence_area"] = (
                as_string(source_subset[lookup["residence_area"]]) if "residence_area" in lookup else pd.NA
            )
            out["transfer_proxy"] = pd.NA
            out["icu_any"] = pd.NA
            out["icu_days"] = pd.NA
            out["procedure_code_raw"] = (
                as_string(source_subset[lookup["procedure_code_raw"]]) if "procedure_code_raw" in lookup else pd.NA
            )
            out["procedure_group"] = "UNCLASSIFIED"
            out["procedure_mapping_confidence"] = "NA"
            out["primary_acute_surgery"] = 0
            out["any_surgical_intervention"] = (
                map_chile_surgery(source_subset[lookup["any_surgical_intervention"]])
                if "any_surgical_intervention" in lookup else pd.NA
            )
            out["external_cause"] = pd.NA
            out["insurance_type"] = (
                as_string(source_subset[lookup["insurance_type"]]) if "insurance_type" in lookup else pd.NA
            )
            out["beneficiary_type"] = pd.NA
            out["ethnicity"] = as_string(source_subset[lookup["ethnicity"]]) if "ethnicity" in lookup else pd.NA
            out["nationality"] = as_string(source_subset[lookup["nationality"]]) if "nationality" in lookup else pd.NA
            out["facility_class"] = pd.NA
            out["facility_type"] = pd.NA
            out["facility_entity"] = pd.NA
            out["facility_sector"] = pd.NA
            out["discharge_specialty"] = pd.NA
            out["facility_capacity_linked"] = 0
            out["bed_total_available"] = np.nan
            out["bed_icu_normal"] = np.nan
            out["bed_emergency_normal"] = np.nan
            out["cost_local_currency"] = np.nan
            out["primary_sample_20plus"] = 1
            out["sensitivity_sample_18plus"] = 1
            out["source_file"] = str(source)
            out["source_dataset"] = (
                as_string(source_subset[lookup["source_dataset"]]) if "source_dataset" in lookup else "Chile hospital discharges"
            )
            retained_chunks.append(out.reset_index(drop=True))
            adult_rows += len(out)
            del chunk, source_subset, parsed, out, dx
            collect_memory()

        yearly = pd.concat(retained_chunks, ignore_index=True, sort=False) if retained_chunks else pd.DataFrame(columns=LEAN_COLUMNS)
        yearly_path = output.parent / f"chile_s06_{year}_v260.parquet"
        write_parquet(yearly, yearly_path)
        yearly_paths.append(yearly_path)
        audit_rows.append({
            "year": year,
            "source_file": str(source),
            "raw_rows": raw_rows,
            "s06_all_ages": s06_rows,
            "primary_adult_s06": adult_rows,
            "unparsed_age_rows_among_s06": unparsed_adult_candidates,
        })
        _log(f"Chile {year}: {adult_rows:,} primary adult S06 admissions")
        del yearly, retained_chunks
        collect_memory()

    # The concatenated Chile dataset is small (~50k records), so this final concat is safe.
    chile = pd.concat([pd.read_parquet(path) for path in yearly_paths], ignore_index=True, sort=False)
    write_parquet(chile, output)
    save_table(pd.DataFrame(audit_rows), PATHS.qc / "Chile_source_intake_audit_v260")
    _log(f"Chile corrected checkpoint: {len(chile):,} records | {output}")
    del chile
    collect_memory()
    return output


# -----------------------------------------------------------------------------
# Lean country parts
# -----------------------------------------------------------------------------


def checkpoint_map(base_dir: Path | str = DEFAULT_BASE) -> Dict[str, Path]:
    base = Path(base_dir)
    return {
        "brasil": base / "01_intermediate/brasil/brasil_clean.parquet",
        "mexico": base / "01_intermediate/mexico/mexico_clean.parquet",
        "chile": base / "01_intermediate/chile/chile_clean_v260.parquet",
        "equador": base / "01_intermediate/equador/equador_clean_v240.parquet",
    }


def normalize_country_frame(frame: pd.DataFrame, country: str) -> pd.DataFrame:
    for column in LEAN_COLUMNS:
        if column not in frame:
            frame[column] = pd.NA

    frame["country"] = country
    frame["year"] = as_nullable_int(frame["year"], "year")
    frame["month"] = as_nullable_int(frame["month"], "month")
    for column in FLOAT_COLUMNS:
        frame[column] = as_float(frame[column])
    for column in INTEGER_COLUMNS:
        frame[column] = as_nullable_int(frame[column], column)
    for column in TEXT_COLUMNS:
        frame[column] = as_string(frame[column])

    frame["dx_main"] = normalize_dx(frame["dx_main"])
    frame["dx_secondary"] = normalize_dx(frame["dx_secondary"])
    frame["trauma_subtype"] = frame["dx_main"].map(classify_tbi_subtype).astype("string")
    frame["sex"] = normalize_sex(frame["sex"])

    if country != "chile":
        frame["age_exact_available"] = frame["age"].notna().astype("Int64")
        frame["age_lower"] = frame["age"]
        frame["age_upper"] = frame["age"]
        frame["age_band_common"] = exact_age_band(frame["age"])
        frame["primary_sample_20plus"] = frame["age"].ge(PRIMARY_MIN_AGE).astype("Int64")
        frame["sensitivity_sample_18plus"] = frame["age"].ge(SENSITIVITY_MIN_AGE).astype("Int64")
    else:
        frame["primary_sample_20plus"] = pd.to_numeric(frame["age_lower"], errors="coerce").ge(PRIMARY_MIN_AGE).astype("Int64")
        frame["sensitivity_sample_18plus"] = frame["primary_sample_20plus"]

    if country in VOLUME_COUNTRIES:
        frame["hospital_id"] = as_string(frame["hospital_id"])
        valid_hospital = frame["hospital_id"].notna()
        current_ids = frame.loc[valid_hospital, "hospital_id"].astype(str)
        frame.loc[valid_hospital, "hospital_id"] = np.where(
            current_ids.str.startswith(country + ":"), current_ids, country + ":" + current_ids
        )
        frame["stable_hospital_id"] = valid_hospital.astype("Int64")
        frame["hospital_volume_eligible"] = valid_hospital.astype("Int64")
    else:
        frame["hospital_id"] = pd.Series(pd.NA, index=frame.index, dtype="string")
        frame["stable_hospital_id"] = 0
        frame["hospital_volume_eligible"] = 0

    if country == "brasil":
        frame = apply_procedure_mapping(frame)
    else:
        frame["procedure_code_norm"] = normalize_procedure_code(frame["procedure_code_raw"])
        frame["procedure_group"] = as_string(frame["procedure_group"]).fillna("UNCLASSIFIED")
        frame["procedure_mapping_confidence"] = as_string(frame["procedure_mapping_confidence"]).fillna("NA")

    frame = frame[
        frame["year"].isin(PRIMARY_YEARS)
        & frame["dx_main"].str.startswith("S06", na=False)
        & frame["sensitivity_sample_18plus"].eq(1)
    ].copy()
    return frame[LEAN_COLUMNS]


def build_lean_country_parts_v260(
    base_dir: Path | str = DEFAULT_BASE,
    rebuild_chile: bool = True,
) -> Dict[str, str]:
    global PATHS
    PATHS = make_paths(base_dir)
    if rebuild_chile:
        rebuild_chile_intermediate_v260(base_dir, force=True)
    elif not checkpoint_map(base_dir)["chile"].exists():
        rebuild_chile_intermediate_v260(base_dir, force=False)

    checkpoints = checkpoint_map(base_dir)
    outputs: Dict[str, str] = {}
    audit_rows: List[Dict[str, Any]] = []

    for country in COUNTRY_ORDER:
        source = checkpoints[country]
        if not source.exists():
            raise FileNotFoundError(f"Missing {country} checkpoint: {source}")
        requested = list(dict.fromkeys(LEAN_COLUMNS + [
            "procedure_group_v2", "procedure_mapping_confidence", "primary_acute_surgery",
            "age_band_common", "age_lower", "age_upper", "age_exact_available",
        ]))
        frame = read_parquet_selected(source, requested)
        if "procedure_group" not in frame and "procedure_group_v2" in frame:
            frame["procedure_group"] = frame["procedure_group_v2"]
        before = len(frame)
        frame = normalize_country_frame(frame, country)
        target = PATHS.data / f"lean_{country}_v260.parquet"
        write_parquet(frame, target)
        outputs[country] = str(target)
        audit_rows.append({
            "country": country,
            "source_checkpoint": str(source),
            "source_rows": before,
            "retained_adult_s06_2015_2023": len(frame),
            "primary_20plus": int(frame["primary_sample_20plus"].fillna(0).sum()),
            "years": ",".join(map(str, sorted(pd.to_numeric(frame["year"], errors="coerce").dropna().astype(int).unique()))),
            "hospital_ids": int(frame["hospital_id"].nunique(dropna=True)),
            "memory_gb_before_release": round(frame.memory_usage(deep=True).sum() / 1024**3, 3),
        })
        _log(f"Lean {country}: {len(frame):,} rows -> {target}")
        del frame
        collect_memory()
        log_memory(f"after lean {country}")

    save_table(pd.DataFrame(audit_rows), PATHS.qc / "Country_checkpoint_audit_v260")
    return outputs


# -----------------------------------------------------------------------------
# Cohort and hospital volume — one country at a time
# -----------------------------------------------------------------------------


def _zscore(series: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce").astype(float)
    valid = numeric.notna()
    result = pd.Series(np.nan, index=series.index, dtype=float)
    if not valid.any():
        return result
    sd = float(numeric.loc[valid].std(ddof=0))
    if not np.isfinite(sd) or sd <= 0:
        result.loc[valid] = 0.0
    else:
        result.loc[valid] = (numeric.loc[valid] - float(numeric.loc[valid].mean())) / sd
    return result


def _quantile_labels(series: pd.Series, q: int, prefix: str) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce")
    result = pd.Series(pd.NA, index=series.index, dtype="string")
    valid = numeric.notna()
    if valid.sum() < q or numeric.loc[valid].nunique() < 2:
        return result
    ranks = numeric.loc[valid].rank(method="first")
    result.loc[valid] = pd.qcut(ranks, q=q, labels=[f"{prefix}{i}" for i in range(1, q + 1)]).astype("string")
    return result


def build_hospital_year_v260(base_dir: Path | str = DEFAULT_BASE) -> Path:
    global PATHS
    PATHS = make_paths(base_dir)
    units: List[pd.DataFrame] = []
    for country in VOLUME_COUNTRIES:
        source = PATHS.data / f"lean_{country}_v260.parquet"
        frame = pd.read_parquet(source, columns=["country", "hospital_id", "year", "primary_sample_20plus"])
        frame = frame[frame["primary_sample_20plus"].eq(1) & frame["hospital_id"].notna()]
        grouped = (
            frame.groupby(["country", "hospital_id", "year"], observed=True)
            .size().rename("hospital_volume_year").reset_index()
        )
        units.append(grouped)
        del frame, grouped
        collect_memory()

    hy = pd.concat(units, ignore_index=True, sort=False)
    hy["hospital_volume_year"] = pd.to_numeric(hy["hospital_volume_year"], errors="coerce").astype("Int64")
    hy["log_volume"] = np.log1p(pd.to_numeric(hy["hospital_volume_year"], errors="coerce"))
    hy["volume_z_country_year"] = hy.groupby(["country", "year"], observed=True)["log_volume"].transform(_zscore)
    hy["volume_quartile"] = hy.groupby(["country", "year"], observed=True)["hospital_volume_year"].transform(
        lambda values: _quantile_labels(values, 4, "Q")
    )
    hy["volume_decile"] = hy.groupby(["country", "year"], observed=True)["hospital_volume_year"].transform(
        lambda values: _quantile_labels(values, 10, "D")
    )
    hy = hy.sort_values(["country", "hospital_id", "year"]).reset_index(drop=True)
    hy["lag_volume"] = hy.groupby(["country", "hospital_id"], observed=True)["hospital_volume_year"].shift(1)
    hy["log_lag_volume"] = np.log1p(pd.to_numeric(hy["lag_volume"], errors="coerce"))
    hy["lag_volume_z_country_year"] = hy.groupby(["country", "year"], observed=True)["log_lag_volume"].transform(_zscore)
    target = PATHS.data / "hospital_year_v260.parquet"
    write_parquet(hy, target)
    _log(f"Hospital-year table: {len(hy):,} units | {target}")
    del units, hy
    collect_memory()
    return target


def build_analysis_cohort_parts_v260(base_dir: Path | str = DEFAULT_BASE) -> Dict[str, str]:
    global PATHS
    PATHS = make_paths(base_dir)
    hy_path = build_hospital_year_v260(base_dir)
    hy = pd.read_parquet(hy_path)
    outputs: Dict[str, str] = {}

    for country in COUNTRY_ORDER:
        source = PATHS.data / f"lean_{country}_v260.parquet"
        frame = pd.read_parquet(source)
        frame = frame[frame["primary_sample_20plus"].eq(1)].copy()
        if country in VOLUME_COUNTRIES:
            country_hy = hy[hy["country"].eq(country)].copy()
            frame = frame.merge(country_hy, on=["country", "hospital_id", "year"], how="left", validate="many_to_one")
            del country_hy
        else:
            for column in VOLUME_COLUMNS:
                frame[column] = pd.NA
        target = PATHS.data / f"cohort_main_{country}_v260.parquet"
        write_parquet(frame, target)
        outputs[country] = str(target)

        if country == "brasil":
            strict = frame[frame["procedure_group"].isin(["DECOMPRESSIVE_CODED", "ACUTE_CRANIAL_SURGERY"])].copy()
            strict["procedure_class_analysis"] = np.where(strict["procedure_group"].eq("DECOMPRESSIVE_CODED"), "Decompressive-coded", "Other acute cranial surgery")
            broad = frame[frame["procedure_group"].isin([
                "DECOMPRESSIVE_CODED", "ACUTE_CRANIAL_SURGERY", "CHRONIC_SDH_SURGERY",
                "ICP_MONITORING_OR_TREPANATION", "GENERIC_NEUROSURGERY",
            ])].copy()
            write_parquet(strict, PATHS.data / "cohort_surgical_strict_brazil_v260.parquet")
            write_parquet(broad, PATHS.data / "cohort_surgical_broad_brazil_v260.parquet")
            del strict, broad

        _log(f"Primary cohort {country}: {len(frame):,} rows")
        del frame
        collect_memory()
        log_memory(f"after cohort {country}")

    del hy
    collect_memory()
    return outputs


def cohort_path(country: str) -> Path:
    return PATHS.data / f"cohort_main_{country}_v260.parquet"


def load_cohort_columns(countries: Sequence[str], columns: Sequence[str]) -> pd.DataFrame:
    frames: List[pd.DataFrame] = []
    for country in countries:
        path = cohort_path(country)
        available = set(parquet_columns(path))
        selected = [column for column in columns if column in available]
        frame = pd.read_parquet(path, columns=selected)
        for column in columns:
            if column not in frame:
                frame[column] = pd.NA
        frames.append(frame[list(columns)])
    result = pd.concat(frames, ignore_index=True, sort=False)
    del frames
    return result


# -----------------------------------------------------------------------------
# Tables
# -----------------------------------------------------------------------------


def wilson_interval(events: int, total: int, z: float = 1.96) -> Tuple[float, float]:
    if total <= 0:
        return np.nan, np.nan
    p = events / total
    denominator = 1 + z**2 / total
    center = (p + z**2 / (2 * total)) / denominator
    half = z * math.sqrt(p * (1 - p) / total + z**2 / (4 * total**2)) / denominator
    return max(0.0, center - half), min(1.0, center + half)


def table_cohort_characteristics_v260() -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    columns = ["country", "year", "age", "age_band_common", "sex", "death_in_hospital", "los_days", "hospital_id"]
    for country in COUNTRY_ORDER:
        frame = pd.read_parquet(cohort_path(country), columns=columns)
        death = pd.to_numeric(frame["death_in_hospital"], errors="coerce")
        valid_death = death.isin([0, 1])
        los = pd.to_numeric(frame["los_days"], errors="coerce")
        valid_los = los.ge(0)
        age = pd.to_numeric(frame["age"], errors="coerce")
        rows.append({
            "Country": COUNTRY_DISPLAY[country],
            "Admissions": len(frame),
            "Years": f"{int(pd.to_numeric(frame['year'], errors='coerce').min())}-{int(pd.to_numeric(frame['year'], errors='coerce').max())}",
            "Unique hospitals": int(frame["hospital_id"].nunique(dropna=True)),
            "Female, %": round(100 * frame["sex"].eq("Female").mean(), 2),
            "Age, median": round(float(age.median()), 1) if age.notna().any() else np.nan,
            "In-hospital mortality, %": round(100 * float(death[valid_death].mean()), 2) if valid_death.any() else np.nan,
            "Length of stay, median": round(float(los[valid_los].median()), 1) if valid_los.any() else np.nan,
            "Length of stay, Q1": round(float(los[valid_los].quantile(0.25)), 1) if valid_los.any() else np.nan,
            "Length of stay, Q3": round(float(los[valid_los].quantile(0.75)), 1) if valid_los.any() else np.nan,
            "Age data type": "Grouped age midpoint" if country == "chile" else "Exact age",
        })
        del frame
        collect_memory()
    return pd.DataFrame(rows)


def table_annual_outcomes_v260() -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    columns = ["country", "year", "death_in_hospital", "los_days"]
    for country in COUNTRY_ORDER:
        frame = pd.read_parquet(cohort_path(country), columns=columns)
        for year, sub in frame.groupby("year", observed=True):
            death = pd.to_numeric(sub["death_in_hospital"], errors="coerce")
            valid = death.isin([0, 1])
            events, total = int(death[valid].sum()), int(valid.sum())
            low, high = wilson_interval(events, total)
            los = pd.to_numeric(sub["los_days"], errors="coerce")
            rows.append({
                "country": country,
                "year": int(year),
                "admissions": len(sub),
                "deaths": events,
                "mortality_pct": 100 * events / total if total else np.nan,
                "mortality_ci_low_pct": 100 * low,
                "mortality_ci_high_pct": 100 * high,
                "los_median": float(los[los.ge(0)].median()) if los.ge(0).any() else np.nan,
            })
        del frame
        collect_memory()
    return pd.DataFrame(rows).sort_values(["country", "year"])


def table_subtype_outcomes_v260() -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    columns = ["country", "trauma_subtype", "death_in_hospital", "los_days"]
    for country in COUNTRY_ORDER:
        frame = pd.read_parquet(cohort_path(country), columns=columns)
        total_country = len(frame)
        for subtype, sub in frame.groupby("trauma_subtype", observed=True, dropna=False):
            death = pd.to_numeric(sub["death_in_hospital"], errors="coerce")
            valid = death.isin([0, 1])
            los = pd.to_numeric(sub["los_days"], errors="coerce")
            rows.append({
                "country": country,
                "trauma_subtype": str(subtype),
                "admissions": len(sub),
                "share_country_pct": 100 * len(sub) / total_country if total_country else np.nan,
                "mortality_pct": 100 * float(death[valid].mean()) if valid.any() else np.nan,
                "los_median": float(los[los.ge(0)].median()) if los.ge(0).any() else np.nan,
            })
        del frame
        collect_memory()
    return pd.DataFrame(rows).sort_values(["country", "mortality_pct"], ascending=[True, False])


def table_age_band_outcomes_v260() -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    columns = ["country", "age_band_common", "death_in_hospital", "los_days"]
    for country in COUNTRY_ORDER:
        frame = pd.read_parquet(cohort_path(country), columns=columns)
        for band, sub in frame.groupby("age_band_common", observed=True, dropna=False):
            death = pd.to_numeric(sub["death_in_hospital"], errors="coerce")
            valid = death.isin([0, 1])
            los = pd.to_numeric(sub["los_days"], errors="coerce")
            rows.append({
                "country": country,
                "age_band": str(band),
                "admissions": len(sub),
                "mortality_pct": 100 * float(death[valid].mean()) if valid.any() else np.nan,
                "los_median": float(los[los.ge(0)].median()) if los.ge(0).any() else np.nan,
            })
        del frame
        collect_memory()
    return pd.DataFrame(rows)


def table_variable_availability_v260() -> pd.DataFrame:
    variables = [
        "age", "sex", "dx_main", "dx_secondary", "trauma_subtype", "death_in_hospital",
        "los_days", "hospital_id", "icu_any", "icu_days", "procedure_code_raw",
        "external_cause", "residence_region", "transfer_proxy", "insurance_type",
        "ethnicity", "facility_sector", "bed_total_available", "cost_local_currency",
    ]
    rows: List[Dict[str, Any]] = []
    for country in COUNTRY_ORDER:
        path = cohort_path(country)
        available = set(parquet_columns(path))
        selected = [variable for variable in variables if variable in available]
        frame = pd.read_parquet(path, columns=selected)
        total = len(frame)
        for variable in variables:
            valid = int(frame[variable].notna().sum()) if variable in frame else 0
            pct = 100 * valid / total if total else np.nan
            rows.append({
                "country": country,
                "variable": variable,
                "records": total,
                "non_missing": valid,
                "availability_pct": pct,
                "status": "Available" if pct >= 95 else "Partial" if pct > 0 else "Structurally unavailable",
            })
        del frame
        collect_memory()
    return pd.DataFrame(rows)


def table_hospital_volume_v260() -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    hy = pd.read_parquet(PATHS.data / "hospital_year_v260.parquet")
    central_rows: List[Dict[str, Any]] = []
    for (country, year), sub in hy.groupby(["country", "year"], observed=True):
        volumes = pd.to_numeric(sub["hospital_volume_year"], errors="coerce").dropna().sort_values(ascending=False).to_numpy()
        total = float(volumes.sum())
        n = len(volumes)
        shares = volumes / total if total else np.array([])
        sorted_asc = np.sort(volumes)
        if len(sorted_asc) and sorted_asc.sum() > 0:
            ranks = np.arange(1, len(sorted_asc) + 1)
            gini = (2 * np.sum(ranks * sorted_asc) / (len(sorted_asc) * sorted_asc.sum())) - (len(sorted_asc) + 1) / len(sorted_asc)
        else:
            gini = np.nan
        def top_share(frac: float) -> float:
            k = max(1, int(math.ceil(frac * n))) if n else 0
            return 100 * float(volumes[:k].sum() / total) if total and k else np.nan
        central_rows.append({
            "country": country, "year": int(year), "hospital_year_units": n,
            "admissions": int(total), "median_volume": float(np.median(volumes)) if n else np.nan,
            "top_5pct_share_pct": top_share(0.05), "top_10pct_share_pct": top_share(0.10),
            "top_20pct_share_pct": top_share(0.20),
            "hhi_0_10000": 10000 * float(np.sum(shares**2)) if total else np.nan,
            "gini_volume": float(gini),
        })
    centralization = pd.DataFrame(central_rows)

    quartile_rows: List[Dict[str, Any]] = []
    decile_rows: List[Dict[str, Any]] = []
    columns = ["country", "hospital_id", "year", "hospital_volume_year", "volume_quartile", "volume_decile", "death_in_hospital", "los_days"]
    for country in VOLUME_COUNTRIES:
        frame = pd.read_parquet(cohort_path(country), columns=columns)
        for quartile, sub in frame.groupby("volume_quartile", observed=True, dropna=True):
            death = pd.to_numeric(sub["death_in_hospital"], errors="coerce")
            valid = death.isin([0, 1])
            los = pd.to_numeric(sub["los_days"], errors="coerce")
            units = sub[["country", "hospital_id", "year", "hospital_volume_year"]].drop_duplicates()
            quartile_rows.append({
                "country": country, "volume_quartile": str(quartile),
                "hospital_year_units": len(units), "unique_hospitals": int(units["hospital_id"].nunique()),
                "admissions": len(sub), "median_hospital_year_volume": float(pd.to_numeric(units["hospital_volume_year"], errors="coerce").median()),
                "mortality_pct": 100 * float(death[valid].mean()) if valid.any() else np.nan,
                "los_median": float(los[los.ge(0)].median()) if los.ge(0).any() else np.nan,
            })
        for decile, sub in frame.groupby("volume_decile", observed=True, dropna=True):
            death = pd.to_numeric(sub["death_in_hospital"], errors="coerce")
            valid = death.isin([0, 1])
            events, total = int(death[valid].sum()), int(valid.sum())
            low, high = wilson_interval(events, total)
            units = sub[["country", "hospital_id", "year", "hospital_volume_year"]].drop_duplicates()
            decile_rows.append({
                "country": country, "volume_decile": str(decile),
                "hospital_year_units": len(units), "admissions": len(sub),
                "median_volume": float(pd.to_numeric(units["hospital_volume_year"], errors="coerce").median()),
                "mortality_pct": 100 * events / total if total else np.nan,
                "mortality_ci_low_pct": 100 * low, "mortality_ci_high_pct": 100 * high,
            })
        del frame
        collect_memory()
    return pd.DataFrame(quartile_rows), pd.DataFrame(decile_rows), centralization


def table_country_specific_v260() -> Dict[str, pd.DataFrame]:
    outputs: Dict[str, pd.DataFrame] = {}
    specifications = {
        "chile": ["insurance_type", "ethnicity", "any_surgical_intervention", "residence_region", "trauma_subtype"],
        "equador": ["facility_sector", "facility_class", "facility_type", "facility_entity", "ethnicity", "residence_area", "transfer_proxy", "discharge_specialty"],
    }
    for country, variables in specifications.items():
        columns = ["country", "death_in_hospital", "los_days"] + variables
        frame = pd.read_parquet(cohort_path(country), columns=[c for c in columns if c in parquet_columns(cohort_path(country))])
        rows: List[pd.DataFrame] = []
        for variable in variables:
            if variable not in frame or frame[variable].notna().sum() == 0:
                continue
            grouped_rows: List[Dict[str, Any]] = []
            for level, sub in frame.groupby(variable, observed=True, dropna=False):
                death = pd.to_numeric(sub["death_in_hospital"], errors="coerce")
                valid = death.isin([0, 1])
                los = pd.to_numeric(sub["los_days"], errors="coerce")
                grouped_rows.append({
                    "variable": variable, "level": str(level), "admissions": len(sub),
                    "mortality_pct": 100 * float(death[valid].mean()) if valid.any() else np.nan,
                    "los_median": float(los[los.ge(0)].median()) if los.ge(0).any() else np.nan,
                })
            rows.append(pd.DataFrame(grouped_rows))
        outputs[country] = pd.concat(rows, ignore_index=True, sort=False) if rows else pd.DataFrame()
        del frame
        collect_memory()
    return outputs


def run_tables_v260(base_dir: Path | str = DEFAULT_BASE) -> Dict[str, str]:
    global PATHS
    PATHS = make_paths(base_dir)
    _log("Generating English manuscript tables")
    outputs: Dict[str, str] = {}
    tables = {
        "Table_1_Cohort_characteristics": table_cohort_characteristics_v260(),
        "Table_2_Annual_outcomes": table_annual_outcomes_v260(),
        "Table_3_TBI_subtype_outcomes": table_subtype_outcomes_v260(),
        "Table_4_Age_band_outcomes": table_age_band_outcomes_v260(),
        "Supplementary_Table_1_Variable_availability": table_variable_availability_v260(),
    }
    quartiles, deciles, centralization = table_hospital_volume_v260()
    tables.update({
        "Table_5_Hospital_volume_quartiles": quartiles,
        "Supplementary_Table_2_Hospital_volume_deciles": deciles,
        "Table_6_Centralization_metrics": centralization,
    })
    country_specific = table_country_specific_v260()
    tables["Supplementary_Table_3_Chile_specific_factors"] = country_specific.get("chile", pd.DataFrame())
    tables["Supplementary_Table_4_Ecuador_specific_factors"] = country_specific.get("equador", pd.DataFrame())

    for name, frame in tables.items():
        save_table(frame, PATHS.tables / name)
        outputs[name] = str(PATHS.tables / f"{name}.csv")
        del frame
        collect_memory()
    _log("Tables completed")
    return outputs


# -----------------------------------------------------------------------------
# Models — sequential, minimal columns, no model objects retained
# -----------------------------------------------------------------------------


def native_model_frame(frame: pd.DataFrame, numeric: Sequence[str], categorical: Sequence[str]) -> pd.DataFrame:
    for column in numeric:
        frame[column] = pd.to_numeric(frame[column], errors="coerce").astype("float64")
    for column in categorical:
        frame[column] = as_string(frame[column]).astype(object).where(frame[column].notna(), None)
    return frame


def extract_effects(fit: Any, analysis: str, terms: Optional[Sequence[str]] = None, effect_label: str = "OR") -> List[Dict[str, Any]]:
    confidence = fit.conf_int()
    rows: List[Dict[str, Any]] = []
    selected = list(fit.params.index) if terms is None else [term for term in terms if term in fit.params.index]
    for term in selected:
        if term == "Intercept":
            continue
        beta = float(fit.params[term])
        rows.append({
            "analysis": analysis,
            "term": term,
            "effect_measure": effect_label,
            "estimate": float(np.exp(beta)),
            "ci_low": float(np.exp(float(confidence.loc[term, 0]))),
            "ci_high": float(np.exp(float(confidence.loc[term, 1]))),
            "p_value": float(fit.pvalues[term]),
            "n": int(fit.nobs),
        })
    return rows


def fit_glm_sequential(
    frame: pd.DataFrame,
    formula: str,
    family: Any,
    analysis: str,
    cluster_column: Optional[str],
    terms: Optional[Sequence[str]] = None,
    effect_label: str = "OR",
) -> List[Dict[str, Any]]:
    if len(frame) < 500:
        _log(f"{analysis}: skipped (N={len(frame)})", "WARNING")
        return []
    kwargs: Dict[str, Any] = {"maxiter": 100}
    if cluster_column:
        kwargs.update({"cov_type": "cluster", "cov_kwds": {"groups": np.asarray(frame[cluster_column], dtype=object)}})
    else:
        kwargs.update({"cov_type": "HC1"})
    try:
        fit = smf.glm(formula=formula, data=frame, family=family).fit(**kwargs)
        rows = extract_effects(fit, analysis, terms, effect_label)
        _log(f"{analysis}: fitted N={int(fit.nobs):,}")
        del fit
        collect_memory()
        return rows
    except Exception as exc:
        _log(f"{analysis}: model failed safely — {type(exc).__name__}: {exc}", "ERROR")
        collect_memory()
        return []


def run_volume_models_v260() -> pd.DataFrame:
    columns = [
        "country", "year", "age", "sex", "trauma_subtype", "death_in_hospital",
        "los_days", "hospital_id", "volume_z_country_year", "log_volume",
        "lag_volume_z_country_year", "hospital_volume_year",
    ]
    data = load_cohort_columns(VOLUME_COUNTRIES, columns)
    data["country_year"] = data["country"].astype(str) + "_" + data["year"].astype(str)
    data = native_model_frame(
        data,
        numeric=["age", "death_in_hospital", "los_days", "volume_z_country_year", "log_volume", "lag_volume_z_country_year", "hospital_volume_year"],
        categorical=["country", "country_year", "year", "sex", "trauma_subtype", "hospital_id"],
    )
    rows: List[Dict[str, Any]] = []
    base = "bs(age, df=4, degree=3, include_intercept=False) + C(sex) + C(trauma_subtype) + C(country_year)"

    mortality = data.dropna(subset=["death_in_hospital", "volume_z_country_year", "age", "sex", "trauma_subtype", "country_year", "hospital_id"])
    mortality = mortality[mortality["death_in_hospital"].isin([0, 1])]
    rows += fit_glm_sequential(
        mortality,
        "death_in_hospital ~ volume_z_country_year + " + base,
        sm.families.Binomial(),
        "Primary mortality: same-year hospital volume",
        "hospital_id",
        ["volume_z_country_year"],
        "OR per 1-SD increase in log volume",
    )
    del mortality
    collect_memory()

    interaction = data.dropna(subset=["death_in_hospital", "volume_z_country_year", "age", "sex", "trauma_subtype", "country", "country_year", "hospital_id"])
    interaction = interaction[interaction["death_in_hospital"].isin([0, 1])]
    rows += fit_glm_sequential(
        interaction,
        "death_in_hospital ~ volume_z_country_year + volume_z_country_year:C(country) + bs(age, df=4, degree=3, include_intercept=False) + C(sex) + C(trauma_subtype) + C(country_year)",
        sm.families.Binomial(),
        "Mortality: hospital volume by country interaction",
        "hospital_id",
        ["volume_z_country_year", "volume_z_country_year:C(country)[T.mexico]"],
        "OR",
    )
    del interaction
    collect_memory()

    lagged = data.dropna(subset=["death_in_hospital", "lag_volume_z_country_year", "age", "sex", "trauma_subtype", "country_year", "hospital_id"])
    lagged = lagged[lagged["death_in_hospital"].isin([0, 1])]
    rows += fit_glm_sequential(
        lagged,
        "death_in_hospital ~ lag_volume_z_country_year + " + base,
        sm.families.Binomial(),
        "Sensitivity mortality: prior-year hospital volume",
        "hospital_id",
        ["lag_volume_z_country_year"],
        "OR per 1-SD increase in prior-year log volume",
    )
    del lagged
    collect_memory()

    survivors = data[
        data["death_in_hospital"].eq(0)
        & data["los_days"].ge(1)
        & data["volume_z_country_year"].notna()
    ].dropna(subset=["age", "sex", "trauma_subtype", "country_year", "hospital_id"])
    rows += fit_glm_sequential(
        survivors,
        "los_days ~ volume_z_country_year + " + base,
        sm.families.Poisson(),
        "Survivor length of stay: same-year hospital volume",
        "hospital_id",
        ["volume_z_country_year"],
        "Mean ratio",
    )
    del survivors
    collect_memory()

    # Country-specific primary models and nonlinear volume curves.
    for country in VOLUME_COUNTRIES:
        country_data = data[data["country"].eq(country)].copy()
        country_data = country_data.dropna(subset=["death_in_hospital", "volume_z_country_year", "age", "sex", "trauma_subtype", "year", "hospital_id"])
        country_data = country_data[country_data["death_in_hospital"].isin([0, 1])]
        rows += fit_glm_sequential(
            country_data,
            "death_in_hospital ~ volume_z_country_year + bs(age, df=4, degree=3, include_intercept=False) + C(sex) + C(trauma_subtype) + C(year)",
            sm.families.Binomial(),
            f"Country-specific mortality: {COUNTRY_DISPLAY[country]}",
            "hospital_id",
            ["volume_z_country_year"],
            "OR per 1-SD increase in log volume",
        )
        # Low-volume exclusion sensitivity.
        minimum = country_data[country_data["hospital_volume_year"].ge(5)].copy()
        rows += fit_glm_sequential(
            minimum,
            "death_in_hospital ~ volume_z_country_year + bs(age, df=4, degree=3, include_intercept=False) + C(sex) + C(trauma_subtype) + C(year)",
            sm.families.Binomial(),
            f"Sensitivity excluding hospital-years with <5 admissions: {COUNTRY_DISPLAY[country]}",
            "hospital_id",
            ["volume_z_country_year"],
            "OR per 1-SD increase in log volume",
        )
        del minimum, country_data
        collect_memory()

    del data
    collect_memory()
    return pd.DataFrame(rows)


def run_multinational_factor_models_v260() -> pd.DataFrame:
    columns = ["country", "year", "age_band_common", "sex", "trauma_subtype", "death_in_hospital", "hospital_id"]
    data = load_cohort_columns(COUNTRY_ORDER, columns)
    data["country_year"] = data["country"].astype(str) + "_" + data["year"].astype(str)
    data = native_model_frame(
        data,
        numeric=["death_in_hospital"],
        categorical=["country", "country_year", "year", "age_band_common", "sex", "trauma_subtype", "hospital_id"],
    )
    data = data.dropna(subset=["death_in_hospital", "country_year", "age_band_common", "sex", "trauma_subtype"])
    data = data[data["death_in_hospital"].isin([0, 1])]

    age_levels = [level for level in ["20-29", "30-49", "50-69", "70-79", "80+"] if level in set(data["age_band_common"].astype(str))]
    if not age_levels:
        raise RuntimeError("No harmonized age-band levels available")
    reference = age_levels[0]
    formula = (
        f"death_in_hospital ~ C(age_band_common, Treatment(reference='{reference}')) + "
        "C(sex) + C(trauma_subtype) + C(country_year)"
    )
    rows = fit_glm_sequential(
        data, formula, sm.families.Binomial(),
        "Pooled individual-level factors across four countries", None, None, "Adjusted OR"
    )
    for row in rows:
        if not any(token in row["term"] for token in ("age_band_common", "C(sex)", "C(trauma_subtype)")):
            row["report_in_factor_table"] = 0
        else:
            row["report_in_factor_table"] = 1

    # Country-specific factor models. Hospital clustering is used only where a stable ID exists.
    for country in COUNTRY_ORDER:
        subset = data[data["country"].eq(country)].copy()
        if len(subset) < 500:
            continue
        cluster = "hospital_id" if country in VOLUME_COUNTRIES else None
        country_levels = [level for level in age_levels if level in set(subset["age_band_common"].astype(str))]
        country_reference = country_levels[0] if country_levels else reference
        country_formula = (
            f"death_in_hospital ~ C(age_band_common, Treatment(reference='{country_reference}')) + "
            "C(sex) + C(trauma_subtype) + C(year)"
        )
        country_rows = fit_glm_sequential(
            subset, country_formula, sm.families.Binomial(),
            f"Country-specific individual factors: {COUNTRY_DISPLAY[country]}", cluster, None, "Adjusted OR"
        )
        for row in country_rows:
            row["country"] = country
            row["report_in_factor_table"] = int(any(token in row["term"] for token in ("age_band_common", "C(sex)", "C(trauma_subtype)")))
        rows.extend(country_rows)
        del subset
        collect_memory()

    del data
    collect_memory()
    result = pd.DataFrame(rows)
    if not result.empty and "report_in_factor_table" in result:
        result = result[result["report_in_factor_table"].eq(1)].reset_index(drop=True)
    return result


def run_pandemic_period_models_v260() -> pd.DataFrame:
    columns = ["country", "year", "age_band_common", "sex", "trauma_subtype", "death_in_hospital", "hospital_id"]
    data = load_cohort_columns(COUNTRY_ORDER, columns)
    numeric_year = pd.to_numeric(data["year"], errors="coerce")
    data["pandemic_period"] = pd.cut(
        numeric_year, bins=[2014, 2019, 2021, 2023],
        labels=["Pre-pandemic", "Pandemic", "Recovery"], right=True
    ).astype("string")
    data = native_model_frame(
        data, numeric=["death_in_hospital"],
        categorical=["country", "year", "pandemic_period", "age_band_common", "sex", "trauma_subtype", "hospital_id"],
    )
    data = data.dropna(subset=["death_in_hospital", "country", "pandemic_period", "age_band_common", "sex", "trauma_subtype"])
    data = data[data["death_in_hospital"].isin([0, 1])]
    rows = fit_glm_sequential(
        data,
        "death_in_hospital ~ C(pandemic_period, Treatment(reference='Pre-pandemic')) * C(country) + C(age_band_common) + C(sex) + C(trauma_subtype)",
        sm.families.Binomial(),
        "Pandemic-period mortality across four countries",
        None, None, "Adjusted OR",
    )
    rows = [row for row in rows if "pandemic_period" in row["term"]]
    for country in COUNTRY_ORDER:
        subset = data[data["country"].eq(country)].copy()
        cluster = "hospital_id" if country in VOLUME_COUNTRIES else None
        country_rows = fit_glm_sequential(
            subset,
            "death_in_hospital ~ C(pandemic_period, Treatment(reference='Pre-pandemic')) + C(age_band_common) + C(sex) + C(trauma_subtype)",
            sm.families.Binomial(),
            f"Pandemic-period mortality: {COUNTRY_DISPLAY[country]}",
            cluster, None, "Adjusted OR",
        )
        for row in country_rows:
            if "pandemic_period" in row["term"]:
                row["country"] = country
                rows.append(row)
        del subset
        collect_memory()
    del data
    collect_memory()
    return pd.DataFrame(rows)


def run_brazil_surgical_model_v260() -> pd.DataFrame:
    path = PATHS.data / "cohort_surgical_strict_brazil_v260.parquet"
    if not path.exists():
        return pd.DataFrame()
    columns = ["procedure_class_analysis", "death_in_hospital", "age", "sex", "trauma_subtype", "year", "hospital_id", "volume_z_country_year"]
    available = [column for column in columns if column in parquet_columns(path)]
    data = pd.read_parquet(path, columns=available)
    for column in columns:
        if column not in data:
            data[column] = pd.NA
    data = native_model_frame(
        data, numeric=["death_in_hospital", "age", "volume_z_country_year"],
        categorical=["procedure_class_analysis", "sex", "trauma_subtype", "year", "hospital_id"],
    )
    data = data.dropna(subset=["procedure_class_analysis", "death_in_hospital", "age", "sex", "trauma_subtype", "year", "hospital_id"])
    data = data[data["death_in_hospital"].isin([0, 1])]
    rows = fit_glm_sequential(
        data,
        "death_in_hospital ~ C(procedure_class_analysis, Treatment(reference='Other acute cranial surgery')) + bs(age, df=4, degree=3, include_intercept=False) + C(sex) + C(trauma_subtype) + C(year) + volume_z_country_year",
        sm.families.Binomial(),
        "Exploratory Brazil surgical association (confounding by indication expected)",
        "hospital_id", None, "Adjusted OR",
    )
    rows = [row for row in rows if "procedure_class_analysis" in row["term"]]
    del data
    collect_memory()
    return pd.DataFrame(rows)


def _collapse_rare_levels(series: pd.Series, minimum: int = 200, maximum_levels: int = 12) -> pd.Series:
    text = as_string(series).fillna("Missing")
    counts = text.value_counts(dropna=False)
    keep = list(counts[counts >= minimum].head(maximum_levels - 1).index)
    return text.where(text.isin(keep), "Other/rare")


def run_country_specific_exploratory_models_v260() -> pd.DataFrame:
    specifications = {
        "chile": ["insurance_type", "ethnicity", "any_surgical_intervention", "residence_region"],
        "equador": ["facility_sector", "facility_class", "facility_type", "facility_entity", "ethnicity", "residence_area", "transfer_proxy", "discharge_specialty"],
    }
    rows: List[Dict[str, Any]] = []
    for country, predictors in specifications.items():
        base_columns = ["death_in_hospital", "age_band_common", "sex", "trauma_subtype", "year"]
        available_columns = set(parquet_columns(cohort_path(country)))
        selected = base_columns + [predictor for predictor in predictors if predictor in available_columns]
        data = pd.read_parquet(cohort_path(country), columns=selected)
        data = native_model_frame(
            data, numeric=["death_in_hospital"],
            categorical=["age_band_common", "sex", "trauma_subtype", "year"] + [p for p in predictors if p in data],
        )
        data = data[data["death_in_hospital"].isin([0, 1])]
        for predictor in predictors:
            if predictor not in data or data[predictor].notna().sum() < 500:
                continue
            model_data = data[["death_in_hospital", "age_band_common", "sex", "trauma_subtype", "year", predictor]].copy()
            model_data[predictor] = _collapse_rare_levels(model_data[predictor])
            model_data = model_data.dropna(subset=["death_in_hospital", "age_band_common", "sex", "trauma_subtype", "year", predictor])
            if model_data[predictor].nunique() < 2:
                continue
            predictor_rows = fit_glm_sequential(
                model_data,
                f"death_in_hospital ~ C({predictor}) + C(age_band_common) + C(sex) + C(trauma_subtype) + C(year)",
                sm.families.Binomial(),
                f"Exploratory {COUNTRY_DISPLAY[country]} association: {predictor}",
                None, None, "Adjusted OR",
            )
            for row in predictor_rows:
                if predictor in row["term"]:
                    row["country"] = country
                    row["predictor"] = predictor
                    rows.append(row)
            del model_data
            collect_memory()
        del data
        collect_memory()
    return pd.DataFrame(rows)


def benjamini_hochberg(p_values: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(p_values, errors="coerce")
    result = pd.Series(np.nan, index=p_values.index, dtype=float)
    valid = numeric.notna()
    if not valid.any():
        return result
    p = numeric.loc[valid].to_numpy()
    order = np.argsort(p)
    ranked = p[order]
    adjusted = np.minimum.accumulate((ranked * len(ranked) / np.arange(1, len(ranked) + 1))[::-1])[::-1]
    adjusted = np.clip(adjusted, 0, 1)
    restored = np.empty_like(adjusted)
    restored[order] = adjusted
    result.loc[valid] = restored
    return result


def run_models_v260(base_dir: Path | str = DEFAULT_BASE) -> Dict[str, str]:
    global PATHS
    PATHS = make_paths(base_dir)
    outputs: Dict[str, str] = {}
    log_memory("before models")

    volume = run_volume_models_v260()
    if volume.empty:
        volume = pd.DataFrame(columns=["analysis", "term", "effect_measure", "estimate", "ci_low", "ci_high", "p_value", "n"])
    volume["fdr_q_value"] = benjamini_hochberg(volume["p_value"])
    save_table(volume, PATHS.tables / "Table_7_Hospital_volume_models")
    volume.to_json(PATHS.models / "Hospital_volume_models_v260.json", orient="records", indent=2)
    outputs["volume_models"] = str(PATHS.tables / "Table_7_Hospital_volume_models.csv")
    del volume
    collect_memory()
    log_memory("after volume models")

    factors = run_multinational_factor_models_v260()
    if factors.empty:
        factors = pd.DataFrame(columns=["analysis", "term", "effect_measure", "estimate", "ci_low", "ci_high", "p_value", "n", "report_in_factor_table"])
    factors["fdr_q_value"] = benjamini_hochberg(factors["p_value"])
    save_table(factors, PATHS.tables / "Table_8_Individual_factor_models")
    factors.to_json(PATHS.models / "Individual_factor_models_v260.json", orient="records", indent=2)
    outputs["factor_models"] = str(PATHS.tables / "Table_8_Individual_factor_models.csv")
    del factors
    collect_memory()
    log_memory("after factor models")

    pandemic = run_pandemic_period_models_v260()
    if pandemic.empty:
        pandemic = pd.DataFrame(columns=["analysis", "term", "effect_measure", "estimate", "ci_low", "ci_high", "p_value", "n"])
    pandemic["fdr_q_value"] = benjamini_hochberg(pandemic["p_value"])
    save_table(pandemic, PATHS.tables / "Table_9_Pandemic_period_models")
    outputs["pandemic_models"] = str(PATHS.tables / "Table_9_Pandemic_period_models.csv")
    del pandemic
    collect_memory()

    surgical = run_brazil_surgical_model_v260()
    if surgical.empty:
        surgical = pd.DataFrame(columns=["analysis", "term", "effect_measure", "estimate", "ci_low", "ci_high", "p_value", "n"])
    save_table(surgical, PATHS.tables / "Supplementary_Table_5_Exploratory_Brazil_surgical_model")
    outputs["surgical_model"] = str(PATHS.tables / "Supplementary_Table_5_Exploratory_Brazil_surgical_model.csv")
    del surgical
    collect_memory()

    exploratory = run_country_specific_exploratory_models_v260()
    if exploratory.empty:
        exploratory = pd.DataFrame(columns=["analysis", "country", "predictor", "term", "effect_measure", "estimate", "ci_low", "ci_high", "p_value", "n"])
    exploratory["fdr_q_value"] = benjamini_hochberg(exploratory["p_value"])
    save_table(exploratory, PATHS.tables / "Supplementary_Table_6_Country_specific_exploratory_models")
    outputs["country_specific_models"] = str(PATHS.tables / "Supplementary_Table_6_Country_specific_exploratory_models.csv")
    del exploratory
    collect_memory()
    log_memory("after all models")
    return outputs


# -----------------------------------------------------------------------------
# English figures — summary tables only
# -----------------------------------------------------------------------------

COUNTRY_LABELS = {"brasil": "Brazil", "mexico": "Mexico", "chile": "Chile", "equador": "Ecuador"}
COUNTRY_COLORS = {"brasil": "#1f77b4", "mexico": "#d62728", "chile": "#2ca02c", "equador": "#ff7f0e"}


def save_figure(fig: plt.Figure, stem: Path) -> None:
    stem.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(stem.with_suffix(".png"), dpi=320, bbox_inches="tight")
    fig.savefig(stem.with_suffix(".pdf"), bbox_inches="tight")
    plt.close(fig)


def figure_cohort_composition(table1: pd.DataFrame) -> None:
    fig, ax = plt.subplots(figsize=(12, 7))
    ax.axis("off")
    total = int(table1["Admissions"].sum())
    ax.text(0.5, 0.91, "Multinational cohort of adults hospitalized with a principal S06.x diagnosis", ha="center", va="center", fontsize=17, weight="bold")
    ax.text(0.5, 0.83, f"Primary harmonized sample (age ≥20 years): N = {total:,}", ha="center", va="center", fontsize=14)
    x_positions = np.linspace(0.13, 0.87, len(table1))
    for x, (_, row) in zip(x_positions, table1.iterrows()):
        country_key = str(row["Country"]).lower().replace("brazil", "brasil").replace("ecuador", "equador")
        color = COUNTRY_COLORS.get(country_key, "#555555")
        hospitals = int(row["Unique hospitals"])
        hospital_text = f"Hospitals: {hospitals:,}" if hospitals > 0 else "Hospital ID unavailable"
        text = f"{row['Country']}\nN = {int(row['Admissions']):,}\n{hospital_text}\n{row['Years']}"
        ax.text(x, 0.48, text, ha="center", va="center", fontsize=12, color="white",
                bbox=dict(boxstyle="round,pad=0.8", facecolor=color, edgecolor="none"))
        ax.annotate("", xy=(x, 0.62), xytext=(0.5, 0.78), arrowprops=dict(arrowstyle="->", color="#666666", lw=1.5))
    ax.text(0.5, 0.13,
            "Hospital-volume analyses: Brazil and Mexico. Individual-level epidemiology and outcome analyses: all four countries.",
            ha="center", fontsize=11,
            bbox=dict(boxstyle="round,pad=0.7", facecolor="#eef1f4", edgecolor="#aab3bd"))
    save_figure(fig, PATHS.figures_main / "Figure_1_Cohort_composition")


def figure_annual_mortality(annual: pd.DataFrame) -> None:
    fig, ax = plt.subplots(figsize=(10, 6))
    for country in COUNTRY_ORDER:
        sub = annual[annual["country"].eq(country)].sort_values("year")
        if sub.empty:
            continue
        label = COUNTRY_LABELS[country]
        color = COUNTRY_COLORS[country]
        x = pd.to_numeric(sub["year"], errors="coerce").to_numpy(dtype=float)
        y = pd.to_numeric(sub["mortality_pct"], errors="coerce").to_numpy(dtype=float)
        low = pd.to_numeric(sub["mortality_ci_low_pct"], errors="coerce").to_numpy(dtype=float)
        high = pd.to_numeric(sub["mortality_ci_high_pct"], errors="coerce").to_numpy(dtype=float)
        ax.plot(x, y, marker="o", linewidth=2, label=label, color=color)
        ax.fill_between(x, low, high, alpha=0.14, color=color)
    ax.set_title("Annual in-hospital mortality after traumatic brain injury")
    ax.set_xlabel("Discharge year")
    ax.set_ylabel("In-hospital mortality (%)")
    ax.legend(frameon=False, ncol=2)
    ax.grid(axis="y", alpha=0.2)
    save_figure(fig, PATHS.figures_main / "Figure_2_Annual_mortality")


def figure_volume_deciles(deciles: pd.DataFrame) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(12, 5), sharey=True)
    for ax, country in zip(axes, VOLUME_COUNTRIES):
        sub = deciles[deciles["country"].eq(country)].copy()
        sub["decile_number"] = pd.to_numeric(sub["volume_decile"].astype(str).str.extract(r"(\d+)")[0], errors="coerce")
        sub = sub.sort_values("decile_number")
        ax.errorbar(
            sub["median_volume"], sub["mortality_pct"],
            yerr=[sub["mortality_pct"] - sub["mortality_ci_low_pct"], sub["mortality_ci_high_pct"] - sub["mortality_pct"]],
            fmt="o-", capsize=3, color=COUNTRY_COLORS[country], linewidth=1.8,
        )
        ax.set_xscale("log")
        ax.set_title(COUNTRY_LABELS[country])
        ax.set_xlabel("Median annual TBI volume per hospital (log scale)")
        ax.grid(alpha=0.2)
    axes[0].set_ylabel("Crude in-hospital mortality (%)")
    fig.suptitle("Unadjusted hospital-volume deciles and mortality")
    save_figure(fig, PATHS.figures_main / "Figure_3_Volume_deciles_and_mortality")


def figure_volume_forest(models: pd.DataFrame) -> None:
    required = {"analysis", "term", "estimate", "ci_low", "ci_high"}
    if models.empty or not required.issubset(models.columns):
        return
    keep = models[
        models["analysis"].isin([
            "Primary mortality: same-year hospital volume",
            "Country-specific mortality: Brazil",
            "Country-specific mortality: Mexico",
            "Sensitivity mortality: prior-year hospital volume",
        ])
        & models["term"].isin(["volume_z_country_year", "lag_volume_z_country_year"])
    ].copy()
    if keep.empty:
        return
    labels = keep["analysis"].tolist()
    y = np.arange(len(keep))[::-1]
    fig, ax = plt.subplots(figsize=(9, 4.8))
    ax.errorbar(
        keep["estimate"], y,
        xerr=[keep["estimate"] - keep["ci_low"], keep["ci_high"] - keep["estimate"]],
        fmt="o", capsize=4, color="#333333",
    )
    ax.axvline(1.0, color="#777777", linestyle="--", linewidth=1)
    ax.set_yticks(y, labels)
    ax.set_xscale("log")
    ax.set_xlabel("Adjusted odds ratio (95% CI)")
    ax.set_title("Hospital volume and in-hospital mortality")
    ax.grid(axis="x", alpha=0.2)
    save_figure(fig, PATHS.figures_main / "Figure_4_Hospital_volume_forest_plot")


def figure_centralization(centralization: pd.DataFrame) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(12, 5))
    for country in VOLUME_COUNTRIES:
        sub = centralization[centralization["country"].eq(country)].sort_values("year")
        axes[0].plot(sub["year"], sub["top_10pct_share_pct"], marker="o", label=COUNTRY_LABELS[country], color=COUNTRY_COLORS[country])
        axes[1].plot(sub["year"], sub["gini_volume"], marker="o", label=COUNTRY_LABELS[country], color=COUNTRY_COLORS[country])
    axes[0].set_title("Share treated by the highest-volume 10% of hospitals")
    axes[0].set_ylabel("Admissions (%)")
    axes[1].set_title("Inequality in hospital volume")
    axes[1].set_ylabel("Gini coefficient")
    for ax in axes:
        ax.set_xlabel("Year")
        ax.grid(alpha=0.2)
        ax.legend(frameon=False)
    save_figure(fig, PATHS.figures_main / "Figure_5_Centralization_trends")


def figure_availability(availability: pd.DataFrame) -> None:
    pivot = availability.pivot(index="variable", columns="country", values="availability_pct").reindex(columns=COUNTRY_ORDER)
    fig, ax = plt.subplots(figsize=(8, 8))
    image = ax.imshow(pivot.to_numpy(), aspect="auto", vmin=0, vmax=100, cmap="viridis")
    ax.set_xticks(range(len(pivot.columns)), [COUNTRY_LABELS[c] for c in pivot.columns])
    ax.set_yticks(range(len(pivot.index)), [label.replace("_", " ").title() for label in pivot.index])
    for i in range(len(pivot.index)):
        for j in range(len(pivot.columns)):
            value = pivot.iloc[i, j]
            ax.text(j, i, f"{value:.0f}%", ha="center", va="center", color="white" if value < 50 else "black", fontsize=8)
    ax.set_title("Availability of harmonized variables by country")
    colorbar = fig.colorbar(image, ax=ax)
    colorbar.set_label("Non-missing records (%)")
    save_figure(fig, PATHS.figures_supp / "Supplementary_Figure_1_Variable_availability")


def figure_subtype(subtype: pd.DataFrame) -> None:
    fig, axes = plt.subplots(2, 2, figsize=(13, 9))
    for ax, country in zip(axes.ravel(), COUNTRY_ORDER):
        sub = subtype[subtype["country"].eq(country)].nlargest(8, "admissions").sort_values("mortality_pct")
        ax.barh(sub["trauma_subtype"].str.replace("_", " ").str.title(), sub["mortality_pct"], color=COUNTRY_COLORS[country])
        ax.set_title(COUNTRY_LABELS[country])
        ax.set_xlabel("Crude in-hospital mortality (%)")
        ax.grid(axis="x", alpha=0.2)
    fig.suptitle("In-hospital mortality by administrative TBI subtype")
    save_figure(fig, PATHS.figures_supp / "Supplementary_Figure_2_Mortality_by_TBI_subtype")


def figure_age_band(age_table: pd.DataFrame) -> None:
    order = ["20-29", "30-49", "50-69", "70-79", "80+"]
    fig, ax = plt.subplots(figsize=(9, 6))
    for country in COUNTRY_ORDER:
        sub = age_table[age_table["country"].eq(country)].copy()
        sub["age_band"] = pd.Categorical(sub["age_band"], categories=order, ordered=True)
        sub = sub.sort_values("age_band")
        ax.plot(sub["age_band"].astype(str), sub["mortality_pct"], marker="o", label=COUNTRY_LABELS[country], color=COUNTRY_COLORS[country])
    ax.set_title("In-hospital mortality across harmonized age bands")
    ax.set_xlabel("Age band (years)")
    ax.set_ylabel("Crude in-hospital mortality (%)")
    ax.legend(frameon=False, ncol=2)
    ax.grid(axis="y", alpha=0.2)
    save_figure(fig, PATHS.figures_supp / "Supplementary_Figure_3_Mortality_by_age_band")


def run_figures_v260(base_dir: Path | str = DEFAULT_BASE) -> Dict[str, str]:
    global PATHS
    PATHS = make_paths(base_dir)
    table1 = pd.read_csv(PATHS.tables / "Table_1_Cohort_characteristics.csv")
    annual = pd.read_csv(PATHS.tables / "Table_2_Annual_outcomes.csv")
    subtype = pd.read_csv(PATHS.tables / "Table_3_TBI_subtype_outcomes.csv")
    age_table = pd.read_csv(PATHS.tables / "Table_4_Age_band_outcomes.csv")
    deciles = pd.read_csv(PATHS.tables / "Supplementary_Table_2_Hospital_volume_deciles.csv")
    centralization = pd.read_csv(PATHS.tables / "Table_6_Centralization_metrics.csv")
    availability = pd.read_csv(PATHS.tables / "Supplementary_Table_1_Variable_availability.csv")

    figure_cohort_composition(table1)
    figure_annual_mortality(annual)
    figure_volume_deciles(deciles)
    figure_centralization(centralization)
    figure_availability(availability)
    figure_subtype(subtype)
    figure_age_band(age_table)
    model_path = PATHS.tables / "Table_7_Hospital_volume_models.csv"
    if model_path.exists():
        figure_volume_forest(pd.read_csv(model_path))

    del table1, annual, subtype, age_table, deciles, centralization, availability
    collect_memory()
    return {
        "main_figures": str(PATHS.figures_main),
        "supplementary_figures": str(PATHS.figures_supp),
    }


# -----------------------------------------------------------------------------
# QC and orchestration
# -----------------------------------------------------------------------------


def validate_outputs_v260() -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    for country in COUNTRY_ORDER:
        path = cohort_path(country)
        if not path.exists():
            rows.append({"country": country, "status": "MISSING", "records": 0})
            continue
        columns = ["country", "year", "death_in_hospital", "los_days", "age_band_common", "hospital_id"]
        frame = pd.read_parquet(path, columns=columns)
        rows.append({
            "country": country,
            "status": "OK",
            "records": len(frame),
            "years": ",".join(map(str, sorted(pd.to_numeric(frame["year"], errors="coerce").dropna().astype(int).unique()))),
            "mortality_available_pct": 100 * frame["death_in_hospital"].notna().mean(),
            "los_available_pct": 100 * frame["los_days"].notna().mean(),
            "age_band_available_pct": 100 * frame["age_band_common"].notna().mean(),
            "unique_hospitals": int(frame["hospital_id"].nunique(dropna=True)),
        })
        del frame
        collect_memory()
    report = pd.DataFrame(rows)
    save_table(report, PATHS.qc / "Final_cohort_validation_v260")
    return report


def write_analysis_manifest_v260(extra: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    validation = validate_outputs_v260()
    manifest: Dict[str, Any] = {
        "version": VERSION,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "base_dir": str(PATHS.base),
        "output_dir": str(PATHS.output),
        "primary_age_policy": "Age >=20 years in all countries",
        "sensitivity_age_policy": "Age >=18 years where exact age is available; Chile remains >=20",
        "hospital_volume_countries": ["Brazil", "Mexico"],
        "individual_level_countries": ["Brazil", "Mexico", "Chile", "Ecuador"],
        "cohort_validation": validation.to_dict(orient="records"),
        "tables_dir": str(PATHS.tables),
        "main_figures_dir": str(PATHS.figures_main),
        "supplementary_figures_dir": str(PATHS.figures_supp),
        "models_dir": str(PATHS.models),
        "memory_design": "Country-partitioned Parquet; selected-column reads; sequential models; no large DataFrames returned",
    }
    if extra:
        manifest.update(dict(extra))
    PATHS.manuscript.mkdir(parents=True, exist_ok=True)
    (PATHS.manuscript / "analysis_manifest_v260.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return manifest


def run_pipeline_complete_v260(
    base_dir: Path | str = DEFAULT_BASE,
    rebuild_chile: bool = True,
    run_models: bool = True,
    clean_output: bool = True,
) -> Dict[str, Any]:
    """
    Complete clean run. Returns only a small manifest, never the full cohort DataFrames.
    """
    global PATHS
    if clean_output:
        reset_analysis_v260(base_dir)
    else:
        PATHS = make_paths(base_dir)
    release_legacy_notebook_objects_v260()
    log_memory("start")
    _log(f"▶▶▶ TCE MASTER v{VERSION} LOW-MEMORY RUN STARTED ◀◀◀")

    stages: Dict[str, Any] = {}
    stages["lean_parts"] = build_lean_country_parts_v260(base_dir, rebuild_chile=rebuild_chile)
    log_memory("after lean parts")
    stages["cohort_parts"] = build_analysis_cohort_parts_v260(base_dir)
    log_memory("after cohort parts")
    stages["tables"] = run_tables_v260(base_dir)
    log_memory("after tables")
    if run_models:
        stages["models"] = run_models_v260(base_dir)
        log_memory("after models")
    stages["figures"] = run_figures_v260(base_dir)
    log_memory("after figures")
    manifest = write_analysis_manifest_v260({"stages": stages})
    _log(f"▶▶▶ TCE MASTER v{VERSION} COMPLETED ◀◀◀")
    return manifest


def resume_analysis_v260(
    base_dir: Path | str = DEFAULT_BASE,
    run_models: bool = True,
    regenerate_tables: bool = True,
    regenerate_figures: bool = True,
) -> Dict[str, Any]:
    """Resume from v2.6 country cohort parts without rebuilding raw/intermediate data."""
    global PATHS
    PATHS = make_paths(base_dir)
    release_legacy_notebook_objects_v260()
    missing = [country for country in COUNTRY_ORDER if not cohort_path(country).exists()]
    if missing:
        raise FileNotFoundError(f"Missing v2.6 cohort part(s): {missing}. Run prepare_data_v260() first.")
    stages: Dict[str, Any] = {}
    if regenerate_tables:
        stages["tables"] = run_tables_v260(base_dir)
    if run_models:
        stages["models"] = run_models_v260(base_dir)
    if regenerate_figures:
        stages["figures"] = run_figures_v260(base_dir)
    return write_analysis_manifest_v260({"resume_stages": stages})


def prepare_data_v260(
    base_dir: Path | str = DEFAULT_BASE,
    rebuild_chile: bool = True,
    clean_output: bool = True,
) -> Dict[str, Any]:
    """Data preparation only. Recommended first call on a 13-GB Colab runtime."""
    global PATHS
    if clean_output:
        reset_analysis_v260(base_dir)
    else:
        PATHS = make_paths(base_dir)
    release_legacy_notebook_objects_v260()
    lean = build_lean_country_parts_v260(base_dir, rebuild_chile=rebuild_chile)
    cohorts = build_analysis_cohort_parts_v260(base_dir)
    validation = validate_outputs_v260()
    return {"version": VERSION, "lean_parts": lean, "cohort_parts": cohorts, "validation": validation.to_dict(orient="records")}


def verify_tce_master_v260() -> Dict[str, Any]:
    status = {
        "version": VERSION,
        "complete_runner": run_pipeline_complete_v260.__name__,
        "data_preparation": prepare_data_v260.__name__,
        "resume": resume_analysis_v260.__name__,
        "output_root": str(PATHS.output),
        "primary_years": list(PRIMARY_YEARS),
        "volume_countries": list(VOLUME_COUNTRIES),
        "primary_min_age": PRIMARY_MIN_AGE,
        "returns_large_dataframes": False,
        "figure_language": "English",
    }
    print(json.dumps(status, indent=2, ensure_ascii=False))
    return status


# v2.6 loading banner suppressed by the integrated v2.8 final analytic suite.

# =============================================================================
# TCE MASTER v2.8.0 — FINAL ANALYTIC REBUILD
# =============================================================================
# This section supersedes the v2.6 runners above. It preserves the validated
# low-memory utilities while rebuilding Mexico from annual checkpoints when
# available and adding the final Q1-oriented analytic suite.

FINAL_VERSION = "2.8.0"

try:
    from scipy.stats import chi2, poisson
except Exception as exc:  # pragma: no cover
    raise ImportError("scipy.stats chi2 and poisson are required") from exc

try:
    from patsy import build_design_matrices
except Exception as exc:  # pragma: no cover
    raise ImportError("patsy is required") from exc


def make_paths_v280(base_dir: Path | str = DEFAULT_BASE) -> Paths:
    base = Path(base_dir)
    output = base / "analysis_v280"
    paths = Paths(
        base=base,
        raw=base / "00_raw",
        intermediate=base / "01_intermediate",
        output=output,
        data=output / "01_data",
        qc=output / "02_qc",
        tables=output / "03_tables",
        figures_main=output / "04_figures_main",
        figures_supp=output / "05_figures_supplement",
        models=output / "06_models",
        logs=output / "07_logs",
        manuscript=output / "08_manuscript_support",
    )
    for folder in paths.__dict__.values():
        if isinstance(folder, Path):
            folder.mkdir(parents=True, exist_ok=True)
    return paths


def activate_v280(base_dir: Path | str = DEFAULT_BASE) -> Paths:
    global PATHS
    PATHS = make_paths_v280(base_dir)
    return PATHS


def reset_analysis_v280(base_dir: Path | str = DEFAULT_BASE) -> Path:
    global PATHS
    base = Path(base_dir)
    target = base / "analysis_v280"
    if target.exists():
        shutil.rmtree(target)
    PATHS = make_paths_v280(base)
    _log(f"Clean v2.8 output directory prepared: {target}")
    return target


def _meaningful_text(series: pd.Series) -> pd.Series:
    text = as_string(series).str.strip()
    bad = text.str.upper().isin({
        "", "NA", "N/A", "NAN", "NONE", "NULL", "<NA>", "UNKNOWN",
        "UNKNOWN/NOT RECORDED", "DESCONOCIDO", "IGNORADO", "NO INFORMADO",
        "NOT AVAILABLE", "MISSING",
    })
    return text.where(text.notna() & ~bad, pd.NA)


def _safe_exp_scalar(value: Any) -> float:
    try:
        numeric = float(value)
    except Exception:
        return float("nan")
    if not np.isfinite(numeric):
        return float("nan") if np.isnan(numeric) else (float("inf") if numeric > 0 else 0.0)
    if numeric > 700:
        return float("inf")
    if numeric < -745:
        return 0.0
    return float(np.exp(numeric))


def extract_effects_v280(
    fit: Any,
    analysis: str,
    terms: Optional[Sequence[str]] = None,
    effect_label: str = "OR",
    family: str = "unspecified",
    model_role: str = "secondary",
) -> List[Dict[str, Any]]:
    confidence = fit.conf_int()
    rows: List[Dict[str, Any]] = []
    selected = list(fit.params.index) if terms is None else [term for term in terms if term in fit.params.index]
    for term in selected:
        if term == "Intercept":
            continue
        beta = float(fit.params[term])
        rows.append({
            "analysis": analysis,
            "model_role": model_role,
            "multiplicity_family": family,
            "term": term,
            "effect_measure": effect_label,
            "estimate": _safe_exp_scalar(beta),
            "ci_low": _safe_exp_scalar(confidence.loc[term, 0]),
            "ci_high": _safe_exp_scalar(confidence.loc[term, 1]),
            "p_value": float(fit.pvalues[term]),
            "n": int(fit.nobs),
            "converged": bool(getattr(fit, "converged", True)),
            "aic": float(getattr(fit, "aic", np.nan)),
        })
    return rows



def fit_glm_v280(
    frame: pd.DataFrame,
    formula: str,
    family_obj: Any,
    analysis: str,
    cluster_column: Optional[str] = None,
    terms: Optional[Sequence[str]] = None,
    effect_label: str = "OR",
    multiplicity_family: str = "unspecified",
    model_role: str = "secondary",
    return_fit: bool = False,
) -> Tuple[List[Dict[str, Any]], Optional[Any]]:
    """Fit a GLM with cluster groups aligned to Patsy's complete-case rows.

    Statsmodels/Patsy may drop formula rows with missing values. Passing the
    pre-drop cluster vector causes the classic "weights and list don't have the
    same length" error. This implementation builds the model first, retrieves
    the exact retained row labels, and aligns cluster groups to them.
    """
    if len(frame) < 500:
        _log(f"{analysis}: skipped (N={len(frame)})", "WARNING")
        return [], None
    working = frame.reset_index(drop=True).copy()
    if cluster_column and cluster_column in working:
        working = working[working[cluster_column].notna()].reset_index(drop=True)
    try:
        model = smf.glm(formula=formula, data=working, family=family_obj, missing="drop")
        retained_n = int(len(model.endog))
        if retained_n < 500:
            _log(f"{analysis}: skipped after complete-case design (N={retained_n})", "WARNING")
            return [], None
        fit_kwargs: Dict[str, Any] = {"maxiter": 150}
        if cluster_column and cluster_column in working:
            labels = list(model.data.row_labels)
            groups = working.loc[labels, cluster_column].astype(str).to_numpy()
            if len(groups) != retained_n:
                raise RuntimeError(
                    f"Cluster alignment failed: groups={len(groups)} retained_rows={retained_n}"
                )
            if pd.Series(groups).nunique(dropna=True) >= 2:
                fit_kwargs.update({"cov_type": "cluster", "cov_kwds": {"groups": groups}})
            else:
                fit_kwargs.update({"cov_type": "HC1"})
        else:
            fit_kwargs.update({"cov_type": "HC1"})
        fit = model.fit(**fit_kwargs)
        rows = extract_effects_v280(
            fit,
            analysis,
            terms,
            effect_label,
            family=multiplicity_family,
            model_role=model_role,
        )
        _log(f"{analysis}: fitted N={int(fit.nobs):,}")
        if return_fit:
            return rows, fit
        del fit, model, working
        collect_memory()
        return rows, None
    except Exception as exc:
        _log(f"{analysis}: model failed safely — {type(exc).__name__}: {exc}", "ERROR")
        collect_memory()
        return [], None


def apply_fdr_by_family_v280(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    result["fdr_q_value"] = np.nan
    if result.empty or "p_value" not in result:
        return result
    family_series = result.get("multiplicity_family", pd.Series("unspecified", index=result.index)).fillna("unspecified")
    for family_name, idx in family_series.groupby(family_series).groups.items():
        result.loc[idx, "fdr_q_value"] = benjamini_hochberg(result.loc[idx, "p_value"]).values
    return result


# -----------------------------------------------------------------------------
# Mexico annual-checkpoint audit and reconstruction
# -----------------------------------------------------------------------------



def mexico_annual_checkpoint_map_v280(base_dir: Path | str = DEFAULT_BASE) -> Dict[int, Path]:
    """Return compact annual Mexico S06 checkpoints when present."""
    inter = Path(base_dir) / "01_intermediate" / "mexico"
    result: Dict[int, Path] = {}
    for year in PRIMARY_YEARS:
        candidate = inter / f"mexico_s06_analytic_{year}.parquet"
        if candidate.exists():
            result[year] = candidate
    return result


def mexico_full_checkpoint_map_v280(base_dir: Path | str = DEFAULT_BASE) -> Dict[int, Path]:
    """Return full annual Mexico S06 checkpoints when present.

    The full checkpoint is essential for recovering fields that were omitted from
    an older compact checkpoint. It is still S06-filtered and therefore small
    enough for the low-memory workflow.
    """
    inter = Path(base_dir) / "01_intermediate" / "mexico"
    result: Dict[int, Path] = {}
    for year in PRIMARY_YEARS:
        candidate = inter / f"mexico_s06_full_{year}.parquet"
        if candidate.exists():
            result[year] = candidate
    return result


MEXICO_ALIASES_V280: Dict[str, Sequence[str]] = {
    "year": ("year", "ANIO_EGR", "AÑO_EGR", "ANIO", "AÑO", "ANIOEGRESO", "AÑOEGRESO", "ANOCAP"),
    "month": ("month", "MES_EGR", "MES", "MES_EGRESO", "MESCAP"),
    "hospital_id": (
        "hospital_id", "hospital_id_raw", "CLUES", "CLUES_UNIDAD", "CLUES_HOSP",
        "UNIDAD_MEDICA", "ID_UNIDAD", "CLUES_ESTABLECIMIENTO", "CLUES_EGRESO",
    ),
    "hospital_region": ("hospital_region", "ENTIDAD_UM", "ENTIDAD_UNIDAD", "ENTIDAD", "EDO", "EENTIDAD", "CEDOCVE"),
    "residence_region": ("residence_region", "ENTIDAD_RES", "ENTIDAD_RESIDENCIA", "EDO_RES", "RES_ENTIDAD"),
    "age": ("age", "EDAD", "EDAD_CUMPLIDA", "EDAD1", "EDAD_INSP", "EDADCUMPL", "EDAD_CUMPL"),
    "age_unit": ("age_unit", "TIPO_EDAD", "EDAD_TIPO", "UNIDAD_EDAD", "CLAVE_EDAD", "CEDAD_INSP"),
    "sex": ("sex", "sex_raw", "SEXO", "SEX"),
    "los_days": ("los_days", "DIAS_ESTANCIA", "DIAS_ESTA", "ESTANCIA", "DIAS_ESTADA", "DIAS_ESTAD"),
    "dx_main": (
        "dx_main", "AFECCION_PPAL", "AFECCION_PRINCIPAL", "DIAG_PRIN", "DIAG_PRINC",
        "DIAGNOSTICO_PRINCIPAL", "CAUSA_EGRESO", "CIE10", "AFECPRIN4", "AFECPRIN3",
        "AFEC_PRIN4", "AFEC_PRIN3",
    ),
    "dx_secondary": ("dx_secondary", "AFECCION_SEC", "DIAG_SEC", "DIAGNOSTICO_SECUNDARIO"),
    "external_cause": ("external_cause", "CAUSA_EXT", "CAUSA_EXTERNA", "CAUSABAS4", "CAUSABAS3"),
    "discharge_reason": ("discharge_reason", "MOTIVO_EGRESO", "MOTIVO_DE_EGRESO", "MOTEGRE"),
    "discharge_condition": ("discharge_condition", "CONDICION_EGRESO", "COND_EGRESO", "COND_EGR"),
    "death_in_hospital": ("death_in_hospital",),
    "procedure_code_raw": ("procedure_code_raw", "INTERVENCION_QX", "TIPO_INTERVENCION", "CODIGO_CIE_9_MC", "COD_CIE9MC"),
    "record_id": ("record_id",),
    "source_file": ("source_file", "_source_file"),
    "source_dataset": ("source_dataset", "source"),
}


def _column_key_v280(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", ascii_text(value))


def _resolve_aliases_v280(columns: Sequence[str], aliases: Mapping[str, Sequence[str]]) -> Dict[str, str]:
    by_key: Dict[str, str] = {}
    for column in columns:
        by_key.setdefault(_column_key_v280(column), column)
    result: Dict[str, str] = {}
    for canonical, choices in aliases.items():
        for choice in choices:
            actual = by_key.get(_column_key_v280(choice))
            if actual is not None:
                result[canonical] = actual
                break
    return result


def _mexico_age_years_v280(age_source: pd.Series, unit_source: Optional[pd.Series]) -> Tuple[pd.Series, str]:
    age = pd.to_numeric(age_source, errors="coerce").astype(float)
    age = age.where(age.between(0, 120))
    if unit_source is None:
        return age, "AGE_0_120_NO_UNIT_FIELD"

    unit_raw = _meaningful_text(unit_source)
    if unit_raw.notna().sum() == 0:
        return age, "AGE_0_120_UNIT_EMPTY"

    unit_ascii = unit_raw.map(ascii_text)
    years_text = unit_ascii.str.contains(r"ano|anos|year|years", regex=True, na=False)
    nonyears_text = unit_ascii.str.contains(r"dia|dias|day|days|mes|meses|month|months|hora|horas", regex=True, na=False)
    numeric_unit = pd.to_numeric(unit_raw, errors="coerce")

    valid = pd.Series(False, index=age.index)
    valid.loc[years_text] = True
    valid.loc[nonyears_text] = False

    # Historical SAEH releases may code the age unit numerically. Rather than
    # hard-code an undocumented value, infer the likely years code from the
    # dominant unit among plausible adult ages. This is recorded in the audit.
    unresolved_numeric = numeric_unit.notna() & ~years_text & ~nonyears_text
    method = "TEXT_UNIT"
    if unresolved_numeric.any():
        plausible = unresolved_numeric & age.between(18, 120)
        counts = numeric_unit.loc[plausible].value_counts(dropna=True)
        if not counts.empty:
            years_code = counts.index[0]
            dominance = float(counts.iloc[0] / counts.sum())
            if dominance >= 0.50:
                valid.loc[unresolved_numeric & numeric_unit.eq(years_code)] = True
                method = f"INFERRED_NUMERIC_YEARS_CODE={years_code};DOMINANCE={dominance:.3f}"
            else:
                method = "AMBIGUOUS_NUMERIC_AGE_UNIT"

    # If the unit is nonnumeric and unrecognized, do not silently assume years.
    unknown_unit = unit_raw.notna() & ~years_text & ~nonyears_text & numeric_unit.isna()
    valid.loc[unknown_unit] = False
    return age.where(valid), method


def _mexico_death_v280(frame: pd.DataFrame, mapping: Mapping[str, str]) -> Tuple[pd.Series, str]:
    if "death_in_hospital" in mapping:
        numeric = pd.to_numeric(frame[mapping["death_in_hospital"]], errors="coerce")
        death = numeric.where(numeric.isin([0, 1])).astype("Int64")
        if death.notna().any():
            return death, "EXISTING_BINARY_OUTCOME"

    for canonical in ("discharge_reason", "discharge_condition"):
        source = mapping.get(canonical)
        if source is None:
            continue
        raw = _meaningful_text(frame[source])
        if raw.notna().sum() == 0:
            continue
        text = raw.map(ascii_text)
        numeric = pd.to_numeric(raw, errors="coerce")
        death = pd.Series(pd.NA, index=frame.index, dtype="Int64")
        death_text = text.str.contains(r"defunc|fallec|muerte|death|deceso", regex=True, na=False)
        alive_text = text.str.contains(r"alta|mejoria|curacion|traslado|voluntaria|alive|vivo", regex=True, na=False)
        death.loc[death_text] = 1
        death.loc[alive_text] = 0
        unique_codes = set(numeric.dropna().astype(int).unique().tolist())
        if unique_codes and unique_codes.issubset({1, 2}):
            death.loc[numeric.eq(1)] = 0
            death.loc[numeric.eq(2)] = 1
            method = f"{canonical.upper()}_BINARY_1_ALIVE_2_DEATH"
        else:
            death.loc[numeric.eq(5)] = 1
            death.loc[numeric.isin([1, 2, 3, 4, 6, 7, 8, 9])] = 0
            method = f"{canonical.upper()}_CODE5_DEATH"
        if death.notna().any():
            return death, method
    return pd.Series(pd.NA, index=frame.index, dtype="Int64"), "OUTCOME_UNRESOLVED"


def normalize_mexico_candidate_v280(frame: pd.DataFrame, year: int, source_path: Path, source_type: str) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    frame = frame.copy()
    mapping = _resolve_aliases_v280(list(frame.columns), MEXICO_ALIASES_V280)
    out = pd.DataFrame(index=frame.index)
    out["country"] = "mexico"
    out["year"] = int(year)
    out["month"] = pd.to_numeric(frame[mapping["month"]], errors="coerce") if "month" in mapping else pd.NA
    out["dx_main"] = normalize_dx(frame[mapping["dx_main"]]) if "dx_main" in mapping else pd.NA
    out["dx_secondary"] = normalize_dx(frame[mapping["dx_secondary"]]) if "dx_secondary" in mapping else pd.NA
    out["hospital_id"] = _meaningful_text(frame[mapping["hospital_id"]]) if "hospital_id" in mapping else pd.NA
    out["hospital_region"] = _meaningful_text(frame[mapping["hospital_region"]]) if "hospital_region" in mapping else pd.NA
    out["residence_region"] = _meaningful_text(frame[mapping["residence_region"]]) if "residence_region" in mapping else pd.NA
    if "age" in mapping:
        age, age_method = _mexico_age_years_v280(frame[mapping["age"]], frame[mapping["age_unit"]] if "age_unit" in mapping else None)
    else:
        age = pd.Series(np.nan, index=frame.index, dtype=float)
        age_method = "AGE_FIELD_UNRESOLVED"
    out["age"] = age
    out["age_unit"] = _meaningful_text(frame[mapping["age_unit"]]) if "age_unit" in mapping else pd.NA
    out["sex"] = normalize_sex(frame[mapping["sex"]]) if "sex" in mapping else pd.NA
    out["los_days"] = pd.to_numeric(frame[mapping["los_days"]], errors="coerce") if "los_days" in mapping else pd.NA
    out["external_cause"] = _meaningful_text(frame[mapping["external_cause"]]) if "external_cause" in mapping else pd.NA
    out["procedure_code_raw"] = _meaningful_text(frame[mapping["procedure_code_raw"]]) if "procedure_code_raw" in mapping else pd.NA
    death, death_method = _mexico_death_v280(frame, mapping)
    out["death_in_hospital"] = death
    out["record_id"] = (
        _meaningful_text(frame[mapping["record_id"]]) if "record_id" in mapping
        else pd.Series([f"MX-{year}-{source_type}-{i+1}" for i in range(len(frame))], index=frame.index, dtype="string")
    )
    out["source_file"] = str(source_path)
    out["source_dataset"] = (
        _meaningful_text(frame[mapping["source_dataset"]]).fillna(f"SAEH-DGIS {source_type}")
        if "source_dataset" in mapping else f"SAEH-DGIS {source_type}"
    )

    # Fields retained for the canonical lean schema.
    for column in LEAN_COLUMNS:
        if column not in out:
            out[column] = pd.NA
    before_s06 = len(out)
    out = normalize_country_frame(out, "mexico")
    adult_rows = int(len(out))
    mortality_pct = 100 * float(out["death_in_hospital"].notna().mean()) if adult_rows else 0.0
    hospital_pct = 100 * float(out["hospital_id"].notna().mean()) if adult_rows else 0.0
    status: str
    if before_s06 == 0 or "dx_main" not in mapping:
        status = "EXCLUDED_NO_DIAGNOSIS_FIELD"
    elif adult_rows == 0:
        status = "EXCLUDED_NO_VALID_ADULT_AGE"
    elif mortality_pct < 80:
        status = "EXCLUDED_OUTCOME_INCOMPLETE"
    elif hospital_pct < 50:
        status = "INCLUDED_INDIVIDUAL_ONLY"
    else:
        status = "INCLUDED_VOLUME_AND_INDIVIDUAL"
    audit = {
        "year": int(year),
        "source_type": source_type,
        "path": str(source_path),
        "source_rows": int(len(frame)),
        "canonical_columns_resolved": ",".join(sorted(mapping)),
        "age_method": age_method,
        "death_method": death_method,
        "adult_18plus_s06_rows": adult_rows,
        "adult_20plus_s06_rows": int(out["primary_sample_20plus"].fillna(0).sum()) if adult_rows else 0,
        "mortality_available_pct": round(mortality_pct, 3),
        "hospital_id_available_pct": round(hospital_pct, 3),
        "status": status,
    }
    return out, audit


def _read_mexico_candidate_v280(path: Path, year: int, source_type: str) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    columns = parquet_columns(path)
    mapping = _resolve_aliases_v280(columns, MEXICO_ALIASES_V280)
    requested = sorted(set(mapping.values()))
    # Avoid reading every column of full checkpoints.
    raw = pd.read_parquet(path, columns=requested) if requested else pd.DataFrame(index=range(pq.ParquetFile(path).metadata.num_rows))
    normalized, audit = normalize_mexico_candidate_v280(raw, year, path, source_type)
    del raw
    collect_memory()
    return normalized, audit


def resolve_mexico_year_v280(base_dir: Path | str, year: int) -> Tuple[pd.DataFrame, pd.DataFrame]:
    inter = Path(base_dir) / "01_intermediate" / "mexico"
    candidates: List[Tuple[str, Path]] = []
    analytic = inter / f"mexico_s06_analytic_{year}.parquet"
    full = inter / f"mexico_s06_full_{year}.parquet"
    if analytic.exists():
        candidates.append(("ANALYTIC_CHECKPOINT", analytic))
    if full.exists():
        candidates.append(("FULL_CHECKPOINT", full))
    if not candidates:
        return pd.DataFrame(columns=LEAN_COLUMNS), pd.DataFrame([{
            "year": year, "source_type": "NONE", "path": "", "source_rows": 0,
            "canonical_columns_resolved": "", "age_method": "", "death_method": "",
            "adult_18plus_s06_rows": 0, "adult_20plus_s06_rows": 0,
            "mortality_available_pct": 0.0, "hospital_id_available_pct": 0.0,
            "status": "EXCLUDED_NO_ANNUAL_CHECKPOINT",
            "selected_for_analysis": False,
        }])

    audits: List[Dict[str, Any]] = []
    resolved: List[Tuple[pd.DataFrame, Dict[str, Any]]] = []
    for source_type, candidate in candidates:
        try:
            frame, audit = _read_mexico_candidate_v280(candidate, year, source_type)
        except Exception as exc:
            frame = pd.DataFrame(columns=LEAN_COLUMNS)
            audit = {
                "year": year, "source_type": source_type, "path": str(candidate),
                "source_rows": 0, "canonical_columns_resolved": "", "age_method": "",
                "death_method": "", "adult_18plus_s06_rows": 0,
                "adult_20plus_s06_rows": 0, "mortality_available_pct": 0.0,
                "hospital_id_available_pct": 0.0,
                "status": f"EXCLUDED_READ_ERROR:{type(exc).__name__}:{exc}",
            }
        audits.append(audit)
        resolved.append((frame, audit))

    def candidate_score(item: Tuple[pd.DataFrame, Dict[str, Any]]) -> Tuple[int, float, float, int]:
        frame, audit = item
        included = int(str(audit["status"]).startswith("INCLUDED"))
        return (
            included,
            float(audit.get("mortality_available_pct", 0)),
            float(audit.get("hospital_id_available_pct", 0)),
            int(audit.get("adult_18plus_s06_rows", 0)),
        )

    best_frame, best_audit = max(resolved, key=candidate_score)
    for frame, audit in resolved:
        audit["selected_for_analysis"] = bool(audit is best_audit and str(audit["status"]).startswith("INCLUDED"))
        if frame is not best_frame:
            del frame
    if not str(best_audit["status"]).startswith("INCLUDED"):
        best_frame = pd.DataFrame(columns=LEAN_COLUMNS)
    return best_frame, pd.DataFrame(audits)


def audit_mexico_coverage_v280(base_dir: Path | str = DEFAULT_BASE) -> pd.DataFrame:
    activate_v280(base_dir)
    rows: List[pd.DataFrame] = []
    clean_path = Path(base_dir) / "01_intermediate" / "mexico" / "mexico_clean.parquet"
    clean_year_counts: Dict[int, int] = {}
    if clean_path.exists() and "year" in set(parquet_columns(clean_path)):
        clean = pd.read_parquet(clean_path, columns=["year"])
        clean_year_counts = pd.to_numeric(clean["year"], errors="coerce").value_counts().astype(int).to_dict()
        del clean
        collect_memory()
    for year in PRIMARY_YEARS:
        frame, audit = resolve_mexico_year_v280(base_dir, year)
        audit["rows_in_mexico_clean"] = int(clean_year_counts.get(year, 0))
        rows.append(audit)
        del frame, audit
        collect_memory()
    result = pd.concat(rows, ignore_index=True, sort=False)
    save_table(result, PATHS.qc / "Mexico_year_coverage_audit_v280")

    early = result[result["year"].isin([2015, 2016, 2017])]
    included = early[early["selected_for_analysis"].fillna(False)]
    if included.empty:
        rationale = """# Mexico 2015–2017 analytic exclusion rationale\n\nAnnual S06 checkpoints were present, but the v2.8 schema-recovery audit did not identify a candidate with simultaneously usable adult age and in-hospital outcome information. Full annual checkpoints were examined when available. These years were therefore excluded rather than assigning ages, outcomes, or hospital identifiers by assumption. Mexico contributes 2018–2023 to the primary analysis.\n"""
    else:
        years = ", ".join(map(str, sorted(included["year"].astype(int).unique())))
        rationale = f"# Mexico early-year recovery\n\nThe schema-recovery audit identified usable individual discharge records for: {years}. These years were included according to the same age and outcome rules used for later Mexico data.\n"
    rationale_path = PATHS.manuscript / "Mexico_2015_2017_data_resolution_v280.md"
    rationale_path.parent.mkdir(parents=True, exist_ok=True)
    rationale_path.write_text(rationale, encoding="utf-8")
    return result


def build_mexico_lean_v280(base_dir: Path | str = DEFAULT_BASE) -> pd.DataFrame:
    frames: List[pd.DataFrame] = []
    audit_frames: List[pd.DataFrame] = []
    for year in PRIMARY_YEARS:
        resolved, audit = resolve_mexico_year_v280(base_dir, year)
        audit_frames.append(audit)
        selected = audit[audit["selected_for_analysis"].fillna(False)]
        if resolved.empty or selected.empty:
            reason = "; ".join(audit["status"].astype(str).tolist())
            _log(f"Mexico {year}: excluded after schema-recovery audit — {reason}", "WARNING")
        else:
            frames.append(resolved)
            selected_status = selected.iloc[0]["status"]
            _log(f"Mexico {year}: {len(resolved):,} adult S06 records retained ({selected_status})")
        collect_memory()
    audit_result = pd.concat(audit_frames, ignore_index=True, sort=False)
    save_table(audit_result, PATHS.qc / "Mexico_year_coverage_audit_v280")
    if not frames:
        fallback = Path(base_dir) / "01_intermediate" / "mexico" / "mexico_clean.parquet"
        if not fallback.exists():
            raise FileNotFoundError("No analyzable Mexico annual checkpoints or mexico_clean.parquet found")
        frame = read_parquet_selected(fallback, LEAN_COLUMNS)
        return normalize_country_frame(frame, "mexico")
    result = pd.concat(frames, ignore_index=True, sort=False)
    del frames, audit_frames
    collect_memory()
    return result


# -----------------------------------------------------------------------------
# Final v2.8 data preparation
# -----------------------------------------------------------------------------


def checkpoint_map_v280(base_dir: Path | str = DEFAULT_BASE) -> Dict[str, Path]:
    base = Path(base_dir)
    return {
        "brasil": base / "01_intermediate" / "brasil" / "brasil_clean.parquet",
        "chile": base / "01_intermediate" / "chile" / "chile_clean_v260.parquet",
        "equador": base / "01_intermediate" / "equador" / "equador_clean_v240.parquet",
    }


def build_lean_country_parts_v280(
    base_dir: Path | str = DEFAULT_BASE,
    rebuild_chile: bool = False,
) -> Dict[str, str]:
    activate_v280(base_dir)
    if rebuild_chile or not checkpoint_map_v280(base_dir)["chile"].exists():
        rebuild_chile_intermediate_v260(base_dir, force=True)
    audit_mexico_coverage_v280(base_dir)
    outputs: Dict[str, str] = {}
    audit_rows: List[Dict[str, Any]] = []
    for country in COUNTRY_ORDER:
        if country == "mexico":
            frame = build_mexico_lean_v280(base_dir)
            source_description = "Annual Mexico analytic checkpoints preferred"
        else:
            source = checkpoint_map_v280(base_dir)[country]
            if not source.exists():
                raise FileNotFoundError(f"Missing {country} checkpoint: {source}")
            requested = list(dict.fromkeys(LEAN_COLUMNS + [
                "procedure_group_v2", "procedure_mapping_confidence", "primary_acute_surgery",
                "age_band_common", "age_lower", "age_upper", "age_exact_available",
            ]))
            raw = read_parquet_selected(source, requested)
            if "procedure_group" not in raw and "procedure_group_v2" in raw:
                raw["procedure_group"] = raw["procedure_group_v2"]
            frame = normalize_country_frame(raw, country)
            source_description = str(source)
            del raw
        target = PATHS.data / f"lean_{country}_v280.parquet"
        write_parquet(frame, target)
        outputs[country] = str(target)
        year_counts = pd.to_numeric(frame["year"], errors="coerce").value_counts().sort_index()
        audit_rows.append({
            "country": country,
            "source": source_description,
            "records_18plus_available": int(len(frame)),
            "primary_20plus": int(frame["primary_sample_20plus"].fillna(0).sum()),
            "years": ",".join(map(str, year_counts.index.astype(int).tolist())),
            "year_counts_json": json.dumps({str(int(k)): int(v) for k, v in year_counts.items()}),
            "unique_hospitals": int(frame["hospital_id"].nunique(dropna=True)),
        })
        _log(f"Lean v2.8 {country}: {len(frame):,} rows -> {target}")
        del frame
        collect_memory()
        log_memory(f"after v2.8 lean {country}")
    save_table(pd.DataFrame(audit_rows), PATHS.qc / "Country_checkpoint_audit_v280")
    return outputs


def _zscore_valid(series: pd.Series) -> pd.Series:
    return _zscore(series)


def build_hospital_year_v280(base_dir: Path | str = DEFAULT_BASE) -> Path:
    activate_v280(base_dir)
    units: List[pd.DataFrame] = []
    for country in VOLUME_COUNTRIES:
        path = PATHS.data / f"lean_{country}_v280.parquet"
        frame = pd.read_parquet(path, columns=["country", "hospital_id", "year", "primary_sample_20plus"])
        frame = frame[frame["primary_sample_20plus"].eq(1) & frame["hospital_id"].notna()]
        grouped = frame.groupby(["country", "hospital_id", "year"], observed=True).size().rename("hospital_volume_year").reset_index()
        units.append(grouped)
        del frame, grouped
        collect_memory()
    hy = pd.concat(units, ignore_index=True, sort=False).sort_values(["country", "hospital_id", "year"]).reset_index(drop=True)
    hy["hospital_volume_year"] = pd.to_numeric(hy["hospital_volume_year"], errors="coerce").astype("Int64")
    hy["log_volume"] = np.log1p(pd.to_numeric(hy["hospital_volume_year"], errors="coerce"))
    hy["volume_z_country_year"] = hy.groupby(["country", "year"], observed=True)["log_volume"].transform(_zscore_valid)
    hy["volume_quartile"] = hy.groupby(["country", "year"], observed=True)["hospital_volume_year"].transform(lambda x: _quantile_labels(x, 4, "Q"))
    hy["volume_decile"] = hy.groupby(["country", "year"], observed=True)["hospital_volume_year"].transform(lambda x: _quantile_labels(x, 10, "D"))
    hy["previous_year"] = hy.groupby(["country", "hospital_id"], observed=True)["year"].shift(1)
    raw_lag = hy.groupby(["country", "hospital_id"], observed=True)["hospital_volume_year"].shift(1)
    contiguous = pd.to_numeric(hy["year"], errors="coerce") - pd.to_numeric(hy["previous_year"], errors="coerce") == 1
    hy["lag_volume"] = raw_lag.where(contiguous)
    hy["log_lag_volume"] = np.log1p(pd.to_numeric(hy["lag_volume"], errors="coerce"))
    hy["lag_volume_z_country_year"] = hy.groupby(["country", "year"], observed=True)["log_lag_volume"].transform(_zscore_valid)
    hy.drop(columns=["previous_year"], inplace=True)
    target = PATHS.data / "hospital_year_v280.parquet"
    write_parquet(hy, target)
    save_table(hy.groupby(["country", "year"], observed=True).agg(
        hospital_year_units=("hospital_id", "size"),
        hospitals=("hospital_id", "nunique"),
        admissions=("hospital_volume_year", "sum"),
        lag_available=("lag_volume", lambda x: int(x.notna().sum())),
    ).reset_index(), PATHS.qc / "Hospital_year_validation_v280")
    _log(f"Hospital-year v2.8: {len(hy):,} units")
    del hy, units
    collect_memory()
    return target


def build_analysis_cohort_parts_v280(base_dir: Path | str = DEFAULT_BASE) -> Dict[str, str]:
    activate_v280(base_dir)
    hy_path = build_hospital_year_v280(base_dir)
    hy = pd.read_parquet(hy_path)
    outputs: Dict[str, str] = {}
    validation: List[Dict[str, Any]] = []
    for country in COUNTRY_ORDER:
        lean_path = PATHS.data / f"lean_{country}_v280.parquet"
        lean = pd.read_parquet(lean_path)
        primary = lean[lean["primary_sample_20plus"].eq(1)].copy()
        sensitivity = lean[lean["sensitivity_sample_18plus"].eq(1)].copy()
        if country in VOLUME_COUNTRIES:
            chy = hy[hy["country"].eq(country)].copy()
            primary = primary.merge(chy, on=["country", "hospital_id", "year"], how="left", validate="many_to_one")
            sensitivity = sensitivity.merge(chy, on=["country", "hospital_id", "year"], how="left", validate="many_to_one")
            del chy
        else:
            for column in VOLUME_COLUMNS:
                primary[column] = pd.NA
                sensitivity[column] = pd.NA
        ptarget = PATHS.data / f"cohort_main_{country}_v280.parquet"
        starget = PATHS.data / f"cohort_sensitivity18_{country}_v280.parquet"
        write_parquet(primary, ptarget)
        write_parquet(sensitivity, starget)
        outputs[country] = str(ptarget)
        validation.append({
            "country": country,
            "primary_records": len(primary),
            "sensitivity_records": len(sensitivity),
            "years": ",".join(map(str, sorted(pd.to_numeric(primary["year"], errors="coerce").dropna().astype(int).unique()))),
            "mortality_available_pct": round(100 * primary["death_in_hospital"].notna().mean(), 3),
            "los_available_pct": round(100 * primary["los_days"].notna().mean(), 3),
            "unique_hospitals": int(primary["hospital_id"].nunique(dropna=True)),
        })
        if country == "brasil":
            strict = primary[primary["procedure_group"].isin(["DECOMPRESSIVE_CODED", "ACUTE_CRANIAL_SURGERY"])].copy()
            strict["procedure_class_analysis"] = np.where(strict["procedure_group"].eq("DECOMPRESSIVE_CODED"), "Decompressive-coded", "Other acute cranial surgery")
            broad = primary[primary["procedure_group"].isin([
                "DECOMPRESSIVE_CODED", "ACUTE_CRANIAL_SURGERY", "CHRONIC_SDH_SURGERY",
                "ICP_MONITORING_OR_TREPANATION", "GENERIC_NEUROSURGERY",
            ])].copy()
            write_parquet(strict, PATHS.data / "cohort_surgical_strict_brazil_v280.parquet")
            write_parquet(broad, PATHS.data / "cohort_surgical_broad_brazil_v280.parquet")
            del strict, broad
        del lean, primary, sensitivity
        collect_memory()
        log_memory(f"after v2.8 cohort {country}")
    del hy
    collect_memory()
    save_table(pd.DataFrame(validation), PATHS.qc / "Final_cohort_validation_v280")
    return outputs


def cohort_path_v280(country: str, sensitivity18: bool = False) -> Path:
    prefix = "cohort_sensitivity18" if sensitivity18 else "cohort_main"
    return PATHS.data / f"{prefix}_{country}_v280.parquet"


def load_cohort_columns_v280(
    countries: Sequence[str],
    columns: Sequence[str],
    sensitivity18: bool = False,
) -> pd.DataFrame:
    frames: List[pd.DataFrame] = []
    for country in countries:
        path = cohort_path_v280(country, sensitivity18=sensitivity18)
        available = set(parquet_columns(path))
        selected = [c for c in columns if c in available]
        frame = pd.read_parquet(path, columns=selected)
        for column in columns:
            if column not in frame:
                frame[column] = pd.NA
        frames.append(frame[list(columns)])
    result = pd.concat(frames, ignore_index=True, sort=False)
    del frames
    return result

# Route legacy summary helpers to v2.8 data partitions at runtime.
cohort_path = cohort_path_v280
load_cohort_columns = load_cohort_columns_v280


# -----------------------------------------------------------------------------
# Availability and code-label audit
# -----------------------------------------------------------------------------

AVAILABILITY_VARIABLES_V280 = [
    "age", "age_band_common", "sex", "dx_main", "dx_secondary", "trauma_subtype",
    "death_in_hospital", "los_days", "hospital_id", "hospital_region", "residence_region",
    "transfer_proxy", "icu_any", "icu_days", "procedure_code_raw", "external_cause",
    "insurance_type", "ethnicity", "any_surgical_intervention", "facility_sector",
    "facility_class", "facility_type", "facility_entity", "residence_area",
    "discharge_specialty", "bed_total_available", "bed_icu_normal",
]


def informative_mask_v280(series: pd.Series) -> pd.Series:
    if pd.api.types.is_numeric_dtype(series.dtype):
        return pd.to_numeric(series, errors="coerce").notna()
    return _meaningful_text(series).notna()


def availability_audit_v280() -> Tuple[pd.DataFrame, pd.DataFrame]:
    country_rows: List[Dict[str, Any]] = []
    year_rows: List[Dict[str, Any]] = []
    for country in COUNTRY_ORDER:
        path = cohort_path_v280(country)
        available_columns = set(parquet_columns(path))
        selected = ["year"] + [v for v in AVAILABILITY_VARIABLES_V280 if v in available_columns]
        frame = pd.read_parquet(path, columns=selected)
        for variable in AVAILABILITY_VARIABLES_V280:
            if variable not in frame:
                frame[variable] = pd.NA
            mask = informative_mask_v280(frame[variable])
            meaningful = frame.loc[mask, variable]
            unique = int(meaningful.astype(str).nunique()) if len(meaningful) else 0
            pct = 100 * float(mask.mean()) if len(frame) else np.nan
            years_informative = 0
            year_statuses: List[str] = []
            for year, sub in frame.groupby("year", observed=True):
                ym = informative_mask_v280(sub[variable])
                yunique = int(sub.loc[ym, variable].astype(str).nunique()) if ym.any() else 0
                ypct = 100 * float(ym.mean()) if len(sub) else np.nan
                if not ym.any():
                    status = "Structurally unavailable"
                elif yunique <= 1:
                    status = "Present but non-informative/constant"
                elif ypct < 50:
                    status = "Low completeness"
                elif ypct < 90:
                    status = "Partially available"
                else:
                    status = "Available and informative"
                    years_informative += 1
                year_statuses.append(status)
                year_rows.append({
                    "country": country,
                    "year": int(year),
                    "variable": variable,
                    "records": len(sub),
                    "informative_records": int(ym.sum()),
                    "informative_pct": round(ypct, 3),
                    "unique_informative_levels": yunique,
                    "status": status,
                })
            if not mask.any():
                summary_status = "Structurally unavailable"
            elif unique <= 1:
                summary_status = "Present but non-informative/constant"
            elif years_informative == 0:
                summary_status = "Insufficient for modeling"
            elif years_informative < frame["year"].nunique():
                summary_status = "Year-limited availability"
            elif pct < 90:
                summary_status = "Partially available"
            else:
                summary_status = "Available and informative"
            country_rows.append({
                "country": country,
                "variable": variable,
                "records": len(frame),
                "informative_records": int(mask.sum()),
                "informative_pct": round(pct, 3),
                "unique_informative_levels": unique,
                "informative_years": years_informative,
                "total_years": int(frame["year"].nunique()),
                "status": summary_status,
            })
        del frame
        collect_memory()
    summary = pd.DataFrame(country_rows)
    by_year = pd.DataFrame(year_rows)
    save_table(summary, PATHS.tables / "Supplementary_Table_1_Informative_variable_availability")
    save_table(by_year, PATHS.qc / "Variable_availability_by_country_year_v280")
    return summary, by_year


ECUADOR_CANONICAL_ALIASES_V280: Dict[str, Sequence[str]] = {
    "facility_sector": ["sector"],
    "facility_class": ["clase"],
    "facility_type": ["tipo"],
    "facility_entity": ["entidad"],
    "ethnicity": ["etnia"],
    "residence_area": ["area_res", "area_residencia"],
    "discharge_specialty": ["esp_egrpa", "especialidad_egreso"],
}


def extract_ecuador_value_labels_v280(base_dir: Path | str = DEFAULT_BASE) -> pd.DataFrame:
    activate_v280(base_dir)
    rows: List[Dict[str, Any]] = []
    try:
        import pyreadstat
    except Exception:
        _log("pyreadstat unavailable; Ecuador labels will remain coded", "WARNING")
        empty = pd.DataFrame(columns=["year", "canonical_variable", "raw_variable", "code", "label", "source_file"])
        save_table(empty, PATHS.qc / "Ecuador_value_labels_v280")
        return empty
    root = Path(base_dir) / "00_raw" / "equador"
    sav_files = [p for p in root.rglob("*.sav") if "egres" in ascii_text(p.name)]
    for path in sorted(sav_files):
        match = re.search(r"(20\d{2})", str(path))
        year = int(match.group(1)) if match else None
        if year not in PRIMARY_YEARS:
            continue
        try:
            _, meta = pyreadstat.read_sav(str(path), metadataonly=True)
        except Exception as exc:
            _log(f"Ecuador metadata labels failed for {path.name}: {exc}", "WARNING")
            continue
        available = {ascii_text(name): name for name in meta.column_names}
        value_labels = getattr(meta, "variable_value_labels", {}) or {}
        for canonical, aliases in ECUADOR_CANONICAL_ALIASES_V280.items():
            raw_name = None
            for alias in aliases:
                if ascii_text(alias) in available:
                    raw_name = available[ascii_text(alias)]
                    break
            if raw_name is None:
                continue
            mapping = value_labels.get(raw_name, {}) or {}
            for code, label in mapping.items():
                rows.append({
                    "year": year,
                    "canonical_variable": canonical,
                    "raw_variable": raw_name,
                    "code": str(code).rstrip("0").rstrip(".") if isinstance(code, float) else str(code),
                    "label": str(label),
                    "source_file": str(path),
                })
    result = pd.DataFrame(rows).drop_duplicates()
    save_table(result, PATHS.qc / "Ecuador_value_labels_v280")
    return result


def apply_ecuador_labels_v280(frame: pd.DataFrame, labels: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    if labels.empty:
        return result
    for variable in labels["canonical_variable"].dropna().unique():
        if variable not in result:
            continue
        result[f"{variable}_label"] = pd.NA
        subset = labels[labels["canonical_variable"].eq(variable)]
        for year, year_map in subset.groupby("year", observed=True):
            mapping = dict(zip(year_map["code"].astype(str), year_map["label"].astype(str)))
            mask = pd.to_numeric(result["year"], errors="coerce").eq(int(year))
            codes = as_string(result.loc[mask, variable]).str.replace(r"\.0+$", "", regex=True)
            mapped = codes.map(mapping)
            result.loc[mask, f"{variable}_label"] = mapped.where(mapped.notna(), "Code " + codes)
    return result


def eligible_years_for_predictor_v280(frame: pd.DataFrame, predictor: str, minimum_completeness: float = 0.70) -> List[int]:
    eligible: List[int] = []
    for year, sub in frame.groupby("year", observed=True):
        meaningful = _meaningful_text(sub[predictor]) if not pd.api.types.is_numeric_dtype(sub[predictor]) else pd.to_numeric(sub[predictor], errors="coerce")
        mask = meaningful.notna()
        if mask.mean() >= minimum_completeness and meaningful.loc[mask].astype(str).nunique() >= 2:
            eligible.append(int(year))
    return eligible

# -----------------------------------------------------------------------------
# Risk standardization, hierarchical shrinkage, and within-hospital analyses
# -----------------------------------------------------------------------------


def _auc_rank(y: pd.Series, score: pd.Series) -> float:
    yv = pd.to_numeric(y, errors="coerce").to_numpy()
    sv = pd.to_numeric(score, errors="coerce").to_numpy()
    valid = np.isfinite(yv) & np.isfinite(sv) & np.isin(yv, [0, 1])
    yv, sv = yv[valid], sv[valid]
    n1 = int((yv == 1).sum())
    n0 = int((yv == 0).sum())
    if n1 == 0 or n0 == 0:
        return np.nan
    ranks = pd.Series(sv).rank(method="average").to_numpy()
    return float((ranks[yv == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def _calibration_metrics(y: pd.Series, p: pd.Series) -> Dict[str, float]:
    yv = pd.to_numeric(y, errors="coerce").astype(float)
    pv = pd.to_numeric(p, errors="coerce").astype(float).clip(1e-6, 1 - 1e-6)
    valid = yv.isin([0, 1]) & pv.notna()
    yv, pv = yv.loc[valid], pv.loc[valid]
    if len(yv) == 0:
        return {"auc": np.nan, "brier": np.nan, "calibration_intercept": np.nan, "calibration_slope": np.nan}
    logit = np.log(pv / (1 - pv))
    try:
        intercept_fit = sm.GLM(yv, np.ones((len(yv), 1)), family=sm.families.Binomial(), offset=logit).fit()
        calibration_intercept = float(intercept_fit.params[0])
    except Exception:
        calibration_intercept = np.nan
    try:
        slope_fit = sm.GLM(yv, sm.add_constant(logit), family=sm.families.Binomial()).fit()
        calibration_slope = float(slope_fit.params.iloc[-1] if hasattr(slope_fit.params, "iloc") else slope_fit.params[-1])
    except Exception:
        calibration_slope = np.nan
    return {
        "auc": _auc_rank(yv, pv),
        "brier": float(np.mean((yv.to_numpy() - pv.to_numpy()) ** 2)),
        "calibration_intercept": calibration_intercept,
        "calibration_slope": calibration_slope,
    }


def fit_risk_model_hospital_year_v280(country: str) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    columns = [
        "country", "year", "age", "sex", "trauma_subtype", "death_in_hospital",
        "hospital_id", "hospital_region", "transfer_proxy", "hospital_volume_year",
        "lag_volume", "log_volume", "log_lag_volume",
    ]
    path = cohort_path_v280(country)
    available = set(parquet_columns(path))
    data = pd.read_parquet(path, columns=[c for c in columns if c in available])
    for col in columns:
        if col not in data:
            data[col] = pd.NA
    data = native_model_frame(
        data,
        numeric=["age", "death_in_hospital", "hospital_volume_year", "lag_volume", "log_volume", "log_lag_volume", "transfer_proxy"],
        categorical=["year", "sex", "trauma_subtype", "hospital_id", "hospital_region"],
    )
    required = ["age", "death_in_hospital", "year", "sex", "trauma_subtype", "hospital_id"]
    data = data.dropna(subset=required)
    data = data[data["death_in_hospital"].isin([0, 1])]
    terms = [
        "bs(age, df=4, degree=3, include_intercept=False)",
        "C(sex)", "C(trauma_subtype)", "C(year)",
    ]
    if data["hospital_region"].notna().mean() >= 0.8 and data["hospital_region"].nunique() > 1:
        terms.append("C(hospital_region)")
    formula = "death_in_hospital ~ " + " + ".join(terms)
    fit = smf.glm(formula=formula, data=data, family=sm.families.Binomial()).fit(maxiter=150)
    data["expected_probability"] = np.asarray(fit.predict(data), dtype=float)
    data["expected_variance"] = data["expected_probability"] * (1 - data["expected_probability"])
    calibration = _calibration_metrics(data["death_in_hospital"], data["expected_probability"])
    grouped = data.groupby(["country", "hospital_id", "year"], observed=True).agg(
        admissions=("death_in_hospital", "size"),
        observed_deaths=("death_in_hospital", "sum"),
        expected_deaths=("expected_probability", "sum"),
        expected_variance=("expected_variance", "sum"),
        hospital_volume_year=("hospital_volume_year", "first"),
        lag_volume=("lag_volume", "first"),
        log_volume=("log_volume", "first"),
        log_lag_volume=("log_lag_volume", "first"),
    ).reset_index()
    grouped["observed_mortality"] = grouped["observed_deaths"] / grouped["admissions"]
    grouped["expected_mortality"] = grouped["expected_deaths"] / grouped["admissions"]
    grouped["risk_difference"] = grouped["observed_mortality"] - grouped["expected_mortality"]
    grouped["smr"] = grouped["observed_deaths"] / grouped["expected_deaths"].replace(0, np.nan)
    diagnostics = {
        "country": country,
        "analysis": "Patient-level mortality risk model for standardization",
        "n": int(fit.nobs),
        "events": int(data["death_in_hospital"].sum()),
        "hospitals": int(data["hospital_id"].nunique()),
        "aic": float(fit.aic),
        "formula": formula,
        **calibration,
    }
    target = PATHS.data / f"hospital_year_risk_{country}_v280.parquet"
    write_parquet(grouped, target)
    del data, fit
    collect_memory()
    return grouped, diagnostics


def empirical_bayes_hospital_standardization_v280(hy: pd.DataFrame, country: str) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    hospital = hy.groupby(["country", "hospital_id"], observed=True).agg(
        admissions=("admissions", "sum"),
        observed_deaths=("observed_deaths", "sum"),
        expected_deaths=("expected_deaths", "sum"),
        hospital_years=("year", "nunique"),
    ).reset_index()
    o = pd.to_numeric(hospital["observed_deaths"], errors="coerce").astype(float)
    e = pd.to_numeric(hospital["expected_deaths"], errors="coerce").astype(float)
    y = np.log((o + 0.5) / (e + 0.5))
    v = 1.0 / (o + 0.5)
    w = 1.0 / v
    fixed_mean = float(np.sum(w * y) / np.sum(w))
    q = float(np.sum(w * (y - fixed_mean) ** 2))
    c = float(np.sum(w) - np.sum(w**2) / np.sum(w))
    tau2 = max(0.0, (q - (len(y) - 1)) / c) if c > 0 and len(y) > 1 else 0.0
    wr = 1.0 / (v + tau2)
    mu = float(np.sum(wr * y) / np.sum(wr))
    shrinkage = tau2 / (tau2 + v) if tau2 > 0 else np.zeros_like(v)
    posterior = shrinkage * y + (1 - shrinkage) * mu
    overall_rate = float(o.sum() / hospital["admissions"].sum())
    hospital["observed_mortality"] = o / hospital["admissions"]
    hospital["smr"] = o / e.replace(0, np.nan)
    hospital["posterior_log_smr"] = posterior
    hospital["shrinkage_weight"] = shrinkage
    hospital["risk_standardized_mortality"] = np.exp(posterior) * overall_rate
    hospital["expected_deaths_for_funnel"] = e
    hospital["outside_95pct"] = 0
    hospital["outside_998pct"] = 0
    lower95 = poisson.ppf(0.025, e) / e.replace(0, np.nan)
    upper95 = poisson.ppf(0.975, e) / e.replace(0, np.nan)
    lower998 = poisson.ppf(0.001, e) / e.replace(0, np.nan)
    upper998 = poisson.ppf(0.999, e) / e.replace(0, np.nan)
    hospital.loc[(hospital["smr"] < lower95) | (hospital["smr"] > upper95), "outside_95pct"] = 1
    hospital.loc[(hospital["smr"] < lower998) | (hospital["smr"] > upper998), "outside_998pct"] = 1
    mor = float(np.exp(0.67448975 * np.sqrt(2 * tau2))) if tau2 > 0 else 1.0
    heterogeneity = {
        "country": country,
        "hospitals": len(hospital),
        "overall_mortality": overall_rate,
        "tau_squared_log_smr": tau2,
        "median_rate_ratio_approx": mor,
        "outside_95pct": int(hospital["outside_95pct"].sum()),
        "outside_998pct": int(hospital["outside_998pct"].sum()),
        "method": "Empirical-Bayes random-effects shrinkage of log observed/expected mortality",
    }
    return hospital, heterogeneity


def _weighted_group_mean(values: pd.Series, groups: pd.Series, weights: pd.Series) -> pd.Series:
    numerator = (values * weights).groupby(groups, observed=True).transform("sum")
    denominator = weights.groupby(groups, observed=True).transform("sum")
    return numerator / denominator.replace(0, np.nan)


def weighted_two_way_residual_v280(
    values: pd.Series,
    group1: pd.Series,
    group2: pd.Series,
    weights: pd.Series,
    max_iter: int = 100,
    tolerance: float = 1e-10,
) -> pd.Series:
    residual = pd.to_numeric(values, errors="coerce").astype(float).copy()
    weights = pd.to_numeric(weights, errors="coerce").astype(float)
    for _ in range(max_iter):
        previous = residual.copy()
        residual = residual - _weighted_group_mean(residual, group1, weights)
        residual = residual - _weighted_group_mean(residual, group2, weights)
        difference = np.nanmax(np.abs((residual - previous).to_numpy()))
        if np.isfinite(difference) and difference < tolerance:
            break
    return residual


def within_hospital_models_v280(hospital_year_frames: Mapping[str, pd.DataFrame]) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    for country, source in hospital_year_frames.items():
        panel = source.dropna(subset=["risk_difference", "log_lag_volume", "hospital_id", "year", "admissions"]).copy()
        panel = panel[panel["admissions"].ge(5)]
        eligible_hospitals = panel.groupby("hospital_id", observed=True)["year"].nunique()
        panel = panel[panel["hospital_id"].isin(eligible_hospitals[eligible_hospitals >= 2].index)]
        if len(panel) < 100:
            continue
        panel["year_factor"] = panel["year"].astype(str)
        x_res = weighted_two_way_residual_v280(panel["log_lag_volume"], panel["hospital_id"], panel["year_factor"], panel["admissions"])
        y_res = weighted_two_way_residual_v280(panel["risk_difference"], panel["hospital_id"], panel["year_factor"], panel["admissions"])
        weighted_sd = math.sqrt(float(np.average(x_res**2, weights=panel["admissions"])))
        if not np.isfinite(weighted_sd) or weighted_sd <= 0:
            continue
        panel["x_within_z"] = x_res / weighted_sd
        panel["y_within"] = y_res
        try:
            fit = sm.WLS(panel["y_within"], panel[["x_within_z"]], weights=panel["admissions"]).fit(
                cov_type="cluster", cov_kwds={"groups": np.asarray(panel["hospital_id"], dtype=object)}
            )
            beta = float(fit.params["x_within_z"])
            ci = fit.conf_int().loc["x_within_z"]
            rows.append({
                "analysis": f"Within-hospital two-way fixed-effects risk-difference model: {COUNTRY_DISPLAY[country]}",
                "model_role": "key sensitivity",
                "multiplicity_family": "within_hospital",
                "term": "Within-hospital 1-SD change in prior-year log volume",
                "effect_measure": "Absolute mortality difference, percentage points",
                "estimate": 100 * beta,
                "ci_low": 100 * float(ci.iloc[0]),
                "ci_high": 100 * float(ci.iloc[1]),
                "p_value": float(fit.pvalues["x_within_z"]),
                "n": int(fit.nobs),
                "hospitals": int(panel["hospital_id"].nunique()),
                "converged": True,
            })
            del fit
        except Exception as exc:
            _log(f"Within-hospital FE failed for {country}: {exc}", "ERROR")
        panel = panel.sort_values(["hospital_id", "year"])
        panel["previous_year"] = panel.groupby("hospital_id", observed=True)["year"].shift(1)
        contiguous = pd.to_numeric(panel["year"], errors="coerce") - pd.to_numeric(panel["previous_year"], errors="coerce") == 1
        panel["delta_risk"] = panel.groupby("hospital_id", observed=True)["risk_difference"].diff().where(contiguous)
        panel["delta_log_volume"] = panel.groupby("hospital_id", observed=True)["log_volume"].diff().where(contiguous)
        diff = panel.dropna(subset=["delta_risk", "delta_log_volume"]).copy()
        if len(diff) >= 100 and diff["delta_log_volume"].std(ddof=0) > 0:
            sd = float(diff["delta_log_volume"].std(ddof=0))
            diff["delta_log_volume_z"] = diff["delta_log_volume"] / sd
            try:
                fit = sm.WLS(
                    diff["delta_risk"], sm.add_constant(diff["delta_log_volume_z"]), weights=diff["admissions"]
                ).fit(cov_type="cluster", cov_kwds={"groups": np.asarray(diff["hospital_id"], dtype=object)})
                ci = fit.conf_int().loc["delta_log_volume_z"]
                rows.append({
                    "analysis": f"First-difference hospital-year risk-difference model: {COUNTRY_DISPLAY[country]}",
                    "model_role": "supporting sensitivity",
                    "multiplicity_family": "within_hospital",
                    "term": "1-SD year-to-year change in log volume",
                    "effect_measure": "Absolute mortality difference, percentage points",
                    "estimate": 100 * float(fit.params["delta_log_volume_z"]),
                    "ci_low": 100 * float(ci.iloc[0]),
                    "ci_high": 100 * float(ci.iloc[1]),
                    "p_value": float(fit.pvalues["delta_log_volume_z"]),
                    "n": int(fit.nobs),
                    "hospitals": int(diff["hospital_id"].nunique()),
                    "converged": True,
                })
                del fit
            except Exception as exc:
                _log(f"First-difference model failed for {country}: {exc}", "ERROR")
        del panel, diff
        collect_memory()
    return apply_fdr_by_family_v280(pd.DataFrame(rows))


def run_risk_standardization_v280() -> Dict[str, Any]:
    hospital_year_frames: Dict[str, pd.DataFrame] = {}
    diagnostics: List[Dict[str, Any]] = []
    heterogeneity: List[Dict[str, Any]] = []
    hospital_outputs: List[pd.DataFrame] = []
    for country in VOLUME_COUNTRIES:
        hy, diag = fit_risk_model_hospital_year_v280(country)
        hospital, hetero = empirical_bayes_hospital_standardization_v280(hy, country)
        write_parquet(hospital, PATHS.data / f"hospital_risk_standardized_{country}_v280.parquet")
        hospital_year_frames[country] = hy
        hospital_outputs.append(hospital)
        diagnostics.append(diag)
        heterogeneity.append(hetero)
    within = within_hospital_models_v280(hospital_year_frames)
    save_table(within, PATHS.tables / "Table_9_Within_hospital_volume_models")
    combined_hospitals = pd.concat(hospital_outputs, ignore_index=True, sort=False)
    save_table(combined_hospitals, PATHS.tables / "Supplementary_Table_7_Hospital_risk_standardization")
    save_table(pd.DataFrame(diagnostics), PATHS.qc / "Mortality_risk_model_diagnostics_v280")
    save_table(pd.DataFrame(heterogeneity), PATHS.tables / "Table_8_Hospital_heterogeneity")
    del hospital_year_frames, hospital_outputs, combined_hospitals
    collect_memory()
    return {
        "within_hospital": str(PATHS.tables / "Table_9_Within_hospital_volume_models.csv"),
        "heterogeneity": str(PATHS.tables / "Table_8_Hospital_heterogeneity.csv"),
        "risk_standardized_hospitals": str(PATHS.tables / "Supplementary_Table_7_Hospital_risk_standardization.csv"),
    }

# -----------------------------------------------------------------------------
# Final hospital-volume models, nonlinear curves, and robust LOS models
# -----------------------------------------------------------------------------


def _prepare_volume_data_v280(sensitivity18: bool = False) -> pd.DataFrame:
    columns = [
        "country", "year", "age", "sex", "trauma_subtype", "death_in_hospital",
        "los_days", "hospital_id", "hospital_region", "transfer_proxy", "icu_any",
        "primary_acute_surgery", "volume_z_country_year", "log_volume",
        "lag_volume_z_country_year", "log_lag_volume", "hospital_volume_year", "lag_volume",
    ]
    data = load_cohort_columns_v280(VOLUME_COUNTRIES, columns, sensitivity18=sensitivity18)
    data["country_year"] = data["country"].astype(str) + "_" + data["year"].astype(str)
    return native_model_frame(
        data,
        numeric=[
            "age", "death_in_hospital", "los_days", "volume_z_country_year", "log_volume",
            "lag_volume_z_country_year", "log_lag_volume", "hospital_volume_year", "lag_volume",
            "transfer_proxy", "icu_any", "primary_acute_surgery",
        ],
        categorical=["country", "country_year", "year", "sex", "trauma_subtype", "hospital_id", "hospital_region"],
    )


def _standardized_prediction_curve_v280(
    fit: Any,
    data: pd.DataFrame,
    country: str,
    sample_size: int = 10000,
    grid_points: int = 40,
) -> pd.DataFrame:
    volumes = pd.to_numeric(data["lag_volume"], errors="coerce").dropna()
    lower = max(1.0, float(volumes.quantile(0.05)))
    upper = max(lower + 1.0, float(volumes.quantile(0.95)))
    grid = np.unique(np.round(np.geomspace(lower, upper, grid_points), 3))
    sample = data.sample(n=min(sample_size, len(data)), random_state=270).copy()
    design_info = fit.model.data.design_info
    beta = np.asarray(fit.params, dtype=float)
    covariance = np.asarray(fit.cov_params(), dtype=float)
    rows: List[Dict[str, Any]] = []
    for volume in grid:
        new_data = sample.copy()
        new_data["log_lag_volume"] = np.log1p(volume)
        design = np.asarray(build_design_matrices([design_info], new_data, return_type="dataframe")[0], dtype=float)
        eta = design @ beta
        probability = 1.0 / (1.0 + np.exp(-np.clip(eta, -35, 35)))
        mean_probability = float(np.mean(probability))
        gradient = np.mean((probability * (1 - probability))[:, None] * design, axis=0)
        variance = float(gradient @ covariance @ gradient)
        standard_error = math.sqrt(max(0.0, variance))
        rows.append({
            "country": country,
            "hospital_volume": float(volume),
            "predicted_mortality": mean_probability,
            "ci_low": max(0.0, mean_probability - 1.96 * standard_error),
            "ci_high": min(1.0, mean_probability + 1.96 * standard_error),
            "standardization_sample_n": len(sample),
        })
    del sample
    collect_memory()
    return pd.DataFrame(rows)


def fit_country_spline_v280(country: str) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    data = _prepare_volume_data_v280(False)
    data = data[data["country"].eq(country)].copy()
    required = ["death_in_hospital", "log_lag_volume", "lag_volume", "age", "sex", "trauma_subtype", "year", "hospital_id"]
    data = data.dropna(subset=required)
    data = data[data["death_in_hospital"].isin([0, 1])]
    spline_formula = (
        "death_in_hospital ~ cr(log_lag_volume, df=4) + "
        "bs(age, df=4, degree=3, include_intercept=False) + C(sex) + C(trauma_subtype) + C(year)"
    )
    linear_formula = (
        "death_in_hospital ~ log_lag_volume + "
        "bs(age, df=4, degree=3, include_intercept=False) + C(sex) + C(trauma_subtype) + C(year)"
    )
    fit = smf.glm(spline_formula, data=data, family=sm.families.Binomial()).fit(
        cov_type="cluster", cov_kwds={"groups": np.asarray(data["hospital_id"], dtype=object)}, maxiter=150
    )
    linear = smf.glm(linear_formula, data=data, family=sm.families.Binomial()).fit(maxiter=150)
    lr = max(0.0, 2 * (float(fit.llf) - float(linear.llf)))
    df_difference = max(1, int(round(float(fit.df_model - linear.df_model))))
    nonlinearity_p = float(chi2.sf(lr, df_difference))
    curve = _standardized_prediction_curve_v280(fit, data, country)
    curve["nonlinearity_lr"] = lr
    curve["nonlinearity_df"] = df_difference
    curve["nonlinearity_p"] = nonlinearity_p
    diagnostics = {
        "country": country,
        "analysis": "Restricted cubic spline of prior-year hospital volume",
        "n": int(fit.nobs),
        "hospitals": int(data["hospital_id"].nunique()),
        "spline_aic": float(fit.aic),
        "linear_aic": float(linear.aic),
        "nonlinearity_lr": lr,
        "nonlinearity_df": df_difference,
        "nonlinearity_p": nonlinearity_p,
        "formula": spline_formula,
    }
    del fit, linear, data
    collect_memory()
    return curve, diagnostics


def run_final_volume_models_v280() -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    data = _prepare_volume_data_v280(False)
    base = "bs(age, df=4, degree=3, include_intercept=False) + C(sex) + C(trauma_subtype) + C(country_year)"
    rows: List[Dict[str, Any]] = []

    prior = data.dropna(subset=["death_in_hospital", "lag_volume_z_country_year", "age", "sex", "trauma_subtype", "country_year", "hospital_id"])
    prior = prior[prior["death_in_hospital"].isin([0, 1])]
    model_rows, _ = fit_glm_v280(
        prior,
        "death_in_hospital ~ lag_volume_z_country_year + " + base,
        sm.families.Binomial(),
        "Primary mortality: prior-year hospital TBI volume",
        "hospital_id", ["lag_volume_z_country_year"],
        "OR per 1-SD increase in prior-year log volume",
        "primary_volume", "primary",
    )
    rows += model_rows

    interaction_rows, _ = fit_glm_v280(
        prior,
        "death_in_hospital ~ lag_volume_z_country_year + lag_volume_z_country_year:C(country) + " + base,
        sm.families.Binomial(),
        "Primary heterogeneity: prior-year volume by country interaction",
        "hospital_id", ["lag_volume_z_country_year", "lag_volume_z_country_year:C(country)[T.mexico]"],
        "OR", "primary_volume", "primary interaction",
    )
    rows += interaction_rows

    same = data.dropna(subset=["death_in_hospital", "volume_z_country_year", "age", "sex", "trauma_subtype", "country_year", "hospital_id"])
    same = same[same["death_in_hospital"].isin([0, 1])]
    same_rows, _ = fit_glm_v280(
        same,
        "death_in_hospital ~ volume_z_country_year + " + base,
        sm.families.Binomial(),
        "Sensitivity mortality: same-year hospital TBI volume",
        "hospital_id", ["volume_z_country_year"],
        "OR per 1-SD increase in same-year log volume",
        "volume_sensitivity", "sensitivity",
    )
    rows += same_rows

    structural = prior[~prior["trauma_subtype"].isin(["CONCUSSION", "OTHER_OR_UNSPECIFIED"])].copy()
    structural_rows, _ = fit_glm_v280(
        structural,
        "death_in_hospital ~ lag_volume_z_country_year + " + base,
        sm.families.Binomial(),
        "Sensitivity mortality: structural intracranial injury subset",
        "hospital_id", ["lag_volume_z_country_year"],
        "OR per 1-SD increase in prior-year log volume",
        "volume_sensitivity", "severity sensitivity",
    )
    rows += structural_rows
    del structural

    for country in VOLUME_COUNTRIES:
        subset = prior[prior["country"].eq(country)].copy()
        country_base = "bs(age, df=4, degree=3, include_intercept=False) + C(sex) + C(trauma_subtype) + C(year)"
        country_rows, _ = fit_glm_v280(
            subset,
            "death_in_hospital ~ lag_volume_z_country_year + " + country_base,
            sm.families.Binomial(),
            f"Country-specific prior-year volume: {COUNTRY_DISPLAY[country]}",
            "hospital_id", ["lag_volume_z_country_year"],
            "OR per 1-SD increase in prior-year log volume",
            "country_volume", "country-specific",
        )
        rows += country_rows
        minimum = subset[pd.to_numeric(subset["hospital_volume_year"], errors="coerce").ge(5)].copy()
        min_rows, _ = fit_glm_v280(
            minimum,
            "death_in_hospital ~ lag_volume_z_country_year + " + country_base,
            sm.families.Binomial(),
            f"Sensitivity excluding hospital-years with <5 admissions: {COUNTRY_DISPLAY[country]}",
            "hospital_id", ["lag_volume_z_country_year"],
            "OR per 1-SD increase in prior-year log volume",
            "volume_sensitivity", "low-volume sensitivity",
        )
        rows += min_rows
        if country == "brasil":
            enhanced_terms = country_base
            if subset["icu_any"].notna().mean() >= 0.8 and subset["icu_any"].nunique(dropna=True) >= 2:
                enhanced_terms += " + C(icu_any)"
            if subset["primary_acute_surgery"].notna().mean() >= 0.8 and subset["primary_acute_surgery"].nunique(dropna=True) >= 2:
                enhanced_terms += " + C(primary_acute_surgery)"
            enhanced_rows, _ = fit_glm_v280(
                subset,
                "death_in_hospital ~ lag_volume_z_country_year + " + enhanced_terms,
                sm.families.Binomial(),
                "Post-admission severity sensitivity: Brazil with ICU/surgery indicators",
                "hospital_id", ["lag_volume_z_country_year"],
                "OR per 1-SD increase in prior-year log volume",
                "volume_sensitivity", "post-treatment sensitivity",
            )
            rows += enhanced_rows
        del subset, minimum
        collect_memory()

    # Age >=18 sensitivity where exact age is available. Chile is not part of volume models.
    sensitivity_data = _prepare_volume_data_v280(True)
    age18 = sensitivity_data.dropna(subset=["death_in_hospital", "lag_volume_z_country_year", "age", "sex", "trauma_subtype", "country_year", "hospital_id"])
    age18 = age18[age18["death_in_hospital"].isin([0, 1])]
    age18_rows, _ = fit_glm_v280(
        age18,
        "death_in_hospital ~ lag_volume_z_country_year + " + base,
        sm.families.Binomial(),
        "Sensitivity mortality: age >=18 in Brazil and Mexico",
        "hospital_id", ["lag_volume_z_country_year"],
        "OR per 1-SD increase in prior-year log volume",
        "volume_sensitivity", "age sensitivity",
    )
    rows += age18_rows
    del sensitivity_data, age18

    survivors = prior[prior["death_in_hospital"].eq(0) & prior["los_days"].ge(1)].copy()
    gamma_rows, _ = fit_glm_v280(
        survivors,
        "los_days ~ lag_volume_z_country_year + " + base,
        sm.families.Gamma(link=sm.families.links.Log()),
        "Survivor length of stay: Gamma log-link model",
        "hospital_id", ["lag_volume_z_country_year"],
        "Adjusted mean ratio",
        "los_models", "primary LOS model",
    )
    rows += gamma_rows
    nb_rows, _ = fit_glm_v280(
        survivors,
        "los_days ~ lag_volume_z_country_year + " + base,
        sm.families.NegativeBinomial(alpha=1.0, link=sm.families.links.Log()),
        "Survivor length of stay: negative-binomial sensitivity",
        "hospital_id", ["lag_volume_z_country_year"],
        "Adjusted mean ratio",
        "los_models", "LOS sensitivity",
    )
    rows += nb_rows
    del survivors, prior, same, data
    collect_memory()

    spline_frames: List[pd.DataFrame] = []
    spline_diagnostics: List[Dict[str, Any]] = []
    for country in VOLUME_COUNTRIES:
        curve, diagnostic = fit_country_spline_v280(country)
        spline_frames.append(curve)
        spline_diagnostics.append(diagnostic)
    curves = pd.concat(spline_frames, ignore_index=True, sort=False)
    diagnostics = pd.DataFrame(spline_diagnostics)
    save_table(curves, PATHS.tables / "Table_7_Adjusted_volume_spline_predictions")
    save_table(diagnostics, PATHS.qc / "Volume_spline_diagnostics_v280")
    result = apply_fdr_by_family_v280(pd.DataFrame(rows))
    save_table(result, PATHS.tables / "Table_6_Final_hospital_volume_models")
    return result, curves, diagnostics

# -----------------------------------------------------------------------------
# Individual factors, pandemic/event-study, and cleaned country-specific models
# -----------------------------------------------------------------------------


def run_individual_factor_models_v280() -> pd.DataFrame:
    columns = ["country", "year", "age_band_common", "sex", "trauma_subtype", "death_in_hospital", "hospital_id"]
    data = load_cohort_columns_v280(COUNTRY_ORDER, columns)
    data["country_year"] = data["country"].astype(str) + "_" + data["year"].astype(str)
    data = native_model_frame(
        data,
        numeric=["death_in_hospital"],
        categorical=["country", "country_year", "year", "age_band_common", "sex", "trauma_subtype", "hospital_id"],
    )
    data = data.dropna(subset=["death_in_hospital", "country_year", "age_band_common", "sex", "trauma_subtype"])
    data = data[data["death_in_hospital"].isin([0, 1])]
    levels = [x for x in ["20-29", "30-49", "50-69", "70-79", "80+"] if x in set(data["age_band_common"].astype(str))]
    reference = levels[0]
    formula = (
        f"death_in_hospital ~ C(age_band_common, Treatment(reference='{reference}')) + "
        "C(sex) + C(trauma_subtype) + C(country_year)"
    )
    rows, _ = fit_glm_v280(
        data, formula, sm.families.Binomial(),
        "Pooled individual-level factors across four countries", None, None,
        "Adjusted OR", "individual_factors", "pooled secondary",
    )
    rows = [r for r in rows if any(token in r["term"] for token in ("age_band_common", "C(sex)", "C(trauma_subtype)"))]
    for country in COUNTRY_ORDER:
        subset = data[data["country"].eq(country)].copy()
        available_levels = [x for x in levels if x in set(subset["age_band_common"].astype(str))]
        if not available_levels:
            continue
        ref = available_levels[0]
        country_formula = (
            f"death_in_hospital ~ C(age_band_common, Treatment(reference='{ref}')) + "
            "C(sex) + C(trauma_subtype) + C(year)"
        )
        cluster = "hospital_id" if country in VOLUME_COUNTRIES else None
        country_rows, _ = fit_glm_v280(
            subset, country_formula, sm.families.Binomial(),
            f"Country-specific individual factors: {COUNTRY_DISPLAY[country]}", cluster, None,
            "Adjusted OR", "individual_factors_by_country", "country-specific",
        )
        for row in country_rows:
            if any(token in row["term"] for token in ("age_band_common", "C(sex)", "C(trauma_subtype)")):
                row["country"] = country
                rows.append(row)
        del subset
        collect_memory()
    del data
    collect_memory()
    result = apply_fdr_by_family_v280(pd.DataFrame(rows))
    save_table(result, PATHS.tables / "Table_5_Individual_factor_models")
    return result


def run_pandemic_event_study_v280() -> Tuple[pd.DataFrame, pd.DataFrame]:
    countries = ("brasil", "mexico", "chile")
    columns = ["country", "year", "age_band_common", "sex", "trauma_subtype", "death_in_hospital", "hospital_id"]
    data = load_cohort_columns_v280(countries, columns)
    numeric_year = pd.to_numeric(data["year"], errors="coerce")
    data["pandemic_period"] = pd.cut(
        numeric_year, bins=[2014, 2019, 2021, 2023],
        labels=["Pre-pandemic", "Pandemic", "Recovery"], right=True,
    ).astype("string")
    data = native_model_frame(
        data,
        numeric=["death_in_hospital"],
        categorical=["country", "year", "pandemic_period", "age_band_common", "sex", "trauma_subtype", "hospital_id"],
    )
    data = data.dropna(subset=["death_in_hospital", "country", "pandemic_period", "age_band_common", "sex", "trauma_subtype"])
    data = data[data["death_in_hospital"].isin([0, 1])]
    rows, _ = fit_glm_v280(
        data,
        "death_in_hospital ~ C(pandemic_period, Treatment(reference='Pre-pandemic')) * C(country) + C(age_band_common) + C(sex) + C(trauma_subtype)",
        sm.families.Binomial(),
        "Pandemic-period mortality across Brazil, Mexico, and Chile", None, None,
        "Adjusted OR", "pandemic_period", "secondary",
    )
    rows = [r for r in rows if "pandemic_period" in r["term"]]
    event_rows: List[Dict[str, Any]] = []
    for country in countries:
        subset = data[data["country"].eq(country)].copy()
        cluster = "hospital_id" if country in VOLUME_COUNTRIES else None
        period_rows, _ = fit_glm_v280(
            subset,
            "death_in_hospital ~ C(pandemic_period, Treatment(reference='Pre-pandemic')) + C(age_band_common) + C(sex) + C(trauma_subtype)",
            sm.families.Binomial(),
            f"Pandemic-period mortality: {COUNTRY_DISPLAY[country]}", cluster, None,
            "Adjusted OR", "pandemic_period_by_country", "secondary",
        )
        for row in period_rows:
            if "pandemic_period" in row["term"]:
                row["country"] = country
                rows.append(row)
        if 2019 in pd.to_numeric(subset["year"], errors="coerce").dropna().astype(int).unique():
            year_rows, _ = fit_glm_v280(
                subset,
                "death_in_hospital ~ C(year, Treatment(reference='2019')) + C(age_band_common) + C(sex) + C(trauma_subtype)",
                sm.families.Binomial(),
                f"Adjusted annual mortality event study: {COUNTRY_DISPLAY[country]}", cluster, None,
                "Adjusted OR", f"event_study_{country}", "supplementary",
            )
            for row in year_rows:
                if "C(year" in row["term"]:
                    row["country"] = country
                    match = re.search(r"\[T\.(\d{4})\]", row["term"])
                    row["year"] = int(match.group(1)) if match else np.nan
                    event_rows.append(row)
        del subset
        collect_memory()
    result = apply_fdr_by_family_v280(pd.DataFrame(rows))
    event = apply_fdr_by_family_v280(pd.DataFrame(event_rows))
    save_table(result, PATHS.tables / "Supplementary_Table_8_Pandemic_period_models")
    save_table(event, PATHS.tables / "Supplementary_Table_9_Annual_event_study")
    del data
    collect_memory()
    return result, event



def run_brazil_surgical_model_v280() -> pd.DataFrame:
    path = PATHS.data / "cohort_surgical_strict_brazil_v280.parquet"
    output_stem = PATHS.tables / "Supplementary_Table_10_Exploratory_Brazil_surgical_model"
    if not path.exists():
        result = pd.DataFrame([{
            "analysis": "Exploratory Brazil surgical association",
            "status": "SKIPPED_MISSING_SURGICAL_COHORT",
            "n": 0,
        }])
        save_table(result, output_stem)
        return result
    columns = [
        "procedure_class_analysis", "death_in_hospital", "age", "sex",
        "trauma_subtype", "year", "hospital_id", "lag_volume_z_country_year",
    ]
    available = set(parquet_columns(path))
    data = pd.read_parquet(path, columns=[c for c in columns if c in available])
    for col in columns:
        if col not in data:
            data[col] = pd.NA
    data = native_model_frame(
        data,
        numeric=["death_in_hospital", "age", "lag_volume_z_country_year"],
        categorical=["procedure_class_analysis", "sex", "trauma_subtype", "year", "hospital_id"],
    )
    complete_columns = [
        "procedure_class_analysis", "death_in_hospital", "age", "sex",
        "trauma_subtype", "year", "hospital_id", "lag_volume_z_country_year",
    ]
    data = data.dropna(subset=complete_columns)
    data = data[data["death_in_hospital"].isin([0, 1])].reset_index(drop=True)
    counts = data["procedure_class_analysis"].value_counts(dropna=True)
    if len(data) < 500 or counts.size < 2:
        result = pd.DataFrame([{
            "analysis": "Exploratory Brazil surgical association",
            "status": "SKIPPED_INSUFFICIENT_COMPLETE_CASES_OR_LEVELS",
            "n": int(len(data)),
            "level_counts": json.dumps({str(k): int(v) for k, v in counts.items()}),
        }])
        save_table(result, output_stem)
        del data
        collect_memory()
        return result
    preferred = "Other acute cranial surgery"
    reference = preferred if preferred in counts.index else str(counts.index[0])
    formula = (
        "death_in_hospital ~ "
        f"C(procedure_class_analysis, Treatment(reference={reference!r})) + "
        "bs(age, df=4, degree=3, include_intercept=False) + "
        "C(sex) + C(trauma_subtype) + C(year) + lag_volume_z_country_year"
    )
    rows, _ = fit_glm_v280(
        data,
        formula,
        sm.families.Binomial(),
        "Exploratory Brazil surgical association; confounding by indication expected",
        "hospital_id", None, "Adjusted OR", "brazil_surgical", "exploratory",
    )
    rows = [r for r in rows if "procedure_class_analysis" in r["term"]]
    if rows:
        result = pd.DataFrame(rows)
        result["status"] = "COMPLETED"
        result["reference_level"] = reference
        result["level_counts"] = json.dumps({str(k): int(v) for k, v in counts.items()})
    else:
        result = pd.DataFrame([{
            "analysis": "Exploratory Brazil surgical association",
            "status": "MODEL_FAILED_NO_EFFECT_ROWS",
            "n": int(len(data)),
            "reference_level": reference,
            "level_counts": json.dumps({str(k): int(v) for k, v in counts.items()}),
        }])
    save_table(result, output_stem)
    del data
    collect_memory()
    return result


def _collapse_rare_complete_levels_v280(series: pd.Series, minimum: int = 200, maximum_levels: int = 10) -> pd.Series:
    text = _meaningful_text(series)
    counts = text.value_counts(dropna=True)
    keep = list(counts[counts >= minimum].head(maximum_levels - 1).index)
    return text.where(text.isin(keep), "Other/rare")


def run_clean_country_specific_models_v280(base_dir: Path | str = DEFAULT_BASE) -> Tuple[pd.DataFrame, pd.DataFrame]:
    labels = extract_ecuador_value_labels_v280(base_dir)
    descriptive_rows: List[Dict[str, Any]] = []
    model_rows: List[Dict[str, Any]] = []
    specifications = {
        "chile": ["insurance_type", "ethnicity", "any_surgical_intervention", "residence_region"],
        "equador": ["facility_sector", "facility_class", "facility_type", "facility_entity", "ethnicity", "residence_area", "transfer_proxy", "discharge_specialty"],
    }
    for country, predictors in specifications.items():
        path = cohort_path_v280(country)
        base_columns = ["year", "death_in_hospital", "los_days", "age_band_common", "sex", "trauma_subtype"]
        available = set(parquet_columns(path))
        selected = base_columns + [p for p in predictors if p in available]
        data = pd.read_parquet(path, columns=selected)
        if country == "equador":
            data = apply_ecuador_labels_v280(data, labels)
        data = native_model_frame(
            data,
            numeric=["death_in_hospital", "los_days"],
            categorical=["year", "age_band_common", "sex", "trauma_subtype"] + [c for c in data if c not in ("death_in_hospital", "los_days")],
        )
        data = data[data["death_in_hospital"].isin([0, 1])]
        for predictor in predictors:
            model_predictor = f"{predictor}_label" if country == "equador" and f"{predictor}_label" in data else predictor
            if model_predictor not in data:
                continue
            eligible_years = eligible_years_for_predictor_v280(data, model_predictor, 0.70)
            subset = data[data["year"].astype(float).isin(eligible_years)].copy()
            subset[model_predictor] = _collapse_rare_complete_levels_v280(subset[model_predictor])
            subset = subset.dropna(subset=[model_predictor, "death_in_hospital", "age_band_common", "sex", "trauma_subtype", "year"])
            if len(subset) < 500 or subset[model_predictor].nunique() < 2:
                continue
            for level, group in subset.groupby(model_predictor, observed=True):
                descriptive_rows.append({
                    "country": country,
                    "predictor": predictor,
                    "display_variable": model_predictor,
                    "eligible_years": ",".join(map(str, eligible_years)),
                    "level": level,
                    "admissions": len(group),
                    "deaths": int(group["death_in_hospital"].sum()),
                    "mortality_pct": 100 * float(group["death_in_hospital"].mean()),
                    "los_median": float(pd.to_numeric(group["los_days"], errors="coerce").median()),
                })
            rows, _ = fit_glm_v280(
                subset,
                f"death_in_hospital ~ C({model_predictor}) + C(age_band_common) + C(sex) + C(trauma_subtype) + C(year)",
                sm.families.Binomial(),
                f"Exploratory {COUNTRY_DISPLAY[country]} association: {predictor}", None, None,
                "Adjusted OR", f"{country}_{predictor}", "exploratory",
            )
            for row in rows:
                if model_predictor in row["term"]:
                    row["country"] = country
                    row["predictor"] = predictor
                    row["eligible_years"] = ",".join(map(str, eligible_years))
                    model_rows.append(row)
            del subset
            collect_memory()
        del data
        collect_memory()
    descriptive = pd.DataFrame(descriptive_rows)
    models = apply_fdr_by_family_v280(pd.DataFrame(model_rows))
    save_table(descriptive, PATHS.tables / "Supplementary_Table_11_Country_specific_descriptive_factors")
    save_table(models, PATHS.tables / "Supplementary_Table_12_Country_specific_exploratory_models")
    return descriptive, models

# -----------------------------------------------------------------------------
# Final tables
# -----------------------------------------------------------------------------


def table_hospital_volume_v280() -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    hy = pd.read_parquet(PATHS.data / "hospital_year_v280.parquet")
    central_rows: List[Dict[str, Any]] = []
    for (country, year), sub in hy.groupby(["country", "year"], observed=True):
        volumes = pd.to_numeric(sub["hospital_volume_year"], errors="coerce").dropna().sort_values(ascending=False).to_numpy()
        total = float(volumes.sum())
        n = len(volumes)
        shares = volumes / total if total else np.array([])
        asc = np.sort(volumes)
        if len(asc) and asc.sum() > 0:
            ranks = np.arange(1, len(asc) + 1)
            gini = (2 * np.sum(ranks * asc) / (len(asc) * asc.sum())) - (len(asc) + 1) / len(asc)
        else:
            gini = np.nan
        def top_share(frac: float) -> float:
            k = max(1, int(math.ceil(frac * n))) if n else 0
            return 100 * float(volumes[:k].sum() / total) if total and k else np.nan
        central_rows.append({
            "country": country,
            "year": int(year),
            "hospital_year_units": n,
            "admissions": int(total),
            "median_volume": float(np.median(volumes)) if n else np.nan,
            "top_5pct_share_pct": top_share(0.05),
            "top_10pct_share_pct": top_share(0.10),
            "top_20pct_share_pct": top_share(0.20),
            "hhi_0_10000": 10000 * float(np.sum(shares**2)) if total else np.nan,
            "gini_volume": float(gini),
        })
    quartile_rows: List[Dict[str, Any]] = []
    decile_rows: List[Dict[str, Any]] = []
    columns = ["country", "hospital_id", "year", "hospital_volume_year", "volume_quartile", "volume_decile", "death_in_hospital", "los_days"]
    for country in VOLUME_COUNTRIES:
        frame = pd.read_parquet(cohort_path_v280(country), columns=columns)
        for quartile, sub in frame.groupby("volume_quartile", observed=True, dropna=True):
            death = pd.to_numeric(sub["death_in_hospital"], errors="coerce")
            valid = death.isin([0, 1])
            los = pd.to_numeric(sub["los_days"], errors="coerce")
            units = sub[["country", "hospital_id", "year", "hospital_volume_year"]].drop_duplicates()
            quartile_rows.append({
                "country": country,
                "volume_quartile": str(quartile),
                "hospital_year_units": len(units),
                "unique_hospitals": int(units["hospital_id"].nunique()),
                "admissions": len(sub),
                "median_hospital_year_volume": float(pd.to_numeric(units["hospital_volume_year"], errors="coerce").median()),
                "mortality_pct": 100 * float(death[valid].mean()) if valid.any() else np.nan,
                "los_median": float(los[los.ge(0)].median()) if los.ge(0).any() else np.nan,
            })
        for decile, sub in frame.groupby("volume_decile", observed=True, dropna=True):
            death = pd.to_numeric(sub["death_in_hospital"], errors="coerce")
            valid = death.isin([0, 1])
            events, total_n = int(death[valid].sum()), int(valid.sum())
            low, high = wilson_interval(events, total_n)
            units = sub[["country", "hospital_id", "year", "hospital_volume_year"]].drop_duplicates()
            decile_rows.append({
                "country": country,
                "volume_decile": str(decile),
                "hospital_year_units": len(units),
                "admissions": len(sub),
                "median_volume": float(pd.to_numeric(units["hospital_volume_year"], errors="coerce").median()),
                "mortality_pct": 100 * events / total_n if total_n else np.nan,
                "mortality_ci_low_pct": 100 * low,
                "mortality_ci_high_pct": 100 * high,
            })
        del frame
        collect_memory()
    del hy
    collect_memory()
    return pd.DataFrame(quartile_rows), pd.DataFrame(decile_rows), pd.DataFrame(central_rows)


def build_cohort_flow_table_v280() -> pd.DataFrame:
    audit_path = PATHS.qc / "Country_checkpoint_audit_v280.csv"
    validation_path = PATHS.qc / "Final_cohort_validation_v280.csv"
    audit = pd.read_csv(audit_path)
    validation = pd.read_csv(validation_path)
    rows: List[Dict[str, Any]] = []
    for country in COUNTRY_ORDER:
        a = audit[audit["country"].eq(country)].iloc[0]
        v = validation[validation["country"].eq(country)].iloc[0]
        rows.extend([
            {"country": country, "stage": "Validated adult S06 records available", "records": int(a["records_18plus_available"])},
            {"country": country, "stage": "Primary harmonized cohort, age >=20 years", "records": int(v["primary_records"])},
            {"country": country, "stage": "Hospital-volume cohort", "records": int(v["primary_records"]) if country in VOLUME_COUNTRIES else 0},
        ])
    result = pd.DataFrame(rows)
    save_table(result, PATHS.qc / "Cohort_flow_v280")
    return result


def run_tables_v280(base_dir: Path | str = DEFAULT_BASE) -> Dict[str, str]:
    activate_v280(base_dir)
    outputs: Dict[str, str] = {}
    table_map = {
        "Table_1_Cohort_characteristics": table_cohort_characteristics_v260(),
        "Table_2_Annual_outcomes": table_annual_outcomes_v260(),
        "Supplementary_Table_2_TBI_subtype_outcomes": table_subtype_outcomes_v260(),
        "Supplementary_Table_3_Age_band_outcomes": table_age_band_outcomes_v260(),
    }
    quartiles, deciles, centralization = table_hospital_volume_v280()
    table_map["Table_3_Hospital_volume_quartiles"] = quartiles
    table_map["Supplementary_Table_4_Hospital_volume_deciles"] = deciles
    table_map["Table_4_Centralization_metrics"] = centralization
    for name, frame in table_map.items():
        save_table(frame, PATHS.tables / name)
        outputs[name] = str(PATHS.tables / f"{name}.csv")
    availability_audit_v280()
    build_cohort_flow_table_v280()
    return outputs


# -----------------------------------------------------------------------------
# Final English figures
# -----------------------------------------------------------------------------


def figure_flow_v280() -> None:
    flow = pd.read_csv(PATHS.qc / "Cohort_flow_v280.csv")
    fig, ax = plt.subplots(figsize=(13, 8))
    ax.set_xlim(0, 13)
    ax.set_ylim(0, 9)
    ax.axis("off")
    x_positions = {"brasil": 1.8, "mexico": 4.8, "chile": 7.8, "equador": 10.8}
    stage_y = {
        "Validated adult S06 records available": 7.2,
        "Primary harmonized cohort, age >=20 years": 4.8,
        "Hospital-volume cohort": 2.4,
    }
    for country in COUNTRY_ORDER:
        x = x_positions[country]
        ax.text(x, 8.45, COUNTRY_DISPLAY[country], ha="center", va="center", fontsize=15, fontweight="bold")
        country_flow = flow[flow["country"].eq(country)]
        previous_y = None
        for _, row in country_flow.iterrows():
            y = stage_y[row["stage"]]
            records = int(row["records"])
            if row["stage"] == "Hospital-volume cohort" and records == 0:
                label = "Not eligible\n(no stable hospital identifier)"
            else:
                label = f"{row['stage']}\nN = {records:,}"
            box = dict(boxstyle="round,pad=0.6", facecolor="white", edgecolor="black", linewidth=1.2)
            ax.text(x, y, label, ha="center", va="center", fontsize=10, bbox=box)
            if previous_y is not None:
                ax.annotate("", xy=(x, y + 0.55), xytext=(x, previous_y - 0.55), arrowprops=dict(arrowstyle="->", linewidth=1.2))
            previous_y = y
    ax.text(6.5, 0.65, "Primary multinational cohort: individual-level analysis across four countries\nNested hospital-volume analysis: Brazil and Mexico", ha="center", fontsize=12, fontweight="bold")
    save_figure(fig, PATHS.figures_main / "Figure_1_Cohort_flow")


def figure_annual_mortality_v280() -> None:
    annual = pd.read_csv(PATHS.tables / "Table_2_Annual_outcomes.csv")
    fig, ax = plt.subplots(figsize=(10.5, 6.5))
    for country in COUNTRY_ORDER:
        sub = annual[annual["country"].eq(country)].sort_values("year")
        if sub.empty:
            continue
        ax.plot(sub["year"], sub["mortality_pct"], marker="o", linewidth=2, label=COUNTRY_DISPLAY[country])
        ax.fill_between(sub["year"], sub["mortality_ci_low_pct"], sub["mortality_ci_high_pct"], alpha=0.12)
    ax.axvline(2020, linestyle="--", linewidth=1, color="black", alpha=0.6)
    ax.text(2020.08, ax.get_ylim()[1] * 0.97, "COVID-19 period begins", va="top", fontsize=9)
    ax.set_xlabel("Year")
    ax.set_ylabel("In-hospital mortality (%)")
    ax.set_title("Annual in-hospital mortality after traumatic brain injury")
    ax.legend(frameon=False, ncol=2)
    ax.grid(axis="y", alpha=0.2)
    save_figure(fig, PATHS.figures_main / "Figure_2_Annual_mortality")


def figure_adjusted_spline_v280() -> None:
    curve = pd.read_csv(PATHS.tables / "Table_7_Adjusted_volume_spline_predictions.csv")
    fig, axes = plt.subplots(1, 2, figsize=(13, 5.5), sharey=True)
    for ax, country in zip(axes, VOLUME_COUNTRIES):
        sub = curve[curve["country"].eq(country)].sort_values("hospital_volume")
        ax.plot(sub["hospital_volume"], 100 * sub["predicted_mortality"], linewidth=2.3)
        ax.fill_between(sub["hospital_volume"], 100 * sub["ci_low"], 100 * sub["ci_high"], alpha=0.2)
        ax.set_xscale("log")
        p = sub["nonlinearity_p"].iloc[0] if not sub.empty else np.nan
        p_text = "<0.001" if np.isfinite(p) and p < 0.001 else f"{p:.3f}" if np.isfinite(p) else "NA"
        ax.set_title(f"{COUNTRY_DISPLAY[country]}\nP for nonlinearity = {p_text}")
        ax.set_xlabel("Prior-year hospital TBI volume (log scale)")
        ax.grid(alpha=0.2)
    axes[0].set_ylabel("Adjusted predicted in-hospital mortality (%)")
    fig.suptitle("Adjusted mortality across prior-year hospital TBI volume", fontsize=14, fontweight="bold")
    save_figure(fig, PATHS.figures_main / "Figure_3_Adjusted_volume_splines")


def figure_volume_forest_v280() -> None:
    models = pd.read_csv(PATHS.tables / "Table_6_Final_hospital_volume_models.csv")
    keep_names = [
        "Primary mortality: prior-year hospital TBI volume",
        "Sensitivity mortality: same-year hospital TBI volume",
        "Sensitivity mortality: structural intracranial injury subset",
        "Sensitivity mortality: age >=18 in Brazil and Mexico",
        "Country-specific prior-year volume: Brazil",
        "Country-specific prior-year volume: Mexico",
        "Sensitivity excluding hospital-years with <5 admissions: Brazil",
        "Sensitivity excluding hospital-years with <5 admissions: Mexico",
    ]
    plot = models[models["analysis"].isin(keep_names) & models["effect_measure"].str.contains("OR", na=False)].copy()
    order = {name: i for i, name in enumerate(keep_names)}
    plot["order"] = plot["analysis"].map(order)
    plot = plot.sort_values("order", ascending=False)
    labels = [x.replace("Sensitivity mortality: ", "").replace("Country-specific prior-year volume: ", "") for x in plot["analysis"]]
    y = np.arange(len(plot))
    fig, ax = plt.subplots(figsize=(10.5, max(5.5, 0.65 * len(plot) + 2)))
    ax.errorbar(plot["estimate"], y, xerr=[plot["estimate"] - plot["ci_low"], plot["ci_high"] - plot["estimate"]], fmt="o", capsize=3)
    ax.axvline(1, linestyle="--", color="black", linewidth=1)
    ax.set_yticks(y)
    ax.set_yticklabels(labels)
    ax.set_xlabel("Adjusted odds ratio per 1-SD increase in log hospital volume")
    ax.set_title("Hospital TBI volume and in-hospital mortality")
    ax.grid(axis="x", alpha=0.2)
    save_figure(fig, PATHS.figures_main / "Figure_4_Hospital_volume_forest_plot")


def figure_funnel_plots_v280() -> None:
    fig, axes = plt.subplots(1, 2, figsize=(13, 5.7), sharey=True)
    for ax, country in zip(axes, VOLUME_COUNTRIES):
        path = PATHS.data / f"hospital_risk_standardized_{country}_v280.parquet"
        data = pd.read_parquet(path, columns=["expected_deaths_for_funnel", "smr", "outside_998pct"])
        data = data.dropna(subset=["expected_deaths_for_funnel", "smr"])
        data = data[data["expected_deaths_for_funnel"].gt(0)]
        ax.scatter(data["expected_deaths_for_funnel"], data["smr"], s=10, alpha=0.35)
        grid = np.geomspace(max(0.2, data["expected_deaths_for_funnel"].min()), data["expected_deaths_for_funnel"].max(), 200)
        for probability, linestyle, label in [(0.025, "--", "95% limits"), (0.001, ":", "99.8% limits")]:
            lower = poisson.ppf(probability, grid) / grid
            upper = poisson.ppf(1 - probability, grid) / grid
            ax.plot(grid, lower, linestyle=linestyle, color="black", linewidth=1, label=label)
            ax.plot(grid, upper, linestyle=linestyle, color="black", linewidth=1)
        ax.axhline(1, color="black", linewidth=1)
        ax.set_xscale("log")
        ax.set_ylim(bottom=0)
        ax.set_xlabel("Expected in-hospital deaths (log scale)")
        ax.set_title(COUNTRY_DISPLAY[country])
        ax.grid(alpha=0.15)
    axes[0].set_ylabel("Observed-to-expected mortality ratio")
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles[:2], labels[:2], loc="lower center", ncol=2, frameon=False)
    fig.suptitle("Risk-adjusted hospital mortality funnel plots", fontsize=14, fontweight="bold")
    fig.subplots_adjust(bottom=0.18)
    save_figure(fig, PATHS.figures_main / "Figure_5_Risk_adjusted_funnel_plots")


def figure_centralization_v280() -> None:
    table = pd.read_csv(PATHS.tables / "Table_4_Centralization_metrics.csv")
    fig, axes = plt.subplots(1, 2, figsize=(13, 5.5))
    for country in VOLUME_COUNTRIES:
        sub = table[table["country"].eq(country)].sort_values("year")
        axes[0].plot(sub["year"], sub["top_10pct_share_pct"], marker="o", linewidth=2, label=COUNTRY_DISPLAY[country])
        axes[1].plot(sub["year"], sub["gini_volume"], marker="o", linewidth=2, label=COUNTRY_DISPLAY[country])
    axes[0].set_ylabel("Admissions treated by the top 10% of hospitals (%)")
    axes[1].set_ylabel("Gini coefficient of hospital TBI volume")
    for ax in axes:
        ax.set_xlabel("Year")
        ax.grid(alpha=0.2)
        ax.legend(frameon=False)
    fig.suptitle("Hospital concentration of traumatic brain injury care", fontsize=14, fontweight="bold")
    save_figure(fig, PATHS.figures_main / "Figure_6_Centralization_trends")


def figure_event_study_v280() -> None:
    path = PATHS.tables / "Supplementary_Table_9_Annual_event_study.csv"
    if not path.exists():
        return
    data = pd.read_csv(path)
    fig, axes = plt.subplots(1, 3, figsize=(15, 4.8), sharey=True)
    for ax, country in zip(axes, ("brasil", "mexico", "chile")):
        sub = data[data["country"].eq(country)].sort_values("year")
        reference = pd.DataFrame({"year": [2019], "estimate": [1.0], "ci_low": [1.0], "ci_high": [1.0]})
        sub = pd.concat([sub[["year", "estimate", "ci_low", "ci_high"]], reference], ignore_index=True).sort_values("year")
        ax.errorbar(sub["year"], sub["estimate"], yerr=[sub["estimate"] - sub["ci_low"], sub["ci_high"] - sub["estimate"]], marker="o", capsize=3)
        ax.axhline(1, linestyle="--", color="black", linewidth=1)
        ax.axvline(2020, linestyle=":", color="black", linewidth=1)
        ax.set_title(COUNTRY_DISPLAY[country])
        ax.set_xlabel("Year")
        ax.grid(alpha=0.2)
    axes[0].set_ylabel("Adjusted odds ratio vs 2019")
    fig.suptitle("Adjusted annual mortality event study", fontsize=14, fontweight="bold")
    save_figure(fig, PATHS.figures_supp / "Supplementary_Figure_1_Annual_event_study")


def figure_risk_standardized_distribution_v280() -> None:
    fig, ax = plt.subplots(figsize=(10, 6))
    positions = []
    labels = []
    values = []
    for i, country in enumerate(VOLUME_COUNTRIES, start=1):
        path = PATHS.data / f"hospital_risk_standardized_{country}_v280.parquet"
        data = pd.read_parquet(path, columns=["risk_standardized_mortality", "admissions"])
        data = data[data["admissions"].ge(20)]
        values.append(100 * pd.to_numeric(data["risk_standardized_mortality"], errors="coerce").dropna().to_numpy())
        positions.append(i)
        labels.append(COUNTRY_DISPLAY[country])
    ax.boxplot(values, positions=positions, labels=labels, showfliers=False)
    ax.set_ylabel("Empirical-Bayes risk-standardized mortality (%)")
    ax.set_title("Between-hospital variation after risk standardization")
    ax.grid(axis="y", alpha=0.2)
    save_figure(fig, PATHS.figures_supp / "Supplementary_Figure_2_Risk_standardized_mortality")


def figure_availability_v280() -> None:
    table = pd.read_csv(PATHS.tables / "Supplementary_Table_1_Informative_variable_availability.csv")
    pivot = table.pivot(index="variable", columns="country", values="informative_pct").reindex(columns=list(COUNTRY_ORDER))
    fig, ax = plt.subplots(figsize=(9, max(6, 0.32 * len(pivot))))
    image = ax.imshow(pivot.fillna(0).to_numpy(), aspect="auto", vmin=0, vmax=100)
    ax.set_xticks(range(len(pivot.columns)))
    ax.set_xticklabels([COUNTRY_DISPLAY[c] for c in pivot.columns])
    ax.set_yticks(range(len(pivot.index)))
    ax.set_yticklabels([v.replace("_", " ") for v in pivot.index], fontsize=8)
    ax.set_title("Informative variable availability by country")
    cbar = fig.colorbar(image, ax=ax)
    cbar.set_label("Informative records (%)")
    save_figure(fig, PATHS.figures_supp / "Supplementary_Figure_3_Informative_variable_availability")


def run_figures_v280(base_dir: Path | str = DEFAULT_BASE) -> Dict[str, str]:
    activate_v280(base_dir)
    figure_flow_v280()
    figure_annual_mortality_v280()
    figure_adjusted_spline_v280()
    figure_volume_forest_v280()
    figure_funnel_plots_v280()
    figure_centralization_v280()
    figure_event_study_v280()
    figure_risk_standardized_distribution_v280()
    figure_availability_v280()
    # Reuse the clear age/subtype figures from the low-memory base, now routed to v2.8 paths.
    subtype = pd.read_csv(PATHS.tables / "Supplementary_Table_2_TBI_subtype_outcomes.csv")
    age_table = pd.read_csv(PATHS.tables / "Supplementary_Table_3_Age_band_outcomes.csv")
    figure_subtype(subtype)
    figure_age_band(age_table)
    return {
        "main_figures": str(PATHS.figures_main),
        "supplementary_figures": str(PATHS.figures_supp),
    }

# -----------------------------------------------------------------------------
# Complete final model suite, manuscript support, and runners
# -----------------------------------------------------------------------------


def english_level_label_v280(value: Any) -> str:
    text = str(value)
    replacements = {
        "No se identifica con alguna etnia": "Does not identify with an ethnic group",
        "Si se identifica con alguna etnia": "Identifies with an ethnic group",
        "Sí se identifica con alguna etnia": "Identifies with an ethnic group",
        "Publico": "Public", "Público": "Public", "Privado": "Private",
        "Urbano": "Urban", "Rural": "Rural", "Desconocido": "Unknown",
        "Hospital basico": "Basic hospital", "Hospital básico": "Basic hospital",
        "Hospital general": "General hospital", "Hospital especializado": "Specialized hospital",
        "Hospital de especialidades": "Specialties hospital",
        "Ministerio de Salud Publica": "Ministry of Public Health",
        "Ministerio de Salud Pública": "Ministry of Public Health",
        "Instituto Ecuatoriano de Seguridad Social": "Ecuadorian Social Security Institute",
    }
    return replacements.get(text, text)


def add_english_display_columns_v280() -> None:
    descriptive_path = PATHS.tables / "Supplementary_Table_11_Country_specific_descriptive_factors.csv"
    model_path = PATHS.tables / "Supplementary_Table_12_Country_specific_exploratory_models.csv"
    if descriptive_path.exists():
        frame = pd.read_csv(descriptive_path)
        frame["level_english"] = frame["level"].map(english_level_label_v280)
        save_table(frame, descriptive_path.with_suffix(""))
    if model_path.exists():
        frame = pd.read_csv(model_path)
        frame["term_english"] = frame["term"].astype(str)
        for original, translated in {
            "No se identifica con alguna etnia": "Does not identify with an ethnic group",
            "Si se identifica con alguna etnia": "Identifies with an ethnic group",
            "Sí se identifica con alguna etnia": "Identifies with an ethnic group",
        }.items():
            frame["term_english"] = frame["term_english"].str.replace(original, translated, regex=False)
        save_table(frame, model_path.with_suffix(""))


def run_models_v280(base_dir: Path | str = DEFAULT_BASE) -> Dict[str, str]:
    activate_v280(base_dir)
    outputs: Dict[str, str] = {}
    log_memory("before final v2.8 models")
    volume, curves, spline_diag = run_final_volume_models_v280()
    outputs["volume_models"] = str(PATHS.tables / "Table_6_Final_hospital_volume_models.csv")
    del volume, curves, spline_diag
    collect_memory()
    log_memory("after volume and spline models")

    outputs.update(run_risk_standardization_v280())
    log_memory("after risk standardization")

    factors = run_individual_factor_models_v280()
    outputs["individual_factors"] = str(PATHS.tables / "Table_5_Individual_factor_models.csv")
    del factors
    collect_memory()

    pandemic, event = run_pandemic_event_study_v280()
    outputs["pandemic_models"] = str(PATHS.tables / "Supplementary_Table_8_Pandemic_period_models.csv")
    outputs["event_study"] = str(PATHS.tables / "Supplementary_Table_9_Annual_event_study.csv")
    del pandemic, event
    collect_memory()

    surgical = run_brazil_surgical_model_v280()
    outputs["brazil_surgical"] = str(PATHS.tables / "Supplementary_Table_10_Exploratory_Brazil_surgical_model.csv")
    del surgical
    collect_memory()

    descriptive, exploratory = run_clean_country_specific_models_v280(base_dir)
    outputs["country_specific_descriptive"] = str(PATHS.tables / "Supplementary_Table_11_Country_specific_descriptive_factors.csv")
    outputs["country_specific_models"] = str(PATHS.tables / "Supplementary_Table_12_Country_specific_exploratory_models.csv")
    del descriptive, exploratory
    add_english_display_columns_v280()
    collect_memory()
    log_memory("after all final v2.8 models")
    return outputs


def write_statistical_analysis_plan_v280() -> Path:
    text = """# Frozen statistical analysis plan — TCE Multinational v2.8.0

## Study design
Retrospective multinational cohort of hospital discharge records with a principal S06.x diagnosis. The primary harmonized cohort includes patients aged 20 years or older in Brazil, Mexico, Chile, and Ecuador. Chile is released in grouped ages, so 20 years was selected as the common lower bound. A sensitivity cohort includes patients aged 18–19 years where exact age is available.

## Primary descriptive objective
Describe admissions, in-hospital mortality, length of stay, age, sex, administrative TBI phenotype, and temporal trends across the four participating health systems. Country comparisons are descriptive and are not interpreted as rankings of quality.

## Primary hospital-volume objective
Evaluate the association between prior-year hospital TBI volume and in-hospital mortality in Brazil and Mexico, the countries with stable hospital identifiers. Prior-year volume is the primary exposure to reduce simultaneity. Same-year volume is a sensitivity exposure.

## Primary volume model
Patient-level logistic generalized linear model with hospital-clustered standard errors. Adjustment: age restricted spline, sex, administrative TBI subtype, and country-year fixed effects. Exposure: country-year standardized log prior-year hospital volume.

## Prespecified volume sensitivities
Same-year volume; country interaction; country-specific estimates; exclusion of hospital-years with fewer than five admissions; age >=18 sensitivity; structural intracranial injury subset; post-admission ICU/surgery sensitivity in Brazil; Gamma and negative-binomial survivor length-of-stay models.

## Nonlinear association
Country-specific restricted cubic spline models of absolute prior-year hospital volume. Standardized adjusted predictions are generated over the 5th to 95th percentiles. A likelihood-ratio test compares spline and linear specifications.

## Hospital variation and risk standardization
Country-specific mortality risk models excluding hospital volume are used to calculate expected deaths. Hospital-year observed and expected mortality are aggregated. Hospital estimates are stabilized with empirical-Bayes random-effects shrinkage of log observed-to-expected mortality. Funnel plots use Poisson 95% and 99.8% control limits.

## Within-hospital analyses
Risk-adjusted hospital-year mortality differences are analyzed using weighted two-way hospital and year fixed-effects residualization. A contiguous-year first-difference model is a supporting sensitivity. These analyses estimate whether changes in a hospital's own volume are associated with changes in its risk-adjusted mortality.

## Individual-level factors
Age band, sex, and administrative TBI phenotype are modeled in the four-country cohort with country-year adjustment. Country-specific models are supplementary.

## Pandemic analyses
Brazil, Mexico, and Chile are included. Ecuador ends in 2019 and is excluded from pandemic contrasts. Period models and an adjusted annual event study use 2019 as reference.

## Country-specific exploratory analyses
Chile and Ecuador variables are analyzed only in years with at least 70% informative completeness and at least two levels. Missing values are not coded as absence. Rare levels are collapsed. Ecuador value labels are read from official SAV metadata when available.

## Multiplicity
The primary hospital-volume hypothesis is interpreted using its nominal two-sided p value and confidence interval. Benjamini-Hochberg correction is applied separately within prespecified secondary/exploratory families.

## Interpretation
All estimates are associations. Residual confounding by injury severity, referral, transfer, imaging findings, physiology, and indication for surgery is expected. The volume coefficient is not interpreted as a causal measure of hospital quality.
"""
    path = PATHS.manuscript / "Frozen_statistical_analysis_plan_v280.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def write_reporting_checklist_v280() -> Path:
    rows = [
        ("RECORD", "Describe the type of routinely collected data and participating systems", "Required"),
        ("RECORD", "Provide code lists and phenotype definitions", "Required"),
        ("RECORD", "Describe data cleaning, linkage, and country-specific harmonization", "Required"),
        ("RECORD", "Report database coverage and years separately for each country", "Required"),
        ("STROBE", "Describe eligibility, exclusions, and final cohort flow", "Required"),
        ("STROBE", "Report missingness and informative availability by country and year", "Required"),
        ("STROBE", "Report adjusted and unadjusted estimates with precision", "Required"),
        ("STROBE", "Discuss residual confounding and referral bias", "Required"),
        ("Analysis", "Freeze primary and secondary model roles before manuscript drafting", "Completed in v2.8"),
        ("Analysis", "Keep Chile/Ecuador outside hospital-volume models", "Completed in v2.8"),
        ("Analysis", "Use English manuscript figures and labels", "Completed in v2.8"),
    ]
    frame = pd.DataFrame(rows, columns=["framework", "item", "status"])
    save_table(frame, PATHS.manuscript / "STROBE_RECORD_readiness_checklist_v280")
    return PATHS.manuscript / "STROBE_RECORD_readiness_checklist_v280.csv"



def validate_outputs_v280() -> pd.DataFrame:
    required = [
        PATHS.qc / "Mexico_year_coverage_audit_v280.csv",
        PATHS.qc / "Final_cohort_validation_v280.csv",
        PATHS.tables / "Table_1_Cohort_characteristics.csv",
        PATHS.tables / "Table_4_Centralization_metrics.csv",
        PATHS.tables / "Table_5_Individual_factor_models.csv",
        PATHS.tables / "Table_6_Final_hospital_volume_models.csv",
        PATHS.tables / "Table_7_Adjusted_volume_spline_predictions.csv",
        PATHS.tables / "Table_8_Hospital_heterogeneity.csv",
        PATHS.tables / "Table_9_Within_hospital_volume_models.csv",
        PATHS.tables / "Supplementary_Table_10_Exploratory_Brazil_surgical_model.csv",
        PATHS.figures_main / "Figure_1_Cohort_flow.png",
        PATHS.figures_main / "Figure_3_Adjusted_volume_splines.png",
        PATHS.figures_main / "Figure_5_Risk_adjusted_funnel_plots.png",
        PATHS.manuscript / "Mexico_2015_2017_data_resolution_v280.md",
    ]
    rows = [
        {
            "check": "required_output",
            "path": str(path),
            "passed": bool(path.exists() and path.stat().st_size > 0),
            "detail": "",
            "size_bytes": path.stat().st_size if path.exists() else 0,
        }
        for path in required
    ]
    mexico_path = PATHS.qc / "Mexico_year_coverage_audit_v280.csv"
    if mexico_path.exists():
        mexico = pd.read_csv(mexico_path)
        for year in PRIMARY_YEARS:
            subset = mexico[mexico["year"].eq(year)]
            selected = subset[subset.get("selected_for_analysis", False).fillna(False)] if not subset.empty else pd.DataFrame()
            statuses = subset["status"].astype(str).tolist() if not subset.empty else ["MISSING_AUDIT_ROW"]
            documented = bool(not subset.empty and all(s.startswith(("INCLUDED", "EXCLUDED")) for s in statuses))
            if not selected.empty:
                analytically_valid = bool(
                    selected["adult_18plus_s06_rows"].gt(0).all()
                    and selected["mortality_available_pct"].ge(80).all()
                )
            else:
                analytically_valid = documented
            rows.append({
                "check": "mexico_year_resolution",
                "path": f"Mexico {year}",
                "passed": analytically_valid,
                "detail": "; ".join(statuses),
                "size_bytes": int(selected["adult_18plus_s06_rows"].sum()) if not selected.empty else 0,
            })
    surgical_path = PATHS.tables / "Supplementary_Table_10_Exploratory_Brazil_surgical_model.csv"
    if surgical_path.exists():
        surgical = pd.read_csv(surgical_path)
        status_values = surgical.get("status", pd.Series("COMPLETED", index=surgical.index)).astype(str)
        passed = bool(status_values.isin([
            "COMPLETED", "SKIPPED_MISSING_SURGICAL_COHORT",
            "SKIPPED_INSUFFICIENT_COMPLETE_CASES_OR_LEVELS",
        ]).all())
        rows.append({
            "check": "brazil_surgical_model_resolution",
            "path": str(surgical_path),
            "passed": passed,
            "detail": "; ".join(sorted(status_values.unique())),
            "size_bytes": surgical_path.stat().st_size,
        })
    validation = pd.DataFrame(rows)
    save_table(validation, PATHS.qc / "Final_output_validation_v280")
    if not validation["passed"].all():
        failures = validation.loc[~validation["passed"], ["check", "path", "detail"]].to_dict(orient="records")
        raise RuntimeError(f"v2.8 output validation failed: {failures}")
    return validation


def write_analysis_manifest_v280(extra: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    validation_path = PATHS.qc / "Final_cohort_validation_v280.csv"
    cohort_validation = pd.read_csv(validation_path).to_dict(orient="records") if validation_path.exists() else []
    manifest: Dict[str, Any] = {
        "version": FINAL_VERSION,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "output_dir": str(PATHS.output),
        "primary_age_policy": "Age >=20 years in all four countries",
        "sensitivity_age_policy": "Age >=18 years where exact age is available",
        "primary_volume_exposure": "Prior-year country-year standardized log hospital TBI volume",
        "volume_countries": ["Brazil", "Mexico"],
        "individual_level_countries": ["Brazil", "Mexico", "Chile", "Ecuador"],
        "cohort_validation": cohort_validation,
        "methodological_upgrades": [
            "Mexico rebuilt from annual 2015-2023 analytic checkpoints when available",
            "Contiguous-year prior-volume definition",
            "Restricted cubic spline volume models",
            "Within-hospital two-way fixed-effects and first-difference sensitivities",
            "Empirical-Bayes hierarchical hospital risk standardization",
            "Poisson funnel plots",
            "Gamma and negative-binomial LOS models",
            "Pandemic model restricted to countries with pandemic-era observations",
            "Year-specific informative availability audit",
            "Official Ecuador SAV value-label extraction",
            "Family-specific false-discovery-rate control",
        ],
        "figure_language": "English",
        "returns_large_dataframes": False,
    }
    if extra:
        manifest.update(dict(extra))
    path = PATHS.manuscript / "analysis_manifest_v280.json"
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return manifest


def prepare_data_v280(
    base_dir: Path | str = DEFAULT_BASE,
    clean_output: bool = True,
    rebuild_chile: bool = False,
) -> Dict[str, Any]:
    if clean_output:
        reset_analysis_v280(base_dir)
    else:
        activate_v280(base_dir)
    release_legacy_notebook_objects_v260()
    lean = build_lean_country_parts_v280(base_dir, rebuild_chile=rebuild_chile)
    cohorts = build_analysis_cohort_parts_v280(base_dir)
    validation = pd.read_csv(PATHS.qc / "Final_cohort_validation_v280.csv")
    return {
        "version": FINAL_VERSION,
        "lean_parts": lean,
        "cohort_parts": cohorts,
        "validation": validation.to_dict(orient="records"),
        "next_step": "Restart runtime, load this file again, and run resume_final_analysis_v280().",
    }


def resume_final_analysis_v280(
    base_dir: Path | str = DEFAULT_BASE,
    run_models: bool = True,
    regenerate_tables: bool = True,
    regenerate_figures: bool = True,
) -> Dict[str, Any]:
    activate_v280(base_dir)
    release_legacy_notebook_objects_v260()
    missing = [c for c in COUNTRY_ORDER if not cohort_path_v280(c).exists()]
    if missing:
        raise FileNotFoundError(f"Missing v2.8 cohort partitions: {missing}. Run prepare_data_v280() first.")
    stages: Dict[str, Any] = {}
    if regenerate_tables:
        stages["tables"] = run_tables_v280(base_dir)
    if run_models:
        stages["models"] = run_models_v280(base_dir)
    if regenerate_figures:
        stages["figures"] = run_figures_v280(base_dir)
    stages["statistical_analysis_plan"] = str(write_statistical_analysis_plan_v280())
    stages["reporting_checklist"] = str(write_reporting_checklist_v280())
    stages["validation"] = validate_outputs_v280().to_dict(orient="records")
    return write_analysis_manifest_v280({"stages": stages})


def run_pipeline_complete_v280(
    base_dir: Path | str = DEFAULT_BASE,
    rebuild_chile: bool = False,
    clean_output: bool = True,
) -> Dict[str, Any]:
    prepare_data_v280(base_dir, clean_output=clean_output, rebuild_chile=rebuild_chile)
    return resume_final_analysis_v280(base_dir, run_models=True, regenerate_tables=True, regenerate_figures=True)


def verify_tce_master_v280() -> Dict[str, Any]:
    activate_v280(DEFAULT_BASE)
    status = {
        "version": FINAL_VERSION,
        "data_preparation": prepare_data_v280.__name__,
        "final_analysis": resume_final_analysis_v280.__name__,
        "complete_runner": run_pipeline_complete_v280.__name__,
        "output_root": str(PATHS.output),
        "primary_volume_exposure": "prior-year hospital TBI volume",
        "mexico_annual_checkpoint_rebuild": True,
        "nonlinear_volume_models": True,
        "within_hospital_models": True,
        "empirical_bayes_risk_standardization": True,
        "robust_los_models": ["Gamma log-link", "Negative binomial"],
        "pandemic_countries": ["Brazil", "Mexico", "Chile"],
        "figure_language": "English",
        "returns_large_dataframes": False,
    }
    print(json.dumps(status, indent=2, ensure_ascii=False))
    return status


# Override logging after all definitions so both inherited and v2.8 functions use the final log.
def _log(message: str, level: str = "INFO") -> None:
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"{timestamp} | {level:<7} | {message}"
    print(line, flush=True)
    try:
        PATHS.logs.mkdir(parents=True, exist_ok=True)
        with (PATHS.logs / "pipeline_v280.log").open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except Exception:
        pass




# =============================================================================
# v2.8.1 RAW-AWARE FINAL OVERRIDES
# =============================================================================

FINAL_VERSION = "2.8.1"
PREFLIGHT_DIR_NAME = "analysis_v281_preflight"
FINAL_OUTPUT_DIR_NAME = "analysis_v281_final"


def make_paths_v280(base_dir: Path | str = DEFAULT_BASE) -> Paths:
    """v2.8.1 output paths, isolated from v2.8/v2.7 outputs."""
    base = Path(base_dir)
    output = base / FINAL_OUTPUT_DIR_NAME
    paths = Paths(
        base=base,
        raw=base / "00_raw",
        intermediate=base / "01_intermediate",
        output=output,
        data=output / "01_data",
        qc=output / "02_qc",
        tables=output / "03_tables",
        figures_main=output / "04_figures_main",
        figures_supp=output / "05_figures_supplement",
        models=output / "06_models",
        logs=output / "07_logs",
        manuscript=output / "08_manuscript_support",
    )
    for folder in paths.__dict__.values():
        if isinstance(folder, Path):
            folder.mkdir(parents=True, exist_ok=True)
    return paths


def activate_v280(base_dir: Path | str = DEFAULT_BASE) -> Paths:
    global PATHS
    PATHS = make_paths_v280(base_dir)
    return PATHS


def reset_analysis_v280(base_dir: Path | str = DEFAULT_BASE) -> Path:
    global PATHS
    base = Path(base_dir)
    target = base / FINAL_OUTPUT_DIR_NAME
    if target.exists():
        shutil.rmtree(target)
    PATHS = make_paths_v280(base)
    _log(f"Clean v2.8.1 output directory prepared: {target}")
    return target


def _v281_preflight_root(base_dir: Path | str = DEFAULT_BASE) -> Path:
    return Path(base_dir) / PREFLIGHT_DIR_NAME


def _v281_preferred_recovery_table(base_dir: Path | str = DEFAULT_BASE) -> Path:
    return _v281_preflight_root(base_dir) / "01_mexico" / "Mexico_preferred_recovered_sources_v281.csv"


def _v281_recovered_path(base_dir: Path | str, year: int) -> Path:
    return _v281_preflight_root(base_dir) / "01_mexico" / "recovered" / f"mexico_s06_raw_recovered_{year}_v281.parquet"


def _v281_copy_preflight_artifacts(base_dir: Path | str = DEFAULT_BASE) -> None:
    """Copy the source-audit evidence into the final analysis QC folder."""
    activate_v280(base_dir)
    preflight = _v281_preflight_root(base_dir)
    if not preflight.exists():
        return
    names = [
        preflight / "01_mexico" / "Mexico_raw_file_inventory_v281.csv",
        preflight / "01_mexico" / "Mexico_schema_matrix_v281.csv",
        preflight / "01_mexico" / "Mexico_age_unit_calibration_v281.csv",
        preflight / "01_mexico" / "Mexico_death_code_calibration_v281.csv",
        preflight / "01_mexico" / "Mexico_coding_consensus_v281.csv",
        preflight / "01_mexico" / "Mexico_2015_2017_recoverability_v281.csv",
        preflight / "01_mexico" / "Mexico_annual_vs_consolidated_overlap_v281.csv",
        preflight / "01_mexico" / "Mexico_preferred_recovered_sources_v281.csv",
        preflight / "02_chile" / "Chile_hospital_linkage_audit_v281.csv",
        preflight / "03_ecuador" / "Ecuador_hospital_linkage_audit_v281.csv",
        preflight / "04_summary" / "Source_expansion_recommendations_v281.md",
    ]
    for source in names:
        if not source.exists():
            continue
        destination = PATHS.qc / source.name if source.suffix.lower() == ".csv" else PATHS.manuscript / source.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


# Preserve the v2.8 resolver as a fallback before overriding it.
_resolve_mexico_year_v280_fallback = resolve_mexico_year_v280


def resolve_mexico_year_v281(base_dir: Path | str, year: int) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Prefer strictly validated raw-source recovery, then fall back to v2.8.

    A recovered early-year file exists only when the independent source audit
    validated diagnosis, adult age coding, in-hospital outcome coding, and the
    stated hospital-linkage status. No raw file is consumed directly here.
    """
    recovered = _v281_recovered_path(base_dir, year)
    preferred_table = _v281_preferred_recovery_table(base_dir)
    selected = False
    source_name = ""
    source_status = ""
    if preferred_table.exists():
        try:
            preferred = pd.read_csv(preferred_table)
            row = preferred[pd.to_numeric(preferred["year"], errors="coerce").eq(year)]
            if not row.empty:
                selected = bool(row.iloc[0].get("selected", False))
                source_name = str(row.iloc[0].get("source_name", ""))
                source_status = str(row.iloc[0].get("status", ""))
        except Exception:
            selected = False
    if selected and recovered.exists():
        frame = pd.read_parquet(recovered)
        frame = normalize_country_frame(frame, "mexico")
        mortality_pct = 100 * float(frame["death_in_hospital"].notna().mean()) if len(frame) else 0.0
        hospital_pct = 100 * float(frame["hospital_id"].notna().mean()) if len(frame) else 0.0
        status = "INCLUDED_VOLUME_AND_INDIVIDUAL" if hospital_pct >= 50 else "INCLUDED_INDIVIDUAL_ONLY"
        audit = pd.DataFrame([{
            "year": int(year),
            "source_type": "RAW_RECOVERED_V281",
            "path": str(recovered),
            "source_rows": int(len(frame)),
            "canonical_columns_resolved": ",".join(sorted(frame.columns)),
            "age_method": "VALIDATED_BY_2018_2023_RAW_TO_CHECKPOINT_CALIBRATION",
            "death_method": "VALIDATED_BY_2018_2023_RAW_TO_CHECKPOINT_CALIBRATION",
            "adult_18plus_s06_rows": int(len(frame)),
            "adult_20plus_s06_rows": int(frame["primary_sample_20plus"].fillna(0).sum()),
            "mortality_available_pct": round(mortality_pct, 3),
            "hospital_id_available_pct": round(hospital_pct, 3),
            "status": status,
            "selected_for_analysis": True,
            "raw_source_name": source_name,
            "raw_recovery_status": source_status,
        }])
        return frame, audit
    return _resolve_mexico_year_v280_fallback(base_dir, year)


# All inherited preparation functions resolve this name at runtime.
resolve_mexico_year_v280 = resolve_mexico_year_v281


def _v281_require_preflight(base_dir: Path | str = DEFAULT_BASE) -> None:
    table = _v281_preferred_recovery_table(base_dir)
    if not table.exists():
        raise FileNotFoundError(
            "The independent LATAM source preflight has not been completed. "
            "Run tce_latam_source_super_audit_v281.py and "
            "run_latam_source_super_audit_v281() before final preparation."
        )


def prepare_data_v281(
    base_dir: Path | str = DEFAULT_BASE,
    clean_output: bool = True,
    rebuild_chile: bool = False,
) -> Dict[str, Any]:
    _v281_require_preflight(base_dir)
    result = prepare_data_v280(base_dir, clean_output=clean_output, rebuild_chile=rebuild_chile)
    _v281_copy_preflight_artifacts(base_dir)
    result["version"] = FINAL_VERSION
    result["preflight_root"] = str(_v281_preflight_root(base_dir))
    result["next_step"] = "Restart runtime, load v2.8.1 again, and run resume_final_analysis_v281()."
    return result


def resume_final_analysis_v281(
    base_dir: Path | str = DEFAULT_BASE,
    run_models: bool = True,
    regenerate_tables: bool = True,
    regenerate_figures: bool = True,
) -> Dict[str, Any]:
    manifest = resume_final_analysis_v280(
        base_dir,
        run_models=run_models,
        regenerate_tables=regenerate_tables,
        regenerate_figures=regenerate_figures,
    )
    manifest["version"] = FINAL_VERSION
    manifest["preflight_root"] = str(_v281_preflight_root(base_dir))
    return manifest


def run_pipeline_complete_v281(
    base_dir: Path | str = DEFAULT_BASE,
    rebuild_chile: bool = False,
    clean_output: bool = True,
) -> Dict[str, Any]:
    prepare_data_v281(base_dir, clean_output=clean_output, rebuild_chile=rebuild_chile)
    return resume_final_analysis_v281(base_dir, run_models=True, regenerate_tables=True, regenerate_figures=True)


def verify_tce_master_v281() -> Dict[str, Any]:
    activate_v280(DEFAULT_BASE)
    status = {
        "version": FINAL_VERSION,
        "preflight_required": True,
        "preflight_runner": "run_latam_source_super_audit_v281",
        "data_preparation": prepare_data_v281.__name__,
        "final_analysis": resume_final_analysis_v281.__name__,
        "complete_runner": run_pipeline_complete_v281.__name__,
        "output_root": str(PATHS.output),
        "raw_mexico_2015_2017_used_only_after_strict_calibration": True,
        "annual_mexico_source_preferred_over_consolidated": True,
        "chile_and_ecuador_linkage_audits_copied_to_final_qc": True,
        "primary_volume_exposure": "prior-year hospital TBI volume",
        "figure_language": "English",
        "returns_large_dataframes": False,
    }
    print(json.dumps(status, indent=2, ensure_ascii=False))
    return status


# Public aliases for the final release.
prepare_data = prepare_data_v281
resume_final_analysis = resume_final_analysis_v281
run_pipeline_complete = run_pipeline_complete_v281



# =============================================================================
# v2.8.4 SOURCE-LOCKED FINAL OVERRIDES
# =============================================================================
# This block consumes the independently audited v2.8.3 repaired sources.
# It keeps the v2.8 analytic engine, but replaces source selection and the
# affected country-specific analyses. The common primary window remains
# 2015-2023; Ecuador 2024 is retained in the preflight evidence but is not
# mixed into the primary multinational cohort.

FINAL_VERSION = "2.8.4"
PREFLIGHT_DIR_NAME = "analysis_v283_preflight_repair"
FINAL_OUTPUT_DIR_NAME = "analysis_v284_final"
SOURCE_LOCK_PRIMARY_YEARS = tuple(range(2015, 2024))
SOURCE_LOCK_MEXICO_EARLY_YEARS = (2015, 2016, 2017)
SOURCE_LOCK_ECUADOR_PRIMARY_YEARS = tuple(range(2015, 2024))
SOURCE_LOCK_ECUADOR_EXTENSION_YEARS = (2024,)

# Preserve the analytic engine functions before public wrappers are replaced.
_prepare_data_v280_engine = prepare_data_v280
_resume_final_analysis_v280_engine = resume_final_analysis_v280
_resolve_mexico_year_v280_engine = _resolve_mexico_year_v280_fallback


def _v284_preflight_root(base_dir: Path | str = DEFAULT_BASE) -> Path:
    return Path(base_dir) / PREFLIGHT_DIR_NAME


def _v284_mexico_audit_path(base_dir: Path | str = DEFAULT_BASE) -> Path:
    return _v284_preflight_root(base_dir) / "01_mexico" / "Mexico_2015_2017_recovery_v283.csv"


def _v284_mexico_recovered_path(base_dir: Path | str, year: int) -> Path:
    return _v284_preflight_root(base_dir) / "01_mexico" / "recovered" / f"mexico_s06_recovered_{year}_v283.parquet"


def _v284_ecuador_manifest_path(base_dir: Path | str = DEFAULT_BASE) -> Path:
    return _v284_preflight_root(base_dir) / "03_ecuador" / "Ecuador_recovery_manifest_v283.csv"


def _v284_ecuador_recovered_path(base_dir: Path | str, year: int) -> Path:
    return _v284_preflight_root(base_dir) / "03_ecuador" / "recovered" / f"ecuador_s06_recovered_{year}_v283.parquet"


def _v284_chile_audit_path(base_dir: Path | str = DEFAULT_BASE) -> Path:
    return _v284_preflight_root(base_dir) / "02_chile" / "Chile_analysis_use_v283.csv"


def _v284_bool(series: pd.Series) -> pd.Series:
    return series.astype(str).str.strip().str.lower().isin({"true", "1", "yes", "y"})


def validate_v283_source_lock(base_dir: Path | str = DEFAULT_BASE) -> pd.DataFrame:
    """Hard gate for the repaired source package before any analytic output is reset."""
    base = Path(base_dir)
    root = _v284_preflight_root(base)
    rows: List[Dict[str, Any]] = []

    rows.append({
        "component": "v2.8.3 preflight root",
        "passed": root.exists(),
        "detail": str(root),
    })

    mx_path = _v284_mexico_audit_path(base)
    if mx_path.exists():
        mx = pd.read_csv(mx_path, encoding="utf-8-sig")
        for year in SOURCE_LOCK_MEXICO_EARLY_YEARS:
            subset = mx[pd.to_numeric(mx.get("year"), errors="coerce").eq(year)]
            status = str(subset.iloc[0].get("status", "MISSING")) if not subset.empty else "MISSING"
            recovered = _v284_mexico_recovered_path(base, year)
            strict = bool(
                not subset.empty
                and status == "PASS_STRICT"
                and recovered.exists()
                and float(subset.iloc[0].get("mortality_available_pct", 0) or 0) >= 99
                and float(subset.iloc[0].get("hospital_id_available_pct", 0) or 0) >= 95
                and int(subset.iloc[0].get("adult20_s06", 0) or 0) > 0
            )
            rows.append({
                "component": f"Mexico {year} repaired source",
                "passed": strict,
                "detail": f"status={status}; path={recovered}",
            })
    else:
        for year in SOURCE_LOCK_MEXICO_EARLY_YEARS:
            rows.append({
                "component": f"Mexico {year} repaired source",
                "passed": False,
                "detail": f"missing audit: {mx_path}",
            })

    ec_path = _v284_ecuador_manifest_path(base)
    if ec_path.exists():
        ec = pd.read_csv(ec_path, encoding="utf-8-sig")
        for year in SOURCE_LOCK_ECUADOR_PRIMARY_YEARS:
            subset = ec[pd.to_numeric(ec.get("year"), errors="coerce").eq(year)]
            status = str(subset.iloc[0].get("status", "MISSING")) if not subset.empty else "MISSING"
            recovered = _v284_ecuador_recovered_path(base, year)
            valid = bool(
                not subset.empty
                and status == "PASS_INDIVIDUAL_OUTCOMES"
                and recovered.exists()
                and float(subset.iloc[0].get("mortality_available_pct", 0) or 0) >= 95
                and int(subset.iloc[0].get("adult20_s06", 0) or 0) > 0
            )
            rows.append({
                "component": f"Ecuador {year} repaired source",
                "passed": valid,
                "detail": f"status={status}; path={recovered}",
            })
        extension = _v284_ecuador_recovered_path(base, 2024)
        rows.append({
            "component": "Ecuador 2024 extension preserved",
            "passed": extension.exists(),
            "detail": f"not included in common 2015-2023 primary window; path={extension}",
        })
    else:
        for year in SOURCE_LOCK_ECUADOR_PRIMARY_YEARS:
            rows.append({
                "component": f"Ecuador {year} repaired source",
                "passed": False,
                "detail": f"missing manifest: {ec_path}",
            })

    chile_path = _v284_chile_audit_path(base)
    if chile_path.exists():
        chile = pd.read_csv(chile_path, encoding="utf-8-sig")
        primary = chile[pd.to_numeric(chile.get("year"), errors="coerce").isin(PRIMARY_YEARS)].copy()
        no_volume = bool(
            len(primary) == len(PRIMARY_YEARS)
            and not _v284_bool(primary.get("hospital_volume_primary_allowed", pd.Series(False, index=primary.index))).any()
        )
        rows.append({
            "component": "Chile hospital-volume exclusion",
            "passed": no_volume,
            "detail": "No exact establishment identifier; individual-outcome analyses only",
        })
    else:
        rows.append({
            "component": "Chile hospital-volume exclusion",
            "passed": False,
            "detail": f"missing audit: {chile_path}",
        })

    result = pd.DataFrame(rows)
    if not result["passed"].all():
        failures = result.loc[~result["passed"], ["component", "detail"]].to_dict(orient="records")
        raise RuntimeError(f"v2.8.4 source-lock validation failed: {failures}")
    return result


def _v284_copy_preflight_artifacts(base_dir: Path | str = DEFAULT_BASE) -> None:
    activate_v280(base_dir)
    root = _v284_preflight_root(base_dir)
    if not root.exists():
        return
    for source in root.rglob("*"):
        if not source.is_file() or source.suffix.lower() == ".parquet":
            continue
        if source.suffix.lower() in {".md", ".json"}:
            destination = PATHS.manuscript / source.name
        elif source.suffix.lower() == ".log":
            destination = PATHS.logs / source.name
        else:
            destination = PATHS.qc / source.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def resolve_mexico_year_v284(base_dir: Path | str, year: int) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Use strict v2.8.3 repair for 2015-2017; use annual v2.8 resolver thereafter."""
    if year not in SOURCE_LOCK_MEXICO_EARLY_YEARS:
        return _resolve_mexico_year_v280_engine(base_dir, year)

    audit_path = _v284_mexico_audit_path(base_dir)
    recovered = _v284_mexico_recovered_path(base_dir, year)
    audit_table = pd.read_csv(audit_path, encoding="utf-8-sig")
    row = audit_table[pd.to_numeric(audit_table["year"], errors="coerce").eq(year)]
    if row.empty or str(row.iloc[0].get("status")) != "PASS_STRICT" or not recovered.exists():
        status = str(row.iloc[0].get("status", "MISSING")) if not row.empty else "MISSING"
        raise RuntimeError(f"Mexico {year} source lock failed: status={status}; path={recovered}")

    frame = pd.read_parquet(recovered)
    if "record_id" not in frame:
        frame["record_id"] = pd.Series(
            [f"mexico-{year}-v283-{i}" for i in range(len(frame))],
            dtype="string",
        )
    frame = normalize_country_frame(frame, "mexico")
    mortality_pct = 100 * float(frame["death_in_hospital"].notna().mean()) if len(frame) else 0.0
    hospital_pct = 100 * float(frame["hospital_id"].notna().mean()) if len(frame) else 0.0
    audit = pd.DataFrame([{
        "year": int(year),
        "source_type": "V283_STRICT_REPAIRED_CONSOLIDATED",
        "path": str(recovered),
        "source_rows": int(len(frame)),
        "canonical_columns_resolved": ",".join(sorted(frame.columns)),
        "age_method": "CEDAD_INSP_CODE_5_YEARS_VALIDATED_2018_2023",
        "death_method": "MOTEGRE_CODE_5_DEFUNCION_OFFICIAL_DGIS",
        "adult_18plus_s06_rows": int(len(frame)),
        "adult_20plus_s06_rows": int(frame["primary_sample_20plus"].fillna(0).sum()),
        "mortality_available_pct": round(mortality_pct, 3),
        "hospital_id_available_pct": round(hospital_pct, 3),
        "status": "INCLUDED_VOLUME_AND_INDIVIDUAL",
        "selected_for_analysis": True,
        "raw_source_name": "consolidated_2013_2020",
        "raw_recovery_status": "PASS_STRICT_V283",
    }])
    return frame, audit


# All inherited Mexico audit/build helpers resolve this symbol at runtime.
resolve_mexico_year_v280 = resolve_mexico_year_v284


def _adapt_ecuador_recovered_v284(raw: pd.DataFrame, year: int) -> pd.DataFrame:
    frame = raw.copy()
    if "record_id" not in frame:
        frame["record_id"] = pd.Series(
            [f"equador-{year}-v283-{i}" for i in range(len(frame))],
            dtype="string",
        )
    if "hospital_region" not in frame or frame["hospital_region"].isna().all():
        frame["hospital_region"] = frame.get("province", pd.Series(pd.NA, index=frame.index))
    if "hospital_area" not in frame or frame["hospital_area"].isna().all():
        frame["hospital_area"] = frame.get("area", pd.Series(pd.NA, index=frame.index))
    if "residence_region" not in frame or frame["residence_region"].isna().all():
        frame["residence_region"] = frame.get("residence_province", pd.Series(pd.NA, index=frame.index))
    if "source_dataset" not in frame:
        frame["source_dataset"] = "INEC Ecuador hospital discharges — v2.8.3 recovered"
    else:
        frame["source_dataset"] = as_string(frame["source_dataset"]).fillna(
            "INEC Ecuador hospital discharges — v2.8.3 recovered"
        )
    frame["facility_capacity_linked"] = 0
    frame["hospital_id"] = pd.NA
    return normalize_country_frame(frame, "equador")


def build_ecuador_lean_v284(base_dir: Path | str = DEFAULT_BASE) -> pd.DataFrame:
    manifest = pd.read_csv(_v284_ecuador_manifest_path(base_dir), encoding="utf-8-sig")
    frames: List[pd.DataFrame] = []
    audit_rows: List[Dict[str, Any]] = []
    for year in SOURCE_LOCK_ECUADOR_PRIMARY_YEARS:
        row = manifest[pd.to_numeric(manifest["year"], errors="coerce").eq(year)]
        path = _v284_ecuador_recovered_path(base_dir, year)
        status = str(row.iloc[0].get("status", "MISSING")) if not row.empty else "MISSING"
        if status != "PASS_INDIVIDUAL_OUTCOMES" or not path.exists():
            raise RuntimeError(f"Ecuador {year} is not source-locked for outcomes: status={status}; path={path}")
        raw = pd.read_parquet(path)
        frame = _adapt_ecuador_recovered_v284(raw, year)
        if frame.empty:
            raise RuntimeError(f"Ecuador {year}: recovered file produced zero eligible records")
        mortality_pct = 100 * float(frame["death_in_hospital"].notna().mean())
        if mortality_pct < 95:
            raise RuntimeError(f"Ecuador {year}: mortality completeness {mortality_pct:.3f}% below gate")
        audit_rows.append({
            "year": year,
            "source": str(path),
            "records_18plus": int(len(frame)),
            "records_20plus": int(frame["primary_sample_20plus"].fillna(0).sum()),
            "mortality_available_pct": round(mortality_pct, 3),
            "hospital_volume_allowed": False,
            "status": "INCLUDED_INDIVIDUAL_OUTCOMES_ONLY",
        })
        frames.append(frame)
        del raw, frame
        collect_memory()
    result = pd.concat(frames, ignore_index=True, sort=False)
    save_table(pd.DataFrame(audit_rows), PATHS.qc / "Ecuador_source_lock_by_year_v284")
    return result


def build_lean_country_parts_v284(
    base_dir: Path | str = DEFAULT_BASE,
    rebuild_chile: bool = False,
) -> Dict[str, str]:
    activate_v280(base_dir)
    if rebuild_chile or not checkpoint_map_v280(base_dir)["chile"].exists():
        rebuild_chile_intermediate_v260(base_dir, force=True)

    audit_mexico_coverage_v280(base_dir)
    outputs: Dict[str, str] = {}
    audit_rows: List[Dict[str, Any]] = []

    for country in COUNTRY_ORDER:
        if country == "mexico":
            frame = build_mexico_lean_v280(base_dir)
            source_description = "Mexico 2015-2017 v2.8.3 strict repair + annual validated 2018-2023 checkpoints"
        elif country == "equador":
            frame = build_ecuador_lean_v284(base_dir)
            source_description = "Ecuador v2.8.3 repaired annual sources 2015-2023"
        else:
            source = checkpoint_map_v280(base_dir)[country]
            if not source.exists():
                raise FileNotFoundError(f"Missing {country} checkpoint: {source}")
            requested = list(dict.fromkeys(LEAN_COLUMNS + [
                "procedure_group_v2", "procedure_mapping_confidence", "primary_acute_surgery",
                "age_band_common", "age_lower", "age_upper", "age_exact_available",
            ]))
            raw = read_parquet_selected(source, requested)
            if "procedure_group" not in raw and "procedure_group_v2" in raw:
                raw["procedure_group"] = raw["procedure_group_v2"]
            if country == "chile":
                ownership = as_string(raw.get("source_dataset", pd.Series(pd.NA, index=raw.index)))
                current_sector = as_string(raw.get("facility_sector", pd.Series(pd.NA, index=raw.index)))
                raw["facility_sector"] = current_sector.where(current_sector.notna(), ownership)
            frame = normalize_country_frame(raw, country)
            source_description = str(source)
            del raw

        target = PATHS.data / f"lean_{country}_v280.parquet"
        write_parquet(frame, target)
        outputs[country] = str(target)
        year_counts = pd.to_numeric(frame["year"], errors="coerce").value_counts().sort_index()
        audit_rows.append({
            "country": country,
            "source": source_description,
            "records_18plus_available": int(len(frame)),
            "primary_20plus": int(frame["primary_sample_20plus"].fillna(0).sum()),
            "years": ",".join(map(str, year_counts.index.astype(int).tolist())),
            "year_counts_json": json.dumps({str(int(k)): int(v) for k, v in year_counts.items()}),
            "unique_hospitals": int(frame["hospital_id"].nunique(dropna=True)),
            "hospital_volume_allowed": country in VOLUME_COUNTRIES,
        })
        _log(f"Lean v2.8.4 {country}: {len(frame):,} rows -> {target}")
        del frame
        collect_memory()
        log_memory(f"after v2.8.4 lean {country}")

    audit = pd.DataFrame(audit_rows)
    save_table(audit, PATHS.qc / "Country_checkpoint_audit_v284")
    # Keep the inherited filename for downstream compatibility and validation.
    save_table(audit, PATHS.qc / "Country_checkpoint_audit_v280")
    return outputs


# The inherited preparation engine resolves this name at runtime.
build_lean_country_parts_v280 = build_lean_country_parts_v284


def apply_ecuador_labels_v284(frame: pd.DataFrame, labels: pd.DataFrame) -> pd.DataFrame:
    """Map numeric codes where metadata exist, while preserving already textual labels."""
    result = frame.copy()
    variables = labels["canonical_variable"].dropna().unique() if not labels.empty else []
    for variable in variables:
        if variable not in result:
            continue
        label_column = f"{variable}_label"
        result[label_column] = as_string(result[variable])
        subset = labels[labels["canonical_variable"].eq(variable)]
        for year, year_map in subset.groupby("year", observed=True):
            mapping = dict(zip(year_map["code"].astype(str), year_map["label"].astype(str)))
            mask = pd.to_numeric(result["year"], errors="coerce").eq(int(year))
            codes = as_string(result.loc[mask, variable]).str.replace(r"\.0+$", "", regex=True)
            mapped = codes.map(mapping)
            existing = as_string(result.loc[mask, variable])
            result.loc[mask, label_column] = mapped.where(mapped.notna(), existing)
    return result


apply_ecuador_labels_v280 = apply_ecuador_labels_v284


def run_pandemic_event_study_v284() -> Tuple[pd.DataFrame, pd.DataFrame]:
    countries = COUNTRY_ORDER
    columns = ["country", "year", "age_band_common", "sex", "trauma_subtype", "death_in_hospital", "hospital_id"]
    data = load_cohort_columns_v280(countries, columns)
    numeric_year = pd.to_numeric(data["year"], errors="coerce")
    data["pandemic_period"] = pd.cut(
        numeric_year, bins=[2014, 2019, 2021, 2023],
        labels=["Pre-pandemic", "Pandemic", "Recovery"], right=True,
    ).astype("string")
    data = native_model_frame(
        data,
        numeric=["death_in_hospital"],
        categorical=["country", "year", "pandemic_period", "age_band_common", "sex", "trauma_subtype", "hospital_id"],
    )
    data = data.dropna(subset=["death_in_hospital", "country", "pandemic_period", "age_band_common", "sex", "trauma_subtype"])
    data = data[data["death_in_hospital"].isin([0, 1])]
    rows, _ = fit_glm_v280(
        data,
        "death_in_hospital ~ C(pandemic_period, Treatment(reference='Pre-pandemic')) * C(country) + C(age_band_common) + C(sex) + C(trauma_subtype)",
        sm.families.Binomial(),
        "Pandemic-period mortality across Brazil, Mexico, Chile, and Ecuador", None, None,
        "Adjusted OR", "pandemic_period", "secondary",
    )
    rows = [r for r in rows if "pandemic_period" in r["term"]]
    event_rows: List[Dict[str, Any]] = []
    for country in countries:
        subset = data[data["country"].eq(country)].copy()
        cluster = "hospital_id" if country in VOLUME_COUNTRIES else None
        period_rows, _ = fit_glm_v280(
            subset,
            "death_in_hospital ~ C(pandemic_period, Treatment(reference='Pre-pandemic')) + C(age_band_common) + C(sex) + C(trauma_subtype)",
            sm.families.Binomial(),
            f"Pandemic-period mortality: {COUNTRY_DISPLAY[country]}", cluster, None,
            "Adjusted OR", "pandemic_period_by_country", "secondary",
        )
        for row in period_rows:
            if "pandemic_period" in row["term"]:
                row["country"] = country
                rows.append(row)
        if 2019 in pd.to_numeric(subset["year"], errors="coerce").dropna().astype(int).unique():
            year_rows, _ = fit_glm_v280(
                subset,
                "death_in_hospital ~ C(year, Treatment(reference='2019')) + C(age_band_common) + C(sex) + C(trauma_subtype)",
                sm.families.Binomial(),
                f"Adjusted annual mortality event study: {COUNTRY_DISPLAY[country]}", cluster, None,
                "Adjusted OR", f"event_study_{country}", "supplementary",
            )
            for row in year_rows:
                if "C(year" in row["term"]:
                    row["country"] = country
                    match = re.search(r"\[T\.(\d{4})\]", row["term"])
                    row["year"] = int(match.group(1)) if match else np.nan
                    event_rows.append(row)
        del subset
        collect_memory()
    result = apply_fdr_by_family_v280(pd.DataFrame(rows))
    event = apply_fdr_by_family_v280(pd.DataFrame(event_rows))
    save_table(result, PATHS.tables / "Supplementary_Table_8_Pandemic_period_models")
    save_table(event, PATHS.tables / "Supplementary_Table_9_Annual_event_study")
    del data
    collect_memory()
    return result, event


run_pandemic_event_study_v280 = run_pandemic_event_study_v284


def run_clean_country_specific_models_v284(base_dir: Path | str = DEFAULT_BASE) -> Tuple[pd.DataFrame, pd.DataFrame]:
    labels = extract_ecuador_value_labels_v280(base_dir)
    descriptive_rows: List[Dict[str, Any]] = []
    model_rows: List[Dict[str, Any]] = []
    specifications = {
        "chile": [
            "insurance_type", "facility_sector", "ethnicity",
            "any_surgical_intervention", "residence_region",
        ],
        "equador": [
            "facility_sector", "facility_class", "facility_type", "facility_entity",
            "ethnicity", "hospital_region", "residence_region", "residence_area",
            "discharge_specialty",
        ],
    }
    for country, predictors in specifications.items():
        path = cohort_path_v280(country)
        base_columns = ["year", "death_in_hospital", "los_days", "age_band_common", "sex", "trauma_subtype"]
        available = set(parquet_columns(path))
        selected = base_columns + [p for p in predictors if p in available]
        data = pd.read_parquet(path, columns=selected)
        if country == "equador":
            data = apply_ecuador_labels_v280(data, labels)
        data = native_model_frame(
            data,
            numeric=["death_in_hospital", "los_days"],
            categorical=["year", "age_band_common", "sex", "trauma_subtype"] + [
                c for c in data if c not in ("death_in_hospital", "los_days")
            ],
        )
        data = data[data["death_in_hospital"].isin([0, 1])]
        for predictor in predictors:
            label_candidate = f"{predictor}_label"
            model_predictor = label_candidate if country == "equador" and label_candidate in data else predictor
            if model_predictor not in data:
                continue
            eligible_years = eligible_years_for_predictor_v280(data, model_predictor, 0.70)
            subset = data[data["year"].astype(float).isin(eligible_years)].copy()
            subset[model_predictor] = _collapse_rare_complete_levels_v280(subset[model_predictor])
            subset = subset.dropna(subset=[model_predictor, "death_in_hospital", "age_band_common", "sex", "trauma_subtype", "year"])
            if len(subset) < 500 or subset[model_predictor].nunique() < 2:
                continue
            for level, group in subset.groupby(model_predictor, observed=True):
                descriptive_rows.append({
                    "country": country,
                    "predictor": predictor,
                    "display_variable": model_predictor,
                    "eligible_years": ",".join(map(str, eligible_years)),
                    "level": level,
                    "admissions": len(group),
                    "deaths": int(group["death_in_hospital"].sum()),
                    "mortality_pct": 100 * float(group["death_in_hospital"].mean()),
                    "los_median": float(pd.to_numeric(group["los_days"], errors="coerce").median()),
                })
            model_rows_for_predictor, _ = fit_glm_v280(
                subset,
                f"death_in_hospital ~ C({model_predictor}) + C(age_band_common) + C(sex) + C(trauma_subtype) + C(year)",
                sm.families.Binomial(),
                f"Exploratory {COUNTRY_DISPLAY[country]} association: {predictor}", None, None,
                "Adjusted OR", f"{country}_{predictor}", "exploratory",
            )
            for model_row in model_rows_for_predictor:
                if model_predictor in model_row["term"]:
                    model_row["country"] = country
                    model_row["predictor"] = predictor
                    model_row["eligible_years"] = ",".join(map(str, eligible_years))
                    model_rows.append(model_row)
            del subset
            collect_memory()
        del data
        collect_memory()
    descriptive = pd.DataFrame(descriptive_rows)
    models = apply_fdr_by_family_v280(pd.DataFrame(model_rows))
    save_table(descriptive, PATHS.tables / "Supplementary_Table_11_Country_specific_descriptive_factors")
    save_table(models, PATHS.tables / "Supplementary_Table_12_Country_specific_exploratory_models")
    return descriptive, models


run_clean_country_specific_models_v280 = run_clean_country_specific_models_v284


def write_statistical_analysis_plan_v284() -> Path:
    text = """# Frozen statistical analysis plan — TCE Multinational v2.8.4

## Study design
Retrospective multinational cohort of hospital discharge records with a principal S06.x diagnosis. The primary harmonized cohort includes patients aged 20 years or older in Brazil, Mexico, Chile, and Ecuador from 2015 through 2023. Chile is released in grouped ages, so 20 years was selected as the common lower bound. A sensitivity cohort includes patients aged 18–19 years where exact age is available. Ecuador 2024 is preserved as a source extension but is not mixed into the primary common-period cohort.

## Source lock
Mexico 2015–2017 is derived from the consolidated 2013–2020 SAEH/DGIS source after exact agreement with cached S06 and adult-age counts; age-unit code 5 denotes years and MOTEGRE code 5 denotes in-hospital death. Mexico 2018–2023 uses validated annual checkpoints. Ecuador 2015–2023 uses one selected annual source per year with at least 95% mortality completeness. Chile has no exact public establishment identifier and is excluded from hospital-volume analyses. Ecuador composite capacity linkage is ecological only and is not treated as hospital-level linkage.

## Primary descriptive objective
Describe admissions, in-hospital mortality, length of stay, age, sex, administrative TBI phenotype, and temporal trends across the four participating health systems. Country comparisons are descriptive and are not interpreted as rankings of quality.

## Primary hospital-volume objective
Evaluate the association between prior-year hospital TBI volume and in-hospital mortality in Brazil and Mexico, the countries with stable hospital identifiers. Prior-year volume is the primary exposure to reduce simultaneity. Same-year volume is a sensitivity exposure.

## Primary volume model
Patient-level logistic generalized linear model with hospital-clustered standard errors. Adjustment: age restricted spline, sex, administrative TBI subtype, and country-year fixed effects. Exposure: country-year standardized log prior-year hospital volume.

## Prespecified volume sensitivities
Same-year volume; country interaction; country-specific estimates; exclusion of hospital-years with fewer than five admissions; age >=18 sensitivity; structural intracranial injury subset; post-admission ICU/surgery sensitivity in Brazil; Gamma and negative-binomial survivor length-of-stay models.

## Nonlinear association
Country-specific restricted cubic spline models of absolute prior-year hospital volume. Standardized adjusted predictions are generated over the 5th to 95th percentiles. A likelihood-ratio test compares spline and linear specifications.

## Hospital variation and risk standardization
Country-specific mortality risk models excluding hospital volume are used to calculate expected deaths. Hospital-year observed and expected mortality are aggregated. Hospital estimates are stabilized with empirical-Bayes random-effects shrinkage of log observed-to-expected mortality. Funnel plots use Poisson 95% and 99.8% control limits.

## Within-hospital analyses
Risk-adjusted hospital-year mortality differences are analyzed using weighted two-way hospital and year fixed-effects residualization. A contiguous-year first-difference model is a supporting sensitivity. These analyses estimate whether changes in a hospital's own volume are associated with changes in its risk-adjusted mortality.

## Individual-level factors
Age band, sex, and administrative TBI phenotype are modeled in the four-country cohort with country-year adjustment. Country-specific models are supplementary.

## Pandemic analyses
Brazil, Mexico, Chile, and Ecuador are included because all four now have validated individual mortality through 2023. Period models classify 2015–2019 as pre-pandemic, 2020–2021 as pandemic, and 2022–2023 as recovery. Adjusted annual event studies use 2019 as reference.

## Country-specific exploratory analyses
Chile and Ecuador variables are analyzed only in years with at least 70% informative completeness and at least two levels. Missing values are not coded as absence. Rare levels are collapsed. Chile facility ownership is retained as a system-level attribute, not a hospital identifier. Ecuador numeric labels are mapped from official SAV metadata when available, while already textual labels are preserved.

## Multiplicity
The primary hospital-volume hypothesis is interpreted using its nominal two-sided p value and confidence interval. Benjamini-Hochberg correction is applied separately within prespecified secondary/exploratory families.

## Interpretation
All estimates are associations. Residual confounding by injury severity, referral, transfer, imaging findings, physiology, and indication for surgery is expected. The volume coefficient is not interpreted as a causal measure of hospital quality. Chile and Ecuador must not be used for hospital-volume inference.
"""
    path = PATHS.manuscript / "Frozen_statistical_analysis_plan_v284.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


write_statistical_analysis_plan_v280 = write_statistical_analysis_plan_v284


def write_analysis_manifest_v284(extra: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    validation_path = PATHS.qc / "Final_cohort_validation_v280.csv"
    cohort_validation = pd.read_csv(validation_path).to_dict(orient="records") if validation_path.exists() else []
    manifest: Dict[str, Any] = {
        "version": FINAL_VERSION,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "output_dir": str(PATHS.output),
        "source_preflight": str(_v284_preflight_root(PATHS.base)),
        "primary_years": list(PRIMARY_YEARS),
        "primary_age_policy": "Age >=20 years in all four countries",
        "sensitivity_age_policy": "Age >=18 years where exact age is available; Chile remains >=20",
        "primary_volume_exposure": "Prior-year country-year standardized log hospital TBI volume",
        "volume_countries": ["Brazil", "Mexico"],
        "individual_level_countries": ["Brazil", "Mexico", "Chile", "Ecuador"],
        "country_source_lock": {
            "Brazil": "Validated SIH/SUS intermediate checkpoint, 2015-2023",
            "Mexico": "v2.8.3 strict consolidated recovery for 2015-2017 plus validated annual checkpoints for 2018-2023",
            "Chile": "Official annual discharge files, 2015-2023; no exact hospital identifier",
            "Ecuador": "v2.8.3 selected annual individual-outcome sources, 2015-2023; 2024 preserved outside common-period primary cohort",
        },
        "cohort_validation": cohort_validation,
        "methodological_upgrades": [
            "Strict source-locked recovery of Mexico 2015-2017",
            "Complete Ecuador individual mortality series for 2015-2023",
            "Four-country pandemic and annual event-study analyses",
            "Explicit exclusion of Chile and Ecuador from hospital-volume inference",
            "Preservation of textual Ecuador labels and official numeric metadata labels",
            "Chile facility ownership retained without misclassifying it as hospital identity",
            "Contiguous-year prior-volume definition",
            "Restricted cubic spline volume models",
            "Within-hospital two-way fixed-effects and first-difference sensitivities",
            "Empirical-Bayes hierarchical hospital risk standardization",
            "Poisson funnel plots",
            "Gamma and negative-binomial LOS models",
            "Year-specific informative availability audit",
            "Family-specific false-discovery-rate control",
        ],
        "figure_language": "English",
        "returns_large_dataframes": False,
    }
    if extra:
        manifest.update(dict(extra))
    path = PATHS.manuscript / "analysis_manifest_v284.json"
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return manifest


write_analysis_manifest_v280 = write_analysis_manifest_v284


def prepare_data_v284(
    base_dir: Path | str = DEFAULT_BASE,
    clean_output: bool = True,
    rebuild_chile: bool = False,
) -> Dict[str, Any]:
    gate = validate_v283_source_lock(base_dir)
    result = _prepare_data_v280_engine(
        base_dir,
        clean_output=clean_output,
        rebuild_chile=rebuild_chile,
    )
    _v284_copy_preflight_artifacts(base_dir)
    save_table(gate, PATHS.qc / "Source_lock_gate_v284")
    result["version"] = FINAL_VERSION
    result["preflight_root"] = str(_v284_preflight_root(base_dir))
    result["primary_years"] = list(PRIMARY_YEARS)
    result["next_step"] = "Restart runtime, load v2.8.4 again, and run resume_final_analysis_v284()."
    return result


def resume_final_analysis_v284(
    base_dir: Path | str = DEFAULT_BASE,
    run_models: bool = True,
    regenerate_tables: bool = True,
    regenerate_figures: bool = True,
) -> Dict[str, Any]:
    validate_v283_source_lock(base_dir)
    manifest = _resume_final_analysis_v280_engine(
        base_dir,
        run_models=run_models,
        regenerate_tables=regenerate_tables,
        regenerate_figures=regenerate_figures,
    )
    manifest["version"] = FINAL_VERSION
    manifest["preflight_root"] = str(_v284_preflight_root(base_dir))
    return manifest


def run_pipeline_complete_v284(
    base_dir: Path | str = DEFAULT_BASE,
    rebuild_chile: bool = False,
    clean_output: bool = True,
) -> Dict[str, Any]:
    prepare_data_v284(base_dir, clean_output=clean_output, rebuild_chile=rebuild_chile)
    return resume_final_analysis_v284(
        base_dir,
        run_models=True,
        regenerate_tables=True,
        regenerate_figures=True,
    )


def verify_tce_master_v284(base_dir: Path | str = DEFAULT_BASE) -> Dict[str, Any]:
    activate_v280(base_dir)
    gate = validate_v283_source_lock(base_dir)
    status = {
        "version": FINAL_VERSION,
        "source_lock_passed": bool(gate["passed"].all()),
        "source_lock_checks": int(len(gate)),
        "data_preparation": prepare_data_v284.__name__,
        "final_analysis": resume_final_analysis_v284.__name__,
        "complete_runner": run_pipeline_complete_v284.__name__,
        "output_root": str(PATHS.output),
        "primary_years": list(PRIMARY_YEARS),
        "mexico_2015_2017": "PASS_STRICT v2.8.3 repaired consolidated source",
        "ecuador_2015_2023": "PASS_INDIVIDUAL_OUTCOMES v2.8.3 selected annual sources",
        "ecuador_2024": "Preserved extension; excluded from common-period primary cohort",
        "hospital_volume_countries": ["Brazil", "Mexico"],
        "pandemic_countries": ["Brazil", "Mexico", "Chile", "Ecuador"],
        "figure_language": "English",
        "returns_large_dataframes": False,
    }
    print(json.dumps(status, indent=2, ensure_ascii=False))
    return status


# Public aliases for the source-locked final release.
prepare_data = prepare_data_v284
resume_final_analysis = resume_final_analysis_v284
run_pipeline_complete = run_pipeline_complete_v284

print(f"✅ TCE Master v{FINAL_VERSION} loaded — source-locked final analytic suite.")
print("Required sequence:")
print("  Session 1: verify_tce_master_v284()")
print("  Session 1: prepare_data_v284(clean_output=True, rebuild_chile=False)")
print("  Restart runtime")
print("  Session 2: resume_final_analysis_v284(run_models=True)")
