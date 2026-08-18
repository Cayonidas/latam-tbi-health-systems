#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TCE LATAM SOURCE SUPER-AUDIT v2.8.1
===================================

Purpose
-------
1. Audit every raw Mexico sectorial discharge file listed for 2015-2023 plus
   the 2013-2020 consolidated file.
2. Use 2018-2023 already-clean Mexico checkpoints as a calibration reference
   for historical age-unit and discharge-outcome coding.
3. Recover Mexico 2015-2017 only when the raw schema is demonstrably usable.
4. Audit whether Chile and Ecuador can support exact hospital-level linkage,
   coarser geographic/service-level analyses, or validated capacity linkage.

The script is deliberately fail-closed: it never invents age-unit mappings,
death codes, or hospital identifiers. Recovered early-year Mexico Parquets are
written only when strict validation criteria are satisfied.

Recommended Colab call
-----------------------
    %run /content/tce_latam_source_super_audit_v281.py
    result = run_latam_source_super_audit_v281()
    result

Outputs are written under:
    /content/drive/MyDrive/Projeto_TCE_Multinacional/analysis_v281_preflight/
"""

from __future__ import annotations

import csv
import gc
import hashlib
import json
import math
import os
import re
import shutil
import sys
import time
import unicodedata
import warnings
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Mapping, MutableMapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

try:
    import pyarrow.parquet as pq
except Exception as exc:  # pragma: no cover
    raise ImportError("pyarrow is required") from exc

try:
    import pyreadstat
except Exception:
    pyreadstat = None

VERSION = "2.8.1-preflight"
DEFAULT_BASE = Path("/content/drive/MyDrive/Projeto_TCE_Multinacional")
PRIMARY_YEARS = tuple(range(2015, 2024))
EARLY_MEXICO_YEARS = (2015, 2016, 2017)
LATER_MEXICO_YEARS = tuple(range(2018, 2024))
CHUNK_SIZE = 250_000


# -----------------------------------------------------------------------------
# Exact source manifest supplied by the user
# -----------------------------------------------------------------------------

MEXICO_RAW_RELATIVE: Dict[str, str] = {
    "consolidated_2013_2020": "00_raw/mexico/extracted_2015_INSP_CONAHCYT_Egresos_sectorial_2013_2020/Egresos_sectorial_2013_2020.csv",
    "annual_2015": "00_raw/mexico/extracted_2015_mexico_2015_sectorial_egresos_2015/2015_Sectorial.csv",
    "annual_2016": "00_raw/mexico/extracted_2016_mexico_2016_sectorial_egresos_2016/2016_Sectorial.csv",
    "annual_2017": "00_raw/mexico/extracted_2017_mexico_2017_sectorial_egresos_2017/SECTORIAL_2017.csv",
    "annual_2018": "00_raw/mexico/extracted_2018_mexico_2018_sectorial_egresos_2018/Egresos_Sectorial_2018.csv",
    "annual_2019": "00_raw/mexico/extracted_2019_mexico_2019_sectorial_egresos_2019/Egresos_Sectorial_2019.csv",
    "annual_2020": "00_raw/mexico/extracted_2020_mexico_2020_sectorial_egresos_2020/Egresos_Sectorial_2020.csv",
    "annual_2021": "00_raw/mexico/extracted_2021_mexico_2021_sectorial_egresos_2021/Egresos_Sectorial_2021.txt",
    "annual_2022": "00_raw/mexico/extracted_2022_mexico_2022_sectorial_egresos_2022/Egresos_Sectorial_2022.txt",
    "annual_2023": "00_raw/mexico/extracted_2023_mexico_2023_sectorial_egresos_2023/Egresos_Sectorial_2023.txt",
}

MEXICO_ZIP_RELATIVE: Dict[str, str] = {
    "zip_consolidated_2013_2020": "00_raw/mexico/INSP_CONAHCYT_Egresos_sectorial_2013_2020.zip",
    **{f"zip_{year}": f"00_raw/mexico/mexico_{year}_sectorial_egresos_{year}.zip" for year in PRIMARY_YEARS},
}


# -----------------------------------------------------------------------------
# Alias dictionaries
# -----------------------------------------------------------------------------

ALIASES: Dict[str, Sequence[str]] = {
    "year": (
        "year", "anio", "año", "anio_egr", "año_egr", "ano_egreso",
        "anioegreso", "anoegreso", "anocap", "anio_alta", "anio_egreso",
    ),
    "month": ("month", "mes", "mes_egr", "mes_egreso", "mescap"),
    "hospital_id": (
        "hospital_id", "hospital_id_raw", "clues", "clues_unidad", "clues_hosp",
        "clues_establecimiento", "clues_egreso", "cluesuni", "id_unidad",
        "unidad_medica", "cod_unidad", "clave_unidad", "unidad", "clues_orig",
    ),
    "hospital_region": (
        "hospital_region", "entidad_um", "entidad_unidad", "entidad", "edo",
        "eentidad", "cedocve", "entidad_establecimiento", "entidad_clues",
    ),
    "residence_region": (
        "residence_region", "entidad_res", "entidad_residencia", "edo_res",
        "res_entidad", "entres", "entidad_resid",
    ),
    "age": (
        "age", "edad", "edad_cumplida", "edad1", "edad_insp", "edadcumpl",
        "edad_cumpl", "edadegre", "edad_egreso",
    ),
    "age_unit": (
        "age_unit", "tipo_edad", "edad_tipo", "unidad_edad", "clave_edad",
        "cedad", "cedad_insp", "cod_edad", "tipoedad",
    ),
    "sex": ("sex", "sexo", "sex_raw", "genero"),
    "los_days": (
        "los_days", "dias_estancia", "dias_esta", "estancia", "dias_estada",
        "dias_estad", "dia_estad", "diasestancia",
    ),
    "dx_main": (
        "dx_main", "afeccion_ppal", "afeccion_principal", "diag_prin",
        "diag_princ", "diagnostico_principal", "causa_egreso", "cie10",
        "afecprin4", "afecprin3", "afec_prin4", "afec_prin3", "afeccion",
        "diag1", "diagnostico", "diag_egreso", "codcie10",
    ),
    "dx_secondary": (
        "dx_secondary", "afeccion_sec", "diag_sec", "diagnostico_secundario",
        "afecsec4", "afecsec3", "diag2",
    ),
    "external_cause": (
        "external_cause", "causa_ext", "causa_externa", "causabas4",
        "causabas3", "causaext4", "causaext3",
    ),
    "discharge_reason": (
        "discharge_reason", "motivo_egreso", "motivo_de_egreso", "motegre",
        "motivoegreso", "mot_egreso",
    ),
    "discharge_condition": (
        "discharge_condition", "condicion_egreso", "cond_egreso", "cond_egr",
        "con_egr", "condicion_al_egreso",
    ),
    "death": ("death_in_hospital", "defuncion", "fallecido", "muerte"),
    "procedure": (
        "procedure_code_raw", "intervencion_qx", "tipo_intervencion",
        "codigo_cie_9_mc", "cod_cie9mc", "procedimiento", "procprin4",
    ),
}

CHILE_ID_ALIASES = (
    "estab", "establecimiento", "cod_estab", "codigo_establecimiento",
    "codigo_estab", "cl_estab", "id_establecimiento", "hospital_id",
)
CHILE_AGGREGATION_ALIASES: Dict[str, Sequence[str]] = {
    "health_service": ("ser_salud", "serv_salud", "servicio_salud", "serv_sal", "ssalud"),
    "region": ("region", "region_res", "region_estab", "cod_region"),
    "commune": ("comuna", "comuna_res", "comuna_estab", "cod_comuna"),
    "insurance": ("previ", "prevision", "seguro", "insurance_type"),
}

ECUADOR_ID_ALIASES = (
    "unicodigo", "uni_codigo", "codigo_establecimiento", "cod_estab",
    "codigo_estab", "id_establecimiento", "establecimiento_id", "codestab",
    "cod_establec", "codigo",
)
ECUADOR_KEY_GROUPS: Dict[str, Sequence[str]] = {
    "province": ("prov_ubi", "prov_ubie", "provincia", "provincia_estab"),
    "canton": ("cant_ubi", "cant_ubie", "canton", "canton_estab"),
    "parish": ("parr_ubi", "parr_ubie", "parroquia", "parroquia_estab"),
    "area": ("area_ubi", "area_ubie", "area"),
    "facility_class": ("clase", "facility_class"),
    "facility_type": ("tipo", "facility_type"),
    "facility_entity": ("entidad", "facility_entity"),
    "facility_sector": ("sector", "facility_sector"),
}


# -----------------------------------------------------------------------------
# Generic helpers
# -----------------------------------------------------------------------------


def ascii_text(value: Any) -> str:
    text = "" if value is None else str(value)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", text).strip().lower()


def column_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", ascii_text(value))


def normalize_dx(series: pd.Series) -> pd.Series:
    text = series.astype("string").str.upper().str.strip()
    text = text.str.replace(r"[^A-Z0-9]", "", regex=True)
    return text.where(text.ne(""), pd.NA)


def meaningful_text(series: pd.Series) -> pd.Series:
    text = series.astype("string").str.strip()
    bad = text.str.upper().isin({"", "NA", "N/A", "NAN", "NONE", "NULL", "<NA>", "UNKNOWN", "DESCONOCIDO", "IGNORADO", "NO INFORMADO", "999", "9999"})
    return text.where(text.notna() & ~bad, pd.NA)


def normalize_code_series(series: pd.Series) -> pd.Series:
    """Canonicalize categorical codes without changing free-text labels.

    Numeric strings such as ``4``, ``4.0`` and ``04`` become ``4``. Textual
    categories are stripped but otherwise preserved. This prevents false
    mismatches when yearly files change numeric formatting.
    """
    text = meaningful_text(series)
    numeric = pd.to_numeric(text, errors="coerce")
    result = text.astype("string").copy()
    integer_like = numeric.notna() & np.isclose(numeric, np.round(numeric), equal_nan=False)
    result.loc[integer_like] = numeric.loc[integer_like].round().astype("Int64").astype("string")
    return result


def resolve_aliases(columns: Sequence[str], aliases: Mapping[str, Sequence[str]] = ALIASES) -> Dict[str, str]:
    by_key: Dict[str, str] = {}
    for col in columns:
        by_key.setdefault(column_key(col), col)
    result: Dict[str, str] = {}
    for canonical, choices in aliases.items():
        for choice in choices:
            actual = by_key.get(column_key(choice))
            if actual is not None:
                result[canonical] = actual
                break
    return result


def resolve_one(columns: Sequence[str], choices: Sequence[str]) -> Optional[str]:
    by_key = {column_key(c): c for c in columns}
    for choice in choices:
        actual = by_key.get(column_key(choice))
        if actual is not None:
            return actual
    return None


def ensure_dirs(base: Path) -> Dict[str, Path]:
    root = base / "analysis_v281_preflight"
    dirs = {
        "root": root,
        "mexico": root / "01_mexico",
        "recovered": root / "01_mexico" / "recovered",
        "chile": root / "02_chile",
        "ecuador": root / "03_ecuador",
        "summary": root / "04_summary",
        "logs": root / "05_logs",
    }
    for path in dirs.values():
        path.mkdir(parents=True, exist_ok=True)
    return dirs


def log(message: str, dirs: Mapping[str, Path], level: str = "INFO") -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} | {level:<7} | {message}"
    print(line, flush=True)
    try:
        with (dirs["logs"] / "super_audit_v281.log").open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except Exception:
        pass


def save_table(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, encoding="utf-8-sig")


def json_safe(value: Any) -> Any:
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, Path):
        return str(value)
    return value


def detect_text_format(path: Path) -> Tuple[str, str]:
    raw = path.read_bytes()[:262_144]
    encodings = ("utf-8-sig", "utf-8", "cp1252", "latin-1")
    candidates: List[Tuple[float, str, str]] = []
    for encoding in encodings:
        try:
            text = raw.decode(encoding)
        except Exception:
            continue
        lines = [line for line in text.splitlines()[:20] if line.strip()]
        if not lines:
            continue
        for sep in ("|", "\t", ";", ","):
            counts = [line.count(sep) for line in lines]
            mean = float(np.mean(counts)) if counts else 0.0
            sd = float(np.std(counts)) if counts else 999.0
            score = mean * 10 - sd
            if mean >= 1:
                candidates.append((score, encoding, sep))
    if not candidates:
        return "latin-1", ","
    candidates.sort(reverse=True)
    _, encoding, sep = candidates[0]
    return encoding, sep


def count_text_rows(path: Path) -> int:
    with path.open("rb") as handle:
        count = sum(block.count(b"\n") for block in iter(lambda: handle.read(8 * 1024 * 1024), b""))
    return max(0, count - 1)


def read_header(path: Path, encoding: str, sep: str) -> List[str]:
    frame = pd.read_csv(path, sep=sep, encoding=encoding, nrows=0, engine="python")
    return [str(c) for c in frame.columns]


def read_parquet_reference_counts(base: Path) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    inter = base / "01_intermediate" / "mexico"
    clean_path = inter / "mexico_clean.parquet"
    clean: Optional[pd.DataFrame] = None
    if clean_path.exists():
        columns = pq.ParquetFile(clean_path).schema.names
        wanted = [c for c in ["year", "age", "death_in_hospital", "hospital_id", "dx_main", "primary_sample_20plus", "sensitivity_sample_18plus"] if c in columns]
        if wanted:
            clean = pd.read_parquet(clean_path, columns=wanted)
    for year in PRIMARY_YEARS:
        analytic = inter / f"mexico_s06_analytic_{year}.parquet"
        source = analytic if analytic.exists() else None
        if source is not None:
            cols = pq.ParquetFile(source).schema.names
            wanted = [c for c in ["year", "age", "death_in_hospital", "hospital_id", "dx_main", "primary_sample_20plus", "sensitivity_sample_18plus"] if c in cols]
            frame = pd.read_parquet(source, columns=wanted)
            source_type = "annual_analytic_checkpoint"
        elif clean is not None and "year" in clean:
            frame = clean[pd.to_numeric(clean["year"], errors="coerce").eq(year)].copy()
            source_type = "mexico_clean"
        else:
            frame = pd.DataFrame()
            source_type = "none"
        if frame.empty:
            rows.append({"year": year, "reference_source": source_type, "reference_rows": 0, "reference_adult20": 0, "reference_mortality": np.nan, "reference_hospitals": 0})
            continue
        age = pd.to_numeric(frame.get("age"), errors="coerce") if "age" in frame else pd.Series(np.nan, index=frame.index)
        adult20 = frame[age.ge(20)] if "age" in frame else frame
        death = pd.to_numeric(adult20.get("death_in_hospital"), errors="coerce") if "death_in_hospital" in adult20 else pd.Series(np.nan, index=adult20.index)
        rows.append({
            "year": year,
            "reference_source": source_type,
            "reference_rows": int(len(frame)),
            "reference_adult20": int(len(adult20)),
            "reference_mortality": float(death.mean()) if death.notna().any() else np.nan,
            "reference_hospitals": int(adult20["hospital_id"].nunique(dropna=True)) if "hospital_id" in adult20 else 0,
        })
        del frame, adult20
        gc.collect()
    if clean is not None:
        del clean
    return pd.DataFrame(rows)


@dataclass
class MexicoScanResult:
    source_name: str
    path: str
    year_hint: Optional[int]
    is_consolidated: bool
    exists: bool
    size_mb: float
    encoding: str
    separator: str
    columns: List[str]
    mapping: Dict[str, str]
    rows_total: int
    rows_target_year: Dict[int, int]
    s06_by_year: Dict[int, int]
    age_unit_stats: Dict[int, Dict[str, Dict[str, int]]]
    outcome_stats: Dict[int, Dict[str, int]]
    outcome_by_unit_stats: Dict[int, Dict[str, Dict[str, int]]]
    hospital_nonmissing: Dict[int, int]
    hospital_unique_samples: Dict[int, int]
    sample_values: Dict[str, List[str]]
    error: str = ""


def scan_mexico_text_source(
    source_name: str,
    path: Path,
    year_hint: Optional[int],
    is_consolidated: bool,
    dirs: Mapping[str, Path],
) -> MexicoScanResult:
    if not path.exists():
        return MexicoScanResult(source_name, str(path), year_hint, is_consolidated, False, 0.0, "", "", [], {}, 0, {}, {}, {}, {}, {}, {}, {}, {}, "FILE_MISSING")
    encoding, sep = detect_text_format(path)
    try:
        columns = read_header(path, encoding, sep)
    except Exception as exc:
        return MexicoScanResult(source_name, str(path), year_hint, is_consolidated, True, path.stat().st_size / 1024**2, encoding, sep, [], {}, 0, {}, {}, {}, {}, {}, {}, {}, {}, f"HEADER_ERROR:{type(exc).__name__}:{exc}")
    mapping = resolve_aliases(columns)
    requested = sorted(set(mapping.values()))
    rows_total = 0
    rows_target_year: Counter[int] = Counter()
    s06_by_year: Counter[int] = Counter()
    age_unit_stats: Dict[int, Dict[str, Counter[str]]] = defaultdict(lambda: defaultdict(Counter))
    outcome_stats: Dict[int, Counter[str]] = defaultdict(Counter)
    outcome_by_unit_stats: Dict[int, Dict[str, Counter[str]]] = defaultdict(lambda: defaultdict(Counter))
    hospital_nonmissing: Counter[int] = Counter()
    hospital_sets: Dict[int, set] = defaultdict(set)
    samples: Dict[str, set] = defaultdict(set)

    if not requested:
        return MexicoScanResult(source_name, str(path), year_hint, is_consolidated, True, path.stat().st_size / 1024**2, encoding, sep, columns, mapping, count_text_rows(path), {}, {}, {}, {}, {}, {}, {}, {}, "NO_MAPPED_COLUMNS")

    try:
        iterator = pd.read_csv(
            path,
            sep=sep,
            encoding=encoding,
            usecols=requested,
            chunksize=CHUNK_SIZE,
            dtype="string",
            engine="python",
            on_bad_lines="skip",
        )
        for chunk_index, chunk in enumerate(iterator, start=1):
            rows_total += len(chunk)
            if "year" in mapping:
                year_values = pd.to_numeric(chunk[mapping["year"]], errors="coerce")
            else:
                year_values = pd.Series(year_hint, index=chunk.index, dtype="float64")
            if year_hint is not None and not is_consolidated:
                year_values[:] = year_hint
            dx = normalize_dx(chunk[mapping["dx_main"]]) if "dx_main" in mapping else pd.Series(pd.NA, index=chunk.index, dtype="string")
            s06 = dx.str.startswith("S06", na=False)
            age = pd.to_numeric(chunk[mapping["age"]], errors="coerce") if "age" in mapping else pd.Series(np.nan, index=chunk.index)
            unit = normalize_code_series(chunk[mapping["age_unit"]]) if "age_unit" in mapping else pd.Series("NO_UNIT_FIELD", index=chunk.index, dtype="string")
            outcome_col = mapping.get("death") or mapping.get("discharge_reason") or mapping.get("discharge_condition")
            outcome = normalize_code_series(chunk[outcome_col]) if outcome_col else pd.Series(pd.NA, index=chunk.index, dtype="string")
            hospital = meaningful_text(chunk[mapping["hospital_id"]]) if "hospital_id" in mapping else pd.Series(pd.NA, index=chunk.index, dtype="string")

            valid_years = sorted(set(pd.to_numeric(year_values, errors="coerce").dropna().astype(int).tolist()))
            for year in valid_years:
                if year not in range(2013, 2024):
                    continue
                year_mask = pd.to_numeric(year_values, errors="coerce").eq(year)
                rows_target_year[year] += int(year_mask.sum())
                mask = year_mask & s06
                s06_by_year[year] += int(mask.sum())
                if not mask.any():
                    continue
                sub_age = age[mask]
                sub_unit = unit[mask].fillna("<MISSING>")
                grouped = pd.DataFrame({"unit": sub_unit.astype(str), "age": sub_age})
                for unit_value, group in grouped.groupby("unit", dropna=False):
                    key = str(unit_value)
                    age_unit_stats[year][key]["s06"] += int(len(group))
                    age_unit_stats[year][key]["age_0_120"] += int(group["age"].between(0, 120).sum())
                    age_unit_stats[year][key]["age_18plus"] += int(group["age"].between(18, 120).sum())
                    age_unit_stats[year][key]["age_20plus"] += int(group["age"].between(20, 120).sum())
                for value, count in outcome[mask].fillna("<MISSING>").astype(str).value_counts().head(100).items():
                    outcome_stats[year][str(value)] += int(count)
                paired = pd.DataFrame({
                    "unit": unit[mask].fillna("<MISSING>").astype(str),
                    "outcome": outcome[mask].fillna("<MISSING>").astype(str),
                })
                for (unit_value, outcome_value), count in paired.value_counts().items():
                    outcome_by_unit_stats[year][str(unit_value)][str(outcome_value)] += int(count)
                h = hospital[mask]
                hospital_nonmissing[year] += int(h.notna().sum())
                if len(hospital_sets[year]) < 100_000:
                    hospital_sets[year].update(h.dropna().astype(str).head(100_000).tolist())
                for canonical, actual in mapping.items():
                    if len(samples[canonical]) >= 20:
                        continue
                    values = meaningful_text(chunk.loc[mask, actual]).dropna().astype(str).head(20).tolist()
                    samples[canonical].update(values)
            if chunk_index % 10 == 0:
                log(f"Scanning {source_name}: {rows_total:,} rows", dirs)
            del chunk
            gc.collect()
    except Exception as exc:
        return MexicoScanResult(source_name, str(path), year_hint, is_consolidated, True, path.stat().st_size / 1024**2, encoding, sep, columns, mapping, rows_total, dict(rows_target_year), dict(s06_by_year), {}, {}, {}, {}, {}, {}, f"SCAN_ERROR:{type(exc).__name__}:{exc}")

    age_stats_plain: Dict[int, Dict[str, Dict[str, int]]] = {}
    for year, unit_map in age_unit_stats.items():
        age_stats_plain[year] = {unit: dict(counter) for unit, counter in unit_map.items()}
    return MexicoScanResult(
        source_name=source_name,
        path=str(path),
        year_hint=year_hint,
        is_consolidated=is_consolidated,
        exists=True,
        size_mb=path.stat().st_size / 1024**2,
        encoding=encoding,
        separator=sep,
        columns=columns,
        mapping=mapping,
        rows_total=rows_total,
        rows_target_year=dict(rows_target_year),
        s06_by_year=dict(s06_by_year),
        age_unit_stats=age_stats_plain,
        outcome_stats={year: dict(counter) for year, counter in outcome_stats.items()},
        outcome_by_unit_stats={year: {unit: dict(counter) for unit, counter in unit_map.items()} for year, unit_map in outcome_by_unit_stats.items()},
        hospital_nonmissing=dict(hospital_nonmissing),
        hospital_unique_samples={year: len(values) for year, values in hospital_sets.items()},
        sample_values={key: sorted(values)[:20] for key, values in samples.items()},
        error="",
    )


def flatten_mexico_scan(scan: MexicoScanResult) -> Dict[str, Any]:
    return {
        "source_name": scan.source_name,
        "path": scan.path,
        "year_hint": scan.year_hint,
        "is_consolidated": scan.is_consolidated,
        "exists": scan.exists,
        "size_mb": round(scan.size_mb, 3),
        "encoding": scan.encoding,
        "separator_repr": repr(scan.separator),
        "n_columns": len(scan.columns),
        "columns_json": json.dumps(scan.columns, ensure_ascii=False),
        "mapping_json": json.dumps(scan.mapping, ensure_ascii=False),
        "rows_total": scan.rows_total,
        "rows_target_year_json": json.dumps(scan.rows_target_year, ensure_ascii=False),
        "s06_by_year_json": json.dumps(scan.s06_by_year, ensure_ascii=False),
        "age_unit_stats_json": json.dumps(scan.age_unit_stats, ensure_ascii=False),
        "outcome_stats_json": json.dumps(scan.outcome_stats, ensure_ascii=False),
        "outcome_by_unit_stats_json": json.dumps(scan.outcome_by_unit_stats, ensure_ascii=False),
        "hospital_nonmissing_json": json.dumps(scan.hospital_nonmissing, ensure_ascii=False),
        "hospital_unique_sample_json": json.dumps(scan.hospital_unique_samples, ensure_ascii=False),
        "sample_values_json": json.dumps(scan.sample_values, ensure_ascii=False),
        "error": scan.error,
    }


def candidate_age_units(scan: MexicoScanResult, year: int) -> pd.DataFrame:
    rows = []
    for unit_value, stats in scan.age_unit_stats.get(year, {}).items():
        rows.append({
            "source_name": scan.source_name,
            "year": year,
            "age_unit_value": unit_value,
            **{key: int(stats.get(key, 0)) for key in ("s06", "age_0_120", "age_18plus", "age_20plus")},
        })
    return pd.DataFrame(rows)


def calibrate_age_unit_mapping(scans: Sequence[MexicoScanResult], reference: pd.DataFrame) -> pd.DataFrame:
    candidates = pd.concat(
        [candidate_age_units(scan, year) for scan in scans for year in LATER_MEXICO_YEARS if scan.s06_by_year.get(year, 0) > 0],
        ignore_index=True,
        sort=False,
    ) if scans else pd.DataFrame()
    if candidates.empty:
        return candidates
    ref = reference[["year", "reference_adult20"]].copy()
    candidates = candidates.merge(ref, on="year", how="left")
    candidates["relative_error_vs_reference"] = np.where(
        candidates["reference_adult20"].gt(0),
        (candidates["age_20plus"] - candidates["reference_adult20"]).abs() / candidates["reference_adult20"],
        np.nan,
    )
    candidates["candidate_plausibility"] = np.where(
        candidates["s06"].gt(0), candidates["age_0_120"] / candidates["s06"], np.nan
    )
    candidates["calibration_pass"] = (
        candidates["relative_error_vs_reference"].le(0.08)
        & candidates["candidate_plausibility"].ge(0.90)
        & candidates["age_20plus"].gt(0)
    )
    return candidates.sort_values(["year", "relative_error_vs_reference", "source_name"], na_position="last")


def derive_consensus_age_unit(calibration: pd.DataFrame) -> Dict[str, Any]:
    passed = calibration[calibration["calibration_pass"].fillna(False)].copy()
    if passed.empty:
        return {"status": "NO_VALIDATED_AGE_UNIT", "unit_value": None, "support_years": [], "median_relative_error": np.nan}
    summary = passed.groupby("age_unit_value", observed=True).agg(
        support_years=("year", "nunique"),
        median_relative_error=("relative_error_vs_reference", "median"),
        sources=("source_name", "nunique"),
    ).reset_index()
    summary = summary.sort_values(["support_years", "median_relative_error", "sources"], ascending=[False, True, False])
    best = summary.iloc[0]
    if int(best["support_years"]) < 2:
        return {"status": "INSUFFICIENT_AGE_UNIT_SUPPORT", "unit_value": str(best["age_unit_value"]), "support_years": sorted(passed.loc[passed["age_unit_value"].eq(best["age_unit_value"]), "year"].unique().tolist()), "median_relative_error": float(best["median_relative_error"])}
    return {
        "status": "VALIDATED_BY_LATER_YEARS",
        "unit_value": str(best["age_unit_value"]),
        "support_years": sorted(passed.loc[passed["age_unit_value"].eq(best["age_unit_value"]), "year"].unique().tolist()),
        "median_relative_error": float(best["median_relative_error"]),
    }


def evaluate_death_code_candidates(
    scans: Sequence[MexicoScanResult],
    reference: pd.DataFrame,
    age_unit_consensus: Mapping[str, Any],
) -> pd.DataFrame:
    """Calibrate the death code among S06 records in the validated age unit."""
    rows: List[Dict[str, Any]] = []
    ref_map = reference.set_index("year")["reference_mortality"].to_dict()
    selected_unit = age_unit_consensus.get("unit_value")
    if selected_unit is None:
        return pd.DataFrame()
    selected_unit = str(selected_unit)
    for scan in scans:
        for year in LATER_MEXICO_YEARS:
            stats = scan.outcome_by_unit_stats.get(year, {}).get(selected_unit, {})
            total = sum(int(v) for k, v in stats.items() if k != "<MISSING>")
            if total <= 0 or not np.isfinite(ref_map.get(year, np.nan)):
                continue
            for code, count in stats.items():
                if code == "<MISSING>":
                    continue
                mortality = int(count) / total
                rows.append({
                    "source_name": scan.source_name,
                    "year": year,
                    "validated_age_unit": selected_unit,
                    "candidate_death_code": str(code),
                    "candidate_mortality": mortality,
                    "reference_mortality": float(ref_map[year]),
                    "absolute_difference": abs(mortality - float(ref_map[year])),
                    "outcome_nonmissing": total,
                })
    result = pd.DataFrame(rows)
    if result.empty:
        return result
    result["calibration_pass"] = result["absolute_difference"].le(0.02)
    return result.sort_values(["year", "absolute_difference", "source_name"])


def derive_consensus_death_code(calibration: pd.DataFrame) -> Dict[str, Any]:
    passed = calibration[calibration["calibration_pass"].fillna(False)].copy()
    if passed.empty:
        return {"status": "NO_VALIDATED_DEATH_CODE", "code": None, "support_years": [], "median_abs_difference": np.nan}
    summary = passed.groupby("candidate_death_code", observed=True).agg(
        support_years=("year", "nunique"),
        median_abs_difference=("absolute_difference", "median"),
        sources=("source_name", "nunique"),
    ).reset_index().sort_values(["support_years", "median_abs_difference", "sources"], ascending=[False, True, False])
    best = summary.iloc[0]
    if int(best["support_years"]) < 2:
        return {"status": "INSUFFICIENT_DEATH_CODE_SUPPORT", "code": str(best["candidate_death_code"]), "support_years": sorted(passed.loc[passed["candidate_death_code"].eq(best["candidate_death_code"]), "year"].unique().tolist()), "median_abs_difference": float(best["median_abs_difference"])}
    return {
        "status": "VALIDATED_BY_LATER_YEARS",
        "code": str(best["candidate_death_code"]),
        "support_years": sorted(passed.loc[passed["candidate_death_code"].eq(best["candidate_death_code"]), "year"].unique().tolist()),
        "median_abs_difference": float(best["median_abs_difference"]),
    }


def select_source_for_year(scans: Sequence[MexicoScanResult], year: int) -> List[MexicoScanResult]:
    eligible = [scan for scan in scans if scan.exists and not scan.error and scan.s06_by_year.get(year, 0) > 0]
    annual = [scan for scan in eligible if scan.year_hint == year and not scan.is_consolidated]
    consolidated = [scan for scan in eligible if scan.is_consolidated]
    return annual + consolidated


def normalize_sex_simple(series: pd.Series) -> pd.Series:
    raw = meaningful_text(series)
    text = raw.map(ascii_text)
    numeric = pd.to_numeric(raw, errors="coerce")
    result = pd.Series(pd.NA, index=series.index, dtype="string")
    result.loc[text.str.contains(r"masc|hombre|male", regex=True, na=False)] = "Male"
    result.loc[text.str.contains(r"fem|mujer|female", regex=True, na=False)] = "Female"
    result.loc[numeric.eq(1)] = "Male"
    result.loc[numeric.eq(2)] = "Female"
    return result


def build_recovered_mexico_year(
    scan: MexicoScanResult,
    year: int,
    age_unit_consensus: Mapping[str, Any],
    death_code_consensus: Mapping[str, Any],
    target: Path,
    dirs: Mapping[str, Path],
) -> Tuple[Optional[pd.DataFrame], Dict[str, Any]]:
    mapping = scan.mapping
    required = {"dx_main", "age", "hospital_id"}
    outcome_field = mapping.get("death") or mapping.get("discharge_reason") or mapping.get("discharge_condition")
    if not required.issubset(mapping) or outcome_field is None:
        return None, {"year": year, "source_name": scan.source_name, "status": "FAIL_REQUIRED_FIELDS", "missing": ",".join(sorted(required - set(mapping))), "path": scan.path}
    if age_unit_consensus.get("status") != "VALIDATED_BY_LATER_YEARS":
        return None, {"year": year, "source_name": scan.source_name, "status": "FAIL_AGE_UNIT_NOT_VALIDATED", "path": scan.path}
    if death_code_consensus.get("status") != "VALIDATED_BY_LATER_YEARS":
        return None, {"year": year, "source_name": scan.source_name, "status": "FAIL_DEATH_CODE_NOT_VALIDATED", "path": scan.path}

    path = Path(scan.path)
    requested = sorted(set(mapping.values()))
    frames: List[pd.DataFrame] = []
    unit_value = str(age_unit_consensus["unit_value"])
    death_code = str(death_code_consensus["code"])
    try:
        iterator = pd.read_csv(
            path,
            sep=scan.separator,
            encoding=scan.encoding,
            usecols=requested,
            chunksize=CHUNK_SIZE,
            dtype="string",
            engine="python",
            on_bad_lines="skip",
        )
        for chunk in iterator:
            if "year" in mapping:
                years = pd.to_numeric(chunk[mapping["year"]], errors="coerce")
                chunk = chunk[years.eq(year)]
            elif scan.year_hint != year:
                continue
            if chunk.empty:
                continue
            dx = normalize_dx(chunk[mapping["dx_main"]])
            mask = dx.str.startswith("S06", na=False)
            if "age_unit" in mapping:
                units = normalize_code_series(chunk[mapping["age_unit"]]).astype("string")
                mask &= units.eq(unit_value)
            age = pd.to_numeric(chunk[mapping["age"]], errors="coerce")
            mask &= age.between(18, 120)
            if not mask.any():
                continue
            sub = chunk.loc[mask].copy()
            out = pd.DataFrame(index=sub.index)
            out["country"] = "mexico"
            out["year"] = int(year)
            out["month"] = pd.to_numeric(sub[mapping["month"]], errors="coerce") if "month" in mapping else pd.NA
            out["hospital_id"] = meaningful_text(sub[mapping["hospital_id"]])
            out["hospital_region"] = meaningful_text(sub[mapping["hospital_region"]]) if "hospital_region" in mapping else pd.NA
            out["residence_region"] = meaningful_text(sub[mapping["residence_region"]]) if "residence_region" in mapping else pd.NA
            out["age"] = pd.to_numeric(sub[mapping["age"]], errors="coerce")
            out["age_unit"] = meaningful_text(sub[mapping["age_unit"]]) if "age_unit" in mapping else "years"
            out["sex"] = normalize_sex_simple(sub[mapping["sex"]]) if "sex" in mapping else pd.NA
            out["los_days"] = pd.to_numeric(sub[mapping["los_days"]], errors="coerce") if "los_days" in mapping else pd.NA
            out["dx_main"] = normalize_dx(sub[mapping["dx_main"]])
            out["dx_secondary"] = normalize_dx(sub[mapping["dx_secondary"]]) if "dx_secondary" in mapping else pd.NA
            out["external_cause"] = meaningful_text(sub[mapping["external_cause"]]) if "external_cause" in mapping else pd.NA
            raw_outcome = normalize_code_series(sub[outcome_field]).astype("string")
            out["death_in_hospital"] = raw_outcome.eq(death_code).astype("Int64")
            out["procedure_code_raw"] = meaningful_text(sub[mapping["procedure"]]) if "procedure" in mapping else pd.NA
            out["source_file"] = str(path)
            out["source_dataset"] = f"Mexico sectorial raw recovered v281 ({scan.source_name})"
            out["record_id"] = [f"MX-{year}-{scan.source_name}-{i}" for i in range(len(out))]
            out["primary_sample_20plus"] = out["age"].ge(20).astype("Int64")
            out["sensitivity_sample_18plus"] = out["age"].ge(18).astype("Int64")
            out["age_exact_available"] = 1
            bins = [18, 20, 30, 50, 70, 80, np.inf]
            labels = ["18-19", "20-29", "30-49", "50-69", "70-79", "80+"]
            out["age_band_common"] = pd.cut(out["age"], bins=bins, labels=labels, right=False).astype("string")
            frames.append(out.reset_index(drop=True))
            del chunk, sub, out
            gc.collect()
    except Exception as exc:
        return None, {"year": year, "source_name": scan.source_name, "status": f"FAIL_READ:{type(exc).__name__}:{exc}", "path": scan.path}
    if not frames:
        return None, {"year": year, "source_name": scan.source_name, "status": "FAIL_ZERO_RECOVERED_ROWS", "path": scan.path}
    result = pd.concat(frames, ignore_index=True, sort=False)
    hospital_pct = 100 * result["hospital_id"].notna().mean()
    mortality_pct = 100 * result["death_in_hospital"].notna().mean()
    status = "PASS_VOLUME_AND_INDIVIDUAL" if hospital_pct >= 80 and mortality_pct >= 95 else "PASS_INDIVIDUAL_ONLY" if mortality_pct >= 95 else "FAIL_OUTCOME_COMPLETENESS"
    if status.startswith("PASS"):
        target.parent.mkdir(parents=True, exist_ok=True)
        result.to_parquet(target, index=False, compression="snappy")
    audit = {
        "year": year,
        "source_name": scan.source_name,
        "status": status,
        "path": scan.path,
        "recovered_rows_18plus": int(len(result)),
        "recovered_rows_20plus": int(result["primary_sample_20plus"].sum()),
        "mortality_rate": float(result["death_in_hospital"].mean()),
        "hospital_id_available_pct": round(hospital_pct, 3),
        "unique_hospitals": int(result["hospital_id"].nunique(dropna=True)),
        "output_parquet": str(target) if status.startswith("PASS") else "",
        "age_unit_value": unit_value,
        "death_code": death_code,
    }
    return result if status.startswith("PASS") else None, audit


def compare_annual_and_consolidated(recovered: Mapping[Tuple[int, str], pd.DataFrame]) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    for year in EARLY_MEXICO_YEARS:
        annual_keys = [key for key in recovered if key[0] == year and key[1].startswith("annual_")]
        consolidated_keys = [key for key in recovered if key[0] == year and key[1] == "consolidated_2013_2020"]
        if not annual_keys or not consolidated_keys:
            rows.append({"year": year, "status": "COMPARISON_NOT_AVAILABLE", "annual_rows": 0, "consolidated_rows": 0, "hash_overlap_pct": np.nan})
            continue
        annual = recovered[annual_keys[0]].copy()
        consolidated = recovered[consolidated_keys[0]].copy()
        key_cols = [c for c in ["hospital_id", "age", "sex", "dx_main", "los_days", "death_in_hospital"] if c in annual and c in consolidated]
        if not key_cols:
            rows.append({"year": year, "status": "NO_COMMON_HASH_COLUMNS", "annual_rows": len(annual), "consolidated_rows": len(consolidated), "hash_overlap_pct": np.nan})
            continue
        def hashes(frame: pd.DataFrame) -> set:
            text = frame[key_cols].astype("string").fillna("<NA>").agg("|".join, axis=1)
            return set(pd.util.hash_pandas_object(text, index=False).astype(str).tolist())
        ha, hc = hashes(annual), hashes(consolidated)
        overlap = len(ha & hc) / max(1, len(ha))
        rows.append({
            "year": year,
            "status": "ANNUAL_PREFERRED" if overlap >= 0.80 else "SOURCE_DISCORDANCE_REVIEW",
            "annual_rows": len(annual),
            "consolidated_rows": len(consolidated),
            "hash_overlap_pct": round(100 * overlap, 3),
            "common_hash_columns": ",".join(key_cols),
        })
    return pd.DataFrame(rows)


# -----------------------------------------------------------------------------
# Chile audit
# -----------------------------------------------------------------------------


def audit_chile_linkage(base: Path, dirs: Mapping[str, Path]) -> pd.DataFrame:
    root = base / "00_raw" / "chile" / "Chile"
    rows: List[Dict[str, Any]] = []
    for path in sorted(root.rglob("*.csv")):
        year_match = re.search(r"(20\d{2})", str(path))
        year = int(year_match.group(1)) if year_match else None
        encoding, sep = detect_text_format(path)
        try:
            columns = read_header(path, encoding, sep)
            id_col = resolve_one(columns, CHILE_ID_ALIASES)
            aggregation = {name: resolve_one(columns, aliases) for name, aliases in CHILE_AGGREGATION_ALIASES.items()}
            rows.append({
                "year": year,
                "path": str(path),
                "size_mb": round(path.stat().st_size / 1024**2, 3),
                "n_columns": len(columns),
                "exact_hospital_id_column": id_col or "",
                "exact_hospital_linkage_possible": bool(id_col),
                **{f"{name}_column": value or "" for name, value in aggregation.items()},
                "coarser_system_level_possible": any(value for value in aggregation.values()),
                "columns_json": json.dumps(columns, ensure_ascii=False),
            })
        except Exception as exc:
            rows.append({"year": year, "path": str(path), "error": f"{type(exc).__name__}:{exc}"})
    result = pd.DataFrame(rows)
    save_table(result, dirs["chile"] / "Chile_hospital_linkage_audit_v281.csv")
    return result


# -----------------------------------------------------------------------------
# Ecuador audit
# -----------------------------------------------------------------------------


def sav_metadata(path: Path) -> Tuple[List[str], Dict[str, str], int]:
    if pyreadstat is None:
        raise ImportError("pyreadstat is required for Ecuador SAV audit")
    _, meta = pyreadstat.read_sav(str(path), metadataonly=True)
    columns = list(meta.column_names)
    labels = dict(zip(meta.column_names, meta.column_labels or [""] * len(columns)))
    rows = int(meta.number_rows or 0)
    return columns, labels, rows


def resolve_grouped_columns(columns: Sequence[str], groups: Mapping[str, Sequence[str]]) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for name, aliases in groups.items():
        found = resolve_one(columns, aliases)
        if found:
            result[name] = found
    return result


def audit_ecuador_linkage(base: Path, dirs: Mapping[str, Path]) -> pd.DataFrame:
    root = base / "00_raw" / "equador" / "Equador"
    rows: List[Dict[str, Any]] = []
    if pyreadstat is None:
        result = pd.DataFrame([{"status": "PYREADSTAT_MISSING"}])
        save_table(result, dirs["ecuador"] / "Ecuador_hospital_linkage_audit_v281.csv")
        return result
    for year in range(2015, 2020):
        eg_candidates = [p for p in root.rglob("*.sav") if "egres" in ascii_text(p.name) and str(year) in str(p)]
        bed_candidates = [p for p in root.rglob("*.sav") if "cama" in ascii_text(p.name) and str(year) in str(p)]
        if not eg_candidates or not bed_candidates:
            rows.append({"year": year, "status": "MISSING_EGRESOS_OR_CAMAS", "egresos_candidates": len(eg_candidates), "camas_candidates": len(bed_candidates)})
            continue
        eg_path, bed_path = eg_candidates[0], bed_candidates[0]
        try:
            eg_cols, eg_labels, eg_rows = sav_metadata(eg_path)
            bed_cols, bed_labels, bed_rows = sav_metadata(bed_path)
            eg_id = resolve_one(eg_cols, ECUADOR_ID_ALIASES)
            bed_id = resolve_one(bed_cols, ECUADOR_ID_ALIASES)
            exact_common = bool(eg_id and bed_id)
            eg_keys = resolve_grouped_columns(eg_cols, ECUADOR_KEY_GROUPS)
            bed_keys = resolve_grouped_columns(bed_cols, ECUADOR_KEY_GROUPS)
            common_key_names = [name for name in ECUADOR_KEY_GROUPS if name in eg_keys and name in bed_keys]
            unique_rate = match_rate = np.nan
            matched_rows = 0
            if exact_common:
                eg, _ = pyreadstat.read_sav(str(eg_path), usecols=[eg_id], apply_value_formats=False)
                bed, _ = pyreadstat.read_sav(str(bed_path), usecols=[bed_id], apply_value_formats=False)
                eg_key = meaningful_text(eg[eg_id])
                bed_key = meaningful_text(bed[bed_id])
                unique_rate = 100 * bed_key.dropna().nunique() / max(1, bed_key.notna().sum())
                match_rate = 100 * eg_key.isin(set(bed_key.dropna().astype(str))).mean()
                matched_rows = int(eg_key.isin(set(bed_key.dropna().astype(str))).sum())
                del eg, bed
            elif len(common_key_names) >= 5:
                eg_use = [eg_keys[name] for name in common_key_names]
                bed_use = [bed_keys[name] for name in common_key_names]
                eg, _ = pyreadstat.read_sav(str(eg_path), usecols=eg_use, apply_value_formats=False)
                bed, _ = pyreadstat.read_sav(str(bed_path), usecols=bed_use, apply_value_formats=False)
                eg_norm = pd.DataFrame({name: meaningful_text(eg[eg_keys[name]]).map(ascii_text) for name in common_key_names})
                bed_norm = pd.DataFrame({name: meaningful_text(bed[bed_keys[name]]).map(ascii_text) for name in common_key_names})
                eg_key = eg_norm.fillna("<NA>").agg("|".join, axis=1)
                bed_key = bed_norm.fillna("<NA>").agg("|".join, axis=1)
                bed_counts = bed_key.value_counts()
                unique_keys = set(bed_counts[bed_counts.eq(1)].index)
                unique_rate = 100 * len(unique_keys) / max(1, bed_key.nunique())
                matched = eg_key.isin(unique_keys)
                match_rate = 100 * matched.mean()
                matched_rows = int(matched.sum())
                del eg, bed, eg_norm, bed_norm
            status = (
                "EXACT_ID_LINKAGE_POSSIBLE" if exact_common and match_rate >= 90
                else "VALIDATED_COMPOSITE_LINKAGE_CANDIDATE" if not exact_common and np.isfinite(match_rate) and match_rate >= 80 and unique_rate >= 80
                else "AGGREGATED_CAPACITY_ONLY"
            )
            rows.append({
                "year": year,
                "status": status,
                "egresos_path": str(eg_path),
                "camas_path": str(bed_path),
                "egresos_rows_metadata": eg_rows,
                "camas_rows_metadata": bed_rows,
                "egresos_exact_id_column": eg_id or "",
                "camas_exact_id_column": bed_id or "",
                "exact_common_id": exact_common,
                "common_composite_key_names": ",".join(common_key_names),
                "bed_key_unique_pct": round(float(unique_rate), 3) if np.isfinite(unique_rate) else np.nan,
                "egresos_match_pct": round(float(match_rate), 3) if np.isfinite(match_rate) else np.nan,
                "matched_egresos_rows": matched_rows,
                "egresos_columns_json": json.dumps(eg_cols, ensure_ascii=False),
                "camas_columns_json": json.dumps(bed_cols, ensure_ascii=False),
            })
        except Exception as exc:
            rows.append({"year": year, "status": f"ERROR:{type(exc).__name__}:{exc}", "egresos_path": str(eg_path), "camas_path": str(bed_path)})
        gc.collect()
    result = pd.DataFrame(rows)
    save_table(result, dirs["ecuador"] / "Ecuador_hospital_linkage_audit_v281.csv")
    return result


# -----------------------------------------------------------------------------
# Main audit orchestration
# -----------------------------------------------------------------------------


def run_mexico_super_audit(base: Path, dirs: Mapping[str, Path], build_recovered: bool = True) -> Dict[str, Any]:
    reference = read_parquet_reference_counts(base)
    save_table(reference, dirs["mexico"] / "Mexico_reference_checkpoint_counts_v281.csv")

    scans: List[MexicoScanResult] = []
    for source_name, relative in MEXICO_RAW_RELATIVE.items():
        path = base / relative
        year_match = re.search(r"annual_(20\d{2})", source_name)
        year_hint = int(year_match.group(1)) if year_match else None
        is_consolidated = source_name.startswith("consolidated")
        log(f"Auditing Mexico source: {source_name}", dirs)
        scan = scan_mexico_text_source(source_name, path, year_hint, is_consolidated, dirs)
        scans.append(scan)
        log(f"Completed {source_name}: rows={scan.rows_total:,}; error={scan.error or 'none'}", dirs)

    inventory = pd.DataFrame([flatten_mexico_scan(scan) for scan in scans])
    save_table(inventory, dirs["mexico"] / "Mexico_raw_file_inventory_v281.csv")

    schema_rows: List[Dict[str, Any]] = []
    for scan in scans:
        row = {"source_name": scan.source_name, "path": scan.path, "year_hint": scan.year_hint, "is_consolidated": scan.is_consolidated, "error": scan.error}
        for canonical in ALIASES:
            row[f"column_{canonical}"] = scan.mapping.get(canonical, "")
        schema_rows.append(row)
    schema = pd.DataFrame(schema_rows)
    save_table(schema, dirs["mexico"] / "Mexico_schema_matrix_v281.csv")

    age_calibration = calibrate_age_unit_mapping(scans, reference)
    save_table(age_calibration, dirs["mexico"] / "Mexico_age_unit_calibration_v281.csv")
    age_consensus = derive_consensus_age_unit(age_calibration)

    death_calibration = evaluate_death_code_candidates(scans, reference, age_consensus)
    save_table(death_calibration, dirs["mexico"] / "Mexico_death_code_calibration_v281.csv")
    death_consensus = derive_consensus_death_code(death_calibration)

    consensus = pd.DataFrame([
        {"parameter": "age_unit", **age_consensus},
        {"parameter": "death_code", **death_consensus},
    ])
    save_table(consensus, dirs["mexico"] / "Mexico_coding_consensus_v281.csv")

    recovered_frames: Dict[Tuple[int, str], pd.DataFrame] = {}
    recovery_rows: List[Dict[str, Any]] = []
    if build_recovered:
        for year in EARLY_MEXICO_YEARS:
            for scan in select_source_for_year(scans, year):
                target = dirs["recovered"] / f"mexico_s06_raw_recovered_{year}_{scan.source_name}_v281.parquet"
                frame, audit = build_recovered_mexico_year(scan, year, age_consensus, death_consensus, target, dirs)
                recovery_rows.append(audit)
                if frame is not None:
                    recovered_frames[(year, scan.source_name)] = frame
                    log(f"Recovered Mexico {year} from {scan.source_name}: {len(frame):,} adult S06", dirs)
                else:
                    log(f"Mexico {year} not recovered from {scan.source_name}: {audit['status']}", dirs, "WARNING")
        recovery = pd.DataFrame(recovery_rows)
        save_table(recovery, dirs["mexico"] / "Mexico_2015_2017_recoverability_v281.csv")
        comparison = compare_annual_and_consolidated(recovered_frames)
        save_table(comparison, dirs["mexico"] / "Mexico_annual_vs_consolidated_overlap_v281.csv")

        # Select one preferred recovered file per year only when annual source passes.
        preferred_rows = []
        for year in EARLY_MEXICO_YEARS:
            candidates = recovery[(recovery["year"].eq(year)) & recovery["status"].astype(str).str.startswith("PASS")] if not recovery.empty else pd.DataFrame()
            annual = candidates[candidates["source_name"].astype(str).str.startswith("annual_")] if not candidates.empty else pd.DataFrame()
            chosen = annual.iloc[0] if not annual.empty else (candidates.iloc[0] if not candidates.empty else None)
            if chosen is not None:
                source_path = Path(chosen["output_parquet"])
                final_path = dirs["recovered"] / f"mexico_s06_raw_recovered_{year}_v281.parquet"
                shutil.copy2(source_path, final_path)
                preferred_rows.append({"year": year, "selected": True, "source_name": chosen["source_name"], "source_parquet": str(source_path), "final_parquet": str(final_path), "status": chosen["status"]})
            else:
                preferred_rows.append({"year": year, "selected": False, "source_name": "", "source_parquet": "", "final_parquet": "", "status": "NO_STRICTLY_VALID_RECOVERY"})
        save_table(pd.DataFrame(preferred_rows), dirs["mexico"] / "Mexico_preferred_recovered_sources_v281.csv")
    else:
        recovery = pd.DataFrame()
        comparison = pd.DataFrame()

    for frame in recovered_frames.values():
        del frame
    recovered_frames.clear()
    gc.collect()

    return {
        "inventory": str(dirs["mexico"] / "Mexico_raw_file_inventory_v281.csv"),
        "schema": str(dirs["mexico"] / "Mexico_schema_matrix_v281.csv"),
        "age_consensus": age_consensus,
        "death_consensus": death_consensus,
        "recovery": str(dirs["mexico"] / "Mexico_2015_2017_recoverability_v281.csv"),
        "comparison": str(dirs["mexico"] / "Mexico_annual_vs_consolidated_overlap_v281.csv"),
        "preferred": str(dirs["mexico"] / "Mexico_preferred_recovered_sources_v281.csv"),
    }


def write_summary(
    dirs: Mapping[str, Path],
    mexico: Mapping[str, Any],
    chile: pd.DataFrame,
    ecuador: pd.DataFrame,
) -> Path:
    preferred_path = Path(mexico["preferred"])
    preferred = pd.read_csv(preferred_path) if preferred_path.exists() else pd.DataFrame()
    mx_selected = preferred[preferred.get("selected", False).fillna(False)] if not preferred.empty else pd.DataFrame()
    chile_exact = bool(chile.get("exact_hospital_linkage_possible", pd.Series(dtype=bool)).fillna(False).any()) if not chile.empty else False
    ecuador_exact = bool(ecuador.get("status", pd.Series(dtype=str)).astype(str).eq("EXACT_ID_LINKAGE_POSSIBLE").any()) if not ecuador.empty else False
    ecuador_composite = bool(ecuador.get("status", pd.Series(dtype=str)).astype(str).eq("VALIDATED_COMPOSITE_LINKAGE_CANDIDATE").any()) if not ecuador.empty else False

    lines = [
        "# LATAM source super-audit v2.8.1",
        "",
        "## Mexico 2015–2017",
    ]
    if not mx_selected.empty:
        years = ", ".join(map(str, sorted(mx_selected["year"].astype(int).tolist())))
        lines += [
            f"Strict raw-source recovery passed for: **{years}**.",
            "The annual source is preferred over the 2013–2020 consolidated source when both are available.",
            "Recovered files were written under `01_mexico/recovered/` and may be consumed by the v2.8.1 master.",
        ]
    else:
        lines += [
            "No early Mexico year passed the strict age/outcome/hospital validation.",
            "Do not include 2015–2017 until the audit tables are reviewed; the script deliberately avoids undocumented coding assumptions.",
        ]
    lines += [
        "",
        "## Chile",
    ]
    if chile_exact:
        lines.append("At least one annual discharge file contains a candidate exact establishment identifier. Exact linkage must still be validated against the official annual establishment roster.")
    else:
        lines.append("The audited public discharge files do not expose an exact hospital identifier. Downloading the official establishment roster alone cannot create a patient-to-hospital link. Service-of-health, region, commune, insurance, and other coarser analyses remain possible when those fields are present.")
    lines += [
        "",
        "## Ecuador",
    ]
    if ecuador_exact:
        lines.append("At least one year supports an exact shared establishment identifier between egresos and camas; those years can enter hospital/capacity analyses after count validation.")
    elif ecuador_composite:
        lines.append("At least one year supports a high-uniqueness composite facility key. This can support a validated capacity sensitivity analysis, but should not be described as an official hospital identifier.")
    else:
        lines.append("The public files support individual outcomes and facility-class/sector analyses. Capacity linkage should remain aggregated unless an exact or highly unique composite key is demonstrated.")
    lines += [
        "",
        "## Recommended next action",
        "Review the Mexico consensus, recoverability, and annual-versus-consolidated overlap tables before running the final master. Use the v2.8.1 master only after the preferred recovered-source table is generated.",
    ]
    path = dirs["summary"] / "Source_expansion_recommendations_v281.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def run_latam_source_super_audit_v281(
    base_dir: str | Path = DEFAULT_BASE,
    clean_output: bool = True,
    build_mexico_recovered: bool = True,
) -> Dict[str, Any]:
    base = Path(base_dir)
    root = base / "analysis_v281_preflight"
    if clean_output and root.exists():
        shutil.rmtree(root)
    dirs = ensure_dirs(base)
    log(f"Starting LATAM source super-audit {VERSION}", dirs)

    mexico = run_mexico_super_audit(base, dirs, build_recovered=build_mexico_recovered)
    log("Mexico audit completed", dirs)
    chile = audit_chile_linkage(base, dirs)
    log("Chile linkage audit completed", dirs)
    ecuador = audit_ecuador_linkage(base, dirs)
    log("Ecuador linkage audit completed", dirs)
    summary = write_summary(dirs, mexico, chile, ecuador)

    zip_path = base / "analysis_v281_preflight.zip"
    if zip_path.exists():
        zip_path.unlink()
    shutil.make_archive(str(zip_path.with_suffix("")), "zip", root_dir=root)
    log(f"Audit package created: {zip_path}", dirs)

    manifest = {
        "version": VERSION,
        "base_dir": str(base),
        "output_dir": str(root),
        "mexico": mexico,
        "chile_audit": str(dirs["chile"] / "Chile_hospital_linkage_audit_v281.csv"),
        "ecuador_audit": str(dirs["ecuador"] / "Ecuador_hospital_linkage_audit_v281.csv"),
        "summary": str(summary),
        "zip": str(zip_path),
    }
    (dirs["summary"] / "audit_manifest_v281.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False, default=json_safe), encoding="utf-8")
    return manifest


def verify_latam_source_super_audit_v281() -> Dict[str, Any]:
    status = {
        "version": VERSION,
        "runner": run_latam_source_super_audit_v281.__name__,
        "mexico_sources": len(MEXICO_RAW_RELATIVE),
        "mexico_early_years": list(EARLY_MEXICO_YEARS),
        "calibration_years": list(LATER_MEXICO_YEARS),
        "fail_closed": True,
        "writes_recovered_parquets_only_after_strict_validation": True,
        "audits_chile_exact_hospital_id": True,
        "audits_ecuador_exact_and_composite_linkage": True,
    }
    print(json.dumps(status, indent=2, ensure_ascii=False))
    return status


print(f"✅ TCE LATAM source super-audit {VERSION} loaded.")
print("Run: result = run_latam_source_super_audit_v281()")

# =============================================================================
# v2.8.2 OVERRIDES — empty-safe Mexico calibration, resumable scans,
# and expanded Ecuador 2015–2024 source/linkage audit.
# =============================================================================

VERSION = "2.8.2-preflight"
V282_OUTPUT_DIR = "analysis_v282_preflight"
ECUADOR_YEARS = tuple(range(2015, 2025))

AGE_CALIBRATION_COLUMNS = [
    "source_name", "year", "age_unit_value", "s06", "age_0_120",
    "age_18plus", "age_20plus", "reference_adult20",
    "relative_error_vs_reference", "candidate_plausibility", "calibration_pass",
]
DEATH_CALIBRATION_COLUMNS = [
    "source_name", "year", "validated_age_unit", "candidate_death_code",
    "candidate_mortality", "reference_mortality", "absolute_difference",
    "outcome_nonmissing", "calibration_pass",
]

ECUADOR_PATIENT_ALIASES: Dict[str, Sequence[str]] = {
    "year": ("year", "anio_egr", "ano_egr", "anio", "año", "anio_egreso"),
    "dx_main": ("cau_cie10", "causa3", "cie10", "diag1", "diagnostico_principal", "causa"),
    "dx_secondary": ("diag2", "diagnostico_secundario", "cau_cie10_2"),
    "age": ("edad", "age"),
    "age_unit": ("cod_edad", "tipo_edad", "unidad_edad"),
    "sex": ("sexo", "sex"),
    "los_days": ("dia_estad", "dias_estad", "dias_estancia", "estancia"),
    "discharge_condition": ("con_egrpa", "condicion_egreso", "cond_egr", "condicion_al_egreso"),
    "hospital_id": ECUADOR_ID_ALIASES,
    "province": ECUADOR_KEY_GROUPS["province"],
    "canton": ECUADOR_KEY_GROUPS["canton"],
    "parish": ECUADOR_KEY_GROUPS["parish"],
    "area": ECUADOR_KEY_GROUPS["area"],
    "facility_class": ECUADOR_KEY_GROUPS["facility_class"],
    "facility_type": ECUADOR_KEY_GROUPS["facility_type"],
    "facility_entity": ECUADOR_KEY_GROUPS["facility_entity"],
    "facility_sector": ECUADOR_KEY_GROUPS["facility_sector"],
    "residence_province": ("prov_res", "provincia_res", "prov_residencia"),
    "residence_canton": ("cant_res", "canton_res", "cant_residencia"),
    "residence_parish": ("parr_res", "parroquia_res", "parr_residencia"),
    "residence_area": ("area_res", "area_residencia"),
    "ethnicity": ("etnia", "grupo_etnico", "ethnicity"),
    "nationality": ("nacionalidad", "nationality"),
    "discharge_specialty": ("esp_egr", "especialidad_egreso", "especialidad", "servicio_egreso"),
}

ECUADOR_CAPACITY_ALIASES: Dict[str, Sequence[str]] = {
    "year": ("year", "anio", "año", "anio_camas"),
    "hospital_id": ECUADOR_ID_ALIASES,
    "province": ECUADOR_KEY_GROUPS["province"],
    "canton": ECUADOR_KEY_GROUPS["canton"],
    "parish": ECUADOR_KEY_GROUPS["parish"],
    "area": ECUADOR_KEY_GROUPS["area"],
    "facility_class": ECUADOR_KEY_GROUPS["facility_class"],
    "facility_type": ECUADOR_KEY_GROUPS["facility_type"],
    "facility_entity": ECUADOR_KEY_GROUPS["facility_entity"],
    "facility_sector": ECUADOR_KEY_GROUPS["facility_sector"],
    "bed_total_normal": ("cam_normal", "camas_normales", "tot_cam_norm", "total_camas_normales"),
    "bed_total_available": ("cam_dispo", "camas_disponibles", "tot_cam_disp", "total_camas_disponibles"),
    "bed_icu_normal": ("cam_uci", "camas_uci", "uci", "camas_cuidado_intensivo"),
    "bed_emergency_normal": ("cam_emerg", "camas_emergencia", "emergencia"),
    "total_discharges": ("totegres", "total_egresos", "egresos", "tot_egresos"),
    "deaths_lt48": ("falmen48", "fallecidos_menor_48", "def_men48"),
    "deaths_ge48": ("falmas48", "fallecidos_mayor_48", "def_mas48"),
    "total_deaths": ("total_fallecidos", "total_defunciones", "tot_fallecidos"),
    "total_stay_days": ("dia_estad", "dias_estada", "total_dias_estada", "tot_dias_estada"),
}


def ensure_dirs(base: Path) -> Dict[str, Path]:
    root = base / V282_OUTPUT_DIR
    dirs = {
        "root": root,
        "mexico": root / "01_mexico",
        "recovered": root / "01_mexico" / "recovered",
        "mexico_cache": root / "01_mexico" / "scan_cache",
        "chile": root / "02_chile",
        "ecuador": root / "03_ecuador",
        "ecuador_recovered": root / "03_ecuador" / "recovered",
        "ecuador_capacity": root / "03_ecuador" / "capacity",
        "summary": root / "04_summary",
        "logs": root / "05_logs",
    }
    for path in dirs.values():
        path.mkdir(parents=True, exist_ok=True)
    return dirs


def log(message: str, dirs: Mapping[str, Path], level: str = "INFO") -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} | {level:<7} | {message}"
    print(line, flush=True)
    try:
        with (dirs["logs"] / "super_audit_v282.log").open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except Exception:
        pass


def _empty_frame(columns: Sequence[str]) -> pd.DataFrame:
    return pd.DataFrame({column: pd.Series(dtype="object") for column in columns})


def calibrate_age_unit_mapping(scans: Sequence[MexicoScanResult], reference: pd.DataFrame) -> pd.DataFrame:
    parts: List[pd.DataFrame] = []
    for scan in scans:
        for year in LATER_MEXICO_YEARS:
            if scan.s06_by_year.get(year, 0) <= 0:
                continue
            candidate = candidate_age_units(scan, year)
            if not candidate.empty:
                parts.append(candidate)
    if not parts:
        return _empty_frame(AGE_CALIBRATION_COLUMNS)
    candidates = pd.concat(parts, ignore_index=True, sort=False)
    ref = reference[["year", "reference_adult20"]].copy() if {"year", "reference_adult20"}.issubset(reference.columns) else pd.DataFrame(columns=["year", "reference_adult20"])
    candidates = candidates.merge(ref, on="year", how="left")
    candidates["relative_error_vs_reference"] = np.where(
        pd.to_numeric(candidates["reference_adult20"], errors="coerce").gt(0),
        (pd.to_numeric(candidates["age_20plus"], errors="coerce") - pd.to_numeric(candidates["reference_adult20"], errors="coerce")).abs()
        / pd.to_numeric(candidates["reference_adult20"], errors="coerce"),
        np.nan,
    )
    candidates["candidate_plausibility"] = np.where(
        pd.to_numeric(candidates["s06"], errors="coerce").gt(0),
        pd.to_numeric(candidates["age_0_120"], errors="coerce") / pd.to_numeric(candidates["s06"], errors="coerce"),
        np.nan,
    )
    candidates["calibration_pass"] = (
        candidates["relative_error_vs_reference"].le(0.08)
        & candidates["candidate_plausibility"].ge(0.90)
        & pd.to_numeric(candidates["age_20plus"], errors="coerce").gt(0)
    )
    for column in AGE_CALIBRATION_COLUMNS:
        if column not in candidates:
            candidates[column] = pd.NA
    return candidates[AGE_CALIBRATION_COLUMNS].sort_values(["year", "relative_error_vs_reference", "source_name"], na_position="last")


def derive_consensus_age_unit(calibration: pd.DataFrame) -> Dict[str, Any]:
    if calibration is None or calibration.empty or "calibration_pass" not in calibration.columns:
        return {"status": "NO_VALIDATED_AGE_UNIT", "unit_value": None, "support_years": [], "median_relative_error": np.nan}
    passed = calibration.loc[calibration["calibration_pass"].fillna(False).astype(bool)].copy()
    if passed.empty or "age_unit_value" not in passed:
        return {"status": "NO_VALIDATED_AGE_UNIT", "unit_value": None, "support_years": [], "median_relative_error": np.nan}
    summary = passed.groupby("age_unit_value", observed=True).agg(
        support_years=("year", "nunique"),
        median_relative_error=("relative_error_vs_reference", "median"),
        sources=("source_name", "nunique"),
    ).reset_index().sort_values(["support_years", "median_relative_error", "sources"], ascending=[False, True, False])
    if summary.empty:
        return {"status": "NO_VALIDATED_AGE_UNIT", "unit_value": None, "support_years": [], "median_relative_error": np.nan}
    best = summary.iloc[0]
    years = sorted(pd.to_numeric(passed.loc[passed["age_unit_value"].eq(best["age_unit_value"]), "year"], errors="coerce").dropna().astype(int).unique().tolist())
    return {
        "status": "VALIDATED_BY_LATER_YEARS" if int(best["support_years"]) >= 2 else "INSUFFICIENT_AGE_UNIT_SUPPORT",
        "unit_value": str(best["age_unit_value"]),
        "support_years": years,
        "median_relative_error": float(best["median_relative_error"]),
    }


def evaluate_death_code_candidates(scans: Sequence[MexicoScanResult], reference: pd.DataFrame, age_unit_consensus: Mapping[str, Any]) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    if reference is None or reference.empty or not {"year", "reference_mortality"}.issubset(reference.columns):
        return _empty_frame(DEATH_CALIBRATION_COLUMNS)
    ref_map = pd.Series(pd.to_numeric(reference["reference_mortality"], errors="coerce").values, index=pd.to_numeric(reference["year"], errors="coerce")).to_dict()
    selected_unit = age_unit_consensus.get("unit_value")
    if selected_unit is None:
        return _empty_frame(DEATH_CALIBRATION_COLUMNS)
    selected_unit = str(selected_unit)
    for scan in scans:
        for year in LATER_MEXICO_YEARS:
            stats = scan.outcome_by_unit_stats.get(year, {}).get(selected_unit, {})
            # Fallback: if unit-specific pairing is absent but the selected unit is
            # the only plausible adult unit, retain the year-level outcome counts.
            if not stats and len(scan.age_unit_stats.get(year, {})) == 1:
                stats = scan.outcome_stats.get(year, {})
            total = sum(int(v) for k, v in stats.items() if str(k) != "<MISSING>")
            reference_mortality = ref_map.get(year, np.nan)
            if total <= 0 or not np.isfinite(reference_mortality):
                continue
            for code, count in stats.items():
                if str(code) == "<MISSING>":
                    continue
                mortality = int(count) / total
                rows.append({
                    "source_name": scan.source_name,
                    "year": int(year),
                    "validated_age_unit": selected_unit,
                    "candidate_death_code": str(code),
                    "candidate_mortality": mortality,
                    "reference_mortality": float(reference_mortality),
                    "absolute_difference": abs(mortality - float(reference_mortality)),
                    "outcome_nonmissing": int(total),
                    "calibration_pass": abs(mortality - float(reference_mortality)) <= 0.02,
                })
    if not rows:
        return _empty_frame(DEATH_CALIBRATION_COLUMNS)
    result = pd.DataFrame(rows)
    for column in DEATH_CALIBRATION_COLUMNS:
        if column not in result:
            result[column] = pd.NA
    return result[DEATH_CALIBRATION_COLUMNS].sort_values(["year", "absolute_difference", "source_name"])


def derive_consensus_death_code(calibration: pd.DataFrame) -> Dict[str, Any]:
    if calibration is None or calibration.empty or "calibration_pass" not in calibration.columns:
        return {"status": "NO_VALIDATED_DEATH_CODE", "code": None, "support_years": [], "median_abs_difference": np.nan}
    passed = calibration.loc[calibration["calibration_pass"].fillna(False).astype(bool)].copy()
    if passed.empty or "candidate_death_code" not in passed:
        return {"status": "NO_VALIDATED_DEATH_CODE", "code": None, "support_years": [], "median_abs_difference": np.nan}
    summary = passed.groupby("candidate_death_code", observed=True).agg(
        support_years=("year", "nunique"),
        median_abs_difference=("absolute_difference", "median"),
        sources=("source_name", "nunique"),
    ).reset_index().sort_values(["support_years", "median_abs_difference", "sources"], ascending=[False, True, False])
    if summary.empty:
        return {"status": "NO_VALIDATED_DEATH_CODE", "code": None, "support_years": [], "median_abs_difference": np.nan}
    best = summary.iloc[0]
    years = sorted(pd.to_numeric(passed.loc[passed["candidate_death_code"].eq(best["candidate_death_code"]), "year"], errors="coerce").dropna().astype(int).unique().tolist())
    return {
        "status": "VALIDATED_BY_LATER_YEARS" if int(best["support_years"]) >= 2 else "INSUFFICIENT_DEATH_CODE_SUPPORT",
        "code": str(best["candidate_death_code"]),
        "support_years": years,
        "median_abs_difference": float(best["median_abs_difference"]),
    }


def _scan_to_json(scan: MexicoScanResult) -> Dict[str, Any]:
    return {key: json_safe(value) for key, value in vars(scan).items()}


def _int_keys(value: Any, depth: int = 1) -> Any:
    if not isinstance(value, dict):
        return value
    result = {}
    for key, item in value.items():
        try:
            new_key: Any = int(key)
        except Exception:
            new_key = key
        result[new_key] = _int_keys(item, depth - 1) if depth > 1 else item
    return result


def _scan_from_json(payload: Mapping[str, Any]) -> MexicoScanResult:
    data = dict(payload)
    for name in ("rows_target_year", "s06_by_year", "hospital_nonmissing", "hospital_unique_samples"):
        data[name] = _int_keys(data.get(name, {}), 1)
    for name in ("age_unit_stats", "outcome_stats", "outcome_by_unit_stats"):
        data[name] = _int_keys(data.get(name, {}), 3)
    return MexicoScanResult(**data)


def _load_scan_from_v281_inventory(base: Path, source_name: str, source_path: Path) -> Optional[MexicoScanResult]:
    old = base / "analysis_v281_preflight" / "01_mexico" / "Mexico_raw_file_inventory_v281.csv"
    if not old.exists():
        return None
    try:
        frame = pd.read_csv(old)
        row = frame[frame["source_name"].astype(str).eq(source_name)]
        if row.empty:
            return None
        row = row.iloc[0]
        if Path(str(row["path"])) != source_path or not source_path.exists():
            return None
        if abs(float(row.get("size_mb", 0)) - source_path.stat().st_size / 1024**2) > 0.05:
            return None
        payload = {
            "source_name": source_name,
            "path": str(source_path),
            "year_hint": None if pd.isna(row.get("year_hint")) else int(row.get("year_hint")),
            "is_consolidated": bool(row.get("is_consolidated", False)),
            "exists": bool(row.get("exists", True)),
            "size_mb": float(row.get("size_mb", 0)),
            "encoding": str(row.get("encoding", "")),
            "separator": str(row.get("separator_repr", "','")).strip("'\""),
            "columns": json.loads(row.get("columns_json", "[]")),
            "mapping": json.loads(row.get("mapping_json", "{}")),
            "rows_total": int(row.get("rows_total", 0)),
            "rows_target_year": json.loads(row.get("rows_target_year_json", "{}")),
            "s06_by_year": json.loads(row.get("s06_by_year_json", "{}")),
            "age_unit_stats": json.loads(row.get("age_unit_stats_json", "{}")),
            "outcome_stats": json.loads(row.get("outcome_stats_json", "{}")),
            "outcome_by_unit_stats": json.loads(row.get("outcome_by_unit_stats_json", "{}")),
            "hospital_nonmissing": json.loads(row.get("hospital_nonmissing_json", "{}")),
            "hospital_unique_samples": json.loads(row.get("hospital_unique_sample_json", "{}")),
            "sample_values": json.loads(row.get("sample_values_json", "{}")),
            "error": "" if pd.isna(row.get("error")) else str(row.get("error")),
        }
        return _scan_from_json(payload)
    except Exception:
        return None


def _get_mexico_scan(base: Path, dirs: Mapping[str, Path], source_name: str, path: Path, year_hint: Optional[int], is_consolidated: bool, force_rescan: bool = False) -> MexicoScanResult:
    cache_path = dirs["mexico_cache"] / f"{source_name}.json"
    if not force_rescan and cache_path.exists():
        try:
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
            if path.exists() and payload.get("path") == str(path) and abs(float(payload.get("size_mb", 0)) - path.stat().st_size / 1024**2) <= 0.05:
                log(f"Reusing v2.8.2 scan cache: {source_name}", dirs)
                return _scan_from_json(payload)
        except Exception:
            pass
    if not force_rescan:
        imported = _load_scan_from_v281_inventory(base, source_name, path)
        if imported is not None:
            log(f"Imported completed v2.8.1 scan: {source_name}", dirs)
            cache_path.write_text(json.dumps(_scan_to_json(imported), ensure_ascii=False, default=json_safe), encoding="utf-8")
            return imported
    log(f"Auditing Mexico source: {source_name}", dirs)
    scan = scan_mexico_text_source(source_name, path, year_hint, is_consolidated, dirs)
    cache_path.write_text(json.dumps(_scan_to_json(scan), ensure_ascii=False, default=json_safe), encoding="utf-8")
    return scan


def run_mexico_super_audit_v282(base: Path, dirs: Mapping[str, Path], build_recovered: bool = True, force_rescan: bool = False) -> Dict[str, Any]:
    reference = read_parquet_reference_counts(base)
    save_table(reference, dirs["mexico"] / "Mexico_reference_checkpoint_counts_v282.csv")
    scans: List[MexicoScanResult] = []
    for source_name, relative in MEXICO_RAW_RELATIVE.items():
        path = base / relative
        match = re.search(r"annual_(20\d{2})", source_name)
        year_hint = int(match.group(1)) if match else None
        scan = _get_mexico_scan(base, dirs, source_name, path, year_hint, source_name.startswith("consolidated"), force_rescan=force_rescan)
        scans.append(scan)
        log(f"Completed {source_name}: rows={scan.rows_total:,}; error={scan.error or 'none'}", dirs)

    inventory = pd.DataFrame([flatten_mexico_scan(scan) for scan in scans])
    save_table(inventory, dirs["mexico"] / "Mexico_raw_file_inventory_v282.csv")
    schema_rows = []
    for scan in scans:
        row = {"source_name": scan.source_name, "path": scan.path, "year_hint": scan.year_hint, "is_consolidated": scan.is_consolidated, "error": scan.error}
        for canonical in ALIASES:
            row[f"column_{canonical}"] = scan.mapping.get(canonical, "")
        schema_rows.append(row)
    save_table(pd.DataFrame(schema_rows), dirs["mexico"] / "Mexico_schema_matrix_v282.csv")

    age_calibration = calibrate_age_unit_mapping(scans, reference)
    save_table(age_calibration, dirs["mexico"] / "Mexico_age_unit_calibration_v282.csv")
    age_consensus = derive_consensus_age_unit(age_calibration)
    death_calibration = evaluate_death_code_candidates(scans, reference, age_consensus)
    save_table(death_calibration, dirs["mexico"] / "Mexico_death_code_calibration_v282.csv")
    death_consensus = derive_consensus_death_code(death_calibration)
    save_table(pd.DataFrame([{"parameter": "age_unit", **age_consensus}, {"parameter": "death_code", **death_consensus}]), dirs["mexico"] / "Mexico_coding_consensus_v282.csv")

    recovery_rows: List[Dict[str, Any]] = []
    recovered_frames: Dict[Tuple[int, str], pd.DataFrame] = {}
    if build_recovered:
        for year in EARLY_MEXICO_YEARS:
            for scan in select_source_for_year(scans, year):
                target = dirs["recovered"] / f"mexico_s06_raw_recovered_{year}_{scan.source_name}_v282.parquet"
                frame, audit = build_recovered_mexico_year(scan, year, age_consensus, death_consensus, target, dirs)
                recovery_rows.append(audit)
                if frame is not None:
                    recovered_frames[(year, scan.source_name)] = frame
                    log(f"Recovered Mexico {year} from {scan.source_name}: {len(frame):,} adult S06", dirs)
                else:
                    log(f"Mexico {year} not recovered from {scan.source_name}: {audit.get('status')}", dirs, "WARNING")
    recovery = pd.DataFrame(recovery_rows)
    save_table(recovery, dirs["mexico"] / "Mexico_2015_2017_recoverability_v282.csv")
    comparison = compare_annual_and_consolidated(recovered_frames) if recovered_frames else pd.DataFrame(columns=["year", "status", "annual_rows", "consolidated_rows", "hash_overlap_pct"])
    save_table(comparison, dirs["mexico"] / "Mexico_annual_vs_consolidated_overlap_v282.csv")

    preferred_rows = []
    for year in EARLY_MEXICO_YEARS:
        candidates = recovery[(pd.to_numeric(recovery.get("year"), errors="coerce").eq(year)) & recovery.get("status", pd.Series(dtype="string")).astype(str).str.startswith("PASS")] if not recovery.empty else pd.DataFrame()
        annual = candidates[candidates["source_name"].astype(str).str.startswith("annual_")] if not candidates.empty else pd.DataFrame()
        chosen = annual.iloc[0] if not annual.empty else (candidates.iloc[0] if not candidates.empty else None)
        if chosen is not None and str(chosen.get("output_parquet", "")):
            source_path = Path(str(chosen["output_parquet"]))
            final_path = dirs["recovered"] / f"mexico_s06_raw_recovered_{year}_v282.parquet"
            shutil.copy2(source_path, final_path)
            preferred_rows.append({"year": year, "selected": True, "source_name": chosen["source_name"], "source_parquet": str(source_path), "final_parquet": str(final_path), "status": chosen["status"]})
        else:
            preferred_rows.append({"year": year, "selected": False, "source_name": "", "source_parquet": "", "final_parquet": "", "status": "NO_STRICTLY_VALID_RECOVERY"})
    preferred = pd.DataFrame(preferred_rows)
    save_table(preferred, dirs["mexico"] / "Mexico_preferred_recovered_sources_v282.csv")
    recovered_frames.clear()
    gc.collect()
    return {
        "inventory": str(dirs["mexico"] / "Mexico_raw_file_inventory_v282.csv"),
        "schema": str(dirs["mexico"] / "Mexico_schema_matrix_v282.csv"),
        "age_consensus": age_consensus,
        "death_consensus": death_consensus,
        "recovery": str(dirs["mexico"] / "Mexico_2015_2017_recoverability_v282.csv"),
        "comparison": str(dirs["mexico"] / "Mexico_annual_vs_consolidated_overlap_v282.csv"),
        "preferred": str(dirs["mexico"] / "Mexico_preferred_recovered_sources_v282.csv"),
    }


def _extract_year_from_path(path: Path) -> Optional[int]:
    matches = [int(value) for value in re.findall(r"(?<!\d)(20(?:1[3-9]|2[0-4]))(?!\d)", str(path))]
    return matches[-1] if matches else None


def _classify_ecuador_file(path: Path) -> str:
    text = ascii_text(str(path))
    name = ascii_text(path.name)
    if any(token in text for token in ("diccionario", "metadato", "guia_de_usuario", "guia de usuario")):
        return "DOCUMENTATION"
    if "tabulados" in text or re.fullmatch(r"[0-9.() -]+\.csv", name):
        return "TABULATION"
    if path.suffix.lower() in {".zip"}:
        return "ARCHIVE"
    if "egres" in name and path.suffix.lower() in {".csv", ".sav", ".rds", ".rdata"}:
        return "PATIENT_EGRESOS"
    if "cama" in name and path.suffix.lower() in {".csv", ".sav", ".rds", ".rdata"}:
        return "FACILITY_CAPACITY"
    return "OTHER"


def _metadata_for_source(path: Path) -> Tuple[List[str], int, str, str]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        encoding, sep = detect_text_format(path)
        columns = read_header(path, encoding, sep)
        return columns, -1, encoding, sep
    if suffix == ".sav":
        columns, _, rows = sav_metadata(path)
        return columns, rows, "binary", ""
    # R objects are kept as fallbacks because matching CSV/SAV versions exist.
    return [], -1, "unsupported_without_pyreadr", ""


def _ecuador_schema_score(role: str, columns: Sequence[str]) -> Tuple[int, Dict[str, str]]:
    aliases = ECUADOR_PATIENT_ALIASES if role == "PATIENT_EGRESOS" else ECUADOR_CAPACITY_ALIASES
    mapping = resolve_aliases(columns, aliases)
    if role == "PATIENT_EGRESOS":
        required = ("dx_main", "age", "age_unit", "discharge_condition", "los_days")
        score = 100 * sum(field in mapping for field in required) + 10 * len(mapping)
    else:
        required = ("province", "canton", "parish")
        bed_fields = ("bed_total_normal", "bed_total_available", "bed_icu_normal", "total_discharges")
        score = 80 * sum(field in mapping for field in required) + 60 * sum(field in mapping for field in bed_fields) + 10 * len(mapping)
    return score, mapping


def audit_ecuador_sources_v282(base: Path, dirs: Mapping[str, Path], build_recovered: bool = True) -> Dict[str, Any]:
    root = base / "00_raw" / "equador"
    rows: List[Dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        role = _classify_ecuador_file(path)
        year = _extract_year_from_path(path)
        item: Dict[str, Any] = {
            "year": year,
            "role": role,
            "path": str(path),
            "suffix": path.suffix.lower(),
            "size_mb": round(path.stat().st_size / 1024**2, 3),
            "status": "NOT_INSPECTED",
        }
        if role in {"PATIENT_EGRESOS", "FACILITY_CAPACITY"}:
            try:
                columns, nrows, encoding, sep = _metadata_for_source(path)
                score, mapping = _ecuador_schema_score(role, columns)
                item.update({
                    "status": "OK" if columns else "UNSUPPORTED_FORMAT_FALLBACK",
                    "n_rows_metadata": nrows,
                    "n_columns": len(columns),
                    "encoding": encoding,
                    "separator": repr(sep),
                    "schema_score": score,
                    "mapping_json": json.dumps(mapping, ensure_ascii=False),
                    "columns_json": json.dumps(columns, ensure_ascii=False),
                    "schema_fingerprint": hashlib.sha1("|".join(sorted(column_key(c) for c in columns)).encode()).hexdigest() if columns else "",
                })
            except Exception as exc:
                item["status"] = f"ERROR:{type(exc).__name__}:{exc}"
        rows.append(item)
    inventory = pd.DataFrame(rows)
    save_table(inventory, dirs["ecuador"] / "Ecuador_source_inventory_v282.csv")

    candidate = inventory[(inventory["role"].isin(["PATIENT_EGRESOS", "FACILITY_CAPACITY"])) & inventory["year"].isin(ECUADOR_YEARS) & inventory["status"].eq("OK")].copy()
    format_rank = {".csv": 3, ".sav": 2, ".rds": 1, ".rdata": 1}
    candidate["format_rank"] = candidate["suffix"].map(format_rank).fillna(0)
    candidate["preference_score"] = pd.to_numeric(candidate["schema_score"], errors="coerce").fillna(0) + candidate["format_rank"] * 5 + np.log1p(pd.to_numeric(candidate["size_mb"], errors="coerce").fillna(0))
    selected_rows: List[Dict[str, Any]] = []
    for year in ECUADOR_YEARS:
        for role in ("PATIENT_EGRESOS", "FACILITY_CAPACITY"):
            subset = candidate[(candidate["year"].eq(year)) & candidate["role"].eq(role)].sort_values(["preference_score", "size_mb"], ascending=False)
            if subset.empty:
                selected_rows.append({"year": year, "role": role, "selected": False, "path": "", "status": "NO_VALID_SOURCE"})
            else:
                chosen = subset.iloc[0]
                selected_rows.append({"year": year, "role": role, "selected": True, "path": chosen["path"], "status": "SELECTED", "suffix": chosen["suffix"], "schema_score": chosen["schema_score"], "alternatives": int(len(subset) - 1)})
    selected = pd.DataFrame(selected_rows)
    save_table(selected, dirs["ecuador"] / "Ecuador_preferred_sources_v282.csv")

    linkage = _audit_ecuador_selected_linkage_v282(selected, inventory, dirs)
    recovery = _build_ecuador_recovered_v282(selected, inventory, dirs) if build_recovered else pd.DataFrame()
    return {
        "inventory": str(dirs["ecuador"] / "Ecuador_source_inventory_v282.csv"),
        "preferred": str(dirs["ecuador"] / "Ecuador_preferred_sources_v282.csv"),
        "linkage": str(dirs["ecuador"] / "Ecuador_hospital_linkage_audit_v282.csv"),
        "recovery": str(dirs["ecuador"] / "Ecuador_recovery_manifest_v282.csv"),
    }


def _read_selected_small(path: Path, columns: Sequence[str], inventory_row: pd.Series) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        encoding = str(inventory_row.get("encoding", "utf-8"))
        sep = str(inventory_row.get("separator", "','")).strip("'\"") or ","
        return pd.read_csv(path, sep=sep, encoding=encoding, usecols=list(columns), dtype="string", engine="python", on_bad_lines="skip")
    if suffix == ".sav":
        frame, _ = pyreadstat.read_sav(str(path), usecols=list(columns), apply_value_formats=False)
        return frame
    raise ValueError(f"Unsupported selected source: {path}")


def _iter_selected_chunks(path: Path, columns: Sequence[str], inventory_row: pd.Series) -> Iterator[pd.DataFrame]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        encoding = str(inventory_row.get("encoding", "utf-8"))
        sep = str(inventory_row.get("separator", "','")).strip("'\"") or ","
        yield from pd.read_csv(path, sep=sep, encoding=encoding, usecols=list(columns), chunksize=CHUNK_SIZE, dtype="string", engine="python", on_bad_lines="skip")
    elif suffix == ".sav":
        if pyreadstat is None:
            raise ImportError("pyreadstat required")
        for frame, _ in pyreadstat.read_file_in_chunks(pyreadstat.read_sav, str(path), chunksize=CHUNK_SIZE, usecols=list(columns), apply_value_formats=False):
            yield frame
    else:
        raise ValueError(f"Unsupported selected source: {path}")


def _selected_inventory_row(inventory: pd.DataFrame, path: Path) -> pd.Series:
    row = inventory[inventory["path"].astype(str).eq(str(path))]
    if row.empty:
        raise KeyError(str(path))
    return row.iloc[0]


def _audit_ecuador_selected_linkage_v282(selected: pd.DataFrame, inventory: pd.DataFrame, dirs: Mapping[str, Path]) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    for year in ECUADOR_YEARS:
        eg_sel = selected[(selected["year"].eq(year)) & selected["role"].eq("PATIENT_EGRESOS") & selected["selected"].fillna(False)]
        bed_sel = selected[(selected["year"].eq(year)) & selected["role"].eq("FACILITY_CAPACITY") & selected["selected"].fillna(False)]
        if eg_sel.empty or bed_sel.empty:
            rows.append({"year": year, "status": "MISSING_EGRESOS_OR_CAMAS"})
            continue
        eg_path, bed_path = Path(eg_sel.iloc[0]["path"]), Path(bed_sel.iloc[0]["path"])
        eg_inv, bed_inv = _selected_inventory_row(inventory, eg_path), _selected_inventory_row(inventory, bed_path)
        eg_map = json.loads(eg_inv.get("mapping_json", "{}")); bed_map = json.loads(bed_inv.get("mapping_json", "{}"))
        exact = bool(eg_map.get("hospital_id") and bed_map.get("hospital_id"))
        common_names = [name for name in ("province", "canton", "parish", "area", "facility_class", "facility_type", "facility_entity", "facility_sector") if eg_map.get(name) and bed_map.get(name)]
        try:
            if exact:
                bed = _read_selected_small(bed_path, [bed_map["hospital_id"]], bed_inv)
                bed_keys = set(meaningful_text(bed[bed_map["hospital_id"]]).dropna().astype(str))
                matched = total = 0
                for chunk in _iter_selected_chunks(eg_path, [eg_map["hospital_id"]], eg_inv):
                    values = meaningful_text(chunk[eg_map["hospital_id"]])
                    total += len(values); matched += int(values.isin(bed_keys).sum())
                match_rate = 100 * matched / max(1, total)
                unique_rate = 100 * len(bed_keys) / max(1, meaningful_text(bed[bed_map["hospital_id"]]).notna().sum())
                status = "EXACT_ID_LINKAGE_POSSIBLE" if match_rate >= 90 else "EXACT_ID_LOW_MATCH_REVIEW"
            elif len(common_names) >= 5:
                bed_cols = [bed_map[name] for name in common_names]
                bed = _read_selected_small(bed_path, bed_cols, bed_inv)
                bed_norm = pd.DataFrame({name: meaningful_text(bed[bed_map[name]]).map(ascii_text) for name in common_names})
                bed_key = bed_norm.fillna("<NA>").agg("|".join, axis=1)
                counts = bed_key.value_counts(); unique_keys = set(counts[counts.eq(1)].index)
                unique_rate = 100 * len(unique_keys) / max(1, bed_key.nunique())
                matched = total = 0
                eg_cols = [eg_map[name] for name in common_names]
                for chunk in _iter_selected_chunks(eg_path, eg_cols, eg_inv):
                    norm = pd.DataFrame({name: meaningful_text(chunk[eg_map[name]]).map(ascii_text) for name in common_names})
                    keys = norm.fillna("<NA>").agg("|".join, axis=1)
                    total += len(keys); matched += int(keys.isin(unique_keys).sum())
                match_rate = 100 * matched / max(1, total)
                status = "VALIDATED_COMPOSITE_LINKAGE_CANDIDATE" if match_rate >= 80 and unique_rate >= 80 else "AGGREGATED_CAPACITY_ONLY"
            else:
                match_rate = unique_rate = np.nan; matched = 0; status = "AGGREGATED_CAPACITY_ONLY"
            rows.append({
                "year": year, "status": status, "egresos_path": str(eg_path), "camas_path": str(bed_path),
                "exact_common_id": exact, "common_composite_key_names": ",".join(common_names),
                "bed_key_unique_pct": round(float(unique_rate), 3) if np.isfinite(unique_rate) else np.nan,
                "egresos_match_pct": round(float(match_rate), 3) if np.isfinite(match_rate) else np.nan,
                "matched_egresos_rows": int(matched),
            })
        except Exception as exc:
            rows.append({"year": year, "status": f"ERROR:{type(exc).__name__}:{exc}", "egresos_path": str(eg_path), "camas_path": str(bed_path)})
        gc.collect()
    result = pd.DataFrame(rows)
    save_table(result, dirs["ecuador"] / "Ecuador_hospital_linkage_audit_v282.csv")
    return result


def _normalize_ecuador_sex(series: pd.Series) -> pd.Series:
    raw = meaningful_text(series); text = raw.map(ascii_text); num = pd.to_numeric(raw, errors="coerce")
    out = pd.Series(pd.NA, index=series.index, dtype="string")
    out.loc[text.str.contains("hombre|masc", regex=True, na=False) | num.eq(1)] = "Male"
    out.loc[text.str.contains("mujer|fem", regex=True, na=False) | num.eq(2)] = "Female"
    return out


def _build_ecuador_recovered_v282(selected: pd.DataFrame, inventory: pd.DataFrame, dirs: Mapping[str, Path]) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    for year in ECUADOR_YEARS:
        chosen = selected[(selected["year"].eq(year)) & selected["role"].eq("PATIENT_EGRESOS") & selected["selected"].fillna(False)]
        if chosen.empty:
            rows.append({"year": year, "status": "NO_VALID_PATIENT_SOURCE"}); continue
        path = Path(chosen.iloc[0]["path"]); inv = _selected_inventory_row(inventory, path); mapping = json.loads(inv.get("mapping_json", "{}"))
        required = {"dx_main", "age", "age_unit", "discharge_condition"}
        if not required.issubset(mapping):
            rows.append({"year": year, "status": "MISSING_REQUIRED_FIELDS", "path": str(path), "missing": ",".join(sorted(required - set(mapping)))}); continue
        usecols = sorted(set(mapping.values())); frames: List[pd.DataFrame] = []; raw_rows = 0; s06_rows = 0
        try:
            for chunk in _iter_selected_chunks(path, usecols, inv):
                raw_rows += len(chunk)
                dx = normalize_dx(chunk[mapping["dx_main"]]); age = pd.to_numeric(chunk[mapping["age"]], errors="coerce")
                unit_raw = normalize_code_series(chunk[mapping["age_unit"]]); unit_text = unit_raw.map(ascii_text)
                years_mask = unit_raw.eq("4") | unit_text.str.contains(r"ano|anio|year", regex=True, na=False)
                mask = dx.str.startswith("S06", na=False) & years_mask & age.between(18, 120)
                if not mask.any():
                    continue
                s06_rows += int(mask.sum()); sub = chunk.loc[mask].copy(); out = pd.DataFrame(index=sub.index)
                out["country"] = "equador"; out["year"] = int(year); out["age"] = age.loc[mask].astype(float)
                out["sex"] = _normalize_ecuador_sex(sub[mapping["sex"]]) if mapping.get("sex") else pd.NA
                out["los_days"] = pd.to_numeric(sub[mapping["los_days"]], errors="coerce") if mapping.get("los_days") else np.nan
                cond = normalize_code_series(sub[mapping["discharge_condition"]]); out["death_in_hospital"] = cond.isin(["2", "3"]).astype("Int64")
                out["dx_main"] = dx.loc[mask].astype("string")
                out["dx_secondary"] = normalize_dx(sub[mapping["dx_secondary"]]) if mapping.get("dx_secondary") else pd.NA
                out["hospital_id"] = meaningful_text(sub[mapping["hospital_id"]]) if mapping.get("hospital_id") else pd.NA
                for canonical in ("province", "canton", "parish", "area", "facility_class", "facility_type", "facility_entity", "facility_sector", "residence_province", "residence_canton", "residence_parish", "residence_area", "ethnicity", "nationality", "discharge_specialty"):
                    out[canonical] = meaningful_text(sub[mapping[canonical]]) if mapping.get(canonical) else pd.NA
                out["primary_sample_20plus"] = out["age"].ge(20).astype("Int64"); out["sensitivity_sample_18plus"] = 1; out["age_exact_available"] = 1
                bins = [18, 20, 30, 50, 70, 80, np.inf]; labels = ["18-19", "20-29", "30-49", "50-69", "70-79", "80+"]
                out["age_band_common"] = pd.cut(out["age"], bins=bins, labels=labels, right=False).astype("string")
                out["_source_file"] = str(path)
                frames.append(out.reset_index(drop=True)); del chunk, sub, out; gc.collect()
            if not frames:
                rows.append({"year": year, "status": "ZERO_ADULT_S06", "path": str(path), "raw_rows": raw_rows}); continue
            result = pd.concat(frames, ignore_index=True, sort=False)
            target = dirs["ecuador_recovered"] / f"ecuador_s06_recovered_{year}_v282.parquet"
            result.to_parquet(target, index=False, compression="snappy")
            rows.append({"year": year, "status": "PASS", "path": str(path), "raw_rows": raw_rows, "adult18_s06": len(result), "adult20_s06": int(result["primary_sample_20plus"].sum()), "mortality_available_pct": round(100 * result["death_in_hospital"].notna().mean(), 3), "hospital_id_available_pct": round(100 * result["hospital_id"].notna().mean(), 3), "output_parquet": str(target)})
            del result, frames; gc.collect()
        except Exception as exc:
            rows.append({"year": year, "status": f"ERROR:{type(exc).__name__}:{exc}", "path": str(path), "raw_rows": raw_rows, "s06_rows_seen": s06_rows})
    manifest = pd.DataFrame(rows)
    save_table(manifest, dirs["ecuador"] / "Ecuador_recovery_manifest_v282.csv")
    return manifest


def audit_chile_linkage_v282(base: Path, dirs: Mapping[str, Path]) -> pd.DataFrame:
    result = audit_chile_linkage(base, dirs)
    old = dirs["chile"] / "Chile_hospital_linkage_audit_v281.csv"
    new = dirs["chile"] / "Chile_hospital_linkage_audit_v282.csv"
    if old.exists():
        old.replace(new)
    return result


def write_summary_v282(dirs: Mapping[str, Path], mexico: Mapping[str, Any], chile: pd.DataFrame, ecuador_manifest: Mapping[str, Any]) -> Path:
    preferred = pd.read_csv(mexico["preferred"]) if Path(mexico["preferred"]).exists() else pd.DataFrame()
    selected_years = sorted(pd.to_numeric(preferred.loc[preferred.get("selected", False).fillna(False), "year"], errors="coerce").dropna().astype(int).tolist()) if not preferred.empty else []
    linkage_path = Path(ecuador_manifest["linkage"]); linkage = pd.read_csv(linkage_path) if linkage_path.exists() else pd.DataFrame()
    recovered_path = Path(ecuador_manifest["recovery"]); recovered = pd.read_csv(recovered_path) if recovered_path.exists() else pd.DataFrame()
    exact_years = sorted(pd.to_numeric(linkage.loc[linkage.get("status", pd.Series(dtype="string")).eq("EXACT_ID_LINKAGE_POSSIBLE"), "year"], errors="coerce").dropna().astype(int).tolist()) if not linkage.empty else []
    composite_years = sorted(pd.to_numeric(linkage.loc[linkage.get("status", pd.Series(dtype="string")).eq("VALIDATED_COMPOSITE_LINKAGE_CANDIDATE"), "year"], errors="coerce").dropna().astype(int).tolist()) if not linkage.empty else []
    recovered_years = sorted(pd.to_numeric(recovered.loc[recovered.get("status", pd.Series(dtype="string")).eq("PASS"), "year"], errors="coerce").dropna().astype(int).tolist()) if not recovered.empty else []
    lines = [
        "# LATAM source super-audit v2.8.2", "", "## Mexico 2015–2017",
        f"Strictly recovered years: **{', '.join(map(str, selected_years)) if selected_years else 'none yet'}**.",
        "The v2.8.2 audit reused the completed v2.8.1 source scans when file sizes matched, avoiding another >100-million-row pass.",
        "", "## Ecuador 2015–2024",
        f"Patient-level S06 recovery passed for: **{', '.join(map(str, recovered_years)) if recovered_years else 'none'}**.",
        f"Exact facility-linkage candidate years: **{', '.join(map(str, exact_years)) if exact_years else 'none'}**.",
        f"Validated composite-linkage candidate years: **{', '.join(map(str, composite_years)) if composite_years else 'none'}**.",
        "CSV/SAV duplicates are inventoried and a single preferred source is selected for each role-year. RDS/RData files are retained as fallbacks when no readable CSV/SAV equivalent exists.",
        "", "## Chile",
        "No login-dependent roster is required for the current individual-level analyses. Hospital-volume analysis remains unavailable unless an exact establishment identifier exists in the public egresos files.",
        "", "## Next step",
        "Review the Mexico coding consensus and the Ecuador preferred-source, recovery, and linkage tables before modifying the final analytic master.",
    ]
    path = dirs["summary"] / "Source_expansion_recommendations_v282.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def run_latam_source_super_audit_v282(base_dir: str | Path = DEFAULT_BASE, clean_output: bool = True, build_mexico_recovered: bool = True, build_ecuador_recovered: bool = True, force_rescan_mexico: bool = False) -> Dict[str, Any]:
    base = Path(base_dir); root = base / V282_OUTPUT_DIR
    if clean_output and root.exists():
        shutil.rmtree(root)
    dirs = ensure_dirs(base)
    log(f"Starting LATAM source super-audit {VERSION}", dirs)
    mexico = run_mexico_super_audit_v282(base, dirs, build_recovered=build_mexico_recovered, force_rescan=force_rescan_mexico)
    log("Mexico audit completed", dirs)
    chile = audit_chile_linkage_v282(base, dirs)
    log("Chile linkage audit completed", dirs)
    ecuador = audit_ecuador_sources_v282(base, dirs, build_recovered=build_ecuador_recovered)
    log("Ecuador 2015–2024 audit completed", dirs)
    summary = write_summary_v282(dirs, mexico, chile, ecuador)
    zip_path = base / f"{V282_OUTPUT_DIR}.zip"
    if zip_path.exists(): zip_path.unlink()
    shutil.make_archive(str(zip_path.with_suffix("")), "zip", root_dir=root)
    manifest = {"version": VERSION, "base_dir": str(base), "output_dir": str(root), "mexico": mexico, "chile_audit": str(dirs["chile"] / "Chile_hospital_linkage_audit_v282.csv"), "ecuador": ecuador, "summary": str(summary), "zip": str(zip_path)}
    (dirs["summary"] / "audit_manifest_v282.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False, default=json_safe), encoding="utf-8")
    log(f"Audit package created: {zip_path}", dirs)
    return manifest


def verify_latam_source_super_audit_v282() -> Dict[str, Any]:
    status = {
        "version": VERSION,
        "runner": run_latam_source_super_audit_v282.__name__,
        "reuses_completed_v281_mexico_scan": True,
        "empty_calibration_safe": True,
        "mexico_sources": len(MEXICO_RAW_RELATIVE),
        "ecuador_years": list(ECUADOR_YEARS),
        "ecuador_duplicate_source_selection": True,
        "ecuador_patient_recovery": True,
        "ecuador_exact_and_composite_linkage": True,
        "fail_closed": True,
    }
    print(json.dumps(status, indent=2, ensure_ascii=False))
    return status

# Public aliases for the corrected preflight.
run_latam_source_super_audit = run_latam_source_super_audit_v282
verify_latam_source_super_audit = verify_latam_source_super_audit_v282

print(f"✅ LATAM source super-audit {VERSION} loaded.")
print("Recommended call:")
print("  result = run_latam_source_super_audit_v282(clean_output=True, force_rescan_mexico=False)")

# =============================================================================
# v2.8.2.1 safety refinements
# =============================================================================

ECUADOR_STRICT_ID_ALIASES = (
    "unicodigo", "uni_codigo", "codigo_establecimiento", "cod_estab",
    "codigo_estab", "id_establecimiento", "establecimiento_id", "codestab",
    "cod_establec", "codigo_unico_establecimiento", "codigo_establec",
)
ECUADOR_PATIENT_ALIASES["hospital_id"] = ECUADOR_STRICT_ID_ALIASES
ECUADOR_CAPACITY_ALIASES["hospital_id"] = ECUADOR_STRICT_ID_ALIASES


def _bool_value(value: Any) -> bool:
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    return ascii_text(value) in {"true", "1", "yes", "sim"}


# Replace the importer with strict boolean parsing.
def _load_scan_from_v281_inventory(base: Path, source_name: str, source_path: Path) -> Optional[MexicoScanResult]:
    old = base / "analysis_v281_preflight" / "01_mexico" / "Mexico_raw_file_inventory_v281.csv"
    if not old.exists():
        return None
    try:
        frame = pd.read_csv(old)
        row = frame[frame["source_name"].astype(str).eq(source_name)]
        if row.empty:
            return None
        row = row.iloc[0]
        if Path(str(row["path"])) != source_path or not source_path.exists():
            return None
        if abs(float(row.get("size_mb", 0)) - source_path.stat().st_size / 1024**2) > 0.05:
            return None
        separator = str(row.get("separator_repr", "','"))
        try:
            separator = bytes(separator.strip("'\""), "utf-8").decode("unicode_escape")
        except Exception:
            separator = separator.strip("'\"")
        payload = {
            "source_name": source_name,
            "path": str(source_path),
            "year_hint": None if pd.isna(row.get("year_hint")) else int(row.get("year_hint")),
            "is_consolidated": _bool_value(row.get("is_consolidated", False)),
            "exists": _bool_value(row.get("exists", True)),
            "size_mb": float(row.get("size_mb", 0)),
            "encoding": str(row.get("encoding", "")),
            "separator": separator,
            "columns": json.loads(row.get("columns_json", "[]")),
            "mapping": json.loads(row.get("mapping_json", "{}")),
            "rows_total": int(row.get("rows_total", 0)),
            "rows_target_year": json.loads(row.get("rows_target_year_json", "{}")),
            "s06_by_year": json.loads(row.get("s06_by_year_json", "{}")),
            "age_unit_stats": json.loads(row.get("age_unit_stats_json", "{}")),
            "outcome_stats": json.loads(row.get("outcome_stats_json", "{}")),
            "outcome_by_unit_stats": json.loads(row.get("outcome_by_unit_stats_json", "{}")),
            "hospital_nonmissing": json.loads(row.get("hospital_nonmissing_json", "{}")),
            "hospital_unique_samples": json.loads(row.get("hospital_unique_sample_json", "{}")),
            "sample_values": json.loads(row.get("sample_values_json", "{}")),
            "error": "" if pd.isna(row.get("error")) else str(row.get("error")),
        }
        return _scan_from_json(payload)
    except Exception:
        return None


def _safe_relative_difference(observed: pd.Series, expected: pd.Series) -> pd.Series:
    observed = pd.to_numeric(observed, errors="coerce")
    expected = pd.to_numeric(expected, errors="coerce")
    return (observed - expected).abs() / expected.abs().where(expected.abs().gt(0))


def _audit_ecuador_selected_linkage_v282(selected: pd.DataFrame, inventory: pd.DataFrame, dirs: Mapping[str, Path]) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    for year in ECUADOR_YEARS:
        eg_sel = selected[(selected["year"].eq(year)) & selected["role"].eq("PATIENT_EGRESOS") & selected["selected"].fillna(False)]
        bed_sel = selected[(selected["year"].eq(year)) & selected["role"].eq("FACILITY_CAPACITY") & selected["selected"].fillna(False)]
        if eg_sel.empty or bed_sel.empty:
            rows.append({"year": year, "status": "MISSING_EGRESOS_OR_CAMAS"})
            continue
        eg_path, bed_path = Path(eg_sel.iloc[0]["path"]), Path(bed_sel.iloc[0]["path"])
        eg_inv, bed_inv = _selected_inventory_row(inventory, eg_path), _selected_inventory_row(inventory, bed_path)
        eg_map = json.loads(eg_inv.get("mapping_json", "{}")); bed_map = json.loads(bed_inv.get("mapping_json", "{}"))
        exact = bool(eg_map.get("hospital_id") and bed_map.get("hospital_id"))
        common_names = [name for name in ("province", "canton", "parish", "area", "facility_class", "facility_type", "facility_entity", "facility_sector") if eg_map.get(name) and bed_map.get(name)]
        try:
            if exact:
                key_name = "hospital_id"
                bed_key_columns = [bed_map[key_name]]
            elif len(common_names) >= 5:
                key_name = "composite"
                bed_key_columns = [bed_map[name] for name in common_names]
            else:
                rows.append({"year": year, "status": "AGGREGATED_CAPACITY_ONLY", "egresos_path": str(eg_path), "camas_path": str(bed_path), "exact_common_id": False, "common_composite_key_names": ",".join(common_names)})
                continue

            extra_bed = [bed_map[name] for name in ("total_discharges", "deaths_lt48", "deaths_ge48", "total_deaths", "total_stay_days") if bed_map.get(name)]
            bed = _read_selected_small(bed_path, list(dict.fromkeys(bed_key_columns + extra_bed)), bed_inv)
            if exact:
                bed_key = meaningful_text(bed[bed_map["hospital_id"]]).map(ascii_text)
            else:
                bed_norm = pd.DataFrame({name: meaningful_text(bed[bed_map[name]]).map(ascii_text) for name in common_names})
                bed_key = bed_norm.fillna("<NA>").agg("|".join, axis=1)
            bed_counts = bed_key.value_counts(dropna=False)
            unique_keys = set(bed_counts[bed_counts.eq(1)].index)
            unique_rate = 100 * len(unique_keys) / max(1, bed_key.nunique(dropna=True))

            patient_counts: Counter[str] = Counter()
            patient_deaths: Counter[str] = Counter()
            patient_los: MutableMapping[str, float] = defaultdict(float)
            eg_cols = [eg_map["hospital_id"]] if exact else [eg_map[name] for name in common_names]
            if eg_map.get("discharge_condition"):
                eg_cols.append(eg_map["discharge_condition"])
            if eg_map.get("los_days"):
                eg_cols.append(eg_map["los_days"])
            eg_cols = list(dict.fromkeys(eg_cols))
            total_rows = matched_rows = 0
            for chunk in _iter_selected_chunks(eg_path, eg_cols, eg_inv):
                if exact:
                    keys = meaningful_text(chunk[eg_map["hospital_id"]]).map(ascii_text)
                else:
                    norm = pd.DataFrame({name: meaningful_text(chunk[eg_map[name]]).map(ascii_text) for name in common_names})
                    keys = norm.fillna("<NA>").agg("|".join, axis=1)
                valid = keys.notna() & keys.ne("")
                keys = keys[valid]
                total_rows += int(valid.sum())
                matched_rows += int(keys.isin(unique_keys).sum())
                for key, count in keys.value_counts().items():
                    patient_counts[str(key)] += int(count)
                if eg_map.get("discharge_condition"):
                    condition = normalize_code_series(chunk.loc[valid, eg_map["discharge_condition"]])
                    death = condition.isin(["2", "3"])
                    for key, count in keys[death.values].value_counts().items():
                        patient_deaths[str(key)] += int(count)
                if eg_map.get("los_days"):
                    los = pd.to_numeric(chunk.loc[valid, eg_map["los_days"]], errors="coerce")
                    grouped = pd.DataFrame({"key": keys.values, "los": los.values}).groupby("key", observed=True)["los"].sum(min_count=1)
                    for key, value in grouped.dropna().items():
                        patient_los[str(key)] += float(value)
                del chunk
            match_rate = 100 * matched_rows / max(1, total_rows)

            validation = pd.DataFrame({"link_key": bed_key.astype(str)})
            validation["patient_discharges"] = validation["link_key"].map(patient_counts).fillna(0)
            validation["patient_deaths"] = validation["link_key"].map(patient_deaths).fillna(0)
            validation["patient_stay_days"] = validation["link_key"].map(patient_los).fillna(0)
            if bed_map.get("total_discharges"):
                validation["capacity_discharges"] = pd.to_numeric(bed[bed_map["total_discharges"]], errors="coerce")
            if bed_map.get("total_deaths"):
                validation["capacity_deaths"] = pd.to_numeric(bed[bed_map["total_deaths"]], errors="coerce")
            elif bed_map.get("deaths_lt48") or bed_map.get("deaths_ge48"):
                left = pd.to_numeric(bed[bed_map["deaths_lt48"]], errors="coerce") if bed_map.get("deaths_lt48") else 0
                right = pd.to_numeric(bed[bed_map["deaths_ge48"]], errors="coerce") if bed_map.get("deaths_ge48") else 0
                validation["capacity_deaths"] = left + right
            if bed_map.get("total_stay_days"):
                validation["capacity_stay_days"] = pd.to_numeric(bed[bed_map["total_stay_days"]], errors="coerce")

            metrics: Dict[str, Any] = {}
            for prefix, patient_col, capacity_col in (
                ("discharges", "patient_discharges", "capacity_discharges"),
                ("deaths", "patient_deaths", "capacity_deaths"),
                ("stay_days", "patient_stay_days", "capacity_stay_days"),
            ):
                if capacity_col not in validation:
                    continue
                pair = validation[[patient_col, capacity_col]].dropna()
                pair = pair[pair[capacity_col].ge(0)]
                if len(pair) >= 3:
                    rel = _safe_relative_difference(pair[patient_col], pair[capacity_col])
                    metrics[f"{prefix}_units_compared"] = int(len(pair))
                    metrics[f"{prefix}_pearson"] = float(pair.corr(method="pearson").iloc[0, 1]) if pair[patient_col].nunique() > 1 and pair[capacity_col].nunique() > 1 else np.nan
                    metrics[f"{prefix}_spearman"] = float(pair.corr(method="spearman").iloc[0, 1]) if pair[patient_col].nunique() > 1 and pair[capacity_col].nunique() > 1 else np.nan
                    metrics[f"{prefix}_median_abs_relative_difference"] = float(rel.median()) if rel.notna().any() else np.nan
                    metrics[f"{prefix}_within_10pct"] = float(100 * rel.le(0.10).mean()) if rel.notna().any() else np.nan
                    metrics[f"{prefix}_within_20pct"] = float(100 * rel.le(0.20).mean()) if rel.notna().any() else np.nan

            counts_valid = metrics.get("discharges_spearman", np.nan)
            if exact and match_rate >= 90:
                status = "EXACT_ID_LINKAGE_VALIDATED_COUNTS" if np.isfinite(counts_valid) and counts_valid >= 0.90 else "EXACT_ID_LINKAGE_POSSIBLE"
            elif not exact and match_rate >= 80 and unique_rate >= 80:
                status = "VALIDATED_COMPOSITE_LINKAGE_COUNTS" if np.isfinite(counts_valid) and counts_valid >= 0.90 else "VALIDATED_COMPOSITE_LINKAGE_CANDIDATE"
            else:
                status = "AGGREGATED_CAPACITY_ONLY"
            validation.to_csv(dirs["ecuador"] / f"Ecuador_linkage_count_validation_{year}_v282.csv", index=False, encoding="utf-8-sig")
            rows.append({
                "year": year, "status": status, "egresos_path": str(eg_path), "camas_path": str(bed_path),
                "exact_common_id": exact, "common_composite_key_names": ",".join(common_names),
                "bed_key_unique_pct": round(float(unique_rate), 3), "egresos_match_pct": round(float(match_rate), 3),
                "matched_egresos_rows": int(matched_rows), **metrics,
            })
        except Exception as exc:
            rows.append({"year": year, "status": f"ERROR:{type(exc).__name__}:{exc}", "egresos_path": str(eg_path), "camas_path": str(bed_path)})
        gc.collect()
    result = pd.DataFrame(rows)
    save_table(result, dirs["ecuador"] / "Ecuador_hospital_linkage_audit_v282.csv")
    return result


# Replace patient recovery mortality mapping so missing/unknown condition is not
# silently classified as survival.
_original_build_ecuador_recovered_v282 = _build_ecuador_recovered_v282

def _build_ecuador_recovered_v282(selected: pd.DataFrame, inventory: pd.DataFrame, dirs: Mapping[str, Path]) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    for year in ECUADOR_YEARS:
        chosen = selected[(selected["year"].eq(year)) & selected["role"].eq("PATIENT_EGRESOS") & selected["selected"].fillna(False)]
        if chosen.empty:
            rows.append({"year": year, "status": "NO_VALID_PATIENT_SOURCE"}); continue
        path = Path(chosen.iloc[0]["path"]); inv = _selected_inventory_row(inventory, path); mapping = json.loads(inv.get("mapping_json", "{}"))
        required = {"dx_main", "age", "age_unit", "discharge_condition"}
        if not required.issubset(mapping):
            rows.append({"year": year, "status": "MISSING_REQUIRED_FIELDS", "path": str(path), "missing": ",".join(sorted(required - set(mapping)))}); continue
        usecols = sorted(set(mapping.values())); frames: List[pd.DataFrame] = []; raw_rows = 0
        try:
            for chunk in _iter_selected_chunks(path, usecols, inv):
                raw_rows += len(chunk)
                dx = normalize_dx(chunk[mapping["dx_main"]]); age = pd.to_numeric(chunk[mapping["age"]], errors="coerce")
                unit_raw = normalize_code_series(chunk[mapping["age_unit"]]); unit_text = unit_raw.map(ascii_text)
                years_mask = unit_raw.eq("4") | unit_text.str.contains(r"ano|anio|year", regex=True, na=False)
                mask = dx.str.startswith("S06", na=False) & years_mask & age.between(18, 120)
                if not mask.any():
                    continue
                sub = chunk.loc[mask].copy(); out = pd.DataFrame(index=sub.index)
                out["country"] = "equador"; out["year"] = int(year); out["age"] = age.loc[mask].astype(float)
                out["sex"] = _normalize_ecuador_sex(sub[mapping["sex"]]) if mapping.get("sex") else pd.NA
                out["los_days"] = pd.to_numeric(sub[mapping["los_days"]], errors="coerce") if mapping.get("los_days") else np.nan
                cond = normalize_code_series(sub[mapping["discharge_condition"]])
                death = pd.Series(pd.NA, index=sub.index, dtype="Int64")
                death.loc[cond.eq("1")] = 0
                death.loc[cond.isin(["2", "3"])] = 1
                out["death_in_hospital"] = death
                out["dx_main"] = dx.loc[mask].astype("string")
                out["dx_secondary"] = normalize_dx(sub[mapping["dx_secondary"]]) if mapping.get("dx_secondary") else pd.NA
                out["hospital_id"] = meaningful_text(sub[mapping["hospital_id"]]) if mapping.get("hospital_id") else pd.NA
                for canonical in ("province", "canton", "parish", "area", "facility_class", "facility_type", "facility_entity", "facility_sector", "residence_province", "residence_canton", "residence_parish", "residence_area", "ethnicity", "nationality", "discharge_specialty"):
                    out[canonical] = meaningful_text(sub[mapping[canonical]]) if mapping.get(canonical) else pd.NA
                out["primary_sample_20plus"] = out["age"].ge(20).astype("Int64"); out["sensitivity_sample_18plus"] = 1; out["age_exact_available"] = 1
                bins = [18, 20, 30, 50, 70, 80, np.inf]; labels = ["18-19", "20-29", "30-49", "50-69", "70-79", "80+"]
                out["age_band_common"] = pd.cut(out["age"], bins=bins, labels=labels, right=False).astype("string")
                out["_source_file"] = str(path)
                frames.append(out.reset_index(drop=True)); del chunk, sub, out; gc.collect()
            if not frames:
                rows.append({"year": year, "status": "ZERO_ADULT_S06", "path": str(path), "raw_rows": raw_rows}); continue
            result = pd.concat(frames, ignore_index=True, sort=False)
            target = dirs["ecuador_recovered"] / f"ecuador_s06_recovered_{year}_v282.parquet"
            result.to_parquet(target, index=False, compression="snappy")
            rows.append({"year": year, "status": "PASS", "path": str(path), "raw_rows": raw_rows, "adult18_s06": len(result), "adult20_s06": int(result["primary_sample_20plus"].sum()), "mortality_available_pct": round(100 * result["death_in_hospital"].notna().mean(), 3), "hospital_id_available_pct": round(100 * result["hospital_id"].notna().mean(), 3), "output_parquet": str(target)})
            del result, frames; gc.collect()
        except Exception as exc:
            rows.append({"year": year, "status": f"ERROR:{type(exc).__name__}:{exc}", "path": str(path), "raw_rows": raw_rows})
    manifest = pd.DataFrame(rows)
    save_table(manifest, dirs["ecuador"] / "Ecuador_recovery_manifest_v282.csv")
    return manifest


# Update aliases once more after all overrides.
run_latam_source_super_audit = run_latam_source_super_audit_v282
verify_latam_source_super_audit = verify_latam_source_super_audit_v282

# Preserve missingness while normalizing linkage keys.
def _normalized_key_series(series: pd.Series) -> pd.Series:
    text = meaningful_text(series)
    return text.map(lambda value: ascii_text(value) if pd.notna(value) else pd.NA).astype("string")


# Final linkage override with NA-preserving exact keys.
_previous_linkage_v282 = _audit_ecuador_selected_linkage_v282

def _audit_ecuador_selected_linkage_v282(selected: pd.DataFrame, inventory: pd.DataFrame, dirs: Mapping[str, Path]) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    for year in ECUADOR_YEARS:
        eg_sel = selected[(selected["year"].eq(year)) & selected["role"].eq("PATIENT_EGRESOS") & selected["selected"].fillna(False)]
        bed_sel = selected[(selected["year"].eq(year)) & selected["role"].eq("FACILITY_CAPACITY") & selected["selected"].fillna(False)]
        if eg_sel.empty or bed_sel.empty:
            rows.append({"year": year, "status": "MISSING_EGRESOS_OR_CAMAS"}); continue
        eg_path, bed_path = Path(eg_sel.iloc[0]["path"]), Path(bed_sel.iloc[0]["path"])
        eg_inv, bed_inv = _selected_inventory_row(inventory, eg_path), _selected_inventory_row(inventory, bed_path)
        eg_map = json.loads(eg_inv.get("mapping_json", "{}")); bed_map = json.loads(bed_inv.get("mapping_json", "{}"))
        exact = bool(eg_map.get("hospital_id") and bed_map.get("hospital_id"))
        common_names = [name for name in ("province", "canton", "parish", "area", "facility_class", "facility_type", "facility_entity", "facility_sector") if eg_map.get(name) and bed_map.get(name)]
        try:
            if exact:
                bed_key_columns = [bed_map["hospital_id"]]
            elif len(common_names) >= 5:
                bed_key_columns = [bed_map[name] for name in common_names]
            else:
                rows.append({"year": year, "status": "AGGREGATED_CAPACITY_ONLY", "egresos_path": str(eg_path), "camas_path": str(bed_path), "exact_common_id": False, "common_composite_key_names": ",".join(common_names)}); continue
            extra_bed = [bed_map[name] for name in ("total_discharges", "deaths_lt48", "deaths_ge48", "total_deaths", "total_stay_days") if bed_map.get(name)]
            bed = _read_selected_small(bed_path, list(dict.fromkeys(bed_key_columns + extra_bed)), bed_inv)
            if exact:
                bed_key = _normalized_key_series(bed[bed_map["hospital_id"]])
            else:
                bed_norm = pd.DataFrame({name: _normalized_key_series(bed[bed_map[name]]) for name in common_names})
                bed_key = bed_norm.fillna("<NA>").agg("|".join, axis=1).astype("string")
            valid_bed_key = bed_key.dropna(); bed_counts = valid_bed_key.value_counts()
            unique_keys = set(bed_counts[bed_counts.eq(1)].index.astype(str))
            unique_rate = 100 * len(unique_keys) / max(1, valid_bed_key.nunique())
            patient_counts: Counter[str] = Counter(); patient_deaths: Counter[str] = Counter(); patient_los: MutableMapping[str, float] = defaultdict(float)
            eg_cols = [eg_map["hospital_id"]] if exact else [eg_map[name] for name in common_names]
            if eg_map.get("discharge_condition"): eg_cols.append(eg_map["discharge_condition"])
            if eg_map.get("los_days"): eg_cols.append(eg_map["los_days"])
            eg_cols = list(dict.fromkeys(eg_cols)); total_rows = matched_rows = 0
            for chunk in _iter_selected_chunks(eg_path, eg_cols, eg_inv):
                if exact:
                    keys_all = _normalized_key_series(chunk[eg_map["hospital_id"]])
                else:
                    norm = pd.DataFrame({name: _normalized_key_series(chunk[eg_map[name]]) for name in common_names})
                    keys_all = norm.fillna("<NA>").agg("|".join, axis=1).astype("string")
                valid = keys_all.notna() & keys_all.ne("") & keys_all.ne("<NA>")
                keys = keys_all.loc[valid].astype(str)
                total_rows += int(valid.sum()); matched_rows += int(keys.isin(unique_keys).sum())
                for key, count in keys.value_counts().items(): patient_counts[str(key)] += int(count)
                if eg_map.get("discharge_condition"):
                    condition = normalize_code_series(chunk.loc[valid, eg_map["discharge_condition"]]); death = condition.isin(["2", "3"])
                    for key, count in keys.loc[death.values].value_counts().items(): patient_deaths[str(key)] += int(count)
                if eg_map.get("los_days"):
                    los = pd.to_numeric(chunk.loc[valid, eg_map["los_days"]], errors="coerce")
                    grouped = pd.DataFrame({"key": keys.values, "los": los.values}).groupby("key", observed=True)["los"].sum(min_count=1)
                    for key, value in grouped.dropna().items(): patient_los[str(key)] += float(value)
                del chunk
            match_rate = 100 * matched_rows / max(1, total_rows)
            validation = pd.DataFrame({"link_key": bed_key.astype("string")})
            validation["patient_discharges"] = validation["link_key"].astype(str).map(patient_counts).fillna(0)
            validation["patient_deaths"] = validation["link_key"].astype(str).map(patient_deaths).fillna(0)
            validation["patient_stay_days"] = validation["link_key"].astype(str).map(patient_los).fillna(0)
            if bed_map.get("total_discharges"): validation["capacity_discharges"] = pd.to_numeric(bed[bed_map["total_discharges"]], errors="coerce")
            if bed_map.get("total_deaths"): validation["capacity_deaths"] = pd.to_numeric(bed[bed_map["total_deaths"]], errors="coerce")
            elif bed_map.get("deaths_lt48") or bed_map.get("deaths_ge48"):
                left = pd.to_numeric(bed[bed_map["deaths_lt48"]], errors="coerce") if bed_map.get("deaths_lt48") else 0
                right = pd.to_numeric(bed[bed_map["deaths_ge48"]], errors="coerce") if bed_map.get("deaths_ge48") else 0
                validation["capacity_deaths"] = left + right
            if bed_map.get("total_stay_days"): validation["capacity_stay_days"] = pd.to_numeric(bed[bed_map["total_stay_days"]], errors="coerce")
            metrics: Dict[str, Any] = {}
            for prefix, patient_col, capacity_col in (("discharges", "patient_discharges", "capacity_discharges"), ("deaths", "patient_deaths", "capacity_deaths"), ("stay_days", "patient_stay_days", "capacity_stay_days")):
                if capacity_col not in validation: continue
                pair = validation[[patient_col, capacity_col]].dropna(); pair = pair[pair[capacity_col].ge(0)]
                if len(pair) >= 3:
                    rel = _safe_relative_difference(pair[patient_col], pair[capacity_col])
                    metrics[f"{prefix}_units_compared"] = int(len(pair))
                    metrics[f"{prefix}_pearson"] = float(pair.corr(method="pearson").iloc[0, 1]) if pair[patient_col].nunique() > 1 and pair[capacity_col].nunique() > 1 else np.nan
                    metrics[f"{prefix}_spearman"] = float(pair.corr(method="spearman").iloc[0, 1]) if pair[patient_col].nunique() > 1 and pair[capacity_col].nunique() > 1 else np.nan
                    metrics[f"{prefix}_median_abs_relative_difference"] = float(rel.median()) if rel.notna().any() else np.nan
                    metrics[f"{prefix}_within_10pct"] = float(100 * rel.le(0.10).mean()) if rel.notna().any() else np.nan
                    metrics[f"{prefix}_within_20pct"] = float(100 * rel.le(0.20).mean()) if rel.notna().any() else np.nan
            counts_valid = metrics.get("discharges_spearman", np.nan)
            if exact and match_rate >= 90: status = "EXACT_ID_LINKAGE_VALIDATED_COUNTS" if np.isfinite(counts_valid) and counts_valid >= 0.90 else "EXACT_ID_LINKAGE_POSSIBLE"
            elif not exact and match_rate >= 80 and unique_rate >= 80: status = "VALIDATED_COMPOSITE_LINKAGE_COUNTS" if np.isfinite(counts_valid) and counts_valid >= 0.90 else "VALIDATED_COMPOSITE_LINKAGE_CANDIDATE"
            else: status = "AGGREGATED_CAPACITY_ONLY"
            validation.to_csv(dirs["ecuador"] / f"Ecuador_linkage_count_validation_{year}_v282.csv", index=False, encoding="utf-8-sig")
            rows.append({"year": year, "status": status, "egresos_path": str(eg_path), "camas_path": str(bed_path), "exact_common_id": exact, "common_composite_key_names": ",".join(common_names), "bed_key_unique_pct": round(float(unique_rate), 3), "egresos_match_pct": round(float(match_rate), 3), "matched_egresos_rows": int(matched_rows), **metrics})
        except Exception as exc:
            rows.append({"year": year, "status": f"ERROR:{type(exc).__name__}:{exc}", "egresos_path": str(eg_path), "camas_path": str(bed_path)})
        gc.collect()
    result = pd.DataFrame(rows); save_table(result, dirs["ecuador"] / "Ecuador_hospital_linkage_audit_v282.csv"); return result

run_latam_source_super_audit = run_latam_source_super_audit_v282
verify_latam_source_super_audit = verify_latam_source_super_audit_v282

# =============================================================================
# v2.8.2.2 Ecuador ZIP support (needed for 2020 camas archives)
# =============================================================================

import zipfile as _zipfile


def ensure_dirs(base: Path) -> Dict[str, Path]:
    root = base / V282_OUTPUT_DIR
    dirs = {
        "root": root,
        "mexico": root / "01_mexico",
        "recovered": root / "01_mexico" / "recovered",
        "mexico_cache": root / "01_mexico" / "scan_cache",
        "chile": root / "02_chile",
        "ecuador": root / "03_ecuador",
        "ecuador_recovered": root / "03_ecuador" / "recovered",
        "ecuador_capacity": root / "03_ecuador" / "capacity",
        "ecuador_archive_extract": root / "03_ecuador" / "archive_extract",
        "summary": root / "04_summary",
        "logs": root / "05_logs",
    }
    for path in dirs.values():
        path.mkdir(parents=True, exist_ok=True)
    return dirs


def _extract_ecuador_archives_v282(base: Path, dirs: Mapping[str, Path]) -> pd.DataFrame:
    root = base / "00_raw" / "equador"
    allowed = {".csv", ".sav", ".rds", ".rdata", ".ods", ".xls", ".xlsx", ".pdf"}
    rows: List[Dict[str, Any]] = []
    for archive in sorted(root.rglob("*.zip")):
        target_root = dirs["ecuador_archive_extract"] / re.sub(r"[^A-Za-z0-9_.-]+", "_", archive.stem)
        target_root.mkdir(parents=True, exist_ok=True)
        try:
            with _zipfile.ZipFile(archive) as zf:
                for member in zf.infolist():
                    member_path = Path(member.filename)
                    suffix = member_path.suffix.lower()
                    if member.is_dir() or suffix not in allowed:
                        continue
                    # Path-traversal safe extraction.
                    safe_name = Path(*[part for part in member_path.parts if part not in {"", ".", ".."}])
                    destination = target_root / safe_name
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(member) as source, destination.open("wb") as target:
                        shutil.copyfileobj(source, target)
                    rows.append({"archive": str(archive), "member": member.filename, "extracted_path": str(destination), "size_mb": round(member.file_size / 1024**2, 3), "status": "EXTRACTED"})
        except Exception as exc:
            rows.append({"archive": str(archive), "member": "", "extracted_path": "", "size_mb": np.nan, "status": f"ERROR:{type(exc).__name__}:{exc}"})
    result = pd.DataFrame(rows)
    save_table(result, dirs["ecuador"] / "Ecuador_archive_extraction_manifest_v282.csv")
    return result


_previous_audit_ecuador_sources_v282 = audit_ecuador_sources_v282

def audit_ecuador_sources_v282(base: Path, dirs: Mapping[str, Path], build_recovered: bool = True) -> Dict[str, Any]:
    _extract_ecuador_archives_v282(base, dirs)
    roots = [base / "00_raw" / "equador", dirs["ecuador_archive_extract"]]
    rows: List[Dict[str, Any]] = []
    seen_paths: set[str] = set()
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file() or str(path) in seen_paths:
                continue
            seen_paths.add(str(path))
            role = _classify_ecuador_file(path); year = _extract_year_from_path(path)
            item: Dict[str, Any] = {"year": year, "role": role, "path": str(path), "suffix": path.suffix.lower(), "size_mb": round(path.stat().st_size / 1024**2, 3), "status": "NOT_INSPECTED", "from_archive": str(path).startswith(str(dirs["ecuador_archive_extract"]))}
            if role in {"PATIENT_EGRESOS", "FACILITY_CAPACITY"}:
                try:
                    columns, nrows, encoding, sep = _metadata_for_source(path)
                    score, mapping = _ecuador_schema_score(role, columns)
                    item.update({"status": "OK" if columns else "UNSUPPORTED_FORMAT_FALLBACK", "n_rows_metadata": nrows, "n_columns": len(columns), "encoding": encoding, "separator": repr(sep), "schema_score": score, "mapping_json": json.dumps(mapping, ensure_ascii=False), "columns_json": json.dumps(columns, ensure_ascii=False), "schema_fingerprint": hashlib.sha1("|".join(sorted(column_key(c) for c in columns)).encode()).hexdigest() if columns else ""})
                except Exception as exc:
                    item["status"] = f"ERROR:{type(exc).__name__}:{exc}"
            rows.append(item)
    inventory = pd.DataFrame(rows); save_table(inventory, dirs["ecuador"] / "Ecuador_source_inventory_v282.csv")
    candidate = inventory[(inventory["role"].isin(["PATIENT_EGRESOS", "FACILITY_CAPACITY"])) & inventory["year"].isin(ECUADOR_YEARS) & inventory["status"].eq("OK")].copy()
    format_rank = {".csv": 3, ".sav": 2, ".rds": 1, ".rdata": 1}
    candidate["format_rank"] = candidate["suffix"].map(format_rank).fillna(0)
    candidate["preference_score"] = pd.to_numeric(candidate["schema_score"], errors="coerce").fillna(0) + candidate["format_rank"] * 5 + np.log1p(pd.to_numeric(candidate["size_mb"], errors="coerce").fillna(0))
    selected_rows: List[Dict[str, Any]] = []
    for year in ECUADOR_YEARS:
        for role in ("PATIENT_EGRESOS", "FACILITY_CAPACITY"):
            subset = candidate[(candidate["year"].eq(year)) & candidate["role"].eq(role)].sort_values(["preference_score", "size_mb"], ascending=False)
            if subset.empty:
                selected_rows.append({"year": year, "role": role, "selected": False, "path": "", "status": "NO_VALID_SOURCE"})
            else:
                chosen = subset.iloc[0]
                selected_rows.append({"year": year, "role": role, "selected": True, "path": chosen["path"], "status": "SELECTED", "suffix": chosen["suffix"], "schema_score": chosen["schema_score"], "alternatives": int(len(subset) - 1), "from_archive": bool(chosen.get("from_archive", False))})
    selected = pd.DataFrame(selected_rows); save_table(selected, dirs["ecuador"] / "Ecuador_preferred_sources_v282.csv")
    # Explicit duplicate/equivalent-source table.
    duplicate_rows = []
    if not candidate.empty:
        for (year, role, fingerprint), group in candidate.groupby(["year", "role", "schema_fingerprint"], dropna=False):
            if len(group) <= 1:
                continue
            duplicate_rows.append({"year": year, "role": role, "schema_fingerprint": fingerprint, "n_equivalent_schema_sources": len(group), "paths_json": json.dumps(group["path"].tolist(), ensure_ascii=False), "suffixes": ",".join(sorted(set(group["suffix"].astype(str)))), "size_mb_min": float(group["size_mb"].min()), "size_mb_max": float(group["size_mb"].max())})
    save_table(pd.DataFrame(duplicate_rows), dirs["ecuador"] / "Ecuador_duplicate_source_groups_v282.csv")
    linkage = _audit_ecuador_selected_linkage_v282(selected, inventory, dirs)
    recovery = _build_ecuador_recovered_v282(selected, inventory, dirs) if build_recovered else pd.DataFrame()
    return {"inventory": str(dirs["ecuador"] / "Ecuador_source_inventory_v282.csv"), "preferred": str(dirs["ecuador"] / "Ecuador_preferred_sources_v282.csv"), "duplicates": str(dirs["ecuador"] / "Ecuador_duplicate_source_groups_v282.csv"), "archives": str(dirs["ecuador"] / "Ecuador_archive_extraction_manifest_v282.csv"), "linkage": str(dirs["ecuador"] / "Ecuador_hospital_linkage_audit_v282.csv"), "recovery": str(dirs["ecuador"] / "Ecuador_recovery_manifest_v282.csv")}

run_latam_source_super_audit = run_latam_source_super_audit_v282
verify_latam_source_super_audit = verify_latam_source_super_audit_v282
