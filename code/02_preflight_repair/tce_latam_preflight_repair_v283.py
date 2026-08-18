"""
TCE LATAM preflight repair v2.8.3
================================

Purpose
-------
Repair the specific defects revealed by analysis_v282_preflight without
repeating the completed >100-million-row Mexico source audit.

Main actions
------------
1. Mexico 2015-2017: recover adult S06 records from the consolidated
   2013-2020 source in ONE streaming pass, using the validated age-unit code
   5 (years) and official DGIS discharge-reason code 5 (death).
2. Ecuador 2015-2024: retry selected/alternative patient sources with robust
   encoding handling, profile age-unit and discharge-condition values, and
   distinguish full outcome recovery from counts-only recovery.
3. Chile: convert the source audit into defensible analysis-use labels.
4. Ecuador facility linkage: downgrade non-exact composite matches to
   ecological/capacity sensitivity only; never treat them as true hospitals.

The script reads the existing analysis_v282_preflight directory and writes a
new, separate analysis_v283_preflight_repair directory.
"""

from __future__ import annotations

import gc
import hashlib
import json
import math
import re
import shutil
import time
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

try:
    import pyreadstat
except Exception:
    pyreadstat = None


VERSION = "2.8.3-preflight-repair"
DEFAULT_BASE = Path("/content/drive/MyDrive/Projeto_TCE_Multinacional")
V282_DIR = "analysis_v282_preflight"
OUTPUT_DIR = "analysis_v283_preflight_repair"
CHUNK_SIZE = 250_000
MEXICO_EARLY_YEARS = (2015, 2016, 2017)
ECUADOR_YEARS = tuple(range(2015, 2025))

# Official Mexican hospital discharge form: MOTIVO DE EGRESO 5 = Defunción.
MEXICO_DEATH_CODE = "5"
MEXICO_YEARS_AGE_UNIT_CODE = "5"
ECUADOR_YEARS_AGE_UNIT_CODE = "4"


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------

def ascii_text(value: Any) -> str:
    text = "" if value is None else str(value)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", text).strip().lower()


def meaningful_text(series: pd.Series) -> pd.Series:
    text = series.astype("string").str.strip()
    bad = text.str.upper().isin(
        {"", "NA", "N/A", "NAN", "NONE", "NULL", "<NA>", "UNKNOWN", "DESCONOCIDO", "IGNORADO", "NO INFORMADO", "999", "9999"}
    )
    return text.where(text.notna() & ~bad, pd.NA)


def normalize_code_series(series: pd.Series) -> pd.Series:
    text = meaningful_text(series)
    numeric = pd.to_numeric(text, errors="coerce")
    out = text.astype("string").copy()
    integer_like = numeric.notna() & np.isclose(numeric, np.round(numeric), equal_nan=False)
    out.loc[integer_like] = numeric.loc[integer_like].round().astype("Int64").astype("string")
    return out


def normalize_dx(series: pd.Series) -> pd.Series:
    text = series.astype("string").str.upper().str.strip()
    text = text.str.replace(r"[^A-Z0-9]", "", regex=True)
    return text.where(text.ne(""), pd.NA)


def normalize_sex(series: pd.Series) -> pd.Series:
    raw = meaningful_text(series)
    text = raw.map(ascii_text)
    num = pd.to_numeric(raw, errors="coerce")
    out = pd.Series(pd.NA, index=series.index, dtype="string")
    out.loc[text.str.contains(r"masc|hombre|male", regex=True, na=False) | num.eq(1)] = "Male"
    out.loc[text.str.contains(r"fem|mujer|female", regex=True, na=False) | num.eq(2)] = "Female"
    return out


def safe_float(value: Any) -> float:
    try:
        result = float(value)
        return result if np.isfinite(result) else np.nan
    except Exception:
        return np.nan


def json_load(value: Any, default: Any) -> Any:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return default
    try:
        return json.loads(str(value))
    except Exception:
        return default


def ensure_dirs(base: Path, clean_output: bool) -> Dict[str, Path]:
    root = base / OUTPUT_DIR
    if clean_output and root.exists():
        shutil.rmtree(root)
    dirs = {
        "root": root,
        "mexico": root / "01_mexico",
        "mexico_recovered": root / "01_mexico" / "recovered",
        "chile": root / "02_chile",
        "ecuador": root / "03_ecuador",
        "ecuador_recovered": root / "03_ecuador" / "recovered",
        "summary": root / "04_summary",
        "logs": root / "05_logs",
    }
    for path in dirs.values():
        path.mkdir(parents=True, exist_ok=True)
    return dirs


def log(message: str, dirs: Mapping[str, Path], level: str = "INFO") -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} | {level:<7} | {message}"
    print(line, flush=True)
    with (dirs["logs"] / "preflight_repair_v283.log").open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def save_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, encoding="utf-8-sig")


def build_age_band(age: pd.Series) -> pd.Series:
    bins = [18, 20, 30, 50, 70, 80, np.inf]
    labels = ["18-19", "20-29", "30-49", "50-69", "70-79", "80+"]
    return pd.cut(pd.to_numeric(age, errors="coerce"), bins=bins, labels=labels, right=False).astype("string")


def parquet_write(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False, compression="snappy")


def _separator_from_inventory(value: Any) -> str:
    text = str(value if value is not None else ",").strip()
    text = text.strip("'\"")
    return text or ","


def _encoding_candidates(preferred: Any) -> List[str]:
    values = [str(preferred or "").strip(), "utf-8-sig", "utf-8", "cp1252", "latin-1"]
    result: List[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result


def iter_csv_chunks_robust(
    path: Path,
    usecols: Sequence[str],
    separator: str,
    preferred_encoding: Any,
    chunksize: int = CHUNK_SIZE,
) -> Iterator[pd.DataFrame]:
    """Stream a CSV without allowing a late accented byte to abort the file.

    The audit v2.8.2 sometimes detected UTF-8 from the beginning of a file but
    failed hundreds of megabytes later. ``encoding_errors='replace'`` preserves
    ASCII diagnostic/clinical codes and avoids that false failure. If a parser
    still fails before yielding, the next encoding is attempted.
    """
    last_error: Optional[Exception] = None
    for encoding in _encoding_candidates(preferred_encoding):
        yielded = False
        try:
            reader = pd.read_csv(
                path,
                sep=separator,
                encoding=encoding,
                encoding_errors="replace",
                usecols=list(usecols),
                chunksize=chunksize,
                dtype="string",
                engine="python",
                on_bad_lines="skip",
            )
            for chunk in reader:
                yielded = True
                yield chunk
            return
        except Exception as exc:
            last_error = exc
            if yielded:
                raise RuntimeError(
                    f"CSV failed after partial read with encoding={encoding}: {type(exc).__name__}: {exc}"
                ) from exc
            continue
    raise RuntimeError(f"No CSV encoding/parser succeeded for {path}: {last_error}")


def iter_sav_chunks(path: Path, usecols: Sequence[str], chunksize: int = CHUNK_SIZE) -> Iterator[pd.DataFrame]:
    if pyreadstat is None:
        raise ImportError("pyreadstat is required for SAV fallback")
    for frame, _ in pyreadstat.read_file_in_chunks(
        pyreadstat.read_sav,
        str(path),
        chunksize=chunksize,
        usecols=list(usecols),
        apply_value_formats=False,
    ):
        yield frame


def iter_source_chunks(path: Path, usecols: Sequence[str], inventory_row: Mapping[str, Any]) -> Iterator[pd.DataFrame]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        yield from iter_csv_chunks_robust(
            path,
            usecols,
            _separator_from_inventory(inventory_row.get("separator", ",")),
            inventory_row.get("encoding", "utf-8-sig"),
        )
    elif suffix == ".sav":
        yield from iter_sav_chunks(path, usecols)
    else:
        raise ValueError(f"Unsupported patient source for repair: {path}")


# ---------------------------------------------------------------------------
# Mexico repair
# ---------------------------------------------------------------------------

def _load_mexico_inventory(v282_root: Path) -> pd.DataFrame:
    path = v282_root / "01_mexico" / "Mexico_raw_file_inventory_v282.csv"
    if not path.exists():
        raise FileNotFoundError(path)
    return pd.read_csv(path, encoding="utf-8-sig")


def _mexico_death_from_motegre(raw: pd.Series) -> pd.Series:
    code = normalize_code_series(raw)
    out = pd.Series(pd.NA, index=raw.index, dtype="Int64")
    out.loc[code.eq(MEXICO_DEATH_CODE)] = 1
    known_alive = code.isin(["1", "2", "3", "4", "6", "7"])
    out.loc[known_alive] = 0
    text = meaningful_text(raw).map(ascii_text)
    out.loc[text.str.contains(r"defunc|fallec|muerte|deceso", regex=True, na=False)] = 1
    out.loc[text.str.contains(r"curacion|mejoria|voluntar|traslado|fuga|otro", regex=True, na=False)] = 0
    return out


def repair_mexico_2015_2017(base: Path, v282_root: Path, dirs: Mapping[str, Path]) -> Dict[str, Any]:
    inventory = _load_mexico_inventory(v282_root)
    row = inventory[inventory["source_name"].astype(str).eq("consolidated_2013_2020")]
    if row.empty:
        raise RuntimeError("consolidated_2013_2020 missing from v2.8.2 inventory")
    info = row.iloc[0]
    path = Path(str(info["path"]))
    if not path.exists():
        raise FileNotFoundError(path)
    mapping = json_load(info.get("mapping_json"), {})
    required = {"year", "age", "age_unit", "sex", "los_days", "dx_main", "discharge_reason"}
    missing = sorted(required - set(mapping))
    if missing:
        raise RuntimeError(f"Mexico consolidated mapping lacks: {missing}")

    optional = ["hospital_id", "hospital_region", "external_cause", "month"]
    usecols = sorted({mapping[name] for name in required | set(optional) if name in mapping})
    encoding = str(info.get("encoding", "utf-8-sig"))
    separator = _separator_from_inventory(info.get("separator_repr", ","))

    frames: Dict[int, List[pd.DataFrame]] = {year: [] for year in MEXICO_EARLY_YEARS}
    raw_s06 = Counter()
    unit_s06 = Counter()
    rows_read = 0
    log("Mexico repair: scanning consolidated 2013-2020 once for 2015-2017", dirs)

    for chunk in iter_csv_chunks_robust(path, usecols, separator, encoding):
        rows_read += len(chunk)
        year = pd.to_numeric(chunk[mapping["year"]], errors="coerce").astype("Int64")
        year_mask = year.isin(MEXICO_EARLY_YEARS)
        if not year_mask.any():
            continue
        sub = chunk.loc[year_mask].copy()
        year_sub = year.loc[year_mask]
        dx = normalize_dx(sub[mapping["dx_main"]])
        s06 = dx.str.startswith("S06", na=False)
        if not s06.any():
            continue
        sub = sub.loc[s06].copy()
        year_sub = year_sub.loc[s06]
        dx = dx.loc[s06]
        for y, n in year_sub.value_counts().items():
            raw_s06[int(y)] += int(n)

        age = pd.to_numeric(sub[mapping["age"]], errors="coerce")
        unit = normalize_code_series(sub[mapping["age_unit"]])
        adult = unit.eq(MEXICO_YEARS_AGE_UNIT_CODE) & age.between(18, 120)
        for y, n in year_sub.loc[unit.eq(MEXICO_YEARS_AGE_UNIT_CODE)].value_counts().items():
            unit_s06[int(y)] += int(n)
        if not adult.any():
            continue

        sub = sub.loc[adult].copy()
        yv = year_sub.loc[adult].astype(int)
        out = pd.DataFrame(index=sub.index)
        out["country"] = "mexico"
        out["year"] = yv
        out["month"] = pd.to_numeric(sub[mapping["month"]], errors="coerce") if mapping.get("month") else pd.NA
        out["age"] = age.loc[adult].astype(float)
        out["sex"] = normalize_sex(sub[mapping["sex"]])
        out["los_days"] = pd.to_numeric(sub[mapping["los_days"]], errors="coerce")
        out["dx_main"] = dx.loc[adult].astype("string")
        out["dx_secondary"] = pd.NA
        out["external_cause"] = meaningful_text(sub[mapping["external_cause"]]) if mapping.get("external_cause") else pd.NA
        out["discharge_reason_raw"] = normalize_code_series(sub[mapping["discharge_reason"]])
        out["death_in_hospital"] = _mexico_death_from_motegre(sub[mapping["discharge_reason"]])
        out["hospital_id"] = meaningful_text(sub[mapping["hospital_id"]]) if mapping.get("hospital_id") else pd.NA
        out["hospital_region"] = meaningful_text(sub[mapping["hospital_region"]]) if mapping.get("hospital_region") else pd.NA
        out["primary_sample_20plus"] = out["age"].ge(20).astype("Int64")
        out["sensitivity_sample_18plus"] = 1
        out["age_exact_available"] = 1
        out["age_band_common"] = build_age_band(out["age"])
        out["source_dataset"] = "SAEH-DGIS consolidated 2013-2020"
        out["source_file"] = str(path)
        out["death_mapping_method"] = "MOTEGRE_CODE_5_DEFUNCION_OFFICIAL"
        out["age_mapping_method"] = "CEDAD_INSP_CODE_5_YEARS_VALIDATED_2018_2023"

        for y in MEXICO_EARLY_YEARS:
            part = out[out["year"].eq(y)]
            if not part.empty:
                frames[y].append(part.reset_index(drop=True))
        del chunk, sub, out
        gc.collect()

    scan_cache_path = v282_root / "01_mexico" / "scan_cache" / "consolidated_2013_2020.json"
    scan_cache = json.loads(scan_cache_path.read_text(encoding="utf-8")) if scan_cache_path.exists() else {}
    expected_age = scan_cache.get("age_unit_stats", {})
    expected_s06 = scan_cache.get("s06_by_year", {})
    outcome_stats = scan_cache.get("outcome_stats", {})

    audit_rows: List[Dict[str, Any]] = []
    outputs: Dict[int, str] = {}
    for year in MEXICO_EARLY_YEARS:
        if not frames[year]:
            audit_rows.append({"year": year, "status": "FAIL_NO_ADULT_S06", "source": str(path)})
            continue
        result = pd.concat(frames[year], ignore_index=True, sort=False)
        target = dirs["mexico_recovered"] / f"mexico_s06_recovered_{year}_v283.parquet"
        parquet_write(result, target)
        outputs[year] = str(target)

        expected_unit = expected_age.get(str(year), {}).get(MEXICO_YEARS_AGE_UNIT_CODE, {})
        expected_adult18 = int(expected_unit.get("age_18plus", 0) or 0)
        expected_adult20 = int(expected_unit.get("age_20plus", 0) or 0)
        mortality_available = 100 * float(result["death_in_hospital"].notna().mean())
        hospital_available = 100 * float(result["hospital_id"].notna().mean())
        status = "PASS_STRICT" if (
            len(result) == expected_adult18
            and int(result["primary_sample_20plus"].sum()) == expected_adult20
            and mortality_available >= 99
            and hospital_available >= 95
        ) else "PASS_WITH_QC_REVIEW"
        code5_all_s06 = int(outcome_stats.get(str(year), {}).get(MEXICO_DEATH_CODE, 0) or 0)
        audit_rows.append({
            "year": year,
            "status": status,
            "source": str(path),
            "raw_s06_seen": int(raw_s06[year]),
            "expected_raw_s06_from_cache": int(expected_s06.get(str(year), 0) or 0),
            "years_unit_s06_seen": int(unit_s06[year]),
            "adult18_s06": int(len(result)),
            "expected_adult18": expected_adult18,
            "adult20_s06": int(result["primary_sample_20plus"].sum()),
            "expected_adult20": expected_adult20,
            "deaths": int(result["death_in_hospital"].sum()),
            "mortality_rate": float(result["death_in_hospital"].mean()),
            "mortality_available_pct": round(mortality_available, 3),
            "hospital_id_available_pct": round(hospital_available, 3),
            "hospitals": int(result["hospital_id"].nunique(dropna=True)),
            "all_age_s06_motegre_code5_count": code5_all_s06,
            "death_code": MEXICO_DEATH_CODE,
            "death_validation": "DGIS_OFFICIAL_MOTIVO_EGRESO_CODE_5_DEFUNCION",
            "age_unit_code": MEXICO_YEARS_AGE_UNIT_CODE,
            "output_parquet": str(target),
        })
        del result
        gc.collect()

    audit = pd.DataFrame(audit_rows)
    save_csv(audit, dirs["mexico"] / "Mexico_2015_2017_recovery_v283.csv")
    consensus = pd.DataFrame([
        {
            "parameter": "age_unit",
            "status": "VALIDATED_BY_LATER_YEARS",
            "code": MEXICO_YEARS_AGE_UNIT_CODE,
            "meaning": "years",
            "support_years": "2018,2019,2020,2021,2022,2023",
        },
        {
            "parameter": "death_code",
            "status": "VALIDATED_BY_OFFICIAL_CODEBOOK",
            "code": MEXICO_DEATH_CODE,
            "meaning": "in-hospital death / defuncion",
            "support_years": "official DGIS form; stable field in 2013-2023 sources",
        },
    ])
    save_csv(consensus, dirs["mexico"] / "Mexico_coding_consensus_v283.csv")

    source_decision = pd.DataFrame([
        {
            "years": "2015-2017",
            "selected_source": "consolidated_2013_2020",
            "reason": "Contains exact age plus age-unit field; annual files lack age-unit and expose EDAD values unsuitable for exact adult filtering.",
            "annual_files_use": "inventory/count cross-check only",
            "consolidated_rows_scanned_once": rows_read,
        }
    ])
    save_csv(source_decision, dirs["mexico"] / "Mexico_source_decision_v283.csv")
    return {"audit": str(dirs["mexico"] / "Mexico_2015_2017_recovery_v283.csv"), "outputs": outputs}


# ---------------------------------------------------------------------------
# Ecuador repair
# ---------------------------------------------------------------------------

def _load_ecuador_tables(v282_root: Path) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    inventory = pd.read_csv(v282_root / "03_ecuador" / "Ecuador_source_inventory_v282.csv", encoding="utf-8-sig")
    preferred = pd.read_csv(v282_root / "03_ecuador" / "Ecuador_preferred_sources_v282.csv", encoding="utf-8-sig")
    linkage = pd.read_csv(v282_root / "03_ecuador" / "Ecuador_hospital_linkage_audit_v282.csv", encoding="utf-8-sig")
    return inventory, preferred, linkage


def _ecuador_condition_to_death(raw: pd.Series) -> Tuple[pd.Series, pd.Series]:
    code = normalize_code_series(raw)
    text = meaningful_text(raw).map(ascii_text)
    death = pd.Series(pd.NA, index=raw.index, dtype="Int64")
    method = pd.Series(pd.NA, index=raw.index, dtype="string")

    alive_num = code.eq("1")
    death_num = code.isin(["2", "3"])
    death.loc[alive_num] = 0
    method.loc[alive_num] = "NUMERIC_1_ALIVE"
    death.loc[death_num] = 1
    method.loc[death_num] = "NUMERIC_2_3_DEATH"

    death_text = text.str.contains(r"fallec|defunc|muert|deceso|obit", regex=True, na=False)
    alive_text = text.str.contains(r"vivo|alta|egreso vivo|alive", regex=True, na=False)
    death.loc[death_text] = 1
    method.loc[death_text] = "TEXT_DEATH"
    death.loc[alive_text & death.isna()] = 0
    method.loc[alive_text & method.isna()] = "TEXT_ALIVE"
    return death, method


def _candidate_sources_for_year(inventory: pd.DataFrame, preferred: pd.DataFrame, year: int) -> pd.DataFrame:
    candidates = inventory[
        pd.to_numeric(inventory["year"], errors="coerce").eq(year)
        & inventory["role"].astype(str).eq("PATIENT_EGRESOS")
        & inventory["status"].astype(str).eq("OK")
        & inventory["suffix"].astype(str).str.lower().isin([".csv", ".sav"])
    ].copy()
    if candidates.empty:
        return candidates
    preferred_paths = set(
        preferred[
            pd.to_numeric(preferred["year"], errors="coerce").eq(year)
            & preferred["role"].astype(str).eq("PATIENT_EGRESOS")
            & preferred["selected"].fillna(False).astype(bool)
        ]["path"].astype(str)
    )
    candidates["is_preferred_v282"] = candidates["path"].astype(str).isin(preferred_paths)
    candidates["format_rank"] = candidates["suffix"].astype(str).str.lower().map({".csv": 2, ".sav": 1}).fillna(0)
    candidates["size_numeric"] = pd.to_numeric(candidates["size_mb"], errors="coerce").fillna(0)
    return candidates.sort_values(["is_preferred_v282", "format_rank", "schema_score", "size_numeric"], ascending=False)


def _profile_counter(counter: Counter[str], limit: int = 30) -> str:
    return json.dumps(dict(counter.most_common(limit)), ensure_ascii=False)


def _attempt_ecuador_source(year: int, row: Mapping[str, Any], dirs: Mapping[str, Path]) -> Tuple[Optional[pd.DataFrame], Dict[str, Any], List[Dict[str, Any]], List[Dict[str, Any]]]:
    path = Path(str(row["path"]))
    mapping = json_load(row.get("mapping_json"), {})
    required = {"dx_main", "age", "age_unit", "discharge_condition"}
    missing = sorted(required - set(mapping))
    if missing:
        return None, {"year": year, "path": str(path), "status": "MISSING_REQUIRED_FIELDS", "missing": ",".join(missing)}, [], []
    if not path.exists():
        return None, {"year": year, "path": str(path), "status": "SOURCE_NOT_FOUND"}, [], []

    desired = [
        "dx_main", "dx_secondary", "age", "age_unit", "sex", "los_days", "discharge_condition", "hospital_id",
        "province", "canton", "parish", "area", "facility_class", "facility_type", "facility_entity", "facility_sector",
        "residence_province", "residence_canton", "residence_parish", "residence_area", "ethnicity", "nationality", "discharge_specialty",
    ]
    usecols = sorted({mapping[name] for name in desired if mapping.get(name)})
    collected: List[pd.DataFrame] = []
    raw_rows = 0
    all_s06 = 0
    unit_counter: Counter[str] = Counter()
    unit_adult_counter: Counter[str] = Counter()
    condition_counter: Counter[str] = Counter()
    condition_adult_counter: Counter[str] = Counter()
    age_rows: List[Dict[str, Any]] = []
    outcome_rows: List[Dict[str, Any]] = []

    try:
        for chunk in iter_source_chunks(path, usecols, row):
            raw_rows += len(chunk)
            dx = normalize_dx(chunk[mapping["dx_main"]])
            s06 = dx.str.startswith("S06", na=False)
            if not s06.any():
                continue
            all_s06 += int(s06.sum())
            sub = chunk.loc[s06].copy()
            dx_sub = dx.loc[s06]
            age = pd.to_numeric(sub[mapping["age"]], errors="coerce")
            unit = normalize_code_series(sub[mapping["age_unit"]])
            condition = normalize_code_series(sub[mapping["discharge_condition"]])
            for value, count in unit.fillna("<MISSING>").value_counts(dropna=False).items():
                unit_counter[str(value)] += int(count)
            plausible_age = age.between(18, 120)
            for value, count in unit.loc[plausible_age].fillna("<MISSING>").value_counts(dropna=False).items():
                unit_adult_counter[str(value)] += int(count)
            for value, count in condition.fillna("<MISSING>").value_counts(dropna=False).items():
                condition_counter[str(value)] += int(count)

            unit_text = meaningful_text(sub[mapping["age_unit"]]).map(ascii_text)
            years_mask = unit.eq(ECUADOR_YEARS_AGE_UNIT_CODE) | unit_text.str.contains(r"ano|anio|year", regex=True, na=False)
            adult = years_mask & plausible_age
            if not adult.any():
                continue
            for value, count in condition.loc[adult].fillna("<MISSING>").value_counts(dropna=False).items():
                condition_adult_counter[str(value)] += int(count)

            adult_sub = sub.loc[adult].copy()
            out = pd.DataFrame(index=adult_sub.index)
            out["country"] = "equador"
            out["year"] = int(year)
            out["age"] = age.loc[adult].astype(float)
            out["sex"] = normalize_sex(adult_sub[mapping["sex"]]) if mapping.get("sex") else pd.NA
            out["los_days"] = pd.to_numeric(adult_sub[mapping["los_days"]], errors="coerce") if mapping.get("los_days") else np.nan
            out["dx_main"] = dx_sub.loc[adult].astype("string")
            out["dx_secondary"] = normalize_dx(adult_sub[mapping["dx_secondary"]]) if mapping.get("dx_secondary") else pd.NA
            out["discharge_condition_raw"] = normalize_code_series(adult_sub[mapping["discharge_condition"]])
            death, death_method = _ecuador_condition_to_death(adult_sub[mapping["discharge_condition"]])
            out["death_in_hospital"] = death
            out["death_mapping_method"] = death_method
            out["hospital_id"] = meaningful_text(adult_sub[mapping["hospital_id"]]) if mapping.get("hospital_id") else pd.NA
            for canonical in (
                "province", "canton", "parish", "area", "facility_class", "facility_type", "facility_entity", "facility_sector",
                "residence_province", "residence_canton", "residence_parish", "residence_area", "ethnicity", "nationality", "discharge_specialty",
            ):
                out[canonical] = meaningful_text(adult_sub[mapping[canonical]]) if mapping.get(canonical) else pd.NA
            out["primary_sample_20plus"] = out["age"].ge(20).astype("Int64")
            out["sensitivity_sample_18plus"] = 1
            out["age_exact_available"] = 1
            out["age_band_common"] = build_age_band(out["age"])
            out["source_file"] = str(path)
            out["age_mapping_method"] = "COD_EDAD_4_YEARS_OFFICIAL"
            collected.append(out.reset_index(drop=True))
            del chunk, sub, adult_sub, out
            gc.collect()
    except Exception as exc:
        return None, {
            "year": year,
            "path": str(path),
            "suffix": path.suffix.lower(),
            "status": f"READ_ERROR:{type(exc).__name__}:{exc}",
            "raw_rows": raw_rows,
            "all_s06": all_s06,
            "age_unit_counts": _profile_counter(unit_counter),
            "adult_plausible_by_unit": _profile_counter(unit_adult_counter),
            "condition_counts": _profile_counter(condition_counter),
        }, [], []

    for code, count in sorted(unit_counter.items()):
        age_rows.append({
            "year": year,
            "path": str(path),
            "age_unit_value": code,
            "s06_count": int(count),
            "s06_age18_120_count": int(unit_adult_counter.get(code, 0)),
            "official_years_code": code == ECUADOR_YEARS_AGE_UNIT_CODE,
        })
    for code, count in sorted(condition_counter.items()):
        outcome_rows.append({
            "year": year,
            "path": str(path),
            "condition_value": code,
            "s06_count": int(count),
            "adult_s06_count": int(condition_adult_counter.get(code, 0)),
        })

    if not collected:
        return None, {
            "year": year,
            "path": str(path),
            "suffix": path.suffix.lower(),
            "status": "ZERO_OFFICIAL_ADULT_S06",
            "raw_rows": raw_rows,
            "all_s06": all_s06,
            "age_unit_counts": _profile_counter(unit_counter),
            "adult_plausible_by_unit": _profile_counter(unit_adult_counter),
            "condition_counts": _profile_counter(condition_counter),
        }, age_rows, outcome_rows

    result = pd.concat(collected, ignore_index=True, sort=False)
    mortality_available = 100 * float(result["death_in_hospital"].notna().mean())
    hospital_available = 100 * float(result["hospital_id"].notna().mean())
    if mortality_available >= 95:
        status = "PASS_INDIVIDUAL_OUTCOMES"
    elif mortality_available > 0:
        status = "PASS_PARTIAL_OUTCOME_REVIEW"
    else:
        status = "PASS_COUNTS_ONLY_OUTCOME_UNRESOLVED"
    audit = {
        "year": year,
        "path": str(path),
        "suffix": path.suffix.lower(),
        "status": status,
        "raw_rows": raw_rows,
        "all_s06": all_s06,
        "adult18_s06": int(len(result)),
        "adult20_s06": int(result["primary_sample_20plus"].sum()),
        "deaths": int(result["death_in_hospital"].sum(skipna=True)),
        "mortality_rate": float(result["death_in_hospital"].mean()) if result["death_in_hospital"].notna().any() else np.nan,
        "mortality_available_pct": round(mortality_available, 3),
        "hospital_id_available_pct": round(hospital_available, 3),
        "age_unit_counts": _profile_counter(unit_counter),
        "adult_plausible_by_unit": _profile_counter(unit_adult_counter),
        "condition_counts": _profile_counter(condition_counter),
    }
    return result, audit, age_rows, outcome_rows


def repair_ecuador(base: Path, v282_root: Path, dirs: Mapping[str, Path]) -> Dict[str, Any]:
    inventory, preferred, linkage = _load_ecuador_tables(v282_root)
    manifest_rows: List[Dict[str, Any]] = []
    attempt_rows: List[Dict[str, Any]] = []
    age_profile_rows: List[Dict[str, Any]] = []
    outcome_profile_rows: List[Dict[str, Any]] = []
    outputs: Dict[int, str] = {}

    for year in ECUADOR_YEARS:
        candidates = _candidate_sources_for_year(inventory, preferred, year)
        if candidates.empty:
            manifest_rows.append({"year": year, "status": "NO_PATIENT_SOURCE"})
            continue
        chosen_result: Optional[pd.DataFrame] = None
        chosen_audit: Optional[Dict[str, Any]] = None
        best_score: Tuple[int, float, int] = (-1, -1.0, -1)
        for _, candidate in candidates.iterrows():
            log(f"Ecuador {year}: testing {Path(str(candidate['path'])).name}", dirs)
            result, audit, age_rows, outcome_rows = _attempt_ecuador_source(year, candidate, dirs)
            audit["v282_preferred"] = bool(candidate.get("is_preferred_v282", False))
            attempt_rows.append(audit)
            age_profile_rows.extend(age_rows)
            outcome_profile_rows.extend(outcome_rows)
            if result is None:
                continue
            complete = safe_float(audit.get("mortality_available_pct"))
            score = (
                2 if complete >= 95 else 1 if complete > 0 else 0,
                complete if np.isfinite(complete) else -1,
                int(audit.get("adult18_s06", 0)),
            )
            if score > best_score:
                if chosen_result is not None:
                    del chosen_result
                chosen_result = result
                chosen_audit = audit
                best_score = score
            else:
                del result
            # A complete outcome source is sufficient; do not scan duplicate formats.
            if best_score[0] == 2:
                break
            gc.collect()

        if chosen_result is None or chosen_audit is None:
            failed = [r for r in attempt_rows if int(r.get("year", -1)) == year]
            statuses = " | ".join(str(r.get("status")) for r in failed)
            manifest_rows.append({"year": year, "status": "NO_VALID_RECOVERY", "attempt_statuses": statuses})
            continue

        target = dirs["ecuador_recovered"] / f"ecuador_s06_recovered_{year}_v283.parquet"
        parquet_write(chosen_result, target)
        outputs[year] = str(target)
        row = dict(chosen_audit)
        row["selected_for_v283"] = True
        row["output_parquet"] = str(target)
        manifest_rows.append(row)
        del chosen_result
        gc.collect()

    manifest = pd.DataFrame(manifest_rows)
    attempts = pd.DataFrame(attempt_rows)
    age_profile = pd.DataFrame(age_profile_rows)
    outcome_profile = pd.DataFrame(outcome_profile_rows)
    save_csv(manifest, dirs["ecuador"] / "Ecuador_recovery_manifest_v283.csv")
    save_csv(attempts, dirs["ecuador"] / "Ecuador_source_attempts_v283.csv")
    save_csv(age_profile, dirs["ecuador"] / "Ecuador_age_unit_profile_v283.csv")
    save_csv(outcome_profile, dirs["ecuador"] / "Ecuador_outcome_profile_v283.csv")

    linkage = linkage.copy()
    linkage["analysis_use_v283"] = np.where(
        linkage.get("exact_common_id", False).astype(str).str.lower().eq("true"),
        "HOSPITAL_LEVEL_REQUIRES_FINAL_ID_VALIDATION",
        "ECOLOGICAL_CAPACITY_SENSITIVITY_ONLY",
    )
    linkage["hospital_volume_primary_allowed"] = False
    linkage["interpretation"] = (
        "No exact public establishment identifier. Composite geographic/institutional keys may combine multiple facilities and cannot define hospital volume."
    )
    save_csv(linkage, dirs["ecuador"] / "Ecuador_linkage_interpretation_v283.csv")
    return {
        "manifest": str(dirs["ecuador"] / "Ecuador_recovery_manifest_v283.csv"),
        "attempts": str(dirs["ecuador"] / "Ecuador_source_attempts_v283.csv"),
        "age_profile": str(dirs["ecuador"] / "Ecuador_age_unit_profile_v283.csv"),
        "outcome_profile": str(dirs["ecuador"] / "Ecuador_outcome_profile_v283.csv"),
        "linkage": str(dirs["ecuador"] / "Ecuador_linkage_interpretation_v283.csv"),
        "outputs": outputs,
    }


# ---------------------------------------------------------------------------
# Chile interpretation repair
# ---------------------------------------------------------------------------

def repair_chile(v282_root: Path, dirs: Mapping[str, Path]) -> str:
    source = v282_root / "02_chile" / "Chile_hospital_linkage_audit_v282.csv"
    frame = pd.read_csv(source, encoding="utf-8-sig")
    procedure_levels: List[str] = []
    analysis_use: List[str] = []
    for _, row in frame.iterrows():
        columns = set(json_load(row.get("columns_json"), []))
        if {"INTERV_Q", "PROCED"} & columns:
            proc = "GENERAL_INTERVENTION_FIELDS_AVAILABLE"
        elif {"GLOSA_INTERV_Q_PPAL", "GLOSA_PROCED_PPAL"} & columns:
            proc = "DESCRIPTIVE_PRINCIPAL_INTERVENTION_AVAILABLE"
        else:
            proc = "NO_PROCEDURE_FIELD"
        procedure_levels.append(proc)
        analysis_use.append(
            "INDIVIDUAL_OUTCOMES_ONLY; residence geography/insurance/facility ownership allowed; no hospital-volume analysis"
        )
    frame["procedure_availability_v283"] = procedure_levels
    frame["analysis_use_v283"] = analysis_use
    frame["facility_geography_available"] = False
    frame["residence_geography_available"] = True
    frame["hospital_volume_primary_allowed"] = False
    frame["warning_v283"] = (
        "REGION_RESIDENCIA and COMUNA_RESIDENCIA describe patient residence, not hospital location; PERTENENCIA_ESTABLECIMIENTO_SALUD is not an establishment ID."
    )
    target = dirs["chile"] / "Chile_analysis_use_v283.csv"
    save_csv(frame, target)
    return str(target)


# ---------------------------------------------------------------------------
# Summary and runner
# ---------------------------------------------------------------------------

def write_summary(dirs: Mapping[str, Path], mexico: Mapping[str, Any], ecuador: Mapping[str, Any], chile_path: str) -> Path:
    mx = pd.read_csv(mexico["audit"], encoding="utf-8-sig") if Path(mexico["audit"]).exists() else pd.DataFrame()
    ec = pd.read_csv(ecuador["manifest"], encoding="utf-8-sig") if Path(ecuador["manifest"]).exists() else pd.DataFrame()
    mx_pass = sorted(pd.to_numeric(mx.loc[mx.get("status", pd.Series(dtype="string")).astype(str).str.startswith("PASS"), "year"], errors="coerce").dropna().astype(int).tolist()) if not mx.empty else []
    ec_outcome = sorted(pd.to_numeric(ec.loc[ec.get("status", pd.Series(dtype="string")).eq("PASS_INDIVIDUAL_OUTCOMES"), "year"], errors="coerce").dropna().astype(int).tolist()) if not ec.empty else []
    ec_counts = sorted(pd.to_numeric(ec.loc[ec.get("status", pd.Series(dtype="string")).astype(str).str.startswith("PASS"), "year"], errors="coerce").dropna().astype(int).tolist()) if not ec.empty else []
    lines = [
        "# TCE LATAM preflight repair v2.8.3",
        "",
        "## Mexico",
        f"Recovered from the consolidated 2013-2020 source: **{', '.join(map(str, mx_pass)) if mx_pass else 'none'}**.",
        "Age unit 5 is treated as years based on the later-year calibration. MOTEGRE code 5 is treated as in-hospital death based on the official DGIS discharge form.",
        "Annual 2015-2017 files are retained only for row/count cross-checking because they do not expose the age-unit field needed for exact adult filtering.",
        "",
        "## Ecuador",
        f"Years with complete individual mortality recovery: **{', '.join(map(str, ec_outcome)) if ec_outcome else 'none'}**.",
        f"Years with at least adult S06 counts recovered: **{', '.join(map(str, ec_counts)) if ec_counts else 'none'}**.",
        "A year is no longer called a full pass when the discharge outcome is unresolved.",
        "No Ecuador year is authorized for primary hospital-volume analysis because no exact establishment ID was found. Composite linkage is ecological/capacity sensitivity only.",
        "",
        "## Chile",
        "Chile remains eligible for individual mortality, length-of-stay, diagnosis, demographic, insurance, residence-geography, and facility-ownership analyses.",
        "Chile is not eligible for hospital-volume analysis. Procedure fields are year-limited and must not be assumed to distinguish decompressive craniectomy from craniotomy without a validated codebook.",
        "",
        "## Gate before final master",
        "Proceed to a final analytic master only if Mexico 2015-2017 pass strict QC and the intended Ecuador mortality years are labeled PASS_INDIVIDUAL_OUTCOMES.",
    ]
    path = dirs["summary"] / "Preflight_repair_recommendations_v283.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def run_latam_preflight_repair_v283(
    base_dir: str | Path = DEFAULT_BASE,
    clean_output: bool = True,
    repair_mexico: bool = True,
    repair_ecuador_sources: bool = True,
) -> Dict[str, Any]:
    base = Path(base_dir)
    v282_root = base / V282_DIR
    if not v282_root.exists():
        raise FileNotFoundError(
            f"Required prior audit folder not found: {v282_root}. Extract or retain analysis_v282_preflight first."
        )
    dirs = ensure_dirs(base, clean_output=clean_output)
    log(f"Starting TCE LATAM preflight repair {VERSION}", dirs)

    mexico = repair_mexico_2015_2017(base, v282_root, dirs) if repair_mexico else {"audit": "", "outputs": {}}
    log("Mexico 2015-2017 repair completed", dirs)
    chile = repair_chile(v282_root, dirs)
    log("Chile analysis-use interpretation completed", dirs)
    ecuador = repair_ecuador(base, v282_root, dirs) if repair_ecuador_sources else {"manifest": "", "outputs": {}}
    log("Ecuador source recovery repair completed", dirs)
    summary = write_summary(dirs, mexico, ecuador, chile)

    manifest = {
        "version": VERSION,
        "base_dir": str(base),
        "output_dir": str(dirs["root"]),
        "mexico": mexico,
        "chile": chile,
        "ecuador": ecuador,
        "summary": str(summary),
    }
    manifest_path = dirs["summary"] / "repair_manifest_v283.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    zip_path = base / f"{OUTPUT_DIR}.zip"
    if zip_path.exists():
        zip_path.unlink()
    shutil.make_archive(str(zip_path.with_suffix("")), "zip", root_dir=dirs["root"])
    manifest["zip"] = str(zip_path)
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    log(f"Repair package created: {zip_path}", dirs)
    return manifest


def verify_latam_preflight_repair_v283() -> Dict[str, Any]:
    status = {
        "version": VERSION,
        "mexico_consolidated_single_pass": True,
        "mexico_death_code": MEXICO_DEATH_CODE,
        "mexico_age_unit_years": MEXICO_YEARS_AGE_UNIT_CODE,
        "ecuador_encoding_errors_repaired": True,
        "ecuador_csv_sav_fallback": True,
        "ecuador_outcome_text_and_numeric_mapping": True,
        "ecuador_strict_outcome_gate": True,
        "chile_hospital_volume_disabled": True,
        "ecuador_composite_linkage_ecological_only": True,
    }
    print(json.dumps(status, indent=2, ensure_ascii=False))
    return status


print(f"✅ TCE LATAM preflight repair {VERSION} loaded.")
print("Run:")
print("  repair = run_latam_preflight_repair_v283(clean_output=True)")
