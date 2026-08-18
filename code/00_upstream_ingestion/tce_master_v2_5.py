# Commented out IPython magic to ensure Python compatibility.
# ============================================================
#  PIPELINE TCE MULTINACIONAL — PARTE 1 REVISADA
#  Blocos 0–4 | Versão 1.1 (revisão técnica)
#
#  CORREÇÕES APLICADAS NESTA PARTE:
#  • Bloco 4: conversão .dbc completamente reescrita
#    - pysus como método principal
#    - simpledbf como fallback correto para .dbf
#    - remoção do blast-dbf + pyreadstat.read_dta (incorreto)
#    - diagnóstico claro de falha de conversão
# ============================================================


# ╔══════════════════════════════════════════════════════════╗
# ║  BLOCO 0 — Instalações e Imports                        ║
# ╚══════════════════════════════════════════════════════════╝

"""
!pip install \
    pyarrow fastparquet openpyxl xlsxwriter \
    requests tqdm \
    statsmodels scipy scikit-learn \
    matplotlib seaborn \
    pyreadstat chardet simpledbf \
    datasus-dbc readdbc
"""

# %pip install -U pip setuptools wheel
# %pip install -U simpledbf datasus-dbc readdbc pyreaddbc
# %pip install -U beautifulsoup4 lxml html5lib odfpy pyxlsb

import sys
print(sys.executable)

# %pip install -U pip setuptools wheel
# %pip install -U simpledbf datasus-dbc readdbc pyreaddbc

import pkgutil

print("datasus_dbc?", pkgutil.find_loader("datasus_dbc") is not None)
print("readdbc?", pkgutil.find_loader("readdbc") is not None)
print("pyreaddbc?", pkgutil.find_loader("pyreaddbc") is not None)

try:
    import datasus_dbc
    print("datasus_dbc OK")
except Exception as e:
    print("datasus_dbc FAIL:", repr(e))

try:
    import readdbc
    print("readdbc OK")
except Exception as e:
    print("readdbc FAIL:", repr(e))

try:
    import pyreaddbc
    print("pyreaddbc OK")
except Exception as e:
    print("pyreaddbc FAIL:", repr(e))

import os
import sys
import json
import time
import hashlib
import logging
import warnings
import traceback
import zipfile
import shutil
import urllib.request
from io import BytesIO
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, List, Tuple, Any

import numpy as np
import pandas as pd
import requests
from tqdm import tqdm

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import seaborn as sns

import statsmodels.api as sm
import statsmodels.formula.api as smf
from scipy import stats

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=pd.errors.DtypeWarning)

print("✅  Imports concluídos.")








# ╔══════════════════════════════════════════════════════════╗
# ║  BLOCO 1 — Configuração Geral (CONFIG CENTRAL)          ║
# ╚══════════════════════════════════════════════════════════╝

CONFIG = {
    # ── Identificação ─────────────────────────────────────
    "project_name": "Projeto_TCE_Multinacional",

    # ── Janela temporal ───────────────────────────────────
    "study_years": list(range(2015, 2024)),

    # ── Países ativos ─────────────────────────────────────
    "countries": {
        "brasil":   True,
        "mexico":   True,
        "chile":    False,
        "equador":  False,
    },

    # ── Coorte ────────────────────────────────────────────
    "min_age": 18,
    "dx_pattern": r"^S06",

    # ── Crosswalk ─────────────────────────────────────────
    "proc_crosswalk_file":  "crosswalk_procedures.csv",
    # Confiança mínima para incluir na análise DC vs CRAN
    # Aceitos: HIGH, MODERATE. Excluídos: LOW, UNVERIFIED.
    "min_proc_confidence_for_dc_analysis": ["HIGH", "MODERATE"],

    # ── Análises habilitadas ───────────────────────────────
    "run_main_analysis":    True,
    "run_dc_subanalysis":   True,
    "run_sensitivity":      True,
    "run_metaanalysis":     True,


    # ── Estratégia multinacional ─────────────────────────
    # Coorte principal NÃO exige procedimento: permite incluir México/Chile/Equador
    "primary_requires_surgery": False,

    # Países que podem entrar em análise cirúrgica ampla se tiverem procedimento mapeado
    "surgical_analysis_countries": ["brasil", "mexico", "chile", "equador"],

    # Países que podem entrar em DC vs CRAN estrito
    # Brasil e México são os mais plausíveis inicialmente; Chile/Equador só entram após validação do crosswalk.
    "dc_cran_analysis_countries": ["brasil", "mexico"],

    # Volume usado na análise principal multinacional
    # "tbi" = volume anual de internações S06.x por hospital
    # "surgical" = volume anual de cirurgias cranianas por hospital
    "primary_volume_definition": "tbi",


    # ── Thresholds ────────────────────────────────────────
    "min_hospital_volume":  5,
    "low_volume_threshold": 10,
    "high_volume_threshold": 50,

    # ── Figuras ───────────────────────────────────────────
    "fig_dpi": 300,

    # ── Google Drive ──────────────────────────────────────
    "drive_base": "/content/drive/MyDrive",
}

CONFIG["base_dir"] = Path(CONFIG["drive_base"]) / CONFIG["project_name"]

print("✅  CONFIG carregado.")
print(f"    Base: {CONFIG['base_dir']}")
print(f"    Anos: {CONFIG['study_years'][0]}–{CONFIG['study_years'][-1]}")
print(f"    Países: {[k for k, v in CONFIG['countries'].items() if v]}")


# ╔══════════════════════════════════════════════════════════╗
# ║  BLOCO 2 — Montagem do Drive e Criação de Diretórios   ║
# ╚══════════════════════════════════════════════════════════╝

def mount_drive() -> bool:
    try:
        from google.colab import drive
        drive.mount("/content/drive", force_remount=False)
        print("✅  Google Drive montado.")
        return True
    except ImportError:
        print("⚠️  Ambiente não-Colab. Usando caminhos locais.")
        return False
    except Exception as exc:
        print(f"❌  Falha ao montar Drive: {exc}")
        return False


def create_directory_tree(base: Path) -> Dict[str, Path]:
    dirs = {
        "raw_br":       base / "00_raw" / "brasil",
        "raw_mx":       base / "00_raw" / "mexico",
        "raw_cl":       base / "00_raw" / "chile",
        "raw_ec":       base / "00_raw" / "equador",
        "intermediate": base / "01_intermediate",
        "harmonized":   base / "02_harmonized",
        "qc":           base / "03_qc",
        "tables":       base / "04_tables",
        "fig_main":     base / "05_figures_main",
        "fig_suppl":    base / "06_figures_supplement",
        "models":       base / "07_models",
        "logs":         base / "08_logs",
        "metadata":     base / "09_metadata",
        "manuscript":   base / "10_manuscript_support",
    }
    for path in dirs.values():
        path.mkdir(parents=True, exist_ok=True)
    print(f"✅  Árvore de diretórios criada: {base}")
    return dirs


mount_drive()
DIRS = create_directory_tree(CONFIG["base_dir"])




# ╔══════════════════════════════════════════════════════════╗
# ║  BLOCO 3 — Funções Utilitárias e Logging               ║
# ╚══════════════════════════════════════════════════════════╝

def setup_logger(log_dir: Path) -> logging.Logger:
    ts  = datetime.now().strftime("%Y%m%d_%H%M%S")
    log = log_dir / f"pipeline_{ts}.log"
    # --- INÍCIO DO NOVO TRECHO ---
    logger = logging.getLogger("TCE_Pipeline")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()
    logger.propagate = False
    fmt = logging.Formatter("%(asctime)s | %(levelname)-8s | %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S")
# --- FIM DO NOVO TRECHO ---
    fh = logging.FileHandler(log, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(ch)
    logger.info(f"Logger iniciado: {log}")
    return logger


LOG = setup_logger(DIRS["logs"])


def file_exists_ok(path: Path, min_bytes: int = 1024) -> bool:
    """Verifica existência e tamanho mínimo — evita redownload."""
    return path.exists() and path.stat().st_size >= min_bytes


def check_url_available(url: str, timeout: int = 10) -> bool:
    """Faz HEAD request para verificar disponibilidade da URL."""
    try:
        r = requests.head(url, timeout=timeout,
                          headers={"User-Agent": "Mozilla/5.0 (TCE-Pipeline/1.1)"})
        return r.status_code < 400
    except Exception:
        return False


import subprocess
from urllib.parse import urlparse, unquote

def _safe_filename_from_url(url: str, fallback: str = "download.bin") -> str:
    parsed = urlparse(url)
    name = Path(unquote(parsed.path)).name
    if not name or "." not in name:
        return fallback
    return name.split("?")[0]


def download_file(url: str, dest: Path, desc: str = "") -> bool:
    """
    Download robusto para portais governamentais:
    1) requests normal
    2) requests com verify=False se erro SSL
    3) curl -L -k como fallback final
    """
    if file_exists_ok(dest):
        LOG.info(f"[SKIP] Arquivo local já existe: {dest.name}")
        return True

    dest.parent.mkdir(parents=True, exist_ok=True)
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) TCE-Pipeline/1.1",
        "Accept": "*/*",
        "Connection": "keep-alive",
    }

    def _requests_try(verify: bool) -> bool:
        try:
            LOG.info(f"[HTTP] GET verify={verify}: {url}")
            with requests.get(
                url,
                headers=headers,
                stream=True,
                timeout=300,
                verify=verify,
                allow_redirects=True,
            ) as r:
                r.raise_for_status()
                total = int(r.headers.get("content-length", 0))
                with open(dest, "wb") as f, tqdm(
                    total=total, unit="B", unit_scale=True, desc=desc or dest.name
                ) as bar:
                    for chunk in r.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            f.write(chunk)
                            bar.update(len(chunk))

            if dest.exists() and dest.stat().st_size > 1024:
                LOG.info(f"[HTTP-OK] {dest.name} ({dest.stat().st_size/1e6:.1f} MB)")
                return True
        except Exception as exc:
            LOG.warning(f"[HTTP-FAIL verify={verify}] {url}: {exc}")

        if dest.exists() and dest.stat().st_size < 1024:
            dest.unlink()
        return False

    # 1) tentativa normal
    if _requests_try(verify=True):
        return True

    # 2) tentativa sem verificar SSL
    if _requests_try(verify=False):
        return True

    # 3) fallback curl
    try:
        LOG.info(f"[CURL] Tentando curl -L -k: {url}")
        cmd = [
            "curl",
            "-L",
            "-k",
            "--fail",
            "--retry", "3",
            "--connect-timeout", "30",
            "-A", headers["User-Agent"],
            "-o", str(dest),
            url,
        ]
        ret = subprocess.run(cmd, capture_output=True, text=True, timeout=None)
        if ret.returncode == 0 and dest.exists() and dest.stat().st_size > 1024:
            LOG.info(f"[CURL-OK] {dest.name} ({dest.stat().st_size/1e6:.1f} MB)")
            return True
        LOG.warning(f"[CURL-FAIL] {url}: {ret.stderr[:500]}")
    except Exception as exc:
        LOG.error(f"[CURL-ERROR] {url}: {exc}")

    if dest.exists() and dest.stat().st_size < 1024:
        dest.unlink()

    LOG.error(f"[DOWNLOAD-FAIL] {url}")
    return False


def download_ftp_file(url: str, dest: Path) -> bool:
    """Download FTP via urllib. Prioriza arquivo local."""
    if file_exists_ok(dest):
        LOG.info(f"[SKIP-FTP] {dest.name}")
        return True
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(url, str(dest))
        LOG.info(f"[FTP-OK] {dest.name} ({dest.stat().st_size/1e6:.1f} MB)")
        return True
    except Exception as exc:
        LOG.warning(f"[FTP-FAIL] {url}: {exc}")
        if dest.exists() and dest.stat().st_size < 1024:
            dest.unlink()
        return False


def extract_zip(zip_path: Path, dest_dir: Path) -> List[Path]:
    dest_dir.mkdir(parents=True, exist_ok=True)
    extracted = []
    with zipfile.ZipFile(zip_path, "r") as z:
        for member in z.infolist():
            target = dest_dir / member.filename
            if not target.exists():
                z.extract(member, dest_dir)
            extracted.append(dest_dir / member.filename)
    LOG.info(f"[ZIP] {len(extracted)} arquivo(s) extraído(s) de {zip_path.name}")
    return extracted


def save_parquet(df: pd.DataFrame, path: Path, desc: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False, engine="pyarrow", compression="snappy")
    LOG.info(f"[SAVE-PQ] {desc or path.name}: {len(df):,} linhas")


def save_csv_xlsx(df: pd.DataFrame, stem: Path, sheet: str = "Sheet1") -> None:
    csv_path  = stem.with_suffix(".csv")
    xlsx_path = stem.with_suffix(".xlsx")
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    with pd.ExcelWriter(xlsx_path, engine="openpyxl") as w:
        df.to_excel(w, sheet_name=sheet, index=False)
    LOG.info(f"[SAVE-TBL] {stem.name}: CSV + XLSX")


def quick_audit(df: pd.DataFrame, label: str) -> pd.DataFrame:
    n = len(df)
    audit = pd.DataFrame({
        "column":     df.columns,
        "dtype":      [str(df[c].dtype) for c in df.columns],
        "n_non_null": [df[c].notna().sum() for c in df.columns],
        "pct_null":   [(df[c].isna().sum() / n * 100).round(2) for c in df.columns],
        "n_unique":   [df[c].nunique() for c in df.columns],
    })
    LOG.info(
        f"[AUDIT] {label}: {n:,} linhas, {df.shape[1]} cols | "
        f"missing máx = {audit['pct_null'].max():.1f}%"
    )
    return audit


def manual_upload_instructions(country: str, year: int, dest_dir: Path) -> None:
    """Imprime instruções explícitas de upload manual."""
    LOG.warning(
        f"\n{'='*60}\n"
        f"  ⚠  UPLOAD MANUAL NECESSÁRIO\n"
        f"  País   : {country}\n"
        f"  Ano    : {year}\n"
        f"  Destino: {dest_dir}\n"
        f"  Instruções:\n"
        f"    1. Baixe o arquivo no portal oficial do país\n"
        f"    2. No Colab: from google.colab import files; files.upload()\n"
        f"    3. Mova: !mv <arquivo> {dest_dir}/\n"
        f"    4. Reexecute este bloco\n"
        f"{'='*60}\n"
    )


print("✅  Utilitários e logger prontos.")


# ╔══════════════════════════════════════════════════════════╗
# ║  BLOCO 4 — Ingestão Brasil (SIH/SUS + CNES)            ║
# ║  REVISADO: conversão .dbc completamente corrigida       ║
# ╚══════════════════════════════════════════════════════════╝
#
#  MÉTODO PRINCIPAL: pysus (biblioteca Python oficial para DATASUS)
#    → Converte .dbc → DataFrame diretamente via blast-dbf interno
#    → Recomendado e mantido pela comunidade de saúde pública BR
#
#  FALLBACK: simpledbf
#    → Aplicado SE pysus falhar
#    → pysus internamente converte .dbc → .dbf → DataFrame
#    → Se a conversão .dbc→.dbf existir no disco, simpledbf lê o .dbf
#    → simpledbf é a biblioteca correta para .dbf (NOT pyreadstat.read_dta)
#
#  DIAGNÓSTICO DE FALHA:
#    → Função retorna None com log detalhado se ambos falharem
#    → Arquivo problemático é registrado em log separado de falhas
#
#  NOTA: .dbc é formato proprietário da Blemaster Consultoria para DATASUS.
#  Não existe leitura nativa em Python sem conversão prévia.
# ──────────────────────────────────────────────────────────────

BRASIL_RAW_DIR = DIRS["raw_br"]
BRASIL_INTER   = DIRS["intermediate"] / "brasil"
BRASIL_INTER.mkdir(parents=True, exist_ok=True)

UF_LIST = [
    "AC","AL","AM","AP","BA","CE","DF","ES","GO",
    "MA","MG","MS","MT","PA","PB","PE","PI","PR",
    "RJ","RN","RO","RR","RS","SC","SE","SP","TO",
]

# Log de falhas de conversão .dbc
DBC_FAIL_LOG: List[str] = []


# ── Verificação e instalação de dependências ──────────────────

def ensure_pysus() -> bool:
    """
    Garante que pysus está instalado e importável.
    Retorna True se disponível.
    """
    try:
        import pysus  # noqa: F401
        LOG.info("[DBC] pysus disponível.")
        return True
    except ImportError:
        LOG.info("[DBC] pysus não encontrado — instalando...")
        ret = os.system("pip install -q pysus 2>/dev/null")
        try:
            import pysus  # noqa: F401
            LOG.info("[DBC] pysus instalado com sucesso.")
            return True
        except ImportError:
            LOG.warning("[DBC] pysus indisponível. Usando fallback simpledbf.")
            return False


def ensure_simpledbf() -> bool:
    """Garante que simpledbf está instalado."""
    try:
        import simpledbf  # noqa: F401
        return True
    except ImportError:
        os.system("pip install -q simpledbf 2>/dev/null")
        try:
            import simpledbf  # noqa: F401
            return True
        except ImportError:
            LOG.warning("[DBC] simpledbf também indisponível.")
            return False


PYSUS_AVAIL    = ensure_pysus()
SIMPLEDBF_AVAIL = ensure_simpledbf()


# ── Conversão .dbc — método principal (pysus) ────────────────

# --- INÍCIO DO NOVO TRECHO ---
def _convert_dbc_pysus(dbc_path: Path) -> Optional[pd.DataFrame]:
    try:
        from pysus.preprocessing.decoders import decompress
        csv_out = dbc_path.with_suffix(".csv")
        if not csv_out.exists():
            decompress(str(dbc_path), str(csv_out))
        if csv_out.exists() and csv_out.stat().st_size > 100:
            df = pd.read_csv(csv_out, encoding="latin-1", dtype=str, low_memory=False)
            LOG.info(f"[DBC-pysus] {dbc_path.name}: {len(df):,} linhas")
            return df
    except Exception as exc:
        LOG.debug(f"[DBC-pysus] {dbc_path.name}: {exc}")
    return None
# --- FIM DO NOVO TRECHO ---


# ── Conversão .dbc — fallback (simpledbf) ────────────────────

def _read_dbf_simpledbf(dbf_path: Path) -> Optional[pd.DataFrame]:
    """
    Lê arquivo .dbf com simpledbf (biblioteca correta para este formato).
    simpledbf é o fallback correto — NÃO use pyreadstat.read_dta para .dbf.
    """
    try:
        from simpledbf import Dbf5
        dbf = Dbf5(str(dbf_path), codec="latin-1")
        df  = dbf.to_dataframe()
        df  = df.astype(str)  # normalizar todos como str para consistência
        LOG.info(f"[DBC-simpledbf] {dbf_path.name}: {len(df):,} linhas")
        return df
    except Exception as exc:
        LOG.error(f"[DBC-simpledbf-FAIL] {dbf_path.name}: {exc}")
        return None


def _convert_dbc_blast_fallback(dbc_path: Path) -> Optional[pd.DataFrame]:
    """
    Fallback: chama blast-dbf via subprocess se disponível no sistema.
    Produz .dbf, depois lê com simpledbf.
    Este caminho é usado apenas se pysus não tiver blast-dbf embarcado.
    """
    dbf_path = dbc_path.with_suffix(".dbf")
    if not dbf_path.exists():
        # Tenta instalar blast-dbf no sistema (Linux/Colab)
        install_cmd = (
            "if ! command -v blast-dbf &>/dev/null; then "
            "wget -q -O /tmp/bdbf.tar.gz "
            "https://github.com/eaglebh/blast-dbf/releases/latest/download/blast-dbf-linux-amd64.tar.gz "
            "&& tar -xzf /tmp/bdbf.tar.gz -C /usr/local/bin/ --wildcards 'blast-dbf' 2>/dev/null; "
            "fi"
        )
        os.system(install_cmd)
        ret = os.system(f"blast-dbf {dbc_path} {dbf_path} > /dev/null 2>&1")
        if ret != 0 or not dbf_path.exists():
            LOG.warning(f"[DBC-blast] blast-dbf falhou para {dbc_path.name}")
            return None

    return _read_dbf_simpledbf(dbf_path)


def _preflight_dbc_backend(raw_dir: Path) -> str:
    local_csv = list(raw_dir.glob("*.csv")) + list(raw_dir.glob("*.CSV"))
    local_dbf = list(raw_dir.glob("*.dbf")) + list(raw_dir.glob("*.DBF"))

    try:
        import datasus_dbc  # noqa: F401
        LOG.info("[PRE-FLIGHT] Backend ativo: datasus_dbc")
        return "datasus_dbc"
    except Exception as exc:
        LOG.warning(f"[PRE-FLIGHT] datasus_dbc indisponível: {exc}")

    try:
        import readdbc  # noqa: F401
        LOG.info("[PRE-FLIGHT] Backend ativo: readdbc")
        return "readdbc"
    except Exception as exc:
        LOG.warning(f"[PRE-FLIGHT] readdbc indisponível: {exc}")

    try:
        import pyreaddbc  # noqa: F401
        LOG.info("[PRE-FLIGHT] Backend ativo: pyreaddbc")
        return "pyreaddbc"
    except Exception as exc:
        LOG.warning(f"[PRE-FLIGHT] pyreaddbc indisponível: {exc}")

    if local_csv or local_dbf:
        LOG.info("[PRE-FLIGHT] Sem pacote conversor, mas há arquivos locais convertidos. Backend = local_only")
        return "local_only"

    LOG.warning("[PRE-FLIGHT] Sem conversor ativo.")
    return "none"

_DBC_BACKEND = "unknown"

def dbc_to_dataframe(dbc_path: Path) -> Optional[pd.DataFrame]:
    global _DBC_BACKEND
    if _DBC_BACKEND == "unknown":
        _DBC_BACKEND = _preflight_dbc_backend(dbc_path.parent)

    csv_cache = dbc_path.with_suffix(".csv")
    if csv_cache.exists() and csv_cache.stat().st_size > 100:
        try:
            df = pd.read_csv(csv_cache, encoding="latin-1", dtype=str, low_memory=False)
            LOG.info(f"[DBC-CACHE] {dbc_path.name}: {len(df):,} linhas")
            return df
        except Exception:
            pass

    dbf_cache = dbc_path.with_suffix(".dbf")
    if dbf_cache.exists() and dbf_cache.stat().st_size > 512:
        df = _read_dbf_simpledbf(dbf_cache)
        if df is not None:
            return df

    if _DBC_BACKEND == "local_only":
        LOG.warning(f"[DBC-local_only] Sem CSV/DBF disponível para {dbc_path.name}. Faça conversão manual.")
        DBC_FAIL_LOG.append(str(dbc_path))
        return None

    if _DBC_BACKEND == "datasus_dbc":
        try:
            import datasus_dbc
            dbf_path = dbc_path.with_suffix(".dbf")
            if not dbf_path.exists():
                datasus_dbc.decompress(str(dbc_path), str(dbf_path))
            if dbf_path.exists() and dbf_path.stat().st_size > 512:
                df = _read_dbf_simpledbf(dbf_path)
                if df is not None:
                    return df
        except Exception as exc:
            LOG.error(f"[DBC-datasus_dbc-FAIL] {dbc_path.name}: {exc}")

    if _DBC_BACKEND == "readdbc":
        df = _convert_dbc_readdbc(dbc_path)
        if df is not None:
            return df

    if _DBC_BACKEND == "pyreaddbc":
        df = _convert_dbc_pyreaddbc(dbc_path)
        if df is not None:
            return df

    LOG.error(f"[DBC-FAIL] Conversão impossível: {dbc_path.name}")
    DBC_FAIL_LOG.append(str(dbc_path))
    return None
# --- FIM DO NOVO TRECHO ---
def _convert_dbc_readdbc(dbc_path: Path) -> Optional[pd.DataFrame]:
    try:
        import readdbc
        csv_out = dbc_path.with_suffix(".csv")
        if not csv_out.exists():
            readdbc.dbc2csv(str(dbc_path), str(csv_out))
        if csv_out.exists() and csv_out.stat().st_size > 100:
            df = pd.read_csv(csv_out, encoding="latin-1", dtype=str, low_memory=False)
            LOG.info(f"[DBC-readdbc] {dbc_path.name}: {len(df):,} linhas")
            return df
    except Exception as exc:
        LOG.error(f"[DBC-readdbc-FAIL] {dbc_path.name}: {exc}")
    return None


def _convert_dbc_pyreaddbc(dbc_path: Path) -> Optional[pd.DataFrame]:
    try:
        import pyreaddbc
        df = pyreaddbc.read_dbc(str(dbc_path), encoding="iso-8859-1")
        if df is not None and len(df) > 0:
            df = df.astype(str)
            LOG.info(f"[DBC-pyreaddbc] {dbc_path.name}: {len(df):,} linhas")
            return df
    except Exception as exc:
        LOG.error(f"[DBC-pyreaddbc-FAIL] {dbc_path.name}: {exc}")
    return None

# ── URLs do DATASUS ───────────────────────────────────────────

def build_sih_url(uf: str, year: int, month: int) -> str:
    yy = str(year)[-2:]
    mm = str(month).zfill(2)
    return (
        f"ftp://ftp.datasus.gov.br/dissemin/publicos/SIHSUS/200801_/dados/"
        f"RD{uf}{yy}{mm}.dbc"
    )


def build_cnes_url(uf: str, year: int, month: int) -> str:
    yy = str(year)[-2:]
    mm = str(month).zfill(2)
    return (
        f"ftp://ftp.datasus.gov.br/dissemin/publicos/CNES/200508_/dados/ST/"
        f"ST{uf}{yy}{mm}.dbc"
    )


# ── Colunas de interesse SIH ──────────────────────────────────

SIH_COLS_INTEREST = [
    "UF_ZI", "ANO_CMPT", "MES_CMPT", "CGC_HOSP",
    "N_AIH", "MUNIC_RES", "NASC", "SEXO",
    "UTI_MES_TO", "UTI_INT_TO",
    "QT_DIARIAS", "DIAS_PERM", "MORTE", "NACIONAL",
    "PROC_REA", "PROC_SOLIC",
    "DIAG_PRINC", "DIAG_SECUN",
    "VALOR_TOT", "VAL_UTI",
    "CNES", "MUNIC_MOV", "IDADE", "COD_IDADE",
    "COMPLEX", "FINANC", "REGCT",
]

CNES_COLS_INTEREST = [
    "CNES", "CODUFMUN", "REGSAUDE", "TP_UNID",
    "NIV_HIER", "TURNO_AT", "CLIENTEL",
    "QT_EXIST", "QT_CONTR", "QT_SUS",
]


# ── Ingestão SIH ─────────────────────────────────────────────

def ingest_sih_brasil(
    years: List[int],
    ufs: List[str],
    raw_dir: Path,
    inter_dir: Path,
    months: List[int] = list(range(1, 13)),
) -> Optional[pd.DataFrame]:
    """
    Baixa e converte arquivos SIH-RD do FTP DATASUS.
    Filtra preliminarmente para S06.x.
    Salva parquet intermediário por UF/ano com checkpoint.
    """
    LOG.info("═" * 60)
    LOG.info("INGESTÃO SIH-BRASIL")
    LOG.info("═" * 60)

    all_dfs      = []
    download_log = []

    for year in years:
        for uf in ufs:
            # Checkpoint por UF/ano
            pq_path = inter_dir / f"SIH_{uf}_{year}.parquet"
            if file_exists_ok(pq_path):
                LOG.info(f"[SKIP-PQ] SIH {uf} {year} já processado.")
                all_dfs.append(pd.read_parquet(pq_path))
                continue

            uf_dfs = []
            for month in months:
                url  = build_sih_url(uf, year, month)
                dest = raw_dir / f"RD{uf}{str(year)[-2:]}{str(month).zfill(2)}.dbc"

                ok = download_ftp_file(url, dest)
                download_log.append({
                    "country": "brasil", "source": "SIH",
                    "uf": uf, "year": year, "month": month,
                    "filename": dest.name, "download_ok": ok,
                })

                if not ok or not file_exists_ok(dest):
                    continue

                df_raw = dbc_to_dataframe(dest)
                if df_raw is None:
                    continue

                cols_ok = [c for c in SIH_COLS_INTEREST if c in df_raw.columns]
                df_raw  = df_raw[cols_ok].copy()
                uf_dfs.append(df_raw)

            if uf_dfs:
                df_uf = pd.concat(uf_dfs, ignore_index=True)
                if "DIAG_PRINC" in df_uf.columns:
                    mask  = df_uf["DIAG_PRINC"].str.startswith("S06", na=False)
                    df_uf = df_uf[mask].copy()
                save_parquet(df_uf, pq_path, f"SIH {uf} {year}")
                all_dfs.append(df_uf)

    # Salvar log de download
    pd.DataFrame(download_log).to_csv(
        DIRS["logs"] / "brasil_sih_download_log.csv", index=False
    )

    # Relatório de falhas .dbc
    if DBC_FAIL_LOG:
        fail_path = DIRS["logs"] / "dbc_conversion_failures.txt"
        with open(fail_path, "w") as f:
            f.write("\n".join(DBC_FAIL_LOG))
        LOG.warning(f"[DBC] {len(DBC_FAIL_LOG)} falhas de conversão → {fail_path}")

    if not all_dfs:
        LOG.error("[BR] Nenhum dado SIH carregado.")
        return None

    df_brasil = pd.concat(all_dfs, ignore_index=True)
    save_parquet(df_brasil, inter_dir / "SIH_brasil_all.parquet", "SIH Brasil consolidado")
    LOG.info(f"SIH Brasil: {len(df_brasil):,} registros (S06.x)")
    return df_brasil


def ingest_cnes_brasil(
    years: List[int],
    ufs: List[str],
    raw_dir: Path,
    inter_dir: Path,
) -> Optional[pd.DataFrame]:
    """
    Baixa CNES-ST (dezembro de cada ano) para características hospitalares.
    """
    LOG.info("INGESTÃO CNES-ST-BRASIL")
    all_dfs = []

    for year in years:
        pq_path = inter_dir / f"CNES_{year}.parquet"
        if file_exists_ok(pq_path):
            all_dfs.append(pd.read_parquet(pq_path))
            continue

        uf_dfs = []
        for uf in ufs:
            url  = build_cnes_url(uf, year, 12)
            dest = raw_dir / f"ST{uf}{str(year)[-2:]}12.dbc"
            ok   = download_ftp_file(url, dest)
            if not ok or not file_exists_ok(dest):
                continue
            df_raw = dbc_to_dataframe(dest)
            if df_raw is None:
                continue
            cols_ok = [c for c in CNES_COLS_INTEREST if c in df_raw.columns]
            df_raw  = df_raw[cols_ok].copy()
            df_raw["year"] = year
            uf_dfs.append(df_raw)

        if uf_dfs:
            df_yr = pd.concat(uf_dfs, ignore_index=True)
            save_parquet(df_yr, pq_path, f"CNES {year}")
            all_dfs.append(df_yr)

    if not all_dfs:
        LOG.warning("[CNES] Nenhum dado CNES carregado.")
        return None

    df_cnes = pd.concat(all_dfs, ignore_index=True)
    save_parquet(df_cnes, inter_dir / "CNES_brasil_all.parquet", "CNES Brasil")
    return df_cnes


# ── Limpeza e padronização Brasil ─────────────────────────────

# --- INÍCIO DO NOVO TRECHO ---
def collapse_sih_to_aih(df: pd.DataFrame) -> pd.DataFrame:
    if "N_AIH" not in df.columns:
        return df

    df = df.copy()

    def first_valid(s):
        s = s.dropna()
        return s.iloc[0] if len(s) else pd.NA

    def join_unique_codes(s):
        vals = [str(x).strip() for x in s.dropna() if str(x).strip() not in ["", "nan", "None", "<NA>"]]
        vals = sorted(set(vals))
        return "|".join(vals) if vals else pd.NA

    agg_map = {
        "CNES": first_valid,
        "ANO_CMPT": first_valid,
        "MES_CMPT": first_valid,
        "MUNIC_MOV": first_valid,
        "MUNIC_RES": first_valid,
        "DIAG_PRINC": first_valid,
        "DIAG_SECUN": first_valid,
        "IDADE": first_valid,
        "COD_IDADE": first_valid,
        "SEXO": first_valid,
        "DIAS_PERM": "max",
        "MORTE": "max",
        "UTI_MES_TO": "max",
        "UTI_INT_TO": "max",
        "VALOR_TOT": "max",
        "REGCT": first_valid,
        "PROC_REA": join_unique_codes,
        "PROC_SOLIC": join_unique_codes,
    }

    usable = {k: v for k, v in agg_map.items() if k in df.columns}

    df_non_missing = df[df["N_AIH"].notna()].copy()
    df_missing = df[df["N_AIH"].isna()].copy()

    out_non_missing = (
        df_non_missing.groupby("N_AIH", dropna=False)
        .agg(usable)
        .reset_index()
        if not df_non_missing.empty else pd.DataFrame(columns=["N_AIH"] + list(usable.keys()))
    )

    if not df_missing.empty:
        df_missing = df_missing.reset_index(drop=True)
        df_missing["N_AIH"] = [f"MISSING_AIH_{i}" for i in range(len(df_missing))]
        out_missing = df_missing[["N_AIH"] + [c for c in usable.keys() if c in df_missing.columns]].copy()
        out = pd.concat([out_non_missing, out_missing], ignore_index=True)
    else:
        out = out_non_missing

    LOG.info(f"[BR] Colapso AIH: {len(df):,} linhas → {len(out):,} episódios")
    return out
# --- FIM DO NOVO TRECHO ---

def clean_standardize_brasil(
    df_sih: pd.DataFrame,
    df_cnes: Optional[pd.DataFrame],
) -> pd.DataFrame:
    """Limpeza e padronização do SIH. Retorna formato pré-harmonização."""
    LOG.info("Limpeza e padronização Brasil...")
    df = df_sih.copy()

    # Aplica o colapso por AIH garantindo procedimentos múltiplos na mesma string
    df = collapse_sih_to_aih(df)

    # Tratamento de Idade: Não anual vira NA, não zero
    if "IDADE" in df.columns and "COD_IDADE" in df.columns:
        idade_num = pd.to_numeric(df["IDADE"], errors="coerce")
        cod_idade = df["COD_IDADE"].astype(str).str.strip()
        df["age"] = pd.Series(pd.NA, index=df.index, dtype="Int64")
        mask_anos = cod_idade == "4"
        df.loc[mask_anos, "age"] = idade_num.loc[mask_anos].astype("Int64")
    elif "IDADE" in df.columns:
        df["age"] = pd.to_numeric(df["IDADE"], errors="coerce").astype("Int64")

    if "SEXO" in df.columns:
        df["sex"] = df["SEXO"].map({"1": "M", "3": "F", "0": "unknown"})
    if "DIAS_PERM" in df.columns:
        df["los_days"] = pd.to_numeric(df["DIAS_PERM"], errors="coerce").astype("Int64")
    if "MORTE" in df.columns:
        df["death_in_hospital"] = pd.to_numeric(df["MORTE"], errors="coerce").astype("Int64")

    # Soma real de dias de UTI
    uti_mes = pd.to_numeric(df["UTI_MES_TO"], errors="coerce") if "UTI_MES_TO" in df.columns else pd.Series(0, index=df.index)
    uti_int = pd.to_numeric(df["UTI_INT_TO"], errors="coerce") if "UTI_INT_TO" in df.columns else pd.Series(0, index=df.index)
    uti_total = uti_mes.fillna(0) + uti_int.fillna(0)
    df["icu_days"] = uti_total.astype("Int64")
    df["icu_any"] = (uti_total > 0).astype("Int64")
    if "VALOR_TOT" in df.columns:
        df["cost_local_currency"] = pd.to_numeric(df["VALOR_TOT"], errors="coerce")
    if "ANO_CMPT" in df.columns:
        df["year"] = pd.to_numeric(df["ANO_CMPT"], errors="coerce").astype("Int64")
    if "MES_CMPT" in df.columns:
        df["month"] = pd.to_numeric(df["MES_CMPT"], errors="coerce").astype("Int64")

    # Ponto 5: Evitar contaminação por "BR_nan"
    if "CNES" in df.columns:
        df["hospital_id"] = df["CNES"].apply(
            lambda x: f"BR_{str(x).strip().zfill(7)}" if pd.notna(x) and str(x).strip() not in ["", "nan", "None"] else pd.NA
        )
    else:
        df["hospital_id"] = pd.NA
# --- FIM DO NOVO TRECHO ---

    if "MUNIC_MOV" in df.columns:
        df["hospital_region"]  = df["MUNIC_MOV"].astype(str).str[:2]
    if "MUNIC_RES" in df.columns:
        df["residence_region"] = df["MUNIC_RES"].astype(str).str[:2]

    if "DIAG_PRINC" in df.columns:
        df["dx_main"]      = df["DIAG_PRINC"].str.strip().str.upper()
    if "DIAG_SECUN" in df.columns:
        df["dx_secondary"] = df["DIAG_SECUN"].str.strip().str.upper()

    if "PROC_REA" in df.columns:
        df["procedure_code_raw"] = df["PROC_REA"].str.strip()
    elif "PROC_SOLIC" in df.columns:
        df["procedure_code_raw"] = df["PROC_SOLIC"].str.strip()

    # --- INÍCIO DO NOVO TRECHO ---
    df["country"] = "brasil"
    df["source"]  = "SIH-SUS"

    if "REGCT" in df.columns:
        df["urgent_admission"] = (df["REGCT"] == "05").astype("Int64")
    else:
        df["urgent_admission"] = pd.NA

    # Merge CNES
# --- FIM DO NOVO TRECHO ---

    # Merge CNES
    if df_cnes is not None and "CNES" in df_cnes.columns:
        cnes_slim = df_cnes[["CNES", "year", "TP_UNID", "NIV_HIER", "REGSAUDE"]].copy()
        cnes_slim["hospital_id"] = "BR_" + cnes_slim["CNES"].astype(str).str.strip().str.zfill(7)
        cnes_slim = cnes_slim.drop(columns=["CNES"])
        df = df.merge(cnes_slim, on=["hospital_id", "year"], how="left")

    before = len(df)
    df     = df[df["age"] >= CONFIG["min_age"]].copy()
    LOG.info(f"[BR] >=18 anos: {before:,} → {len(df):,}")

    before = len(df)
    df     = df[df["dx_main"].str.startswith("S06", na=False)].copy()
    LOG.info(f"[BR] S06.x: {before:,} → {len(df):,}")

    return df


# --- INÍCIO DO NOVO TRECHO ---
def run_brasil_ingestion(config: dict, dirs: dict) -> Optional[pd.DataFrame]:
    global _DBC_BACKEND

    if not config["countries"]["brasil"]:
        return None

    inter = dirs["intermediate"] / "brasil"
    inter.mkdir(parents=True, exist_ok=True)

    ck = inter / "brasil_clean.parquet"
    if file_exists_ok(ck):
        LOG.info(f"[CHECKPOINT-BR] {ck}")
        return pd.read_parquet(ck)

    if _DBC_BACKEND in [None, "unknown", "none"]:
        _DBC_BACKEND = _preflight_dbc_backend(dirs["raw_br"])

    if _DBC_BACKEND == "none":
        raise RuntimeError(
            "Nenhum backend funcional para .dbc. "
            "Verifique a instalação de datasus-dbc, readdbc ou pyreaddbc no bloco inicial."
        )

    LOG.info(f"[BR] Backend em uso: {_DBC_BACKEND}")

    df_sih  = ingest_sih_brasil(config["study_years"], UF_LIST, dirs["raw_br"], inter)
    df_cnes = ingest_cnes_brasil(config["study_years"], UF_LIST, dirs["raw_br"], inter)

    if df_sih is None:
        LOG.error("Ingestão Brasil falhou. Sem dados SIH disponíveis.")
        return None

    df_clean = clean_standardize_brasil(df_sih, df_cnes)
    save_parquet(df_clean, ck, "Brasil limpo")
    save_csv_xlsx(quick_audit(df_clean, "Brasil"), dirs["qc"] / "audit_brasil")
    return df_clean
# --- FIM DO NOVO TRECHO ---




# df_brasil = run_brasil_ingestion(CONFIG, DIRS)
print("✅  Bloco 4 (Brasil REVISADO) pronto.")

# ============================================================
#  PIPELINE TCE MULTINACIONAL — PARTE 2 REVISADA
#  Blocos 5–9 | Versão 1.1
#
#  CORREÇÕES APLICADAS:
#  • Blocos 5–7: função genérica de download/fallback
#    - checagem HEAD antes de download
#    - prioriza arquivo local se já existente
#    - instrução explícita de upload manual se falhar
#    - logs mais informativos por país/ano
#  • Bloco 9: validação de tipos mais robusta
# ============================================================


# ╔══════════════════════════════════════════════════════════╗
# ║  FUNÇÃO GENÉRICA DE INGESTÃO COM FALLBACK LOCAL         ║
# ║  Reutilizada por México, Chile e Equador                ║
# ╚══════════════════════════════════════════════════════════╝

def _candidate_files_for_year(raw_dir: Path, year: int) -> List[Path]:
    exts = {
        ".csv", ".txt", ".tsv", ".sav", ".xlsx", ".xls", ".ods",
        ".dbf", ".parquet", ".zip", ".7z", ".rar"
    }

    candidates = []
    for p in raw_dir.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix.lower() not in exts:
            continue
        if year_matches_path(str(p), year):
            candidates.append(p)

    seen = set()
    out = []
    for p in candidates:
        if p not in seen:
            out.append(p)
            seen.add(p)
    return out


def _extract_zip_to_folder(zip_path: Path, country: str, year: int) -> List[Path]:
    """
    Extrai ZIP local ou baixado e retorna arquivos tabulares extraídos.
    """
    dest_dir = zip_path.parent / f"extracted_{year}_{zip_path.stem}"
    dest_dir.mkdir(parents=True, exist_ok=True)

    try:
        extracted = extract_zip(zip_path, dest_dir)
    except Exception as exc:
        LOG.error(f"[{country.upper()}] ZIP falhou {zip_path.name}: {exc}")
        return []

    valid_exts = {".csv", ".txt", ".tsv", ".sav", ".xlsx", ".xls", ".dbf", ".parquet"}
    return [p for p in dest_dir.rglob("*") if p.is_file() and p.suffix.lower() in valid_exts]


def ingest_country_year_generic(
    country: str,
    year: int,
    url: Optional[str],
    raw_dir: Path,
    zip_filename: str,
    csv_patterns: List[str] = ["*.csv", "*.CSV"],
    sav_support: bool = False,
) -> List[pd.DataFrame]:
    """
    Ingestão robusta por país/ano.

    Estratégia:
      1. Procura arquivos locais em 00_raw/<pais>/, inclusive subpastas
      2. Se achar ZIP local, extrai e lê
      3. Se não achar nada, tenta URL
      4. Se URL falhar, apenas orienta upload manual

    Isso é o certo para bases governamentais que mudam URL/estrutura.
    """
    dfs: List[pd.DataFrame] = []

    # 1) Local-first
    local_candidates = _candidate_files_for_year(raw_dir, year)

    if local_candidates:
        LOG.info(f"[{country.upper()}] {year}: {len(local_candidates)} arquivo(s) local(is) encontrado(s).")

        expanded_files = []
        for fpath in local_candidates:
            if fpath.suffix.lower() == ".zip":
                expanded_files.extend(_extract_zip_to_folder(fpath, country, year))
            else:
                expanded_files.append(fpath)

        for fpath in expanded_files:
            df = _read_tabular_file(fpath, country)
            if df is not None and len(df) > 0:
                df["_source_file"] = str(fpath)
                dfs.append(df)

        if dfs:
            return dfs

    # 2) Download, se houver URL
    if url is None:
        LOG.warning(f"[{country.upper()}] {year}: Sem URL configurada e sem arquivo local.")
        manual_upload_instructions(country, year, raw_dir)
        return dfs

    zip_path = raw_dir / zip_filename

    if not file_exists_ok(zip_path):
        LOG.info(f"[{country.upper()}] {year}: Tentando download direto...")
        ok = download_file(url, zip_path, desc=f"{country.title()} {year}")

        if not ok:
            LOG.error(f"[{country.upper()}] {year}: Download falhou. Usar upload/manual/local.")
            manual_upload_instructions(country, year, raw_dir)
            return dfs

    # 3) Extrair ZIP baixado
    if zip_path.exists():
        expanded_files = _extract_zip_to_folder(zip_path, country, year)
        for fpath in expanded_files:
            df = _read_tabular_file(fpath, country)
            if df is not None and len(df) > 0:
                df["_source_file"] = str(fpath)
                dfs.append(df)

    if not dfs:
        LOG.warning(f"[{country.upper()}] {year}: Nenhum dado extraído.")
        manual_upload_instructions(country, year, raw_dir)

    return dfs


def _read_tabular_file(fpath: Path, country: str) -> Optional[pd.DataFrame]:
    """
    Lê CSV/TXT/TSV/SAV/XLS/XLSX/DBF/Parquet.
    Mantém tudo como string para harmonização posterior.
    """
    suffix = fpath.suffix.lower()

    if suffix == ".parquet":
        try:
            df = pd.read_parquet(fpath)
            df = df.astype(str)
            LOG.info(f"[{country.upper()}] Parquet lido: {fpath.name} ({len(df):,} linhas)")
            return df
        except Exception as exc:
            LOG.error(f"[{country.upper()}] Parquet falhou {fpath.name}: {exc}")
            return None

    if suffix == ".sav":
        try:
            import pyreadstat
            df, _ = pyreadstat.read_sav(str(fpath), apply_value_formats=False)
            df = df.astype(str)
            LOG.info(f"[{country.upper()}] SAV lido: {fpath.name} ({len(df):,} linhas)")
            return df
        except Exception as exc:
            LOG.error(f"[{country.upper()}] SAV falhou {fpath.name}: {exc}")
            return None

    if suffix in [".xlsx", ".xls"]:
        try:
            df = pd.read_excel(fpath, dtype=str)
            LOG.info(f"[{country.upper()}] Excel lido: {fpath.name} ({len(df):,} linhas)")
            return df
        except Exception as exc:
            LOG.error(f"[{country.upper()}] Excel falhou {fpath.name}: {exc}")
            return None

    if suffix == ".dbf":
        try:
            from simpledbf import Dbf5
            dbf = Dbf5(str(fpath), codec="latin-1")
            df = dbf.to_dataframe().astype(str)
            LOG.info(f"[{country.upper()}] DBF lido: {fpath.name} ({len(df):,} linhas)")
            return df
        except Exception as exc:
            LOG.error(f"[{country.upper()}] DBF falhou {fpath.name}: {exc}")
            return None

    if suffix in [".csv", ".txt", ".tsv"]:
        separators = [";", ",", "\t", "|"]
        encodings = ["utf-8", "latin-1", "cp1252", "iso-8859-1"]

        for enc in encodings:
            for sep in separators:
                try:
                    df = pd.read_csv(
                        fpath,
                        encoding=enc,
                        dtype=str,
                        low_memory=False,
                        sep=sep,
                    )
                    if len(df.columns) > 1:
                        LOG.info(
                            f"[{country.upper()}] Texto lido: {fpath.name} "
                            f"({len(df):,} linhas, sep='{sep}', enc={enc})"
                        )
                        return df
                except Exception:
                    continue

    LOG.error(f"[{country.upper()}] Não foi possível ler: {fpath.name}")
    return None

from bs4 import BeautifulSoup
from urllib.parse import urljoin
import re

DATA_EXTENSIONS = (
    ".zip", ".csv", ".txt", ".tsv", ".xls", ".xlsx", ".ods",
    ".dbf", ".sav", ".parquet", ".7z", ".rar"
)

def http_get_text(url: str, timeout: int = 60) -> Optional[str]:
    headers = {
        "User-Agent": "Mozilla/5.0 TCE-Pipeline/1.1",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    for verify in [True, False]:
        try:
            r = requests.get(url, headers=headers, timeout=timeout, verify=verify)
            r.raise_for_status()
            r.encoding = r.apparent_encoding or "utf-8"
            return r.text
        except Exception as exc:
            LOG.debug(f"[GET-TEXT-FAIL verify={verify}] {url}: {exc}")
    return None


def looks_like_data_url(url: str) -> bool:
    u = url.lower().split("?")[0]
    return any(u.endswith(ext) for ext in DATA_EXTENSIONS)


def extract_data_urls_from_page(page_url: str) -> List[str]:
    html = http_get_text(page_url)
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    urls = []

    for tag in soup.find_all(["a", "link", "script"]):
        href = tag.get("href") or tag.get("src")
        if not href:
            continue
        full = urljoin(page_url, href)
        if looks_like_data_url(full):
            urls.append(full)

    # Também captura URLs em texto bruto
    raw_urls = re.findall(r'https?://[^\s"\'<>]+', html)
    for u in raw_urls:
        if looks_like_data_url(u):
            urls.append(u)

    # Deduplicar preservando ordem
    seen = set()
    out = []
    for u in urls:
        if u not in seen:
            out.append(u)
            seen.add(u)

    return out


def ckan_package_search(base_url: str, query: str, rows: int = 100) -> List[dict]:
    """
    Busca datasets em portal CKAN.
    Exemplos:
      Ecuador: https://www.datosabiertos.gob.ec
      Chile:   https://datos.gob.cl
    """
    api_url = base_url.rstrip("/") + "/api/3/action/package_search"
    try:
        r = requests.get(
            api_url,
            params={"q": query, "rows": rows},
            headers={"User-Agent": "Mozilla/5.0 TCE-Pipeline/1.1"},
            timeout=120,
            verify=False,
        )
        r.raise_for_status()
        js = r.json()
        if js.get("success"):
            return js.get("result", {}).get("results", [])
    except Exception as exc:
        LOG.warning(f"[CKAN-SEARCH-FAIL] {base_url} | {query}: {exc}")
    return []


def ckan_discover_resource_urls(
    base_url: str,
    query: str,
    years: List[int],
    allowed_formats: Tuple[str, ...] = ("CSV", "ZIP", "XLS", "XLSX", "ODS", "SAV", "DBF"),
) -> List[dict]:
    """
    Retorna recursos baixáveis encontrados no CKAN.
    """
    datasets = ckan_package_search(base_url, query, rows=200)
    resources = []

    for pkg in datasets:
        pkg_title = str(pkg.get("title", ""))
        pkg_name = str(pkg.get("name", ""))
        blob_pkg = f"{pkg_title} {pkg_name}".lower()

        for res in pkg.get("resources", []):
            fmt = str(res.get("format", "")).upper().strip()
            url = res.get("url")
            name = str(res.get("name", "") or res.get("description", ""))

            if not url:
                continue

            blob = f"{blob_pkg} {name} {url}".lower()

            year_hits = [y for y in years if str(y) in blob]
            if not year_hits:
                continue

            if allowed_formats and fmt and fmt not in allowed_formats:
                # se o formato vier vazio/errado, ainda deixa passar se a URL tiver extensão boa
                if not looks_like_data_url(url):
                    continue

            for y in year_hits:
                resources.append({
                    "year": y,
                    "url": url,
                    "name": name or _safe_filename_from_url(url, f"resource_{y}"),
                    "format": fmt,
                    "package": pkg_title,
                    "source": base_url,
                })

    # Deduplicar
    seen = set()
    out = []
    for r in resources:
        key = (r["year"], r["url"])
        if key not in seen:
            out.append(r)
            seen.add(key)

    return out


def download_resource_list(resources: List[dict], raw_dir: Path, country: str) -> int:
    """
    Baixa lista de recursos descobertos.
    """
    raw_dir.mkdir(parents=True, exist_ok=True)
    n_ok = 0

    for res in resources:
        year = res.get("year", "unknown")
        url = res["url"]
        fname = _safe_filename_from_url(url, fallback=f"{country}_{year}_{len(str(url))}.dat")

        # Prefixo para evitar colisão
        dest = raw_dir / f"{country}_{year}_{fname}"

        ok = download_file(url, dest, desc=f"{country.upper()} {year}")
        if ok:
            n_ok += 1

    LOG.info(f"[{country.upper()}] Recursos baixados com sucesso: {n_ok}/{len(resources)}")
    return n_ok


def year_matches_path(path_or_url: str, year: int) -> bool:
    """
    Detecta ano direto ou faixa tipo 2013_2020 / 2013-2020.
    """
    s = str(path_or_url).lower()
    if str(year) in s:
        return True

    ranges = re.findall(r"(20\d{2})\s*[_\-]\s*(20\d{2})", s)
    for a, b in ranges:
        if int(a) <= year <= int(b):
            return True

    return False



def _apply_s06_filter(df: pd.DataFrame, dx_col: str, country: str, year: int) -> pd.DataFrame:
    """Filtra por S06.x na coluna especificada, aceitando S060/S06.0/s060."""
    if dx_col not in df.columns:
        LOG.warning(f"[{country.upper()}] {year}: Coluna '{dx_col}' ausente — NÃO é seguro seguir sem filtro diagnóstico.")
        return df.iloc[0:0].copy()

    n0 = len(df)
    dx = (
        df[dx_col]
        .astype(str)
        .str.strip()
        .str.upper()
        .str.replace(".", "", regex=False)
    )

    mask = dx.str.startswith("S06", na=False)
    df = df[mask].copy()

    LOG.info(f"[{country.upper()}] {year}: S06.x: {n0:,} → {len(df):,}")
    return df

def auto_collect_mexico_sources(years: List[int], raw_dir: Path) -> None:
    """
    México:
    1) Baixa ZIP setorial 2013-2020 do repositório INSP/CONAHCYT.
    2) Tenta URLs abertas DGIS antigas/novas.
    3) Raspa páginas SINBA/Cubos Dinámicos para links baixáveis.
    """
    LOG.info("[MX-AUTO] Descoberta automática de fontes México...")

    # Fonte robusta para 2015-2020
    mx_bulk_sources = [
        {
            "label": "INSP_CONAHCYT_Egresos_sectorial_2013_2020",
            "url": "https://repositorio-salud.conacyt.mx/jspui/bitstream/1000/117/4/2%20Egresos_sectorial_2013_2020.zip",
            "years_min": 2013,
            "years_max": 2020,
        }
    ]

    for src in mx_bulk_sources:
        if any(src["years_min"] <= y <= src["years_max"] for y in years):
            dest = raw_dir / f"{src['label']}.zip"
            download_file(src["url"], dest, desc=src["label"])

    # Tentar padrões DGIS dados abertos por ano
    candidate_urls = []
    for y in years:
        candidate_urls.extend([
            f"http://www.dgis.salud.gob.mx/descargas/datosabiertos/egresos/sectorial_egresos_{y}.zip",
            f"https://www.dgis.salud.gob.mx/descargas/datosabiertos/egresos/sectorial_egresos_{y}.zip",
            f"http://www.dgis.salud.gob.mx/descargas/datosabiertos/egresos/egresos_{y}.zip",
            f"https://www.dgis.salud.gob.mx/descargas/datosabiertos/egresos/egresos_{y}.zip",
        ])

    # Páginas SINBA/cubos: podem conter links para recursos baixáveis
    sinba_pages = []
    for y in years:
        sinba_pages.extend([
            f"https://sinba.salud.gob.mx/cuboEGRESOS/egresos{y}/",
            f"https://sinba.salud.gob.mx/cuboEGRESOS/egresos{y}",
            f"https://sinba.salud.gob.mx/cuboEGRESOS/egresosProcedimientos{y}/",
            f"https://sinba.salud.gob.mx/cuboEGRESOS/egresosProcedimientos{y}",
            f"https://sinba.salud.gob.mx/cuboEGRESOS/egresosProductos{y}/",
            f"https://sinba.salud.gob.mx/cuboEGRESOS/egresosProductos{y}",
        ])

    # Link setorial por unidade médica 2018-2024
    sinba_pages.append("https://sinba.salud.gob.mx/cuboEGRESOS/InstitucionesPublicasMorbilidadUM2018_2022")
    sinba_pages.append("https://sinba.salud.gob.mx/cuboEGRESOS/InstitucionesPublicasMorbilidadUM2018_2024")

    for page in sinba_pages:
        urls = extract_data_urls_from_page(page)
        candidate_urls.extend(urls)

    # Baixar candidatos
    seen = set()
    resources = []
    for url in candidate_urls:
        if url in seen:
            continue
        seen.add(url)

        hit_years = [y for y in years if year_matches_path(url, y)]
        if not hit_years and "2013_2020" in url:
            hit_years = [y for y in years if 2013 <= y <= 2020]
        if not hit_years:
            continue

        for y in hit_years:
            resources.append({
                "year": y,
                "url": url,
                "name": _safe_filename_from_url(url, f"mexico_{y}.dat"),
                "format": Path(urlparse(url).path).suffix.upper().replace(".", ""),
                "package": "Mexico auto-discovered",
                "source": "DGIS/SINBA/INSP",
            })

    download_resource_list(resources, raw_dir, "mexico")


def auto_collect_equador_sources(years: List[int], raw_dir: Path) -> None:
    """
    Equador:
    Usa CKAN oficial datosabiertos.gob.ec.
    Esse deve funcionar melhor para 2020-2024.
    """
    LOG.info("[EC-AUTO] Descoberta automática de fontes Equador...")

    resources = ckan_discover_resource_urls(
        base_url="https://www.datosabiertos.gob.ec",
        query='"Registro Estadístico de Egresos Hospitalarios"',
        years=years,
        allowed_formats=("CSV", "ZIP", "XLS", "XLSX", "ODS", "SAV", "DBF"),
    )

    if not resources:
        # Busca mais ampla
        resources = ckan_discover_resource_urls(
            base_url="https://www.datosabiertos.gob.ec",
            query="Egresos Hospitalarios INEC",
            years=years,
            allowed_formats=("CSV", "ZIP", "XLS", "XLSX", "ODS", "SAV", "DBF"),
        )

    download_resource_list(resources, raw_dir, "equador")

    # Fallback: raspar página institucional do INEC
    pages = [
        "https://www.ecuadorencifras.gob.ec/camas-y-egresos-hospitalarios/",
        "https://www.datosabiertos.gob.ec/dataset/?res_format=CSV&tags=Egresos+Hospitalarios",
    ]
    candidate_urls = []
    for page in pages:
        candidate_urls.extend(extract_data_urls_from_page(page))

    resources2 = []
    for url in candidate_urls:
        hit_years = [y for y in years if year_matches_path(url, y)]
        for y in hit_years:
            resources2.append({
                "year": y,
                "url": url,
                "name": _safe_filename_from_url(url, f"equador_{y}.dat"),
                "format": Path(urlparse(url).path).suffix.upper().replace(".", ""),
                "package": "Ecuador auto-discovered",
                "source": "INEC/CKAN",
            })

    download_resource_list(resources2, raw_dir, "equador")


def auto_collect_chile_sources(years: List[int], raw_dir: Path) -> None:
    """
    Chile:
    Usa CKAN datos.gob.cl e raspa páginas DEIS.
    Observação: pode retornar produtos agregados, não microdados paciente-a-paciente.
    """
    LOG.info("[CL-AUTO] Descoberta automática de fontes Chile...")

    resources = ckan_discover_resource_urls(
        base_url="https://datos.gob.cl",
        query="Egresos Hospitalarios",
        years=years,
        allowed_formats=("CSV", "ZIP", "XLS", "XLSX", "ODS", "SAV", "DBF"),
    )

    download_resource_list(resources, raw_dir, "chile")

    pages = [
        "https://deis.minsal.cl/sistemas/",
        "https://deis.minsal.cl/",
        "https://datos.gob.cl/dataset?q=egresos+hospitalarios",
    ]

    candidate_urls = []
    for page in pages:
        candidate_urls.extend(extract_data_urls_from_page(page))

    resources2 = []
    for url in candidate_urls:
        hit_years = [y for y in years if year_matches_path(url, y)]
        for y in hit_years:
            resources2.append({
                "year": y,
                "url": url,
                "name": _safe_filename_from_url(url, f"chile_{y}.dat"),
                "format": Path(urlparse(url).path).suffix.upper().replace(".", ""),
                "package": "Chile auto-discovered",
                "source": "DEIS/CKAN",
            })

    download_resource_list(resources2, raw_dir, "chile")


def auto_collect_country_sources(country: str, years: List[int], raw_dir: Path) -> None:
    if country == "mexico":
        auto_collect_mexico_sources(years, raw_dir)
    elif country == "equador":
        auto_collect_equador_sources(years, raw_dir)
    elif country == "chile":
        auto_collect_chile_sources(years, raw_dir)
    else:
        LOG.info(f"[AUTO] Sem coletor automático específico para {country}.")

# ╔══════════════════════════════════════════════════════════╗
# ║  BLOCO 5 — México (SAEH/DGIS)                          ║
# ╚══════════════════════════════════════════════════════════╝

MEXICO_RAW_DIR = DIRS["raw_mx"]
MEXICO_INTER   = DIRS["intermediate"] / "mexico"
MEXICO_INTER.mkdir(parents=True, exist_ok=True)

# URLs SAEH — atualize se o DGIS modificar estrutura
# Verificar: https://www.dgis.salud.gob.mx/contenidos/basesdedatos/da_egresoshospitalarios_gobmx.html
MEXICO_URLS = {year: None for year in CONFIG["study_years"]}


import unicodedata
import re

def _norm_col(x: str) -> str:
    """
    Normaliza nomes de colunas para facilitar matching entre países.
    """
    x = str(x)
    x = unicodedata.normalize("NFKD", x).encode("ascii", "ignore").decode("ascii")
    x = x.upper().strip()
    x = re.sub(r"[^A-Z0-9]+", "_", x)
    x = re.sub(r"_+", "_", x).strip("_")
    return x


def standardize_columns_by_alias(df: pd.DataFrame, aliases: Dict[str, List[str]], country: str) -> pd.DataFrame:
    """
    Renomeia colunas usando listas de aliases.
    Não depende de nomes exatos.
    """
    df = df.copy()
    norm_to_original = {_norm_col(c): c for c in df.columns}

    rename_map = {}
    for canonical, possible_names in aliases.items():
        for name in possible_names:
            key = _norm_col(name)
            if key in norm_to_original:
                rename_map[norm_to_original[key]] = canonical
                break

    df = df.rename(columns=rename_map)

    missing_core = [c for c in ["dx_main", "age", "sex_raw", "los_days"] if c not in df.columns]
    if missing_core:
        LOG.warning(f"[{country.upper()}] Colunas essenciais não encontradas após aliases: {missing_core}")
        LOG.warning(f"[{country.upper()}] Colunas disponíveis: {list(df.columns)[:80]}")

    return df


def combine_procedure_columns(df: pd.DataFrame, proc_aliases: List[str]) -> pd.Series:
    """
    Combina múltiplas colunas de procedimento numa string separada por |.
    Isso é essencial para México/Chile/Equador, que podem ter vários campos de procedimento.
    """
    norm_to_original = {_norm_col(c): c for c in df.columns}
    found = []

    for name in proc_aliases:
        key = _norm_col(name)
        if key in norm_to_original:
            found.append(norm_to_original[key])

    if not found:
        return pd.Series(pd.NA, index=df.index, dtype="object")

    def join_row(row):
        vals = []
        for col in found:
            v = row.get(col, pd.NA)
            if pd.isna(v):
                continue
            s = str(v).strip().upper().replace(".", "")
            if s and s not in ["NAN", "NONE", "<NA>", ""]:
                vals.append(s)
        vals = sorted(set(vals))
        return "|".join(vals) if vals else pd.NA

    return df.apply(join_row, axis=1)

MEXICO_ALIASES = {
    "year": [
        "ANIO_EGR", "AÑO_EGR", "ANIO", "AÑO", "ANIOEGRESO", "AÑOEGRESO",
        "ANOCAP", "ANIO_CAP", "AÑO_CAP", "ANIO_BASE", "AÑO_BASE"
    ],
    "month": [
        "MES_EGR", "MES", "MES_EGRESO", "MESCAP"
    ],
    "hospital_id_raw": [
        "CLUES", "CLUES_UNIDAD", "CLUES_HOSP", "UNIDAD_MEDICA", "ID_UNIDAD",
        "CLUES_ESTABLECIMIENTO"
    ],
    "hospital_region": [
        "ENTIDAD_UM", "ENTIDAD_UNIDAD", "ENTIDAD", "EDO", "EENTIDAD",
        "CEDOCVE"
    ],
    "residence_region": [
        "ENTIDAD_RES", "ENTIDAD_RESIDENCIA", "EDO_RES", "RES_ENTIDAD"
    ],
    "age": [
        "EDAD", "EDAD_CUMPLIDA", "EDAD1", "EDAD_INSP"
    ],
    "age_unit": [
        "TIPO_EDAD", "EDAD_TIPO", "UNIDAD_EDAD", "CLAVE_EDAD", "CEDAD_INSP"
    ],
    "sex_raw": [
        "SEXO", "SEX"
    ],
    "los_days": [
        "DIAS_ESTANCIA", "DIAS_ESTA", "ESTANCIA", "DIAS_ESTADA"
    ],
    "dx_main": [
        "AFECCION_PPAL", "AFECCION_PRINCIPAL", "DIAG_PRIN", "DIAG_PRINC",
        "DIAGNOSTICO_PRINCIPAL", "CAUSA_EGRESO", "CIE10",
        "AFECPRIN4", "AFECPRIN3", "AFEC_PRIN4", "AFEC_PRIN3"
    ],
    "external_cause": [
        "CAUSA_EXT", "CAUSA_EXTERNA", "CAUSABAS4", "CAUSABAS3"
    ],
    "discharge_reason": [
        "MOTIVO_EGRESO", "MOTIVO_DE_EGRESO", "MOTEGRE"
    ],
    "discharge_condition": [
        "CONDICION_EGRESO", "COND_EGRESO"
    ],
    "case_count": [
        "CASOS"
    ],
}

MEXICO_PROC_ALIASES = [
    "INTERVENCION_QX",
    "TIPO_INTERVENCION",
    "PROC1", "PROC2", "PROC3", "PROC4", "PROC5", "PROC6",
    "PROCED1", "PROCED2", "PROCED3", "PROCED4", "PROCED5", "PROCED6",
    "CIE9_1", "CIE9_2", "CIE9_3", "CIE9_4", "CIE9_5", "CIE9_6",
    "CODIGO_CIE_9_MC", "COD_CIE9MC", "CIE_9_MC"
]


def ingest_mexico(years: List[int], raw_dir: Path, inter_dir: Path) -> Optional[pd.DataFrame]:
    LOG.info("═" * 60)
    LOG.info("INGESTÃO MÉXICO (SAEH/DGIS)")
    LOG.info("═" * 60)

    ck = inter_dir / "mexico_raw.parquet"
    if file_exists_ok(ck):
        LOG.info(f"[CHECKPOINT-MX] {ck}")
        return pd.read_parquet(ck)

    all_dfs = []
    for year in years:
        url  = MEXICO_URLS.get(year)
        dfs  = ingest_country_year_generic(
            country="mexico", year=year, url=url,
            raw_dir=raw_dir,
            zip_filename=f"SAEH_{year}.zip",
        )
        for df in dfs:
            df = standardize_columns_by_alias(df, MEXICO_ALIASES, "mexico")
            df["procedure_code_raw"] = combine_procedure_columns(df, MEXICO_PROC_ALIASES)
            df = _apply_s06_filter(df, "dx_main", "mexico", year)

            if "year" in df.columns and df["year"].notna().any():
                df["year"] = pd.to_numeric(df["year"], errors="coerce").astype("Int64")
                before_y = len(df)
                df = df[df["year"] == year].copy()
                LOG.info(f"[MEXICO] {year}: filtrado por ano real: {before_y:,} → {len(df):,}")
            else:
                df["year"] = year

            if len(df) == 0:
                continue

            df["country"] = "mexico"
            df["source"]  = "SAEH-DGIS"
            all_dfs.append(df)

    if not all_dfs:
        LOG.error("[MX] Nenhum dado Mexico carregado.")
        return None

    df_mx = pd.concat(all_dfs, ignore_index=True)

    before_dup = len(df_mx)
    subset_dup = [c for c in ["year", "dx_main", "age", "sex_raw", "los_days", "hospital_id_raw", "discharge_reason"] if c in df_mx.columns]
    df_mx = df_mx.drop_duplicates(subset=subset_dup).copy()
    LOG.info(f"[MEXICO] Remoção de duplicatas exatas: {before_dup:,} → {len(df_mx):,}")

    save_parquet(df_mx, ck, "México raw S06")
    return df_mx


def clean_standardize_mexico(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    if "case_count" in df.columns:
        df["case_count"] = pd.to_numeric(df["case_count"], errors="coerce").fillna(1).astype("Int64")
        if df["case_count"].max() > 1:
            LOG.warning(
                f"[MX] CASOS tem valores >1. Verificar se a base é agregada. "
                f"max(CASOS)={df['case_count'].max()}"
            )
    else:
        df["case_count"] = 1

    if "age" in df.columns:
        df["age"] = pd.to_numeric(df["age"], errors="coerce")
        df.loc[df["age"] >= 999, "age"] = pd.NA
        df["age"] = df["age"].astype("Int64")

    if "sex_raw" in df.columns:
        df["sex"] = df["sex_raw"].astype(str).str.strip().map({
            "1": "M",
            "2": "F",
            "HOMBRE": "M",
            "MUJER": "F",
            "M": "M",
            "F": "F",
        }).fillna("unknown")

    if "los_days" in df.columns:
        df["los_days"] = pd.to_numeric(df["los_days"], errors="coerce").astype("Int64")

    if "discharge_reason" in df.columns:
        x = df["discharge_reason"].astype(str).str.strip().str.upper()
        df["death_in_hospital"] = x.isin(["5", "05", "DEFUNCION", "DEFUNCIÓN"]).astype("Int64")
    elif "discharge_condition" in df.columns:
        x = df["discharge_condition"].astype(str).str.strip().str.upper()
        df["death_in_hospital"] = x.isin(["5", "05", "DEFUNCION", "DEFUNCIÓN"]).astype("Int64")
    else:
        df["death_in_hospital"] = pd.NA

    if "hospital_id_raw" in df.columns:
        df["hospital_id"] = df["hospital_id_raw"].apply(
            lambda x: f"MX_{str(x).strip()}"
            if pd.notna(x) and str(x).strip() not in ["", "nan", "None", "<NA>"]
            else pd.NA
        )
    else:
        df["hospital_id"] = pd.NA

    if "dx_main" in df.columns:
        df["dx_main"] = (
            df["dx_main"]
            .astype(str)
            .str.strip()
            .str.upper()
            .str.replace(".", "", regex=False)
        )

    df["icu_any"] = pd.NA
    df["icu_days"] = pd.NA
    df["cost_local_currency"] = pd.NA
    df["urgent_admission"] = pd.NA

    before = len(df)
    df = df[df["age"] >= CONFIG["min_age"]].copy()
    LOG.info(f"[MX] >=18 anos: {before:,} → {len(df):,}")

    return df


def run_mexico_ingestion(config, dirs) -> Optional[pd.DataFrame]:
    if not config["countries"]["mexico"]:
        return None

    ck = MEXICO_INTER / "mexico_clean.parquet"
    if file_exists_ok(ck):
        return pd.read_parquet(ck)

    # Novo: coleta automática antes da ingestão
    auto_collect_country_sources("mexico", config["study_years"], MEXICO_RAW_DIR)

    df_raw = ingest_mexico(config["study_years"], MEXICO_RAW_DIR, MEXICO_INTER)
    if df_raw is None:
        LOG.error("[MX] Ainda sem dados após coleta automática.")
        return None

    df_clean = clean_standardize_mexico(df_raw)
    save_parquet(df_clean, ck, "México limpo")
    save_csv_xlsx(quick_audit(df_clean, "México"), DIRS["qc"] / "audit_mexico")
    return df_clean


print("✅  Bloco 5 (México REVISADO) pronto.")


# ╔══════════════════════════════════════════════════════════╗
# ║  BLOCO 6 — Chile (DEIS/MINSAL)                         ║
# ╚══════════════════════════════════════════════════════════╝

CHILE_RAW_DIR = DIRS["raw_cl"]
CHILE_INTER   = DIRS["intermediate"] / "chile"
CHILE_INTER.mkdir(parents=True, exist_ok=True)

# URLs DEIS — verificar anualmente em: https://deis.minsal.cl
CHILE_URLS = {year: None for year in CONFIG["study_years"]}


CHILE_ALIASES = {
    "year": [
        "ANO_EGR", "AÑO_EGR", "ANO", "AÑO", "YEAR"
    ],
    "dx_main": [
        "DIAG1", "DIAG_PRIN", "DIAG_PRINC", "DIAGNOSTICO1", "DIAGNOSTICO_PRINCIPAL"
    ],
    "dx_secondary": [
        "DIAG2", "DIAGNOSTICO2"
    ],
    "external_cause": [
        "CAUSA_EXT", "CAUSA_EXTERNA"
    ],
    "hospital_id_raw": [
        "CODESTAB", "COD_ESTAB", "ESTABLECIMIENTO", "ID_ESTAB", "CODIGO_ESTABLECIMIENTO"
    ],
    "hospital_region": [
        "REGION_ESTAB", "REG_ESTAB", "REGION_HOSP", "SERVICIO_SALUD"
    ],
    "residence_region": [
        "REGION_RES", "REG_RES", "REGION_RESIDENCIA"
    ],
    "age": [
        "EDAD_CANT", "EDAD", "EDAD_ANOS", "EDAD_AÑOS"
    ],
    "age_type": [
        "EDAD_TIPO", "TIPO_EDAD", "UNIDAD_EDAD"
    ],
    "sex_raw": [
        "SEXO", "SEX"
    ],
    "los_days": [
        "DIAS_ESTADA", "DIAS_ESTANCIA", "ESTADA", "DIAS"
    ],
    "discharge_condition": [
        "CONDICION_EGRESO", "COND_EGRESO", "CONDICION_AL_EGRESO"
    ],
}

CHILE_PROC_ALIASES = [
    "INTERV_Q", "TIPO_INTERV_Q", "INTERVENCION_Q",
    "PROC1", "PROC2", "PROC3", "PROC4", "PROC5",
    "COD_INTERV", "CODIGO_INTERVENCION", "FONASA", "COD_FONASA"
]


def ingest_chile(years: List[int], raw_dir: Path, inter_dir: Path) -> Optional[pd.DataFrame]:
    LOG.info("═" * 60)
    LOG.info("INGESTÃO CHILE (DEIS/MINSAL)")
    LOG.info("═" * 60)

    ck = inter_dir / "chile_raw.parquet"
    if file_exists_ok(ck):
        LOG.info(f"[CHECKPOINT-CL] {ck}")
        df_ck = pd.read_parquet(ck)
        if df_ck is None or len(df_ck) == 0 or "age" not in df_ck.columns:
            LOG.warning("[CL] Checkpoint Chile vazio/inválido detectado. Remova o checkpoint e reprocesse.")
            return None
        return df_ck

    all_dfs = []

    for year in years:
        url = CHILE_URLS.get(year)
        dfs = ingest_country_year_generic(
            country="chile",
            year=year,
            url=url,
            raw_dir=raw_dir,
            zip_filename=f"Egresos_Chile_{year}.zip",
        )

        for df in dfs:
            source_file = str(df["_source_file"].iloc[0]) if "_source_file" in df.columns and len(df) else ""

            # Pula arquivos claramente não-microdados
            bad_tokens = [
                "base de establecimientos",
                "base_establecimiento",
                "diccionario",
                "esquema",
                "formulario",
                "ficha",
                "urgencia",
                "urgencias",
                "remsas",
                "remasep",
                "establecimientos",
            ]
            sf_low = source_file.lower()
            if any(tok in sf_low for tok in bad_tokens):
                LOG.info(f"[CHILE] {year}: pulando arquivo não-microdado: {Path(source_file).name}")
                continue

            df = standardize_columns_by_alias(df, CHILE_ALIASES, "chile")

            # Se nem tem diagnóstico/idade/sexo/permanência, não é microdado compatível
            required_min = {"dx_main", "age", "sex_raw", "los_days"}
            missing = [c for c in required_min if c not in df.columns]
            if missing:
                LOG.info(
                    f"[CHILE] {year}: arquivo ignorado por ausência de colunas mínimas {missing}: "
                    f"{Path(source_file).name}"
                )
                continue

            df["procedure_code_raw"] = combine_procedure_columns(df, CHILE_PROC_ALIASES)
            df = _apply_s06_filter(df, "dx_main", "chile", year)

            if len(df) == 0:
                continue

            df["year"] = year
            df["country"] = "chile"
            df["source"] = "DEIS-MINSAL"
            all_dfs.append(df)

    if not all_dfs:
        LOG.warning(
            "[CL] Nenhum microdado Chile S06.x encontrado. "
            "Chile será pulado nesta rodada."
        )
        return None

    df_cl = pd.concat(all_dfs, ignore_index=True)

    if len(df_cl) == 0 or "age" not in df_cl.columns:
        LOG.warning("[CL] Chile resultou vazio ou sem idade. Pulando país.")
        return None

    save_parquet(df_cl, ck, "Chile raw S06")
    return df_cl


def clean_standardize_chile(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    if df is None or len(df) == 0:
        LOG.warning("[CL] DataFrame Chile vazio. Retornando vazio.")
        return pd.DataFrame()

    required = ["age", "dx_main"]
    missing_required = [c for c in required if c not in df.columns]

    if missing_required:
        LOG.warning(
            f"[CL] Chile sem colunas mínimas {missing_required}. "
            "Provavelmente foram baixadas tabelas agregadas, não microdados. Pulando Chile."
        )
        return pd.DataFrame()

    df["age"] = pd.to_numeric(df["age"], errors="coerce")
    df.loc[df["age"] >= 999, "age"] = pd.NA
    df["age"] = df["age"].astype("Int64")

    if "age_type" in df.columns:
        # Mantém apenas idade em anos quando o código for claramente anual.
        # Se futuramente validarmos outro código, ajustamos aqui.
        age_type = df["age_type"].astype(str).str.strip()
        df.loc[~age_type.isin(["1", "01", "A", "ANOS", "AÑOS"]), "age"] = pd.NA

    if "sex_raw" in df.columns:
        df["sex"] = df["sex_raw"].astype(str).str.strip().str.upper().map({
            "1": "M",
            "2": "F",
            "M": "M",
            "F": "F",
            "HOMBRE": "M",
            "MUJER": "F",
        }).fillna("unknown")
    else:
        df["sex"] = "unknown"

    if "los_days" in df.columns:
        df["los_days"] = pd.to_numeric(df["los_days"], errors="coerce").astype("Int64")
    else:
        df["los_days"] = pd.NA

    if "discharge_condition" in df.columns:
        x = df["discharge_condition"].astype(str).str.strip().str.upper()
        df["death_in_hospital"] = x.isin(["2", "02", "FALLECIDO", "FALLECIDA", "MUERTE"]).astype("Int64")
    else:
        df["death_in_hospital"] = pd.NA

    if "hospital_id_raw" in df.columns:
        df["hospital_id"] = df["hospital_id_raw"].apply(
            lambda x: f"CL_{str(x).strip()}"
            if pd.notna(x) and str(x).strip() not in ["", "nan", "None", "<NA>"]
            else pd.NA
        )
    else:
        df["hospital_id"] = pd.NA

    df["dx_main"] = (
        df["dx_main"]
        .astype(str)
        .str.strip()
        .str.upper()
        .str.replace(".", "", regex=False)
    )

    df["icu_any"] = pd.NA
    df["icu_days"] = pd.NA
    df["cost_local_currency"] = pd.NA
    df["urgent_admission"] = pd.NA

    before = len(df)
    df = df[df["age"] >= CONFIG["min_age"]].copy()
    LOG.info(f"[CL] >=18 anos: {before:,} → {len(df):,}")

    return df



def run_chile_ingestion(config, dirs) -> Optional[pd.DataFrame]:
    if not config["countries"]["chile"]:
        return None

    ck = CHILE_INTER / "chile_clean.parquet"
    if file_exists_ok(ck):
        df_ck = pd.read_parquet(ck)
        if df_ck is not None and len(df_ck) > 0 and "age" in df_ck.columns:
            return df_ck
        else:
            LOG.warning("[CL] Checkpoint Chile limpo vazio/inválido. Ignorando checkpoint.")
            try:
                ck.unlink()
            except Exception:
                pass

    auto_collect_country_sources("chile", config["study_years"], CHILE_RAW_DIR)

    df_raw = ingest_chile(config["study_years"], CHILE_RAW_DIR, CHILE_INTER)

    if df_raw is None or len(df_raw) == 0:
        LOG.warning(
            "[CL] Sem microdados Chile válidos. "
            "Chile será excluído da análise multinacional nesta rodada."
        )
        return None

    df_clean = clean_standardize_chile(df_raw)

    if df_clean is None or len(df_clean) == 0:
        LOG.warning("[CL] Chile limpo vazio. Pulando Chile.")
        return None

    save_parquet(df_clean, ck, "Chile limpo")
    save_csv_xlsx(quick_audit(df_clean, "Chile"), DIRS["qc"] / "audit_chile")
    return df_clean

print("✅  Bloco 6 (Chile REVISADO) pronto.")


# ╔══════════════════════════════════════════════════════════╗
# ║  BLOCO 7 — Equador (INEC-EH)                           ║
# ╚══════════════════════════════════════════════════════════╝

EQUADOR_RAW_DIR = DIRS["raw_ec"]
EQUADOR_INTER   = DIRS["intermediate"] / "equador"
EQUADOR_INTER.mkdir(parents=True, exist_ok=True)

# URLs INEC — verificar em: https://www.ecuadorencifras.gob.ec
EQUADOR_URLS = {year: None for year in CONFIG["study_years"]}

EQUADOR_ALIASES = {
    "year": [
        "ANO", "AÑO", "ANIO", "YEAR"
    ],
    "hospital_region": [
        "COD_PROV", "PROV_ESTAB", "PROVINCIA_ESTABLECIMIENTO"
    ],
    "residence_region": [
        "COD_PROV_RES", "PROV_RES", "PROVINCIA_RESIDENCIA"
    ],
    "hospital_id_raw": [
        "COD_ESTAB", "ESTABLECIMIENTO", "ID_ESTAB", "CODIGO_ESTABLECIMIENTO"
    ],
    "age": [
        "EDAD", "EDAD_PACIENTE"
    ],
    "age_unit": [
        "UNIDAD_EDAD", "TIPO_EDAD"
    ],
    "sex_raw": [
        "SEXO", "SEX"
    ],
    "los_days": [
        "DIAS_ESTANCIA", "DIAS_ESTADA", "ESTANCIA"
    ],
    "dx_main": [
        "DIAG_PRIN", "DIAG_PRINC", "DIAGNOSTICO_PRINCIPAL", "CAUSA_MORBILIDAD", "CIE10"
    ],
    "dx_secondary": [
        "DIAG_SEC1", "DIAG_SEC", "DIAGNOSTICO_SECUNDARIO"
    ],
    "external_cause": [
        "CAUSA_EXT", "CAUSA_EXTERNA"
    ],
    "discharge_condition": [
        "CONDICION_EGR", "CONDICION_EGRESO", "COND_EGRESO"
    ],
}

EQUADOR_PROC_ALIASES = [
    "COD_INTERV", "COD_INTERVENCION", "INTERVENCION",
    "PROC1", "PROC2", "PROC3", "PROC4", "CIE9", "CIE_9_CM"
]


def ingest_equador(years: List[int], raw_dir: Path, inter_dir: Path) -> Optional[pd.DataFrame]:
    LOG.info("═" * 60)
    LOG.info("INGESTÃO EQUADOR (INEC-EH)")
    LOG.info("  ⚠  AVISO: campos procedimentais limitados no INEC.")
    LOG.info("  Equador participará principalmente em análises descritivas.")
    LOG.info("═" * 60)

    ck = inter_dir / "equador_raw.parquet"
    if file_exists_ok(ck):
        LOG.info(f"[CHECKPOINT-EC] {ck}")
        return pd.read_parquet(ck)

    all_dfs = []
    for year in years:
        url = EQUADOR_URLS.get(year)
        dfs = ingest_country_year_generic(
            country="equador", year=year, url=url,
            raw_dir=raw_dir,
            zip_filename=f"INEC_EH_{year}.zip",
            sav_support=True,
        )
        for df in dfs:
            df = standardize_columns_by_alias(df, EQUADOR_ALIASES, "equador")
            df["procedure_code_raw"] = combine_procedure_columns(df, EQUADOR_PROC_ALIASES)
            df = _apply_s06_filter(df, "dx_main", "equador", year)
            df["year"]    = year
            df["country"] = "equador"
            df["source"]  = "INEC-EH"
            all_dfs.append(df)

    if not all_dfs:
        LOG.error("[EC] Nenhum dado Equador.")
        return None

    df_ec = pd.concat(all_dfs, ignore_index=True)
    save_parquet(df_ec, ck, "Equador raw S06")
    return df_ec


# --- INÍCIO DO NOVO TRECHO ---
def clean_standardize_equador(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    # Ponto 24: Assumindo UNIDAD_EDAD == 1 como anos
    if "age" in df.columns:
        df["age"] = pd.to_numeric(df["age"], errors="coerce").astype("Int64")
        if "age_unit" in df.columns:
            df.loc[df["age_unit"].astype(str).str.strip() != "1", "age"] = pd.NA
    if "sex_raw" in df.columns:
        df["sex"] = df["sex_raw"].map({"1": "M", "2": "F"}).fillna("unknown")
    if "los_days" in df.columns:
        df["los_days"] = pd.to_numeric(df["los_days"], errors="coerce").astype("Int64")

    # Ponto 25: Assunção de óbito CONDICION_EGR == 3 no INEC
    if "discharge_condition" in df.columns:
        df["death_in_hospital"] = (df["discharge_condition"].astype(str).str.strip() == "3").astype("Int64")
    else:
        df["death_in_hospital"] = pd.NA

    # Ponto 5: Corrigindo EC_nan
    if "hospital_id_raw" in df.columns:
        df["hospital_id"] = df["hospital_id_raw"].apply(
            lambda x: f"EC_{str(x).strip()}" if pd.notna(x) and str(x).strip() not in ["", "nan", "None"] else pd.NA
        )
    else:
        df["hospital_id"] = pd.NA

    if "dx_main" in df.columns:
        df["dx_main"] = df["dx_main"].str.strip().str.upper()

    df["icu_any"] = pd.NA; df["icu_days"] = pd.NA
    df["cost_local_currency"] = pd.NA; df["urgent_admission"] = pd.NA

    before = len(df)
    df = df[df["age"] >= CONFIG["min_age"]].copy()
    LOG.info(f"[EC] >=18 anos: {before:,} → {len(df):,}")
    return df
# --- FIM DO NOVO TRECHO ---


def run_equador_ingestion(config, dirs) -> Optional[pd.DataFrame]:
    if not config["countries"]["equador"]:
        return None

    ck = EQUADOR_INTER / "equador_clean.parquet"
    if file_exists_ok(ck):
        return pd.read_parquet(ck)

    # Novo: coleta automática antes da ingestão
    auto_collect_country_sources("equador", config["study_years"], EQUADOR_RAW_DIR)

    df_raw = ingest_equador(config["study_years"], EQUADOR_RAW_DIR, EQUADOR_INTER)
    if df_raw is None:
        LOG.error("[EC] Ainda sem dados após coleta automática.")
        return None

    df_clean = clean_standardize_equador(df_raw)
    save_parquet(df_clean, ck, "Equador limpo")
    save_csv_xlsx(quick_audit(df_clean, "Equador"), DIRS["qc"] / "audit_equador")
    return df_clean


print("✅  Bloco 7 (Equador REVISADO) pronto.")


# ╔══════════════════════════════════════════════════════════╗
# ║  BLOCO 8 — Auditoria Bruta por País                     ║
# ╚══════════════════════════════════════════════════════════╝

def run_raw_audit(country_dfs: Dict[str, Optional[pd.DataFrame]]) -> pd.DataFrame:
    LOG.info("AUDITORIA BRUTA MULTINACIONAL")
    rows = []
    for country, df in country_dfs.items():
        if df is None:
            rows.append({"country": country, "n_records": 0, "n_hospitals": 0,
                         "years_found": "", "pct_missing_dx": 100,
                         "pct_missing_age": 100, "pct_missing_los": 100,
                         "pct_missing_death": 100, "pct_missing_proc": 100,
                         "alert": "SEM DADOS"})
            continue

        n = len(df)
        years = sorted(df["year"].dropna().unique().tolist()) if "year" in df.columns else []

        def pct_null(col):
            return round(df[col].isna().sum() / n * 100, 1) if col in df.columns else 100.0

        alerts = []
        if pct_null("dx_main") > 20:          alerts.append("HIGH_MISSING_DX")
        if pct_null("age") > 10:              alerts.append("HIGH_MISSING_AGE")
        if pct_null("procedure_code_raw") > 50: alerts.append("HIGH_MISSING_PROC")
        if pct_null("death_in_hospital") > 20:  alerts.append("HIGH_MISSING_DEATH")

        rows.append({
            "country":           country,
            "n_records":         n,
            "n_hospitals":       df["hospital_id"].nunique() if "hospital_id" in df.columns else 0,
            "years_found":       str(years),
            "pct_missing_dx":    pct_null("dx_main"),
            "pct_missing_age":   pct_null("age"),
            "pct_missing_los":   pct_null("los_days"),
            "pct_missing_death": pct_null("death_in_hospital"),
            "pct_missing_proc":  pct_null("procedure_code_raw"),
            "alert":             "; ".join(alerts) if alerts else "OK",
        })

    audit_df = pd.DataFrame(rows)
    save_csv_xlsx(audit_df, DIRS["qc"] / "audit_multinacional_bruto")
    LOG.info(f"\n{audit_df[['country','n_records','n_hospitals','alert']].to_string(index=False)}")
    return audit_df


print("✅  Bloco 8 (Auditoria Bruta) pronto.")


# ╔══════════════════════════════════════════════════════════╗
# ║  BLOCO 9 — Limpeza Final e Plausibilidade              ║
# ╚══════════════════════════════════════════════════════════╝

AGE_MAX = 120
LOS_MAX = 365
ICU_MAX = 365

# Colunas do CDM com seus papéis
# OBRIGATÓRIAS: devem existir e ter dados suficientes
# OPCIONAIS: podem ser NA sem excluir o registro
# DERIVADAS: calculadas pelo pipeline
CDM_SCHEMA = {
    # coluna               papel        tipo
    "country":           ("REQUIRED",  "str"),
    "year":              ("REQUIRED",  "Int64"),
    "hospital_id":       ("REQUIRED",  "str"),
    "hospital_region":   ("OPTIONAL",  "str"),
    "residence_region":  ("OPTIONAL",  "str"),
    "age":               ("REQUIRED",  "Int64"),
    "sex":               ("REQUIRED",  "str"),
    "dx_main":           ("REQUIRED",  "str"),
    "dx_secondary":      ("OPTIONAL",  "str"),
    "external_cause":    ("OPTIONAL",  "str"),
    "trauma_subtype":    ("DERIVED",   "str"),
    "urgent_admission":  ("OPTIONAL",  "Int64"),
    "death_in_hospital": ("REQUIRED",  "Int64"),
    "los_days":          ("REQUIRED",  "Int64"),
    "icu_any":           ("OPTIONAL",  "Int64"),
    "icu_days":          ("OPTIONAL",  "Int64"),
    "surgery_any":       ("DERIVED",   "Int64"),
    "procedure_code_raw":("OPTIONAL",  "str"),
    "procedure_class":   ("DERIVED",   "str"),
    "procedure_mapping_confidence": ("DERIVED", "str"),
    "procedure_class_final":        ("DERIVED", "str"),
    "hospital_volume_year":  ("DERIVED", "Int64"),
    "hospital_capacity_score":("DERIVED","Int64"),
    "transfer_proxy":    ("OPTIONAL",  "Int64"),
    "cost_local_currency":("OPTIONAL", "float64"),
    "source":            ("REQUIRED",  "str"),
}


def apply_plausibility_filters(df: pd.DataFrame, country: str) -> pd.DataFrame:
    n0           = len(df)
    exclusion_log = []

    if "age" in df.columns:
        bad = (df["age"] < CONFIG["min_age"]) | (df["age"] > AGE_MAX)
        exclusion_log.append({"step": "age_implausible", "n_excluded": int(bad.sum())})
        df = df[~bad].copy()

    if "los_days" in df.columns:
        neg = df["los_days"] < 0
        exclusion_log.append({"step": "los_negative", "n_excluded": int(neg.sum())})
        df  = df[~neg].copy()
        df.loc[df["los_days"] > LOS_MAX, "los_days"] = pd.NA

    if "icu_days" in df.columns:
        df["icu_days"] = df["icu_days"].clip(upper=ICU_MAX)

    if "death_in_hospital" in df.columns:
        valid = df["death_in_hospital"].isin([0, 1]) | df["death_in_hospital"].isna()
        bad_n = (~valid).sum()
        if bad_n > 0:
            exclusion_log.append({"step": "death_invalid_value", "n_excluded": int(bad_n)})
            df.loc[~valid, "death_in_hospital"] = pd.NA

    LOG.info(f"[{country.upper()}] Plausibilidade: {n0:,} → {len(df):,} ({n0-len(df):,} excl.)")
    excl_df = pd.DataFrame(exclusion_log)
    excl_df["country"] = country
    save_csv_xlsx(excl_df, DIRS["qc"] / f"exclusions_{country}")
    return df


# --- INÍCIO DO NOVO TRECHO ---
def deduplicate(df: pd.DataFrame, country: str) -> pd.DataFrame:
    # Desativando deduplicação heurística agressiva
    # Em bases administrativas sem ID perfeito, é mais seguro manter o N original.
    LOG.info(f"[{country.upper()}] Dedup heurística desativada (preservando N original).")
    return df
# --- FIM DO NOVO TRECHO ---


def finalize_country_df(df: pd.DataFrame, country: str) -> pd.DataFrame:
    """Plausibilidade, dedup e garantia de colunas e tipos do CDM."""
    df = apply_plausibility_filters(df, country)
    df = deduplicate(df, country)

    # Garantir todas as colunas do schema
    for col, (role, dtype) in CDM_SCHEMA.items():
        if col not in df.columns:
            df[col] = pd.NA

    # Aplicar tipos
    for col, (role, dtype) in CDM_SCHEMA.items():
        if col not in df.columns:
            continue
        try:
            if dtype == "Int64":
                df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")
            elif dtype == "float64":
                df[col] = pd.to_numeric(df[col], errors="coerce")
            elif dtype == "str":
                df[col] = df[col].astype(str).replace({"nan": pd.NA, "<NA>": pd.NA, "None": pd.NA})
        except Exception as e:
            LOG.warning(f"[FINALIZE] Tipo {dtype} em {col} falhou: {e}")

    final_cols = [c for c in CDM_SCHEMA.keys() if c in df.columns]
    extras     = [c for c in df.columns if c not in CDM_SCHEMA]
    return df[final_cols + extras]


print("✅  Bloco 9 (Limpeza Final REVISADA) pronto.")

# ============================================================
#  PIPELINE TCE MULTINACIONAL — PARTE 3 REVISADA
#  Blocos 10–14 | Versão 1.1
#
#  CORREÇÕES APLICADAS:
#
#  BLOCO 10 — Crosswalk:
#    • Sistema explícito de confiança (HIGH/MODERATE/LOW/UNVERIFIED)
#    • Crosswalk carregado de arquivo externo editável (09_metadata/)
#    • Fallback para hardcoded se arquivo ausente
#    • Flags `procedure_mapping_confidence` e `procedure_class_final`
#    • DC vs CRAN restrito a HIGH/MODERATE automaticamente
#    • Comentários de validação clínica obrigatória
#
#  BLOCO 11 — Harmonização:
#    • Validação final do CDM com alertas automáticos
#
#  BLOCO 14 — Modelos:
#    ★ CORREÇÃO CRÍTICA: fit_logistic_mixed() substituída por
#      fit_gee_logistic() usando GEE com cluster por hospital
#    • GEE logístico (statsmodels GEE) é estatisticamente correto
#      para desfecho binário com estrutura de cluster hospitalar
#    • Retorna OR, IC95%, p-valor compatíveis com meta-análise
#    • Fallback para regressão logística simples se N < 200
# ============================================================


# ╔══════════════════════════════════════════════════════════╗
# ║  BLOCO 10 — Crosswalk de Procedimentos e Diagnósticos   ║
# ║  REVISADO: confiança explícita + arquivo externo        ║
# ╚══════════════════════════════════════════════════════════╝
#
#  ⚠  AVISO METODOLÓGICO ⚠
#  Os mapeamentos abaixo são pontos de partida baseados em
#  literatura e tabelas públicas de procedimentos.
#  TODOS os mapeamentos com confiança < HIGH devem ser
#  validados por neurocirurgião e profissional de CID antes
#  do uso em análise publicável.
#
#  CONFIANÇA DE MAPEAMENTO:
#    HIGH       = Código específico e inequívoco na tabela oficial
#    MODERATE   = Código provável, mas pode incluir outros procedimentos
#    LOW        = Inferido por similaridade; requer validação
#    UNVERIFIED = Adicionado para completude; NÃO usar em análise DC/CRAN
# ──────────────────────────────────────────────────────────────

# ── Crosswalk diagnóstico (CID-10 — universal) ────────────────

DX_TRAUMA_MAP = {
    r"^S060":  "UNSPEC",
    r"^S061":  "DIFFUSE",
    r"^S062":  "DIFFUSE",
    r"^S063":  "IPH_CONT",
    r"^S064":  "EDH",
    r"^S065":  "SDH",
    r"^S066":  "SAH",
    r"^S068":  "DIFFUSE",
    r"^S069":  "UNSPEC",
}


def classify_dx(dx_code) -> str:
    import re
    if pd.isna(dx_code) or not isinstance(dx_code, str):
        return "UNSPEC"
    code = dx_code.strip().upper().replace(".", "")
    for pattern, label in DX_TRAUMA_MAP.items():
        if re.match(pattern, code):
            return label
    return "UNSPEC"


# ── Crosswalk procedimental — tabela mestre ────────────────────
#
#  Estrutura: lista de dicts com campos:
#    country, code_system, raw_code, description_raw,
#    mapped_class, mapping_confidence, notes
#
#  mapping_confidence: HIGH | MODERATE | LOW | UNVERIFIED
#
#  ⚠  Revise e valide clinicamente antes de publicar.

PROC_CROSSWALK_DEFAULT: List[Dict] = [
    # ── BRASIL — SIGTAP ──────────────────────────────────────
    # Fonte: http://sigtap.datasus.gov.br
    {
        "country": "brasil", "code_system": "SIGTAP",
        "raw_code": "0401060129",
        "description_raw": "Craniectomia descompressiva com duroplastia",
        "mapped_class": "DC", "mapping_confidence": "HIGH",
        "notes": "Código específico SIGTAP para DC com duroplastia",
    },
    {
        "country": "brasil", "code_system": "SIGTAP",
        "raw_code": "0401060110",
        "description_raw": "Craniectomia descompressiva",
        "mapped_class": "DC", "mapping_confidence": "HIGH",
        "notes": "Código direto SIGTAP",
    },
    {
        "country": "brasil", "code_system": "SIGTAP",
        "raw_code": "0401060048",
        "description_raw": "Craniotomia para drenagem de hematoma subdural",
        "mapped_class": "CRAN", "mapping_confidence": "HIGH",
        "notes": "SDH evacuação",
    },
    {
        "country": "brasil", "code_system": "SIGTAP",
        "raw_code": "0401060064",
        "description_raw": "Craniotomia para drenagem de hematoma epidural",
        "mapped_class": "CRAN", "mapping_confidence": "HIGH",
        "notes": "EDH evacuação",
    },
    {
        "country": "brasil", "code_system": "SIGTAP",
        "raw_code": "0401060056",
        "description_raw": "Craniotomia para drenagem de hematoma intracraniano",
        "mapped_class": "CRAN", "mapping_confidence": "HIGH",
        "notes": "",
    },
    {
        "country": "brasil", "code_system": "SIGTAP",
        "raw_code": "0401060072",
        "description_raw": "Craniotomia para contusão cerebral",
        "mapped_class": "CRAN", "mapping_confidence": "MODERATE",
        "notes": "Pode incluir procedimentos não-traumáticos; validar",
    },
    {
        "country": "brasil", "code_system": "SIGTAP",
        "raw_code": "0401060080",
        "description_raw": "Craniotomia para hemorragia intracraniana",
        "mapped_class": "CRAN", "mapping_confidence": "MODERATE",
        "notes": "Inclui AVC hemorrágico — requer filtro de causa externa",
    },
    {
        "country": "brasil", "code_system": "SIGTAP",
        "raw_code": "0401060137",
        "description_raw": "Outras craniotomias/craniectomias",
        "mapped_class": "OTHER_CRAN", "mapping_confidence": "LOW",
        "notes": "Categoria inespecífica — NÃO usar em análise DC vs CRAN",
    },

    # ── MÉXICO — CIE-9-MC ────────────────────────────────────
    # Fonte: CIE-9-MC edição DGIS
    {
        "country": "mexico", "code_system": "CIE-9-MC",
        "raw_code": "0109",
        "description_raw": "Otra craneotomía descompresiva",
        "mapped_class": "DC", "mapping_confidence": "MODERATE",
        "notes": "CIE-9-MC; pode incluir DC não-traumáticas — validar com causa externa",
    },
    {
        "country": "mexico", "code_system": "CIE-9-MC",
        "raw_code": "0102",
        "description_raw": "Evacuación de hematoma subdural",
        "mapped_class": "CRAN", "mapping_confidence": "HIGH",
        "notes": "",
    },
    {
        "country": "mexico", "code_system": "CIE-9-MC",
        "raw_code": "0124",
        "description_raw": "Otra excisión/evacuación de tejido cerebral",
        "mapped_class": "CRAN", "mapping_confidence": "MODERATE",
        "notes": "Categoria ampla — validar contexto clínico",
    },
    {
        "country": "mexico", "code_system": "CIE-9-MC",
        "raw_code": "0125",
        "description_raw": "Aspiración de hematoma intracraniano",
        "mapped_class": "CRAN", "mapping_confidence": "HIGH",
        "notes": "",
    },

    # ── CHILE — FONASA/MINSAL ────────────────────────────────
    # ⚠  Códigos abaixo precisam de validação contra tabela FONASA atual
    # ── CHILE — FONASA/MINSAL ────────────────────────────────
    {
        "country": "chile", "code_system": "FONASA",
        "raw_code": "4103001",
        "description_raw": "Craniectomia descompressiva",
        "mapped_class": "DC", "mapping_confidence": "UNVERIFIED",
        "notes": "UNVERIFIED — confirmar com tabela FONASA 2024",
    },
    {
        "country": "chile", "code_system": "FONASA",
        "raw_code": "4103002",
        "description_raw": "Craniotomia evacuação SDH",
        "mapped_class": "CRAN", "mapping_confidence": "UNVERIFIED",
        "notes": "UNVERIFIED — confirmar com tabela FONASA 2024",
    },
    {
        "country": "chile", "code_system": "FONASA",
        "raw_code": "4103010",
        "description_raw": "Craniotomia para hematoma intracraniano",
        "mapped_class": "CRAN", "mapping_confidence": "UNVERIFIED",
        "notes": "UNVERIFIED",
    },

    # ── EQUADOR — CIE-9-CM ────────────────────────────────────
    {
        "country": "equador", "code_system": "CIE-9-CM",
        "raw_code": "0109",
        "description_raw": "Craniectomia descompressiva (por analogia CIE-9)",
        "mapped_class": "DC", "mapping_confidence": "LOW",
        "notes": "Equador usa CIE-9-CM; validar disponibilidade do campo COD_INTERV",
    },
    {
        "country": "equador", "code_system": "CIE-9-CM",
        "raw_code": "0102",
        "description_raw": "Evacuación SDH",
        "mapped_class": "CRAN", "mapping_confidence": "LOW",
        "notes": "Mesmo aviso do México; campos procedimentais INEC limitados",
    },
]

# Confiança mínima para análise DC/CRAN (configurável)
DC_ANALYSIS_MIN_CONFIDENCE = CONFIG.get(
    "min_proc_confidence_for_dc_analysis", ["HIGH", "MODERATE"]
)


# ── Carregar crosswalk de arquivo externo ou usar default ─────

def load_or_create_crosswalk(metadata_dir: Path) -> pd.DataFrame:
    """
    Carrega crosswalk de arquivo CSV editável em 09_metadata/.
    Se não existir, salva o default e o retorna.

    O arquivo externo permite que o pesquisador edite mapeamentos
    sem alterar o código — workflow correto para validação clínica.
    """
    cw_path = metadata_dir / CONFIG["proc_crosswalk_file"]

    if cw_path.exists():
        df_cw = pd.read_csv(cw_path, dtype=str)
        LOG.info(f"[CROSSWALK] Carregado de arquivo externo: {cw_path} ({len(df_cw)} entradas)")
    else:
        df_cw = pd.DataFrame(PROC_CROSSWALK_DEFAULT)
        df_cw.to_csv(cw_path, index=False, encoding="utf-8-sig")
        LOG.info(
            f"[CROSSWALK] Arquivo externo criado: {cw_path}\n"
            f"  ⚠  Revise e valide clinicamente antes de usar em análise publicável."
        )

    # Adicionar diagnósticos ao mesmo arquivo de referência
    dx_rows = [
        {
            "country": "ALL", "code_system": "CID-10",
            "raw_code": pattern, "description_raw": "",
            "mapped_class": cls, "mapping_confidence": "HIGH",
            "notes": "ICD-10 regex — codificação CID universal",
        }
        for pattern, cls in DX_TRAUMA_MAP.items()
    ]
    df_dx = pd.DataFrame(dx_rows)
    full_cw = pd.concat([df_cw, df_dx], ignore_index=True)

    # Salvar tabela completa de referência em metadata
    save_csv_xlsx(full_cw, metadata_dir / "crosswalk_completo_referencia")
    return df_cw


def _build_proc_lookup(df_cw: pd.DataFrame) -> Dict[str, Dict[str, str]]:
    """
    Constrói dicionário de lookup rápido:
    { country: { raw_code: (mapped_class, confidence) } }
    """
    lookup: Dict[str, Dict[str, Tuple[str, str]]] = {}
    for _, row in df_cw.iterrows():
        country = str(row.get("country", "")).lower()
        code    = str(row.get("raw_code", "")).strip().upper()
        cls     = str(row.get("mapped_class", "UNCLASSIFIED"))
        conf    = str(row.get("mapping_confidence", "UNVERIFIED"))
        if country not in lookup:
            lookup[country] = {}
        lookup[country][code] = (cls, conf)
    return lookup


PROC_LOOKUP: Dict = {}  # preenchido em build_crosswalk_table()


def classify_procedure_with_confidence(proc_code, country: str) -> Tuple[str, str]:
    if pd.isna(proc_code) or not isinstance(proc_code, str):
        return "UNCLASSIFIED", "NA"

    mapping = PROC_LOOKUP.get(country.lower(), {})
    codes = [c.strip().upper().replace(".", "") for c in str(proc_code).split("|") if c.strip()]

    matches = []
    for code in codes:
        if code in mapping:
            matches.append(mapping[code])  # (class, confidence)

    if not matches:
        return "UNCLASSIFIED", "NA"

    class_priority = {"DC": 3, "CRAN": 2, "OTHER_CRAN": 1, "UNCLASSIFIED": 0}
    conf_priority = {"HIGH": 3, "MODERATE": 2, "LOW": 1, "UNVERIFIED": 0, "NA": -1}

    matches = sorted(
        matches,
        key=lambda x: (conf_priority.get(x[1], -1), class_priority.get(x[0], 0)),
        reverse=True
    )
    return matches[0]

def build_crosswalk_table(dirs: dict) -> pd.DataFrame:
    """Carrega crosswalk, preenche lookup global, retorna DataFrame."""
    global PROC_LOOKUP
    df_cw       = load_or_create_crosswalk(dirs["metadata"])
    PROC_LOOKUP = _build_proc_lookup(df_cw)
    LOG.info(f"[CROSSWALK] Lookup: {sum(len(v) for v in PROC_LOOKUP.values())} entradas por país")
    return df_cw


def apply_crosswalk(df: pd.DataFrame) -> pd.DataFrame:
    """Aplica crosswalk ao DataFrame. Adiciona colunas de classe e confiança."""
    df = df.copy()

    # Subtipo diagnóstico
    df["trauma_subtype"] = df["dx_main"].apply(classify_dx)

    # Classificação de procedimento com confiança
    results = df.apply(
        lambda row: classify_procedure_with_confidence(
            row.get("procedure_code_raw", None),
            row.get("country", "")
        ),
        axis=1
    )
    df["procedure_class"]              = results.apply(lambda x: x[0])
    df["procedure_mapping_confidence"] = results.apply(lambda x: x[1])

    # procedure_class_final: usa classe apenas se confiança for suficiente
    df["procedure_class_final"] = df.apply(
        lambda r: r["procedure_class"]
        if r["procedure_mapping_confidence"] in DC_ANALYSIS_MIN_CONFIDENCE
        else "UNCLASSIFIED",
        axis=1
    )

    # surgery_any: usa procedure_class_final (confiança suficiente)
    df["surgery_any"] = (
        df["procedure_class_final"].isin(["DC", "CRAN", "OTHER_CRAN"])
    ).astype("Int64")

    # Log de distribuição
    LOG.info(
        f"[CROSSWALK] procedure_class:\n"
        f"{df['procedure_class'].value_counts().to_string()}\n"
        f"  confidence:\n"
        f"{df['procedure_mapping_confidence'].value_counts().to_string()}"
    )

    # Percentual não classificável por país
    for country, grp in df.groupby("country"):
        pct_unc = (grp["procedure_class"] == "UNCLASSIFIED").mean() * 100
        if pct_unc > 60:
            LOG.warning(
                f"[CROSSWALK] {country}: {pct_unc:.1f}% procedimentos UNCLASSIFIED. "
                f"Crosswalk pode estar incompleto ou base não tem campo procedimental."
            )

    return df


print("✅  Bloco 10 (Crosswalk REVISADO) pronto.")


# ╔══════════════════════════════════════════════════════════╗
# ║  BLOCO 11 — Harmonização Multinacional (CDM)            ║
# ║  REVISADO: validação final do CDM                       ║
# ╚══════════════════════════════════════════════════════════╝

CDM_COLS = [c for c in CDM_SCHEMA.keys()]


def compute_hospital_volume(df: pd.DataFrame) -> pd.DataFrame:
    """
    Calcula volumes hospitalares em três níveis:

    1. hospital_volume_tbi_year:
       volume anual de internações adultas S06.x por hospital.
       Este é o volume principal para análise multinacional.

    2. hospital_volume_surgical_year:
       volume anual de cirurgias cranianas classificadas.

    3. hospital_volume_dc_cran_year:
       volume anual de DC/CRAN estrito.

    Mantém hospital_volume_year como alias compatível, definido pelo CONFIG.
    """
    df = df.copy()

    # Volume TCE geral: todos os S06.x adultos no CDM
    vol_tbi = (
        df.groupby(["hospital_id", "year"])
        .size()
        .reset_index(name="hospital_volume_tbi_year")
    )
    df = df.merge(vol_tbi, on=["hospital_id", "year"], how="left")

    # Volume cirúrgico craniano
    surg_mask = df["procedure_class_final"].isin(["DC", "CRAN", "OTHER_CRAN"])
    vol_surg = (
        df[surg_mask]
        .groupby(["hospital_id", "year"])
        .size()
        .reset_index(name="hospital_volume_surgical_year")
    )
    df = df.merge(vol_surg, on=["hospital_id", "year"], how="left")

    # Volume DC/CRAN estrito
    dc_cran_mask = df["procedure_class_final"].isin(["DC", "CRAN"])
    vol_dc = (
        df[dc_cran_mask]
        .groupby(["hospital_id", "year"])
        .size()
        .reset_index(name="hospital_volume_dc_cran_year")
    )
    df = df.merge(vol_dc, on=["hospital_id", "year"], how="left")

    for col in [
        "hospital_volume_tbi_year",
        "hospital_volume_surgical_year",
        "hospital_volume_dc_cran_year",
    ]:
        df[col] = df[col].fillna(0).astype("Int64")

    # Alias para compatibilidade com o restante do código
    definition = CONFIG.get("primary_volume_definition", "tbi")

    if definition == "surgical":
        df["hospital_volume_year"] = df["hospital_volume_surgical_year"]
    else:
        df["hospital_volume_year"] = df["hospital_volume_tbi_year"]

    df["hospital_volume_year"] = df["hospital_volume_year"].astype("Int64")

    return df


def compute_transfer_proxy(df: pd.DataFrame) -> pd.DataFrame:
    if "hospital_region" in df.columns and "residence_region" in df.columns:
        df["transfer_proxy"] = (
            df["hospital_region"].astype(str) != df["residence_region"].astype(str)
        ).astype("Int64")
    else:
        df["transfer_proxy"] = pd.NA
    return df


# --- INÍCIO DO NOVO TRECHO ---
def compute_hospital_capacity_score(df: pd.DataFrame) -> pd.DataFrame:
    # Ponto 7: Capacidade hospitalar isolada da gravidade do paciente (UTI)
    score = pd.Series(0, index=df.index)
    if "hospital_volume_year" in df.columns:
        score += (df["hospital_volume_year"] >= CONFIG["high_volume_threshold"]).astype(int)
    # Removido icu_any: UTI é desfecho/consumo do paciente, não proxy estrutural fixa.
    if "NIV_HIER" in df.columns:
        score += (df["NIV_HIER"].astype(str) == "1").astype(int)
    df["hospital_capacity_score"] = score.astype("Int64")
    return df
# --- FIM DO NOVO TRECHO ---


def validate_cdm(df: pd.DataFrame) -> List[str]:
    """
    Valida o CDM final. Retorna lista de alertas.
    Alertas são incluídos no relatório final.
    """
    alerts = []

    # Colunas obrigatórias ausentes
    for col, (role, dtype) in CDM_SCHEMA.items():
        if role == "REQUIRED":
            if col not in df.columns:
                alerts.append(f"CDM_MISSING_REQUIRED_COL: {col}")
            elif df[col].isna().mean() > 0.20:
                pct = df[col].isna().mean() * 100
                alerts.append(f"CDM_HIGH_MISSING: {col} = {pct:.1f}%")

    # Tipos inesperados
    if "death_in_hospital" in df.columns:
        unexpected = ~df["death_in_hospital"].isin([0, 1]) & df["death_in_hospital"].notna()
        if unexpected.sum() > 0:
            alerts.append(f"CDM_INVALID_VALUES: death_in_hospital ({unexpected.sum()} linhas)")

    # Proporção de procedimentos não classificáveis
    if "procedure_class" in df.columns:
        pct_unc = (df["procedure_class"] == "UNCLASSIFIED").mean() * 100
        if pct_unc > 50:
            alerts.append(f"CDM_HIGH_UNCLASSIFIED_PROC: {pct_unc:.1f}%")

    # Proporção de cirurgias com confiança insuficiente
    if "procedure_mapping_confidence" in df.columns:
        pct_low = df["procedure_mapping_confidence"].isin(["LOW", "UNVERIFIED", "NA"]).mean() * 100
        if pct_low > 40:
            alerts.append(f"CDM_LOW_PROC_CONFIDENCE: {pct_low:.1f}% com confiança LOW/UNVERIFIED")

    for a in alerts:
        LOG.warning(f"[CDM-ALERT] {a}")

    return alerts


def harmonize_all(
    country_dfs: Dict[str, Optional[pd.DataFrame]]
) -> Tuple[pd.DataFrame, List[str]]:
    """
    Concatena, aplica crosswalk, calcula variáveis derivadas.
    Retorna (df_cdm, cdm_alerts).
    """
    LOG.info("═" * 60)
    LOG.info("HARMONIZAÇÃO MULTINACIONAL")
    LOG.info("═" * 60)

    dfs = []
    for country, df in country_dfs.items():
        if df is None:
            LOG.warning(f"[HARM] {country}: sem dados.")
            continue
        df = finalize_country_df(df, country)
        dfs.append(df)

    if not dfs:
        raise RuntimeError("Nenhum país com dados disponíveis.")

    df_all = pd.concat(dfs, ignore_index=True)
    LOG.info(f"Pré-crosswalk: {len(df_all):,} | países: {df_all['country'].nunique()}")

    df_all = apply_crosswalk(df_all)
    df_all = compute_hospital_volume(df_all)
    # Quartis de volume por país, para evitar comparar hospitais brasileiros diretamente com hospitais menores de outro sistema
    df_all["volume_quartile"] = pd.NA

    for country, idx in df_all.groupby("country").groups.items():
        sub = df_all.loc[idx].copy()
        vals = sub["hospital_volume_year"].astype(float)

        try:
            df_all.loc[idx, "volume_quartile"] = pd.qcut(
                vals.rank(method="first"),
                q=4,
                labels=["Q1", "Q2", "Q3", "Q4"]
            ).astype(str)
        except Exception:
            df_all.loc[idx, "volume_quartile"] = pd.NA
    df_all = compute_transfer_proxy(df_all)
    df_all = compute_hospital_capacity_score(df_all)



    # Validação CDM
    cdm_alerts = validate_cdm(df_all)

    # Selecionar colunas
    final_cols = [c for c in CDM_COLS if c in df_all.columns]
    extras     = [c for c in df_all.columns if c not in CDM_COLS]
    df_cdm     = df_all[final_cols + extras].copy()

    save_parquet(df_cdm, DIRS["harmonized"] / "tce_harmonized_cdm.parquet", "CDM")
    save_csv_xlsx(quick_audit(df_cdm, "CDM"), DIRS["qc"] / "audit_cdm")

    LOG.info(f"CDM: {len(df_cdm):,} | {df_cdm['country'].value_counts().to_dict()}")
    return df_cdm, cdm_alerts


print("✅  Bloco 11 (Harmonização REVISADA) pronto.")


def build_main_cohort(df_cdm: pd.DataFrame) -> pd.DataFrame:
    """
    Coorte principal multinacional:
    todos os adultos com TCE S06.x hospitalizados.

    NÃO exige procedimento cirúrgico.
    Essa é a coorte certa para a análise multinacional principal.
    """
    LOG.info("COORTE PRINCIPAL MULTINACIONAL — TCE HOSPITALIZADO")
    df = df_cdm.copy()

    df = df[df["age"] >= CONFIG["min_age"]].copy()
    LOG.info(f"  >=18: {len(df):,}")

    df = df[df["dx_main"].astype(str).str.startswith("S06", na=False)].copy()
    LOG.info(f"  S06.x: {len(df):,}")

    if "year" in df.columns:
        df = df[df["year"].between(min(CONFIG["study_years"]), max(CONFIG["study_years"]))].copy()
    LOG.info(f"  No período: {len(df):,}")

    # Variáveis mínimas para análise principal
    required = ["hospital_id", "death_in_hospital", "los_days", "hospital_volume_year"]
    before = len(df)
    for col in required:
        if col in df.columns:
            df = df[df[col].notna()].copy()
    LOG.info(f"  Com variáveis principais completas: {before:,} → {len(df):,}")

    if len(df) == 0:
        raise RuntimeError("Coorte principal vazia. Verifique diagnóstico, idade, hospital_id, óbito e LOS.")

    save_parquet(df, DIRS["harmonized"] / "cohort_main.parquet", "Coorte principal TCE hospitalizado")
    LOG.info(f"COORTE PRINCIPAL MULTINACIONAL: {len(df):,}")
    return df


def build_surgical_cohort(df_cdm: pd.DataFrame) -> pd.DataFrame:
    """
    Coorte cirúrgica secundária:
    S06.x adulto + procedimento craniano classificado como DC/CRAN/OTHER_CRAN.
    """
    LOG.info("COORTE CIRÚRGICA SECUNDÁRIA")
    df = df_cdm.copy()

    df = df[df["age"] >= CONFIG["min_age"]].copy()
    df = df[df["dx_main"].astype(str).str.startswith("S06", na=False)].copy()

    allowed_countries = CONFIG.get(
        "surgical_analysis_countries",
        ["brasil", "mexico", "chile", "equador"]
    )
    df = df[df["country"].isin(allowed_countries)].copy()

    surg_mask = (
        df["procedure_class_final"].isin(["DC", "CRAN", "OTHER_CRAN"])
        & df["procedure_mapping_confidence"].isin(["HIGH", "MODERATE"])
    )

    df = df[surg_mask].copy()

    if "year" in df.columns:
        df = df[df["year"].between(min(CONFIG["study_years"]), max(CONFIG["study_years"]))].copy()

    save_parquet(df, DIRS["harmonized"] / "cohort_surgical.parquet", "Coorte cirúrgica")
    LOG.info(f"COORTE CIRÚRGICA: {len(df):,} | países: {df['country'].value_counts().to_dict() if len(df) else {}}")
    return df


def build_dc_subcohort(df_surg: pd.DataFrame) -> pd.DataFrame:
    """
    Subcoorte DC vs CRAN.
    Continua exploratória.
    Só deve incluir países com crosswalk cirúrgico validável.
    """
    LOG.info("SUBCOORTE DC vs CRANIOTOMIA [EXPLORATÓRIA]")

    if df_surg is None or df_surg.empty:
        LOG.warning("[SUBANALISE] Coorte cirúrgica vazia.")
        df_empty = pd.DataFrame()
        save_parquet(df_empty, DIRS["harmonized"] / "cohort_dc_cran.parquet", "Subcoorte DC/CRAN vazia")
        return df_empty

    allowed_countries = CONFIG.get("dc_cran_analysis_countries", ["brasil", "mexico"])

    df = df_surg[
        df_surg["procedure_class_final"].isin(["DC", "CRAN"])
        & df_surg["country"].isin(allowed_countries)
    ].copy()

    if df.empty:
        LOG.warning("[SUBANALISE] Subcoorte DC/CRAN vazia após restrição por países/códigos.")
        save_parquet(df, DIRS["harmonized"] / "cohort_dc_cran.parquet", "Subcoorte DC/CRAN vazia")
        return df

    df["is_dc"] = (df["procedure_class_final"] == "DC").astype("Int64")

    df["subanalysis_flag"] = (
        "EXPLORATORY: sem ajuste de indicação; bases administrativas "
        "sem Glasgow/PIC/Marshall; NÃO inferir causalidade."
    )

    LOG.info(
        f"Subcoorte DC/CRAN: {len(df):,} | "
        f"DC={int(df['is_dc'].sum())} | CRAN={int((df['is_dc']==0).sum())} | "
        f"países={df['country'].value_counts().to_dict()}"
    )

    save_parquet(df, DIRS["harmonized"] / "cohort_dc_cran.parquet", "Subcoorte DC/CRAN")
    return df


def build_cohorts(df_cdm: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    df_main = build_main_cohort(df_cdm)
    df_surg = build_surgical_cohort(df_cdm)
    df_dc   = build_dc_subcohort(df_surg)
    return df_main, df_surg, df_dc

# ╔══════════════════════════════════════════════════════════╗
# ║  BLOCO 13 — Tabelas Descritivas (com guards de N)       ║
# ╚══════════════════════════════════════════════════════════╝

def _safe_pct(series, value=None) -> str:
    """Calcula percentual com guard para N pequeno ou coluna ausente."""
    try:
        if value is not None:
            return f"{(series == value).sum() / len(series) * 100:.1f}"
        return f"{series.mean() * 100:.1f}"
    except Exception:
        return "N/A"


def _safe_median_iqr(series) -> str:
    try:
        s = pd.to_numeric(series, errors="coerce").dropna()
        if len(s) < 5:
            return "N<5"
        return f"{s.median():.0f} [{s.quantile(0.25):.0f}–{s.quantile(0.75):.0f}]"
    except Exception:
        return "N/A"


def table1_patient_characteristics(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for country in sorted(df["country"].dropna().unique()):
        sub = df[df["country"] == country]
        n   = len(sub)
        if n == 0:
            continue
        rows.append({
            "País":                       country.title(),
            "N":                          f"{n:,}",
            "Idade mediana [IQR]":        _safe_median_iqr(sub.get("age", pd.Series(dtype=float))),
            "Sexo M (%)":                 _safe_pct(sub["sex"], "M") if "sex" in sub.columns else "N/A",
            "Óbito intra-hospitalar (%)": _safe_pct(sub["death_in_hospital"], 1) if "death_in_hospital" in sub.columns else "N/A",
            "LOS mediana [IQR]":          _safe_median_iqr(sub.get("los_days", pd.Series(dtype=float))),
            "UTI (%)":                    _safe_pct(sub["icu_any"], 1) if "icu_any" in sub.columns else "N/A",
            "DC (%)":                     _safe_pct(sub["procedure_class_final"], "DC") if "procedure_class_final" in sub.columns else "N/A",
            "CRAN (%)":                   _safe_pct(sub["procedure_class_final"], "CRAN") if "procedure_class_final" in sub.columns else "N/A",
            "SDH (%)":                    _safe_pct(sub["trauma_subtype"], "SDH") if "trauma_subtype" in sub.columns else "N/A",
            "EDH (%)":                    _safe_pct(sub["trauma_subtype"], "EDH") if "trauma_subtype" in sub.columns else "N/A",
            "N hospitais":                f"{sub['hospital_id'].nunique():,}",
        })
    t1 = pd.DataFrame(rows)
    save_csv_xlsx(t1, DIRS["tables"] / "Tabela1_pacientes")
    LOG.info("Tabela 1 salva.")
    return t1


def table2_hospital_by_volume_quartile(df: pd.DataFrame) -> pd.DataFrame:
    if "volume_quartile" not in df.columns or df["volume_quartile"].isna().all():
        LOG.warning("Tabela 2: volume_quartile ausente.")
        return pd.DataFrame()
    try:
        hosp_stats = (
            df.groupby(["hospital_id", "volume_quartile"])
            .agg(
                n_cases       =("hospital_id", "count"),
                mortality_rate=("death_in_hospital", "mean"),
                los_median    =("los_days", "median"),
                icu_rate      =("icu_any", "mean"),
                dc_rate       =("procedure_class_final", lambda x: (x == "DC").mean()),
            ).reset_index()
        )
        t2 = (
            hosp_stats.groupby("volume_quartile")
            .agg(
                n_hospitals   =("hospital_id", "nunique"),
                total_casos   =("n_cases", "sum"),
                mort_media    =("mortality_rate", "mean"),
                los_mediana   =("los_median", "median"),
                icu_rate_media=("icu_rate", "mean"),
                dc_rate_media =("dc_rate", "mean"),
            ).reset_index()
        )
        save_csv_xlsx(t2, DIRS["tables"] / "Tabela2_hospitais_volume")
        return t2
    except Exception as exc:
        LOG.error(f"Tabela 2 falhou: {exc}")
        return pd.DataFrame()


def table3_mortality_los_by_country_volume(df: pd.DataFrame) -> pd.DataFrame:
    try:
        t3 = (
            df.groupby(["country", "volume_quartile"])
            .agg(
                n            =("hospital_id", "count"),
                mortality_pct=("death_in_hospital", lambda x: round(x.mean() * 100, 1)),
                los_median   =("los_days", "median"),
                los_q1       =("los_days", lambda x: x.quantile(0.25)),
                los_q3       =("los_days", lambda x: x.quantile(0.75)),
            ).reset_index()
        )
        save_csv_xlsx(t3, DIRS["tables"] / "Tabela3_mortalidade_LOS")
        return t3
    except Exception as exc:
        LOG.error(f"Tabela 3 falhou: {exc}")
        return pd.DataFrame()


def table6_dc_vs_cran(df_dc: pd.DataFrame) -> pd.DataFrame:
    if df_dc.empty:
        return pd.DataFrame()
    rows = []
    for country in sorted(df_dc["country"].dropna().unique()):
        sub = df_dc[df_dc["country"] == country]
        for proc_class in ["DC", "CRAN"]:
            s = sub[sub["procedure_class_final"] == proc_class]
            n = len(s)
            if n < 5:
                continue
            rows.append({
                "País":              country.title(),
                "Procedimento":      proc_class,
                "N":                 f"{n:,}",
                "Óbito (%)":         _safe_pct(s["death_in_hospital"], 1) if "death_in_hospital" in s.columns else "N/A",
                "LOS mediana [IQR]": _safe_median_iqr(s.get("los_days", pd.Series(dtype=float))),
                "UTI (%)":           _safe_pct(s["icu_any"], 1) if "icu_any" in s.columns else "N/A",
                "SDH (%)":           _safe_pct(s["trauma_subtype"], "SDH") if "trauma_subtype" in s.columns else "N/A",
                "NOTA":              "ANÁLISE EXPLORATÓRIA — sem ajuste de indicação",
            })
    t6 = pd.DataFrame(rows)
    save_csv_xlsx(t6, DIRS["tables"] / "Tabela6_DC_vs_CRAN_exploratoria")
    return t6


def run_all_tables(df_main, df_surg, df_dc):
    return {
        "t1": table1_patient_characteristics(df_main),              # TCE hospitalizado
        "t2": table2_hospital_by_volume_quartile(df_main),          # volume TCE hospitalar
        "t3": table3_mortality_los_by_country_volume(df_main),      # mortalidade/LOS multinacional
        "t6": table6_dc_vs_cran(df_dc),                             # subanálise cirúrgica exploratória
    }


print("✅  Bloco 13 (Tabelas REVISADAS) pronto.")


# ╔══════════════════════════════════════════════════════════╗
# ║  BLOCO 14 — Análises Estatísticas Principais            ║
# ║  ★ CORREÇÃO CRÍTICA: GEE logístico para desfecho binário║
# ╚══════════════════════════════════════════════════════════╝
#
#  PROBLEMA ANTERIOR:
#    fit_logistic_mixed() usava smf.mixedlm() para N grande.
#    smf.mixedlm() é modelo LINEAR misto (LMM) — INCORRETO para
#    desfecho binário (morte in-hospital: 0/1).
#
#  SOLUÇÃO IMPLEMENTADA:
#    GEE logístico (Generalized Estimating Equations) com:
#      - família binomial
#      - estrutura de correlação exchangeable (dentro do hospital)
#      - cluster = hospital_id
#
#  POR QUE GEE E NÃO GLMM?
#    • GEE é mais robusto computacionalmente para N grande (>10k)
#    • statsmodels tem implementação estável e bem testada
#    • GEE produz estimativas populacionais (population-averaged)
#      adequadas para estudos epidemiológicos de impacto de política
#    • GLMM logístico (statsmodels BinomialBayesMixedGLM) é instável
#      para datasets de dezenas de milhares de obs. com muitos clusters
#    • Beta e SE do GEE são diretamente usáveis na meta-análise
#
#  FALLBACK:
#    Se N < 200 ou GEE falhar: regressão logística simples (sem cluster).
#    O fallback é documentado no resultado com flag "model_type".
# ──────────────────────────────────────────────────────────────

def _check_covariates_availability(
    df: pd.DataFrame,
    covariates: List[str],
    country: str,
) -> List[str]:
    """
    Filtra covariáveis para incluir apenas as realmente disponíveis
    (coluna existe, não 100% NA, variância > 0).
    Impede erros silenciosos por covariável sem variação.
    """
    valid = []
    for cov in covariates:
        # Extrai nome da coluna de fórmulas tipo C(col)
        col_name = cov.replace("C(", "").replace(")", "").strip()

        if col_name not in df.columns:
            LOG.debug(f"[{country}] Covariável ausente: {cov}")
            continue
        if df[col_name].isna().all():
            LOG.debug(f"[{country}] Covariável 100% NA: {cov}")
            continue
        # Checar variância (categorical: >= 2 valores únicos não-NA)
        n_unique = df[col_name].dropna().nunique()
        if n_unique < 2:
            LOG.debug(f"[{country}] Covariável sem variação: {cov} (n_unique={n_unique})")
            continue
        valid.append(cov)

    if len(valid) < len(covariates):
        removed = set(covariates) - set(valid)
        LOG.info(f"[{country}] Covariáveis removidas por indisponibilidade: {removed}")
    return valid


def fit_gee_logistic(
    df: pd.DataFrame,
    outcome: str,
    exposure: str,
    covariates: List[str],
    cluster: str,
    country: str,
) -> Optional[Dict]:
    """
    GEE logístico para desfecho binário com cluster hospitalar.

    Estatisticamente correto para:
      - death_in_hospital (0/1) com cluster por hospital
      - estimativa population-averaged (adequada para epidemiologia)

    Fallback para regressão logística simples se N < 200 ou GEE falhar.

    Retorna dict com: beta, se, or, ci_low, ci_high, pval, model_type, n
    para uso direto na meta-análise de efeitos aleatórios.
    """
    # Preparar colunas
    cols_needed = [c for c in [outcome, exposure, cluster] + covariates
                   if c.replace("C(","").replace(")","") in df.columns or c in df.columns]

    sub = df[[c for c in [outcome, exposure, cluster]
              if c in df.columns]].copy()

    # Adicionar covariáveis disponíveis
    valid_covs = _check_covariates_availability(df, covariates, country)
    for cov in valid_covs:
        col = cov.replace("C(", "").replace(")", "")
        if col in df.columns:
            sub[col] = df[col].values

    # --- INÍCIO DO NOVO TRECHO ---
    sub = sub.dropna(subset=[outcome, exposure, cluster])
    sub = sub[sub[outcome].isin([0, 1])].copy()  # garantir binário

    # Correção: Converter Int64 (pandas) para float nativo (numpy) para o statsmodels não quebrar
    for col in sub.columns:
        if pd.api.types.is_numeric_dtype(sub[col]):
            sub[col] = sub[col].astype(float)

    n = len(sub)
# --- FIM DO NOVO TRECHO ---
    if n < 50:
        LOG.warning(f"[{country}] N={n} insuficiente para modelo {outcome}~{exposure}.")
        return None

    # Construir fórmula com covariáveis válidas
    formula_parts = [exposure] + valid_covs
    formula = f"{outcome} ~ {' + '.join(formula_parts)}"

    # --- INÍCIO DO NOVO TRECHO ---
    # ── GEE Logístico (método principal, N >= 200) ────────
    if n >= 200:
        try:
            model = smf.gee(
                formula,
                groups   = sub[cluster],
                data     = sub,
                family   = sm.families.Binomial(),
                cov_struct = sm.cov_struct.Exchangeable(),
            )
# --- FIM DO NOVO TRECHO ---
            result = model.fit()

            if exposure not in result.params.index:
                LOG.warning(f"[{country}] GEE: exposure '{exposure}' não estimado.")
                return None

            beta = result.params[exposure]
            se   = result.bse[exposure]
            pval = result.pvalues[exposure]

            LOG.info(f"[{country}] GEE logístico: OR={np.exp(beta):.3f}, p={pval:.4f}, N={n}")
            return {
                "country":    country,
                "outcome":    outcome,
                "exposure":   exposure,
                "n":          n,
                "beta":       round(beta, 4),
                "se":         round(se, 4),
                "or":         round(np.exp(beta), 3),
                "ci_low":     round(np.exp(beta - 1.96 * se), 3),
                "ci_high":    round(np.exp(beta + 1.96 * se), 3),
                "pval":       round(pval, 4),
                "model_type": "GEE_logistic_exchangeable",
            }

        except Exception as exc:
            LOG.warning(f"[{country}] GEE falhou: {exc}. Usando logística simples como fallback.")

    # ── Fallback: Regressão logística simples ─────────────
    try:
        model  = smf.logit(formula, data=sub).fit(disp=0, maxiter=200)

        if exposure not in model.params.index:
            LOG.warning(f"[{country}] Logit: exposure não estimado.")
            return None

        beta = model.params[exposure]
        se   = model.bse[exposure]
        pval = model.pvalues[exposure]

        LOG.info(f"[{country}] Logística simples (fallback): OR={np.exp(beta):.3f}, N={n}")
        return {
            "country":    country,
            "outcome":    outcome,
            "exposure":   exposure,
            "n":          n,
            "beta":       round(beta, 4),
            "se":         round(se, 4),
            "or":         round(np.exp(beta), 3),
            "ci_low":     round(np.exp(beta - 1.96 * se), 3),
            "ci_high":    round(np.exp(beta + 1.96 * se), 3),
            "pval":       round(pval, 4),
            "model_type": "logistic_simple_fallback",
        }
    except Exception as exc:
        LOG.error(f"[{country}] Modelo {outcome}~{exposure} falhou completamente: {exc}")
        return None


# --- INÍCIO DO NOVO TRECHO ---
def fit_gee_poisson_los(
    df: pd.DataFrame,
    exposure: str,
    covariates: List[str],
    country: str,
) -> Optional[Dict]:
    """GEE Poisson com cluster para LOS (Ponto 6 do Revisor)."""
    valid_covs = _check_covariates_availability(df, covariates, country)
    # Adicionando hospital_id para garantir a modelagem de cluster
    sub = df[["los_days", exposure, "hospital_id"]].copy()
    for cov in valid_covs:
        col = cov.replace("C(", "").replace(")", "")
        if col in df.columns:
            sub[col] = df[col].values

    sub = sub.dropna(subset=["los_days", exposure, "hospital_id"])
    sub = sub[sub["los_days"] > 0].copy()

    for col in sub.columns:
        if pd.api.types.is_numeric_dtype(sub[col]):
            sub[col] = sub[col].astype(float)

    n = len(sub)

    if n < 50:
        LOG.warning(f"[{country}] GEE Poisson: N={n} insuficiente.")
        return None

    formula = "los_days ~ " + exposure + ((" + " + " + ".join(valid_covs)) if valid_covs else "")
    try:
        # Usando GEE Poisson para resolver a correlação intra-hospitalar
        model = smf.gee(
            formula,
            groups=sub["hospital_id"],
            data=sub,
            family=sm.families.Poisson(),
            cov_struct=sm.cov_struct.Exchangeable()
        )
        result = model.fit()

        beta = result.params[exposure]
        se   = result.bse[exposure]
        pval = result.pvalues[exposure]

        return {
            "country":    country,
            "outcome":    "los_days",
            "exposure":   exposure,
            "n":          n,
            "beta":       round(beta, 4),
            "se":         round(se, 4),
            "irr":        round(np.exp(beta), 3),
            "ci_low":     round(np.exp(beta - 1.96 * se), 3),
            "ci_high":    round(np.exp(beta + 1.96 * se), 3),
            "pval":       round(pval, 4),
            "model_type": "GEE_poisson_exchangeable",
        }
    except Exception as exc:
        LOG.error(f"[{country}] GEE Poisson LOS falhou: {exc}")
        return None
# --- FIM DO NOVO TRECHO ---


def random_effects_meta_analysis(results: List[Dict], exposure: str) -> Dict:
    """Meta-análise DerSimonian-Laird (efeitos aleatórios)."""
    valid = [r for r in results if r is not None and "beta" in r and "se" in r]
    # --- INÍCIO DO NOVO TRECHO ---
    if len(valid) < 2:
        LOG.warning("Meta-análise: < 2 estudos válidos.")
        return {"pooled_or": None, "ci_low": None, "ci_high": None,
                "I2_pct": None, "tau2": None, "Q_pval": None, "n_studies": len(valid)}
# --- FIM DO NOVO TRECHO ---

    betas = np.array([r["beta"] for r in valid])
    ses   = np.array([r["se"]   for r in valid])
    w_fe  = 1 / ses**2

    beta_fe = np.sum(w_fe * betas) / np.sum(w_fe)
    Q       = np.sum(w_fe * (betas - beta_fe)**2)
    k       = len(valid)
    df_q    = k - 1

    c    = np.sum(w_fe) - np.sum(w_fe**2) / np.sum(w_fe)
    tau2 = max((Q - df_q) / c, 0)

    w_re    = 1 / (ses**2 + tau2)
    beta_re = np.sum(w_re * betas) / np.sum(w_re)
    se_re   = np.sqrt(1 / np.sum(w_re))
    I2      = max(0, (Q - df_q) / Q * 100) if Q > 0 else 0

    return {
        "exposure":     exposure,
        "n_studies":    k,
        "pooled_beta":  round(beta_re, 4),
        "pooled_or":    round(np.exp(beta_re), 3),
        "ci_low":       round(np.exp(beta_re - 1.96 * se_re), 3),
        "ci_high":      round(np.exp(beta_re + 1.96 * se_re), 3),
        "tau2":         round(tau2, 4),
        "I2_pct":       round(I2, 1),
        "Q":            round(Q, 2),
        "Q_pval":       round(1 - stats.chi2.cdf(Q, df_q), 4),
        "country_results": valid,
    }


def run_main_models(df_main: pd.DataFrame) -> Dict:
    LOG.info("═" * 60)
    LOG.info("MODELOS PRINCIPAIS MULTINACIONAIS (volume TCE hospitalar × mortalidade/LOS)")
    LOG.info("═" * 60)

    df_main = df_main.copy()
    volume_col = "hospital_volume_year"
    df_main["log_vol"] = np.log1p(df_main[volume_col].astype(float))

    COVS_BASE = ["age", "C(sex)", "C(trauma_subtype)", "C(year)"]

    results_mort, results_los = [], []

    for country in sorted(df_main["country"].dropna().unique()):
        sub = df_main[df_main["country"] == country].copy()
        if len(sub) < 100:
            LOG.warning(f"[{country}] N={len(sub)} — modelo ignorado.")
            continue

        r_mort = fit_gee_logistic(
            sub, "death_in_hospital", "log_vol",
            COVS_BASE, "hospital_id", country
        )
        r_los = fit_gee_poisson_los(sub, "log_vol", COVS_BASE, country)

        results_mort.append(r_mort)
        results_los.append(r_los)

    meta_mort = random_effects_meta_analysis(results_mort, "log_vol→death")
    meta_los  = random_effects_meta_analysis(results_los,  "log_vol→LOS")

    # Salvar
    t4 = pd.DataFrame([r for r in results_mort if r])
    t4_los = pd.DataFrame([r for r in results_los if r])
    save_csv_xlsx(t4,     DIRS["tables"] / "Tabela4_modelos_pais_morte")
    save_csv_xlsx(t4_los, DIRS["tables"] / "Tabela4b_modelos_pais_LOS")

    meta_df = pd.DataFrame([
        {"modelo": "Mortalidade (GEE logístico)",
         "pooled_OR_ou_IRR": meta_mort["pooled_or"],
         "CI_95": f"{meta_mort['ci_low']}–{meta_mort['ci_high']}",
         "I2_pct": meta_mort["I2_pct"], "tau2": meta_mort["tau2"],
         "Q_pval": meta_mort["Q_pval"], "n_estudos": meta_mort["n_studies"]},
        {"modelo": "LOS (GEE Poisson robusto)",
         "pooled_OR_ou_IRR": meta_los["pooled_or"],
         "CI_95": f"{meta_los['ci_low']}–{meta_los['ci_high']}",
         "I2_pct": meta_los["I2_pct"], "tau2": meta_los["tau2"],
         "Q_pval": meta_los["Q_pval"], "n_estudos": meta_los["n_studies"]},
    ])
    save_csv_xlsx(meta_df, DIRS["tables"] / "Tabela5_metaanalise")

    with open(DIRS["models"] / "meta_mortality.json", "w") as f:
        json.dump(meta_mort, f, indent=2, default=str)
    with open(DIRS["models"] / "meta_los.json", "w") as f:
        json.dump(meta_los, f, indent=2, default=str)

    LOG.info(
        f"Meta Mortalidade: OR={meta_mort['pooled_or']} "
        f"[{meta_mort['ci_low']}–{meta_mort['ci_high']}], I²={meta_mort['I2_pct']}%"
    )
    LOG.info(
        f"Meta LOS: IRR={meta_los['pooled_or']} "
        f"[{meta_los['ci_low']}–{meta_los['ci_high']}], I²={meta_los['I2_pct']}%"
    )

    return {
        "results_mort": results_mort,
        "results_los":  results_los,
        "meta_mort":    meta_mort,
        "meta_los":     meta_los,
    }


print("✅  Bloco 14 (GEE logístico CORRIGIDO) pronto.")

# ============================================================
#  PIPELINE TCE MULTINACIONAL — PARTE 4 REVISADA
#  Blocos 15–18 | Versão 1.1
#
#  CORREÇÕES APLICADAS:
#  • Bloco 15: sensibilidades usam fit_gee_logistic (não mixedlm)
#    - DC vs CRAN usa mesma lógica estatística da análise principal
#    - Guarda explícito de covariáveis disponíveis
#  • Bloco 16-17: guards para N pequeno, país ausente, coluna ausente
#    - Figuras não quebram se um país não tiver dados
#  • Bloco 18: relatório final muito mais completo
#    - Inclui missingness, % unclassified, alertas CDM, caminhos
# ============================================================


# ╔══════════════════════════════════════════════════════════╗
# ║  BLOCO 15 — Sensibilidades e Subanálises               ║
# ╚══════════════════════════════════════════════════════════╝

COVS_SENS = ["age", "C(sex)", "C(trauma_subtype)"]  # sem year para subset temporais


def _run_gee_by_country(
    df: pd.DataFrame,
    analysis_label: str,
    min_n: int = 50,
) -> List[Dict]:
    """
    Helper: roda GEE logístico por país para uma análise específica.
    Respeita disponibilidade de covariáveis por país.
    """
    results = []
    for country in sorted(df["country"].dropna().unique()):
        sub = df[df["country"] == country].copy()
        if len(sub) < min_n:
            LOG.info(f"[SENS-{analysis_label}] {country}: N={len(sub)} < {min_n}, ignorado.")
            continue
        r = fit_gee_logistic(
            sub, "death_in_hospital", "log_vol",
            COVS_SENS, "hospital_id", country
        )
        if r:
            r["sensitivity"] = analysis_label
            results.append(r)
    return results


def sensitivity_exclude_low_volume(df_main: pd.DataFrame) -> List[Dict]:
    LOG.info("[SENS] Excluindo Q1 de volume...")
    df = df_main[df_main["volume_quartile"].isin(["Q2", "Q3", "Q4"])].copy()
    df["log_vol"] = np.log1p(df["hospital_volume_year"].astype(float))
    return _run_gee_by_country(df, "excl_low_volume")


def sensitivity_restrict_icu(df_main: pd.DataFrame) -> List[Dict]:
    LOG.info("[SENS] Pacientes com UTI documentada...")
    if "icu_any" not in df_main.columns or df_main["icu_any"].isna().all():
        LOG.warning("[SENS] icu_any indisponível — sensibilidade UTI ignorada.")
        return []
    df = df_main[df_main["icu_any"] == 1].copy()
    df["log_vol"] = np.log1p(df["hospital_volume_year"].astype(float))
    return _run_gee_by_country(df, "icu_only")


def sensitivity_pre_post_pandemic(df_main: pd.DataFrame) -> List[Dict]:
    LOG.info("[SENS] Pré vs pós pandemia...")
    results = []
    for period, years in [
        ("pre_pandemic",  range(2015, 2020)),
        ("post_pandemic", range(2020, 2024)),
    ]:
        df = df_main[df_main["year"].isin(years)].copy()
        df["log_vol"] = np.log1p(df["hospital_volume_year"].astype(float))
        res = _run_gee_by_country(df, period)
        results.extend(res)
    return results


def sensitivity_brasil_only(df_main: pd.DataFrame) -> Optional[Dict]:
    LOG.info("[SENS] Brasil only...")
    sub = df_main[df_main["country"] == "brasil"].copy()
    if len(sub) < 100:
        LOG.warning("[SENS] Brasil: N insuficiente.")
        return None
    sub["log_vol"] = np.log1p(sub["hospital_volume_year"].astype(float))
    r = fit_gee_logistic(
        sub, "death_in_hospital", "log_vol",
        ["age", "C(sex)", "C(trauma_subtype)", "C(year)"],
        "hospital_id", "brasil_only"
    )
    if r:
        r["sensitivity"] = "brasil_only"
    return r


def sensitivity_robust_crosswalk_countries(df_main: pd.DataFrame) -> List[Dict]:
    """
    Sensibilidade: restringir a países com crosswalk procedimental robusto.
    'Robusto' = <= 40% UNCLASSIFIED na procedure_class.
    """
    LOG.info("[SENS] Países com crosswalk procedimental robusto...")
    robust_countries = []
    for country, grp in df_main.groupby("country"):
        if "procedure_class" in grp.columns:
            pct_unc = (grp["procedure_class"] == "UNCLASSIFIED").mean() * 100
            if pct_unc <= 40:
                robust_countries.append(country)
                LOG.info(f"  {country}: {pct_unc:.1f}% UNCLASSIFIED → incluído")
            else:
                LOG.info(f"  {country}: {pct_unc:.1f}% UNCLASSIFIED → excluído desta sensibilidade")

    if not robust_countries:
        LOG.warning("[SENS] Nenhum país com crosswalk robusto suficiente.")
        return []

    df = df_main[df_main["country"].isin(robust_countries)].copy()
    df["log_vol"] = np.log1p(df["hospital_volume_year"].astype(float))
    return _run_gee_by_country(df, "robust_crosswalk_only")


def dc_cran_subanalysis(df_dc: pd.DataFrame) -> List[Dict]:
    """
    ⚠  SUBANÁLISE EXPLORATÓRIA — DC vs Craniotomia.

    LIMITAÇÕES METODOLÓGICAS (embutidas no output):
    1. Bases administrativas sem Glasgow, PIC, Marshall → sem ajuste de indicação
    2. Viés de indicação esperado e não controlável nestas bases
    3. Resultado NÃO deve ser interpretado como efeito causal
    4. Restrito a mapeamentos com confiança HIGH ou MODERATE

    Usa GEE logístico — mesma lógica da análise principal.
    """
    LOG.info("[SUBANALISE DC/CRAN] EXPLORATÓRIA — veja nota metodológica")

    if df_dc.empty:
        LOG.warning("[SUBANALISE] Subcoorte DC/CRAN vazia.")
        return []

    results = []
    for country in sorted(df_dc["country"].dropna().unique()):
        sub = df_dc[df_dc["country"] == country].copy()

        if len(sub) < 50:
            LOG.info(f"  {country}: N={len(sub)} insuficiente.")
            continue
        if sub["is_dc"].nunique() < 2:
            LOG.info(f"  {country}: só um tipo de procedimento — análise ignorada.")
            continue

        # log_vol pode não existir na subcoorte — recalcular
        if "log_vol" not in sub.columns:
            sub["log_vol"] = np.log1p(sub["hospital_volume_year"].astype(float))

        r = fit_gee_logistic(
            sub, "death_in_hospital", "is_dc",
            ["age", "C(sex)", "C(trauma_subtype)", "log_vol"],
            "hospital_id", country
        )
        if r:
            r["sensitivity"]         = "dc_vs_cran_exploratory"
            r["methodological_note"] = (
                "EXPLORATÓRIA: sem ajuste de indicação. Viés de indicação "
                "esperado e não controlável. NÃO inferir causalidade."
            )
            r["proc_confidence_min"] = "HIGH/MODERATE only"
            results.append(r)
            LOG.info(
                f"  {country}: OR={r['or']} [{r['ci_low']}–{r['ci_high']}] "
                f"— ⚠  interpretação causal contraindicada"
            )

    return results


def run_all_sensitivity(df_main: pd.DataFrame, df_dc: pd.DataFrame) -> Dict:
    LOG.info("═" * 60)
    LOG.info("ANÁLISES DE SENSIBILIDADE")
    LOG.info("═" * 60)

    sens = {
        "excl_low_vol":     sensitivity_exclude_low_volume(df_main),
        "icu_only":         sensitivity_restrict_icu(df_main),
        "pre_post_pand":    sensitivity_pre_post_pandemic(df_main),
        "brasil_only":      sensitivity_brasil_only(df_main),
        "robust_crosswalk": sensitivity_robust_crosswalk_countries(df_main),
        "dc_vs_cran":       dc_cran_subanalysis(df_dc),
    }

    all_rows = []
    for key, val in sens.items():
        rows = val if isinstance(val, list) else ([val] if val else [])
        for r in rows:
            if r:
                r["analysis"] = key
                all_rows.append(r)

    if all_rows:
        df_sens = pd.DataFrame(all_rows)
        save_csv_xlsx(df_sens, DIRS["tables"] / "TabSup_sensibilidade")

    return sens


print("✅  Bloco 15 (Sensibilidades REVISADAS) pronto.")


# ╔══════════════════════════════════════════════════════════╗
# ║  BLOCOS 16–17 — Figuras com Guards de Robustez         ║
# ╚══════════════════════════════════════════════════════════╝

DPI  = CONFIG["fig_dpi"]
FDIR = DIRS["fig_main"]
SDIR = DIRS["fig_suppl"]

COUNTRY_COLORS = {
    "brasil":  "#1a6985",
    "mexico":  "#c0392b",
    "chile":   "#27ae60",
    "equador": "#e67e22",
}


def _fig_guard(func):
    """Decorator: captura exceções em figuras e loga sem quebrar pipeline."""
    def wrapper(*args, **kwargs):
        try:
            func(*args, **kwargs)
        except Exception as exc:
            LOG.error(f"[FIG-FAIL] {func.__name__}: {exc}\n{traceback.format_exc()}")
        finally:
            plt.close("all")
    return wrapper


# --- INÍCIO DO NOVO TRECHO ---
@_fig_guard
def fig1_cohort_flowchart(df_cdm, df_main, df_dc):
    n_cdm  = len(df_cdm)
    n_adult = len(df_cdm[(df_cdm["age"] >= 18) & (df_cdm["dx_main"].str.startswith("S06", na=False))]) if "age" in df_cdm.columns else "?"
    n_main = len(df_main)
    n_dc   = len(df_dc)

    fig, ax = plt.subplots(figsize=(9, 10))
    ax.axis("off")

    # Ponto 35: Nomenclatura ajustada e caixas refletindo as reais exclusões de coorte
    boxes = [
        (0.5, 0.88, f"Base Harmonizada (CDM)\n(Pós-Limpeza)\nN = {n_cdm:,}",    "#2c3e50"),
        (0.5, 0.65, f"Adultos ≥18 anos e TCE (S06)\nN = {n_adult if isinstance(n_adult, str) else f'{n_adult:,}'}",  "#2980b9"),
        (0.5, 0.42, f"Coorte Principal Cirúrgica\n(Confiança HIGH/MODERATE)\nN = {n_main:,}", "#16a085"),
        (0.5, 0.19, f"Subcoorte DC vs CRAN\n(Apenas países validados)\nN = {n_dc:,}",    "#c0392b"),
    ]

    for x, y, txt, color in boxes:
        ax.text(x, y, txt, ha="center", va="center", fontsize=10,
                bbox=dict(boxstyle="round,pad=0.6", facecolor=color, alpha=0.85, ec="white"),
                color="white", fontweight="bold")
        if y > 0.19:
            ax.annotate("", xy=(x, y - 0.08), xytext=(x, y - 0.04),
                        arrowprops=dict(arrowstyle="->", color="#555", lw=2))

    ax.set_title("Figura 1 — Fluxograma da Coorte", fontsize=13, fontweight="bold", pad=20)
    plt.tight_layout()
    plt.savefig(FDIR / "Figura1_fluxograma.png", dpi=DPI, bbox_inches="tight")
    LOG.info("Figura 1 salva.")
# --- FIM DO NOVO TRECHO ---


@_fig_guard
def fig3_volume_mortality_curve(df_main):
    if "hospital_volume_year" not in df_main.columns:
        LOG.warning("Figura 3: hospital_volume_year ausente.")
        return

    countries = [c for c in COUNTRY_COLORS if c in df_main["country"].unique()]
    if not countries:
        LOG.warning("Figura 3: nenhum país disponível.")
        return

    ncols = len(countries)
    fig, axes = plt.subplots(1, ncols, figsize=(5 * ncols, 5), sharey=False)
    if ncols == 1:
        axes = [axes]

    for ax, country in zip(axes, countries):
        sub = df_main[
            (df_main["country"] == country) &
            df_main["death_in_hospital"].notna() &
            df_main["hospital_volume_year"].notna()
        ].copy()

        if len(sub) < 20:
            ax.text(0.5, 0.5, f"{country.title()}\nN insuficiente",
                    ha="center", va="center", transform=ax.transAxes)
            ax.set_title(country.title())
            continue

        # --- INÍCIO DO NOVO TRECHO ---
        # Ponto 36: Trocando scatter binned/polyfit por curva de probabilidade logística real
        # usando Seaborn para representar o efeito marginal modelado.
        import seaborn as sns

        # Filtrar valores extremos de volume para melhor visualização (corte 95th perc.)
        vol_cap = sub["hospital_volume_year"].quantile(0.95)
        sub_plot = sub[sub["hospital_volume_year"] <= vol_cap].copy()
        sub_plot["death_in_hospital"] = sub_plot["death_in_hospital"].astype(float)

        sns.regplot(
            data=sub_plot,
            x="hospital_volume_year",
            y="death_in_hospital",
            logistic=True,
            n_boot=10,
            scatter_kws={'alpha': 0.05, 's': 15, 'color': '#7f8c8d'},
            line_kws={'color': COUNTRY_COLORS[country], 'lw': 2.5},
            ax=ax
        )

        ax.set_title(country.title(), fontsize=11, fontweight="bold")
        ax.set_xlabel("Volume anual (cirurgias TCE/hospital)", fontsize=9)
        ax.set_ylabel("Probabilidade predita de Óbito", fontsize=9)
        ax.set_ylim(-0.05, 1.05)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)

    fig.suptitle("Figura 3 — Curva descritiva não ajustada: volume × mortalidade", fontsize=13, fontweight="bold")
    plt.tight_layout()
    plt.savefig(FDIR / "Figura3_volume_mortalidade.png", dpi=DPI, bbox_inches="tight")
    LOG.info("Figura 3 salva.")
# --- FIM DO NOVO TRECHO ---


@_fig_guard
def fig4_forest_plot(model_results, title, filename):
    valid = [r for r in (model_results or []) if r is not None]
    if not valid:
        LOG.warning(f"Forest plot {filename}: sem resultados válidos.")
        return

    labels = [r["country"].title() for r in valid]
    ors    = [r.get("or", r.get("irr", 1.0)) for r in valid]
    ci_low = [r["ci_low"]  for r in valid]
    ci_hi  = [r["ci_high"] for r in valid]
    types  = [r.get("model_type", "") for r in valid]

    n_total = len(valid)
    y_pos   = list(range(n_total, 0, -1))

    fig, ax = plt.subplots(figsize=(9, max(4, n_total * 1.3 + 2)))

    for i, (label, OR, low, hi, mtype) in enumerate(zip(labels, ors, ci_low, ci_hi, types)):
        y     = y_pos[i]
        color = "#e74c3c" if "fallback" not in mtype else "#e67e22"
        ax.plot([low, hi], [y, y], "|-", color="#2c3e50", lw=2, ms=8)
        ax.scatter([OR], [y], s=80, color=color, zorder=5)
        ax.text(hi + 0.02, y, f"{OR:.2f} [{low:.2f}–{hi:.2f}]",
                va="center", fontsize=8)
        if "fallback" in mtype:
            ax.text(low - 0.02, y, "†", ha="right", fontsize=8, color="#e67e22")

    ax.axvline(1.0, color="#888", linestyle="--", lw=1)
    ax.set_yticks(y_pos)
    ax.set_yticklabels(labels, fontsize=10)
    ax.set_xlabel("OR / IRR (IC 95%)", fontsize=11)
    ax.set_title(title, fontsize=12, fontweight="bold")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    # Legenda de fallback
    if any("fallback" in t for t in types):
        ax.text(0.01, 0.01, "† Logística simples (fallback — N pequeno)",
                transform=ax.transAxes, fontsize=7, color="#e67e22")

    plt.tight_layout()
    plt.savefig(FDIR / filename, dpi=DPI, bbox_inches="tight")
    LOG.info(f"Forest plot: {filename}")


@_fig_guard
def fig5_temporal_dc_trend(df_dc):
    if df_dc.empty:
        LOG.warning("Figura 5: subcoorte vazia.")
        return
    if "procedure_class_final" not in df_dc.columns:
        LOG.warning("Figura 5: procedure_class_final ausente.")
        return

    yearly = (
        df_dc.groupby(["year", "country", "procedure_class_final"])
        .size().reset_index(name="n")
    )
    total_yr = yearly.groupby(["year", "country"])["n"].transform("sum")
    yearly["pct"] = yearly["n"] / total_yr * 100
    dc_yr = yearly[yearly["procedure_class_final"] == "DC"]

    if dc_yr.empty:
        LOG.warning("Figura 5: nenhum DC encontrado.")
        return

    fig, ax = plt.subplots(figsize=(10, 5))
    for country, grp in dc_yr.groupby("country"):
        if len(grp) < 2:
            continue
        grp_s = grp.sort_values("year")
        ax.plot(grp_s["year"], grp_s["pct"],
                marker="o", label=country.title(),
                color=COUNTRY_COLORS.get(country, "#333"), lw=2)

    ax.set_xlabel("Ano", fontsize=11)
    ax.set_ylabel("Proporção DC (% do total DC+CRAN)", fontsize=11)
    ax.set_title(
        "Figura 5 — Tendência Temporal: Craniectomia Descompressiva\n"
        "⚠  Restrito a procedimentos com confiança HIGH/MODERATE",
        fontsize=12, fontweight="bold"
    )
    ax.legend(fontsize=10)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    plt.tight_layout()
    plt.savefig(FDIR / "Figura5_tendencia_DC.png", dpi=DPI, bbox_inches="tight")
    LOG.info("Figura 5 salva.")


def run_main_figures(df_cdm, df_main, df_dc, model_output):
    LOG.info("FIGURAS PRINCIPAIS")
    fig1_cohort_flowchart(df_cdm, df_main, df_dc)
    fig3_volume_mortality_curve(df_main)
    if model_output:
        fig4_forest_plot(
            model_output.get("results_mort", []),
            "Figura 4 — Volume Hospitalar × Mortalidade (OR — GEE logístico)",
            "Figura4_forest_mortalidade.png"
        )
        fig4_forest_plot(
            model_output.get("results_los", []),
            "Figura 4b — Volume Hospitalar × LOS (IRR — NegBinom)",
            "Figura4b_forest_LOS.png"
        )
    fig5_temporal_dc_trend(df_dc)


@_fig_guard
def figS1_missingness(df_cdm):
    key_cols = ["age","sex","los_days","death_in_hospital",
                "icu_any","procedure_code_raw","hospital_region"]
    avail    = [c for c in key_cols if c in df_cdm.columns]
    countries = sorted(df_cdm["country"].dropna().unique())

    if not countries or not avail:
        return

    mat = np.zeros((len(avail), len(countries)))
    for j, country in enumerate(countries):
        sub = df_cdm[df_cdm["country"] == country]
        for i, col in enumerate(avail):
            mat[i, j] = sub[col].isna().mean() * 100

    fig, ax = plt.subplots(figsize=(max(6, len(countries) * 2), max(4, len(avail) * 0.8)))
    im = ax.imshow(mat, aspect="auto", cmap="YlOrRd", vmin=0, vmax=100)
    ax.set_xticks(range(len(countries)))
    ax.set_xticklabels([c.title() for c in countries], rotation=30, ha="right")
    ax.set_yticks(range(len(avail)))
    ax.set_yticklabels(avail, fontsize=9)

    for i in range(len(avail)):
        for j in range(len(countries)):
            ax.text(j, i, f"{mat[i,j]:.0f}%", ha="center", va="center",
                    fontsize=8, color="black" if mat[i,j] < 60 else "white")

    plt.colorbar(im, ax=ax, label="% missing")
    ax.set_title("FS1 — Missingness por Variável e País", fontsize=12, fontweight="bold")
    plt.tight_layout()
    plt.savefig(SDIR / "FigS1_missingness.png", dpi=DPI, bbox_inches="tight")
    LOG.info("FigS1 salva.")


@_fig_guard
def figS2_los_histogram(df_main):
    countries = sorted(df_main["country"].dropna().unique())
    if not countries:
        return

    ncols = len(countries)
    fig, axes = plt.subplots(1, ncols, figsize=(5 * ncols, 4), sharey=False)
    if ncols == 1:
        axes = [axes]

    for ax, country in zip(axes, countries):
        sub = df_main[
            (df_main["country"] == country) & df_main["los_days"].notna()
        ]
        if len(sub) < 5:
            ax.text(0.5, 0.5, "N insuficiente", ha="center", va="center",
                    transform=ax.transAxes)
            ax.set_title(country.title())
            continue

        vals = sub["los_days"].clip(upper=60)
        ax.hist(vals, bins=30, color=COUNTRY_COLORS.get(country, "#555"),
                edgecolor="white", alpha=0.85)
        ax.axvline(vals.median(), color="red", linestyle="--", lw=1.5,
                   label=f"Md={vals.median():.0f}d")
        ax.set_title(country.title(), fontsize=11, fontweight="bold")
        ax.set_xlabel("LOS (dias, cap=60)", fontsize=9)
        ax.set_ylabel("Frequência", fontsize=9)
        ax.legend(fontsize=8)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)

    fig.suptitle("FS2 — Distribuição do LOS", fontsize=13, fontweight="bold")
    plt.tight_layout()
    plt.savefig(SDIR / "FigS2_LOS_histogramas.png", dpi=DPI, bbox_inches="tight")
    LOG.info("FigS2 salva.")


@_fig_guard
def figS3_volume_distribution(df_main):
    if "hospital_volume_year" not in df_main.columns:
        return
    fig, ax = plt.subplots(figsize=(9, 5))
    plotted = False
    for country, grp in df_main.groupby("country"):
        vols = grp.groupby("hospital_id")["hospital_volume_year"].first().clip(upper=200)
        if len(vols) < 3:
            continue
        ax.hist(vols, bins=30, alpha=0.6, label=country.title(),
                color=COUNTRY_COLORS.get(country, "#333"), edgecolor="white")
        plotted = True

    if not plotted:
        LOG.warning("FigS3: sem dados suficientes.")
        return

    ax.set_xlabel("Volume anual (cirurgias/hospital)", fontsize=11)
    ax.set_ylabel("Número de hospitais", fontsize=11)
    ax.set_title("FS3 — Distribuição do Volume Hospitalar", fontsize=13, fontweight="bold")
    ax.legend(fontsize=10)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    plt.tight_layout()
    plt.savefig(SDIR / "FigS3_volume_hospitalar.png", dpi=DPI, bbox_inches="tight")
    LOG.info("FigS3 salva.")


@_fig_guard
def figS4_procedure_distribution(df_main):
    if "procedure_class_final" not in df_main.columns:
        return
    proc_counts = (
        df_main.groupby(["country", "procedure_class_final"])
        .size().reset_index(name="n")
    )
    total = proc_counts.groupby("country")["n"].transform("sum")
    proc_counts["pct"] = proc_counts["n"] / total * 100

    try:
        pivot = proc_counts.pivot(
            index="country", columns="procedure_class_final", values="pct"
        ).fillna(0)
        pivot.plot(kind="bar", stacked=True, figsize=(9, 5),
                   color=["#c0392b", "#2980b9", "#27ae60", "#888"])
        plt.title("FS4 — Distribuição de Procedimentos (procedure_class_final)",
                  fontsize=12, fontweight="bold")
        plt.xlabel("País", fontsize=11)
        plt.ylabel("Proporção (%)", fontsize=11)
        plt.xticks(rotation=30)
        plt.legend(title="Classe", fontsize=9)
        plt.tight_layout()
        plt.savefig(SDIR / "FigS4_procedimentos.png", dpi=DPI, bbox_inches="tight")
        LOG.info("FigS4 salva.")
    except Exception as exc:
        LOG.error(f"FigS4 falhou: {exc}")


def run_supplemental_figures(df_cdm, df_main):
    LOG.info("FIGURAS SUPLEMENTARES")
    figS1_missingness(df_cdm)
    figS2_los_histogram(df_main)
    figS3_volume_distribution(df_main)
    figS4_procedure_distribution(df_main)


print("✅  Blocos 16–17 (Figuras REVISADAS) prontos.")


# ╔══════════════════════════════════════════════════════════╗
# ║  BLOCO 18 — Exportação Final e Relatório Robusto       ║
# ╚══════════════════════════════════════════════════════════╝

def generate_final_report(
    country_dfs:  Dict[str, Optional[pd.DataFrame]],
    df_cdm:       pd.DataFrame,
    df_main:      pd.DataFrame,
    df_dc:        pd.DataFrame,
    model_output: Dict,
    cdm_alerts:   List[str],
    start_time:   float,
) -> Path:
    elapsed = round((time.time() - start_time) / 60, 1)
    ts      = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _h(title):
        return f"\n── {title} {'─'*(60-len(title)-4)}\n"

    lines = [
        "=" * 70,
        "  PIPELINE TCE MULTINACIONAL — RELATÓRIO DE EXECUÇÃO v1.1",
        f"  Gerado em: {ts}",
        f"  Tempo total: {elapsed} minutos",
        "=" * 70,
    ]

    # ── Registros brutos ──────────────────────────────────
    lines.append(_h("REGISTROS BRUTOS POR PAÍS"))
    for country, df in country_dfs.items():
        n     = len(df) if df is not None else 0
        years = sorted(df["year"].dropna().unique().tolist()) if (df is not None and "year" in df.columns) else []
        status = "OK" if df is not None else "SEM DADOS ⚠"
        lines.append(f"  {country:<12}: {n:>10,} registros | anos: {years} | {status}")

    # ── Após harmonização ─────────────────────────────────
    lines.append(_h("APÓS HARMONIZAÇÃO (CDM)"))
    lines.append(f"  Total CDM           : {len(df_cdm):>10,}")
    lines.append(f"  Países              : {df_cdm['country'].nunique()}")
    lines.append(f"  Hospitais únicos    : {df_cdm['hospital_id'].nunique():>10,}")
    lines.append(f"  Anos no CDM         : {sorted(df_cdm['year'].dropna().unique().astype(int).tolist())}")

    # ── Coorte principal ──────────────────────────────────
    lines.append(_h("COORTE PRINCIPAL"))
    lines.append(f"  N total             : {len(df_main):>10,}")
    for country, grp in df_main.groupby("country"):
        lines.append(f"    {country:<12}: {len(grp):>10,} | hospitais: {grp['hospital_id'].nunique()}")

    # ── Subcoorte DC/CRAN ─────────────────────────────────
    lines.append(_h("SUBCOORTE DC vs CRANIOTOMIA [EXPLORATÓRIA]"))
    lines.append(f"  N total             : {len(df_dc):>10,}")
    if not df_dc.empty and "procedure_class_final" in df_dc.columns:
        lines.append(f"  DC                  : {(df_dc['procedure_class_final']=='DC').sum():>10,}")
        lines.append(f"  Craniotomia         : {(df_dc['procedure_class_final']=='CRAN').sum():>10,}")

    # ── Mortalidade bruta ─────────────────────────────────
    lines.append(_h("MORTALIDADE BRUTA POR PAÍS (coorte principal)"))
    for country, grp in df_main.groupby("country"):
        if "death_in_hospital" in grp.columns and grp["death_in_hospital"].notna().sum() > 0:
            mort = grp["death_in_hospital"].mean() * 100
            lines.append(f"  {country:<12}: {mort:.1f}% (N={len(grp):,})")
        else:
            lines.append(f"  {country:<12}: N/A (death_in_hospital ausente)")

    # ── Procedimentos não classificáveis ──────────────────
    # --- INÍCIO DO NOVO TRECHO ---
    # ── Procedimentos não classificáveis ──────────────────
    # Ponto 28: Calculando sobre df_cdm (Pré-coorte) para não mascarar falhas do crosswalk
    lines.append(_h("% PROCEDIMENTOS UNCLASSIFIED POR PAÍS (Base Pré-Coorte)"))
    for country, grp in df_cdm.groupby("country"):
        if "procedure_class" in grp.columns:
            pct = (grp["procedure_class"] == "UNCLASSIFIED").mean() * 100
            warn = " ⚠  crosswalk altamente limitado" if pct > 40 else ""
            lines.append(f"  {country:<12}: {pct:.1f}%{warn}")
# --- FIM DO NOVO TRECHO ---

    # ── Missingness key variables ─────────────────────────
    lines.append(_h("MISSINGNESS NAS VARIÁVEIS-CHAVE (CDM)"))
    key_vars = ["age","sex","death_in_hospital","los_days",
                "icu_any","procedure_code_raw","hospital_region"]
    for var in key_vars:
        if var in df_cdm.columns:
            pct_miss = df_cdm[var].isna().mean() * 100
            flag = " ⚠" if pct_miss > 20 else ""
            lines.append(f"  {var:<30}: {pct_miss:.1f}%{flag}")

    # ── Alertas CDM ───────────────────────────────────────
    lines.append(_h("ALERTAS DE VALIDAÇÃO DO CDM"))
    if cdm_alerts:
        for a in cdm_alerts:
            lines.append(f"  ⚠  {a}")
    else:
        lines.append("  Nenhum alerta crítico.")

    # ── Alertas de ingestão parcial ───────────────────────
    lines.append(_h("ALERTAS DE INGESTÃO PARCIAL"))
    for country, df in country_dfs.items():
        if df is None:
            lines.append(f"  ⚠  {country}: dados NÃO carregados — análise excluída")
        elif len(df) < 1000:
            lines.append(f"  ⚠  {country}: N={len(df)} baixo — verificar download")

    # ── Meta-análise ──────────────────────────────────────
    if model_output:
        mm = model_output.get("meta_mort", {})
        ml = model_output.get("meta_los",  {})
        lines.append(_h("META-ANÁLISE (Efeitos Aleatórios, DerSimonian-Laird)"))
        lines.append(f"  Mortalidade")
        lines.append(f"    OR pooled (GEE)  : {mm.get('pooled_or','N/A')} [{mm.get('ci_low','?')}–{mm.get('ci_high','?')}]")
        lines.append(f"    I²               : {mm.get('I2_pct','N/A')}%  | tau²={mm.get('tau2','N/A')}  | Q p={mm.get('Q_pval','N/A')}")
        lines.append(f"    N estudos        : {mm.get('n_studies','N/A')}")
        lines.append(f"  LOS")
        lines.append(f"    IRR pooled (NB)  : {ml.get('pooled_or','N/A')} [{ml.get('ci_low','?')}–{ml.get('ci_high','?')}]")
        lines.append(f"    I²               : {ml.get('I2_pct','N/A')}%  | tau²={ml.get('tau2','N/A')}")

    # ── Arquivos gerados ──────────────────────────────────
    lines.append(_h("ARQUIVOS GERADOS"))
    outputs = {
        "CDM Parquet":          DIRS["harmonized"] / "tce_harmonized_cdm.parquet",
        "Coorte principal":     DIRS["harmonized"] / "cohort_main.parquet",
        "Subcoorte DC/CRAN":    DIRS["harmonized"] / "cohort_dc_cran.parquet",
        "Tabelas (CSV+XLSX)":   DIRS["tables"],
        "Figuras principais":   DIRS["fig_main"],
        "Figuras suplementares":DIRS["fig_suppl"],
        "Modelos (JSON)":       DIRS["models"],
        "Logs":                 DIRS["logs"],
        "QC":                   DIRS["qc"],
        "Crosswalk":            DIRS["metadata"] / CONFIG["proc_crosswalk_file"],
    }
    for label, path in outputs.items():
        exists = "✅" if Path(path).exists() else "❌"
        lines.append(f"  {exists} {label:<28}: {path}")

    # ── Notas metodológicas ───────────────────────────────
    lines.append(_h("NOTAS METODOLÓGICAS IMPORTANTES"))
    notes = [
        "1. Análise DC vs craniotomia é EXPLORATÓRIA — sem ajuste de indicação.",
        "2. Bases administrativas sem Glasgow, PIC, Marshall.",
        "3. Volume hospitalar é proxy de centralização, não medida causal.",
        "4. Comparação multinacional sujeita a heterogeneidade de codificação.",
        "5. Crosswalk procedimental requer validação clínica/manual antes de publicar.",
        "6. Modelo estatístico: GEE logístico (population-averaged) com cluster hospital.",
        "7. Fallback logístico simples usado quando N < 200 ou GEE instável (ver Tabela 4).",
        "8. Equador: participação primariamente descritiva — campos procedimentais limitados.",
    ]
    for n in notes:
        lines.append(f"  {n}")

    lines += ["", "=" * 70]

    report_path = DIRS["manuscript"] / "analysis_summary.txt"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    LOG.info(f"Relatório final: {report_path}")
    print("\n".join(lines))
    return report_path


# ╔══════════════════════════════════════════════════════════╗
# ║  ORQUESTRADOR MESTRE — Pipeline Completo               ║
# ╚══════════════════════════════════════════════════════════╝

def run_pipeline_complete(config: dict = CONFIG, dirs: dict = DIRS):
    """
    Ponto de entrada único.

    Uso:
        df_cdm, df_main, df_dc, model_output = run_pipeline_complete()
    """
    START = time.time()
    LOG.info("▶▶▶  PIPELINE TCE v1.1 INICIADO  ◀◀◀")

    # Ingestão
    df_brasil  = run_brasil_ingestion(config, dirs)
    df_mexico  = run_mexico_ingestion(config, dirs)
    df_chile   = run_chile_ingestion(config, dirs)
    df_equador = run_equador_ingestion(config, dirs)

    country_dfs = {
        "brasil":  df_brasil,
        "mexico":  df_mexico,
        "chile":   df_chile,
        "equador": df_equador,
    }

    _ = run_raw_audit(country_dfs)
    _ = build_crosswalk_table(dirs)

    df_cdm, cdm_alerts = harmonize_all(country_dfs)
    df_main, df_surg, df_dc = build_cohorts(df_cdm)
    tables = run_all_tables(df_main, df_surg, df_dc)

    model_output = {}
    if config["run_main_analysis"]:
        model_output = run_main_models(df_main)

    if config["run_sensitivity"]:
        run_all_sensitivity(df_main, df_dc)

    run_main_figures(df_cdm, df_main, df_dc, model_output)
    run_supplemental_figures(df_cdm, df_main)

    generate_final_report(
        country_dfs, df_cdm, df_main, df_dc,
        model_output, cdm_alerts, START
    )

    LOG.info("▶▶▶  PIPELINE CONCLUÍDO  ◀◀◀")
    return df_cdm, df_main, df_surg, df_dc, model_output


print("✅  Bloco 18 + Orquestrador REVISADOS prontos.")
print()
print("Para executar:")
print("    df_cdm, df_main, df_surg, df_dc, model_output = run_pipeline_complete()")

# ============================================================
# HOTFIX v1.2 INTEGRADO
# ============================================================
# -*- coding: utf-8 -*-
"""
TCE Multinational Pipeline — hotfix v1.2

Use AFTER running all definition cells of tce_datasus.py and BEFORE
run_pipeline_complete():

    %run /content/tce_pipeline_hotfix_v1_2.py
    apply_hotfix(globals())
    purge_derived_checkpoints(globals())
    df_cdm, df_main, df_surg, df_dc, model_output = run_pipeline_complete()

This patch intentionally does not invent missing country data. Chile/Ecuador are
included only when true discharge-level microdata with the required fields are
present in 00_raw/<country>.
"""

import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd

HOTFIX_VERSION = "1.2.0"


def _file_sha256(path: Path, chunk_size: int = 8 * 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _clean_text_series(s: pd.Series) -> pd.Series:
    out = s.astype("string").str.strip()
    return out.replace({
        "": pd.NA,
        "nan": pd.NA,
        "NaN": pd.NA,
        "NAN": pd.NA,
        "None": pd.NA,
        "NONE": pd.NA,
        "<NA>": pd.NA,
        "null": pd.NA,
        "NULL": pd.NA,
    })


def _normalize_year_series(s: pd.Series) -> pd.Series:
    """Normalize 17, 2017, 2017.0, 201701 and date-like strings to Int64 year."""
    raw = _clean_text_series(s)
    # Extract a four-digit year first, then fall back to numeric values.
    extracted = raw.str.extract(r"((?:19|20)\d{2})", expand=False)
    year = pd.to_numeric(extracted, errors="coerce")
    numeric = pd.to_numeric(raw.str.replace(r"\.0$", "", regex=True), errors="coerce")
    year = year.fillna(numeric)
    year = year.where(~year.between(0, 99), 2000 + year)
    year = year.where(~year.between(190001, 209912), np.floor(year / 100))
    year = year.where(year.between(1900, 2100))
    return year.astype("Int64")


def _normalize_month_series(s: pd.Series) -> pd.Series:
    month_names = {
        "ENE": 1, "ENERO": 1, "JAN": 1, "JANUARY": 1,
        "FEB": 2, "FEBRERO": 2, "FEBRUARY": 2,
        "MAR": 3, "MARZO": 3, "MARCH": 3,
        "ABR": 4, "ABRIL": 4, "APR": 4, "APRIL": 4,
        "MAY": 5, "MAYO": 5,
        "JUN": 6, "JUNIO": 6, "JUNE": 6,
        "JUL": 7, "JULIO": 7, "JULY": 7,
        "AGO": 8, "AGOSTO": 8, "AUG": 8, "AUGUST": 8,
        "SEP": 9, "SEPT": 9, "SEPTIEMBRE": 9, "SEPTEMBER": 9,
        "OCT": 10, "OCTUBRE": 10, "OCTOBER": 10,
        "NOV": 11, "NOVIEMBRE": 11, "NOVEMBER": 11,
        "DIC": 12, "DICIEMBRE": 12, "DEC": 12, "DECEMBER": 12,
    }
    raw = _clean_text_series(s).str.upper()
    num = pd.to_numeric(raw.str.replace(r"\.0$", "", regex=True), errors="coerce")
    num = num.fillna(raw.map(month_names))
    return num.where(num.between(1, 12)).astype("Int64")


def _jsonify_complex(value: Any) -> Any:
    if value is None or value is pd.NA:
        return pd.NA
    if isinstance(value, (list, tuple, set, dict)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return value


def sanitize_dataframe_for_parquet(df: pd.DataFrame) -> pd.DataFrame:
    """Return a copy with Arrow-safe, deterministic column dtypes."""
    out = df.copy()
    for col in out.columns:
        s = out[col]
        if pd.api.types.is_categorical_dtype(s.dtype):
            out[col] = s.astype("string")
            continue
        if pd.api.types.is_datetime64_any_dtype(s.dtype):
            out[col] = pd.to_datetime(s, errors="coerce")
            continue
        if s.dtype == "object":
            s = s.map(_jsonify_complex)
            # Administrative identifiers and clinical codes must remain text;
            # numeric coercion would destroy leading zeros.
            text_hints = ("id", "code", "codigo", "cod_", "dx", "diag", "cie",
                          "proc", "clues", "cnes", "source", "sex", "region")
            if any(hint in col.lower() for hint in text_hints):
                out[col] = _clean_text_series(s)
                continue
            non_null = s.dropna()
            if non_null.empty:
                out[col] = pd.Series(pd.NA, index=s.index, dtype="string")
                continue
            # Only coerce to numeric if every non-null value is numeric-like.
            numeric = pd.to_numeric(non_null, errors="coerce")
            if numeric.notna().all():
                converted = pd.to_numeric(s, errors="coerce")
                if np.all(np.isclose(converted.dropna() % 1, 0)):
                    out[col] = converted.astype("Int64")
                else:
                    out[col] = converted.astype("Float64")
            else:
                out[col] = _clean_text_series(s)
    return out


def apply_hotfix(ns: Dict[str, Any]) -> None:
    """Install the v1.2 replacements into a Colab notebook global namespace."""
    required = ["LOG", "CONFIG", "DIRS", "CDM_SCHEMA"]
    missing = [name for name in required if name not in ns]
    if missing:
        raise RuntimeError(
            "Run all definition cells of tce_datasus.py first. Missing globals: "
            + ", ".join(missing)
        )

    LOG = ns["LOG"]
    CONFIG = ns["CONFIG"]
    DIRS = ns["DIRS"]
    CDM_SCHEMA = ns["CDM_SCHEMA"]

    # Until a discharge-level Mexican procedure source/crosswalk is validated,
    # procedure analyses must remain Brazil-only. This does not affect the
    # multinational all-TBI volume/outcome analysis.
    CONFIG["surgical_analysis_countries"] = ["brasil"]
    CONFIG["dc_cran_analysis_countries"] = ["brasil"]
    CONFIG.setdefault("equador_age_unit_year_codes", ["1"])
    CONFIG.setdefault("equador_death_codes_by_year", {})
    CONFIG.setdefault("minimum_countries_for_pooling", 3)

    def save_parquet(df: pd.DataFrame, path: Path, desc: str = "") -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        safe = sanitize_dataframe_for_parquet(df)
        try:
            safe.to_parquet(path, index=False, engine="pyarrow", compression="snappy")
        except Exception as exc:
            diagnostics = []
            for col in safe.columns:
                if safe[col].dtype == "object":
                    types = safe[col].dropna().map(lambda x: type(x).__name__).value_counts().to_dict()
                    if len(types) > 1:
                        diagnostics.append(f"{col}={types}")
            LOG.error(f"[SAVE-PQ-FAIL] {path}: {exc}; mixed object columns: {diagnostics[:20]}")
            raise
        LOG.info(f"[SAVE-PQ] {desc or path.name}: {len(safe):,} linhas")

    def quick_audit(df: pd.DataFrame, label: str) -> pd.DataFrame:
        n = len(df)
        denom = max(n, 1)
        audit = pd.DataFrame({
            "column": df.columns,
            "dtype": [str(df[c].dtype) for c in df.columns],
            "n_non_null": [int(df[c].notna().sum()) for c in df.columns],
            "pct_null": [round(df[c].isna().sum() / denom * 100, 2) for c in df.columns],
            "n_unique": [int(df[c].nunique(dropna=True)) for c in df.columns],
        })
        max_missing = float(audit["pct_null"].max()) if len(audit) else 100.0
        LOG.info(f"[AUDIT] {label}: {n:,} linhas, {df.shape[1]} cols | missing máx = {max_missing:.1f}%")
        return audit

    def _candidate_files_for_year(raw_dir: Path, year: int) -> List[Path]:
        exts = {
            ".csv", ".txt", ".tsv", ".sav", ".xlsx", ".xls", ".ods",
            ".dbf", ".parquet", ".zip", ".7z", ".rar",
        }
        raw_dir = Path(raw_dir)
        candidates: List[Path] = []
        for p in raw_dir.rglob("*"):
            if not p.is_file() or p.suffix.lower() not in exts:
                continue
            rel_parts = [part.lower() for part in p.relative_to(raw_dir).parts[:-1]]
            if any(part.startswith("extracted_") for part in rel_parts):
                continue
            if any(part in {"__macosx", ".ipynb_checkpoints"} for part in rel_parts):
                continue
            if ns["year_matches_path"](str(p), year):
                candidates.append(p)
        return sorted(set(candidates), key=lambda p: str(p).lower())

    def _ensure_7z() -> Optional[str]:
        exe = shutil.which("7z") or shutil.which("7zz")
        if exe:
            return exe
        try:
            subprocess.run(
                ["apt-get", "update", "-qq"],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            subprocess.run(
                ["apt-get", "install", "-y", "-qq", "p7zip-full"],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as exc:
            LOG.warning(f"[ARCHIVE] Não foi possível instalar p7zip: {exc}")
            return None
        return shutil.which("7z") or shutil.which("7zz")

    def _extract_zip_to_folder(zip_path: Path, country: str, year: int) -> List[Path]:
        zip_path = Path(zip_path)
        dest_dir = zip_path.parent / f"extracted_{year}_{zip_path.stem}"
        dest_dir.mkdir(parents=True, exist_ok=True)
        valid_exts = {".csv", ".txt", ".tsv", ".sav", ".xlsx", ".xls", ".dbf", ".parquet"}

        try:
            if zip_path.suffix.lower() == ".zip":
                with zipfile.ZipFile(zip_path, "r") as zf:
                    zf.extractall(dest_dir)
            else:
                raise zipfile.BadZipFile("archive requires 7z")
        except Exception as exc:
            exe = _ensure_7z()
            if not exe:
                LOG.error(f"[{country.upper()}] Arquivo não extraído {zip_path.name}: {exc}")
                return []
            cmd = [exe, "x", "-y", f"-o{dest_dir}", str(zip_path)]
            ret = subprocess.run(cmd, capture_output=True, text=True)
            if ret.returncode != 0:
                LOG.error(f"[{country.upper()}] 7z falhou {zip_path.name}: {ret.stderr[-800:]}")
                return []

        files = [
            p for p in dest_dir.rglob("*")
            if p.is_file() and p.suffix.lower() in valid_exts
        ]
        LOG.info(f"[ARCHIVE] {len(files)} arquivo(s) extraído(s) de {zip_path.name}")
        return files

    def _detect_separator(path: Path, encoding: str) -> str:
        with path.open("r", encoding=encoding, errors="replace") as f:
            sample = "".join([f.readline() for _ in range(5)])
        candidates = ["|", ";", ",", "\t"]
        counts = {sep: sample.count(sep) for sep in candidates}
        return max(counts, key=counts.get)

    def _read_tabular_file(fpath: Path, country: str) -> Optional[pd.DataFrame]:
        fpath = Path(fpath)
        suffix = fpath.suffix.lower()
        try:
            if suffix == ".parquet":
                df = pd.read_parquet(fpath).astype("string")
                LOG.info(f"[{country.upper()}] Parquet lido: {fpath.name} ({len(df):,} linhas)")
                return df
            if suffix == ".sav":
                import pyreadstat
                df, _ = pyreadstat.read_sav(str(fpath), apply_value_formats=False)
                df = df.astype("string")
                LOG.info(f"[{country.upper()}] SAV lido: {fpath.name} ({len(df):,} linhas)")
                return df
            if suffix in {".xlsx", ".xls", ".ods"}:
                df = pd.read_excel(fpath, dtype=str)
                LOG.info(f"[{country.upper()}] Excel lido: {fpath.name} ({len(df):,} linhas)")
                return df
            if suffix == ".dbf":
                from simpledbf import Dbf5
                df = Dbf5(str(fpath), codec="latin-1").to_dataframe().astype("string")
                LOG.info(f"[{country.upper()}] DBF lido: {fpath.name} ({len(df):,} linhas)")
                return df
            if suffix in {".csv", ".txt", ".tsv"}:
                for enc in ["utf-8-sig", "utf-8", "cp1252", "latin-1"]:
                    try:
                        sep = _detect_separator(fpath, enc)
                        df = pd.read_csv(
                            fpath,
                            encoding=enc,
                            encoding_errors="replace",
                            dtype=str,
                            low_memory=False,
                            sep=sep,
                            on_bad_lines="skip",
                        )
                        if df.shape[1] > 1:
                            LOG.info(
                                f"[{country.upper()}] Texto lido: {fpath.name} "
                                f"({len(df):,} linhas, sep={sep!r}, enc={enc})"
                            )
                            return df
                    except Exception:
                        continue
                # Last-resort flexible parser for malformed historical files.
                try:
                    df = pd.read_csv(
                        fpath,
                        sep=None,
                        engine="python",
                        dtype=str,
                        encoding="latin-1",
                        encoding_errors="replace",
                        on_bad_lines="skip",
                    )
                    if df.shape[1] > 1:
                        LOG.warning(f"[{country.upper()}] Leitura lenta/fallback: {fpath.name} ({len(df):,})")
                        return df
                except Exception:
                    pass
        except Exception as exc:
            LOG.error(f"[{country.upper()}] Falha ao ler {fpath.name}: {exc}")
            return None
        LOG.error(f"[{country.upper()}] Não foi possível ler: {fpath.name}")
        return None

    def ingest_country_year_generic(
        country: str,
        year: int,
        url: Optional[str],
        raw_dir: Path,
        zip_filename: str,
        csv_patterns: List[str] = ["*.csv", "*.CSV"],
        sav_support: bool = False,
    ) -> List[pd.DataFrame]:
        del csv_patterns, sav_support  # compatibility-only parameters
        raw_dir = Path(raw_dir)
        candidates = _candidate_files_for_year(raw_dir, year)

        if not candidates and url:
            archive_path = raw_dir / zip_filename
            if not ns["file_exists_ok"](archive_path):
                ok = ns["download_file"](url, archive_path, desc=f"{country.title()} {year}")
                if not ok:
                    ns["manual_upload_instructions"](country, year, raw_dir)
                    return []
            candidates = [archive_path]

        if not candidates:
            LOG.warning(f"[{country.upper()}] {year}: sem arquivo local validado.")
            ns["manual_upload_instructions"](country, year, raw_dir)
            return []

        LOG.info(f"[{country.upper()}] {year}: {len(candidates)} fonte(s) candidata(s).")
        expanded: List[Path] = []
        for path in candidates:
            if path.suffix.lower() in {".zip", ".7z", ".rar"}:
                expanded.extend(_extract_zip_to_folder(path, country, year))
            else:
                expanded.append(path)

        # Deduplicate source files by content hash BEFORE reading. This prevents
        # reading the same CSV once directly and once through an archive.
        unique_files: List[Tuple[Path, str]] = []
        seen_hashes: Dict[str, Path] = {}
        for path in expanded:
            try:
                digest = _file_sha256(path)
            except Exception as exc:
                LOG.warning(f"[{country.upper()}] hash falhou {path.name}: {exc}")
                digest = str(path.resolve())
            if digest in seen_hashes:
                LOG.info(
                    f"[{country.upper()}] fonte duplicada ignorada: {path.name} "
                    f"(igual a {seen_hashes[digest].name})"
                )
                continue
            seen_hashes[digest] = path
            unique_files.append((path, digest))

        dfs: List[pd.DataFrame] = []
        for path, digest in unique_files:
            df = _read_tabular_file(path, country)
            if df is None or df.empty:
                continue
            df["_source_file"] = str(path)
            df["_source_sha256"] = digest
            df["_source_year"] = year
            dfs.append(df)
        return dfs

    def _microdata_gate(
        df: pd.DataFrame,
        country: str,
        year: int,
        source_file: str,
        require_hospital: bool = True,
    ) -> bool:
        required = {"dx_main", "age", "sex_raw"}
        if require_hospital:
            required.add("hospital_id_raw")
        missing = sorted(required - set(df.columns))
        if missing:
            LOG.info(
                f"[{country.upper()}] {year}: rejeitado (não é microdado analítico); "
                f"faltam {missing}: {Path(source_file).name}"
            )
            return False
        if len(df) < 100:
            LOG.info(
                f"[{country.upper()}] {year}: rejeitado por granularidade/tamanho "
                f"(N={len(df)}): {Path(source_file).name}"
            )
            return False
        return True


    def auto_collect_country_sources(country: str, years: List[int], raw_dir: Path) -> None:
        """Manifest-first collection: do not scrape broad portals into 00_raw."""
        raw_dir = Path(raw_dir)
        available = {year: bool(_candidate_files_for_year(raw_dir, year)) for year in years}
        missing_years = [year for year, ok in available.items() if not ok]
        if not missing_years:
            LOG.info(f"[{country.upper()}-SOURCE] arquivos locais presentes para todos os anos; descoberta web ignorada.")
            return
        LOG.warning(
            f"[{country.upper()}-SOURCE] anos sem arquivo local validado: {missing_years}. "
            "Coleta automática ampla foi desativada porque misturava microdados, tabelas agregadas e metadados. "
            "Preencha country_source_manifest.csv e coloque somente a base oficial paciente-a-paciente em 00_raw."
        )

    def ingest_mexico(years: List[int], raw_dir: Path, inter_dir: Path) -> Optional[pd.DataFrame]:
        LOG.info("═" * 60)
        LOG.info("INGESTÃO MÉXICO (SAEH/DGIS) — HOTFIX v1.2")
        LOG.info("═" * 60)
        ck = Path(inter_dir) / "mexico_raw.parquet"
        if ns["file_exists_ok"](ck):
            LOG.info(f"[CHECKPOINT-MX] {ck}")
            return pd.read_parquet(ck)

        frames: List[pd.DataFrame] = []
        for year in years:
            dfs = ingest_country_year_generic(
                "mexico", year, ns["MEXICO_URLS"].get(year), Path(raw_dir), f"SAEH_{year}.zip"
            )
            for raw in dfs:
                source_file = str(raw["_source_file"].iloc[0])
                df = ns["standardize_columns_by_alias"](raw, ns["MEXICO_ALIASES"], "mexico")
                if not _microdata_gate(df, "mexico", year, source_file, require_hospital=True):
                    continue
                df["procedure_code_raw"] = ns["combine_procedure_columns"](
                    df, ns["MEXICO_PROC_ALIASES"]
                )
                df = ns["_apply_s06_filter"](df, "dx_main", "mexico", year)
                if df.empty:
                    continue

                if "year" in df.columns and df["year"].notna().any():
                    normalized_year = _normalize_year_series(df["year"])
                    observed = sorted(normalized_year.dropna().astype(int).unique().tolist())
                    before = len(df)
                    if year in observed:
                        df = df[normalized_year == year].copy()
                        df["year"] = year
                        LOG.info(f"[MEXICO] {year}: ano normalizado {observed}; {before:,} → {len(df):,}")
                    else:
                        LOG.error(
                            f"[MEXICO] {year}: ano-alvo ausente após normalização; "
                            f"observados={observed[:20]}. Fonte em quarentena: {Path(source_file).name}"
                        )
                        continue
                else:
                    df["year"] = year

                if "month" in df.columns:
                    df["month"] = _normalize_month_series(df["month"])
                df["country"] = "mexico"
                df["source"] = "SAEH-DGIS"
                frames.append(df)

        if not frames:
            LOG.error("[MX] Nenhum microdado mexicano válido carregado.")
            return None

        df_mx = pd.concat(frames, ignore_index=True, sort=False)
        # No discharge-level deduplication is performed here. Duplicate source
        # files were already removed by SHA-256 before reading.
        LOG.info(f"[MEXICO] Registros após deduplicação de fontes: {len(df_mx):,}")
        save_parquet(df_mx, ck, "México raw S06")
        return df_mx

    def ingest_chile(years: List[int], raw_dir: Path, inter_dir: Path) -> Optional[pd.DataFrame]:
        LOG.info("═" * 60)
        LOG.info("INGESTÃO CHILE (DEIS/MINSAL) — HOTFIX v1.2")
        LOG.info("═" * 60)
        ck = Path(inter_dir) / "chile_raw.parquet"
        if ns["file_exists_ok"](ck):
            df_ck = pd.read_parquet(ck)
            return df_ck if not df_ck.empty else None

        bad_tokens = {
            "base de establecimientos", "base_establecimiento", "diccionario",
            "esquema", "formulario", "ficha", "urgencia", "remsa", "remasep",
            "estadisticaegresos", "egresos segun servicios",
        }
        frames: List[pd.DataFrame] = []
        for year in years:
            dfs = ingest_country_year_generic(
                "chile", year, ns["CHILE_URLS"].get(year), Path(raw_dir), f"Egresos_Chile_{year}.zip"
            )
            for raw in dfs:
                source_file = str(raw["_source_file"].iloc[0])
                low = source_file.lower().replace("_", " ")
                if any(token in low for token in bad_tokens):
                    LOG.info(f"[CHILE] {year}: produto agregado/metadado ignorado: {Path(source_file).name}")
                    continue
                df = ns["standardize_columns_by_alias"](raw, ns["CHILE_ALIASES"], "chile")
                if not _microdata_gate(df, "chile", year, source_file, require_hospital=True):
                    continue
                if not ({"los_days", "discharge_condition"} & set(df.columns)):
                    LOG.info(f"[CHILE] {year}: sem LOS nem condição de alta: {Path(source_file).name}")
                    continue
                df["procedure_code_raw"] = ns["combine_procedure_columns"](
                    df, ns["CHILE_PROC_ALIASES"]
                )
                df = ns["_apply_s06_filter"](df, "dx_main", "chile", year)
                if df.empty:
                    continue
                df["year"] = year
                df["country"] = "chile"
                df["source"] = "DEIS-MINSAL"
                frames.append(df)

        if not frames:
            LOG.warning("[CL] Nenhum microdado DEIS paciente-a-paciente válido encontrado.")
            return None
        df_cl = pd.concat(frames, ignore_index=True, sort=False)
        save_parquet(df_cl, ck, "Chile raw S06")
        return df_cl

    def ingest_equador(years: List[int], raw_dir: Path, inter_dir: Path) -> Optional[pd.DataFrame]:
        LOG.info("═" * 60)
        LOG.info("INGESTÃO EQUADOR (INEC-EH) — HOTFIX v1.2")
        LOG.info("═" * 60)
        ck = Path(inter_dir) / "equador_raw.parquet"
        if ns["file_exists_ok"](ck):
            df_ck = pd.read_parquet(ck)
            return df_ck if not df_ck.empty else None

        frames: List[pd.DataFrame] = []
        for year in years:
            dfs = ingest_country_year_generic(
                "equador", year, ns["EQUADOR_URLS"].get(year), Path(raw_dir), f"INEC_EH_{year}.zip"
            )
            for raw in dfs:
                source_file = str(raw["_source_file"].iloc[0])
                df = ns["standardize_columns_by_alias"](raw, ns["EQUADOR_ALIASES"], "equador")
                if not _microdata_gate(df, "equador", year, source_file, require_hospital=True):
                    continue
                if not ({"los_days", "discharge_condition"} & set(df.columns)):
                    LOG.info(f"[EQUADOR] {year}: sem LOS nem condição de alta: {Path(source_file).name}")
                    continue
                df["procedure_code_raw"] = ns["combine_procedure_columns"](
                    df, ns["EQUADOR_PROC_ALIASES"]
                )
                df = ns["_apply_s06_filter"](df, "dx_main", "equador", year)
                if df.empty:
                    continue
                df["year"] = year
                df["country"] = "equador"
                df["source"] = "INEC-EH"
                frames.append(df)

        if not frames:
            LOG.warning("[EC] Nenhum microdado INEC paciente-a-paciente válido encontrado.")
            return None
        df_ec = pd.concat(frames, ignore_index=True, sort=False)
        save_parquet(df_ec, ck, "Equador raw S06")
        return df_ec

    def clean_standardize_equador(df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        if "age" not in df.columns:
            return pd.DataFrame()
        df["age"] = pd.to_numeric(df["age"], errors="coerce").astype("Int64")
        if "age_unit" in df.columns:
            valid_units = {str(x) for x in CONFIG.get("equador_age_unit_year_codes", ["1"])}
            df.loc[~_clean_text_series(df["age_unit"]).isin(valid_units), "age"] = pd.NA

        if "sex_raw" in df.columns:
            x = _clean_text_series(df["sex_raw"]).str.upper()
            df["sex"] = x.map({"1": "M", "2": "F", "M": "M", "F": "F", "HOMBRE": "M", "MUJER": "F"}).fillna("unknown")
        else:
            df["sex"] = "unknown"

        if "los_days" in df.columns:
            df["los_days"] = pd.to_numeric(df["los_days"], errors="coerce").astype("Int64")
        else:
            df["los_days"] = pd.Series(pd.NA, index=df.index, dtype="Int64")

        # Numeric discharge codes must be validated from each year's official
        # data dictionary. Text labels can be mapped safely.
        df["death_in_hospital"] = pd.Series(pd.NA, index=df.index, dtype="Int64")
        if "discharge_condition" in df.columns:
            cond = _clean_text_series(df["discharge_condition"]).str.upper()
            textual_death = cond.str.contains(r"FALLEC|DEFUNC|MUERTE", na=False)
            df.loc[textual_death, "death_in_hospital"] = 1
            textual_alive = cond.str.contains(r"ALTA|VIVO|VIVA|TRASLADO", na=False)
            df.loc[textual_alive & ~textual_death, "death_in_hospital"] = 0
            code_maps = CONFIG.get("equador_death_codes_by_year", {})
            for year, codes in code_maps.items():
                mask_year = pd.to_numeric(df.get("year"), errors="coerce") == int(year)
                codes = {str(code).strip().upper() for code in codes}
                numeric_known = mask_year & cond.isin(codes)
                df.loc[numeric_known, "death_in_hospital"] = 1
            unresolved_numeric = cond.str.fullmatch(r"\d+", na=False) & df["death_in_hospital"].isna()
            if unresolved_numeric.any():
                LOG.warning(
                    "[EC] Condição de alta numérica não mapeada. Óbito mantido NA até validar dicionário por ano."
                )

        if "hospital_id_raw" in df.columns:
            hid = _clean_text_series(df["hospital_id_raw"])
            df["hospital_id"] = hid.map(lambda x: f"EC_{x}" if pd.notna(x) else pd.NA).astype("string")
        else:
            df["hospital_id"] = pd.NA
        if "dx_main" in df.columns:
            df["dx_main"] = _clean_text_series(df["dx_main"]).str.upper().str.replace(".", "", regex=False)

        for col in ["icu_any", "icu_days", "cost_local_currency", "urgent_admission"]:
            if col not in df.columns:
                df[col] = pd.NA
        before = len(df)
        df = df[df["age"] >= CONFIG["min_age"]].copy()
        LOG.info(f"[EC] >=18 anos: {before:,} → {len(df):,}")
        return df

    def finalize_country_df(df: pd.DataFrame, country: str) -> pd.DataFrame:
        df = ns["apply_plausibility_filters"](df.copy(), country)
        # No heuristic patient deduplication.
        for col, (_, dtype) in CDM_SCHEMA.items():
            if col not in df.columns:
                df[col] = pd.NA
            if dtype == "Int64":
                df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")
            elif dtype == "float64":
                df[col] = pd.to_numeric(df[col], errors="coerce").astype("Float64")
            elif dtype == "str":
                df[col] = _clean_text_series(df[col])
        return df

    def compute_hospital_volume(df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        key_ok = df["hospital_id"].notna() & df["year"].notna()
        base = df.loc[key_ok, ["hospital_id", "year"]].copy()
        vol_tbi = base.groupby(["hospital_id", "year"]).size().rename("hospital_volume_tbi_year").reset_index()
        df = df.merge(vol_tbi, on=["hospital_id", "year"], how="left")

        for name, classes in {
            "hospital_volume_surgical_year": ["DC", "CRAN", "OTHER_CRAN"],
            "hospital_volume_dc_cran_year": ["DC", "CRAN"],
        }.items():
            mask = key_ok & df["procedure_class_final"].isin(classes)
            vol = df.loc[mask].groupby(["hospital_id", "year"]).size().rename(name).reset_index()
            df = df.merge(vol, on=["hospital_id", "year"], how="left")
            # A known hospital-year with no such procedure has true zero.
            df.loc[key_ok, name] = df.loc[key_ok, name].fillna(0)
            df[name] = pd.to_numeric(df[name], errors="coerce").astype("Int64")

        df["hospital_volume_tbi_year"] = pd.to_numeric(df["hospital_volume_tbi_year"], errors="coerce").astype("Int64")
        definition = CONFIG.get("primary_volume_definition", "tbi")
        source_col = "hospital_volume_surgical_year" if definition == "surgical" else "hospital_volume_tbi_year"
        df["hospital_volume_year"] = df[source_col].astype("Int64")
        return df

    def _assign_volume_quartile(df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        out["volume_quartile"] = pd.Series(pd.NA, index=out.index, dtype="string")
        valid = out.dropna(subset=["country", "hospital_id", "year", "hospital_volume_year"])
        for country, sub in valid.groupby("country"):
            units = sub[["hospital_id", "year", "hospital_volume_year"]].drop_duplicates()
            if len(units) < 4 or units["hospital_volume_year"].nunique() < 2:
                continue
            try:
                units["volume_quartile"] = pd.qcut(
                    units["hospital_volume_year"].rank(method="first"),
                    q=4,
                    labels=["Q1", "Q2", "Q3", "Q4"],
                ).astype("string")
            except Exception as exc:
                LOG.warning(f"[{country}] quartis de volume falharam: {exc}")
                continue
            mapping = units.set_index(["hospital_id", "year"])["volume_quartile"]
            idx = out["country"] == country
            keys = pd.MultiIndex.from_frame(out.loc[idx, ["hospital_id", "year"]])
            out.loc[idx, "volume_quartile"] = mapping.reindex(keys).array
        return out

    def compute_transfer_proxy(df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        if {"hospital_region", "residence_region"}.issubset(df.columns):
            known = df["hospital_region"].notna() & df["residence_region"].notna()
            df["transfer_proxy"] = pd.Series(pd.NA, index=df.index, dtype="Int64")
            df.loc[known, "transfer_proxy"] = (
                df.loc[known, "hospital_region"].astype("string")
                != df.loc[known, "residence_region"].astype("string")
            ).astype("Int64")
        else:
            df["transfer_proxy"] = pd.Series(pd.NA, index=df.index, dtype="Int64")
        return df

    def harmonize_all(country_dfs: Dict[str, Optional[pd.DataFrame]]) -> Tuple[pd.DataFrame, List[str]]:
        LOG.info("═" * 60)
        LOG.info("HARMONIZAÇÃO MULTINACIONAL — HOTFIX v1.2")
        LOG.info("═" * 60)
        frames = []
        provenance_rows = []
        for country, df in country_dfs.items():
            if df is None or df.empty:
                LOG.warning(f"[HARM] {country}: sem dados.")
                continue
            finalized = finalize_country_df(df, country)
            frames.append(finalized)
            if "_source_file" in df.columns:
                for source_file, grp in df.groupby("_source_file", dropna=False):
                    provenance_rows.append({
                        "country": country,
                        "source_file": source_file,
                        "n_records": len(grp),
                        "years": "|".join(map(str, sorted(pd.to_numeric(grp.get("year"), errors="coerce").dropna().astype(int).unique()))),
                    })
        if not frames:
            raise RuntimeError("Nenhum país com microdados válidos.")

        df_all = pd.concat(frames, ignore_index=True, sort=False)
        df_all = ns["apply_crosswalk"](df_all)
        df_all = compute_hospital_volume(df_all)
        df_all = _assign_volume_quartile(df_all)
        df_all = compute_transfer_proxy(df_all)
        df_all = ns["compute_hospital_capacity_score"](df_all)
        alerts = ns["validate_cdm"](df_all)

        # Keep the harmonized CDM intentionally narrow. Raw source-specific
        # columns remain in country checkpoints, not in the CDM. This prevents
        # type collisions such as int/string values in `month`.
        extra_derived = [
            "hospital_volume_tbi_year",
            "hospital_volume_surgical_year",
            "hospital_volume_dc_cran_year",
            "volume_quartile",
        ]
        keep = [c for c in list(CDM_SCHEMA) + extra_derived if c in df_all.columns]
        df_cdm = sanitize_dataframe_for_parquet(df_all[keep].copy())
        save_parquet(df_cdm, Path(DIRS["harmonized"]) / "tce_harmonized_cdm.parquet", "CDM")
        ns["save_csv_xlsx"](quick_audit(df_cdm, "CDM"), Path(DIRS["qc"]) / "audit_cdm")
        if provenance_rows:
            ns["save_csv_xlsx"](pd.DataFrame(provenance_rows), Path(DIRS["qc"]) / "source_provenance")
        LOG.info(f"CDM: {len(df_cdm):,} | {df_cdm['country'].value_counts().to_dict()}")
        return df_cdm, alerts

    def build_main_cohort(df_cdm: pd.DataFrame) -> pd.DataFrame:
        LOG.info("COORTE-BASE MULTINACIONAL — TCE HOSPITALIZADO")
        df = df_cdm.copy()
        df = df[df["age"] >= CONFIG["min_age"]].copy()
        df = df[df["dx_main"].astype("string").str.startswith("S06", na=False)].copy()
        df = df[df["year"].between(min(CONFIG["study_years"]), max(CONFIG["study_years"]))].copy()
        # Do not force death and LOS to be jointly complete. Each outcome model
        # performs its own complete-case selection and reports its own N.
        df = df[df["hospital_id"].notna() & df["hospital_volume_year"].notna()].copy()
        if df.empty:
            raise RuntimeError("Coorte-base vazia após diagnóstico/idade/ano/hospital.")

        eligibility = []
        for country, grp in df.groupby("country"):
            eligibility.append({
                "country": country,
                "n_base": len(grp),
                "n_mortality_eligible": int(grp["death_in_hospital"].isin([0, 1]).sum()),
                "n_los_eligible": int(pd.to_numeric(grp["los_days"], errors="coerce").notna().sum()),
                "n_both_outcomes": int((grp["death_in_hospital"].isin([0, 1]) & pd.to_numeric(grp["los_days"], errors="coerce").notna()).sum()),
            })
        ns["save_csv_xlsx"](pd.DataFrame(eligibility), Path(DIRS["qc"]) / "analysis_eligibility_by_country")
        save_parquet(df, Path(DIRS["harmonized"]) / "cohort_main.parquet", "Coorte-base principal")
        LOG.info(f"COORTE-BASE: {len(df):,} | países={df['country'].value_counts().to_dict()}")
        return df

    def fit_gee_poisson_los(
        df: pd.DataFrame,
        exposure: str,
        covariates: List[str],
        country: str,
    ) -> Optional[Dict[str, Any]]:
        valid_covs = ns["_check_covariates_availability"](df, covariates, country)
        cols = ["los_days", exposure, "hospital_id"]
        sub = df[cols].copy()
        for cov in valid_covs:
            col = cov.replace("C(", "").replace(")", "")
            sub[col] = df[col].values
        sub["los_days"] = pd.to_numeric(sub["los_days"], errors="coerce")
        sub = sub.dropna(subset=["los_days", exposure, "hospital_id"])
        sub = sub[sub["los_days"] >= 1].copy()
        for col in sub.columns:
            if pd.api.types.is_numeric_dtype(sub[col]):
                sub[col] = sub[col].astype(float)
        if len(sub) < 50:
            LOG.warning(f"[{country}] GEE NB LOS: N={len(sub)} insuficiente.")
            return None
        formula = "los_days ~ " + exposure + ((" + " + " + ".join(valid_covs)) if valid_covs else "")
        try:
            model = ns["smf"].gee(
                formula,
                groups=sub["hospital_id"],
                data=sub,
                family=ns["sm"].families.NegativeBinomial(alpha=1.0),
                cov_struct=ns["sm"].cov_struct.Exchangeable(),
            )
            result = model.fit()
            beta = float(result.params[exposure])
            se = float(result.bse[exposure])
            pval = float(result.pvalues[exposure])
            return {
                "country": country,
                "outcome": "los_days",
                "exposure": exposure,
                "n": len(sub),
                "beta": round(beta, 4),
                "se": round(se, 4),
                "irr": round(np.exp(beta), 3),
                "ci_low": round(np.exp(beta - 1.96 * se), 3),
                "ci_high": round(np.exp(beta + 1.96 * se), 3),
                "pval": round(pval, 4),
                "model_type": "GEE_negative_binomial_exchangeable",
            }
        except Exception as exc:
            LOG.error(f"[{country}] GEE Negative Binomial LOS falhou: {exc}")
            return None

    def random_effects_meta_analysis(results: List[Dict], exposure: str) -> Dict[str, Any]:
        valid = [r for r in results if r is not None and "beta" in r and "se" in r and r["se"] > 0]
        k = len(valid)
        minimum = int(CONFIG.get("minimum_countries_for_pooling", 3))
        empty = {
            "exposure": exposure,
            "n_studies": k,
            "pooled_beta": None,
            "pooled_or": None,
            "ci_low": None,
            "ci_high": None,
            "tau2": None,
            "I2_pct": None,
            "Q": None,
            "Q_pval": None,
            "country_results": valid,
        }
        if k < minimum:
            empty["reason"] = f"Pooling not performed: {k} countries; minimum={minimum}."
            LOG.warning(f"Meta-análise não agrupada: {k} países válidos; mínimo={minimum}.")
            return empty
        try:
            from statsmodels.stats.meta_analysis import combine_effects
            effects = np.array([float(r["beta"]) for r in valid])
            variances = np.array([float(r["se"]) ** 2 for r in valid])
            result = combine_effects(effects, variances, method_re="iterated", use_t=True)
            beta = float(result.mean_effect_re)
            se = float(result.sd_eff_w_re)
            tau2 = float(result.tau2)
            q = float(result.q)
            df_q = k - 1
            i2 = max(0.0, (q - df_q) / q * 100) if q > 0 else 0.0
            return {
                **empty,
                "pooled_beta": round(beta, 4),
                "pooled_or": round(np.exp(beta), 3),
                "ci_low": round(np.exp(beta - 1.96 * se), 3),
                "ci_high": round(np.exp(beta + 1.96 * se), 3),
                "tau2": round(tau2, 4),
                "I2_pct": round(i2, 1),
                "Q": round(q, 2),
                "Q_pval": round(1 - ns["stats"].chi2.cdf(q, df_q), 4),
                "method": "Paule-Mandel random effects; t-based inference requested",
            }
        except Exception as exc:
            empty["reason"] = f"Pooling failed: {exc}"
            LOG.error(f"Meta-análise falhou: {exc}")
            return empty

    # Install replacements.
    replacements = {
        "save_parquet": save_parquet,
        "quick_audit": quick_audit,
        "_candidate_files_for_year": _candidate_files_for_year,
        "_extract_zip_to_folder": _extract_zip_to_folder,
        "_read_tabular_file": _read_tabular_file,
        "ingest_country_year_generic": ingest_country_year_generic,
        "auto_collect_country_sources": auto_collect_country_sources,
        "ingest_mexico": ingest_mexico,
        "ingest_chile": ingest_chile,
        "ingest_equador": ingest_equador,
        "clean_standardize_equador": clean_standardize_equador,
        "finalize_country_df": finalize_country_df,
        "compute_hospital_volume": compute_hospital_volume,
        "compute_transfer_proxy": compute_transfer_proxy,
        "harmonize_all": harmonize_all,
        "build_main_cohort": build_main_cohort,
        "fit_gee_poisson_los": fit_gee_poisson_los,
        "random_effects_meta_analysis": random_effects_meta_analysis,
        "sanitize_dataframe_for_parquet": sanitize_dataframe_for_parquet,
        "normalize_year_series": _normalize_year_series,
        "normalize_month_series": _normalize_month_series,
    }
    ns.update(replacements)
    LOG.info(
        f"[HOTFIX] TCE pipeline v{HOTFIX_VERSION} aplicado. "
        "Procedimentos multinacionais restritos ao Brasil até validação dos crosswalks."
    )


def purge_derived_checkpoints(ns: Dict[str, Any]) -> None:
    """Delete derived checkpoints that must be rebuilt under v1.2."""
    base = Path(ns["CONFIG"]["base_dir"])
    paths = [
        base / "02_harmonized" / "tce_harmonized_cdm.parquet",
        base / "02_harmonized" / "cohort_main.parquet",
        base / "02_harmonized" / "cohort_surgical.parquet",
        base / "02_harmonized" / "cohort_dc_cran.parquet",
        base / "01_intermediate" / "mexico" / "mexico_raw.parquet",
        base / "01_intermediate" / "mexico" / "mexico_clean.parquet",
        base / "01_intermediate" / "chile" / "chile_raw.parquet",
        base / "01_intermediate" / "chile" / "chile_clean.parquet",
        base / "01_intermediate" / "equador" / "equador_raw.parquet",
        base / "01_intermediate" / "equador" / "equador_clean.parquet",
    ]
    for path in paths:
        if path.exists():
            path.unlink()
            print("Removido:", path)


def write_source_manifest_template(ns: Dict[str, Any]) -> Path:
    base = Path(ns["CONFIG"]["base_dir"])
    out = base / "09_metadata" / "country_source_manifest.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for country in ["brasil", "mexico", "chile", "equador"]:
        for year in ns["CONFIG"]["study_years"]:
            rows.append({
                "country": country,
                "year": year,
                "official_dataset": "",
                "local_file": "",
                "patient_level": "",
                "diagnosis_available": "",
                "hospital_id_available": "",
                "death_available": "",
                "los_available": "",
                "procedure_available": "",
                "dictionary_validated": "",
                "sha256": "",
                "status": "PENDING",
                "notes": "",
            })
    pd.DataFrame(rows).to_csv(out, index=False, encoding="utf-8-sig")
    return out


apply_hotfix(globals())
print('✅ Pipeline TCE v1.2 carregado. Execute purge_derived_checkpoints(globals()) e depois run_pipeline_complete().')
# -*- coding: utf-8 -*-
"""
TCE Multinational Pipeline — hotfix v1.3 RAM-safe / discharge-level

Use after loading tce_datasus_v1_2_revisado.py:

    %run /content/tce_pipeline_hotfix_v1_3_ram.py
    purge_mexico_v13_checkpoints(globals())
    df_mexico = run_mexico_ingestion(CONFIG, DIRS)   # test Mexico alone first

Then, after Mexico succeeds:

    df_cdm, df_main, df_surg, df_dc, model_output = run_pipeline_complete()

This patch preserves row-level hospital-discharge records. It does not aggregate
patients into means/medians and it does not deduplicate clinically similar rows.
"""


import csv
import gc
import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

RAM_HOTFIX_VERSION = "1.3.0"


def _rss_gb() -> float:
    try:
        import psutil
        return psutil.Process(os.getpid()).memory_info().rss / (1024 ** 3)
    except Exception:
        return float("nan")


def _norm_name(x: Any) -> str:
    import unicodedata
    s = unicodedata.normalize("NFKD", str(x)).encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^A-Za-z0-9]+", "_", s.upper().strip())
    return re.sub(r"_+", "_", s).strip("_")


def _clean_text(s: pd.Series) -> pd.Series:
    out = s.astype("string").str.strip()
    return out.replace({
        "": pd.NA, "nan": pd.NA, "NaN": pd.NA, "NAN": pd.NA,
        "None": pd.NA, "NONE": pd.NA, "<NA>": pd.NA,
        "null": pd.NA, "NULL": pd.NA,
    })


def _normalize_year(s: pd.Series) -> pd.Series:
    raw = _clean_text(s)
    extracted = raw.str.extract(r"((?:19|20)\d{2})", expand=False)
    year = pd.to_numeric(extracted, errors="coerce")
    numeric = pd.to_numeric(raw.str.replace(r"\.0$", "", regex=True), errors="coerce")
    year = year.fillna(numeric)
    year = year.where(~year.between(0, 99), 2000 + year)
    year = year.where(~year.between(190001, 209912), np.floor(year / 100))
    return year.where(year.between(1900, 2100)).astype("Int64")


def _normalize_month(s: pd.Series) -> pd.Series:
    names = {
        "ENE": 1, "ENERO": 1, "JAN": 1, "JANUARY": 1,
        "FEB": 2, "FEBRERO": 2, "FEBRUARY": 2,
        "MAR": 3, "MARZO": 3, "MARCH": 3,
        "ABR": 4, "ABRIL": 4, "APR": 4, "APRIL": 4,
        "MAY": 5, "MAYO": 5,
        "JUN": 6, "JUNIO": 6, "JUNE": 6,
        "JUL": 7, "JULIO": 7, "JULY": 7,
        "AGO": 8, "AGOSTO": 8, "AUG": 8, "AUGUST": 8,
        "SEP": 9, "SEPT": 9, "SEPTIEMBRE": 9, "SEPTEMBER": 9,
        "OCT": 10, "OCTUBRE": 10, "OCTOBER": 10,
        "NOV": 11, "NOVIEMBRE": 11, "NOVEMBER": 11,
        "DIC": 12, "DICIEMBRE": 12, "DEC": 12, "DECEMBER": 12,
    }
    raw = _clean_text(s).str.upper()
    out = pd.to_numeric(raw.str.replace(r"\.0$", "", regex=True), errors="coerce")
    out = out.fillna(raw.map(names))
    return out.where(out.between(1, 12)).astype("Int64")


def _sha256(path: Path, block: int = 8 * 1024 * 1024) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        while True:
            b = f.read(block)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def _is_range_source(path: Path) -> bool:
    name = path.name.lower()
    return bool(re.search(r"20\d{2}\s*[_-]\s*20\d{2}", name))


def _contains_exact_year(path: Path, year: int) -> bool:
    return bool(re.search(rf"(?<!\d){year}(?!\d)", path.name))


def _candidate_top_level_sources(raw_dir: Path, year: int) -> List[Path]:
    valid = {".zip", ".7z", ".rar", ".csv", ".txt", ".tsv", ".parquet", ".sav", ".dbf"}
    out: List[Path] = []
    raw_dir = Path(raw_dir)
    for p in raw_dir.rglob("*"):
        if not p.is_file() or p.suffix.lower() not in valid:
            continue
        rel_dirs = [x.lower() for x in p.relative_to(raw_dir).parts[:-1]]
        if any(x.startswith("extracted_") for x in rel_dirs):
            continue
        if any(x in {"__macosx", ".ipynb_checkpoints"} for x in rel_dirs):
            continue
        name = p.name.lower()
        exact = _contains_exact_year(p, year)
        ranges = re.findall(r"(20\d{2})\s*[_-]\s*(20\d{2})", name)
        in_range = any(int(a) <= year <= int(b) for a, b in ranges)
        if exact or in_range:
            out.append(p)
    return sorted(set(out), key=lambda p: str(p).lower())


def _select_mexico_source(raw_dir: Path, year: int, log) -> Optional[Path]:
    candidates = _candidate_top_level_sources(raw_dir, year)
    if not candidates:
        return None

    dedicated = [p for p in candidates if _contains_exact_year(p, year) and not _is_range_source(p)]
    pool = dedicated if dedicated else candidates

    def score(p: Path) -> Tuple[int, int, int, str]:
        name = p.name.lower()
        s = 0
        if _contains_exact_year(p, year):
            s += 100
        if not _is_range_source(p):
            s += 80
        if "sectorial" in name:
            s += 30
        if "egres" in name:
            s += 20
        if p.suffix.lower() in {".csv", ".txt", ".tsv", ".parquet"}:
            s += 15
        if "insp_conahcyt" in name or "2013_2020" in name:
            s -= 200
        try:
            size = p.stat().st_size
        except Exception:
            size = 0
        return (s, size, -len(name), name)

    selected = max(pool, key=score)
    ignored = [p.name for p in candidates if p != selected]
    log.info(f"[MEXICO] {year}: fonte canônica selecionada: {selected.name}")
    if ignored:
        log.info(f"[MEXICO] {year}: fontes alternativas ignoradas: {ignored}")
    return selected


def _ensure_7z(log) -> Optional[str]:
    exe = shutil.which("7z") or shutil.which("7zz")
    if exe:
        return exe
    try:
        subprocess.run(["apt-get", "update", "-qq"], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["apt-get", "install", "-y", "-qq", "p7zip-full"], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as exc:
        log.error(f"[MEXICO] Falha ao instalar p7zip: {exc}")
        return None
    return shutil.which("7z") or shutil.which("7zz")


def _materialize_source_locally(source: Path, year: int, log) -> Tuple[List[Path], Optional[Path]]:
    """Return local tabular files and the temporary directory to delete later."""
    source = Path(source)
    tmp_root = Path("/content/tce_mexico_stream_cache") / str(year)
    if tmp_root.exists():
        shutil.rmtree(tmp_root, ignore_errors=True)
    tmp_root.mkdir(parents=True, exist_ok=True)

    tabular_exts = {".csv", ".txt", ".tsv", ".parquet", ".sav", ".dbf"}

    if source.suffix.lower() in tabular_exts:
        local = tmp_root / source.name
        shutil.copy2(source, local)
        return [local], tmp_root

    try:
        if source.suffix.lower() == ".zip":
            with zipfile.ZipFile(source, "r") as zf:
                zf.extractall(tmp_root)
        else:
            raise zipfile.BadZipFile("requires 7z")
    except Exception as exc:
        exe = _ensure_7z(log)
        if not exe:
            log.error(f"[MEXICO] Não foi possível extrair {source.name}: {exc}")
            return [], tmp_root
        ret = subprocess.run([exe, "x", "-y", f"-o{tmp_root}", str(source)],
                             capture_output=True, text=True)
        if ret.returncode != 0:
            log.error(f"[MEXICO] 7z falhou em {source.name}: {ret.stderr[-1000:]}")
            return [], tmp_root

    files = [p for p in tmp_root.rglob("*") if p.is_file() and p.suffix.lower() in tabular_exts]
    if not files:
        log.error(f"[MEXICO] Nenhum arquivo tabular dentro de {source.name}")
        return [], tmp_root

    bad = re.compile(r"diccion|catalog|layout|readme|nota|metadata|esquema|formulario", re.I)
    analytic = [p for p in files if not bad.search(p.name)] or files
    # Annual archives should contain one main table. The largest is the safest
    # deterministic choice and avoids loading a dictionary or duplicate export.
    selected = max(analytic, key=lambda p: p.stat().st_size)
    log.info(
        f"[MEXICO] {year}: arquivo tabular selecionado: {selected.name} "
        f"({selected.stat().st_size / 1e9:.2f} GB no disco)"
    )
    return [selected], tmp_root


def _sniff_text(path: Path) -> List[Tuple[str, str, str, int]]:
    """Return parser attempts as (encoding, separator, engine, quoting)."""
    candidates: List[Tuple[str, str, str, int]] = []
    for enc in ["utf-8-sig", "utf-8", "cp1252", "latin-1"]:
        try:
            with Path(path).open("r", encoding=enc, errors="replace") as f:
                sample = "".join(f.readline() for _ in range(8))
            counts = {sep: sample.count(sep) for sep in ["|", ";", ",", "\t"]}
            sep = max(counts, key=counts.get)
            if counts[sep] == 0:
                continue
            candidates.append((enc, sep, "c", csv.QUOTE_MINIMAL))
            candidates.append((enc, sep, "c", csv.QUOTE_NONE))
        except Exception:
            continue
    # Slow but robust last resort.
    if candidates:
        enc, sep, _, _ = candidates[0]
        candidates.append((enc, sep, "python", csv.QUOTE_NONE))
    return candidates


def _rename_map(columns: Sequence[str], aliases: Dict[str, List[str]]) -> Dict[str, str]:
    norm_to_original: Dict[str, str] = {}
    for c in columns:
        norm_to_original.setdefault(_norm_name(c), c)
    rename: Dict[str, str] = {}
    for canonical, choices in aliases.items():
        for choice in choices:
            original = norm_to_original.get(_norm_name(choice))
            if original is not None:
                rename[original] = canonical
                break
    return rename


def _find_proc_columns(columns: Sequence[str], proc_aliases: Sequence[str]) -> List[str]:
    norm_to_original: Dict[str, str] = {}
    for c in columns:
        norm_to_original.setdefault(_norm_name(c), c)
    found: List[str] = []
    for alias in proc_aliases:
        original = norm_to_original.get(_norm_name(alias))
        if original is not None and original not in found:
            found.append(original)
    return found


def _combine_procedures_filtered(df: pd.DataFrame, proc_aliases: Sequence[str]) -> pd.Series:
    found = _find_proc_columns(df.columns, proc_aliases)
    if not found:
        return pd.Series(pd.NA, index=df.index, dtype="string")

    cleaned = pd.DataFrame(index=df.index)
    for col in found:
        s = _clean_text(df[col]).str.upper().str.replace(".", "", regex=False)
        cleaned[col] = s

    def join_values(row: pd.Series) -> Any:
        vals = sorted({str(v) for v in row.dropna() if str(v)})
        return "|".join(vals) if vals else pd.NA

    # This row-wise operation is now applied only to S06 records, typically
    # hundreds per chunk rather than 4-6 million rows per annual file.
    return cleaned.apply(join_values, axis=1).astype("string")


def _compact_mexico_columns(df: pd.DataFrame) -> pd.DataFrame:
    preferred = [
        "record_id", "year", "month", "hospital_id_raw", "hospital_region",
        "residence_region", "age", "age_unit", "sex_raw", "los_days",
        "dx_main", "external_cause", "discharge_reason", "discharge_condition",
        "case_count", "procedure_code_raw", "country", "source",
        "_source_file", "_source_sha256", "_source_row_number", "_source_year",
    ]
    keep = [c for c in preferred if c in df.columns]
    return df[keep].copy()


def _stream_one_mexico_table(
    table_path: Path,
    source_path: Path,
    source_digest: str,
    year: int,
    ns: Dict[str, Any],
) -> Optional[pd.DataFrame]:
    log = ns["LOG"]
    aliases = ns["MEXICO_ALIASES"]
    proc_aliases = ns["MEXICO_PROC_ALIASES"]
    chunksize = int(ns["CONFIG"].get("mexico_chunk_rows", 50_000))

    suffix = table_path.suffix.lower()
    if suffix == ".parquet":
        # Row-group iteration would be ideal, but annual Mexican sources here
        # are text. Keep a guarded fallback for future local Parquet inputs.
        df = pd.read_parquet(table_path)
        if len(df) > 1_000_000:
            raise RuntimeError(
                f"Parquet mexicano muito grande sem partições ({len(df):,} linhas). "
                "Converta-o em dataset particionado por ano/diagnóstico."
            )
        attempts = [(None, None, None, None)]
    elif suffix in {".csv", ".txt", ".tsv"}:
        attempts = _sniff_text(table_path)
        if not attempts:
            log.error(f"[MEXICO] Não foi possível detectar formato: {table_path.name}")
            return None
    else:
        log.error(f"[MEXICO] Streaming ainda não implementado para {suffix}: {table_path.name}")
        return None

    last_error: Optional[Exception] = None
    for attempt_no, attempt in enumerate(attempts, start=1):
        year_frames: List[pd.DataFrame] = []
        total_rows = 0
        total_s06 = 0
        rename: Optional[Dict[str, str]] = None
        try:
            if suffix == ".parquet":
                iterator: Iterable[pd.DataFrame] = [df]
                parser_label = "parquet"
            else:
                enc, sep, engine, quoting = attempt
                kwargs = dict(
                    filepath_or_buffer=table_path,
                    sep=sep,
                    encoding=enc,
                    encoding_errors="replace",
                    dtype=str,
                    chunksize=chunksize,
                    on_bad_lines="skip",
                    engine=engine,
                    quoting=quoting,
                )
                if quoting == csv.QUOTE_NONE:
                    kwargs["escapechar"] = "\\"
                iterator = pd.read_csv(**kwargs)
                parser_label = f"enc={enc}, sep={sep!r}, engine={engine}, quoting={quoting}"

            log.info(
                f"[MEXICO] {year}: streaming iniciado ({parser_label}; chunk={chunksize:,}; "
                f"RSS={_rss_gb():.2f} GB)"
            )

            for chunk_no, chunk in enumerate(iterator, start=1):
                n_chunk = len(chunk)
                if n_chunk == 0:
                    continue
                row_start = total_rows + 1
                total_rows += n_chunk

                if rename is None:
                    rename = _rename_map(chunk.columns, aliases)
                    mapped = set(rename.values())
                    missing = sorted({"dx_main", "age", "sex_raw", "hospital_id_raw"} - mapped)
                    if missing:
                        raise RuntimeError(
                            f"Fonte não contém microdados mínimos; faltam {missing}. "
                            f"Colunas iniciais: {list(chunk.columns)[:80]}"
                        )

                chunk.rename(columns=rename, inplace=True)
                dx = (
                    chunk["dx_main"].astype("string").str.strip().str.upper()
                    .str.replace(".", "", regex=False)
                )
                mask = dx.str.startswith("S06", na=False)
                if not mask.any():
                    del chunk, dx, mask
                    if chunk_no % 20 == 0:
                        gc.collect()
                    continue

                row_numbers = np.arange(row_start, row_start + n_chunk, dtype=np.int64)[mask.to_numpy()]
                out = chunk.loc[mask].copy()
                out["dx_main"] = dx.loc[mask].values

                if "year" in out.columns and out["year"].notna().any():
                    yn = _normalize_year(out["year"])
                    observed = sorted(yn.dropna().astype(int).unique().tolist())
                    if year in observed:
                        keep_year = yn == year
                        out = out.loc[keep_year].copy()
                        row_numbers = row_numbers[keep_year.to_numpy()]
                    elif observed:
                        raise RuntimeError(
                            f"Ano-alvo {year} ausente; anos observados no arquivo: {observed[:20]}"
                        )
                    out["year"] = year
                else:
                    out["year"] = year

                if out.empty:
                    del chunk, dx, mask, out
                    continue

                if "month" in out.columns:
                    out["month"] = _normalize_month(out["month"])

                out["procedure_code_raw"] = _combine_procedures_filtered(out, proc_aliases)
                out["country"] = "mexico"
                out["source"] = "SAEH-DGIS"
                out["_source_file"] = str(source_path)
                out["_source_sha256"] = source_digest
                out["_source_row_number"] = row_numbers
                out["_source_year"] = year
                out["record_id"] = (
                    "MX-" + str(year) + "-" + source_digest[:12] + "-" +
                    pd.Series(row_numbers, index=out.index).astype("string")
                )

                total_s06 += len(out)
                year_frames.append(out)

                if chunk_no % 10 == 0:
                    log.info(
                        f"[MEXICO] {year}: {total_rows:,} linhas examinadas; "
                        f"S06 retidas={total_s06:,}; RSS={_rss_gb():.2f} GB"
                    )
                del chunk, dx, mask, out
                gc.collect()

            if not year_frames:
                log.warning(f"[MEXICO] {year}: nenhum registro S06 encontrado em {table_path.name}")
                return None

            full_year = pd.concat(year_frames, ignore_index=True, sort=False)
            del year_frames
            gc.collect()

            if "case_count" in full_year.columns:
                cc = pd.to_numeric(full_year["case_count"], errors="coerce")
                if cc.dropna().gt(1).any():
                    bad_n = int(cc.gt(1).sum())
                    raise RuntimeError(
                        f"A fonte parece agregada: {bad_n:,} linhas têm case_count > 1. "
                        "Não é permitido tratá-las como pacientes individuais."
                    )

            log.info(
                f"[MEXICO] {year}: streaming concluído; {total_rows:,} linhas lidas, "
                f"{len(full_year):,} registros S06 preservados; RSS={_rss_gb():.2f} GB"
            )
            return full_year

        except Exception as exc:
            last_error = exc
            log.warning(
                f"[MEXICO] {year}: tentativa de parser {attempt_no}/{len(attempts)} falhou: {exc}"
            )
            del year_frames
            gc.collect()
            continue

    log.error(f"[MEXICO] {year}: todas as tentativas de leitura falharam: {last_error}")
    return None


def apply_ram_hotfix(ns: Dict[str, Any]) -> None:
    required = [
        "CONFIG", "DIRS", "LOG", "MEXICO_ALIASES", "MEXICO_PROC_ALIASES",
        "run_mexico_ingestion", "run_pipeline_complete", "save_parquet",
    ]
    missing = [x for x in required if x not in ns]
    if missing:
        raise RuntimeError(
            "Carregue primeiro tce_datasus_v1_2_revisado.py. Ausentes: " + ", ".join(missing)
        )

    config = ns["CONFIG"]
    log = ns["LOG"]
    config["pipeline_version"] = RAM_HOTFIX_VERSION
    config.setdefault("mexico_chunk_rows", 50_000)
    config["run_metaanalysis"] = False
    config["analysis_strategy"] = "individual_record_one_stage_plus_country_sensitivity"

    def ingest_mexico(years: List[int], raw_dir: Path, inter_dir: Path) -> Optional[pd.DataFrame]:
        log.info("═" * 60)
        log.info("INGESTÃO MÉXICO — STREAMING RAM-SAFE v1.3")
        log.info("Registros individuais de alta; sem agregação e sem deduplicação clínica")
        log.info("═" * 60)

        inter_dir = Path(inter_dir)
        inter_dir.mkdir(parents=True, exist_ok=True)
        ck_all = inter_dir / "mexico_raw.parquet"
        if ns["file_exists_ok"](ck_all):
            log.info(f"[CHECKPOINT-MX] {ck_all}")
            return pd.read_parquet(ck_all)

        compact_paths: List[Path] = []
        processed_rows = []

        for year in years:
            ck_full = inter_dir / f"mexico_s06_full_{year}.parquet"
            ck_compact = inter_dir / f"mexico_s06_analytic_{year}.parquet"

            if ns["file_exists_ok"](ck_compact):
                log.info(f"[CHECKPOINT-MX-YEAR] {year}: {ck_compact.name}")
                compact_paths.append(ck_compact)
                continue

            source = _select_mexico_source(Path(raw_dir), year, log)
            if source is None:
                log.error(f"[MEXICO] {year}: nenhuma fonte anual encontrada.")
                continue

            source_digest = _sha256(source)
            tables, temp_dir = _materialize_source_locally(source, year, log)
            try:
                if not tables:
                    continue
                full_year = _stream_one_mexico_table(
                    tables[0], source, source_digest, year, ns
                )
                if full_year is None or full_year.empty:
                    continue

                # Full S06 discharge-level source columns are preserved on
                # disk for later variable expansion and audits.
                ns["save_parquet"](full_year, ck_full, f"México {year} S06 full discharge-level")

                compact = _compact_mexico_columns(full_year)
                ns["save_parquet"](compact, ck_compact, f"México {year} S06 analytic")
                compact_paths.append(ck_compact)
                processed_rows.append({
                    "year": year,
                    "source": str(source),
                    "source_sha256": source_digest,
                    "n_s06": len(full_year),
                    "n_columns_full": full_year.shape[1],
                    "n_columns_analytic": compact.shape[1],
                })
                del full_year, compact
                gc.collect()
                log.info(f"[MEXICO] {year}: checkpoint finalizado; RSS={_rss_gb():.2f} GB")
            finally:
                if temp_dir is not None:
                    shutil.rmtree(temp_dir, ignore_errors=True)
                gc.collect()

        if not compact_paths:
            log.error("[MX] Nenhum ano mexicano foi processado.")
            return None

        # Only filtered S06 records are loaded here, not the 40+ million source rows.
        frames = [pd.read_parquet(p) for p in compact_paths]
        df_mx = pd.concat(frames, ignore_index=True, sort=False)
        del frames
        gc.collect()
        log.info(
            f"[MEXICO] Consolidado individual: {len(df_mx):,} altas S06; "
            f"RSS={_rss_gb():.2f} GB"
        )
        ns["save_parquet"](df_mx, ck_all, "México raw S06 RAM-safe")

        if processed_rows:
            audit = pd.DataFrame(processed_rows)
            ns["save_csv_xlsx"](audit, Path(ns["DIRS"]["qc"]) / "mexico_streaming_sources")
        return df_mx

    def _volume_standardized_patient_frame(df_main: pd.DataFrame) -> pd.DataFrame:
        needed = [
            "country", "year", "hospital_id", "hospital_volume_year",
            "age", "sex", "trauma_subtype", "death_in_hospital", "los_days",
        ]
        cols = [c for c in needed if c in df_main.columns]
        model_df = df_main[cols].copy()
        units = (
            model_df[["country", "year", "hospital_id", "hospital_volume_year"]]
            .dropna().drop_duplicates()
        )
        units["log_volume"] = np.log1p(pd.to_numeric(units["hospital_volume_year"], errors="coerce"))
        grp = units.groupby(["country", "year"])["log_volume"]
        mean = grp.transform("mean")
        sd = grp.transform("std").replace(0, np.nan)
        units["volume_z_country_year"] = ((units["log_volume"] - mean) / sd).fillna(0.0)
        model_df = model_df.merge(
            units[["country", "year", "hospital_id", "volume_z_country_year"]],
            on=["country", "year", "hospital_id"], how="left", validate="many_to_one"
        )
        model_df["age10"] = pd.to_numeric(model_df.get("age"), errors="coerce") / 10.0
        return model_df

    def _fit_clustered_glm(
        data: pd.DataFrame,
        outcome: str,
        family: Any,
        include_country_interaction: bool = False,
    ) -> Tuple[Optional[Any], pd.DataFrame]:
        required = [outcome, "volume_z_country_year", "age10", "hospital_id", "country", "year"]
        optional = [c for c in ["sex", "trauma_subtype"] if c in data.columns]
        sub = data[required + optional].copy()
        sub[outcome] = pd.to_numeric(sub[outcome], errors="coerce")
        sub = sub.dropna(subset=required)
        if outcome == "death_in_hospital":
            sub = sub[sub[outcome].isin([0, 1])]
        else:
            sub = sub[sub[outcome] >= 1]
        if len(sub) < 200 or sub["hospital_id"].nunique() < 10:
            return None, pd.DataFrame()
        terms = ["volume_z_country_year", "age10", "C(country)", "C(year)"]
        if "sex" in optional and sub["sex"].nunique(dropna=True) > 1:
            terms.append("C(sex)")
        if "trauma_subtype" in optional and sub["trauma_subtype"].nunique(dropna=True) > 1:
            terms.append("C(trauma_subtype)")
        if include_country_interaction and sub["country"].nunique() > 1:
            terms.append("volume_z_country_year:C(country)")
        formula = f"{outcome} ~ " + " + ".join(terms)
        fit = ns["smf"].glm(formula=formula, data=sub, family=family).fit(
            cov_type="cluster", cov_kwds={"groups": sub["hospital_id"]}, maxiter=100
        )
        ci = fit.conf_int()
        table = pd.DataFrame({
            "term": fit.params.index,
            "beta": fit.params.values,
            "se_cluster_hospital": fit.bse.values,
            "p_value": fit.pvalues.values,
            "ci_low_beta": ci.iloc[:, 0].values,
            "ci_high_beta": ci.iloc[:, 1].values,
        })
        table["exp_beta"] = np.exp(table["beta"])
        table["exp_ci_low"] = np.exp(table["ci_low_beta"])
        table["exp_ci_high"] = np.exp(table["ci_high_beta"])
        table["n_records"] = len(sub)
        table["n_hospitals"] = sub["hospital_id"].nunique()
        table["formula"] = formula
        return fit, table

    def run_main_models_individual(df_main: pd.DataFrame) -> Dict[str, Any]:
        log.info("═" * 60)
        log.info("MODELOS INDIVIDUAIS MULTINACIONAIS — SEM META-ANÁLISE")
        log.info("One-stage GLM com SE agrupado por hospital + sensibilidades por país")
        log.info("═" * 60)
        mdf = _volume_standardized_patient_frame(df_main)

        mortality_fit, mortality_table = _fit_clustered_glm(
            mdf, "death_in_hospital", ns["sm"].families.Binomial(), False
        )
        mortality_het_fit, mortality_het_table = _fit_clustered_glm(
            mdf, "death_in_hospital", ns["sm"].families.Binomial(), True
        )
        los_fit, los_table = _fit_clustered_glm(
            mdf, "los_days", ns["sm"].families.NegativeBinomial(alpha=1.0), False
        )
        los_het_fit, los_het_table = _fit_clustered_glm(
            mdf, "los_days", ns["sm"].families.NegativeBinomial(alpha=1.0), True
        )

        tables_dir = Path(ns["DIRS"]["tables"])
        if not mortality_table.empty:
            ns["save_csv_xlsx"](mortality_table, tables_dir / "Tabela4_one_stage_mortalidade")
        if not mortality_het_table.empty:
            ns["save_csv_xlsx"](mortality_het_table, tables_dir / "Tabela4b_heterogeneidade_mortalidade")
        if not los_table.empty:
            ns["save_csv_xlsx"](los_table, tables_dir / "Tabela5_one_stage_LOS")
        if not los_het_table.empty:
            ns["save_csv_xlsx"](los_het_table, tables_dir / "Tabela5b_heterogeneidade_LOS")

        # Country-specific discharge-level models are retained as sensitivity
        # analyses and forest-plot inputs; their coefficients are not pooled.
        results_mort: List[Any] = []
        results_los: List[Any] = []
        country_df = df_main.copy()
        country_df["log_vol"] = np.log1p(pd.to_numeric(country_df["hospital_volume_year"], errors="coerce"))
        covs = ["age", "C(sex)", "C(trauma_subtype)", "C(year)"]
        for country in sorted(country_df["country"].dropna().unique()):
            sub = country_df[country_df["country"] == country].copy()
            if len(sub) < 100:
                continue
            results_mort.append(ns["fit_gee_logistic"](
                sub, "death_in_hospital", "log_vol", covs, "hospital_id", country
            ))
            results_los.append(ns["fit_gee_poisson_los"](
                sub, "log_vol", covs, country
            ))
        ns["save_csv_xlsx"](
            pd.DataFrame([x for x in results_mort if x]),
            tables_dir / "TabelaS_modelos_pais_mortalidade"
        )
        ns["save_csv_xlsx"](
            pd.DataFrame([x for x in results_los if x]),
            tables_dir / "TabelaS_modelos_pais_LOS"
        )
        del mdf, country_df
        gc.collect()
        return {
            "analysis_type": "individual_record_one_stage_clustered_glm",
            "results_mort": results_mort,
            "results_los": results_los,
            "one_stage_mortality": mortality_table.to_dict("records"),
            "one_stage_mortality_heterogeneity": mortality_het_table.to_dict("records"),
            "one_stage_los": los_table.to_dict("records"),
            "one_stage_los_heterogeneity": los_het_table.to_dict("records"),
            "meta_analysis_performed": False,
        }

    # Replace the memory-critical ingestion and the old two-stage meta-analysis.
    ns["ingest_mexico"] = ingest_mexico
    ns["run_main_models"] = run_main_models_individual

    def run_pipeline_complete(config: dict = ns["CONFIG"], dirs: dict = ns["DIRS"]):
        start = time.time()
        log.info(f"▶▶▶  PIPELINE TCE v{RAM_HOTFIX_VERSION} RAM-SAFE / INDIVIDUAL-RECORD  ◀◀◀")
        df_brasil = ns["run_brasil_ingestion"](config, dirs)
        gc.collect()
        df_mexico = ns["run_mexico_ingestion"](config, dirs)
        gc.collect()
        df_chile = ns["run_chile_ingestion"](config, dirs)
        gc.collect()
        df_equador = ns["run_equador_ingestion"](config, dirs)
        gc.collect()
        country_dfs = {
            "brasil": df_brasil, "mexico": df_mexico,
            "chile": df_chile, "equador": df_equador,
        }
        ns["run_raw_audit"](country_dfs)
        ns["build_crosswalk_table"](dirs)
        df_cdm, cdm_alerts = ns["harmonize_all"](country_dfs)
        df_main, df_surg, df_dc = ns["build_cohorts"](df_cdm)
        ns["run_all_tables"](df_main, df_surg, df_dc)
        model_output: Dict[str, Any] = {}
        if config.get("run_main_analysis", True):
            model_output = ns["run_main_models"](df_main)
        if config.get("run_sensitivity", True):
            ns["run_all_sensitivity"](df_main, df_dc)
        ns["run_main_figures"](df_cdm, df_main, df_dc, model_output)
        ns["run_supplemental_figures"](df_cdm, df_main)
        # The legacy report has a hard-coded meta-analysis section. Passing an
        # empty model object prevents it from falsely describing this IPD run as meta-analysis.
        ns["generate_final_report"](
            country_dfs, df_cdm, df_main, df_dc, {}, cdm_alerts, start
        )
        log.info("▶▶▶  PIPELINE INDIVIDUAL-RECORD CONCLUÍDO  ◀◀◀")
        return df_cdm, df_main, df_surg, df_dc, model_output

    ns["run_pipeline_complete"] = run_pipeline_complete
    ns["select_mexico_source_v13"] = _select_mexico_source
    ns["rss_gb"] = _rss_gb
    log.info(
        f"[HOTFIX] v{RAM_HOTFIX_VERSION} aplicado: México em chunks de "
        f"{config['mexico_chunk_rows']:,}, filtro S06 precoce, checkpoints anuais e "
        "arquivo agregado 2013–2020 ignorado quando existe fonte anual."
    )


def purge_mexico_v13_checkpoints(ns: Dict[str, Any], remove_full_year_files: bool = True) -> None:
    base = Path(ns["CONFIG"]["base_dir"])
    inter = base / "01_intermediate" / "mexico"
    targets = [
        inter / "mexico_raw.parquet",
        inter / "mexico_clean.parquet",
        base / "02_harmonized" / "tce_harmonized_cdm.parquet",
        base / "02_harmonized" / "cohort_main.parquet",
        base / "02_harmonized" / "cohort_surgical.parquet",
        base / "02_harmonized" / "cohort_dc_cran.parquet",
    ]
    targets.extend(inter.glob("mexico_s06_analytic_*.parquet"))
    if remove_full_year_files:
        targets.extend(inter.glob("mexico_s06_full_*.parquet"))
    for p in targets:
        p = Path(p)
        if p.exists():
            p.unlink()
            print("Removido:", p)
    shutil.rmtree("/content/tce_mexico_stream_cache", ignore_errors=True)
    gc.collect()
    print("Checkpoints mexicanos v1.3 limpos.")


def cleanup_old_mexico_extractions(ns: Dict[str, Any], bulk_only: bool = True) -> None:
    """Optional disk cleanup; does not delete original archives."""
    raw = Path(ns["DIRS"]["raw_mx"])
    for p in raw.glob("extracted_*"):
        name = p.name.lower()
        if bulk_only and not ("2013_2020" in name or "insp_conahcyt" in name):
            continue
        shutil.rmtree(p, ignore_errors=True)
        print("Pasta extraída removida:", p)


apply_ram_hotfix(globals())
print("✅ Hotfix TCE v1.3 RAM-safe carregado.")
print("Teste primeiro apenas o México:")
print("  purge_mexico_v13_checkpoints(globals())")
print("  df_mexico = run_mexico_ingestion(CONFIG, DIRS)")
# -*- coding: utf-8 -*-
"""
TCE Multinational Pipeline — hotfix v1.3.1

Apply after loading v1.3.0:

    %run /content/tce_datasus_v1_3_ram_revisado.py
    %run /content/tce_pipeline_hotfix_v1_3_1_models.py

Then rerun only derived stages (Mexico checkpoints are preserved):

    CONFIG["countries"]["chile"] = False
    CONFIG["countries"]["equador"] = False
    purge_analysis_outputs_v131(globals())
    df_cdm, df_main, df_surg, df_dc, model_output = run_pipeline_complete()

Main fixes:
- converts pandas nullable extension dtypes before Patsy/statsmodels formulas;
- prevents a model failure from aborting the whole pipeline;
- performs outcome-specific eligibility checks and reports countries actually used;
- fits models sequentially to reduce memory;
- exports an observed procedure-code inventory without inventing mappings;
- normalizes procedure codes consistently in lookup and source data;
- removes the pandas categorical deprecation warning in Parquet sanitation.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
import gc
import json
import re
import time

import numpy as np
import pandas as pd

MODEL_HOTFIX_VERSION = "1.3.1"


def _v131_norm_proc_token(value: Any) -> Optional[str]:
    if value is None or value is pd.NA:
        return None
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    text = str(value).strip().upper()
    if not text or text in {"NAN", "NONE", "<NA>", "NULL"}:
        return None
    text = re.sub(r"\.0+$", "", text)
    text = re.sub(r"[^A-Z0-9]", "", text)
    return text or None


def _v131_proc_tokens(value: Any) -> List[str]:
    if value is None or value is pd.NA:
        return []
    try:
        if pd.isna(value):
            return []
    except Exception:
        pass
    tokens: List[str] = []
    for part in re.split(r"[|;,\s]+", str(value)):
        token = _v131_norm_proc_token(part)
        if token and token not in tokens:
            tokens.append(token)
    return tokens


def apply_model_hotfix_v131(ns: Dict[str, Any]) -> None:
    required = [
        "CONFIG", "DIRS", "LOG", "sm", "smf", "save_csv_xlsx",
        "run_pipeline_complete", "run_main_models", "apply_crosswalk",
    ]
    missing = [name for name in required if name not in ns]
    if missing:
        raise RuntimeError(
            "Carregue primeiro tce_datasus_v1_3_ram_revisado.py. Ausentes: "
            + ", ".join(missing)
        )

    log = ns["LOG"]
    config = ns["CONFIG"]
    dirs = ns["DIRS"]
    config["pipeline_version"] = MODEL_HOTFIX_VERSION

    def sanitize_dataframe_for_parquet_v131(df: pd.DataFrame) -> pd.DataFrame:
        """Arrow-safe conversion without deprecated categorical-dtype checks."""
        out = df.copy()
        for col in out.columns:
            s = out[col]
            if isinstance(s.dtype, pd.CategoricalDtype):
                out[col] = s.astype("string")
                continue
            if pd.api.types.is_datetime64_any_dtype(s.dtype):
                out[col] = pd.to_datetime(s, errors="coerce")
                continue
            if s.dtype == "object":
                def jsonify(value: Any) -> Any:
                    if value is None or value is pd.NA:
                        return pd.NA
                    if isinstance(value, (list, tuple, set, dict)):
                        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
                    return value
                s = s.map(jsonify)
                text_hints = (
                    "id", "code", "codigo", "cod_", "dx", "diag", "cie",
                    "proc", "clues", "cnes", "source", "sex", "region",
                )
                if any(h in col.lower() for h in text_hints):
                    out[col] = s.astype("string").str.strip().replace(
                        {"": pd.NA, "nan": pd.NA, "None": pd.NA, "<NA>": pd.NA}
                    )
                    continue
                non_null = s.dropna()
                if non_null.empty:
                    out[col] = pd.Series(pd.NA, index=s.index, dtype="string")
                    continue
                numeric = pd.to_numeric(non_null, errors="coerce")
                if numeric.notna().all():
                    converted = pd.to_numeric(s, errors="coerce")
                    finite = converted.dropna()
                    if len(finite) and np.all(np.isclose(finite % 1, 0)):
                        out[col] = converted.astype("Int64")
                    else:
                        out[col] = converted.astype("Float64")
                else:
                    out[col] = s.astype("string")
        return out

    def build_proc_lookup_v131(df_cw: pd.DataFrame) -> Dict[str, Dict[str, Tuple[str, str]]]:
        lookup: Dict[str, Dict[str, Tuple[str, str]]] = {}
        for _, row in df_cw.iterrows():
            country = str(row.get("country", "")).strip().lower()
            code = _v131_norm_proc_token(row.get("raw_code"))
            if not country or not code:
                continue
            cls = str(row.get("mapped_class", "UNCLASSIFIED")).strip().upper()
            conf = str(row.get("mapping_confidence", "UNVERIFIED")).strip().upper()
            lookup.setdefault(country, {})[code] = (cls, conf)
        return lookup

    def classify_proc_v131(proc_code: Any, country: str) -> Tuple[str, str]:
        mapping = ns.get("PROC_LOOKUP", {}).get(str(country).lower(), {})
        matches = [mapping[t] for t in _v131_proc_tokens(proc_code) if t in mapping]
        if not matches:
            return "UNCLASSIFIED", "NA"
        class_priority = {
            "DC": 4, "CRAN": 3, "OTHER_CRAN": 2,
            "NONSURGICAL": 1, "NONNEURO_SURGERY": 1, "UNCLASSIFIED": 0,
        }
        conf_priority = {
            "HIGH": 4, "MODERATE": 3, "LOW": 2,
            "UNVERIFIED": 1, "NA": 0,
        }
        return max(
            matches,
            key=lambda x: (
                conf_priority.get(str(x[1]).upper(), -1),
                class_priority.get(str(x[0]).upper(), -1),
            ),
        )

    def build_crosswalk_table_v131(dirs_arg: Dict[str, Path]) -> pd.DataFrame:
        global PROC_LOOKUP
        df_cw = ns["load_or_create_crosswalk"](dirs_arg["metadata"])
        lookup = build_proc_lookup_v131(df_cw)
        ns["PROC_LOOKUP"] = lookup
        globals()["PROC_LOOKUP"] = lookup
        n_total = sum(len(v) for v in lookup.values())
        log.info(f"[CROSSWALK-v1.3.1] Lookup normalizado: {n_total} códigos totais")
        if n_total <= 25:
            log.warning(
                "[CROSSWALK-v1.3.1] O arquivo contém apenas um crosswalk inicial. "
                "Ele NÃO é suficiente para uma análise cirúrgica publicável."
            )
        return df_cw

    def apply_crosswalk_v131(df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        out["trauma_subtype"] = out["dx_main"].map(ns["classify_dx"])
        pairs = [
            classify_proc_v131(code, country)
            for code, country in zip(out.get("procedure_code_raw"), out.get("country"))
        ]
        out["procedure_class"] = pd.Series((p[0] for p in pairs), index=out.index, dtype="string")
        out["procedure_mapping_confidence"] = pd.Series(
            (p[1] for p in pairs), index=out.index, dtype="string"
        )
        accepted = set(ns.get("DC_ANALYSIS_MIN_CONFIDENCE", ["HIGH", "MODERATE"]))
        ok = out["procedure_mapping_confidence"].isin(accepted)
        out["procedure_class_final"] = out["procedure_class"].where(ok, "UNCLASSIFIED")
        out["surgery_any"] = out["procedure_class_final"].isin(
            ["DC", "CRAN", "OTHER_CRAN"]
        ).astype("Int64")
        log.info(
            "[CROSSWALK-v1.3.1] classes:\n%s\nconfiança:\n%s",
            out["procedure_class"].value_counts(dropna=False).to_string(),
            out["procedure_mapping_confidence"].value_counts(dropna=False).to_string(),
        )
        return out

    def export_procedure_inventory(country_dfs: Dict[str, Optional[pd.DataFrame]]) -> pd.DataFrame:
        rows: List[Dict[str, Any]] = []
        for country, df in country_dfs.items():
            if df is None or df.empty or "procedure_code_raw" not in df.columns:
                rows.append({
                    "country": country,
                    "raw_code": pd.NA,
                    "n_records": 0,
                    "procedure_field_nonmissing_pct": 0.0,
                    "status": "NO_PROCEDURE_FIELD_OR_NO_DATA",
                })
                continue
            nonmissing = df["procedure_code_raw"].notna().mean() * 100
            counts: Dict[str, int] = {}
            for value in df["procedure_code_raw"].dropna():
                for token in _v131_proc_tokens(value):
                    counts[token] = counts.get(token, 0) + 1
            if not counts:
                rows.append({
                    "country": country,
                    "raw_code": pd.NA,
                    "n_records": 0,
                    "procedure_field_nonmissing_pct": round(nonmissing, 3),
                    "status": "FIELD_PRESENT_BUT_NO_VALID_CODES",
                })
            else:
                for token, count in sorted(counts.items(), key=lambda x: (-x[1], x[0])):
                    mapping = ns.get("PROC_LOOKUP", {}).get(country, {}).get(token)
                    rows.append({
                        "country": country,
                        "raw_code": token,
                        "n_records": count,
                        "procedure_field_nonmissing_pct": round(nonmissing, 3),
                        "mapped_class": mapping[0] if mapping else "UNMAPPED",
                        "mapping_confidence": mapping[1] if mapping else "NA",
                        "status": "MAPPED" if mapping else "NEEDS_VALIDATION",
                    })
        inventory = pd.DataFrame(rows)
        ns["save_csv_xlsx"](
            inventory,
            Path(dirs["qc"]) / "procedure_code_inventory_observed",
        )
        return inventory

    def _volume_frame_v131(df_main: pd.DataFrame) -> pd.DataFrame:
        needed = [
            "country", "year", "hospital_id", "hospital_volume_year", "age",
            "sex", "trauma_subtype", "death_in_hospital", "los_days",
        ]
        model_df = df_main[[c for c in needed if c in df_main.columns]].copy()
        units = model_df[[
            "country", "year", "hospital_id", "hospital_volume_year"
        ]].dropna().drop_duplicates()
        units["log_volume"] = np.log1p(
            pd.to_numeric(units["hospital_volume_year"], errors="coerce").astype("float64")
        )
        grp = units.groupby(["country", "year"], observed=True)["log_volume"]
        means = grp.transform("mean")
        sds = grp.transform("std").replace(0, np.nan)
        units["volume_z_country_year"] = ((units["log_volume"] - means) / sds).fillna(0.0)
        model_df = model_df.merge(
            units[["country", "year", "hospital_id", "volume_z_country_year"]],
            on=["country", "year", "hospital_id"], how="left", validate="many_to_one",
        )
        model_df["age10"] = pd.to_numeric(model_df["age"], errors="coerce") / 10.0
        return model_df

    def _prepare_formula_data_v131(data: pd.DataFrame, outcome: str) -> Tuple[pd.DataFrame, List[str]]:
        base = [outcome, "volume_z_country_year", "age10", "hospital_id", "country", "year"]
        missing = [c for c in base if c not in data.columns]
        if missing:
            raise KeyError(f"Colunas ausentes para {outcome}: {missing}")
        optional = [c for c in ["sex", "trauma_subtype"] if c in data.columns]
        sub = data[base + optional].copy()

        for col in [outcome, "volume_z_country_year", "age10"]:
            sub[col] = pd.to_numeric(sub[col], errors="coerce").astype("float64")
        sub["year"] = pd.to_numeric(sub["year"], errors="coerce")
        sub = sub.dropna(subset=base)

        if outcome == "death_in_hospital":
            sub = sub[sub[outcome].isin([0.0, 1.0])].copy()
        else:
            sub = sub[sub[outcome] >= 1.0].copy()

        # Patsy 0.5/1.x may fail on pandas nullable dtypes. All categorical
        # formula variables are converted to ordinary Python-string object arrays.
        for col in ["hospital_id", "country", "year"] + optional:
            sub[col] = sub[col].astype("string").str.strip()
            sub[col] = sub[col].replace({"": pd.NA, "<NA>": pd.NA, "nan": pd.NA})
        sub = sub.dropna(subset=["hospital_id", "country", "year"] + optional)
        for col in ["hospital_id", "country", "year"] + optional:
            sub[col] = sub[col].astype(str).astype(object)

        # Avoid unstable dummy columns from tiny levels.
        if "trauma_subtype" in optional:
            freq = sub["trauma_subtype"].value_counts()
            rare = set(freq[freq < max(50, int(len(sub) * 0.0001))].index)
            if rare:
                sub.loc[sub["trauma_subtype"].isin(rare), "trauma_subtype"] = "OTHER_RARE"
        return sub, optional

    def _fit_one_glm_v131(
        data: pd.DataFrame,
        outcome: str,
        family: Any,
        include_country_interaction: bool,
        model_label: str,
    ) -> pd.DataFrame:
        try:
            sub, optional = _prepare_formula_data_v131(data, outcome)
        except Exception as exc:
            log.error(f"[MODEL-v1.3.1] {model_label}: preparação falhou: {exc}")
            return pd.DataFrame()

        n_countries = sub["country"].nunique()
        n_hospitals = sub["hospital_id"].nunique()
        if len(sub) < 200 or n_hospitals < 10 or sub[outcome].nunique() < 2:
            log.warning(
                f"[MODEL-v1.3.1] {model_label} ignorado: N={len(sub):,}, "
                f"hospitais={n_hospitals}, níveis desfecho={sub[outcome].nunique()}"
            )
            return pd.DataFrame()

        terms = ["volume_z_country_year", "age10"]
        if n_countries > 1:
            terms.append("C(country)")
        if sub["year"].nunique() > 1:
            terms.append("C(year)")
        for col in optional:
            if sub[col].nunique() > 1:
                terms.append(f"C({col})")
        if include_country_interaction and n_countries > 1:
            terms.append("volume_z_country_year:C(country)")
        formula = f"{outcome} ~ " + " + ".join(terms)

        log.info(
            f"[MODEL-v1.3.1] {model_label}: N={len(sub):,}; hospitais={n_hospitals}; "
            f"países={sorted(sub['country'].unique().tolist())}"
        )
        try:
            model = ns["smf"].glm(formula=formula, data=sub, family=family)
            fit = model.fit(
                cov_type="cluster",
                cov_kwds={
                    "groups": np.asarray(sub["hospital_id"], dtype=object),
                    "use_correction": True,
                },
                maxiter=100,
            )
            ci = fit.conf_int()
            table = pd.DataFrame({
                "term": fit.params.index.astype(str),
                "beta": np.asarray(fit.params, dtype=float),
                "se_cluster_hospital": np.asarray(fit.bse, dtype=float),
                "p_value": np.asarray(fit.pvalues, dtype=float),
                "ci_low_beta": np.asarray(ci.iloc[:, 0], dtype=float),
                "ci_high_beta": np.asarray(ci.iloc[:, 1], dtype=float),
            })
            table["exp_beta"] = np.exp(table["beta"])
            table["exp_ci_low"] = np.exp(table["ci_low_beta"])
            table["exp_ci_high"] = np.exp(table["ci_high_beta"])
            table["n_records"] = len(sub)
            table["n_hospitals"] = n_hospitals
            table["n_countries"] = n_countries
            table["countries_included"] = "|".join(sorted(sub["country"].unique()))
            table["model_label"] = model_label
            table["formula"] = formula
            table["converged"] = bool(getattr(fit, "converged", True))
            del fit, model, sub
            gc.collect()
            return table
        except Exception as exc:
            log.exception(f"[MODEL-v1.3.1] {model_label} falhou sem abortar o pipeline: {exc}")
            del sub
            gc.collect()
            return pd.DataFrame()

    def run_main_models_v131(df_main: pd.DataFrame) -> Dict[str, Any]:
        log.info("═" * 60)
        log.info("MODELOS INDIVIDUAIS v1.3.1 — DTYPE-SAFE / RAM-SAFE")
        log.info("Uma etapa; dados de internações; SE agrupado por hospital")
        log.info("═" * 60)

        mdf = _volume_frame_v131(df_main)
        eligibility_rows: List[Dict[str, Any]] = []
        for country, grp in mdf.groupby("country", observed=True):
            mortality = pd.to_numeric(grp.get("death_in_hospital"), errors="coerce")
            los = pd.to_numeric(grp.get("los_days"), errors="coerce")
            eligibility_rows.append({
                "country": country,
                "n_base": len(grp),
                "n_hospitals": grp["hospital_id"].nunique(),
                "n_mortality_valid": int(mortality.isin([0, 1]).sum()),
                "death_rate_valid_records": float(mortality[mortality.isin([0, 1])].mean())
                    if mortality.isin([0, 1]).any() else np.nan,
                "n_los_valid": int((los >= 1).sum()),
                "los_median_valid": float(los[los >= 1].median()) if (los >= 1).any() else np.nan,
            })
        eligibility = pd.DataFrame(eligibility_rows)
        ns["save_csv_xlsx"](
            eligibility,
            Path(dirs["qc"]) / "model_outcome_eligibility_v131",
        )
        log.info("[MODEL-v1.3.1] Elegibilidade:\n%s", eligibility.to_string(index=False))

        specifications = [
            (
                "mortality_main", "death_in_hospital",
                ns["sm"].families.Binomial(), False,
                "Tabela4_one_stage_mortalidade_v131",
            ),
            (
                "mortality_country_interaction", "death_in_hospital",
                ns["sm"].families.Binomial(), True,
                "Tabela4b_heterogeneidade_mortalidade_v131",
            ),
            (
                "los_main", "los_days",
                ns["sm"].families.NegativeBinomial(alpha=1.0), False,
                "Tabela5_one_stage_LOS_v131",
            ),
            (
                "los_country_interaction", "los_days",
                ns["sm"].families.NegativeBinomial(alpha=1.0), True,
                "Tabela5b_heterogeneidade_LOS_v131",
            ),
        ]

        output: Dict[str, Any] = {
            "analysis_type": "individual_record_one_stage_clustered_glm",
            "pipeline_version": MODEL_HOTFIX_VERSION,
            "meta_analysis_performed": False,
            "models": {},
        }
        for label, outcome, family, interaction, filename in specifications:
            table = _fit_one_glm_v131(mdf, outcome, family, interaction, label)
            if not table.empty:
                ns["save_csv_xlsx"](table, Path(dirs["tables"]) / filename)
            output["models"][label] = table.to_dict("records")
            del table
            gc.collect()

        # Country-specific sensitivity models. Numeric extension dtypes are
        # converted before passing to the older GEE helpers.
        sensitivity_mort: List[Dict[str, Any]] = []
        sensitivity_los: List[Dict[str, Any]] = []
        cdf = df_main.copy()
        cdf["log_vol"] = np.log1p(
            pd.to_numeric(cdf["hospital_volume_year"], errors="coerce").astype("float64")
        )
        for numeric_col in ["age", "year", "death_in_hospital", "los_days"]:
            if numeric_col in cdf.columns:
                cdf[numeric_col] = pd.to_numeric(cdf[numeric_col], errors="coerce").astype("float64")
        for cat_col in ["sex", "trauma_subtype", "hospital_id", "country"]:
            if cat_col in cdf.columns:
                cdf[cat_col] = cdf[cat_col].astype("string").astype(object)
        covs = ["age", "C(sex)", "C(trauma_subtype)", "C(year)"]
        for country in sorted(cdf["country"].dropna().unique()):
            sub = cdf[cdf["country"] == country].copy()
            if len(sub) < 100:
                continue
            try:
                r = ns["fit_gee_logistic"](
                    sub, "death_in_hospital", "log_vol", covs, "hospital_id", str(country)
                )
                if r:
                    sensitivity_mort.append(r)
            except Exception as exc:
                log.warning(f"[{country}] sensibilidade de mortalidade falhou: {exc}")
            try:
                r = ns["fit_gee_poisson_los"](
                    sub, "log_vol", covs, str(country)
                )
                if r:
                    sensitivity_los.append(r)
            except Exception as exc:
                log.warning(f"[{country}] sensibilidade de LOS falhou: {exc}")
            del sub
            gc.collect()

        ns["save_csv_xlsx"](
            pd.DataFrame(sensitivity_mort),
            Path(dirs["tables"]) / "TabelaS_modelos_pais_mortalidade_v131",
        )
        ns["save_csv_xlsx"](
            pd.DataFrame(sensitivity_los),
            Path(dirs["tables"]) / "TabelaS_modelos_pais_LOS_v131",
        )
        output["country_sensitivity_mortality"] = sensitivity_mort
        output["country_sensitivity_los"] = sensitivity_los
        # Backward-compatible keys used by the legacy forest-plot function.
        output["results_mort"] = sensitivity_mort
        output["results_los"] = sensitivity_los
        del cdf, mdf
        gc.collect()
        return output


    def fig1_flow_v131(df_cdm: pd.DataFrame, df_main: pd.DataFrame, df_surg: pd.DataFrame, df_dc: pd.DataFrame) -> None:
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(9, 10))
        ax.axis("off")
        boxes = [
            (0.5, 0.88, f"CDM harmonizado\nN = {len(df_cdm):,}", "#2c3e50"),
            (0.5, 0.65, f"Coorte principal: adultos com diagnóstico principal S06.x\nN = {len(df_main):,}", "#2980b9"),
            (0.5, 0.42, f"Coorte cirúrgica secundária com procedimento validado\nN = {len(df_surg):,}", "#16a085"),
            (0.5, 0.19, f"Subcoorte exploratória DC vs craniotomia\nN = {len(df_dc):,}", "#c0392b"),
        ]
        for i, (x, y, text, color) in enumerate(boxes):
            ax.text(
                x, y, text, ha="center", va="center", fontsize=10, color="white",
                fontweight="bold",
                bbox=dict(boxstyle="round,pad=0.6", facecolor=color, alpha=0.88, ec="white"),
            )
            if i < len(boxes) - 1:
                ax.annotate(
                    "", xy=(x, boxes[i + 1][1] + 0.08), xytext=(x, y - 0.08),
                    arrowprops=dict(arrowstyle="->", color="#555", lw=2),
                )
        ax.set_title("Fluxo das coortes analíticas", fontsize=13, fontweight="bold", pad=20)
        fig.tight_layout()
        fig.savefig(Path(dirs["fig_main"]) / "Figura1_fluxograma_v131.png", dpi=config["fig_dpi"], bbox_inches="tight")
        plt.close(fig)

    def fig_volume_mortality_v131(df_main: pd.DataFrame) -> None:
        import matplotlib.pyplot as plt
        valid = df_main.copy()
        valid["death_in_hospital"] = pd.to_numeric(valid["death_in_hospital"], errors="coerce")
        valid["hospital_volume_year"] = pd.to_numeric(valid["hospital_volume_year"], errors="coerce")
        valid = valid.dropna(subset=["death_in_hospital", "hospital_volume_year", "country"])
        valid = valid[valid["death_in_hospital"].isin([0, 1])]
        countries = sorted(valid["country"].unique())
        if not countries:
            return
        fig, axes = plt.subplots(1, len(countries), figsize=(5 * len(countries), 4.5), squeeze=False)
        for ax, country in zip(axes[0], countries):
            sub = valid[valid["country"] == country].copy()
            if len(sub) < 100:
                ax.text(0.5, 0.5, "N insuficiente", ha="center", va="center")
                continue
            try:
                sub["volume_bin"] = pd.qcut(sub["hospital_volume_year"], q=10, duplicates="drop")
                plot = sub.groupby("volume_bin", observed=True).agg(
                    volume=("hospital_volume_year", "median"),
                    mortality=("death_in_hospital", "mean"),
                    n=("death_in_hospital", "size"),
                ).reset_index(drop=True)
                ax.plot(plot["volume"], plot["mortality"], marker="o")
            except Exception:
                pass
            ax.set_title(str(country).title())
            ax.set_xlabel("Volume anual de internações S06.x/hospital")
            ax.set_ylabel("Mortalidade intra-hospitalar bruta")
            ax.set_ylim(bottom=0)
        fig.suptitle("Relação descritiva não ajustada entre volume hospitalar e mortalidade")
        fig.tight_layout()
        fig.savefig(Path(dirs["fig_main"]) / "Figura3_volume_mortalidade_v131.png", dpi=config["fig_dpi"], bbox_inches="tight")
        plt.close(fig)

    def run_main_figures_v131(df_cdm, df_main, df_dc, model_output, df_surg=None):
        log.info("FIGURAS PRINCIPAIS v1.3.1")
        if df_surg is None:
            df_surg = pd.DataFrame()
        try:
            fig1_flow_v131(df_cdm, df_main, df_surg, df_dc)
        except Exception as exc:
            log.exception(f"[FIG-v1.3.1] fluxograma falhou: {exc}")
        try:
            fig_volume_mortality_v131(df_main)
        except Exception as exc:
            log.exception(f"[FIG-v1.3.1] volume-mortalidade falhou: {exc}")
        try:
            ns["fig4_forest_plot"](
                model_output.get("results_mort", []),
                "Volume hospitalar e mortalidade — sensibilidades por país",
                "Figura4_forest_mortalidade_v131.png",
            )
            ns["fig4_forest_plot"](
                model_output.get("results_los", []),
                "Volume hospitalar e permanência — sensibilidades por país",
                "Figura4b_forest_LOS_v131.png",
            )
        except Exception as exc:
            log.exception(f"[FIG-v1.3.1] forest plots falharam: {exc}")
        try:
            ns["fig5_temporal_dc_trend"](df_dc)
        except Exception as exc:
            log.exception(f"[FIG-v1.3.1] tendência DC falhou: {exc}")

    old_pipeline = ns["run_pipeline_complete"]

    def run_pipeline_complete_v131(config_arg: dict = config, dirs_arg: dict = dirs):
        start = time.time()
        log.info(f"▶▶▶  PIPELINE TCE v{MODEL_HOTFIX_VERSION}  ◀◀◀")
        df_brasil = ns["run_brasil_ingestion"](config_arg, dirs_arg)
        gc.collect()
        df_mexico = ns["run_mexico_ingestion"](config_arg, dirs_arg)
        gc.collect()
        df_chile = ns["run_chile_ingestion"](config_arg, dirs_arg) if config_arg["countries"].get("chile") else None
        gc.collect()
        df_equador = ns["run_equador_ingestion"](config_arg, dirs_arg) if config_arg["countries"].get("equador") else None
        gc.collect()
        country_dfs = {
            "brasil": df_brasil,
            "mexico": df_mexico,
            "chile": df_chile,
            "equador": df_equador,
        }
        ns["run_raw_audit"](country_dfs)
        build_crosswalk_table_v131(dirs_arg)
        export_procedure_inventory(country_dfs)
        df_cdm, cdm_alerts = ns["harmonize_all"](country_dfs)
        df_main, df_surg, df_dc = ns["build_cohorts"](df_cdm)
        ns["run_all_tables"](df_main, df_surg, df_dc)
        model_output: Dict[str, Any] = {}
        if config_arg.get("run_main_analysis", True):
            model_output = run_main_models_v131(df_main)
        if config_arg.get("run_sensitivity", True):
            try:
                ns["run_all_sensitivity"](df_main, df_dc)
            except Exception as exc:
                log.exception(f"[SENSITIVITY-v1.3.1] falhou sem abortar relatório: {exc}")
        try:
            run_main_figures_v131(df_cdm, df_main, df_dc, model_output, df_surg=df_surg)
        except Exception as exc:
            log.exception(f"[FIG-MAIN-v1.3.1] falhou sem abortar: {exc}")
        try:
            ns["run_supplemental_figures"](df_cdm, df_main)
        except Exception as exc:
            log.exception(f"[FIG-SUPPL-v1.3.1] falhou sem abortar: {exc}")
        try:
            ns["generate_final_report"](
                country_dfs, df_cdm, df_main, df_dc, {}, cdm_alerts, start
            )
        except Exception as exc:
            log.exception(f"[REPORT-v1.3.1] falhou após salvar análises: {exc}")
        log.info("▶▶▶  PIPELINE v1.3.1 CONCLUÍDO  ◀◀◀")
        return df_cdm, df_main, df_surg, df_dc, model_output

    ns["sanitize_dataframe_for_parquet"] = sanitize_dataframe_for_parquet_v131
    ns["_build_proc_lookup"] = build_proc_lookup_v131
    ns["classify_procedure_with_confidence"] = classify_proc_v131
    ns["build_crosswalk_table"] = build_crosswalk_table_v131
    ns["apply_crosswalk"] = apply_crosswalk_v131
    ns["export_procedure_inventory"] = export_procedure_inventory
    ns["run_main_models"] = run_main_models_v131
    ns["run_main_figures"] = run_main_figures_v131
    ns["run_pipeline_complete"] = run_pipeline_complete_v131
    log.info(
        "[HOTFIX] v1.3.1 aplicado: dtype nativo para Patsy, modelos sequenciais, "
        "guards por desfecho e inventário procedimental auditável."
    )


def purge_analysis_outputs_v131(ns: Dict[str, Any]) -> None:
    """
    Remove only outputs derived after country ingestion.
    Mexico annual/raw/clean checkpoints are deliberately preserved.
    """
    base = Path(ns["CONFIG"]["base_dir"])
    targets = [
        base / "02_harmonized" / "tce_harmonized_cdm.parquet",
        base / "02_harmonized" / "cohort_main.parquet",
        base / "02_harmonized" / "cohort_surgical.parquet",
        base / "02_harmonized" / "cohort_dc_cran.parquet",
    ]
    for p in targets:
        if p.exists():
            p.unlink()
            print("Removido:", p)
    # Remove old derived tables/figures/models, but never raw/intermediate data.
    for dirname in ["04_tables", "05_figures_main", "06_figures_supplement", "07_models"]:
        folder = base / dirname
        if folder.exists():
            for p in folder.iterdir():
                if p.is_file():
                    p.unlink()
    print("Checkpoints mexicanos preservados.")


apply_model_hotfix_v131(globals())
print("✅ Hotfix TCE v1.3.1 carregado.")


# ===== FORCE HOTFIX v1.3.2 (consolidated) =====
# -*- coding: utf-8 -*-
"""
TCE Multinational Pipeline — FORCE HOTFIX v1.3.2

Designed to be appended to the full pipeline or executed with `%run -i` after
loading an older v1.3.x notebook/script.

Fixes:
- forces Patsy/statsmodels inputs to NumPy-native float64/object dtypes;
- overrides BOTH legacy and current model entry points;
- disables Chile/Ecuador by default until validated patient-level microdata exist;
- makes the purge function preserve all country ingestion checkpoints;
- fits models sequentially and prevents a secondary failure from aborting outputs;
- prints and verifies the exact active implementation before a long run.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import gc
import json
import time

import numpy as np
import pandas as pd

TCE_FORCE_HOTFIX_VERSION = "1.3.2"


def apply_tce_force_hotfix_v132(ns: Dict[str, Any]) -> None:
    required = [
        "CONFIG", "DIRS", "LOG", "sm", "smf", "save_csv_xlsx",
        "run_brasil_ingestion", "run_mexico_ingestion", "harmonize_all",
        "build_cohorts", "run_all_tables",
    ]
    missing = [name for name in required if name not in ns]
    if missing:
        raise RuntimeError(
            "A v1.3.2 precisa ser executada após o pipeline base. Ausentes: "
            + ", ".join(missing)
        )

    config = ns["CONFIG"]
    dirs = ns["DIRS"]
    log = ns["LOG"]

    # Scientifically safe defaults: the files currently present for Chile are
    # aggregate reports/metadata, and Ecuador has no validated local microdata.
    config.setdefault("countries", {})
    config["countries"]["brasil"] = True
    config["countries"]["mexico"] = True
    config["countries"]["chile"] = False
    config["countries"]["equador"] = False
    config["run_metaanalysis"] = False
    config["pipeline_version"] = TCE_FORCE_HOTFIX_VERSION

    def purge_derived_checkpoints_v132(namespace: Optional[Dict[str, Any]] = None) -> None:
        """Delete only derived analysis products; preserve every ingestion checkpoint."""
        active = ns if namespace is None else namespace
        base = Path(active["CONFIG"]["base_dir"])
        files = [
            base / "02_harmonized" / "tce_harmonized_cdm.parquet",
            base / "02_harmonized" / "cohort_main.parquet",
            base / "02_harmonized" / "cohort_surgical.parquet",
            base / "02_harmonized" / "cohort_dc_cran.parquet",
        ]
        for path in files:
            if path.exists():
                path.unlink()
                print("Removido derivado:", path)

        for dirname in [
            "04_tables", "05_figures_main", "06_figures_supplement",
            "07_models", "10_manuscript_support",
        ]:
            folder = base / dirname
            if not folder.exists():
                continue
            for path in folder.iterdir():
                if path.is_file():
                    path.unlink()
        print("✅ Checkpoints de Brasil e México preservados.")

    def _as_native_float(series: pd.Series) -> np.ndarray:
        numeric = pd.to_numeric(series, errors="coerce")
        # to_numpy(na_value=...) handles pandas Int64/Float64 extension arrays.
        return numeric.to_numpy(dtype=np.float64, na_value=np.nan)

    def _as_native_text(series: pd.Series, missing_label: str = "UNKNOWN") -> np.ndarray:
        values: List[str] = []
        for value in series.tolist():
            if value is None or value is pd.NA:
                values.append(missing_label)
                continue
            try:
                if pd.isna(value):
                    values.append(missing_label)
                    continue
            except Exception:
                pass
            text = str(value).strip()
            values.append(text if text and text.lower() not in {"nan", "none", "<na>"} else missing_label)
        return np.asarray(values, dtype=object)

    def _volume_frame_v132(df_main: pd.DataFrame) -> pd.DataFrame:
        needed = [
            "country", "year", "hospital_id", "hospital_volume_year", "age",
            "sex", "trauma_subtype", "death_in_hospital", "los_days",
        ]
        present = [col for col in needed if col in df_main.columns]
        required_here = {"country", "year", "hospital_id", "hospital_volume_year", "age"}
        absent = sorted(required_here - set(present))
        if absent:
            raise KeyError(f"Colunas ausentes para modelos: {absent}")

        model_df = df_main[present].copy()
        units = model_df[[
            "country", "year", "hospital_id", "hospital_volume_year"
        ]].dropna().drop_duplicates()
        units["hospital_volume_year"] = _as_native_float(units["hospital_volume_year"])
        units = units[np.isfinite(units["hospital_volume_year"])].copy()
        units["log_volume"] = np.log1p(units["hospital_volume_year"].astype(np.float64))
        grp = units.groupby(["country", "year"], observed=True)["log_volume"]
        means = grp.transform("mean")
        sds = grp.transform("std").replace(0, np.nan)
        units["volume_z_country_year"] = (
            (units["log_volume"] - means) / sds
        ).fillna(0.0).astype(np.float64)
        model_df = model_df.merge(
            units[["country", "year", "hospital_id", "volume_z_country_year"]],
            on=["country", "year", "hospital_id"],
            how="left",
            validate="many_to_one",
        )
        model_df["age10"] = _as_native_float(model_df["age"]) / 10.0
        return model_df

    def _prepare_native_formula_frame_v132(
        data: pd.DataFrame,
        outcome: str,
    ) -> Tuple[pd.DataFrame, List[str]]:
        base = [outcome, "volume_z_country_year", "age10", "hospital_id", "country", "year"]
        missing = [col for col in base if col not in data.columns]
        if missing:
            raise KeyError(f"Colunas ausentes para {outcome}: {missing}")
        optional = [col for col in ["sex", "trauma_subtype"] if col in data.columns]
        raw = data[base + optional].copy()

        outcome_arr = _as_native_float(raw[outcome])
        volume_arr = _as_native_float(raw["volume_z_country_year"])
        age_arr = _as_native_float(raw["age10"])
        year_arr = _as_native_float(raw["year"])

        mask = (
            np.isfinite(outcome_arr)
            & np.isfinite(volume_arr)
            & np.isfinite(age_arr)
            & np.isfinite(year_arr)
        )
        if outcome == "death_in_hospital":
            mask &= np.isin(outcome_arr, [0.0, 1.0])
        else:
            mask &= outcome_arr >= 1.0

        raw = raw.loc[mask].reset_index(drop=True)
        outcome_arr = outcome_arr[mask]
        volume_arr = volume_arr[mask]
        age_arr = age_arr[mask]
        year_arr = year_arr[mask]

        native: Dict[str, Any] = {
            outcome: np.asarray(outcome_arr, dtype=np.float64),
            "volume_z_country_year": np.asarray(volume_arr, dtype=np.float64),
            "age10": np.asarray(age_arr, dtype=np.float64),
            "hospital_id": _as_native_text(raw["hospital_id"], "MISSING_HOSPITAL"),
            "country": _as_native_text(raw["country"], "MISSING_COUNTRY"),
            "year": np.asarray(
                [str(int(x)) if float(x).is_integer() else str(float(x)) for x in year_arr],
                dtype=object,
            ),
        }
        for col in optional:
            native[col] = _as_native_text(raw[col], "UNKNOWN")

        sub = pd.DataFrame(native)
        # Remove unusable identifiers, but retain missing sex/subtype as an explicit category.
        sub = sub[
            (sub["hospital_id"] != "MISSING_HOSPITAL")
            & (sub["country"] != "MISSING_COUNTRY")
        ].reset_index(drop=True)

        if "trauma_subtype" in optional and len(sub):
            counts = sub["trauma_subtype"].value_counts(dropna=False)
            threshold = max(50, int(len(sub) * 0.0001))
            rare = set(counts[counts < threshold].index.tolist())
            if rare:
                vals = sub["trauma_subtype"].to_numpy(dtype=object, copy=True)
                vals[np.isin(vals, list(rare))] = "OTHER_RARE"
                sub["trauma_subtype"] = vals

        # Hard guard: Patsy must never receive pandas nullable extension dtypes.
        bad = {
            col: str(dtype)
            for col, dtype in sub.dtypes.items()
            if pd.api.types.is_extension_array_dtype(dtype)
        }
        if bad:
            raise TypeError(f"Dtypes de extensão ainda presentes: {bad}")
        return sub, optional

    def _fit_glm_v132(
        data: pd.DataFrame,
        outcome: str,
        family: Any,
        interaction: bool,
        label: str,
    ) -> pd.DataFrame:
        try:
            sub, optional = _prepare_native_formula_frame_v132(data, outcome)
        except Exception as exc:
            log.exception(f"[MODEL-v1.3.2] {label}: preparação falhou: {exc}")
            return pd.DataFrame()

        n_records = len(sub)
        n_hospitals = int(sub["hospital_id"].nunique()) if n_records else 0
        n_countries = int(sub["country"].nunique()) if n_records else 0
        n_outcomes = int(sub[outcome].nunique()) if n_records else 0
        if n_records < 200 or n_hospitals < 10 or n_outcomes < 2:
            log.warning(
                f"[MODEL-v1.3.2] {label} ignorado: N={n_records:,}; "
                f"hospitais={n_hospitals}; níveis={n_outcomes}"
            )
            return pd.DataFrame()

        terms = ["volume_z_country_year", "age10"]
        if n_countries > 1:
            terms.append("C(country)")
        if sub["year"].nunique() > 1:
            terms.append("C(year)")
        for col in optional:
            if sub[col].nunique() > 1:
                terms.append(f"C({col})")
        if interaction and n_countries > 1:
            terms.append("volume_z_country_year:C(country)")
        formula = f"{outcome} ~ " + " + ".join(terms)

        log.info(
            f"[MODEL-v1.3.2] {label}: N={n_records:,}; hospitais={n_hospitals}; "
            f"países={sorted(sub['country'].unique().tolist())}; fórmula={formula}"
        )
        try:
            model = ns["smf"].glm(formula=formula, data=sub, family=family)
            groups = sub["hospital_id"].to_numpy(dtype=object, copy=False)
            fit = model.fit(
                cov_type="cluster",
                cov_kwds={"groups": groups, "use_correction": True},
                maxiter=100,
            )
            ci = fit.conf_int()
            table = pd.DataFrame({
                "term": np.asarray(fit.params.index.astype(str), dtype=object),
                "beta": np.asarray(fit.params, dtype=np.float64),
                "se_cluster_hospital": np.asarray(fit.bse, dtype=np.float64),
                "p_value": np.asarray(fit.pvalues, dtype=np.float64),
                "ci_low_beta": np.asarray(ci.iloc[:, 0], dtype=np.float64),
                "ci_high_beta": np.asarray(ci.iloc[:, 1], dtype=np.float64),
            })
            table["exp_beta"] = np.exp(table["beta"])
            table["exp_ci_low"] = np.exp(table["ci_low_beta"])
            table["exp_ci_high"] = np.exp(table["ci_high_beta"])
            table["n_records"] = n_records
            table["n_hospitals"] = n_hospitals
            table["n_countries"] = n_countries
            table["countries_included"] = "|".join(sorted(sub["country"].unique().tolist()))
            table["model_label"] = label
            table["formula"] = formula
            table["converged"] = bool(getattr(fit, "converged", True))
            del fit, model, sub
            gc.collect()
            return table
        except Exception as exc:
            log.exception(f"[MODEL-v1.3.2] {label} falhou sem abortar: {exc}")
            del sub
            gc.collect()
            return pd.DataFrame()

    def _volume_effect_record(
        table: pd.DataFrame,
        country: str,
        outcome: str,
    ) -> Optional[Dict[str, Any]]:
        if table.empty:
            return None
        row = table.loc[table["term"] == "volume_z_country_year"]
        if row.empty:
            return None
        r = row.iloc[0]
        return {
            "country": country,
            "outcome": outcome,
            "effect": float(r["exp_beta"]),
            "ci_low": float(r["exp_ci_low"]),
            "ci_high": float(r["exp_ci_high"]),
            "p_value": float(r["p_value"]),
            "n": int(r["n_records"]),
            "n_hospitals": int(r["n_hospitals"]),
        }

    def run_main_models_v132(df_main: pd.DataFrame) -> Dict[str, Any]:
        log.info("═" * 60)
        log.info("MODELOS INDIVIDUAIS v1.3.2 — NUMPY-NATIVE / RAM-SAFE")
        log.info("One-stage GLM; SE agrupado por hospital; sem meta-análise")
        log.info("═" * 60)

        mdf = _volume_frame_v132(df_main)
        eligibility_rows: List[Dict[str, Any]] = []
        for country, grp in mdf.groupby("country", observed=True):
            death = pd.to_numeric(grp.get("death_in_hospital"), errors="coerce")
            los = pd.to_numeric(grp.get("los_days"), errors="coerce")
            eligibility_rows.append({
                "country": str(country),
                "n_base": int(len(grp)),
                "n_hospitals": int(grp["hospital_id"].nunique()),
                "n_mortality_valid": int(death.isin([0, 1]).sum()),
                "mortality_valid_pct": float(death.isin([0, 1]).mean() * 100),
                "n_los_valid": int((los >= 1).sum()),
                "los_valid_pct": float((los >= 1).mean() * 100),
            })
        eligibility = pd.DataFrame(eligibility_rows)
        ns["save_csv_xlsx"](
            eligibility,
            Path(dirs["qc"]) / "model_outcome_eligibility_v132",
        )
        log.info("[MODEL-v1.3.2] Elegibilidade:\n%s", eligibility.to_string(index=False))

        specs = [
            ("mortality_main", "death_in_hospital", ns["sm"].families.Binomial(), False,
             "Tabela4_one_stage_mortalidade_v132"),
            ("mortality_country_interaction", "death_in_hospital", ns["sm"].families.Binomial(), True,
             "Tabela4b_heterogeneidade_mortalidade_v132"),
            ("los_main", "los_days", ns["sm"].families.NegativeBinomial(alpha=1.0), False,
             "Tabela5_one_stage_LOS_v132"),
            ("los_country_interaction", "los_days", ns["sm"].families.NegativeBinomial(alpha=1.0), True,
             "Tabela5b_heterogeneidade_LOS_v132"),
        ]

        output: Dict[str, Any] = {
            "analysis_type": "individual_record_one_stage_clustered_glm",
            "pipeline_version": TCE_FORCE_HOTFIX_VERSION,
            "meta_analysis_performed": False,
            "models": {},
            "results_mort": [],
            "results_los": [],
        }
        for label, outcome, family, interaction, filename in specs:
            table = _fit_glm_v132(mdf, outcome, family, interaction, label)
            if not table.empty:
                ns["save_csv_xlsx"](table, Path(dirs["tables"]) / filename)
            output["models"][label] = table.to_dict("records")
            if label == "mortality_main":
                output["one_stage_mortality"] = table.to_dict("records")
            elif label == "mortality_country_interaction":
                output["one_stage_mortality_heterogeneity"] = table.to_dict("records")
            elif label == "los_main":
                output["one_stage_los"] = table.to_dict("records")
            elif label == "los_country_interaction":
                output["one_stage_los_heterogeneity"] = table.to_dict("records")
            del table
            gc.collect()

        # Country-specific models for heterogeneity/sensitivity; no pooling.
        for country in sorted(mdf["country"].dropna().astype(str).unique().tolist()):
            country_df = mdf[mdf["country"].astype(str) == country].copy()
            mort = _fit_glm_v132(
                country_df, "death_in_hospital", ns["sm"].families.Binomial(),
                False, f"mortality_{country}",
            )
            los = _fit_glm_v132(
                country_df, "los_days", ns["sm"].families.NegativeBinomial(alpha=1.0),
                False, f"los_{country}",
            )
            mort_record = _volume_effect_record(mort, country, "mortality")
            los_record = _volume_effect_record(los, country, "los")
            if mort_record:
                output["results_mort"].append(mort_record)
            if los_record:
                output["results_los"].append(los_record)
            del mort, los, country_df
            gc.collect()

        ns["save_csv_xlsx"](
            pd.DataFrame(output["results_mort"]),
            Path(dirs["tables"]) / "TabelaS_modelos_pais_mortalidade_v132",
        )
        ns["save_csv_xlsx"](
            pd.DataFrame(output["results_los"]),
            Path(dirs["tables"]) / "TabelaS_modelos_pais_LOS_v132",
        )
        model_json = Path(dirs["models"]) / "model_output_v132.json"
        model_json.write_text(json.dumps(output, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        del mdf
        gc.collect()
        return output

    def run_pipeline_complete_v132(config_arg: Optional[dict] = None, dirs_arg: Optional[dict] = None):
        active_config = config if config_arg is None else config_arg
        active_dirs = dirs if dirs_arg is None else dirs_arg

        # Guard against accidentally reactivating unsupported sources in the same session.
        active_config.setdefault("countries", {})
        active_config["countries"]["chile"] = False
        active_config["countries"]["equador"] = False

        if ns.get("run_main_models") is not run_main_models_v132:
            raise RuntimeError(
                "A função de modelos v1.3.2 não está ativa. Reinicie o runtime e execute apenas a v1.3.2."
            )

        start = time.time()
        log.info("▶▶▶  PIPELINE TCE v1.3.2 FORCE-ACTIVE  ◀◀◀")
        log.info("[ACTIVE] run_pipeline_complete_v132 + run_main_models_v132")
        log.info("[COUNTRIES] Brasil=True; México=True; Chile=False; Equador=False")

        df_brasil = ns["run_brasil_ingestion"](active_config, active_dirs)
        gc.collect()
        df_mexico = ns["run_mexico_ingestion"](active_config, active_dirs)
        gc.collect()
        df_chile = None
        df_equador = None
        country_dfs = {
            "brasil": df_brasil,
            "mexico": df_mexico,
            "chile": df_chile,
            "equador": df_equador,
        }

        ns["run_raw_audit"](country_dfs)
        ns["build_crosswalk_table"](active_dirs)
        if "export_procedure_inventory" in ns:
            try:
                ns["export_procedure_inventory"](country_dfs)
            except Exception as exc:
                log.exception(f"[PROC-INVENTORY-v1.3.2] falhou sem abortar: {exc}")

        df_cdm, cdm_alerts = ns["harmonize_all"](country_dfs)
        df_main, df_surg, df_dc = ns["build_cohorts"](df_cdm)
        ns["run_all_tables"](df_main, df_surg, df_dc)

        model_output: Dict[str, Any] = {}
        if active_config.get("run_main_analysis", True):
            model_output = run_main_models_v132(df_main)

        if active_config.get("run_sensitivity", True):
            try:
                ns["run_all_sensitivity"](df_main, df_dc)
            except Exception as exc:
                log.exception(f"[SENSITIVITY-v1.3.2] falhou sem abortar resultados principais: {exc}")

        try:
            ns["run_main_figures"](df_cdm, df_main, df_dc, model_output)
        except Exception as exc:
            log.exception(f"[FIG-MAIN-v1.3.2] falhou sem abortar: {exc}")
        try:
            ns["run_supplemental_figures"](df_cdm, df_main)
        except Exception as exc:
            log.exception(f"[FIG-SUPPL-v1.3.2] falhou sem abortar: {exc}")
        try:
            ns["generate_final_report"](
                country_dfs, df_cdm, df_main, df_dc, {}, cdm_alerts, start
            )
        except Exception as exc:
            log.exception(f"[REPORT-v1.3.2] falhou após salvar análises: {exc}")

        log.info("▶▶▶  PIPELINE TCE v1.3.2 CONCLUÍDO  ◀◀◀")
        return df_cdm, df_main, df_surg, df_dc, model_output

    def verify_tce_v132() -> Dict[str, Any]:
        status = {
            "pipeline_version": config.get("pipeline_version"),
            "run_pipeline_complete": getattr(ns.get("run_pipeline_complete"), "__name__", None),
            "run_main_models": getattr(ns.get("run_main_models"), "__name__", None),
            "run_main_models_individual": getattr(ns.get("run_main_models_individual"), "__name__", None),
            "countries": dict(config.get("countries", {})),
        }
        expected = {
            "run_pipeline_complete": "run_pipeline_complete_v132",
            "run_main_models": "run_main_models_v132",
            "run_main_models_individual": "run_main_models_v132",
        }
        bad = {k: (status.get(k), v) for k, v in expected.items() if status.get(k) != v}
        if bad:
            raise RuntimeError(f"Hotfix v1.3.2 não ficou ativo: {bad}")
        print(json.dumps(status, ensure_ascii=False, indent=2))
        return status

    # Override every legacy entry point, including the exact name shown in the traceback.
    ns["purge_derived_checkpoints"] = purge_derived_checkpoints_v132
    ns["purge_analysis_outputs_v131"] = purge_derived_checkpoints_v132
    ns["run_main_models_individual"] = run_main_models_v132
    ns["run_main_models_v131"] = run_main_models_v132
    ns["run_main_models"] = run_main_models_v132
    ns["run_pipeline_complete_v132"] = run_pipeline_complete_v132
    ns["run_pipeline_complete"] = run_pipeline_complete_v132
    ns["verify_tce_v132"] = verify_tce_v132
    ns["_prepare_native_formula_frame_v132"] = _prepare_native_formula_frame_v132
    ns["_fit_glm_v132"] = _fit_glm_v132
    ns["ACTIVE_TCE_PATCH"] = TCE_FORCE_HOTFIX_VERSION

    log.info(
        "[HOTFIX] v1.3.2 FORCE aplicado: funções antigas substituídas, "
        "Patsy recebe somente dtypes NumPy nativos, checkpoints preservados."
    )


if "CONFIG" in globals() and "DIRS" in globals() and "LOG" in globals():
    apply_tce_force_hotfix_v132(globals())
    print("✅ TCE FORCE HOTFIX v1.3.2 carregado.")
    verify_tce_v132()
else:
    print(
        "Este é um hotfix. No Colab, execute após o pipeline base com:\n"
        "  %run -i /content/tce_hotfix_v1_3_2_force.py"
    )


# ============================================================================
# EMBEDDED POST-ANALYSIS v1.4.1 (corrected pandas nullable-dtype handling)
# ============================================================================
"""
TCE MULTINACIONAL — POST-ANALYSIS v1.4
=======================================
Objetivos
---------
1. Corrigir tabelas e figuras da v1.3.2 sem sobrescrever os outputs antigos.
2. Tratar ausência estrutural como NA, nunca como zero.
3. Usar hospital-ano como unidade para volume e centralização.
4. Harmonizar a escala do volume em todos os modelos.
5. Acrescentar análises temporais, por subtipo e de concentração hospitalar.
6. Rodar modelos de associação (não prognóstico clínico/causal) com idade flexível.
7. Incluir uma sensibilidade com volume defasado em 1 ano.

Execução no Colab
-----------------
%run /content/tce_postanalysis_v1_4.py
results_v14 = run_postanalysis_v14()

O script lê os parquets da pasta padrão do projeto, mas também aceita df_main/df_cdm
já existentes no namespace:
results_v14 = run_postanalysis_v14(df_main=df_main, df_cdm=df_cdm)
"""


import gc
import json
import math
import re
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

try:
    import statsmodels.api as sm
    import statsmodels.formula.api as smf
except Exception as exc:  # pragma: no cover
    raise RuntimeError("statsmodels é necessário para a etapa de modelos.") from exc


VERSION = "1.4.1"
DEFAULT_ROOT = Path("/content/drive/MyDrive/Projeto_TCE_Multinacional")

COUNTRY_LABEL = {
    "brasil": "Brasil",
    "mexico": "México",
    "chile": "Chile",
    "equador": "Equador",
}
COUNTRY_COLOR = {
    "brasil": "#1f77b4",
    "mexico": "#d62728",
    "chile": "#2ca02c",
    "equador": "#ff7f0e",
}


@dataclass
class V14Dirs:
    root: Path
    tables: Path
    fig_main: Path
    fig_suppl: Path
    models: Path
    qc: Path
    support: Path


def _make_dirs(root: Path) -> V14Dirs:
    d = V14Dirs(
        root=root,
        tables=root / "04_tables_v14",
        fig_main=root / "05_figures_main_v14",
        fig_suppl=root / "06_figures_supplement_v14",
        models=root / "07_models_v14",
        qc=root / "03_qc_v14",
        support=root / "10_manuscript_support_v14",
    )
    for p in (d.tables, d.fig_main, d.fig_suppl, d.models, d.qc, d.support):
        p.mkdir(parents=True, exist_ok=True)
    return d


def _save_table(df: pd.DataFrame, stem: Path) -> None:
    stem.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(stem.with_suffix(".csv"), index=False, encoding="utf-8-sig")
    try:
        df.to_excel(stem.with_suffix(".xlsx"), index=False)
    except Exception as exc:
        warnings.warn(f"XLSX não salvo para {stem.name}: {exc}")


def _safe_read_parquet(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(path)
    try:
        return pd.read_parquet(path)
    except Exception as exc:
        raise RuntimeError(
            f"Falha ao ler {path}. No Colab, instale/importe pyarrow antes de executar."
        ) from exc


def _country_sort(values: Iterable[str]) -> List[str]:
    order = ["brasil", "mexico", "chile", "equador"]
    present = set(str(x) for x in values if pd.notna(x))
    return [x for x in order if x in present] + sorted(present.difference(order))


def _normalize_types(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in out.columns:
        s = out[col]
        if isinstance(s.dtype, pd.CategoricalDtype):
            out[col] = s.astype(object)
        elif pd.api.types.is_extension_array_dtype(s.dtype):
            if pd.api.types.is_numeric_dtype(s.dtype):
                out[col] = pd.to_numeric(s, errors="coerce").astype(float)
            else:
                out[col] = s.astype(object)
    return out


def _as_numeric(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


def _format_pct(n: int, den: int, digits: int = 1) -> str:
    if den <= 0:
        return "NA"
    return f"{n:,}/{den:,} ({100*n/den:.{digits}f}%)"


def _median_iqr(s: pd.Series, valid: Optional[pd.Series] = None) -> str:
    x = _as_numeric(s)
    if valid is not None:
        x = x[valid]
    x = x.dropna()
    if x.empty:
        return "NA"
    q1, med, q3 = x.quantile([0.25, 0.50, 0.75]).tolist()
    return f"{med:.0f} [{q1:.0f}–{q3:.0f}]"


def _wilson_interval(k: int, n: int, z: float = 1.959963984540054) -> Tuple[float, float]:
    if n <= 0:
        return (np.nan, np.nan)
    p = k / n
    denom = 1 + z*z/n
    center = (p + z*z/(2*n)) / denom
    half = z * math.sqrt((p*(1-p)/n) + z*z/(4*n*n)) / denom
    return (max(0.0, center-half), min(1.0, center+half))


def _gini(values: Sequence[float]) -> float:
    x = np.asarray(values, dtype=float)
    x = x[np.isfinite(x) & (x >= 0)]
    if x.size == 0 or np.sum(x) == 0:
        return np.nan
    x = np.sort(x)
    n = x.size
    return float((2*np.sum((np.arange(1, n+1))*x)/(n*np.sum(x))) - (n+1)/n)


def _normalize_procedure_code(value: Any, width: int = 10) -> Optional[str]:
    """Converte 403010020.0 -> 0403010020 sem inventar códigos ausentes."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    text = str(value).strip()
    text = re.sub(r"\.0+$", "", text)
    digits = re.sub(r"\D", "", text)
    if not digits:
        return None
    return digits.zfill(width)


def build_brazil_procedure_crosswalk_v14() -> pd.DataFrame:
    """
    Crosswalk conservador para o procedimento PRINCIPAL da AIH.
    A classe DECOMPRESSIVE_CODED é proxy administrativa de procedimento descompressivo,
    não confirmação anatômica de craniectomia com flap não recolocado.
    """
    rows = [
        ("0403010020", "Craniotomia descompressiva", "DECOMPRESSIVE_CODED", "HIGH", 1),
        ("0403010039", "Craniotomia descompressiva da fossa posterior", "DECOMPRESSIVE_CODED", "HIGH", 1),
        ("0403010268", "Tratamento cirúrgico de fratura do crânio com afundamento", "ACUTE_CRANIAL_SURGERY", "HIGH", 1),
        ("0403010276", "Tratamento cirúrgico de hematoma extradural", "ACUTE_CRANIAL_SURGERY", "HIGH", 1),
        ("0403010284", "Tratamento cirúrgico de hematoma intracerebral", "ACUTE_CRANIAL_SURGERY", "HIGH", 1),
        ("0403010292", "Tratamento cirúrgico de hematoma intracerebral com técnica complementar", "ACUTE_CRANIAL_SURGERY", "HIGH", 1),
        ("0403010306", "Tratamento cirúrgico de hematoma subdural agudo", "ACUTE_CRANIAL_SURGERY", "HIGH", 1),
        ("0403010314", "Tratamento cirúrgico de hematoma subdural crônico", "CHRONIC_SDH_SURGERY", "HIGH", 0),
        ("0403010349", "Trepanação craniana/monitorização de PIC", "ICP_MONITORING_OR_TREPANATION", "HIGH", 0),
        ("0415020077", "Procedimentos sequenciais em neurocirurgia", "GENERIC_NEUROSURGERY", "MODERATE", 0),
        ("0415010012", "Procedimentos múltiplos", "GENERIC_MULTIPLE_PROCEDURES", "LOW", 0),
    ]
    return pd.DataFrame(
        rows,
        columns=["normalized_code", "description", "procedure_group_v14", "mapping_confidence", "include_primary_acute_surgery"],
    )


def apply_brazil_procedure_mapping(df: pd.DataFrame, crosswalk: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["procedure_code_norm"] = out.get("procedure_code_raw", pd.Series(index=out.index, dtype=object)).map(_normalize_procedure_code)
    out = out.merge(crosswalk, how="left", left_on="procedure_code_norm", right_on="normalized_code")
    out["procedure_group_v14"] = out["procedure_group_v14"].fillna("UNCLASSIFIED")
    out["include_primary_acute_surgery"] = out["include_primary_acute_surgery"].fillna(0).astype(int)
    return out


def build_variable_availability(df: pd.DataFrame) -> pd.DataFrame:
    variables = [
        "age", "sex", "dx_main", "trauma_subtype", "death_in_hospital", "los_days",
        "icu_any", "icu_days", "procedure_code_raw", "transfer_proxy", "residence_region",
        "external_cause", "dx_secondary", "cost_local_currency",
    ]
    rows: List[Dict[str, Any]] = []
    for country in _country_sort(df["country"].dropna().unique()):
        sub = df[df["country"].astype(str) == country]
        for var in variables:
            if var not in sub.columns:
                status, n_valid, pct = "ABSENT_COLUMN", 0, 0.0
            else:
                n_valid = int(sub[var].notna().sum())
                pct = 100*n_valid/len(sub) if len(sub) else np.nan
                if n_valid == 0:
                    status = "STRUCTURALLY_UNAVAILABLE"
                elif pct < 50:
                    status = "HIGH_MISSINGNESS"
                elif pct < 95:
                    status = "PARTIAL"
                else:
                    status = "AVAILABLE"
            rows.append({
                "country": country,
                "variable": var,
                "n_records": len(sub),
                "n_non_missing": n_valid,
                "availability_pct": round(pct, 2),
                "status": status,
            })
    return pd.DataFrame(rows)


def build_table1(df: pd.DataFrame) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    for country in _country_sort(df["country"].dropna().unique()):
        sub = df[df["country"].astype(str) == country].copy()
        n = len(sub)
        age = _as_numeric(sub["age"])
        sex = sub["sex"].astype(str).str.upper()
        male = sex.isin(["M", "MALE", "MASCULINO", "1"])
        death = _as_numeric(sub["death_in_hospital"])
        death_valid = death.isin([0, 1])
        los = _as_numeric(sub["los_days"])
        los_valid = los.ge(1)
        los_zero = los.eq(0)

        def binary_summary(col: str) -> str:
            if col not in sub.columns:
                return "NA"
            x = _as_numeric(sub[col])
            valid = x.isin([0, 1])
            if valid.sum() == 0:
                return "NA"
            return _format_pct(int((x[valid] == 1).sum()), int(valid.sum()))

        years = sorted(pd.to_numeric(sub["year"], errors="coerce").dropna().astype(int).unique().tolist())
        rows.append({
            "País": COUNTRY_LABEL.get(country, country.title()),
            "N": n,
            "Período observado": f"{min(years)}–{max(years)}" if years else "NA",
            "Hospitais únicos": int(sub["hospital_id"].nunique()),
            "Idade, mediana [IQR]": _median_iqr(age),
            "Sexo masculino, n/N (%)": _format_pct(int(male.sum()), int(sex.notna().sum())),
            "Óbito intra-hospitalar, n/N (%)": _format_pct(int((death[death_valid] == 1).sum()), int(death_valid.sum())),
            "LOS ≥1 dia, N válido": int(los_valid.sum()),
            "LOS, mediana [IQR]": _median_iqr(los, los_valid),
            "LOS = 0 dia, n/N (%)": _format_pct(int(los_zero.sum()), int(los.notna().sum())),
            "UTI, n/N disponível (%)": binary_summary("icu_any"),
            "Transferência/proxy, n/N disponível (%)": binary_summary("transfer_proxy"),
            "Procedimento principal disponível, n/N (%)": _format_pct(
                int(sub.get("procedure_code_raw", pd.Series(index=sub.index, dtype=object)).notna().sum()), n
            ),
        })
    return pd.DataFrame(rows)


def add_volume_fields(df: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame]:
    out = df.copy()
    out["year"] = pd.to_numeric(out["year"], errors="coerce")
    if "hospital_volume_year" not in out.columns:
        out["hospital_volume_year"] = out.groupby(["country", "hospital_id", "year"])["hospital_id"].transform("size")
    out["hospital_volume_year"] = pd.to_numeric(out["hospital_volume_year"], errors="coerce")

    # Only genuine hospital-year units can contribute to hospital-volume metrics.
    # Countries without a verified stable hospital identifier remain in patient-level
    # analyses, but are intentionally excluded from volume/centralization calculations.
    hy = (
        out[["country", "hospital_id", "year", "hospital_volume_year"]]
        .dropna(subset=["country", "hospital_id", "year", "hospital_volume_year"])
        .drop_duplicates(["country", "hospital_id", "year"])
        .copy()
    )
    hy["log_volume"] = np.log1p(hy["hospital_volume_year"].astype(float))

    def z_by_group(s: pd.Series) -> pd.Series:
        # pandas nullable dtypes can return pd.NA for all-missing groups.
        # Convert to plain float64 and preserve missing first-year lag values.
        x = pd.to_numeric(s, errors="coerce").astype("float64")
        valid = x.notna()
        result = pd.Series(np.nan, index=s.index, dtype="float64")
        if int(valid.sum()) == 0:
            return result
        mean = float(x.loc[valid].mean())
        sd = float(x.loc[valid].std(ddof=0)) if int(valid.sum()) > 1 else 0.0
        if (not np.isfinite(sd)) or sd <= 0.0:
            result.loc[valid] = 0.0
            return result
        result.loc[valid] = (x.loc[valid] - mean) / sd
        return result

    hy["volume_z_country_year_v14"] = hy.groupby(
        ["country", "year"], observed=True
    )["log_volume"].transform(z_by_group)

    # Quartis e decis são definidos em hospital-ano, dentro de país-ano.
    def safe_qcut(s: pd.Series, q: int, labels: Sequence[str]) -> pd.Series:
        if s.nunique() < 2:
            return pd.Series([labels[-1]] * len(s), index=s.index, dtype=object)
        ranked = s.rank(method="first")
        try:
            return pd.qcut(ranked, q=q, labels=labels).astype(object)
        except Exception:
            return pd.Series([labels[-1]] * len(s), index=s.index, dtype=object)

    hy["volume_quartile_hy_v14"] = hy.groupby(
        ["country", "year"], observed=True
    )["hospital_volume_year"].transform(
        lambda s: safe_qcut(s, 4, ["Q1", "Q2", "Q3", "Q4"])
    )
    hy["volume_decile_hy_v14"] = hy.groupby(
        ["country", "year"], observed=True
    )["hospital_volume_year"].transform(
        lambda s: safe_qcut(s, 10, [f"D{i}" for i in range(1, 11)])
    )

    hy = hy.sort_values(["country", "hospital_id", "year"])
    hy["lag_volume"] = hy.groupby(["country", "hospital_id"])["hospital_volume_year"].shift(1)
    hy["log_lag_volume"] = np.log1p(hy["lag_volume"])
    hy["lag_volume_z_country_year_v14"] = hy.groupby(
        ["country", "year"], observed=True
    )["log_lag_volume"].transform(z_by_group)

    out = out.drop(columns=[c for c in [
        "volume_z_country_year_v14", "volume_quartile_hy_v14", "volume_decile_hy_v14",
        "lag_volume", "lag_volume_z_country_year_v14"
    ] if c in out.columns])
    out = out.merge(
        hy[[
            "country", "hospital_id", "year", "volume_z_country_year_v14",
            "volume_quartile_hy_v14", "volume_decile_hy_v14",
            "lag_volume", "lag_volume_z_country_year_v14",
        ]],
        on=["country", "hospital_id", "year"],
        how="left",
        validate="many_to_one",
    )
    return out, hy


def build_hospital_year_table(df: pd.DataFrame, hy: pd.DataFrame) -> pd.DataFrame:
    keep = [
        "country", "hospital_id", "year", "hospital_volume_year", "volume_quartile_hy_v14",
    ]
    # add_volume_fields() já devolve volume_quartile_hy_v14 no nível do paciente.
    # Só fazer o merge quando a função for usada isoladamente.
    if "volume_quartile_hy_v14" in df.columns:
        patient = df.copy()
    else:
        patient = df.merge(
            hy[keep].drop_duplicates(["country", "hospital_id", "year"]),
            on=["country", "hospital_id", "year", "hospital_volume_year"],
            how="left",
            validate="many_to_one",
        )
    patient["death_valid"] = _as_numeric(patient["death_in_hospital"]).isin([0, 1])
    patient["death"] = _as_numeric(patient["death_in_hospital"])
    patient["los_valid"] = _as_numeric(patient["los_days"]).ge(1)
    patient["los"] = _as_numeric(patient["los_days"])

    rows = []
    for (country, q), sub in patient.groupby(["country", "volume_quartile_hy_v14"], dropna=False):
        hys = sub[["country", "hospital_id", "year"]].drop_duplicates()
        d = sub.loc[sub["death_valid"], "death"]
        l = sub.loc[sub["los_valid"], "los"]
        rows.append({
            "country": country,
            "volume_quartile_hospital_year": q,
            "n_hospital_years": len(hys),
            "n_unique_hospitals": hys["hospital_id"].nunique(),
            "n_admissions": len(sub),
            "volume_median_per_hospital_year": float(sub[["country", "hospital_id", "year", "hospital_volume_year"]].drop_duplicates()["hospital_volume_year"].median()),
            "crude_mortality_pct": 100*float(d.mean()) if not d.empty else np.nan,
            "los_median_valid": float(l.median()) if not l.empty else np.nan,
        })
    out = pd.DataFrame(rows)
    order = pd.Categorical(out["volume_quartile_hospital_year"], ["Q1", "Q2", "Q3", "Q4"], ordered=True)
    return out.assign(_order=order).sort_values(["country", "_order"]).drop(columns="_order")


def build_annual_trends(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (country, year), sub in df.groupby(["country", "year"]):
        death = _as_numeric(sub["death_in_hospital"])
        valid_d = death.isin([0, 1])
        k, n = int((death[valid_d] == 1).sum()), int(valid_d.sum())
        lo, hi = _wilson_interval(k, n)
        los = _as_numeric(sub["los_days"])
        valid_l = los.ge(1)
        rows.append({
            "country": country,
            "year": int(year),
            "n_admissions": len(sub),
            "n_hospitals": int(sub["hospital_id"].nunique()),
            "mortality_pct": 100*k/n if n else np.nan,
            "mortality_ci_low_pct": 100*lo,
            "mortality_ci_high_pct": 100*hi,
            "los_valid_n": int(valid_l.sum()),
            "los_median": float(los[valid_l].median()) if valid_l.any() else np.nan,
            "los_q1": float(los[valid_l].quantile(.25)) if valid_l.any() else np.nan,
            "los_q3": float(los[valid_l].quantile(.75)) if valid_l.any() else np.nan,
        })
    return pd.DataFrame(rows).sort_values(["country", "year"])


def build_subtype_table(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (country, subtype), sub in df.groupby(["country", "trauma_subtype"], dropna=False):
        death = _as_numeric(sub["death_in_hospital"])
        valid_d = death.isin([0, 1])
        los = _as_numeric(sub["los_days"])
        valid_l = los.ge(1)
        rows.append({
            "country": country,
            "trauma_subtype": subtype,
            "n": len(sub),
            "share_country_pct": 100*len(sub)/len(df[df["country"] == country]),
            "mortality_pct": 100*death[valid_d].mean() if valid_d.any() else np.nan,
            "los_median": float(los[valid_l].median()) if valid_l.any() else np.nan,
            "icu_pct_available": (
                100*_as_numeric(sub["icu_any"])[_as_numeric(sub["icu_any"]).isin([0, 1])].mean()
                if "icu_any" in sub and _as_numeric(sub["icu_any"]).isin([0, 1]).any()
                else np.nan
            ),
        })
    return pd.DataFrame(rows).sort_values(["country", "mortality_pct"], ascending=[True, False])


def build_centralization_metrics(hy: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (country, year), sub in hy.groupby(["country", "year"]):
        vols = sub["hospital_volume_year"].astype(float).sort_values(ascending=False).to_numpy()
        total = float(vols.sum())
        n = len(vols)
        shares = vols/total if total else np.zeros_like(vols)

        def top_share(frac: float) -> float:
            k = max(1, int(math.ceil(frac*n)))
            return 100*float(vols[:k].sum()/total) if total else np.nan

        rows.append({
            "country": country,
            "year": int(year),
            "n_hospital_year_units": n,
            "n_admissions": int(total),
            "median_volume": float(np.median(vols)) if n else np.nan,
            "p75_volume": float(np.quantile(vols, .75)) if n else np.nan,
            "top_5pct_hospitals_share_pct": top_share(.05),
            "top_10pct_hospitals_share_pct": top_share(.10),
            "top_20pct_hospitals_share_pct": top_share(.20),
            "hhi_0_10000": 10000*float(np.sum(shares**2)) if total else np.nan,
            "gini_volume": _gini(vols),
        })
    return pd.DataFrame(rows).sort_values(["country", "year"])


def build_volume_decile_table(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (country, decile), sub in df.groupby(["country", "volume_decile_hy_v14"], dropna=False):
        death = _as_numeric(sub["death_in_hospital"])
        valid = death.isin([0, 1])
        k, n = int((death[valid] == 1).sum()), int(valid.sum())
        lo, hi = _wilson_interval(k, n)
        hy_sub = sub[["country", "hospital_id", "year", "hospital_volume_year"]].drop_duplicates()
        rows.append({
            "country": country,
            "volume_decile": decile,
            "n_hospital_years": len(hy_sub),
            "n_admissions": len(sub),
            "median_volume": float(hy_sub["hospital_volume_year"].median()),
            "mortality_pct": 100*k/n if n else np.nan,
            "mortality_ci_low_pct": 100*lo,
            "mortality_ci_high_pct": 100*hi,
        })
    out = pd.DataFrame(rows)
    out["decile_number"] = out["volume_decile"].astype(str).str.extract(r"(\d+)").astype(float)
    return out.sort_values(["country", "decile_number"])


def _formula_frame(df: pd.DataFrame, outcome: str, exposure: str) -> pd.DataFrame:
    cols = [outcome, exposure, "age", "sex", "trauma_subtype", "country", "year", "hospital_id"]
    sub = df[cols].copy()
    sub[outcome] = pd.to_numeric(sub[outcome], errors="coerce")
    sub[exposure] = pd.to_numeric(sub[exposure], errors="coerce")
    sub["age"] = pd.to_numeric(sub["age"], errors="coerce")
    sub["year"] = pd.to_numeric(sub["year"], errors="coerce").astype("Int64").astype(str)
    for c in ["sex", "trauma_subtype", "country", "hospital_id"]:
        sub[c] = sub[c].astype(object)
    sub = sub.dropna(subset=[outcome, exposure, "age", "sex", "trauma_subtype", "country", "year", "hospital_id"])
    if outcome == "death_in_hospital":
        sub = sub[sub[outcome].isin([0, 1])]
    return _normalize_types(sub)


def _fit_clustered_glm(
    data: pd.DataFrame,
    formula: str,
    family: Any,
    label: str,
    effect_terms: Sequence[str],
) -> Dict[str, Any]:
    fit = smf.glm(formula=formula, data=data, family=family).fit(
        cov_type="cluster",
        cov_kwds={"groups": np.asarray(data["hospital_id"], dtype=object)},
        maxiter=150,
    )
    rows = []
    conf = fit.conf_int()
    for term in effect_terms:
        if term not in fit.params.index:
            continue
        beta = float(fit.params[term])
        lo = float(conf.loc[term, 0])
        hi = float(conf.loc[term, 1])
        rows.append({
            "analysis": label,
            "term": term,
            "beta": beta,
            "se": float(fit.bse[term]),
            "effect": float(np.exp(beta)),
            "ci_low": float(np.exp(lo)),
            "ci_high": float(np.exp(hi)),
            "p_value": float(fit.pvalues[term]),
            "n": int(fit.nobs),
            "n_hospitals": int(data["hospital_id"].nunique()),
            "formula": formula,
            "model_family": family.__class__.__name__,
            "covariance": "cluster-robust by hospital",
        })
    return {
        "label": label,
        "formula": formula,
        "n": int(fit.nobs),
        "n_hospitals": int(data["hospital_id"].nunique()),
        "aic": float(fit.aic) if np.isfinite(fit.aic) else None,
        "effects": rows,
        "params": {str(k): float(v) for k, v in fit.params.items()},
        "bse": {str(k): float(v) for k, v in fit.bse.items()},
    }


def run_model_suite(df: pd.DataFrame) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    models: Dict[str, Any] = {}

    base_formula_tail = (
        "bs(age, df=4, degree=3, include_intercept=False) + "
        "C(sex) + C(trauma_subtype) + C(country) * C(year)"
    )

    # 1) Mortalidade — exposição simultânea, escala padronizada e idade flexível.
    m = _formula_frame(df, "death_in_hospital", "volume_z_country_year_v14")
    formula = "death_in_hospital ~ volume_z_country_year_v14 + " + base_formula_tail
    model = _fit_clustered_glm(m, formula, sm.families.Binomial(), "mortality_same_year", ["volume_z_country_year_v14"])
    models["mortality_same_year"] = model
    results.extend(model["effects"])
    del m
    gc.collect()

    # 2) Heterogeneidade entre países.
    m = _formula_frame(df, "death_in_hospital", "volume_z_country_year_v14")
    formula = "death_in_hospital ~ volume_z_country_year_v14 * C(country) + " + (
        "bs(age, df=4, degree=3, include_intercept=False) + C(sex) + C(trauma_subtype) + C(year)"
    )
    terms = ["volume_z_country_year_v14"] + [
        x for x in ["volume_z_country_year_v14:C(country)[T.mexico]",
                    "volume_z_country_year_v14:C(country)[T.chile]",
                    "volume_z_country_year_v14:C(country)[T.equador]"]
    ]
    model = _fit_clustered_glm(m, formula, sm.families.Binomial(), "mortality_country_interaction", terms)
    models["mortality_country_interaction"] = model
    results.extend(model["effects"])
    del m
    gc.collect()

    # 3) Volume defasado — reduz simultaneidade entre casos e desfecho no mesmo ano.
    lag = df[df["lag_volume_z_country_year_v14"].notna()].copy()
    if len(lag) >= 500:
        m = _formula_frame(lag, "death_in_hospital", "lag_volume_z_country_year_v14")
        formula = "death_in_hospital ~ lag_volume_z_country_year_v14 + " + base_formula_tail
        model = _fit_clustered_glm(m, formula, sm.families.Binomial(), "mortality_lagged_volume", ["lag_volume_z_country_year_v14"])
        models["mortality_lagged_volume"] = model
        results.extend(model["effects"])
        del m, lag
        gc.collect()

    # 4) LOS entre sobreviventes — Poisson log-link com SE robusto, sem alpha fixo arbitrário.
    survivors = df[(pd.to_numeric(df["death_in_hospital"], errors="coerce") == 0) &
                   (pd.to_numeric(df["los_days"], errors="coerce") >= 1)].copy()
    if len(survivors) >= 500:
        m = _formula_frame(survivors.rename(columns={"los_days": "los_outcome"}), "los_outcome", "volume_z_country_year_v14")
        formula = "los_outcome ~ volume_z_country_year_v14 + " + base_formula_tail
        model = _fit_clustered_glm(m, formula, sm.families.Poisson(), "survivor_los", ["volume_z_country_year_v14"])
        models["survivor_los"] = model
        results.extend(model["effects"])
        del m, survivors
        gc.collect()

    # 5) Modelos por país na MESMA escala.
    for country in _country_sort(df["country"].dropna().unique()):
        cdf = df[df["country"].astype(str) == country]
        if len(cdf) < 500:
            continue
        m = _formula_frame(cdf, "death_in_hospital", "volume_z_country_year_v14")
        formula = (
            "death_in_hospital ~ volume_z_country_year_v14 + "
            "bs(age, df=4, degree=3, include_intercept=False) + C(sex) + C(trauma_subtype) + C(year)"
        )
        model = _fit_clustered_glm(m, formula, sm.families.Binomial(), f"mortality_{country}", ["volume_z_country_year_v14"])
        models[f"mortality_{country}"] = model
        for r in model["effects"]:
            r["country"] = country
        results.extend(model["effects"])
        del m
        gc.collect()

    return pd.DataFrame(results), models


def run_patient_factor_association_model(df: pd.DataFrame) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    sub = df.copy()
    age = pd.to_numeric(sub["age"], errors="coerce")
    sub["age_group_v14"] = pd.cut(
        age,
        bins=[17, 29, 44, 59, 74, np.inf],
        labels=["18–29", "30–44", "45–59", "60–74", "≥75"],
        right=True,
    ).astype(object)
    cols = ["death_in_hospital", "volume_z_country_year_v14", "age_group_v14", "sex", "trauma_subtype", "country", "year", "hospital_id"]
    m = sub[cols].copy()
    m["death_in_hospital"] = pd.to_numeric(m["death_in_hospital"], errors="coerce")
    m["volume_z_country_year_v14"] = pd.to_numeric(m["volume_z_country_year_v14"], errors="coerce")
    m["year"] = pd.to_numeric(m["year"], errors="coerce").astype("Int64").astype(str)
    for c in ["age_group_v14", "sex", "trauma_subtype", "country", "hospital_id"]:
        m[c] = m[c].astype(object)
    m = m.dropna().copy()
    m = m[m["death_in_hospital"].isin([0, 1])]
    m = _normalize_types(m)

    formula = (
        "death_in_hospital ~ volume_z_country_year_v14 + "
        "C(age_group_v14, Treatment(reference='18–29')) + C(sex) + C(trauma_subtype) + C(country) * C(year)"
    )
    fit = smf.glm(formula=formula, data=m, family=sm.families.Binomial()).fit(
        cov_type="cluster", cov_kwds={"groups": np.asarray(m["hospital_id"], dtype=object)}, maxiter=150
    )
    conf = fit.conf_int()
    rows = []
    include_patterns = ["age_group_v14", "C(sex)", "C(trauma_subtype)", "volume_z_country_year_v14"]
    for term in fit.params.index:
        if term == "Intercept" or not any(p in term for p in include_patterns):
            continue
        beta = float(fit.params[term])
        rows.append({
            "term": term,
            "adjusted_or": float(np.exp(beta)),
            "ci_low": float(np.exp(conf.loc[term, 0])),
            "ci_high": float(np.exp(conf.loc[term, 1])),
            "p_value": float(fit.pvalues[term]),
            "n": int(fit.nobs),
            "interpretation": "association adjusted; not a clinical causal predictor",
        })
    meta = {"formula": formula, "n": int(fit.nobs), "n_hospitals": int(m["hospital_id"].nunique())}
    del m, fit
    gc.collect()
    return pd.DataFrame(rows), meta


def _save_figure(fig: plt.Figure, path: Path, dpi: int = 240) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(path, dpi=dpi, bbox_inches="tight")
    plt.close(fig)


def fig_flow(df: pd.DataFrame, path: Path) -> None:
    countries = _country_sort(df["country"].dropna().unique())
    fig, ax = plt.subplots(figsize=(10, 6))
    ax.axis("off")
    ax.text(.5, .90, f"Coorte harmonizada de adultos com diagnóstico principal S06.x\nN = {len(df):,}",
            ha="center", va="center", fontsize=13, fontweight="bold",
            bbox=dict(boxstyle="round,pad=.6", facecolor="#334e68", edgecolor="none"), color="white")
    x_positions = np.linspace(.22, .78, max(1, len(countries)))
    for x, country in zip(x_positions, countries):
        sub = df[df["country"] == country]
        years = pd.to_numeric(sub["year"], errors="coerce").dropna().astype(int)
        period = f"{years.min()}–{years.max()}" if len(years) else "NA"
        txt = f"{COUNTRY_LABEL.get(country, country.title())}\nN = {len(sub):,}\nHospitais = {sub['hospital_id'].nunique():,}\n{period}"
        ax.annotate("", xy=(x, .62), xytext=(.5, .82), arrowprops=dict(arrowstyle="->", lw=1.6, color="#666"))
        ax.text(x, .53, txt, ha="center", va="center", fontsize=11,
                bbox=dict(boxstyle="round,pad=.55", facecolor=COUNTRY_COLOR.get(country, "#777"), edgecolor="none"), color="white")
    ax.text(.5, .18,
            "Análises principais: mortalidade intra-hospitalar e tempo de permanência\n"
            "Análises cirúrgicas não incluídas até validação do crosswalk e dos campos procedimentais",
            ha="center", va="center", fontsize=11,
            bbox=dict(boxstyle="round,pad=.6", facecolor="#e9ecef", edgecolor="#adb5bd"))
    ax.set_title("Fluxo das bases efetivamente incluídas", fontsize=15, fontweight="bold")
    _save_figure(fig, path)


def fig_annual_trends(trends: pd.DataFrame, path: Path) -> None:
    fig, ax = plt.subplots(figsize=(10, 6))
    for country in _country_sort(trends["country"].unique()):
        sub = trends[trends["country"] == country].sort_values("year")
        ax.plot(sub["year"], sub["mortality_pct"], marker="o", lw=2,
                label=COUNTRY_LABEL.get(country, country.title()), color=COUNTRY_COLOR.get(country))
        ax.fill_between(sub["year"].astype(float), sub["mortality_ci_low_pct"].astype(float),
                        sub["mortality_ci_high_pct"].astype(float), alpha=.15,
                        color=COUNTRY_COLOR.get(country))
    ax.set_xlabel("Ano")
    ax.set_ylabel("Mortalidade intra-hospitalar (%)")
    ax.set_title("Tendência anual de mortalidade por país")
    ax.legend(frameon=False)
    ax.spines[["top", "right"]].set_visible(False)
    _save_figure(fig, path)


def fig_volume_deciles(tab: pd.DataFrame, path: Path) -> None:
    countries = _country_sort(tab["country"].unique())
    fig, axes = plt.subplots(1, len(countries), figsize=(6*len(countries), 5), squeeze=False)
    for ax, country in zip(axes[0], countries):
        sub = tab[tab["country"] == country].sort_values("decile_number")
        x = sub["median_volume"].to_numpy(float)
        y = sub["mortality_pct"].to_numpy(float)
        lo = sub["mortality_ci_low_pct"].to_numpy(float)
        hi = sub["mortality_ci_high_pct"].to_numpy(float)
        ax.errorbar(x, y, yerr=np.vstack([y-lo, hi-y]), marker="o", lw=1.8, capsize=3,
                    color=COUNTRY_COLOR.get(country))
        ax.set_xscale("log")
        ax.set_title(COUNTRY_LABEL.get(country, country.title()))
        ax.set_xlabel("Volume mediano de TCE por hospital-ano (escala log)")
        ax.set_ylabel("Mortalidade bruta (%)")
        ax.spines[["top", "right"]].set_visible(False)
    fig.suptitle("Relação descritiva por decis de volume hospital-ano\n(IC 95% binomial; não ajustada)", y=1.03)
    _save_figure(fig, path)


def fig_forest_country_models(model_results: pd.DataFrame, path: Path) -> None:
    sub = model_results[model_results["analysis"].str.startswith("mortality_") &
                        model_results["country"].notna()].copy() if "country" in model_results else pd.DataFrame()
    if sub.empty:
        return
    sub = sub.sort_values("country")
    y = np.arange(len(sub))[::-1]
    fig, ax = plt.subplots(figsize=(8, 4.5))
    for yi, (_, r) in zip(y, sub.iterrows()):
        eff, lo, hi = float(r["effect"]), float(r["ci_low"]), float(r["ci_high"])
        ax.plot([lo, hi], [yi, yi], color="#334e68", lw=2)
        ax.scatter(eff, yi, s=70, color=COUNTRY_COLOR.get(r["country"], "#444"), zorder=3)
        ax.text(hi*1.015, yi, f"{eff:.2f} ({lo:.2f}–{hi:.2f})", va="center", fontsize=9)
    ax.axvline(1, color="#777", ls="--", lw=1)
    ax.set_xscale("log")
    ax.set_yticks(y)
    ax.set_yticklabels([COUNTRY_LABEL.get(c, c.title()) for c in sub["country"]])
    ax.set_xlabel("OR por +1 DP de log(volume), IC 95%")
    ax.set_title("Associação ajustada entre volume hospital-ano e mortalidade")
    ax.spines[["top", "right"]].set_visible(False)
    _save_figure(fig, path)


def fig_centralization(metrics: pd.DataFrame, path: Path) -> None:
    fig, ax = plt.subplots(figsize=(10, 6))
    for country in _country_sort(metrics["country"].unique()):
        sub = metrics[metrics["country"] == country]
        ax.plot(sub["year"], sub["top_10pct_hospitals_share_pct"], marker="o", lw=2,
                label=COUNTRY_LABEL.get(country, country.title()), color=COUNTRY_COLOR.get(country))
    ax.set_xlabel("Ano")
    ax.set_ylabel("Internações concentradas nos 10% maiores hospitais (%)")
    ax.set_title("Centralização do atendimento de TCE")
    ax.legend(frameon=False)
    ax.spines[["top", "right"]].set_visible(False)
    _save_figure(fig, path)


def fig_availability(availability: pd.DataFrame, path: Path) -> None:
    pivot = availability.pivot(index="variable", columns="country", values="availability_pct")
    countries = _country_sort(pivot.columns)
    pivot = pivot.reindex(columns=countries)
    fig, ax = plt.subplots(figsize=(2.2*len(countries)+4, .55*len(pivot)+2))
    im = ax.imshow(pivot.to_numpy(float), vmin=0, vmax=100, aspect="auto", cmap="viridis")
    ax.set_xticks(range(len(countries)), [COUNTRY_LABEL.get(c, c.title()) for c in countries])
    ax.set_yticks(range(len(pivot.index)), pivot.index)
    for i in range(len(pivot.index)):
        for j in range(len(countries)):
            val = pivot.iloc[i, j]
            ax.text(j, i, f"{val:.0f}%", ha="center", va="center",
                    color="white" if val < 55 else "black", fontsize=8)
    fig.colorbar(im, ax=ax, label="Registros não ausentes (%)")
    ax.set_title("Disponibilidade real das variáveis por país")
    _save_figure(fig, path)


def fig_subtype_mortality(tab: pd.DataFrame, path: Path) -> None:
    countries = _country_sort(tab["country"].unique())
    fig, axes = plt.subplots(1, len(countries), figsize=(6*len(countries), 5), squeeze=False)
    for ax, country in zip(axes[0], countries):
        sub = tab[tab["country"] == country].sort_values("mortality_pct")
        ax.barh(sub["trauma_subtype"].astype(str), sub["mortality_pct"], color=COUNTRY_COLOR.get(country))
        ax.set_title(COUNTRY_LABEL.get(country, country.title()))
        ax.set_xlabel("Mortalidade bruta (%)")
        ax.spines[["top", "right"]].set_visible(False)
    fig.suptitle("Mortalidade por subtipo administrativo de TCE", y=1.03)
    _save_figure(fig, path)


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if not np.isfinite(value) else float(value)
    if isinstance(value, Path):
        return str(value)
    return value


def run_postanalysis_v14(
    df_main: Optional[pd.DataFrame] = None,
    df_cdm: Optional[pd.DataFrame] = None,
    root: Path | str = DEFAULT_ROOT,
    run_models: bool = True,
    run_factor_model: bool = True,
) -> Dict[str, Any]:
    root = Path(root)
    dirs = _make_dirs(root)
    print(f"▶ TCE post-analysis v{VERSION}")
    print(f"  outputs novos: {dirs.tables.parent}")

    if df_main is None:
        df_main = _safe_read_parquet(root / "02_harmonized" / "cohort_main.parquet")
    if df_cdm is None:
        cdm_path = root / "02_harmonized" / "tce_harmonized_cdm.parquet"
        df_cdm = _safe_read_parquet(cdm_path) if cdm_path.exists() else df_main.copy()

    df_main = df_main.copy()
    df_cdm = df_cdm.copy()

    # Garantias mínimas.
    required = ["country", "year", "hospital_id", "age", "sex", "trauma_subtype", "death_in_hospital", "los_days"]
    missing = [c for c in required if c not in df_main.columns]
    if missing:
        raise KeyError(f"Colunas obrigatórias ausentes: {missing}")

    df_main, hy = add_volume_fields(df_main)

    availability = build_variable_availability(df_cdm)
    table1 = build_table1(df_main)
    hospital_year = build_hospital_year_table(df_main, hy)
    annual = build_annual_trends(df_main)
    subtype = build_subtype_table(df_main)
    concentration = build_centralization_metrics(hy)
    volume_deciles = build_volume_decile_table(df_main)

    crosswalk_br = build_brazil_procedure_crosswalk_v14()
    _save_table(availability, dirs.qc / "variable_availability_by_country_v14")
    _save_table(table1, dirs.tables / "Tabela1_pacientes_corrigida_v14")
    _save_table(hospital_year, dirs.tables / "Tabela2_hospital_year_volume_v14")
    _save_table(annual, dirs.tables / "Tabela3_tendencias_anuais_v14")
    _save_table(subtype, dirs.tables / "Tabela4_subtipos_desfechos_v14")
    _save_table(concentration, dirs.tables / "Tabela5_centralizacao_v14")
    _save_table(volume_deciles, dirs.tables / "TabelaS_volume_decis_v14")
    _save_table(crosswalk_br, dirs.qc / "crosswalk_brasil_conservador_v14")

    model_results = pd.DataFrame()
    models: Dict[str, Any] = {}
    if run_models:
        print("  ajustando modelos v1.4 em escala harmonizada...")
        model_results, models = run_model_suite(df_main)
        _save_table(model_results, dirs.tables / "Tabela6_modelos_volume_v14")
        with open(dirs.models / "model_suite_v14.json", "w", encoding="utf-8") as f:
            json.dump(_json_safe(models), f, ensure_ascii=False, indent=2)

    factor_results = pd.DataFrame()
    factor_meta: Dict[str, Any] = {}
    if run_factor_model:
        print("  ajustando modelo de associações dos fatores disponíveis...")
        factor_results, factor_meta = run_patient_factor_association_model(df_main)
        _save_table(factor_results, dirs.tables / "Tabela7_fatores_associados_mortalidade_v14")
        with open(dirs.models / "factor_model_v14.json", "w", encoding="utf-8") as f:
            json.dump(_json_safe(factor_meta), f, ensure_ascii=False, indent=2)

    print("  gerando figuras corrigidas...")
    fig_flow(df_main, dirs.fig_main / "Figura1_fluxo_bases_incluidas_v14.png")
    fig_annual_trends(annual, dirs.fig_main / "Figura2_tendencia_mortalidade_v14.png")
    fig_volume_deciles(volume_deciles, dirs.fig_main / "Figura3_volume_mortalidade_decis_v14.png")
    if not model_results.empty:
        fig_forest_country_models(model_results, dirs.fig_main / "Figura4_forest_volume_mortalidade_v14.png")
    fig_centralization(concentration, dirs.fig_main / "Figura5_centralizacao_v14.png")
    fig_availability(availability, dirs.fig_suppl / "FiguraS1_disponibilidade_variaveis_v14.png")
    fig_subtype_mortality(subtype, dirs.fig_suppl / "FiguraS2_mortalidade_subtipo_v14.png")

    summary_lines = [
        f"TCE MULTINACIONAL — POST-ANALYSIS v{VERSION}",
        f"N = {len(df_main):,}",
        f"Países = {', '.join(_country_sort(df_main['country'].unique()))}",
        f"Hospitais únicos = {df_main['hospital_id'].nunique():,}",
        "",
        "Correções principais:",
        "- ausências estruturais exibidas como NA, não 0%;",
        "- volume/quartis/decis definidos em hospital-ano;",
        "- forest plot usa a coluna effect real;",
        "- modelos e sensibilidades usam +1 DP de log(volume) dentro de país-ano;",
        "- idade modelada de forma flexível;",
        "- LOS analisado também entre sobreviventes;",
        "- sensibilidade com volume do ano anterior;",
        "- centralização por top 10%, HHI e Gini.",
        "",
        "Limitações não resolvidas pelo código:",
        "- ausência de Glasgow, pupilas, imagem/Marshall e fisiologia;",
        "- possível encaminhamento preferencial de casos graves a centros de alto volume;",
        "- procedimentos do México ainda não integrados;",
        "- crosswalk cirúrgico brasileiro precisa validação clínica e temporal;",
        "- resultados são associativos, não causais.",
    ]
    (dirs.support / "analysis_summary_v14.txt").write_text("\n".join(summary_lines), encoding="utf-8")

    outputs = {
        "version": VERSION,
        "dirs": dirs,
        "table1": table1,
        "availability": availability,
        "hospital_year": hospital_year,
        "annual_trends": annual,
        "subtype_outcomes": subtype,
        "centralization": concentration,
        "volume_deciles": volume_deciles,
        "model_results": model_results,
        "factor_results": factor_results,
        "models": models,
        "factor_meta": factor_meta,
    }
    print("✅ Post-analysis v1.4 concluída sem sobrescrever a v1.3.2.")
    return outputs


print(f"✅ tce_postanalysis_v1_4.py carregado (v{VERSION}).")
print("Execute: results_v14 = run_postanalysis_v14()")


# ============================================================
# TCE MULTINACIONAL — MASTER PATCH v2.0.0
# ============================================================
# This section is appended to the validated v1.3.2 pipeline and the
# corrected v1.4.1 post-analysis. It activates official-source discovery,
# RAM-safe country-year ingestion, expanded patient-level variables,
# a conservative procedure crosswalk, and an integrated master runner.

import csv as _csv
import gc as _gc
import json as _json
import math as _math
import os as _os
import re as _re
import shutil as _shutil
import time as _time
import unicodedata as _unicodedata
import zipfile as _zipfile
from pathlib import Path as _Path
from typing import Iterable as _Iterable, Iterator as _Iterator
from urllib.parse import urljoin as _urljoin, urlparse as _urlparse

TCE_MASTER_VERSION = "2.0.0"


def _v200_log(level: str, message: str) -> None:
    logger = globals().get("LOG")
    if logger is None:
        print(f"[{level}] {message}")
        return
    getattr(logger, level.lower(), logger.info)(message)


def _v200_norm_name(value: Any) -> str:
    text = _unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode("ascii")
    text = _re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_").lower()
    return text


def _v200_clean_string(series: pd.Series) -> pd.Series:
    out = series.astype("string").str.strip()
    return out.replace({"": pd.NA, "nan": pd.NA, "None": pd.NA, "<NA>": pd.NA})


def _v200_http_json(url: str, params: Optional[dict] = None, timeout: int = 120) -> Optional[dict]:
    headers = {"User-Agent": "Mozilla/5.0 TCE-Multinacional-v2.0"}
    for verify in (True, False):
        try:
            response = requests.get(url, params=params, headers=headers, timeout=timeout, verify=verify)
            response.raise_for_status()
            payload = response.json()
            if isinstance(payload, dict):
                return payload
        except Exception as exc:
            _v200_log("debug", f"[HTTP-JSON] {url} verify={verify}: {exc}")
    return None


def _v200_http_text(url: str, timeout: int = 120) -> Optional[str]:
    headers = {"User-Agent": "Mozilla/5.0 TCE-Multinacional-v2.0"}
    for verify in (True, False):
        try:
            response = requests.get(url, headers=headers, timeout=timeout, verify=verify)
            response.raise_for_status()
            response.encoding = response.apparent_encoding or "utf-8"
            return response.text
        except Exception as exc:
            _v200_log("debug", f"[HTTP-TEXT] {url} verify={verify}: {exc}")
    return None


def _v200_download(url: str, destination: _Path, minimum_bytes: int = 1024, timeout: int = 180) -> bool:
    """Download with atomic rename and rejection of tiny HTML error pages."""
    destination = _Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and destination.stat().st_size >= minimum_bytes:
        _v200_log("info", f"[DOWNLOAD-SKIP] {destination.name}")
        return True
    temporary = destination.with_suffix(destination.suffix + ".part")
    headers = {"User-Agent": "Mozilla/5.0 TCE-Multinacional-v2.0"}
    for verify in (True, False):
        try:
            with requests.get(url, headers=headers, stream=True, timeout=timeout, verify=verify) as response:
                response.raise_for_status()
                content_type = str(response.headers.get("content-type", "")).lower()
                with temporary.open("wb") as handle:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            handle.write(chunk)
            size = temporary.stat().st_size
            if size < minimum_bytes:
                raise RuntimeError(f"arquivo muito pequeno ({size} bytes)")
            if "text/html" in content_type and destination.suffix.lower() not in {".html", ".htm"}:
                head = temporary.read_bytes()[:1000].lower()
                if b"<html" in head or b"<!doctype" in head:
                    raise RuntimeError("servidor devolveu HTML, não microdados")
            temporary.replace(destination)
            _v200_log("info", f"[DOWNLOAD-OK] {destination.name} ({size / 1024**2:.1f} MB)")
            return True
        except Exception as exc:
            _v200_log("warning", f"[DOWNLOAD-FAIL] {url} verify={verify}: {exc}")
            try:
                temporary.unlink(missing_ok=True)
            except Exception:
                pass
    return False


def _v200_ckan_packages(base_url: str, query: str, rows: int = 100) -> List[dict]:
    payload = _v200_http_json(
        base_url.rstrip("/") + "/api/3/action/package_search",
        params={"q": query, "rows": rows},
    )
    if not payload or not payload.get("success"):
        return []
    return list(payload.get("result", {}).get("results", []))


def _v200_pick_patient_resource(packages: List[dict], year: int, country: str) -> Optional[dict]:
    candidates: List[Tuple[int, dict]] = []
    reject = (
        "diccionario", "dictionary", "metadato", "metadata", "perfil", "camas",
        "establecimiento", "formulario", "manual", "boletin", "informe", "agregado",
        "urgencia", "rem", "remsa", "resep",
    )
    for package in packages:
        package_blob = " ".join(str(package.get(k, "")) for k in ("title", "name", "notes")).lower()
        if str(year) not in package_blob and str(year) not in str(package):
            continue
        for resource in package.get("resources", []):
            blob = " ".join(
                str(resource.get(k, "")) for k in ("name", "description", "url", "format")
            ).lower()
            if any(token in blob for token in reject):
                continue
            if country == "equador" and not ("egreso" in blob or "hospital" in blob):
                continue
            if country == "chile" and not ("egreso" in blob or "hospital" in blob):
                continue
            fmt = str(resource.get("format", "")).upper()
            url = resource.get("url")
            if not url:
                continue
            score = 0
            if str(year) in blob:
                score += 5
            if fmt in {"CSV", "ZIP", "SAV", "DBF", "PARQUET"}:
                score += 5
            if "base de datos" in blob or "microdato" in blob:
                score += 6
            if "egresos_hospitalarios" in blob or "egresos hospitalarios" in blob:
                score += 4
            size = resource.get("size")
            try:
                if size and int(size) > 1_000_000:
                    score += 3
            except Exception:
                pass
            candidates.append((score, {**resource, "package_title": package.get("title")}))
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: item[0], reverse=True)[0][1]


ECUADOR_ANDA_CATALOG = {
    2015: 574,
    2016: 595,
    2017: 753,
    2018: 799,
    2019: 878,
    2020: 883,
    2021: 927,
    2022: 976,
    2023: 1042,
}


def _v200_write_manual_manifest(country: str, years: List[int], rows: List[dict], root: _Path) -> _Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"manual_download_required_{country}_v200.csv"
    pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8-sig")
    return path


def download_equador_official_v200(years: Optional[List[int]] = None, raw_dir: Optional[_Path] = None) -> Dict[int, Optional[_Path]]:
    """
    Download direct CC-BY CSV resources from the official Ecuador CKAN portal.
    ANDA years that require explicit acceptance are recorded in a manual manifest;
    the function never silently accepts terms on the user's behalf.
    """
    years = list(years or CONFIG.get("study_years", range(2015, 2024)))
    raw_dir = _Path(raw_dir or DIRS["raw_ec"])
    raw_dir.mkdir(parents=True, exist_ok=True)
    found: Dict[int, Optional[_Path]] = {}
    manual_rows: List[dict] = []
    base = "https://www.datosabiertos.gob.ec"
    for year in years:
        local_candidates = [
            p for p in raw_dir.rglob("*")
            if p.is_file() and str(year) in p.name and "egres" in p.name.lower()
            and p.suffix.lower() in {".csv", ".zip", ".sav", ".dbf", ".parquet"}
        ]
        if local_candidates:
            found[year] = max(local_candidates, key=lambda p: p.stat().st_size)
            continue
        packages: List[dict] = []
        for query in (
            f'"Registro Estadístico de Egresos Hospitalarios" {year}',
            f'"Egresos Hospitalarios" {year} INEC',
        ):
            packages.extend(_v200_ckan_packages(base, query, rows=50))
        resource = _v200_pick_patient_resource(packages, year, "equador")
        if resource:
            url = str(resource["url"])
            suffix = _Path(_urlparse(url).path).suffix.lower() or ".csv"
            if suffix not in {".csv", ".zip", ".sav", ".dbf", ".parquet"}:
                suffix = ".csv"
            destination = raw_dir / f"equador_{year}_egresos_hospitalarios{suffix}"
            if _v200_download(url, destination, minimum_bytes=100_000):
                found[year] = destination
                continue
        catalog_id = ECUADOR_ANDA_CATALOG.get(year)
        manual_rows.append({
            "country": "equador",
            "year": year,
            "reason": "Recurso direto não descoberto; catálogo ANDA exige aceitação explícita dos termos",
            "official_catalog_url": (
                f"https://anda.inec.gob.ec/anda5/index.php/catalog/{catalog_id}/get-microdata"
                if catalog_id else "https://anda.inec.gob.ec/anda5/"
            ),
            "destination_folder": str(raw_dir),
        })
        found[year] = None
    if manual_rows:
        manifest = _v200_write_manual_manifest("equador", years, manual_rows, _Path(DIRS["qc"]))
        _v200_log("warning", f"[EC] Anos que exigem ação manual registrados em {manifest}")
    return found


def _v200_discover_chile_resources(years: List[int]) -> List[dict]:
    resources: List[dict] = []
    # 1) WordPress media API used by the official DEIS site.
    for term in ("egresos", "egresos hospitalarios", "hospitalarios"):
        for page in range(1, 8):
            payload = _v200_http_json(
                "https://deis.minsal.cl/wp-json/wp/v2/media",
                params={"search": term, "per_page": 100, "page": page},
            )
            if not isinstance(payload, list) or not payload:
                break
            for item in payload:
                url = item.get("source_url")
                title = item.get("title", {}).get("rendered", "") if isinstance(item.get("title"), dict) else ""
                caption = item.get("caption", {}).get("rendered", "") if isinstance(item.get("caption"), dict) else ""
                blob = f"{title} {caption} {url}".lower()
                for year in years:
                    if str(year) in blob and url:
                        resources.append({"year": year, "url": url, "name": title, "source": "DEIS WordPress"})
    # 2) Chile CKAN, used only as discovery; candidate validation rejects aggregates.
    for query in ("egresos hospitalarios microdatos", "base egresos hospitalarios DEIS"):
        packages = _v200_ckan_packages("https://datos.gob.cl", query, rows=100)
        for year in years:
            selected = _v200_pick_patient_resource(packages, year, "chile")
            if selected:
                resources.append({"year": year, "url": selected["url"], "name": selected.get("name", ""), "source": "datos.gob.cl"})
    # 3) Scan official DEIS pages and linked JS for file URLs.
    for page_url in ("https://deis.minsal.cl/", "https://deis.minsal.cl/sistemas/", "https://deis.minsal.cl/faqs/"):
        html = _v200_http_text(page_url)
        if not html:
            continue
        urls = _re.findall(r"https?://[^\s\"'<>]+", html)
        for url in urls:
            clean = url.replace("&amp;", "&")
            low = clean.lower()
            if not any(ext in low for ext in (".zip", ".csv", ".sav", ".dbf", ".parquet")):
                continue
            for year in years:
                if str(year) in low and "egres" in low:
                    resources.append({"year": year, "url": clean, "name": _Path(_urlparse(clean).path).name, "source": "DEIS page"})
    seen = set()
    unique = []
    for resource in resources:
        key = (resource["year"], resource["url"])
        if key not in seen:
            unique.append(resource)
            seen.add(key)
    return unique


def download_chile_official_v200(years: Optional[List[int]] = None, raw_dir: Optional[_Path] = None) -> Dict[int, Optional[_Path]]:
    years = list(years or CONFIG.get("study_years", range(2015, 2024)))
    raw_dir = _Path(raw_dir or DIRS["raw_cl"])
    raw_dir.mkdir(parents=True, exist_ok=True)
    resources = _v200_discover_chile_resources(years)
    found: Dict[int, Optional[_Path]] = {}
    manual_rows: List[dict] = []
    reject_name = (
        "diccionario", "establecimiento", "formulario", "urgencia", "rem", "remsa",
        "esquema", "manual", "informe", "servicio", "estadisticaegresos",
    )
    for year in years:
        local = [
            p for p in raw_dir.rglob("*") if p.is_file() and str(year) in p.name
            and p.suffix.lower() in {".csv", ".zip", ".sav", ".dbf", ".parquet"}
            and not any(token in p.name.lower() for token in reject_name)
        ]
        if local:
            found[year] = max(local, key=lambda p: p.stat().st_size)
            continue
        year_resources = [r for r in resources if int(r["year"]) == int(year)]
        success: Optional[_Path] = None
        for number, resource in enumerate(year_resources, start=1):
            url = str(resource["url"])
            low = f"{resource.get('name','')} {url}".lower()
            if any(token in low for token in reject_name):
                continue
            suffix = _Path(_urlparse(url).path).suffix.lower() or ".zip"
            if suffix not in {".csv", ".zip", ".sav", ".dbf", ".parquet"}:
                continue
            destination = raw_dir / f"chile_{year}_egresos_candidate_{number}{suffix}"
            if _v200_download(url, destination, minimum_bytes=1_000_000):
                success = destination
                break
        found[year] = success
        if success is None:
            manual_rows.append({
                "country": "chile",
                "year": year,
                "reason": "Nenhum microdado individual validável foi descoberto automaticamente",
                "official_landing_page": "https://deis.minsal.cl/#datosabiertos",
                "destination_folder": str(raw_dir),
            })
    if manual_rows:
        manifest = _v200_write_manual_manifest("chile", years, manual_rows, _Path(DIRS["qc"]))
        _v200_log("warning", f"[CL] Anos não descobertos registrados em {manifest}")
    return found


def _v200_expand_archives(paths: List[_Path], work_dir: _Path) -> List[_Path]:
    work_dir.mkdir(parents=True, exist_ok=True)
    output: List[_Path] = []
    for path in paths:
        path = _Path(path)
        if path.suffix.lower() == ".zip":
            target = work_dir / path.stem
            target.mkdir(parents=True, exist_ok=True)
            try:
                with _zipfile.ZipFile(path) as archive:
                    safe_members = [m for m in archive.infolist() if not m.is_dir() and ".." not in _Path(m.filename).parts]
                    archive.extractall(target, members=safe_members)
                output.extend([p for p in target.rglob("*") if p.is_file()])
            except Exception as exc:
                _v200_log("warning", f"[ARCHIVE] {path.name}: {exc}")
        else:
            output.append(path)
    return output


def _v200_probe_delimited(path: _Path) -> Tuple[str, str, List[str]]:
    encodings = ("utf-8-sig", "utf-8", "latin-1", "cp1252")
    separators = (",", ";", "|", "\t")
    for encoding in encodings:
        for separator in separators:
            try:
                header = pd.read_csv(path, sep=separator, encoding=encoding, nrows=0).columns.tolist()
                if len(header) >= 5:
                    return encoding, separator, header
            except Exception:
                pass
    raise ValueError(f"Não foi possível identificar CSV/TXT: {path}")


def _v200_alias_lookup(columns: List[str], alias_map: Dict[str, List[str]]) -> Dict[str, str]:
    normalized = {_v200_norm_name(c): c for c in columns}
    selected: Dict[str, str] = {}
    for canonical, aliases in alias_map.items():
        for candidate in [canonical] + list(aliases):
            key = _v200_norm_name(candidate)
            if key in normalized:
                selected[canonical] = normalized[key]
                break
    return selected


CHILE_PATIENT_ALIASES_V200 = {
    "year": ["ANO_EGRESO", "ANIO_EGRESO", "ANO_EGR", "AÑO_EGRESO", "ANO"],
    "month": ["MES_EGRESO", "MES_EGR", "MES"],
    "hospital_id_raw": ["ESTAB", "COD_ESTAB", "CODESTAB", "CODIGO_ESTABLECIMIENTO", "ESTABLECIMIENTO"],
    "hospital_region": ["REGION_ESTAB", "REGION", "REG_ESTAB", "SERVICIO_SALUD"],
    "residence_region": ["REGION_RES", "REG_RESIDENCIA", "REGION_RESIDENCIA"],
    "age": ["EDAD_CANT", "EDAD", "EDAD_ANOS", "EDAD_AÑOS"],
    "age_unit": ["EDAD_TIPO", "TIPO_EDAD", "UNIDAD_EDAD"],
    "sex_raw": ["SEXO", "SEX"],
    "dx_main": ["DIAG1", "DIAG_PRIN", "DIAG_PRINC", "DIAGNOSTICO_PRINCIPAL", "CIE10"],
    "dx_secondary": ["DIAG2", "DIAG_SEC", "DIAGNOSTICO_SECUNDARIO"],
    "external_cause": ["CAUSA_EXT", "CAUSA_EXTERNA", "DIAG_EXT"],
    "los_days": ["DIAS_ESTADA", "DIAS_ESTANCIA", "ESTADA", "DIAS"],
    "discharge_condition": ["CONDICION_EGRESO", "COND_EGRESO", "CONDICION_AL_EGRESO"],
    "discharge_specialty": ["ESPECIALIDAD_EGRESO", "ESPEC_EGRESO", "SERVICIO_EGRESO"],
    "insurance_type": ["PREVISION", "FONASA", "TIPO_PREVISION"],
}

ECUADOR_PATIENT_ALIASES_V200 = {
    "year": ["anio_egr", "ano_egr", "año_egr", "year"],
    "month": ["mes_egr", "mes_inv", "mes"],
    "hospital_region": ["prov_ubi", "provincia_ubicacion"],
    "residence_region": ["prov_res", "provincia_residencia"],
    "hospital_area": ["area_ubi"],
    "residence_area": ["area_res"],
    "facility_class": ["clase"],
    "facility_type": ["tipo"],
    "facility_entity": ["entidad"],
    "facility_sector": ["sector"],
    "age": ["edad"],
    "age_unit": ["cod_edad"],
    "sex_raw": ["sexo"],
    "ethnicity": ["etnia"],
    "dx_main": ["cau_cie10", "causa3", "cie10"],
    "los_days": ["dia_estad", "dias_estada", "dias_estancia"],
    "discharge_condition": ["con_egrpa", "condicion_egreso"],
    "discharge_specialty": ["esp_egrpa", "especialidad_egreso"],
    "insurance_type": ["tipo_seg", "seguro"],
    "disability": ["dis_pac", "discapacidad"],
    "admission_date": ["fecha_ingr", "fecha_ingreso"],
    "discharge_date": ["fecha_egr", "fecha_egreso"],
}


def _v200_age_year_mask(age_unit: pd.Series, country: str) -> pd.Series:
    unit = _v200_clean_string(age_unit).str.upper()
    if country == "chile":
        # DEIS commonly codes 1/year; textual values are accepted.
        return unit.isin(["1", "01", "A", "ANO", "ANOS", "AÑO", "AÑOS", "YEAR", "YEARS"])
    # INEC cod_edad: preserve adult-looking ages only when unit denotes years.
    # Official dictionaries should be audited by the output frequency table.
    return unit.isin(["1", "01", "A", "ANO", "ANOS", "AÑO", "AÑOS", "YEAR", "YEARS"])


def _v200_standardize_chunk(chunk: pd.DataFrame, aliases: Dict[str, List[str]], country: str, file_year: int) -> pd.DataFrame:
    rename = _v200_alias_lookup(list(chunk.columns), aliases)
    frame = chunk[list(rename.values())].rename(columns={source: target for target, source in rename.items()}).copy()
    if "dx_main" not in frame or "age" not in frame:
        return pd.DataFrame()
    frame["dx_main"] = _v200_clean_string(frame["dx_main"]).str.upper().str.replace(".", "", regex=False)
    frame = frame[frame["dx_main"].str.startswith("S06", na=False)].copy()
    if frame.empty:
        return frame
    frame["age"] = pd.to_numeric(frame["age"], errors="coerce")
    if "age_unit" in frame:
        mask_years = _v200_age_year_mask(frame["age_unit"], country)
        # Avoid deleting obvious adult ages if a yearly file omits/uses an unknown unit code.
        if mask_years.mean() >= 0.05:
            frame.loc[~mask_years, "age"] = np.nan
    frame = frame[frame["age"].between(int(CONFIG.get("min_age", 18)), 110, inclusive="both")].copy()
    # frame.get(..., scalar) is unsafe here because pandas scalar objects do not
    # implement fillna/astype. Always construct index-aligned Series.
    if "year" in frame.columns:
        year_values = pd.to_numeric(frame["year"], errors="coerce")
    else:
        year_values = pd.Series(file_year, index=frame.index, dtype="float64")
    frame["year"] = year_values.fillna(file_year).astype("Int64")

    if "month" in frame.columns:
        frame["month"] = pd.to_numeric(frame["month"], errors="coerce").astype("Int64")
    else:
        frame["month"] = pd.Series(pd.NA, index=frame.index, dtype="Int64")
    sex = _v200_clean_string(frame.get("sex_raw", pd.Series(pd.NA, index=frame.index))).str.upper()
    frame["sex"] = sex.map({"1": "M", "2": "F", "M": "M", "F": "F", "H": "M", "HOMBRE": "M", "MUJER": "F"}).fillna("unknown")
    if "los_days" in frame.columns:
        frame["los_days"] = pd.to_numeric(frame["los_days"], errors="coerce").astype("Int64")
    else:
        frame["los_days"] = pd.Series(pd.NA, index=frame.index, dtype="Int64")
    condition = _v200_clean_string(frame.get("discharge_condition", pd.Series(pd.NA, index=frame.index))).str.upper()
    death = pd.Series(pd.NA, index=frame.index, dtype="Int64")
    if country == "equador":
        death.loc[condition.isin(["1", "01", "VIVO", "VIVA"])] = 0
        death.loc[condition.isin(["2", "02", "3", "03"]) | condition.str.contains("FALLEC", na=False)] = 1
    else:
        death.loc[condition.isin(["1", "01", "VIVO", "VIVA", "ALTA"])] = 0
        death.loc[condition.isin(["2", "02"]) | condition.str.contains("FALLEC|MUERTE|DEFUNC", na=False)] = 1
    frame["death_in_hospital"] = death
    frame["country"] = country
    frame["source"] = "DEIS-MINSAL" if country == "chile" else "INEC-EH"
    if country == "chile" and "hospital_id_raw" in frame:
        hid = _v200_clean_string(frame["hospital_id_raw"])
        frame["hospital_id"] = hid.map(lambda x: f"CL_{x}" if pd.notna(x) else pd.NA).astype("string")
        frame["stable_hospital_id"] = frame["hospital_id"].notna().astype("Int64")
    else:
        # Ecuador's public patient file does not expose a verified stable hospital identifier.
        frame["hospital_id"] = pd.Series(pd.NA, index=frame.index, dtype="string")
        frame["stable_hospital_id"] = pd.Series(0, index=frame.index, dtype="Int64")
    frame["hospital_volume_eligible"] = frame["stable_hospital_id"].astype("Int64")
    for col in (
        "hospital_region", "residence_region", "hospital_area", "residence_area",
        "facility_class", "facility_type", "facility_entity", "facility_sector",
        "ethnicity", "discharge_specialty", "insurance_type", "disability",
        "dx_secondary", "external_cause", "admission_date", "discharge_date",
    ):
        if col not in frame:
            frame[col] = pd.NA
    for col in ("icu_any", "icu_days", "urgent_admission", "cost_local_currency", "procedure_code_raw"):
        if col not in frame:
            frame[col] = pd.NA
    return frame


def _v200_read_patient_file(path: _Path, aliases: Dict[str, List[str]], country: str, year: int, chunk_size: int = 200_000) -> pd.DataFrame:
    path = _Path(path)
    suffix = path.suffix.lower()
    if suffix in {".csv", ".txt", ".tsv"}:
        encoding, separator, header = _v200_probe_delimited(path)
        alias_lookup = _v200_alias_lookup(header, aliases)
        required = {"dx_main", "age", "sex_raw", "los_days", "discharge_condition"}
        if not {"dx_main", "age"}.issubset(alias_lookup):
            return pd.DataFrame()
        usecols = list(dict.fromkeys(alias_lookup.values()))
        pieces: List[pd.DataFrame] = []
        for chunk in pd.read_csv(
            path, sep=separator, encoding=encoding, dtype=str, usecols=usecols,
            chunksize=chunk_size, low_memory=True,
        ):
            filtered = _v200_standardize_chunk(chunk, aliases, country, year)
            if not filtered.empty:
                pieces.append(filtered)
            del chunk, filtered
            _gc.collect()
        return pd.concat(pieces, ignore_index=True, sort=False) if pieces else pd.DataFrame()
    # Non-delimited candidates are read one at a time; full national files should preferentially be CSV.
    reader = globals().get("read_any_tabular") or globals().get("read_tabular_file")
    if reader:
        raw = reader(path, country)
    elif suffix in {".parquet"}:
        raw = pd.read_parquet(path)
    elif suffix in {".sav"}:
        raw = pd.read_spss(path)
    elif suffix in {".xlsx", ".xls", ".ods"}:
        raw = pd.read_excel(path, dtype=str)
    else:
        return pd.DataFrame()
    return _v200_standardize_chunk(raw, aliases, country, year)


def _v200_candidate_score(path: _Path, aliases: Dict[str, List[str]], year: int) -> Tuple[int, str]:
    low = path.name.lower()
    reject = (
        "diccionario", "metadata", "metadato", "camas", "establecimiento", "formulario",
        "urgencia", "remsa", "remasep", "manual", "esquema", "boletin", "informe",
    )
    if any(token in low for token in reject):
        return -100, "filename_rejected"
    if str(year) not in low:
        return -10, "year_not_in_filename"
    try:
        if path.suffix.lower() in {".csv", ".txt", ".tsv"}:
            _, _, header = _v200_probe_delimited(path)
        elif path.suffix.lower() == ".parquet":
            header = list(pd.read_parquet(path).columns)
        else:
            # Header-only Excel/SAV inspection is not reliable; allow but score lower.
            return 1, "non_delimited_candidate"
        lookup = _v200_alias_lookup(header, aliases)
        if not {"dx_main", "age"}.issubset(lookup):
            return -50, "missing_dx_or_age"
        score = 10 + len(lookup)
        if "hospital_id_raw" in lookup:
            score += 5
        if "discharge_condition" in lookup:
            score += 3
        if "los_days" in lookup:
            score += 3
        return score, "patient_level_schema"
    except Exception as exc:
        return -20, f"probe_failed:{exc}"


def _v200_ingest_country_years(country: str, years: List[int], raw_dir: _Path, inter_dir: _Path, aliases: Dict[str, List[str]]) -> Optional[pd.DataFrame]:
    inter_dir.mkdir(parents=True, exist_ok=True)
    yearly_frames: List[pd.DataFrame] = []
    audit_rows: List[dict] = []
    expanded = _v200_expand_archives(
        [p for p in raw_dir.rglob("*") if p.is_file() and p.suffix.lower() == ".zip"],
        inter_dir / "extracted_v200",
    )
    files = [p for p in raw_dir.rglob("*") if p.is_file() and p.suffix.lower() != ".zip"] + expanded
    files = list(dict.fromkeys(files))
    for year in years:
        checkpoint = inter_dir / f"{country}_s06_{year}_v200.parquet"
        if checkpoint.exists() and checkpoint.stat().st_size > 1000:
            yearly_frames.append(pd.read_parquet(checkpoint))
            continue
        candidates = []
        for path in files:
            score, reason = _v200_candidate_score(path, aliases, year)
            audit_rows.append({"country": country, "year": year, "file": str(path), "score": score, "reason": reason})
            if score > 0:
                candidates.append((score, path))
        candidates.sort(reverse=True, key=lambda item: item[0])
        selected: Optional[pd.DataFrame] = None
        selected_path: Optional[_Path] = None
        for _, path in candidates:
            try:
                frame = _v200_read_patient_file(path, aliases, country, year)
                if len(frame) >= 10:
                    selected, selected_path = frame, path
                    break
            except Exception as exc:
                _v200_log("warning", f"[{country.upper()}] {year} falhou {path.name}: {exc}")
        if selected is None or selected.empty:
            _v200_log("warning", f"[{country.upper()}] {year}: nenhum microdado individual S06 válido")
            continue
        selected["_source_file"] = str(selected_path)
        selected.to_parquet(checkpoint, index=False, engine="pyarrow", compression="snappy")
        _v200_log("info", f"[{country.upper()}] {year}: {len(selected):,} adultos S06 salvos")
        yearly_frames.append(selected)
        del selected
        _gc.collect()
    if audit_rows:
        pd.DataFrame(audit_rows).to_csv(_Path(DIRS["qc"]) / f"intake_{country}_v200.csv", index=False, encoding="utf-8-sig")
    if not yearly_frames:
        return None
    clean = pd.concat(yearly_frames, ignore_index=True, sort=False)
    clean_path = inter_dir / f"{country}_clean_v200.parquet"
    clean.to_parquet(clean_path, index=False, engine="pyarrow", compression="snappy")
    return clean


def run_chile_ingestion_v200(config: dict, dirs: dict) -> Optional[pd.DataFrame]:
    if not config.get("countries", {}).get("chile", False):
        return None
    raw_dir = _Path(dirs["raw_cl"])
    inter_dir = _Path(dirs["intermediate"]) / "chile"
    download_chile_official_v200(list(config["study_years"]), raw_dir)
    return _v200_ingest_country_years("chile", list(config["study_years"]), raw_dir, inter_dir, CHILE_PATIENT_ALIASES_V200)


def run_equador_ingestion_v200(config: dict, dirs: dict) -> Optional[pd.DataFrame]:
    if not config.get("countries", {}).get("equador", False):
        return None
    raw_dir = _Path(dirs["raw_ec"])
    inter_dir = _Path(dirs["intermediate"]) / "equador"
    download_equador_official_v200(list(config["study_years"]), raw_dir)
    return _v200_ingest_country_years("equador", list(config["study_years"]), raw_dir, inter_dir, ECUADOR_PATIENT_ALIASES_V200)


# Expanded CDM variables retained for country-specific patient-level analyses.
CDM_SCHEMA.update({
    "month": ("OPTIONAL", "Int64"),
    "stable_hospital_id": ("DERIVED", "Int64"),
    "hospital_volume_eligible": ("DERIVED", "Int64"),
    "hospital_area": ("OPTIONAL", "str"),
    "residence_area": ("OPTIONAL", "str"),
    "facility_class": ("OPTIONAL", "str"),
    "facility_type": ("OPTIONAL", "str"),
    "facility_entity": ("OPTIONAL", "str"),
    "facility_sector": ("OPTIONAL", "str"),
    "ethnicity": ("OPTIONAL", "str"),
    "discharge_specialty": ("OPTIONAL", "str"),
    "insurance_type": ("OPTIONAL", "str"),
    "disability": ("OPTIONAL", "str"),
    "admission_date": ("OPTIONAL", "str"),
    "discharge_date": ("OPTIONAL", "str"),
    "procedure_group_v2": ("DERIVED", "str"),
    "primary_acute_surgery": ("DERIVED", "Int64"),
})


def build_crosswalk_table_v200(dirs: dict) -> pd.DataFrame:
    rows = [
        ("brasil", "0403010020", "DECOMPRESSIVE_CODED", "HIGH", 1, "Craniotomia descompressiva"),
        ("brasil", "0403010039", "DECOMPRESSIVE_CODED", "HIGH", 1, "Craniotomia descompressiva da fossa posterior"),
        ("brasil", "0403010268", "ACUTE_CRANIAL_SURGERY", "HIGH", 1, "Fratura de crânio com afundamento"),
        ("brasil", "0403010276", "ACUTE_CRANIAL_SURGERY", "HIGH", 1, "Hematoma extradural"),
        ("brasil", "0403010284", "ACUTE_CRANIAL_SURGERY", "HIGH", 1, "Hematoma intracerebral"),
        ("brasil", "0403010292", "ACUTE_CRANIAL_SURGERY", "HIGH", 1, "Hematoma intracerebral com técnica complementar"),
        ("brasil", "0403010306", "ACUTE_CRANIAL_SURGERY", "HIGH", 1, "Hematoma subdural agudo"),
        ("brasil", "0403010314", "CHRONIC_SDH_SURGERY", "HIGH", 0, "Hematoma subdural crônico"),
        ("brasil", "0403010349", "ICP_MONITORING_OR_TREPANATION", "HIGH", 0, "Trepanação/monitorização de PIC"),
        ("brasil", "0415020077", "GENERIC_NEUROSURGERY", "MODERATE", 0, "Procedimentos sequenciais em neurocirurgia"),
        ("brasil", "0415010012", "GENERIC_MULTIPLE_PROCEDURES", "LOW", 0, "Procedimentos múltiplos"),
    ]
    table = pd.DataFrame(rows, columns=[
        "country", "procedure_code", "procedure_group_v2", "mapping_confidence",
        "primary_acute_surgery", "description",
    ])
    path = _Path(dirs["metadata"]) / "crosswalk_procedures_v200.csv"
    table.to_csv(path, index=False, encoding="utf-8-sig")
    globals()["PROC_LOOKUP_V200"] = {
        (row.country, row.procedure_code): (
            row.procedure_group_v2, row.mapping_confidence, int(row.primary_acute_surgery)
        ) for row in table.itertuples(index=False)
    }
    globals().get("save_csv_xlsx", lambda *_: None)(table, _Path(dirs["tables"]) / "crosswalk_procedures_v200")
    return table


def _v200_proc_token(value: Any, country: str) -> Optional[str]:
    if pd.isna(value):
        return None
    text = _re.sub(r"\.0+$", "", str(value).strip())
    digits = _re.sub(r"\D", "", text)
    if not digits:
        return None
    if country == "brasil":
        return digits.zfill(10)
    return digits


def apply_crosswalk_v200(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["trauma_subtype"] = out["dx_main"].map(globals()["classify_dx"])
    if "PROC_LOOKUP_V200" not in globals():
        build_crosswalk_table_v200(DIRS)
    out["procedure_group_v2"] = "UNCLASSIFIED"
    out["procedure_mapping_confidence"] = "NA"
    out["primary_acute_surgery"] = pd.Series(0, index=out.index, dtype="Int64")
    out["procedure_class"] = "UNCLASSIFIED"
    out["procedure_class_final"] = "UNCLASSIFIED"
    source = out[["country", "procedure_code_raw"]].dropna().copy()
    if not source.empty:
        source["_row"] = source.index
        source["_token"] = source["procedure_code_raw"].astype(str).str.split("|")
        source = source.explode("_token")
        source["_token"] = [
            _v200_proc_token(value, country)
            for value, country in zip(source["_token"], source["country"])
        ]
        lookup = build_crosswalk_table_v200(DIRS).rename(columns={"procedure_code": "_token"})
        merged = source.merge(lookup, on=["country", "_token"], how="left")
        priority = {
            "DECOMPRESSIVE_CODED": 6, "ACUTE_CRANIAL_SURGERY": 5,
            "CHRONIC_SDH_SURGERY": 4, "ICP_MONITORING_OR_TREPANATION": 3,
            "GENERIC_NEUROSURGERY": 2, "GENERIC_MULTIPLE_PROCEDURES": 1,
        }
        merged["_priority"] = merged["procedure_group_v2"].map(priority).fillna(0)
        best = merged.sort_values(["_row", "_priority"], ascending=[True, False]).drop_duplicates("_row")
        best = best.set_index("_row")
        valid_idx = best.index.intersection(out.index)
        out.loc[valid_idx, "procedure_group_v2"] = best.loc[valid_idx, "procedure_group_v2"].fillna("UNCLASSIFIED").astype(str)
        out.loc[valid_idx, "procedure_mapping_confidence"] = best.loc[valid_idx, "mapping_confidence"].fillna("NA").astype(str)
        out.loc[valid_idx, "primary_acute_surgery"] = pd.to_numeric(best.loc[valid_idx, "primary_acute_surgery"], errors="coerce").fillna(0).astype("Int64")
    class_map = {
        "DECOMPRESSIVE_CODED": "DC",
        "ACUTE_CRANIAL_SURGERY": "CRAN",
        "CHRONIC_SDH_SURGERY": "OTHER_CRAN",
        "ICP_MONITORING_OR_TREPANATION": "OTHER_CRAN",
        "GENERIC_NEUROSURGERY": "OTHER_CRAN",
        "GENERIC_MULTIPLE_PROCEDURES": "OTHER_CRAN",
    }
    out["procedure_class"] = out["procedure_group_v2"].map(class_map).fillna("UNCLASSIFIED")
    confident = out["procedure_mapping_confidence"].isin(["HIGH", "MODERATE"])
    out["procedure_class_final"] = out["procedure_class"].where(confident, "UNCLASSIFIED")
    out["surgery_any"] = out["procedure_class_final"].isin(["DC", "CRAN", "OTHER_CRAN"]).astype("Int64")
    return out


def compute_hospital_volume_v200(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    stable = out["hospital_id"].notna()
    if "hospital_volume_eligible" in out:
        stable &= pd.to_numeric(out["hospital_volume_eligible"], errors="coerce").fillna(0).eq(1)
    keys = out.loc[stable, ["country", "hospital_id", "year"]]
    volume = keys.groupby(["country", "hospital_id", "year"], observed=True).size().rename("hospital_volume_year").reset_index()
    out = out.drop(columns=[c for c in ["hospital_volume_year"] if c in out.columns])
    out = out.merge(volume, on=["country", "hospital_id", "year"], how="left", validate="many_to_one")
    out["hospital_volume_year"] = pd.to_numeric(out["hospital_volume_year"], errors="coerce").astype("Int64")
    out["hospital_volume_tbi_year"] = out["hospital_volume_year"].astype("Int64")
    return out


def harmonize_all_v200(country_dfs: Dict[str, Optional[pd.DataFrame]]) -> Tuple[pd.DataFrame, List[str]]:
    frames: List[pd.DataFrame] = []
    provenance: List[dict] = []
    for country, frame in country_dfs.items():
        if frame is None or frame.empty:
            _v200_log("warning", f"[HARM-v2] {country}: sem microdados válidos")
            continue
        final = globals()["finalize_country_df"](frame, country)
        frames.append(final)
        if "_source_file" in frame:
            for source_file, subset in frame.groupby("_source_file", dropna=False):
                provenance.append({"country": country, "source_file": source_file, "n_records": len(subset)})
    if not frames:
        raise RuntimeError("Nenhum país com microdados individuais válidos")
    cdm = pd.concat(frames, ignore_index=True, sort=False)
    cdm = apply_crosswalk_v200(cdm)
    cdm = compute_hospital_volume_v200(cdm)
    # Transfer proxy is computed only when comparable region variables exist.
    if {"hospital_region", "residence_region"}.issubset(cdm.columns):
        known = cdm["hospital_region"].notna() & cdm["residence_region"].notna()
        cdm["transfer_proxy"] = pd.Series(pd.NA, index=cdm.index, dtype="Int64")
        cdm.loc[known, "transfer_proxy"] = (
            cdm.loc[known, "hospital_region"].astype("string") != cdm.loc[known, "residence_region"].astype("string")
        ).astype("Int64")
    alerts = globals()["validate_cdm"](cdm)
    keep = [column for column in CDM_SCHEMA if column in cdm.columns]
    for extra in ("hospital_volume_tbi_year",):
        if extra in cdm and extra not in keep:
            keep.append(extra)
    cdm = cdm[keep].copy()
    globals()["save_parquet"](cdm, _Path(DIRS["harmonized"]) / "tce_harmonized_cdm.parquet", "CDM v2")
    globals()["save_csv_xlsx"](globals()["quick_audit"](cdm, "CDM v2"), _Path(DIRS["qc"]) / "audit_cdm_v200")
    if provenance:
        globals()["save_csv_xlsx"](pd.DataFrame(provenance), _Path(DIRS["qc"]) / "source_provenance_v200")
    return cdm, alerts


def build_cohorts_v200(df_cdm: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    main = df_cdm[
        pd.to_numeric(df_cdm["age"], errors="coerce").ge(int(CONFIG.get("min_age", 18)))
        & df_cdm["dx_main"].astype("string").str.startswith("S06", na=False)
    ].copy()
    surgical = main[main["primary_acute_surgery"].eq(1)].copy()
    dc_cran = surgical[surgical["procedure_group_v2"].isin(["DECOMPRESSIVE_CODED", "ACUTE_CRANIAL_SURGERY"])].copy()
    dc_cran["procedure_class_analysis"] = np.where(
        dc_cran["procedure_group_v2"].eq("DECOMPRESSIVE_CODED"), "DC", "CRAN"
    )
    broad = main[main["procedure_group_v2"].isin([
        "DECOMPRESSIVE_CODED", "ACUTE_CRANIAL_SURGERY", "CHRONIC_SDH_SURGERY",
        "ICP_MONITORING_OR_TREPANATION", "GENERIC_NEUROSURGERY",
    ])].copy()
    globals()["save_parquet"](main, _Path(DIRS["harmonized"]) / "cohort_main.parquet", "Coorte principal v2")
    globals()["save_parquet"](surgical, _Path(DIRS["harmonized"]) / "cohort_surgical.parquet", "Coorte cirúrgica aguda estrita v2")
    globals()["save_parquet"](dc_cran, _Path(DIRS["harmonized"]) / "cohort_dc_cran.parquet", "DC vs cirurgia craniana aguda v2")
    globals()["save_parquet"](broad, _Path(DIRS["harmonized"]) / "cohort_surgical_broad_sensitivity_v200.parquet", "Coorte cirúrgica ampla sensibilidade v2")
    return main, surgical, dc_cran


def _v200_bh_fdr(pvalues: pd.Series) -> pd.Series:
    p = pd.to_numeric(pvalues, errors="coerce")
    output = pd.Series(np.nan, index=p.index, dtype=float)
    valid = p.notna()
    if valid.any():
        from statsmodels.stats.multitest import multipletests
        output.loc[valid] = multipletests(p.loc[valid].clip(0, 1), method="fdr_bh")[1]
    return output


def build_stratified_outcome_tables_v200(df: pd.DataFrame) -> pd.DataFrame:
    """Prespecified descriptive association screen with true denominators."""
    data = df.copy()
    age = pd.to_numeric(data["age"], errors="coerce")
    data["age_group_v200"] = pd.cut(
        age, bins=[17, 29, 44, 59, 74, np.inf], labels=["18-29", "30-44", "45-59", "60-74", "75+"]
    )
    variables = [
        "age_group_v200", "sex", "trauma_subtype", "facility_sector", "facility_type",
        "residence_area", "ethnicity", "insurance_type", "discharge_specialty", "transfer_proxy",
    ]
    rows: List[dict] = []
    for country, country_df in data.groupby("country", observed=True):
        for variable in variables:
            if variable not in country_df or country_df[variable].notna().sum() == 0:
                continue
            counts = country_df[variable].astype("string").value_counts(dropna=True)
            allowed = counts[counts >= 30].index[:30]
            subset = country_df[country_df[variable].astype("string").isin(allowed)].copy()
            for level, group in subset.groupby(variable, observed=True, dropna=True):
                death = pd.to_numeric(group["death_in_hospital"], errors="coerce")
                dvalid = death.isin([0, 1])
                los = pd.to_numeric(group["los_days"], errors="coerce")
                lvalid = los.ge(1)
                rows.append({
                    "country": country,
                    "variable": variable,
                    "level": str(level),
                    "n": len(group),
                    "deaths": int((death[dvalid] == 1).sum()),
                    "mortality_denominator": int(dvalid.sum()),
                    "mortality_pct": 100 * float(death[dvalid].mean()) if dvalid.any() else np.nan,
                    "los_valid_n": int(lvalid.sum()),
                    "los_median": float(los[lvalid].median()) if lvalid.any() else np.nan,
                    "los_q1": float(los[lvalid].quantile(.25)) if lvalid.any() else np.nan,
                    "los_q3": float(los[lvalid].quantile(.75)) if lvalid.any() else np.nan,
                })
    return pd.DataFrame(rows)


def run_extended_association_models_v200(df: pd.DataFrame) -> pd.DataFrame:
    """
    Prespecified country-specific adjusted models. This is not an indiscriminate
    all-by-all correlation search. Variables enter only where present and with
    adequate category counts; BH-FDR is applied across exploratory coefficients.
    """
    results: List[dict] = []
    candidates = ["facility_sector", "residence_area", "ethnicity", "insurance_type", "transfer_proxy"]
    for country, country_df in df.groupby("country", observed=True):
        base = country_df[[c for c in [
            "death_in_hospital", "age", "sex", "year", "trauma_subtype", "hospital_id",
            "stable_hospital_id", *candidates,
        ] if c in country_df.columns]].copy()
        base["death_in_hospital"] = pd.to_numeric(base["death_in_hospital"], errors="coerce")
        base["age"] = pd.to_numeric(base["age"], errors="coerce")
        base = base[base["death_in_hospital"].isin([0, 1]) & base["age"].notna()].copy()
        if len(base) < 500:
            continue
        for variable in candidates:
            if variable not in base or base[variable].notna().sum() < 500:
                continue
            counts = base[variable].astype("string").value_counts()
            levels = counts[counts >= 100].index[:12]
            model_data = base[base[variable].astype("string").isin(levels)].copy()
            if model_data[variable].nunique() < 2:
                continue
            formula = (
                f"death_in_hospital ~ bs(age, df=4, include_intercept=False) + "
                f"C(sex) + C(year) + C(trauma_subtype) + C({variable})"
            )
            try:
                model = smf.glm(formula=formula, data=model_data, family=sm.families.Binomial())
                stable = (
                    "hospital_id" in model_data
                    and model_data["hospital_id"].notna().sum() > 0
                    and model_data.get("stable_hospital_id", pd.Series(0, index=model_data.index)).fillna(0).eq(1).mean() > 0.8
                )
                if stable:
                    fitted = model.fit(cov_type="cluster", cov_kwds={"groups": model_data.loc[model.data.row_labels, "hospital_id"]})
                else:
                    fitted = model.fit(cov_type="HC1")
                conf = fitted.conf_int()
                for term in fitted.params.index:
                    if not term.startswith(f"C({variable})"):
                        continue
                    results.append({
                        "country": country,
                        "variable": variable,
                        "term": term,
                        "or": float(np.exp(fitted.params[term])),
                        "ci_low": float(np.exp(conf.loc[term, 0])),
                        "ci_high": float(np.exp(conf.loc[term, 1])),
                        "p_value": float(fitted.pvalues[term]),
                        "n": int(fitted.nobs),
                        "covariance": "cluster_hospital" if stable else "HC1",
                    })
                del fitted, model, model_data
                _gc.collect()
            except Exception as exc:
                _v200_log("warning", f"[EXT-MODEL] {country}/{variable}: {exc}")
    result = pd.DataFrame(results)
    if not result.empty:
        result["p_fdr_bh"] = _v200_bh_fdr(result["p_value"])
    return result


def run_advanced_analysis_v200(df_cdm: pd.DataFrame, df_main: pd.DataFrame, run_models: bool = True) -> Dict[str, Any]:
    # The corrected v1.4.1 functions are embedded in this master file.
    post = run_postanalysis_v14(df_main=df_main, df_cdm=df_cdm, root=_Path(CONFIG["base_dir"]), run_models=run_models, run_factor_model=run_models)
    extra_dir = _Path(CONFIG["base_dir"]) / "04_tables_v200"
    extra_dir.mkdir(parents=True, exist_ok=True)
    stratified = build_stratified_outcome_tables_v200(df_main)
    _save_table(stratified, extra_dir / "Tabela_exploratoria_estratificada_v200")
    associations = run_extended_association_models_v200(df_main) if run_models else pd.DataFrame()
    _save_table(associations, extra_dir / "Tabela_modelos_associacoes_estendidas_v200")
    availability = build_variable_availability(df_cdm)
    _save_table(availability, extra_dir / "Matriz_disponibilidade_variaveis_v200")
    return {"postanalysis_v141": post, "stratified": stratified, "extended_associations": associations, "availability": availability}


def purge_derived_v200(preserve_country_checkpoints: bool = True) -> None:
    paths = [
        _Path(DIRS["harmonized"]) / "tce_harmonized_cdm.parquet",
        _Path(DIRS["harmonized"]) / "cohort_main.parquet",
        _Path(DIRS["harmonized"]) / "cohort_surgical.parquet",
        _Path(DIRS["harmonized"]) / "cohort_dc_cran.parquet",
        _Path(DIRS["harmonized"]) / "cohort_surgical_broad_sensitivity_v200.parquet",
    ]
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass
    if not preserve_country_checkpoints:
        for country in ("chile", "equador"):
            directory = _Path(DIRS["intermediate"]) / country
            for path in directory.glob(f"{country}_*_v200.parquet"):
                path.unlink(missing_ok=True)


def run_pipeline_complete_v200(config_arg: Optional[dict] = None, dirs_arg: Optional[dict] = None):
    active_config = CONFIG if config_arg is None else config_arg
    active_dirs = DIRS if dirs_arg is None else dirs_arg
    active_config.setdefault("countries", {})
    active_config["countries"].update({"brasil": True, "mexico": True, "chile": True, "equador": True})
    active_config["pipeline_version"] = TCE_MASTER_VERSION
    start = _time.time()
    _v200_log("info", "▶▶▶ PIPELINE TCE MASTER v2.0.0 INICIADO ◀◀◀")
    country_dfs: Dict[str, Optional[pd.DataFrame]] = {}
    country_dfs["brasil"] = globals()["run_brasil_ingestion"](active_config, active_dirs)
    _gc.collect()
    country_dfs["mexico"] = globals()["run_mexico_ingestion"](active_config, active_dirs)
    _gc.collect()
    country_dfs["chile"] = run_chile_ingestion_v200(active_config, active_dirs)
    _gc.collect()
    country_dfs["equador"] = run_equador_ingestion_v200(active_config, active_dirs)
    _gc.collect()
    globals()["run_raw_audit"](country_dfs)
    build_crosswalk_table_v200(active_dirs)
    df_cdm, alerts = harmonize_all_v200(country_dfs)
    df_main, df_surgical, df_dc = build_cohorts_v200(df_cdm)
    try:
        globals()["run_all_tables"](df_main, df_surgical, df_dc)
    except Exception as exc:
        _v200_log("warning", f"[LEGACY-TABLES] {exc}")
    volume_cohort = df_main[
        df_main["hospital_id"].notna()
        & pd.to_numeric(df_main.get("hospital_volume_eligible", 1), errors="coerce").fillna(0).eq(1)
    ].copy()
    model_output: Dict[str, Any] = {}
    if active_config.get("run_main_analysis", True) and not volume_cohort.empty:
        model_output = globals()["run_main_models_v132"](volume_cohort)
    advanced = run_advanced_analysis_v200(
        df_cdm, df_main, run_models=bool(active_config.get("run_main_analysis", True))
    )
    report = {
        "version": TCE_MASTER_VERSION,
        "elapsed_minutes": round((_time.time() - start) / 60, 2),
        "records_by_country": df_main["country"].value_counts().to_dict(),
        "volume_model_records_by_country": volume_cohort["country"].value_counts().to_dict(),
        "cdm_alerts": alerts,
        "important_notes": [
            "Hospital-volume models include only countries with a verified stable hospital identifier.",
            "Ecuador remains eligible for patient-level descriptive and association analyses even when hospital ID is unavailable.",
            "Procedure analyses are limited to clinically validated administrative code mappings.",
            "Exploratory association families use BH-FDR and are not interpreted causally.",
        ],
    }
    support = _Path(CONFIG["base_dir"]) / "10_manuscript_support_v200"
    support.mkdir(parents=True, exist_ok=True)
    (support / "master_run_summary_v200.json").write_text(_json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    _v200_log("info", f"▶▶▶ PIPELINE TCE MASTER v2.0.0 CONCLUÍDO em {report['elapsed_minutes']} min ◀◀◀")
    return df_cdm, df_main, df_surgical, df_dc, model_output, advanced


def verify_tce_master_v200() -> Dict[str, Any]:
    status = {
        "version": TCE_MASTER_VERSION,
        "runner": getattr(globals().get("run_pipeline_complete"), "__name__", None),
        "chile_ingestion": getattr(globals().get("run_chile_ingestion"), "__name__", None),
        "equador_ingestion": getattr(globals().get("run_equador_ingestion"), "__name__", None),
        "postanalysis_version": globals().get("VERSION"),
        "countries": dict(CONFIG.get("countries", {})),
    }
    expected = {
        "runner": "run_pipeline_complete_v200",
        "chile_ingestion": "run_chile_ingestion_v200",
        "equador_ingestion": "run_equador_ingestion_v200",
    }
    bad = {key: (status.get(key), value) for key, value in expected.items() if status.get(key) != value}
    if bad:
        raise RuntimeError(f"MASTER v2.0 não está ativo: {bad}")
    print(_json.dumps(status, ensure_ascii=False, indent=2))
    return status


# Activate v2 master entry points.
CONFIG["countries"].update({"brasil": True, "mexico": True, "chile": True, "equador": True})
CONFIG["pipeline_version"] = TCE_MASTER_VERSION
CONFIG.setdefault("run_advanced_analysis", True)
CONFIG.setdefault("country_download_mode", "official_direct_then_manifest")
globals()["run_chile_ingestion"] = run_chile_ingestion_v200
globals()["run_equador_ingestion"] = run_equador_ingestion_v200
globals()["build_crosswalk_table"] = build_crosswalk_table_v200
globals()["apply_crosswalk"] = apply_crosswalk_v200
globals()["harmonize_all"] = harmonize_all_v200
globals()["build_cohorts"] = build_cohorts_v200
globals()["run_pipeline_complete_v200"] = run_pipeline_complete_v200
globals()["run_pipeline_complete"] = run_pipeline_complete_v200
globals()["verify_tce_master_v200"] = verify_tce_master_v200
globals()["ACTIVE_TCE_PATCH"] = TCE_MASTER_VERSION
_v200_log("info", "[MASTER] TCE v2.0.0 ativado: ingestão oficial, CDM ampliado e pós-análise v1.4.1 integrada.")

# ============================================================================
# TCE MASTER v2.1.0 — FINAL STABILITY / OFFICIAL SOURCE / VOLUME HOTFIX
# Appended overrides. This block is intentionally self-contained and rebinds
# the public entry points defined above.
# ============================================================================

from pathlib import Path as _V210Path
from typing import Any as _V210Any, Dict as _V210Dict, List as _V210List, Optional as _V210Optional, Sequence as _V210Sequence, Tuple as _V210Tuple
import gc as _v210_gc
import json as _v210_json
import math as _v210_math
import re as _v210_re
import time as _v210_time
import traceback as _v210_traceback
import urllib.parse as _v210_urlparse

TCE_MASTER_VERSION = "2.1.0"


def _v210_log(level: str, message: str) -> None:
    logger = globals().get("LOG")
    if logger is not None and hasattr(logger, level):
        getattr(logger, level)(message)
    else:
        print(f"[{level.upper()}] {message}")


def _v210_to_float(value: _V210Any) -> float:
    """Return a plain float or np.nan; never raises on pd.NA/None."""
    try:
        if value is None or value is pd.NA:
            return float("nan")
        result = float(value)
        return result if np.isfinite(result) else float("nan")
    except Exception:
        return float("nan")


def _v210_numeric(series: _V210Any, index: _V210Optional[pd.Index] = None) -> pd.Series:
    """Convert scalars/arrays/nullable Series to a NumPy-backed float64 Series."""
    if isinstance(series, pd.Series):
        out = pd.to_numeric(series, errors="coerce")
        return pd.Series(out.to_numpy(dtype="float64", na_value=np.nan), index=series.index, dtype="float64")
    if index is None:
        index = pd.RangeIndex(1)
    return pd.Series(pd.to_numeric(pd.Series(series, index=index), errors="coerce"), index=index, dtype="float64")


def _v210_zscore_group(series: pd.Series) -> pd.Series:
    x = _v210_numeric(series)
    result = pd.Series(np.nan, index=series.index, dtype="float64")
    valid = x.notna()
    n = int(valid.sum())
    if n == 0:
        return result
    mean = float(x.loc[valid].mean())
    sd = float(x.loc[valid].std(ddof=0)) if n > 1 else 0.0
    if not np.isfinite(sd) or sd <= 0:
        result.loc[valid] = 0.0
    else:
        result.loc[valid] = (x.loc[valid] - mean) / sd
    return result


def _v210_quantile_bins(series: pd.Series, q: int, labels: _V210Sequence[str]) -> pd.Series:
    """Hospital-year bins without weighting by patient count."""
    x = _v210_numeric(series)
    result = pd.Series(pd.NA, index=series.index, dtype="string")
    valid = x.notna()
    n = int(valid.sum())
    if n < q or int(x.loc[valid].nunique()) < 2:
        return result
    try:
        ranked = x.loc[valid].rank(method="first")
        result.loc[valid] = pd.qcut(ranked, q=q, labels=list(labels)).astype("string")
    except Exception:
        pass
    return result


def add_volume_fields_v210(df: pd.DataFrame) -> _V210Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Rebuild hospital-year volume from patient rows, regardless of stale/missing
    columns inherited from earlier checkpoints. Quartiles/deciles are assigned
    on unique hospital-years within country-year, then merged back to patients.
    """
    out = df.copy()
    if out.empty:
        return out, pd.DataFrame(columns=["country", "hospital_id", "year", "hospital_volume_year"])

    for col in ("country", "hospital_id", "year"):
        if col not in out.columns:
            out[col] = pd.NA
    out["country"] = out["country"].astype("string")
    out["hospital_id"] = out["hospital_id"].astype("string")
    out["year"] = pd.to_numeric(out["year"], errors="coerce").astype("Int64")

    eligible = out["country"].notna() & out["hospital_id"].notna() & out["year"].notna()
    if "hospital_volume_eligible" in out.columns:
        eligible &= pd.to_numeric(out["hospital_volume_eligible"], errors="coerce").fillna(0).eq(1)
    elif "stable_hospital_id" in out.columns:
        eligible &= pd.to_numeric(out["stable_hospital_id"], errors="coerce").fillna(0).eq(1)

    units = (
        out.loc[eligible, ["country", "hospital_id", "year"]]
        .groupby(["country", "hospital_id", "year"], observed=True, dropna=False)
        .size()
        .rename("hospital_volume_year")
        .reset_index()
    )
    if units.empty:
        for col in (
            "hospital_volume_year", "log_volume", "log2_volume",
            "volume_z_country_year_v14", "volume_quartile_hy_v14",
            "volume_decile_hy_v14", "lag_volume", "log_lag_volume",
            "lag_volume_z_country_year_v14", "volume_quartile", "volume_decile",
        ):
            out[col] = pd.NA
        return out, units

    units["hospital_volume_year"] = pd.to_numeric(units["hospital_volume_year"], errors="coerce").astype("Int64")
    hv_float = _v210_numeric(units["hospital_volume_year"])
    units["log_volume"] = np.log1p(hv_float)
    units["log2_volume"] = np.log2(hv_float.clip(lower=1))
    units["volume_z_country_year_v14"] = units.groupby(
        ["country", "year"], observed=True, group_keys=False
    )["log_volume"].transform(_v210_zscore_group)
    units["volume_quartile_hy_v14"] = units.groupby(
        ["country", "year"], observed=True, group_keys=False
    )["hospital_volume_year"].transform(
        lambda s: _v210_quantile_bins(s, 4, ["Q1", "Q2", "Q3", "Q4"])
    )
    units["volume_decile_hy_v14"] = units.groupby(
        ["country", "year"], observed=True, group_keys=False
    )["hospital_volume_year"].transform(
        lambda s: _v210_quantile_bins(s, 10, [f"D{i}" for i in range(1, 11)])
    )

    units = units.sort_values(["country", "hospital_id", "year"]).reset_index(drop=True)
    units["lag_volume"] = units.groupby(["country", "hospital_id"], observed=True)["hospital_volume_year"].shift(1)
    units["log_lag_volume"] = np.log1p(_v210_numeric(units["lag_volume"]))
    units["lag_volume_z_country_year_v14"] = units.groupby(
        ["country", "year"], observed=True, group_keys=False
    )["log_lag_volume"].transform(_v210_zscore_group)

    replacement_cols = [
        "hospital_volume_year", "log_volume", "log2_volume",
        "volume_z_country_year_v14", "volume_quartile_hy_v14",
        "volume_decile_hy_v14", "lag_volume", "log_lag_volume",
        "lag_volume_z_country_year_v14", "volume_quartile", "volume_decile", "log_vol",
    ]
    out = out.drop(columns=[c for c in replacement_cols if c in out.columns], errors="ignore")
    out = out.merge(units, on=["country", "hospital_id", "year"], how="left", validate="many_to_one")
    out["volume_quartile"] = out["volume_quartile_hy_v14"].astype("string")
    out["volume_decile"] = out["volume_decile_hy_v14"].astype("string")
    out["log_vol"] = _v210_numeric(out["log_volume"])
    return out, units


def build_hospital_year_table_v210(df: pd.DataFrame, hy: pd.DataFrame) -> pd.DataFrame:
    """NA-safe hospital-year summary. Never calls float(pd.NA)."""
    columns = [
        "country", "volume_quartile_hospital_year", "n_hospital_years",
        "n_unique_hospitals", "n_admissions", "volume_median_per_hospital_year",
        "crude_mortality_pct", "los_median_valid",
    ]
    if df is None or df.empty:
        return pd.DataFrame(columns=columns)
    patient = df.copy()
    if "volume_quartile_hy_v14" not in patient.columns or "hospital_volume_year" not in patient.columns:
        patient, hy = add_volume_fields_v210(patient)
    patient = patient[patient["volume_quartile_hy_v14"].notna()].copy()
    if patient.empty:
        return pd.DataFrame(columns=columns)

    death = _v210_numeric(patient.get("death_in_hospital", pd.Series(np.nan, index=patient.index)))
    los = _v210_numeric(patient.get("los_days", pd.Series(np.nan, index=patient.index)))
    patient["_death_v210"] = death
    patient["_los_v210"] = los

    rows: _V210List[dict] = []
    for (country, quartile), sub in patient.groupby(
        ["country", "volume_quartile_hy_v14"], observed=True, dropna=True
    ):
        hospital_years = sub[["country", "hospital_id", "year"]].drop_duplicates()
        volumes = (
            sub[["country", "hospital_id", "year", "hospital_volume_year"]]
            .drop_duplicates(["country", "hospital_id", "year"])["hospital_volume_year"]
        )
        volumes = _v210_numeric(volumes).dropna()
        d = _v210_numeric(sub["_death_v210"])
        d = d[d.isin([0.0, 1.0])]
        l = _v210_numeric(sub["_los_v210"])
        l = l[l.ge(0)]
        rows.append({
            "country": str(country),
            "volume_quartile_hospital_year": str(quartile),
            "n_hospital_years": int(len(hospital_years)),
            "n_unique_hospitals": int(hospital_years["hospital_id"].nunique()),
            "n_admissions": int(len(sub)),
            "volume_median_per_hospital_year": _v210_to_float(volumes.median() if not volumes.empty else np.nan),
            "crude_mortality_pct": 100.0 * _v210_to_float(d.mean() if not d.empty else np.nan),
            "los_median_valid": _v210_to_float(l.median() if not l.empty else np.nan),
        })
    result = pd.DataFrame(rows, columns=columns)
    if result.empty:
        return result
    result["_order"] = pd.Categorical(
        result["volume_quartile_hospital_year"], ["Q1", "Q2", "Q3", "Q4"], ordered=True
    )
    return result.sort_values(["country", "_order"]).drop(columns="_order").reset_index(drop=True)


# Ecuador official CKAN resources that are stable enough to address directly.
# Each resource is still verified by size/content/schema before use.
ECUADOR_OFFICIAL_RESOURCE_IDS_V210 = {
    2021: "37cb51e9-726f-4b42-a0d3-7b5d5fdd632a",
    2022: "843e0263-0df4-451d-87b9-58ff46abd845",
    2023: "7bc29d96-f21b-48d9-b6c5-5d961fca4434",
}
ECUADOR_PACKAGE_SLUGS_V210 = {
    2020: "registro-estadistico-de-egresos-hospitalarios-2020",
    2021: "registro-estadistico-de-egresos-hospitalarios-2021",
    2022: "registro-estadistico-de-egresos-hospitalarios-2022",
    2023: "registro-estadistico-de-egresos-hospitalarios-2023",
}


def _v210_http_json_hosts(path: str, params: _V210Optional[dict] = None) -> _V210Optional[dict]:
    for host in ("https://www.datosabiertos.gob.ec", "https://datosabiertos.gob.ec"):
        payload = _v200_http_json(host.rstrip("/") + path, params=params, timeout=180)
        if isinstance(payload, dict):
            return payload
    return None


def _v210_equador_resource_urls(year: int) -> _V210List[str]:
    urls: _V210List[str] = []
    rid = ECUADOR_OFFICIAL_RESOURCE_IDS_V210.get(year)
    if rid:
        payload = _v210_http_json_hosts("/api/3/action/resource_show", {"id": rid})
        if payload and payload.get("success"):
            url = payload.get("result", {}).get("url")
            if url:
                urls.append(str(url))
        # CKAN datastore dump is a useful official fallback when enabled.
        urls.extend([
            f"https://www.datosabiertos.gob.ec/datastore/dump/{rid}",
            f"https://datosabiertos.gob.ec/datastore/dump/{rid}",
        ])
    slug = ECUADOR_PACKAGE_SLUGS_V210.get(year)
    if slug:
        payload = _v210_http_json_hosts("/api/3/action/package_show", {"id": slug})
        if payload and payload.get("success"):
            for resource in payload.get("result", {}).get("resources", []):
                blob = " ".join(str(resource.get(k, "")) for k in ("name", "description", "format", "url")).lower()
                if "csv" in blob and "diccionario" not in blob and "metadata" not in blob and "perfil" not in blob:
                    if resource.get("url"):
                        urls.append(str(resource["url"]))
    # Stable landing-page resource URLs are also scraped if the API is degraded.
    landing_candidates = []
    if rid:
        slug_for_page = ECUADOR_PACKAGE_SLUGS_V210.get(year, "")
        landing_candidates.extend([
            f"https://www.datosabiertos.gob.ec/dataset/{slug_for_page}/resource/{rid}",
            f"https://datosabiertos.gob.ec/dataset/{slug_for_page}/resource/{rid}",
        ])
    for landing in landing_candidates:
        html = _v200_http_text(landing, timeout=180)
        if not html:
            continue
        for match in _v210_re.findall(r'href=["\']([^"\']+)["\']', html, flags=_v210_re.I):
            full = _v210_urlparse.urljoin(landing, match)
            low = full.lower()
            if ("download" in low or low.endswith(".csv")) and "diccionario" not in low and "metadata" not in low:
                urls.append(full)
    return list(dict.fromkeys(urls))


def download_equador_official_v210(
    years: _V210Optional[_V210List[int]] = None,
    raw_dir: _V210Optional[_V210Path] = None,
) -> _V210Dict[int, _V210Optional[_V210Path]]:
    years = list(years or CONFIG.get("study_years", range(2015, 2024)))
    raw_dir = _V210Path(raw_dir or DIRS["raw_ec"])
    raw_dir.mkdir(parents=True, exist_ok=True)
    found: _V210Dict[int, _V210Optional[_V210Path]] = {}
    manual: _V210List[dict] = []
    valid_ext = {".csv", ".txt", ".zip", ".sav", ".dbf", ".dta", ".parquet"}

    for year in years:
        local = [
            p for p in raw_dir.rglob("*") if p.is_file() and str(year) in p.name
            and "egres" in p.name.lower() and p.suffix.lower() in valid_ext
            and p.stat().st_size >= 100_000
        ]
        if local:
            found[year] = max(local, key=lambda p: p.stat().st_size)
            continue

        downloaded = None
        if year >= 2020:
            for idx, url in enumerate(_v210_equador_resource_urls(year), start=1):
                parsed_suffix = _V210Path(_v210_urlparse.urlparse(url).path).suffix.lower()
                suffix = parsed_suffix if parsed_suffix in valid_ext else ".csv"
                destination = raw_dir / f"equador_{year}_egresos_hospitalarios_{idx}{suffix}"
                if _v200_download(url, destination, minimum_bytes=500_000, timeout=900):
                    downloaded = destination
                    break
        if downloaded is not None:
            found[year] = downloaded
            continue

        catalog_id = ECUADOR_ANDA_CATALOG.get(year)
        reason = (
            "Portal CKAN não entregou o CSV automaticamente; baixar o microdado oficial"
            if year >= 2020 else
            "Catálogo ANDA exige aceitação explícita dos termos antes do microdado"
        )
        manual.append({
            "country": "equador",
            "year": year,
            "reason": reason,
            "official_dataset_page": (
                f"https://www.datosabiertos.gob.ec/dataset/{ECUADOR_PACKAGE_SLUGS_V210.get(year)}"
                if year in ECUADOR_PACKAGE_SLUGS_V210 else
                f"https://anda.inec.gob.ec/anda5/index.php/catalog/{catalog_id}/get-microdata"
            ),
            "destination_folder": str(raw_dir),
            "accepted_extensions": ",".join(sorted(valid_ext)),
        })
        found[year] = None

    manifest = _V210Path(DIRS["qc"]) / "manual_download_required_equador_v210.csv"
    pd.DataFrame(manual).to_csv(manifest, index=False, encoding="utf-8-sig")
    if manual:
        _v210_log("warning", f"[EC-v2.1] {len(manual)} ano(s) ainda exigem ação manual: {manifest}")
    return found


def _v210_chile_is_candidate_url(url: str, year: int) -> bool:
    low = _v210_urlparse.unquote(str(url)).lower()
    if str(year) not in low or not ("egres" in low or "hospital" in low):
        return False
    reject = (
        "urgencia", "remsa", "remasep", "establecimiento", "diccionario", "manual",
        "formulario", "estructura", "esquema", "informe", "estadistica", "tablero",
        "powerbi", "boletin", "metadata", "metadato",
    )
    if any(token in low for token in reject):
        return False
    return any(ext in low for ext in (".csv", ".txt", ".zip", ".sav", ".dbf", ".dta", ".parquet"))


def _v210_discover_chile_official_urls(years: _V210List[int]) -> _V210Dict[int, _V210List[str]]:
    """
    Domain-restricted discovery on the current official DEIS site. It deliberately
    does not use the old datos.gob.cl monthly aggregate products.
    """
    found: _V210Dict[int, _V210List[str]] = {year: [] for year in years}
    # Search queries through WP REST, including year and likely patient-base terms.
    terms = []
    for year in years:
        terms.extend([f"egresos {year}", f"base egresos {year}", f"hospitalarios {year}"])
    for term in terms:
        payload = _v200_http_json(
            "https://deis.minsal.cl/wp-json/wp/v2/media",
            params={"search": term, "per_page": 100, "page": 1},
            timeout=180,
        )
        if not isinstance(payload, list):
            continue
        for item in payload:
            url = str(item.get("source_url") or "")
            for year in years:
                if _v210_chile_is_candidate_url(url, year):
                    found[year].append(url)

    # Enumerate the media library as a fallback because WP search often ignores filenames.
    max_pages = int(CONFIG.get("chile_wp_media_max_pages", 60))
    for page in range(1, max_pages + 1):
        payload = _v200_http_json(
            "https://deis.minsal.cl/wp-json/wp/v2/media",
            params={"per_page": 100, "page": page, "orderby": "date", "order": "desc"},
            timeout=180,
        )
        if not isinstance(payload, list) or not payload:
            break
        for item in payload:
            url = str(item.get("source_url") or "")
            title = item.get("title", {}).get("rendered", "") if isinstance(item.get("title"), dict) else ""
            blob_url = url + " " + str(title)
            for year in years:
                if _v210_chile_is_candidate_url(blob_url, year):
                    found[year].append(url)

    # Inspect official HTML and scripts for downloadable assets.
    for page_url in (
        "https://deis.minsal.cl/",
        "https://deis.minsal.cl/faqs/",
        "https://deis.minsal.cl/sistemas/",
    ):
        html = _v200_http_text(page_url, timeout=180)
        if not html:
            continue
        raw_urls = _v210_re.findall(r'https?://[^\s"\'<>]+', html, flags=_v210_re.I)
        raw_urls += [
            _v210_urlparse.urljoin(page_url, href)
            for href in _v210_re.findall(r'(?:href|src)=["\']([^"\']+)["\']', html, flags=_v210_re.I)
        ]
        for url in raw_urls:
            if "deis.minsal.cl" not in url:
                continue
            for year in years:
                if _v210_chile_is_candidate_url(url, year):
                    found[year].append(url)
    return {year: list(dict.fromkeys(urls)) for year, urls in found.items()}


def download_chile_official_v210(
    years: _V210Optional[_V210List[int]] = None,
    raw_dir: _V210Optional[_V210Path] = None,
) -> _V210Dict[int, _V210Optional[_V210Path]]:
    years = list(years or CONFIG.get("study_years", range(2015, 2024)))
    raw_dir = _V210Path(raw_dir or DIRS["raw_cl"])
    raw_dir.mkdir(parents=True, exist_ok=True)
    valid_ext = {".csv", ".txt", ".zip", ".sav", ".dbf", ".dta", ".parquet"}
    discovered = _v210_discover_chile_official_urls(years)
    found: _V210Dict[int, _V210Optional[_V210Path]] = {}
    manual: _V210List[dict] = []

    for year in years:
        local = [
            p for p in raw_dir.rglob("*") if p.is_file() and str(year) in p.name
            and p.suffix.lower() in valid_ext and p.stat().st_size >= 100_000
            and not any(token in p.name.lower() for token in (
                "urgencia", "remsa", "remasep", "establecimiento", "diccionario",
                "manual", "formulario", "estructura", "esquema", "informe", "estadistica",
            ))
        ]
        if local:
            found[year] = max(local, key=lambda p: p.stat().st_size)
            continue
        downloaded = None
        for idx, url in enumerate(discovered.get(year, []), start=1):
            suffix = _V210Path(_v210_urlparse.urlparse(url).path).suffix.lower()
            if suffix not in valid_ext:
                continue
            destination = raw_dir / f"chile_{year}_egresos_individuales_{idx}{suffix}"
            if _v200_download(url, destination, minimum_bytes=500_000, timeout=900):
                downloaded = destination
                break
        found[year] = downloaded
        if downloaded is None:
            manual.append({
                "country": "chile",
                "year": year,
                "reason": "O portal oficial dinâmico não expôs um URL estável de microdados nesta execução",
                "official_data_page": "https://deis.minsal.cl/#datosabiertos",
                "search_term": f"egresos hospitalarios {year}",
                "destination_folder": str(raw_dir),
                "required_file": "Base anonimizada individual de egresos hospitalarios; não relatório agregado/REM/urgencias",
                "accepted_extensions": ",".join(sorted(valid_ext)),
            })
    manifest = _V210Path(DIRS["qc"]) / "manual_download_required_chile_v210.csv"
    pd.DataFrame(manual).to_csv(manifest, index=False, encoding="utf-8-sig")
    if manual:
        _v210_log("warning", f"[CL-v2.1] {len(manual)} ano(s) sem URL estável: {manifest}")
    return found


def _v210_age_year_mask(age_unit: pd.Series, country: str) -> pd.Series:
    raw = _v200_clean_string(age_unit)
    unit = raw.map(lambda value: _v200_norm_name(value).replace("_", "").upper() if pd.notna(value) else pd.NA).astype("string")
    if country == "equador":
        # INEC cod_edad: 1 hours, 2 days, 3 months, 4 years, 9 unknown.
        return unit.isin(["4", "04", "A", "ANO", "ANOS", "YEAR", "YEARS"])
    if country == "chile":
        return unit.isin(["1", "01", "4", "04", "A", "ANO", "ANOS", "YEAR", "YEARS"])
    return unit.isin(["1", "01", "A", "ANO", "ANOS", "YEAR", "YEARS"])


def _v210_patient_file_allowed(path: _V210Path, year: int) -> bool:
    low = path.name.lower()
    if str(year) not in low:
        return False
    if path.suffix.lower() not in {".csv", ".txt", ".tsv", ".zip", ".sav", ".dbf", ".dta", ".parquet"}:
        return False
    reject = (
        "diccionario", "metadata", "metadato", "camas", "establecimiento", "formulario",
        "urgencia", "remsa", "remasep", "manual", "esquema", "boletin", "informe",
        "estadistica", "perfil", "estructura",
    )
    return not any(token in low for token in reject)


def _v210_ingest_country_years(
    country: str,
    years: _V210List[int],
    raw_dir: _V210Path,
    inter_dir: _V210Path,
    aliases: _V210Dict[str, _V210List[str]],
) -> _V210Optional[pd.DataFrame]:
    """Patient-only intake with annual checkpoints and early S06 filtering."""
    raw_dir, inter_dir = _V210Path(raw_dir), _V210Path(inter_dir)
    inter_dir.mkdir(parents=True, exist_ok=True)
    extracted_dir = inter_dir / "extracted_v210"
    archives = [p for p in raw_dir.rglob("*.zip") if p.is_file()]
    expanded = _v200_expand_archives(archives, extracted_dir) if archives else []
    source_files = [p for p in raw_dir.rglob("*") if p.is_file() and p.suffix.lower() != ".zip"] + expanded
    source_files = list(dict.fromkeys(_V210Path(p) for p in source_files))
    yearly: _V210List[pd.DataFrame] = []
    audit: _V210List[dict] = []

    for year in years:
        checkpoint = inter_dir / f"{country}_s06_{year}_v210.parquet"
        if checkpoint.exists() and checkpoint.stat().st_size > 1_000:
            frame = pd.read_parquet(checkpoint)
            if not frame.empty:
                yearly.append(frame)
                continue
        candidates = []
        for path in source_files:
            if not _v210_patient_file_allowed(path, year):
                continue
            score, reason = _v200_candidate_score(path, aliases, year)
            audit.append({"country": country, "year": year, "file": str(path), "score": score, "reason": reason})
            if score > 0:
                candidates.append((score, path))
        candidates.sort(key=lambda item: (item[0], item[1].stat().st_size if item[1].exists() else 0), reverse=True)
        selected = None
        selected_path = None
        for score, path in candidates:
            try:
                frame = _v200_read_patient_file(path, aliases, country, year, chunk_size=100_000)
                if frame is not None and len(frame) >= 10:
                    selected, selected_path = frame, path
                    break
            except Exception as exc:
                audit.append({"country": country, "year": year, "file": str(path), "score": score, "reason": f"read_failed:{exc}"})
                _v210_log("warning", f"[{country.upper()}-v2.1] {year}/{path.name}: {exc}")
        if selected is None or selected.empty:
            _v210_log("warning", f"[{country.upper()}-v2.1] {year}: nenhum microdado individual S06 válido")
            continue
        selected["_source_file"] = str(selected_path)
        selected.to_parquet(checkpoint, index=False, engine="pyarrow", compression="snappy")
        _v210_log("info", f"[{country.upper()}-v2.1] {year}: {len(selected):,} adultos S06")
        yearly.append(selected)
        del selected
        _v210_gc.collect()

    pd.DataFrame(audit).to_csv(
        _V210Path(DIRS["qc"]) / f"intake_{country}_v210.csv", index=False, encoding="utf-8-sig"
    )
    if not yearly:
        return None
    clean = pd.concat(yearly, ignore_index=True, sort=False)
    clean_path = inter_dir / f"{country}_clean_v210.parquet"
    clean.to_parquet(clean_path, index=False, engine="pyarrow", compression="snappy")
    return clean


def run_chile_ingestion_v210(config: dict, dirs: dict) -> _V210Optional[pd.DataFrame]:
    if not config.get("countries", {}).get("chile", False):
        return None
    raw_dir = _V210Path(dirs["raw_cl"])
    inter_dir = _V210Path(dirs["intermediate"]) / "chile"
    download_chile_official_v210(list(config["study_years"]), raw_dir)
    return _v210_ingest_country_years(
        "chile", list(config["study_years"]), raw_dir, inter_dir, CHILE_PATIENT_ALIASES_V200
    )


def run_equador_ingestion_v210(config: dict, dirs: dict) -> _V210Optional[pd.DataFrame]:
    if not config.get("countries", {}).get("equador", False):
        return None
    raw_dir = _V210Path(dirs["raw_ec"])
    inter_dir = _V210Path(dirs["intermediate"]) / "equador"
    download_equador_official_v210(list(config["study_years"]), raw_dir)
    return _v210_ingest_country_years(
        "equador", list(config["study_years"]), raw_dir, inter_dir, ECUADOR_PATIENT_ALIASES_V200
    )


# Preserve the already working Mexico reader and make checkpoint use explicit.
_run_mexico_ingestion_pre_v210 = globals().get("run_mexico_ingestion")


def run_mexico_ingestion_v210(config: dict, dirs: dict) -> _V210Optional[pd.DataFrame]:
    checkpoint = _V210Path(dirs["intermediate"]) / "mexico" / "mexico_clean.parquet"
    if checkpoint.exists() and checkpoint.stat().st_size > 1_000:
        _v210_log("info", f"[CHECKPOINT-MX] {checkpoint}")
        return pd.read_parquet(checkpoint)
    if not callable(_run_mexico_ingestion_pre_v210):
        raise RuntimeError("Leitor mexicano anterior não está disponível")
    return _run_mexico_ingestion_pre_v210(config, dirs)


def _v210_attach_volume_to_subset(subset: pd.DataFrame, main: pd.DataFrame) -> pd.DataFrame:
    if subset is None or subset.empty:
        return subset
    fields = [
        "country", "hospital_id", "year", "hospital_volume_year", "log_volume", "log2_volume",
        "volume_z_country_year_v14", "volume_quartile_hy_v14", "volume_decile_hy_v14",
        "lag_volume", "log_lag_volume", "lag_volume_z_country_year_v14",
        "volume_quartile", "volume_decile", "log_vol",
    ]
    available = [c for c in fields if c in main.columns]
    mapping = main[available].drop_duplicates(["country", "hospital_id", "year"])
    out = subset.drop(columns=[c for c in available if c not in {"country", "hospital_id", "year"} and c in subset.columns], errors="ignore")
    return out.merge(mapping, on=["country", "hospital_id", "year"], how="left", validate="many_to_one")


def _v210_source_coverage(country_dfs: _V210Dict[str, _V210Optional[pd.DataFrame]]) -> pd.DataFrame:
    rows = []
    for country in ("brasil", "mexico", "chile", "equador"):
        frame = country_dfs.get(country)
        if frame is None or frame.empty:
            rows.append({
                "country": country, "status": "NO_VALID_PATIENT_MICRODATA", "n_records": 0,
                "n_hospitals": 0, "years": "", "eligible_patient_models": False,
                "eligible_hospital_volume_models": False,
            })
            continue
        years = sorted(pd.to_numeric(frame.get("year"), errors="coerce").dropna().astype(int).unique().tolist())
        stable = pd.to_numeric(frame.get("stable_hospital_id", pd.Series(0, index=frame.index)), errors="coerce").fillna(0).eq(1)
        rows.append({
            "country": country, "status": "VALID_PATIENT_MICRODATA", "n_records": int(len(frame)),
            "n_hospitals": int(frame.loc[stable, "hospital_id"].nunique()) if "hospital_id" in frame else 0,
            "years": ",".join(map(str, years)), "eligible_patient_models": True,
            "eligible_hospital_volume_models": bool(stable.any()),
        })
    return pd.DataFrame(rows)


def _v210_safe_stage(name: str, func, *args, **kwargs):
    try:
        return func(*args, **kwargs)
    except Exception as exc:
        error_dir = _V210Path(CONFIG["base_dir"]) / "08_logs"
        error_dir.mkdir(parents=True, exist_ok=True)
        error_path = error_dir / f"stage_error_{_v210_re.sub(r'[^a-z0-9]+', '_', name.lower())}_v210.txt"
        error_path.write_text(_v210_traceback.format_exc(), encoding="utf-8")
        _v210_log("error", f"[STAGE-FAIL] {name}: {exc} | traceback: {error_path}")
        return None


def run_advanced_analysis_v210(
    df_cdm: pd.DataFrame,
    df_main: pd.DataFrame,
    run_models: bool = True,
) -> _V210Dict[str, _V210Any]:
    post = _v210_safe_stage(
        "postanalysis_v141",
        run_postanalysis_v14,
        df_main=df_main,
        df_cdm=df_cdm,
        root=_V210Path(CONFIG["base_dir"]),
        run_models=run_models,
        run_factor_model=run_models,
    )
    extra_dir = _V210Path(CONFIG["base_dir"]) / "04_tables_v210"
    extra_dir.mkdir(parents=True, exist_ok=True)
    stratified = _v210_safe_stage("stratified_tables", build_stratified_outcome_tables_v200, df_main)
    associations = (
        _v210_safe_stage("extended_associations", run_extended_association_models_v200, df_main)
        if run_models else pd.DataFrame()
    )
    availability = _v210_safe_stage("variable_availability", build_variable_availability, df_cdm)
    for obj, name in (
        (stratified, "Tabela_exploratoria_estratificada_v210"),
        (associations, "Tabela_modelos_associacoes_estendidas_v210"),
        (availability, "Matriz_disponibilidade_variaveis_v210"),
    ):
        if isinstance(obj, pd.DataFrame):
            _save_table(obj, extra_dir / name)
    return {
        "postanalysis_v141": post,
        "stratified": stratified,
        "extended_associations": associations,
        "availability": availability,
    }


def _v210_load_existing_harmonized() -> _V210Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    harm = _V210Path(DIRS["harmonized"])
    paths = {
        "cdm": harm / "tce_harmonized_cdm.parquet",
        "main": harm / "cohort_main.parquet",
        "surg": harm / "cohort_surgical.parquet",
        "dc": harm / "cohort_dc_cran.parquet",
    }
    missing = [str(path) for path in paths.values() if not path.exists()]
    if missing:
        raise FileNotFoundError(f"Checkpoints harmonizados ausentes: {missing}")
    return tuple(pd.read_parquet(paths[key]) for key in ("cdm", "main", "surg", "dc"))


def resume_analysis_v210(run_models: bool = True):
    """Resume from existing CDM/cohorts without re-ingesting Brazil or Mexico."""
    df_cdm, df_main, df_surg, df_dc = _v210_load_existing_harmonized()
    df_main, hy = add_volume_fields_v210(df_main)
    df_surg = _v210_attach_volume_to_subset(df_surg, df_main)
    df_dc = _v210_attach_volume_to_subset(df_dc, df_main)
    harm = _V210Path(DIRS["harmonized"])
    df_main.to_parquet(harm / "cohort_main_v210.parquet", index=False, compression="snappy")
    hy.to_parquet(harm / "hospital_year_v210.parquet", index=False, compression="snappy")
    _v210_safe_stage("legacy_tables", globals()["run_all_tables"], df_main, df_surg, df_dc)
    volume_cohort = df_main[
        df_main["hospital_id"].notna()
        & pd.to_numeric(df_main.get("hospital_volume_eligible", 1), errors="coerce").fillna(0).eq(1)
        & pd.to_numeric(df_main["hospital_volume_year"], errors="coerce").notna()
    ].copy()
    models = {}
    if run_models and not volume_cohort.empty:
        models = _v210_safe_stage("main_models", globals()["run_main_models_v132"], volume_cohort) or {}
    advanced = run_advanced_analysis_v210(df_cdm, df_main, run_models=run_models)
    return df_cdm, df_main, df_surg, df_dc, models, advanced


def run_pipeline_complete_v210(config_arg: _V210Optional[dict] = None, dirs_arg: _V210Optional[dict] = None):
    active_config = CONFIG if config_arg is None else config_arg
    active_dirs = DIRS if dirs_arg is None else dirs_arg
    active_config.setdefault("countries", {})
    active_config["countries"].update({"brasil": True, "mexico": True, "chile": True, "equador": True})
    active_config["pipeline_version"] = TCE_MASTER_VERSION
    start = _v210_time.time()
    _v210_log("info", "▶▶▶ PIPELINE TCE MASTER v2.1.0 INICIADO ◀◀◀")

    country_dfs: _V210Dict[str, _V210Optional[pd.DataFrame]] = {}
    country_dfs["brasil"] = globals()["run_brasil_ingestion"](active_config, active_dirs)
    _v210_gc.collect()
    country_dfs["mexico"] = run_mexico_ingestion_v210(active_config, active_dirs)
    _v210_gc.collect()
    country_dfs["chile"] = run_chile_ingestion_v210(active_config, active_dirs)
    _v210_gc.collect()
    country_dfs["equador"] = run_equador_ingestion_v210(active_config, active_dirs)
    _v210_gc.collect()

    coverage = _v210_source_coverage(country_dfs)
    coverage_dir = _V210Path(active_dirs["qc"])
    coverage_dir.mkdir(parents=True, exist_ok=True)
    coverage.to_csv(coverage_dir / "country_source_coverage_v210.csv", index=False, encoding="utf-8-sig")
    _v210_safe_stage("raw_audit", globals()["run_raw_audit"], country_dfs)
    build_crosswalk_table_v200(active_dirs)
    df_cdm, alerts = harmonize_all_v200(country_dfs)
    df_main, df_surg, df_dc = build_cohorts_v200(df_cdm)

    # Critical ordering fix: volume fields must exist before legacy tables/models/post-analysis.
    df_main, hospital_year = add_volume_fields_v210(df_main)
    df_surg = _v210_attach_volume_to_subset(df_surg, df_main)
    df_dc = _v210_attach_volume_to_subset(df_dc, df_main)
    harm_dir = _V210Path(active_dirs["harmonized"])
    harm_dir.mkdir(parents=True, exist_ok=True)
    df_main.to_parquet(harm_dir / "cohort_main.parquet", index=False, compression="snappy")
    df_surg.to_parquet(harm_dir / "cohort_surgical.parquet", index=False, compression="snappy")
    df_dc.to_parquet(harm_dir / "cohort_dc_cran.parquet", index=False, compression="snappy")
    hospital_year.to_parquet(harm_dir / "hospital_year_v210.parquet", index=False, compression="snappy")

    _v210_safe_stage("legacy_tables", globals()["run_all_tables"], df_main, df_surg, df_dc)
    volume_cohort = df_main[
        df_main["hospital_id"].notna()
        & pd.to_numeric(df_main.get("hospital_volume_eligible", 1), errors="coerce").fillna(0).eq(1)
        & pd.to_numeric(df_main["hospital_volume_year"], errors="coerce").notna()
    ].copy()
    models: _V210Dict[str, _V210Any] = {}
    run_models = bool(active_config.get("run_main_analysis", True))
    if run_models and not volume_cohort.empty:
        models = _v210_safe_stage("main_models", globals()["run_main_models_v132"], volume_cohort) or {}
    advanced = run_advanced_analysis_v210(df_cdm, df_main, run_models=run_models)

    report = {
        "version": TCE_MASTER_VERSION,
        "elapsed_minutes": round((_v210_time.time() - start) / 60, 2),
        "records_by_country": df_main["country"].value_counts().to_dict(),
        "volume_model_records_by_country": volume_cohort["country"].value_counts().to_dict(),
        "country_source_coverage": coverage.to_dict(orient="records"),
        "cdm_alerts": alerts,
        "interpretation_guards": [
            "Patient-level multinational models are one-stage analyses on individual admissions, not meta-analysis of means.",
            "Hospital-volume analyses include only verified stable hospital identifiers.",
            "Missing country microdata are reported as unavailable, never as zero events.",
            "Procedure comparisons remain limited to validated administrative mappings and are not causal.",
            "Exploratory association families use BH-FDR and require effect-size interpretation.",
        ],
    }
    support = _V210Path(active_config["base_dir"]) / "10_manuscript_support_v210"
    support.mkdir(parents=True, exist_ok=True)
    (support / "master_run_summary_v210.json").write_text(
        _v210_json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    _v210_log("info", f"▶▶▶ PIPELINE TCE MASTER v2.1.0 CONCLUÍDO em {report['elapsed_minutes']} min ◀◀◀")
    return df_cdm, df_main, df_surg, df_dc, models, advanced


def verify_tce_master_v210() -> _V210Dict[str, _V210Any]:
    status = {
        "version": TCE_MASTER_VERSION,
        "runner": getattr(globals().get("run_pipeline_complete"), "__name__", None),
        "volume_builder": getattr(globals().get("add_volume_fields"), "__name__", None),
        "hospital_year_table": getattr(globals().get("build_hospital_year_table"), "__name__", None),
        "mexico_ingestion": getattr(globals().get("run_mexico_ingestion"), "__name__", None),
        "chile_ingestion": getattr(globals().get("run_chile_ingestion"), "__name__", None),
        "equador_ingestion": getattr(globals().get("run_equador_ingestion"), "__name__", None),
        "countries": dict(CONFIG.get("countries", {})),
    }
    expected = {
        "runner": "run_pipeline_complete_v210",
        "volume_builder": "add_volume_fields_v210",
        "hospital_year_table": "build_hospital_year_table_v210",
        "mexico_ingestion": "run_mexico_ingestion_v210",
        "chile_ingestion": "run_chile_ingestion_v210",
        "equador_ingestion": "run_equador_ingestion_v210",
    }
    bad = {key: (status.get(key), value) for key, value in expected.items() if status.get(key) != value}
    if bad:
        raise RuntimeError(f"MASTER v2.1 não está ativo: {bad}")
    print(_v210_json.dumps(status, ensure_ascii=False, indent=2))
    return status


# Activate v2.1 entry points and functions used internally by the embedded post-analysis.
CONFIG["pipeline_version"] = TCE_MASTER_VERSION
CONFIG.setdefault("countries", {}).update({"brasil": True, "mexico": True, "chile": True, "equador": True})
CONFIG.setdefault("chile_wp_media_max_pages", 60)
globals()["_v200_age_year_mask"] = _v210_age_year_mask
globals()["download_chile_official_v200"] = download_chile_official_v210
globals()["download_equador_official_v200"] = download_equador_official_v210
globals()["add_volume_fields"] = add_volume_fields_v210
globals()["build_hospital_year_table"] = build_hospital_year_table_v210
globals()["run_mexico_ingestion"] = run_mexico_ingestion_v210
globals()["run_chile_ingestion"] = run_chile_ingestion_v210
globals()["run_equador_ingestion"] = run_equador_ingestion_v210
globals()["run_advanced_analysis_v200"] = run_advanced_analysis_v210
globals()["run_pipeline_complete_v210"] = run_pipeline_complete_v210
globals()["run_pipeline_complete"] = run_pipeline_complete_v210
globals()["resume_analysis_v210"] = resume_analysis_v210
globals()["verify_tce_master_v210"] = verify_tce_master_v210
globals()["ACTIVE_TCE_PATCH"] = TCE_MASTER_VERSION
_v210_log("info", "[MASTER] TCE v2.1.0 ativado: NA-safe volume, ordem corrigida e fontes oficiais restritas.")

# ============================================================
# TCE MULTINACIONAL — MASTER PATCH v2.2.0
# Local nested folders + SPSS SAV + schema-flexible intake
# ============================================================

import gc as _v220_gc
import json as _v220_json
import re as _v220_re
import traceback as _v220_traceback
import unicodedata as _v220_unicodedata
from pathlib import Path as _V220Path
from typing import Any as _V220Any, Dict as _V220Dict, List as _V220List, Optional as _V220Optional, Tuple as _V220Tuple

TCE_MASTER_VERSION = "2.2.0"


def _v220_norm(value: _V220Any) -> str:
    text = "" if value is None else str(value)
    text = "".join(
        ch for ch in _v220_unicodedata.normalize("NFKD", text)
        if not _v220_unicodedata.combining(ch)
    )
    return _v220_re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def _v220_path_blob(path: _V220Path) -> str:
    return _v220_norm(" ".join(path.parts))


def _v220_path_has_year(path: _V220Path, year: int) -> bool:
    return bool(_v220_re.search(rf"(?<!\d){int(year)}(?!\d)", " ".join(path.parts)))


def _v220_is_patient_filename(path: _V220Path, country: str) -> bool:
    """
    Decide by the file itself, not blindly by parent folders. This is important
    for INEC packages named 'Camas y Egresos' that contain the valid egresos SAV.
    """
    name = _v220_norm(path.name)
    suffix = path.suffix.lower()
    if suffix not in {".csv", ".txt", ".tsv", ".sav", ".dbf", ".dta", ".parquet", ".xlsx", ".xls", ".ods"}:
        return False

    hard_reject = (
        "diccionario", "metadata", "metadato", "guia", "manual", "formulario",
        "estructura", "esquema", "perfil", "catalogo", "readme", "glosario",
    )
    if any(token in name for token in hard_reject):
        return False

    if country == "equador":
        # Explicit egresos wins even when a parent directory contains 'camas'.
        if "egres" in name:
            return True
        if "camas" in name or "cama" in name:
            return False
        return False

    if country == "chile":
        reject = ("urgencia", "remsa", "remasep", "establecimiento", "estadistica", "informe")
        if any(token in name for token in reject):
            return False
        return "egre" in name or "hospital" in name

    return True


# Wider, documented aliases for the actual national patient files.
CHILE_PATIENT_ALIASES_V220 = {
    **CHILE_PATIENT_ALIASES_V200,
    "year": list(dict.fromkeys(CHILE_PATIENT_ALIASES_V200.get("year", []) + [
        "ANO", "ANIO", "AÑO", "YEAR", "ANO_EGR", "ANIO_EGR", "AÑO_EGR",
    ])),
    "month": list(dict.fromkeys(CHILE_PATIENT_ALIASES_V200.get("month", []) + ["MES_EGR"])),
    "hospital_id_raw": list(dict.fromkeys(CHILE_PATIENT_ALIASES_V200.get("hospital_id_raw", []) + [
        "ESTAB", "COD_ESTAB", "CODESTAB", "CODIGO_ESTAB", "ID_ESTAB",
    ])),
    "hospital_region": list(dict.fromkeys(CHILE_PATIENT_ALIASES_V200.get("hospital_region", []) + [
        "SER_SALUD", "SERV_SALUD", "SERVICIO_SALUD",
    ])),
    "residence_region": list(dict.fromkeys(CHILE_PATIENT_ALIASES_V200.get("residence_region", []) + [
        "REGION", "SERV_RES", "REG_RES",
    ])),
    "age": list(dict.fromkeys(CHILE_PATIENT_ALIASES_V200.get("age", []) + ["EDAD"])),
    "age_unit": list(dict.fromkeys(CHILE_PATIENT_ALIASES_V200.get("age_unit", []) + [
        "EDAD_TIPO", "TIPO_EDAD", "COD_EDAD",
    ])),
    "sex_raw": list(dict.fromkeys(CHILE_PATIENT_ALIASES_V200.get("sex_raw", []) + ["SEXO"])),
    "dx_main": list(dict.fromkeys(CHILE_PATIENT_ALIASES_V200.get("dx_main", []) + [
        "DIAG1", "DIAG_1", "DIAGNOSTICO1",
    ])),
    "external_cause": list(dict.fromkeys(CHILE_PATIENT_ALIASES_V200.get("external_cause", []) + [
        "DIAG2", "DIAG_2", "CAUSA_EXT",
    ])),
    "los_days": list(dict.fromkeys(CHILE_PATIENT_ALIASES_V200.get("los_days", []) + [
        "DIAS_ESTAD", "DIAS_ESTADA", "DIAS_ESTANCIA",
    ])),
    "discharge_condition": list(dict.fromkeys(CHILE_PATIENT_ALIASES_V200.get("discharge_condition", []) + [
        "COND_EGR", "CONDICION_EGR", "COND_EGRESO",
    ])),
    "discharge_specialty": list(dict.fromkeys(CHILE_PATIENT_ALIASES_V200.get("discharge_specialty", []) + [
        "SERC_EGR", "SERV_CL_EG", "SERV_CL_EGR", "AREAF_EGR",
    ])),
    "insurance_type": list(dict.fromkeys(CHILE_PATIENT_ALIASES_V200.get("insurance_type", []) + [
        "PREVI", "PREVISION",
    ])),
    "admission_date": ["FECHA_ING", "FECHA_INGR", "FECHA_INGRESO"],
    "discharge_date": ["FECHA_EGR", "FECHA_EGRESO"],
}

ECUADOR_PATIENT_ALIASES_V220 = {
    **ECUADOR_PATIENT_ALIASES_V200,
    "year": list(dict.fromkeys(ECUADOR_PATIENT_ALIASES_V200.get("year", []) + [
        "ANIO_EGR", "AÑO_EGR", "ANO_EGR", "ANIO_EGRESO", "AÑO_EGRESO",
    ])),
    "month": list(dict.fromkeys(ECUADOR_PATIENT_ALIASES_V200.get("month", []) + ["MES_INV", "MES_EGR"])),
    "age": list(dict.fromkeys(ECUADOR_PATIENT_ALIASES_V200.get("age", []) + ["EDAD_PAC", "EDAD_PACIENTE"])),
    "age_unit": list(dict.fromkeys(ECUADOR_PATIENT_ALIASES_V200.get("age_unit", []) + [
        "COD_EDAD", "COND_EDAD", "CONDICION_EDAD",
    ])),
    "sex_raw": list(dict.fromkeys(ECUADOR_PATIENT_ALIASES_V200.get("sex_raw", []) + ["SEXO_PAC"])),
    "dx_main": list(dict.fromkeys(ECUADOR_PATIENT_ALIASES_V200.get("dx_main", []) + [
        "CAU_CIE10", "CIE10_EGR", "DIAG_EGR", "DIAGNOSTICO_EGRESO",
    ])),
    "los_days": list(dict.fromkeys(ECUADOR_PATIENT_ALIASES_V200.get("los_days", []) + [
        "DIA_ESTAD", "DIAS_ESTAD", "DIAS_ESTADA",
    ])),
    "discharge_condition": list(dict.fromkeys(ECUADOR_PATIENT_ALIASES_V200.get("discharge_condition", []) + [
        "CON_EGRPA", "COND_EGR", "CONDICION_EGRESO",
    ])),
    "discharge_specialty": list(dict.fromkeys(ECUADOR_PATIENT_ALIASES_V200.get("discharge_specialty", []) + [
        "ESP_EGRPA", "ESPECIALIDAD_EGRESO",
    ])),
}


def _v220_alias_lookup(
    columns: _V220List[str],
    alias_map: _V220Dict[str, _V220List[str]],
    labels: _V220Optional[_V220Dict[str, str]] = None,
) -> _V220Dict[str, str]:
    """Exact aliases first; conservative semantic fallback using names/labels."""
    labels = labels or {}
    name_map = {_v220_norm(col): col for col in columns}
    label_map = {_v220_norm(labels.get(col, "")): col for col in columns if labels.get(col)}
    chosen: _V220Dict[str, str] = {}

    for canonical, aliases in alias_map.items():
        for candidate in [canonical] + list(aliases):
            key = _v220_norm(candidate)
            if key in name_map:
                chosen[canonical] = name_map[key]
                break
            if key in label_map:
                chosen[canonical] = label_map[key]
                break

    semantic = {
        "dx_main": [("diag", "principal"), ("diagnostico", "egreso"), ("cau", "cie10"), ("cie10", "egreso")],
        "age": [("edad",)],
        "age_unit": [("cod", "edad"), ("tipo", "edad"), ("condicion", "edad")],
        "sex_raw": [("sexo",)],
        "los_days": [("dias", "estad"), ("dias", "estancia"), ("dias", "estada")],
        "discharge_condition": [("cond", "egr"), ("condicion", "egreso")],
        "hospital_id_raw": [("estab",), ("codigo", "establecimiento")],
        "discharge_specialty": [("serv", "egr"), ("especialidad", "egreso")],
        "year": [("anio", "egr"), ("ano", "egr")],
        "month": [("mes", "egr"), ("mes", "inv")],
    }
    used = set(chosen.values())
    for canonical, patterns in semantic.items():
        if canonical in chosen:
            continue
        best = None
        for col in columns:
            if col in used:
                continue
            blob = _v220_norm(str(col) + " " + str(labels.get(col, "")))
            if canonical == "age" and any(t in blob for t in ("cod_edad", "tipo_edad", "condicion_edad")):
                continue
            for tokens in patterns:
                if all(token in blob for token in tokens):
                    best = col
                    break
            if best is not None:
                break
        if best is not None:
            chosen[canonical] = best
            used.add(best)
    return chosen


def _v220_sav_metadata(path: _V220Path) -> _V220Tuple[_V220List[str], _V220Dict[str, str]]:
    import pyreadstat
    _, meta = pyreadstat.read_sav(str(path), metadataonly=True, apply_value_formats=False)
    columns = list(getattr(meta, "column_names", []) or [])
    labels = dict(getattr(meta, "column_names_to_labels", {}) or {})
    return columns, labels


def _v220_probe_columns(path: _V220Path) -> _V220Tuple[_V220List[str], _V220Dict[str, str]]:
    suffix = path.suffix.lower()
    if suffix in {".csv", ".txt", ".tsv"}:
        _, _, header = _v200_probe_delimited(path)
        return list(header), {}
    if suffix == ".sav":
        return _v220_sav_metadata(path)
    if suffix == ".parquet":
        try:
            import pyarrow.parquet as pq
            return list(pq.ParquetFile(path).schema.names), {}
        except Exception:
            return list(pd.read_parquet(path).columns), {}
    if suffix in {".xlsx", ".xls", ".ods"}:
        return list(pd.read_excel(path, nrows=0).columns), {}
    if suffix == ".dta":
        import pyreadstat
        _, meta = pyreadstat.read_dta(str(path), metadataonly=True, apply_value_formats=False)
        return list(meta.column_names), dict(meta.column_names_to_labels or {})
    if suffix == ".dbf":
        # DBF probing is cheap enough for these datasets.
        raw = _read_tabular_file(path, "probe")
        return ([] if raw is None else list(raw.columns)), {}
    return [], {}


def _v220_candidate_score(
    path: _V220Path,
    aliases: _V220Dict[str, _V220List[str]],
    country: str,
    year: int,
) -> _V220Tuple[int, str, _V220Dict[str, str]]:
    if not _v220_is_patient_filename(path, country):
        return -100, "filename_not_patient_microdata", {}
    if not _v220_path_has_year(path, year):
        return -10, "year_not_in_full_path", {}
    try:
        columns, labels = _v220_probe_columns(path)
        lookup = _v220_alias_lookup(columns, aliases, labels)
        if not {"dx_main", "age"}.issubset(lookup):
            return -50, "missing_dx_or_age:" + ",".join(sorted(lookup)), lookup
        score = 20 + 2 * len(lookup)
        if "hospital_id_raw" in lookup:
            score += 6
        if "discharge_condition" in lookup:
            score += 4
        if "los_days" in lookup:
            score += 4
        if path.suffix.lower() in {".csv", ".sav", ".parquet"}:
            score += 3
        return score, "patient_level_schema", lookup
    except Exception as exc:
        return -20, f"probe_failed:{type(exc).__name__}:{exc}", {}


def _v220_standardize_chunk(
    chunk: pd.DataFrame,
    aliases: _V220Dict[str, _V220List[str]],
    country: str,
    file_year: int,
    labels: _V220Optional[_V220Dict[str, str]] = None,
) -> pd.DataFrame:
    lookup = _v220_alias_lookup(list(chunk.columns), aliases, labels)
    if not {"dx_main", "age"}.issubset(lookup):
        return pd.DataFrame()
    frame = chunk[list(dict.fromkeys(lookup.values()))].rename(
        columns={source: canonical for canonical, source in lookup.items()}
    ).copy()

    frame["dx_main"] = (
        frame["dx_main"].astype("string").str.strip().str.upper()
        .str.replace(".", "", regex=False)
        .str.replace(r"\s+", "", regex=True)
    )
    frame = frame[frame["dx_main"].str.startswith("S06", na=False)].copy()
    if frame.empty:
        return frame

    frame["age"] = pd.to_numeric(frame["age"], errors="coerce")
    if country == "equador" and "age_unit" in frame.columns:
        raw_unit = frame["age_unit"].astype("string").str.strip().str.upper()
        years_mask = raw_unit.isin(["4", "04", "A", "AÑO", "AÑOS", "ANO", "ANOS", "YEAR", "YEARS"])
        # Use the official unit code when informative; otherwise do not silently erase the whole file.
        if float(years_mask.mean()) >= 0.01:
            frame.loc[~years_mask, "age"] = np.nan
    # Chile's historical DEIS patient files store EDAD directly in years.
    frame = frame[frame["age"].between(int(CONFIG.get("min_age", 18)), 110, inclusive="both")].copy()
    if frame.empty:
        return frame

    if "year" in frame.columns:
        numeric_year = pd.to_numeric(frame["year"], errors="coerce")
        # Date-like or malformed year fields fall back to the year inferred from the path.
        numeric_year = numeric_year.where(numeric_year.between(1900, 2100), np.nan)
    else:
        numeric_year = pd.Series(np.nan, index=frame.index, dtype="float64")
    frame["year"] = numeric_year.fillna(file_year).astype("Int64")

    frame["month"] = (
        pd.to_numeric(frame["month"], errors="coerce").astype("Int64")
        if "month" in frame.columns else pd.Series(pd.NA, index=frame.index, dtype="Int64")
    )
    sex = frame.get("sex_raw", pd.Series(pd.NA, index=frame.index)).astype("string").str.strip().str.upper()
    frame["sex"] = sex.map({
        "1": "M", "2": "F", "M": "M", "F": "F", "H": "M",
        "HOMBRE": "M", "MUJER": "F", "MASCULINO": "M", "FEMENINO": "F",
    }).fillna("unknown")
    frame["los_days"] = (
        pd.to_numeric(frame["los_days"], errors="coerce").astype("Int64")
        if "los_days" in frame.columns else pd.Series(pd.NA, index=frame.index, dtype="Int64")
    )

    condition = frame.get("discharge_condition", pd.Series(pd.NA, index=frame.index)).astype("string").str.strip().str.upper()
    death = pd.Series(pd.NA, index=frame.index, dtype="Int64")
    if country == "equador":
        death.loc[condition.isin(["1", "01", "ALTA", "VIVO", "VIVA"])] = 0
        death.loc[condition.isin(["2", "02", "3", "03"]) | condition.str.contains("FALLEC|MUERTE|DEFUNC", na=False)] = 1
    else:
        death.loc[condition.isin(["1", "01", "VIVO", "VIVA", "ALTA", "MEJORADO"])] = 0
        death.loc[condition.isin(["2", "02"]) | condition.str.contains("FALLEC|MUERTE|DEFUNC", na=False)] = 1
    frame["death_in_hospital"] = death
    frame["country"] = country
    frame["source"] = "DEIS-MINSAL" if country == "chile" else "INEC-EH"

    if country == "chile" and "hospital_id_raw" in frame.columns:
        hid = frame["hospital_id_raw"].astype("string").str.strip()
        hid = hid.mask(hid.isin(["", "nan", "None", "<NA>"]))
        frame["hospital_id"] = hid.map(lambda x: f"CL_{x}" if pd.notna(x) else pd.NA).astype("string")
        frame["stable_hospital_id"] = frame["hospital_id"].notna().astype("Int64")
    else:
        frame["hospital_id"] = pd.Series(pd.NA, index=frame.index, dtype="string")
        frame["stable_hospital_id"] = pd.Series(0, index=frame.index, dtype="Int64")
    frame["hospital_volume_eligible"] = frame["stable_hospital_id"].astype("Int64")

    optional = (
        "hospital_region", "residence_region", "hospital_area", "residence_area",
        "facility_class", "facility_type", "facility_entity", "facility_sector",
        "ethnicity", "discharge_specialty", "insurance_type", "disability",
        "dx_secondary", "external_cause", "admission_date", "discharge_date",
        "icu_any", "icu_days", "urgent_admission", "cost_local_currency",
        "procedure_code_raw",
    )
    for col in optional:
        if col not in frame.columns:
            frame[col] = pd.NA
    return frame


def _v220_read_patient_file(
    path: _V220Path,
    aliases: _V220Dict[str, _V220List[str]],
    country: str,
    year: int,
    chunk_size: int = 100_000,
) -> pd.DataFrame:
    path = _V220Path(path)
    suffix = path.suffix.lower()
    columns, labels = _v220_probe_columns(path)
    lookup = _v220_alias_lookup(columns, aliases, labels)
    if not {"dx_main", "age"}.issubset(lookup):
        return pd.DataFrame()
    usecols = list(dict.fromkeys(lookup.values()))
    pieces: _V220List[pd.DataFrame] = []

    if suffix in {".csv", ".txt", ".tsv"}:
        encoding, separator, _ = _v200_probe_delimited(path)
        for chunk in pd.read_csv(
            path, sep=separator, encoding=encoding, encoding_errors="replace",
            dtype=str, usecols=usecols, chunksize=chunk_size, low_memory=True,
            on_bad_lines="skip",
        ):
            filtered = _v220_standardize_chunk(chunk, aliases, country, year, labels)
            if not filtered.empty:
                pieces.append(filtered)
            del chunk, filtered
            _v220_gc.collect()
        return pd.concat(pieces, ignore_index=True, sort=False) if pieces else pd.DataFrame()

    if suffix == ".sav":
        import pyreadstat
        # Chunked SPSS reading prevents 1M+ row files from consuming all Colab RAM.
        try:
            iterator = pyreadstat.read_file_in_chunks(
                pyreadstat.read_sav, str(path), chunksize=chunk_size,
                usecols=usecols, apply_value_formats=False,
            )
            for raw, _meta in iterator:
                filtered = _v220_standardize_chunk(raw, aliases, country, year, labels)
                if not filtered.empty:
                    pieces.append(filtered)
                del raw, filtered
                _v220_gc.collect()
            return pd.concat(pieces, ignore_index=True, sort=False) if pieces else pd.DataFrame()
        except Exception as chunk_exc:
            _v210_log("warning", f"[{country.upper()}-v2.2] SAV chunk fallback {path.name}: {chunk_exc}")
            raw, _ = pyreadstat.read_sav(str(path), usecols=usecols, apply_value_formats=False)
            return _v220_standardize_chunk(raw, aliases, country, year, labels)

    if suffix == ".dta":
        import pyreadstat
        raw, _ = pyreadstat.read_dta(str(path), usecols=usecols, apply_value_formats=False)
        return _v220_standardize_chunk(raw, aliases, country, year, labels)
    if suffix == ".parquet":
        raw = pd.read_parquet(path, columns=usecols)
        return _v220_standardize_chunk(raw, aliases, country, year, labels)
    if suffix in {".xlsx", ".xls", ".ods"}:
        raw = pd.read_excel(path, dtype=str, usecols=usecols)
        return _v220_standardize_chunk(raw, aliases, country, year, labels)

    raw = _read_tabular_file(path, country)
    return pd.DataFrame() if raw is None else _v220_standardize_chunk(raw, aliases, country, year, labels)


def audit_local_microdata_v220(
    countries: _V220Optional[_V220List[str]] = None,
    years: _V220Optional[_V220List[int]] = None,
) -> pd.DataFrame:
    countries = countries or ["chile", "equador"]
    years = years or list(CONFIG.get("study_years", range(2015, 2024)))
    mapping = {
        "chile": (_V220Path(DIRS["raw_cl"]), CHILE_PATIENT_ALIASES_V220),
        "equador": (_V220Path(DIRS["raw_ec"]), ECUADOR_PATIENT_ALIASES_V220),
    }
    rows = []
    for country in countries:
        raw_dir, aliases = mapping[country]
        for path in raw_dir.rglob("*"):
            if not path.is_file():
                continue
            matching_years = [y for y in years if _v220_path_has_year(path, y)]
            if not matching_years:
                continue
            for year in matching_years:
                score, reason, lookup = _v220_candidate_score(path, aliases, country, year)
                rows.append({
                    "country": country,
                    "year": year,
                    "file": str(path),
                    "size_mb": round(path.stat().st_size / 1024**2, 3),
                    "suffix": path.suffix.lower(),
                    "score": score,
                    "decision": "ACCEPT_CANDIDATE" if score > 0 else "REJECT",
                    "reason": reason,
                    "matched_fields": ",".join(sorted(lookup)),
                    "matched_columns": _v220_json.dumps(lookup, ensure_ascii=False),
                })
    audit = pd.DataFrame(rows)
    out = _V220Path(DIRS["qc"]) / "local_microdata_inventory_v220.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    audit.to_csv(out, index=False, encoding="utf-8-sig")
    _v210_log("info", f"[AUDIT-v2.2] inventário local salvo: {out}")
    return audit


def _v220_local_sources(country: str, raw_dir: _V220Path, years: _V220List[int]) -> _V220Dict[int, _V220List[_V220Path]]:
    result = {year: [] for year in years}
    for path in raw_dir.rglob("*"):
        if not path.is_file() or not _v220_is_patient_filename(path, country):
            continue
        for year in years:
            if _v220_path_has_year(path, year):
                result[year].append(path)
    for year in years:
        result[year] = sorted(set(result[year]), key=lambda p: p.stat().st_size, reverse=True)
    return result


def download_chile_official_v220(
    years: _V220Optional[_V220List[int]] = None,
    raw_dir: _V220Optional[_V220Path] = None,
) -> _V220Dict[int, _V220Optional[_V220Path]]:
    years = list(years or CONFIG.get("study_years", range(2015, 2024)))
    raw_dir = _V220Path(raw_dir or DIRS["raw_cl"])
    local = _v220_local_sources("chile", raw_dir, years)
    found = {year: (local[year][0] if local[year] else None) for year in years}
    missing = [year for year in years if found[year] is None]
    if missing:
        online = download_chile_official_v210(missing, raw_dir)
        for year in missing:
            found[year] = online.get(year)
    else:
        _v210_log("info", "[CL-v2.2] microdados locais encontrados para todos os anos; descoberta web ignorada.")
    return found


def download_equador_official_v220(
    years: _V220Optional[_V220List[int]] = None,
    raw_dir: _V220Optional[_V220Path] = None,
) -> _V220Dict[int, _V220Optional[_V220Path]]:
    years = list(years or CONFIG.get("study_years", range(2015, 2024)))
    raw_dir = _V220Path(raw_dir or DIRS["raw_ec"])
    local = _v220_local_sources("equador", raw_dir, years)
    found = {year: (local[year][0] if local[year] else None) for year in years}
    missing = [year for year in years if found[year] is None]
    if missing:
        online = download_equador_official_v210(missing, raw_dir)
        for year in missing:
            found[year] = online.get(year)
    else:
        _v210_log("info", "[EC-v2.2] microdados locais encontrados para todos os anos; descoberta web ignorada.")
    return found


def _v220_ingest_country_years(
    country: str,
    years: _V220List[int],
    raw_dir: _V220Path,
    inter_dir: _V220Path,
    aliases: _V220Dict[str, _V220List[str]],
) -> _V220Optional[pd.DataFrame]:
    raw_dir, inter_dir = _V220Path(raw_dir), _V220Path(inter_dir)
    inter_dir.mkdir(parents=True, exist_ok=True)
    files = [p for p in raw_dir.rglob("*") if p.is_file()]
    rows = []
    yearly_paths = []

    for year in years:
        checkpoint = inter_dir / f"{country}_s06_{year}_v220.parquet"
        if checkpoint.exists() and checkpoint.stat().st_size > 1_000:
            yearly_paths.append(checkpoint)
            _v210_log("info", f"[{country.upper()}-v2.2] checkpoint {year}: {checkpoint.name}")
            continue

        candidates = []
        for path in files:
            score, reason, lookup = _v220_candidate_score(path, aliases, country, year)
            if _v220_path_has_year(path, year):
                rows.append({
                    "country": country, "year": year, "file": str(path),
                    "size_mb": round(path.stat().st_size / 1024**2, 3),
                    "score": score, "reason": reason,
                    "matched_fields": ",".join(sorted(lookup)),
                })
            if score > 0:
                candidates.append((score, path))
        candidates.sort(key=lambda item: (item[0], item[1].stat().st_size), reverse=True)

        selected = None
        selected_path = None
        for score, path in candidates:
            try:
                frame = _v220_read_patient_file(path, aliases, country, year, chunk_size=100_000)
                if frame is not None and len(frame) >= 10:
                    selected, selected_path = frame, path
                    break
                rows.append({
                    "country": country, "year": year, "file": str(path),
                    "size_mb": round(path.stat().st_size / 1024**2, 3),
                    "score": score, "reason": "read_ok_but_no_adult_S06", "matched_fields": "",
                })
            except Exception as exc:
                rows.append({
                    "country": country, "year": year, "file": str(path),
                    "size_mb": round(path.stat().st_size / 1024**2, 3),
                    "score": score, "reason": f"read_failed:{type(exc).__name__}:{exc}", "matched_fields": "",
                })
                _v210_log("warning", f"[{country.upper()}-v2.2] {year}/{path.name}: {exc}")

        if selected is None or selected.empty:
            _v210_log("warning", f"[{country.upper()}-v2.2] {year}: nenhum microdado individual adulto S06 válido")
            continue
        selected["_source_file"] = str(selected_path)
        selected.to_parquet(checkpoint, index=False, engine="pyarrow", compression="snappy")
        yearly_paths.append(checkpoint)
        _v210_log("info", f"[{country.upper()}-v2.2] {year}: {len(selected):,} adultos S06 | {selected_path}")
        del selected
        _v220_gc.collect()

    audit_path = _V220Path(DIRS["qc"]) / f"intake_{country}_v220.csv"
    pd.DataFrame(rows).to_csv(audit_path, index=False, encoding="utf-8-sig")
    if not yearly_paths:
        return None

    # Reading only filtered annual checkpoints keeps RAM use bounded.
    frames = [pd.read_parquet(path) for path in yearly_paths]
    clean = pd.concat(frames, ignore_index=True, sort=False)
    clean_path = inter_dir / f"{country}_clean_v220.parquet"
    clean.to_parquet(clean_path, index=False, engine="pyarrow", compression="snappy")
    return clean


def run_chile_ingestion_v220(config: dict, dirs: dict) -> _V220Optional[pd.DataFrame]:
    if not config.get("countries", {}).get("chile", False):
        return None
    raw_dir = _V220Path(dirs["raw_cl"])
    inter_dir = _V220Path(dirs["intermediate"]) / "chile"
    download_chile_official_v220(list(config["study_years"]), raw_dir)
    return _v220_ingest_country_years(
        "chile", list(config["study_years"]), raw_dir, inter_dir, CHILE_PATIENT_ALIASES_V220
    )


def run_equador_ingestion_v220(config: dict, dirs: dict) -> _V220Optional[pd.DataFrame]:
    if not config.get("countries", {}).get("equador", False):
        return None
    raw_dir = _V220Path(dirs["raw_ec"])
    inter_dir = _V220Path(dirs["intermediate"]) / "equador"
    download_equador_official_v220(list(config["study_years"]), raw_dir)
    return _v220_ingest_country_years(
        "equador", list(config["study_years"]), raw_dir, inter_dir, ECUADOR_PATIENT_ALIASES_V220
    )


def _v220_patsy_native(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in out.columns:
        dtype_name = str(out[col].dtype)
        if dtype_name in {"Int64", "Int32", "Float64", "Float32", "boolean"}:
            out[col] = pd.to_numeric(out[col], errors="coerce").astype("float64")
        elif dtype_name.startswith("string") or dtype_name == "category":
            out[col] = out[col].astype("object").where(out[col].notna(), None)
    return out


def run_extended_association_models_v220(df: pd.DataFrame) -> pd.DataFrame:
    return run_extended_association_models_v200(_v220_patsy_native(df))


def _v220_df_stage(name: str, func, *args, columns=None, **kwargs) -> pd.DataFrame:
    value = _v210_safe_stage(name, func, *args, **kwargs)
    if isinstance(value, pd.DataFrame):
        return value
    return pd.DataFrame(columns=list(columns or []))


def run_postanalysis_v220(
    df_main: _V220Optional[pd.DataFrame] = None,
    df_cdm: _V220Optional[pd.DataFrame] = None,
    root: _V220Any = DEFAULT_ROOT,
    run_models: bool = True,
    run_factor_model: bool = True,
) -> _V220Dict[str, _V220Any]:
    root = _V220Path(root)
    dirs = _make_dirs(root)
    if df_main is None:
        df_main = _safe_read_parquet(root / "02_harmonized" / "cohort_main.parquet")
    if df_cdm is None:
        cdm_path = root / "02_harmonized" / "tce_harmonized_cdm.parquet"
        df_cdm = _safe_read_parquet(cdm_path) if cdm_path.exists() else df_main.copy()
    df_main = df_main.copy()
    df_cdm = df_cdm.copy()
    required = ["country", "year", "hospital_id", "age", "sex", "trauma_subtype", "death_in_hospital", "los_days"]
    missing = [c for c in required if c not in df_main.columns]
    if missing:
        raise KeyError(f"Colunas obrigatórias ausentes: {missing}")

    df_main, hy = add_volume_fields_v210(df_main)
    availability = _v220_df_stage("availability_v220", build_variable_availability, df_cdm)
    table1 = _v220_df_stage("table1_v220", build_table1, df_main)
    hospital_year = _v220_df_stage(
        "hospital_year_v220", build_hospital_year_table_v210, df_main, hy,
        columns=["country", "volume_quartile_hospital_year"]
    )
    annual = _v220_df_stage("annual_v220", build_annual_trends, df_main, columns=["country", "year"])
    subtype = _v220_df_stage("subtype_v220", build_subtype_table, df_main, columns=["country", "trauma_subtype"])
    concentration = _v220_df_stage("centralization_v220", build_centralization_metrics, hy, columns=["country"])
    volume_deciles = _v220_df_stage("deciles_v220", build_volume_decile_table, df_main, columns=["country"])

    for table, path in (
        (availability, dirs.qc / "variable_availability_by_country_v220"),
        (table1, dirs.tables / "Tabela1_pacientes_corrigida_v220"),
        (hospital_year, dirs.tables / "Tabela2_hospital_year_volume_v220"),
        (annual, dirs.tables / "Tabela3_tendencias_anuais_v220"),
        (subtype, dirs.tables / "Tabela4_subtipos_desfechos_v220"),
        (concentration, dirs.tables / "Tabela5_centralizacao_v220"),
        (volume_deciles, dirs.tables / "TabelaS_volume_decis_v220"),
    ):
        _save_table(table, path)

    native = _v220_patsy_native(df_main)
    model_results = pd.DataFrame()
    models = {}
    if run_models:
        suite = _v210_safe_stage("model_suite_v220", run_model_suite, native)
        if isinstance(suite, tuple) and len(suite) == 2:
            model_results, models = suite
            _save_table(model_results, dirs.tables / "Tabela6_modelos_volume_v220")

    factor_results = pd.DataFrame()
    factor_meta = {}
    if run_factor_model:
        factor = _v210_safe_stage("factor_model_v220", run_patient_factor_association_model, native)
        if isinstance(factor, tuple) and len(factor) == 2:
            factor_results, factor_meta = factor
            _save_table(factor_results, dirs.tables / "Tabela7_fatores_associados_mortalidade_v220")

    # Figures are optional outputs; missing columns in one table cannot abort the analysis.
    figure_specs = [
        (fig_flow, (df_main, dirs.fig_main / "Figura1_fluxo_bases_incluidas_v220.png"), {"country"}),
        (fig_annual_trends, (annual, dirs.fig_main / "Figura2_tendencia_mortalidade_v220.png"), {"country", "year"}),
        (fig_volume_deciles, (volume_deciles, dirs.fig_main / "Figura3_volume_mortalidade_decis_v220.png"), {"country"}),
        (fig_centralization, (concentration, dirs.fig_main / "Figura5_centralizacao_v220.png"), {"country"}),
        (fig_availability, (availability, dirs.fig_suppl / "FiguraS1_disponibilidade_variaveis_v220.png"), {"country"}),
        (fig_subtype_mortality, (subtype, dirs.fig_suppl / "FiguraS2_mortalidade_subtipo_v220.png"), {"country", "trauma_subtype"}),
    ]
    for func, args, needed in figure_specs:
        table = args[0]
        if isinstance(table, pd.DataFrame) and not table.empty and needed.issubset(table.columns):
            _v210_safe_stage(f"figure_{func.__name__}_v220", func, *args)
    if not model_results.empty and {"country"}.issubset(model_results.columns):
        _v210_safe_stage(
            "figure_forest_v220", fig_forest_country_models,
            model_results, dirs.fig_main / "Figura4_forest_volume_mortalidade_v220.png"
        )

    return {
        "version": "2.2.0", "dirs": dirs, "table1": table1,
        "availability": availability, "hospital_year": hospital_year,
        "annual_trends": annual, "subtype_outcomes": subtype,
        "centralization": concentration, "volume_deciles": volume_deciles,
        "model_results": model_results, "factor_results": factor_results,
        "models": models, "factor_meta": factor_meta,
    }


def run_advanced_analysis_v220(df_cdm: pd.DataFrame, df_main: pd.DataFrame, run_models: bool = True) -> _V220Dict[str, _V220Any]:
    post = _v210_safe_stage(
        "postanalysis_v220", run_postanalysis_v220,
        df_main=df_main, df_cdm=df_cdm, root=_V220Path(CONFIG["base_dir"]),
        run_models=run_models, run_factor_model=run_models,
    )
    stratified = _v210_safe_stage("stratified_v220", build_stratified_outcome_tables_v200, df_main)
    associations = (
        _v210_safe_stage("extended_associations_v220", run_extended_association_models_v220, df_main)
        if run_models else pd.DataFrame()
    )
    availability = _v210_safe_stage("availability_matrix_v220", build_variable_availability, df_cdm)
    extra_dir = _V220Path(CONFIG["base_dir"]) / "04_tables_v220"
    extra_dir.mkdir(parents=True, exist_ok=True)
    for obj, name in (
        (stratified, "Tabela_exploratoria_estratificada_v220"),
        (associations, "Tabela_modelos_associacoes_estendidas_v220"),
        (availability, "Matriz_disponibilidade_variaveis_v220"),
    ):
        if isinstance(obj, pd.DataFrame):
            _save_table(obj, extra_dir / name)
    return {"postanalysis_v220": post, "stratified": stratified, "extended_associations": associations, "availability": availability}


def purge_chile_equador_v220_checkpoints(remove_harmonized: bool = True) -> _V220List[str]:
    removed = []
    for country in ("chile", "equador"):
        directory = _V220Path(DIRS["intermediate"]) / country
        if directory.exists():
            for pattern in (f"{country}_s06_*_v2*.parquet", f"{country}_clean_v2*.parquet"):
                for path in directory.glob(pattern):
                    path.unlink(missing_ok=True)
                    removed.append(str(path))
    if remove_harmonized:
        harm = _V220Path(DIRS["harmonized"])
        for name in (
            "tce_harmonized_cdm.parquet", "cohort_main.parquet", "cohort_surgical.parquet",
            "cohort_dc_cran.parquet", "hospital_year_v210.parquet",
        ):
            path = harm / name
            if path.exists():
                path.unlink()
                removed.append(str(path))
    _v210_log("info", f"[PURGE-v2.2] {len(removed)} checkpoint(s) derivados removidos; Brasil/México preservados.")
    return removed


def run_pipeline_complete_v220(config_arg: _V220Optional[dict] = None, dirs_arg: _V220Optional[dict] = None):
    active_config = CONFIG if config_arg is None else config_arg
    active_dirs = DIRS if dirs_arg is None else dirs_arg
    active_config.setdefault("countries", {}).update({"brasil": True, "mexico": True, "chile": True, "equador": True})
    active_config["pipeline_version"] = "2.2.0"
    start = _v210_time.time()
    _v210_log("info", "▶▶▶ PIPELINE TCE MASTER v2.2.0 INICIADO ◀◀◀")

    country_dfs = {}
    country_dfs["brasil"] = globals()["run_brasil_ingestion"](active_config, active_dirs)
    _v220_gc.collect()
    country_dfs["mexico"] = run_mexico_ingestion_v210(active_config, active_dirs)
    _v220_gc.collect()
    country_dfs["chile"] = run_chile_ingestion_v220(active_config, active_dirs)
    _v220_gc.collect()
    country_dfs["equador"] = run_equador_ingestion_v220(active_config, active_dirs)
    _v220_gc.collect()

    coverage = _v210_source_coverage(country_dfs)
    coverage.to_csv(_V220Path(active_dirs["qc"]) / "country_source_coverage_v220.csv", index=False, encoding="utf-8-sig")
    _v210_safe_stage("raw_audit_v220", globals()["run_raw_audit"], country_dfs)
    build_crosswalk_table_v200(active_dirs)
    df_cdm, alerts = harmonize_all_v200(country_dfs)
    df_main, df_surg, df_dc = build_cohorts_v200(df_cdm)
    df_main, hospital_year = add_volume_fields_v210(df_main)
    df_surg = _v210_attach_volume_to_subset(df_surg, df_main)
    df_dc = _v210_attach_volume_to_subset(df_dc, df_main)

    harm_dir = _V220Path(active_dirs["harmonized"])
    harm_dir.mkdir(parents=True, exist_ok=True)
    df_cdm.to_parquet(harm_dir / "tce_harmonized_cdm.parquet", index=False, compression="snappy")
    df_main.to_parquet(harm_dir / "cohort_main.parquet", index=False, compression="snappy")
    df_surg.to_parquet(harm_dir / "cohort_surgical.parquet", index=False, compression="snappy")
    df_dc.to_parquet(harm_dir / "cohort_dc_cran.parquet", index=False, compression="snappy")
    hospital_year.to_parquet(harm_dir / "hospital_year_v220.parquet", index=False, compression="snappy")

    _v210_safe_stage("legacy_tables_v220", globals()["run_all_tables"], df_main, df_surg, df_dc)
    volume_cohort = df_main[
        df_main["hospital_id"].notna()
        & pd.to_numeric(df_main.get("hospital_volume_eligible", 1), errors="coerce").fillna(0).eq(1)
        & pd.to_numeric(df_main["hospital_volume_year"], errors="coerce").notna()
    ].copy()
    run_models = bool(active_config.get("run_main_analysis", True))
    models = {}
    if run_models and not volume_cohort.empty:
        models = _v210_safe_stage("main_models_v220", globals()["run_main_models_v132"], _v220_patsy_native(volume_cohort)) or {}
    advanced = run_advanced_analysis_v220(df_cdm, df_main, run_models=run_models)

    elapsed = round((_v210_time.time() - start) / 60, 2)
    summary = {
        "version": "2.2.0", "elapsed_minutes": elapsed,
        "records_by_country": df_main["country"].value_counts().to_dict(),
        "country_source_coverage": coverage.to_dict(orient="records"),
        "cdm_alerts": alerts,
    }
    support = _V220Path(active_config["base_dir"]) / "10_manuscript_support_v220"
    support.mkdir(parents=True, exist_ok=True)
    (support / "master_run_summary_v220.json").write_text(
        _v220_json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    _v210_log("info", f"▶▶▶ PIPELINE TCE MASTER v2.2.0 CONCLUÍDO em {elapsed} min ◀◀◀")
    return df_cdm, df_main, df_surg, df_dc, models, advanced


def verify_tce_master_v220() -> _V220Dict[str, _V220Any]:
    status = {
        "version": "2.2.0",
        "runner": getattr(globals().get("run_pipeline_complete"), "__name__", None),
        "chile_ingestion": getattr(globals().get("run_chile_ingestion"), "__name__", None),
        "equador_ingestion": getattr(globals().get("run_equador_ingestion"), "__name__", None),
        "postanalysis": getattr(globals().get("run_postanalysis_v14"), "__name__", None),
    }
    expected = {
        "runner": "run_pipeline_complete_v220",
        "chile_ingestion": "run_chile_ingestion_v220",
        "equador_ingestion": "run_equador_ingestion_v220",
        "postanalysis": "run_postanalysis_v220",
    }
    bad = {k: (status[k], v) for k, v in expected.items() if status[k] != v}
    if bad:
        raise RuntimeError(f"MASTER v2.2 não está ativo: {bad}")
    print(_v220_json.dumps(status, ensure_ascii=False, indent=2))
    return status


CONFIG["pipeline_version"] = "2.2.0"
CONFIG.setdefault("countries", {}).update({"brasil": True, "mexico": True, "chile": True, "equador": True})
globals()["CHILE_PATIENT_ALIASES_V220"] = CHILE_PATIENT_ALIASES_V220
globals()["ECUADOR_PATIENT_ALIASES_V220"] = ECUADOR_PATIENT_ALIASES_V220
globals()["download_chile_official_v200"] = download_chile_official_v220
globals()["download_equador_official_v200"] = download_equador_official_v220
globals()["run_chile_ingestion"] = run_chile_ingestion_v220
globals()["run_equador_ingestion"] = run_equador_ingestion_v220
globals()["run_postanalysis_v14"] = run_postanalysis_v220
globals()["run_extended_association_models_v200"] = run_extended_association_models_v220
globals()["run_advanced_analysis_v200"] = run_advanced_analysis_v220
globals()["run_pipeline_complete"] = run_pipeline_complete_v220
globals()["audit_local_microdata_v220"] = audit_local_microdata_v220
globals()["purge_chile_equador_v220_checkpoints"] = purge_chile_equador_v220_checkpoints
globals()["verify_tce_master_v220"] = verify_tce_master_v220
globals()["ACTIVE_TCE_PATCH"] = "2.2.0"
_v210_log("info", "[MASTER] TCE v2.2.0 ativado: pastas recursivas, SAV chunked e intake schema-flexível.")
# ============================================================
# TCE MULTINACIONAL — MASTER PATCH v2.3.0
# Exact local manifests, Chile 2015–2025, Ecuador egresos + camas 2015–2019,
# capacity linkage, expanded CDM and RAM-safe analysis.
# ============================================================


import gc as _v230_gc
import hashlib as _v230_hashlib
import json as _v230_json
import math as _v230_math
import re as _v230_re
import time as _v230_time
import traceback as _v230_traceback
import unicodedata as _v230_unicodedata
from pathlib import Path as _V230Path
from typing import Any as _V230Any, Dict as _V230Dict, Iterable as _V230Iterable, List as _V230List, Optional as _V230Optional, Tuple as _V230Tuple

TCE_MASTER_VERSION = "2.3.0"
PRIMARY_STUDY_YEARS_V230 = list(range(2015, 2024))
CHILE_SOURCE_YEARS_V230 = list(range(2015, 2026))
ECUADOR_SOURCE_YEARS_V230 = list(range(2015, 2020))

CONFIG["pipeline_version"] = TCE_MASTER_VERSION
CONFIG["study_years"] = PRIMARY_STUDY_YEARS_V230
CONFIG["primary_study_years"] = PRIMARY_STUDY_YEARS_V230
CONFIG["source_years"] = {
    "brasil": PRIMARY_STUDY_YEARS_V230,
    "mexico": PRIMARY_STUDY_YEARS_V230,
    "chile": CHILE_SOURCE_YEARS_V230,
    "equador": ECUADOR_SOURCE_YEARS_V230,
}
CONFIG["auto_download_missing_latam"] = False
CONFIG.setdefault("countries", {}).update({"brasil": True, "mexico": True, "chile": True, "equador": True})
CONFIG.setdefault("run_country_specific_analysis", True)
CONFIG.setdefault("run_exploratory_models", True)


def _v230_log(level: str, message: str) -> None:
    logger = globals().get("LOG")
    if logger is not None and hasattr(logger, level):
        getattr(logger, level)(message)
    else:
        print(f"[{level.upper()}] {message}")


def _v230_norm(value: _V230Any) -> str:
    text = "" if value is None else str(value)
    text = "".join(
        ch for ch in _v230_unicodedata.normalize("NFKD", text)
        if not _v230_unicodedata.combining(ch)
    )
    return _v230_re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def _v230_year_from_path(path: _V230Path) -> _V230Optional[int]:
    hits = _v230_re.findall(r"(?<!\d)(20\d{2})(?!\d)", str(path))
    return int(hits[-1]) if hits else None


def _v230_clean_code(value: _V230Any, width: _V230Optional[int] = None) -> _V230Optional[str]:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    text = _v230_re.sub(r"\.0+$", "", text)
    if text.lower() in {"", "nan", "none", "<na>"}:
        return None
    if width and text.isdigit():
        text = text.zfill(width)
    return text


def _v230_hash_token(*values: _V230Any, prefix: str = "") -> str:
    blob = "|".join("" if v is None or pd.isna(v) else str(v) for v in values)
    return prefix + _v230_hashlib.sha1(blob.encode("utf-8", errors="ignore")).hexdigest()[:16]


def _v230_role_from_filename(path: _V230Path) -> str:
    name = _v230_norm(path.name)
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return "DOCUMENTATION"
    if any(x in name for x in ("diccionario", "guia", "manual", "metadata", "esquema", "estructura")):
        return "DICTIONARY"
    if suffix == ".sav" and "egres" in name:
        return "PATIENT_EGRESOS"
    if suffix == ".sav" and "cama" in name:
        return "FACILITY_CAPACITY"
    if suffix in {".csv", ".txt", ".tsv"} and ("egre" in name or name.startswith("egr_") or name.startswith("egrdatos")):
        return "PATIENT_EGRESOS"
    return "OTHER"


def _v230_find_sources(raw_dir: _V230Path, country: str, role: str, years: _V230Iterable[int]) -> _V230Dict[int, _V230List[_V230Path]]:
    output = {int(year): [] for year in years}
    for path in _V230Path(raw_dir).rglob("*"):
        if not path.is_file() or _v230_role_from_filename(path) != role:
            continue
        year = _v230_year_from_path(path)
        if year in output:
            output[year].append(path)
    for year in output:
        output[year] = sorted(set(output[year]), key=lambda p: (p.stat().st_size, str(p)), reverse=True)
    return output


def _v230_probe_sav(path: _V230Path) -> _V230Tuple[_V230List[str], _V230Dict[str, str], _V230Optional[int], _V230Dict[str, _V230Dict[_V230Any, str]]]:
    import pyreadstat
    _, meta = pyreadstat.read_sav(str(path), metadataonly=True, apply_value_formats=False)
    return (
        list(getattr(meta, "column_names", []) or []),
        dict(getattr(meta, "column_names_to_labels", {}) or {}),
        getattr(meta, "number_rows", None),
        dict(getattr(meta, "variable_value_labels", {}) or {}),
    )


def _v230_probe_csv(path: _V230Path) -> _V230Tuple[str, str, _V230List[str]]:
    # Reuse the previously tested detector when available.
    try:
        encoding, separator, header = _v200_probe_delimited(path)
        return encoding, separator, list(header)
    except Exception:
        best = None
        for encoding in ("utf-8-sig", "utf-8", "latin-1", "cp1252"):
            for separator in (",", ";", "|", "\t"):
                try:
                    sample = pd.read_csv(path, sep=separator, encoding=encoding, nrows=5, dtype=str, on_bad_lines="skip")
                    candidate = (len(sample.columns), encoding, separator, list(sample.columns))
                    if best is None or candidate[0] > best[0]:
                        best = candidate
                except Exception:
                    pass
        if best is None:
            raise RuntimeError(f"Cabeçalho CSV ilegível: {path}")
        return best[1], best[2], best[3]


def _v230_alias_lookup(columns: _V230List[str], aliases: _V230Dict[str, _V230List[str]], labels: _V230Optional[_V230Dict[str, str]] = None) -> _V230Dict[str, str]:
    labels = labels or {}
    normalized = {_v230_norm(c): c for c in columns}
    label_normalized = {_v230_norm(labels.get(c, "")): c for c in columns if labels.get(c)}
    selected: _V230Dict[str, str] = {}
    for canonical, candidates in aliases.items():
        for candidate in [canonical] + list(candidates):
            key = _v230_norm(candidate)
            if key in normalized:
                selected[canonical] = normalized[key]
                break
            if key in label_normalized:
                selected[canonical] = label_normalized[key]
                break
    return selected


CHILE_ALIASES_V230 = {
    **CHILE_PATIENT_ALIASES_V220,
    "year": ["ANO", "ANIO", "AÑO", "ANO_EGR", "ANIO_EGR", "AÑO_EGR", "ANO_EGRESO", "ANIO_EGRESO"],
    "month": ["MES", "MES_EGR", "MES_EGRESO"],
    "hospital_id_raw": ["ESTAB", "COD_ESTAB", "CODESTAB", "CODIGO_ESTAB", "CODIGO_ESTABLECIMIENTO"],
    "hospital_region": ["SER_SALUD", "SERV_SALUD", "SERVICIO_SALUD", "REGION_ESTAB"],
    "residence_region": ["REGION", "REG_RES", "REGION_RES"],
    "residence_health_service": ["SERV_RES", "SERVICIO_RESIDENCIA"],
    "residence_municipality": ["COMUNA", "COMUNA_RES", "COD_COMUNA"],
    "age": ["EDAD", "EDAD_CANT", "EDAD_ANOS", "EDAD_AÑOS"],
    "age_unit": ["TIPO_EDAD", "EDAD_TIPO", "COD_EDAD", "UNIDAD_EDAD"],
    "sex_raw": ["SEXO", "SEX"],
    "dx_main": ["DIAG1", "DIAG_1", "DIAG_PRIN", "DIAG_PRINCIPAL", "DIAGNOSTICO_PRINCIPAL"],
    "dx_secondary": ["DIAG_SEC", "DIAGNOSTICO_SECUNDARIO"],
    "external_cause": ["DIAG2", "DIAG_2", "CAUSA_EXT", "CAUSA_EXTERNA"],
    "los_days": ["DIAS_ESTAD", "DIAS_ESTADA", "DIAS_ESTANCIA"],
    "discharge_condition": ["COND_EGR", "CONDICION_EGR", "CONDICION_EGRESO"],
    "discharge_specialty": ["SERC_EGR", "SERV_CL_EGR", "SERVICIO_EGRESO", "AREAF_EGR"],
    "insurance_type": ["PREVI", "PREVISION"],
    "beneficiary_type": ["BENEF", "BENEFICIARIO"],
    "surgery_recorded_raw": ["INTERV_Q", "INTERVENCION_Q", "INTERVENCION_QUIRURGICA"],
    "admission_date": ["FECHA_ING", "FECHA_INGR", "FECHA_INGRESO"],
    "discharge_date": ["FECHA_EGR", "FECHA_EGRESO"],
}

ECUADOR_EGRESOS_ALIASES_V230 = {
    **ECUADOR_PATIENT_ALIASES_V220,
    "year": ["anio_egr", "ano_egr", "año_egr", "anio_egreso"],
    "month": ["mes_egr", "mes_inv"],
    "hospital_region": ["prov_ubi"],
    "hospital_canton": ["cant_ubi"],
    "hospital_parish": ["parr_ubi"],
    "hospital_area": ["area_ubi"],
    "residence_region": ["prov_res"],
    "residence_canton": ["cant_res"],
    "residence_parish": ["parr_res"],
    "residence_area": ["area_res"],
    "facility_class": ["clase"],
    "facility_type": ["tipo"],
    "facility_entity": ["entidad"],
    "facility_sector": ["sector"],
    "nationality": ["nac_pac", "nom_pais", "cod_pais"],
    "age": ["edad"],
    "age_unit": ["cod_edad"],
    "sex_raw": ["sexo"],
    "ethnicity": ["etnia"],
    "dx_main": ["cau_cie10", "cie10_egr", "diag_egr"],
    "dx_group3": ["causa3"],
    "dx_chapter": ["cap221rx"],
    "los_days": ["dia_estad", "dias_estad"],
    "discharge_condition": ["con_egrpa", "cond_egr"],
    "discharge_specialty": ["esp_egrpa", "especialidad_egreso"],
    "admission_date": ["fecha_ingr"],
    "discharge_date": ["fecha_egr"],
}

ECUADOR_CAMAS_ALIASES_V230 = {
    "hospital_region": ["prov_ubie", "prov_ubi"],
    "hospital_canton": ["cant_ubie", "cant_ubi"],
    "hospital_parish": ["parr_ubie", "parr_ubi"],
    "hospital_area": ["area_ubie", "area_ubi"],
    "facility_class": ["clase"],
    "facility_type": ["tipo"],
    "facility_entity": ["entidad"],
    "facility_sector": ["sector"],
    "bed_total_available": ["camas_disp", "total_camas_disponibles"],
    "bed_total_normal": ["camas_dnor", "total_camas_dotacion_normal"],
    "bed_icu_normal": ["dotcinte", "dotcinteadult", "camas_uci"],
    "bed_emergency_normal": ["dotemerg", "camas_emergencia"],
    "reported_discharges": ["totegres", "total_egresos"],
    "reported_deaths_lt48": ["falmen48"],
    "reported_deaths_ge48": ["falmas48"],
    "reported_los_sum": ["dia_estad", "dias_estadia"],
}


def _v230_source_inventory() -> pd.DataFrame:
    rows: _V230List[_V230Dict[str, _V230Any]] = []
    source_sets = [
        ("chile", _V230Path(DIRS["raw_cl"]), CHILE_SOURCE_YEARS_V230),
        ("equador", _V230Path(DIRS["raw_ec"]), ECUADOR_SOURCE_YEARS_V230),
    ]
    for country, raw_dir, years in source_sets:
        for path in raw_dir.rglob("*"):
            if not path.is_file():
                continue
            year = _v230_year_from_path(path)
            role = _v230_role_from_filename(path)
            if year not in years or role == "OTHER":
                continue
            row = {
                "country": country,
                "year": year,
                "role": role,
                "path": str(path),
                "suffix": path.suffix.lower(),
                "size_mb": round(path.stat().st_size / 1024**2, 3),
                "status": "OK",
                "n_rows_metadata": pd.NA,
                "n_columns": pd.NA,
                "matched_fields": "",
                "columns": "",
            }
            try:
                if path.suffix.lower() == ".sav":
                    cols, labels, nrows, _ = _v230_probe_sav(path)
                    aliases = ECUADOR_EGRESOS_ALIASES_V230 if role == "PATIENT_EGRESOS" else ECUADOR_CAMAS_ALIASES_V230
                    lookup = _v230_alias_lookup(cols, aliases, labels)
                    row.update(n_rows_metadata=nrows, n_columns=len(cols), matched_fields=",".join(sorted(lookup)), columns=_v230_json.dumps(cols, ensure_ascii=False))
                elif path.suffix.lower() in {".csv", ".txt", ".tsv"}:
                    _, _, cols = _v230_probe_csv(path)
                    lookup = _v230_alias_lookup(cols, CHILE_ALIASES_V230)
                    row.update(n_columns=len(cols), matched_fields=",".join(sorted(lookup)), columns=_v230_json.dumps(cols, ensure_ascii=False))
                elif path.suffix.lower() in {".xlsx", ".xls"}:
                    row.update(columns=_v230_json.dumps(pd.ExcelFile(path).sheet_names, ensure_ascii=False))
            except Exception as exc:
                row["status"] = f"ERROR:{type(exc).__name__}:{exc}"
            rows.append(row)
    inventory = pd.DataFrame(rows)
    out = _V230Path(DIRS["qc"]) / "local_source_inventory_v230.csv"
    inventory.to_csv(out, index=False, encoding="utf-8-sig")
    return inventory


def inspect_latam_sources_v230() -> _V230Dict[str, pd.DataFrame]:
    inventory = _v230_source_inventory()
    coverage_rows = []
    for year in CHILE_SOURCE_YEARS_V230:
        subset = inventory[(inventory.country == "chile") & (inventory.year == year)]
        coverage_rows.append({"country": "chile", "year": year, "patient_files": int((subset.role == "PATIENT_EGRESOS").sum()), "capacity_files": 0, "dictionary_files": int(subset.role.isin(["DICTIONARY", "DOCUMENTATION"]).sum())})
    for year in ECUADOR_SOURCE_YEARS_V230:
        subset = inventory[(inventory.country == "equador") & (inventory.year == year)]
        coverage_rows.append({"country": "equador", "year": year, "patient_files": int((subset.role == "PATIENT_EGRESOS").sum()), "capacity_files": int((subset.role == "FACILITY_CAPACITY").sum()), "dictionary_files": int(subset.role.isin(["DICTIONARY", "DOCUMENTATION"]).sum())})
    coverage = pd.DataFrame(coverage_rows)
    coverage.to_csv(_V230Path(DIRS["qc"]) / "expected_source_coverage_v230.csv", index=False, encoding="utf-8-sig")
    _v230_log("info", "[INSPECT-v2.3] cobertura local:\n" + coverage.to_string(index=False))
    return {"inventory": inventory, "coverage": coverage}


def _v230_value_labels_for(meta_value_labels: _V230Dict[str, _V230Dict[_V230Any, str]], variable: _V230Optional[str]) -> _V230Dict[_V230Any, str]:
    if variable is None:
        return {}
    return dict(meta_value_labels.get(variable, {}) or {})


def _v230_year_unit_values(labels: _V230Dict[_V230Any, str], fallback: _V230Iterable[_V230Any]) -> set[str]:
    values = set()
    for key, label in labels.items():
        blob = _v230_norm(label)
        if "ano" in blob or "year" in blob:
            values.add(str(key).replace(".0", ""))
    if not values:
        values = {str(value).replace(".0", "") for value in fallback}
    return values


def _v230_common_record_id(country: str, path: _V230Path, rows: pd.Series) -> pd.Series:
    source_hash = _v230_hashlib.sha1(str(path).encode("utf-8", errors="ignore")).hexdigest()[:12]
    return rows.astype("int64").map(lambda n: f"{country.upper()}_{source_hash}_{n}").astype("string")


def _v230_standardize_chile_chunk(chunk: pd.DataFrame, path: _V230Path, year: int, row_offset: int = 0) -> pd.DataFrame:
    lookup = _v230_alias_lookup(list(chunk.columns), CHILE_ALIASES_V230)
    if not {"dx_main", "age"}.issubset(lookup):
        return pd.DataFrame()
    source_rows = pd.Series(range(row_offset, row_offset + len(chunk)), index=chunk.index, dtype="int64")
    frame = chunk[list(dict.fromkeys(lookup.values()))].rename(columns={v: k for k, v in lookup.items()}).copy()
    frame["dx_main"] = frame["dx_main"].astype("string").str.upper().str.strip().str.replace(".", "", regex=False).str.replace(r"\s+", "", regex=True)
    frame = frame[frame["dx_main"].str.startswith("S06", na=False)].copy()
    if frame.empty:
        return frame
    frame["age"] = pd.to_numeric(frame["age"], errors="coerce")
    # DEIS EGRE files normally store EDAD directly in completed years. If an explicit unit field exists,
    # use only recognizable year labels/codes; otherwise retain the documented EDAD field.
    if "age_unit" in frame.columns and frame["age_unit"].notna().any():
        unit = frame["age_unit"].astype("string").str.upper().str.strip().str.replace(r"\.0$", "", regex=True)
        recognized = unit.isin(["1", "4", "A", "ANO", "ANOS", "AÑO", "AÑOS", "YEAR", "YEARS"])
        if recognized.mean() >= 0.05:
            frame.loc[~recognized, "age"] = np.nan
    frame = frame[frame["age"].between(int(CONFIG.get("min_age", 18)), 110, inclusive="both")].copy()
    if frame.empty:
        return frame
    frame["year"] = int(year)
    frame["month"] = pd.to_numeric(frame.get("month"), errors="coerce").astype("Int64") if "month" in frame else pd.Series(pd.NA, index=frame.index, dtype="Int64")
    sex = frame.get("sex_raw", pd.Series(pd.NA, index=frame.index)).astype("string").str.upper().str.strip().str.replace(r"\.0$", "", regex=True)
    frame["sex"] = sex.map({"1": "M", "2": "F", "M": "M", "F": "F", "H": "M", "HOMBRE": "M", "MUJER": "F"}).fillna("unknown")
    frame["los_days"] = pd.to_numeric(frame.get("los_days"), errors="coerce").astype("Int64") if "los_days" in frame else pd.Series(pd.NA, index=frame.index, dtype="Int64")
    condition = frame.get("discharge_condition", pd.Series(pd.NA, index=frame.index)).astype("string").str.upper().str.strip().str.replace(r"\.0$", "", regex=True)
    death = pd.Series(pd.NA, index=frame.index, dtype="Int64")
    death.loc[condition.isin(["1", "MEJORADO", "VIVO", "ALTA"])] = 0
    death.loc[condition.isin(["2"]) | condition.str.contains("MUER|FALLEC|DEFUNC", na=False)] = 1
    frame["death_in_hospital"] = death
    hid = frame.get("hospital_id_raw", pd.Series(pd.NA, index=frame.index)).map(_v230_clean_code).astype("string")
    frame["hospital_id"] = hid.map(lambda x: f"CL_{x}" if pd.notna(x) else pd.NA).astype("string")
    frame["stable_hospital_id"] = frame["hospital_id"].notna().astype("Int64")
    frame["hospital_volume_eligible"] = frame["stable_hospital_id"].astype("Int64")
    frame["lag_volume_eligible"] = frame["stable_hospital_id"].astype("Int64")
    surg = frame.get("surgery_recorded_raw", pd.Series(pd.NA, index=frame.index)).astype("string").str.upper().str.strip().str.replace(r"\.0$", "", regex=True)
    any_surg = pd.Series(pd.NA, index=frame.index, dtype="Int64")
    any_surg.loc[surg.isin(["1", "SI", "S", "YES"])] = 1
    any_surg.loc[surg.isin(["2", "NO", "N"])] = 0
    frame["any_surgical_intervention"] = any_surg
    if {"hospital_region", "residence_health_service"}.issubset(frame.columns):
        known = frame["hospital_region"].notna() & frame["residence_health_service"].notna()
        frame["transfer_proxy"] = pd.Series(pd.NA, index=frame.index, dtype="Int64")
        frame.loc[known, "transfer_proxy"] = (frame.loc[known, "hospital_region"].astype("string") != frame.loc[known, "residence_health_service"].astype("string")).astype("Int64")
    frame["country"] = "chile"
    frame["source"] = "DEIS-MINSAL"
    frame["source_data_status"] = "PRIMARY_2015_2023" if year in PRIMARY_STUDY_YEARS_V230 else "CHILE_EXTENSION_2024_2025"
    frame["_source_file"] = str(path)
    frame["_source_row_number"] = source_rows.loc[frame.index].astype("Int64")
    frame["record_id"] = _v230_common_record_id("chile", path, source_rows.loc[frame.index])
    for col in (
        "dx_secondary", "external_cause", "insurance_type", "beneficiary_type",
        "residence_municipality", "residence_health_service", "discharge_specialty",
        "admission_date", "discharge_date", "hospital_region", "residence_region",
        "procedure_code_raw", "icu_any", "icu_days", "urgent_admission", "cost_local_currency",
    ):
        if col not in frame:
            frame[col] = pd.NA
    return frame


def _v230_ec_facility_key(frame: pd.DataFrame, year: int) -> pd.Series:
    parts = []
    widths = {"hospital_region": 2, "hospital_canton": 2, "hospital_parish": 2}
    for col in ("hospital_region", "hospital_canton", "hospital_parish", "facility_class", "facility_type", "facility_entity", "facility_sector"):
        if col in frame:
            parts.append(frame[col].map(lambda x, w=widths.get(col): _v230_clean_code(x, w)).fillna("NA").astype("string"))
        else:
            parts.append(pd.Series("NA", index=frame.index, dtype="string"))
    blob = parts[0]
    for part in parts[1:]:
        blob = blob + "|" + part
    return blob.map(lambda x: _v230_hash_token(x, prefix="EC_CELL_")).astype("string")


def _v230_standardize_ecuador_chunk(
    chunk: pd.DataFrame,
    path: _V230Path,
    year: int,
    labels: _V230Optional[_V230Dict[str, str]] = None,
    value_labels: _V230Optional[_V230Dict[str, _V230Dict[_V230Any, str]]] = None,
    row_offset: int = 0,
    filter_s06_adult: bool = True,
) -> pd.DataFrame:
    labels = labels or {}
    value_labels = value_labels or {}
    lookup = _v230_alias_lookup(list(chunk.columns), ECUADOR_EGRESOS_ALIASES_V230, labels)
    if not {"dx_main", "age"}.issubset(lookup):
        return pd.DataFrame()
    source_rows = pd.Series(range(row_offset, row_offset + len(chunk)), index=chunk.index, dtype="int64")
    frame = chunk[list(dict.fromkeys(lookup.values()))].rename(columns={v: k for k, v in lookup.items()}).copy()
    frame["dx_main"] = frame["dx_main"].astype("string").str.upper().str.strip().str.replace(".", "", regex=False).str.replace(r"\s+", "", regex=True)
    frame["age"] = pd.to_numeric(frame["age"], errors="coerce")
    if "age_unit" in frame:
        unit_col = lookup.get("age_unit")
        year_values = _v230_year_unit_values(_v230_value_labels_for(value_labels, unit_col), fallback=[4])
        unit = frame["age_unit"].astype("string").str.strip().str.replace(r"\.0$", "", regex=True)
        frame.loc[~unit.isin(year_values), "age"] = np.nan
    if filter_s06_adult:
        frame = frame[
            frame["dx_main"].str.startswith("S06", na=False)
            & frame["age"].between(int(CONFIG.get("min_age", 18)), 110, inclusive="both")
        ].copy()
        if frame.empty:
            return frame
    frame["year"] = int(year)
    frame["month"] = pd.to_numeric(frame.get("month"), errors="coerce").astype("Int64") if "month" in frame else pd.Series(pd.NA, index=frame.index, dtype="Int64")
    sex = frame.get("sex_raw", pd.Series(pd.NA, index=frame.index)).astype("string").str.strip().str.upper().str.replace(r"\.0$", "", regex=True)
    frame["sex"] = sex.map({"1": "M", "2": "F", "M": "M", "F": "F", "HOMBRE": "M", "MUJER": "F"}).fillna("unknown")
    frame["los_days"] = pd.to_numeric(frame.get("los_days"), errors="coerce").astype("Int64") if "los_days" in frame else pd.Series(pd.NA, index=frame.index, dtype="Int64")
    condition = frame.get("discharge_condition", pd.Series(pd.NA, index=frame.index)).astype("string").str.strip().str.upper().str.replace(r"\.0$", "", regex=True)
    death = pd.Series(pd.NA, index=frame.index, dtype="Int64")
    death.loc[condition.isin(["1", "ALTA", "VIVO", "VIVA"])] = 0
    death.loc[condition.isin(["2", "3"]) | condition.str.contains("FALLEC|MUERTE|DEFUNC", na=False)] = 1
    frame["death_in_hospital"] = death
    frame["facility_cell_id"] = _v230_ec_facility_key(frame, year)
    frame["hospital_id"] = pd.Series(pd.NA, index=frame.index, dtype="string")
    frame["stable_hospital_id"] = pd.Series(0, index=frame.index, dtype="Int64")
    frame["hospital_volume_eligible"] = pd.Series(0, index=frame.index, dtype="Int64")
    frame["lag_volume_eligible"] = pd.Series(0, index=frame.index, dtype="Int64")
    if {"hospital_region", "residence_region"}.issubset(frame.columns):
        known = frame["hospital_region"].notna() & frame["residence_region"].notna()
        frame["transfer_proxy"] = pd.Series(pd.NA, index=frame.index, dtype="Int64")
        frame.loc[known, "transfer_proxy"] = (frame.loc[known, "hospital_region"].astype("string") != frame.loc[known, "residence_region"].astype("string")).astype("Int64")
    frame["country"] = "equador"
    frame["source"] = "INEC-EGRESOS"
    frame["source_data_status"] = "PRIMARY_2015_2019"
    frame["_source_file"] = str(path)
    frame["_source_row_number"] = source_rows.loc[frame.index].astype("Int64")
    frame["record_id"] = _v230_common_record_id("equador", path, source_rows.loc[frame.index])
    for col in (
        "dx_secondary", "external_cause", "insurance_type", "disability", "procedure_code_raw",
        "icu_any", "icu_days", "urgent_admission", "cost_local_currency",
        "hospital_canton", "hospital_parish", "residence_canton", "residence_parish",
        "hospital_area", "residence_area", "facility_class", "facility_type",
        "facility_entity", "facility_sector", "ethnicity", "discharge_specialty",
        "admission_date", "discharge_date", "nationality", "dx_group3", "dx_chapter",
    ):
        if col not in frame:
            frame[col] = pd.NA
    return frame


def _v230_read_chile_file(path: _V230Path, year: int, chunk_size: int = 100_000) -> pd.DataFrame:
    encoding, separator, columns = _v230_probe_csv(path)
    lookup = _v230_alias_lookup(columns, CHILE_ALIASES_V230)
    if not {"dx_main", "age"}.issubset(lookup):
        raise RuntimeError(f"Chile {year}: DIAG1/EDAD não encontrados; colunas={columns[:30]}")
    usecols = list(dict.fromkeys(lookup.values()))
    pieces = []
    offset = 0
    for chunk in pd.read_csv(path, sep=separator, encoding=encoding, encoding_errors="replace", dtype=str, usecols=usecols, chunksize=chunk_size, low_memory=True, on_bad_lines="skip"):
        filtered = _v230_standardize_chile_chunk(chunk, path, year, offset)
        if not filtered.empty:
            pieces.append(filtered)
        offset += len(chunk)
        del chunk, filtered
        _v230_gc.collect()
    return pd.concat(pieces, ignore_index=True, sort=False) if pieces else pd.DataFrame()


def _v230_sav_chunk_iterator(path: _V230Path, usecols: _V230List[str], chunk_size: int = 100_000):
    import pyreadstat
    _, meta = pyreadstat.read_sav(str(path), metadataonly=True, apply_value_formats=False)
    nrows = int(getattr(meta, "number_rows", 0) or 0)
    if nrows <= 0:
        # Some SAV writers omit row count in metadata; pyreadstat's iterator remains RAM safe.
        for raw, chunk_meta in pyreadstat.read_file_in_chunks(pyreadstat.read_sav, str(path), chunksize=chunk_size, usecols=usecols, apply_value_formats=False):
            yield raw, chunk_meta
        return
    for offset in range(0, nrows, chunk_size):
        raw, chunk_meta = pyreadstat.read_sav(str(path), usecols=usecols, row_offset=offset, row_limit=min(chunk_size, nrows - offset), apply_value_formats=False)
        yield raw, chunk_meta


def _v230_read_ecuador_egresos(path: _V230Path, year: int, chunk_size: int = 100_000) -> _V230Tuple[pd.DataFrame, pd.DataFrame]:
    columns, labels, _, value_labels = _v230_probe_sav(path)
    lookup = _v230_alias_lookup(columns, ECUADOR_EGRESOS_ALIASES_V230, labels)
    if not {"dx_main", "age"}.issubset(lookup):
        raise RuntimeError(f"Equador {year}: cau_cie10/edad não encontrados; colunas={columns[:30]}")
    usecols = list(dict.fromkeys(lookup.values()))
    filtered_pieces = []
    aggregate_pieces = []
    offset = 0
    for raw, _ in _v230_sav_chunk_iterator(path, usecols, chunk_size):
        all_rows = _v230_standardize_ecuador_chunk(raw, path, year, labels, value_labels, offset, filter_s06_adult=False)
        if not all_rows.empty and "facility_cell_id" in all_rows:
            aggregate_pieces.append(
                all_rows.groupby("facility_cell_id", dropna=False, observed=True).agg(
                    patient_file_discharges=("facility_cell_id", "size"),
                    patient_file_deaths=("death_in_hospital", "sum"),
                    patient_file_los_sum=("los_days", "sum"),
                ).reset_index()
            )
        filtered = all_rows[
            all_rows["dx_main"].astype("string").str.startswith("S06", na=False)
            & pd.to_numeric(all_rows["age"], errors="coerce").between(int(CONFIG.get("min_age", 18)), 110, inclusive="both")
        ].copy()
        if not filtered.empty:
            filtered_pieces.append(filtered)
        offset += len(raw)
        del raw, all_rows, filtered
        _v230_gc.collect()
    filtered_df = pd.concat(filtered_pieces, ignore_index=True, sort=False) if filtered_pieces else pd.DataFrame()
    if aggregate_pieces:
        aggregate = pd.concat(aggregate_pieces, ignore_index=True).groupby("facility_cell_id", observed=True).sum(numeric_only=True).reset_index()
    else:
        aggregate = pd.DataFrame(columns=["facility_cell_id", "patient_file_discharges", "patient_file_deaths", "patient_file_los_sum"])
    return filtered_df, aggregate


def _v230_read_ecuador_camas(path: _V230Path, year: int) -> pd.DataFrame:
    import pyreadstat
    columns, labels, _, _ = _v230_probe_sav(path)
    lookup = _v230_alias_lookup(columns, ECUADOR_CAMAS_ALIASES_V230, labels)
    key_fields = {"hospital_region", "hospital_canton"}
    if not key_fields.issubset(lookup):
        raise RuntimeError(f"Camas Equador {year}: geografia insuficiente; encontrados={lookup}")
    usecols = list(dict.fromkeys(lookup.values()))
    raw, _ = pyreadstat.read_sav(str(path), usecols=usecols, apply_value_formats=False)
    frame = raw.rename(columns={v: k for k, v in lookup.items()}).copy()
    frame["year"] = int(year)
    frame["facility_cell_id"] = _v230_ec_facility_key(frame, year)
    for col in ("bed_total_available", "bed_total_normal", "bed_icu_normal", "bed_emergency_normal", "reported_discharges", "reported_deaths_lt48", "reported_deaths_ge48", "reported_los_sum"):
        frame[col] = pd.to_numeric(frame.get(col), errors="coerce") if col in frame else np.nan
    frame["reported_deaths"] = frame[["reported_deaths_lt48", "reported_deaths_ge48"]].sum(axis=1, min_count=1)
    counts = frame.groupby("facility_cell_id", observed=True).size().rename("capacity_key_rows")
    frame = frame.merge(counts, on="facility_cell_id", how="left")
    frame["capacity_key_unique"] = frame["capacity_key_rows"].eq(1).astype("Int64")
    frame["_capacity_source_file"] = str(path)
    return frame


def _v230_link_ecuador_capacity(patients: pd.DataFrame, all_aggregate: pd.DataFrame, capacity: pd.DataFrame, year: int) -> _V230Tuple[pd.DataFrame, pd.DataFrame]:
    if patients.empty or capacity.empty:
        return patients, pd.DataFrame()
    # Collapse capacity to one row per derived facility cell. If more than one establishment
    # shares the same cell, the row remains explicitly AMBIGUOUS and is not treated as an
    # exact hospital linkage. Totals are summed because the cell then represents an
    # institutional aggregate rather than a single facility.
    numeric_capacity = [
        "bed_total_available", "bed_total_normal", "bed_icu_normal",
        "bed_emergency_normal", "reported_discharges", "reported_deaths",
        "reported_los_sum",
    ]
    aggregation = {col: "sum" for col in numeric_capacity if col in capacity.columns}
    aggregation["capacity_key_rows"] = "max"
    aggregation["capacity_key_unique"] = "max"
    cap_link = capacity.groupby("facility_cell_id", observed=True, as_index=False).agg(aggregation)
    link = cap_link.merge(all_aggregate, on="facility_cell_id", how="left", validate="one_to_one")
    for observed, reported, output in (
        ("patient_file_discharges", "reported_discharges", "rel_diff_discharges"),
        ("patient_file_deaths", "reported_deaths", "rel_diff_deaths"),
        ("patient_file_los_sum", "reported_los_sum", "rel_diff_los"),
    ):
        denominator = pd.to_numeric(link[reported], errors="coerce").abs().replace(0, np.nan)
        link[output] = (pd.to_numeric(link[observed], errors="coerce") - pd.to_numeric(link[reported], errors="coerce")).abs() / denominator
    link["facility_linkage_status"] = "NO_PATIENT_AGGREGATE"
    unique = pd.to_numeric(link["capacity_key_unique"], errors="coerce").fillna(0).eq(1)
    has_counts = link["reported_discharges"].notna() & link["patient_file_discharges"].notna()
    good_counts = link["rel_diff_discharges"].le(0.05)
    link.loc[unique & ~has_counts, "facility_linkage_status"] = "UNIQUE_KEY_COUNTS_UNAVAILABLE"
    link.loc[unique & has_counts & ~good_counts, "facility_linkage_status"] = "UNIQUE_KEY_COUNTS_DISCORDANT"
    link.loc[unique & has_counts & good_counts, "facility_linkage_status"] = "VALIDATED_COUNTS"
    link.loc[~unique, "facility_linkage_status"] = "AMBIGUOUS_KEY"
    columns_to_merge = [
        "facility_cell_id", "facility_linkage_status", "capacity_key_unique",
        "bed_total_available", "bed_total_normal", "bed_icu_normal", "bed_emergency_normal",
        "reported_discharges", "reported_deaths", "reported_los_sum",
        "rel_diff_discharges", "rel_diff_deaths", "rel_diff_los",
    ]
    patients = patients.merge(link[columns_to_merge], on="facility_cell_id", how="left", validate="many_to_one")
    patients["facility_capacity_linked"] = patients["facility_linkage_status"].isin(["VALIDATED_COUNTS", "UNIQUE_KEY_COUNTS_UNAVAILABLE"]).astype("Int64")
    # This is an explicitly exploratory facility-cell key, not a claimed official hospital identifier.
    patients["facility_cell_volume_eligible"] = patients["facility_linkage_status"].eq("VALIDATED_COUNTS").astype("Int64")
    linked = patients["facility_cell_volume_eligible"].eq(1)
    if linked.any():
        volume = patients.loc[linked].groupby(["facility_cell_id", "year"], observed=True).size().rename("facility_cell_tbi_volume_year").reset_index()
        patients = patients.merge(volume, on=["facility_cell_id", "year"], how="left", validate="many_to_one")
    else:
        patients["facility_cell_tbi_volume_year"] = pd.NA
    return patients, link


def run_chile_ingestion_v230(config: dict = CONFIG, dirs: dict = DIRS) -> _V230Optional[pd.DataFrame]:
    if not config.get("countries", {}).get("chile", True):
        return None
    raw_dir = _V230Path(dirs["raw_cl"])
    inter_dir = _V230Path(dirs["intermediate"]) / "chile"
    inter_dir.mkdir(parents=True, exist_ok=True)
    sources = _v230_find_sources(raw_dir, "chile", "PATIENT_EGRESOS", CHILE_SOURCE_YEARS_V230)
    yearly = []
    intake = []
    for year in CHILE_SOURCE_YEARS_V230:
        checkpoint = inter_dir / f"chile_s06_{year}_v230.parquet"
        if checkpoint.exists() and checkpoint.stat().st_size > 1_000:
            yearly.append(checkpoint)
            continue
        candidates = sources.get(year, [])
        if not candidates:
            intake.append({"year": year, "status": "MISSING", "file": ""})
            _v230_log("warning", f"[CHILE-v2.3] {year}: arquivo EGRESOS não encontrado")
            continue
        selected = None
        for path in candidates:
            try:
                frame = _v230_read_chile_file(path, year)
                intake.append({"year": year, "status": "READ", "file": str(path), "n_adult_s06": len(frame)})
                if not frame.empty:
                    selected = frame
                    break
            except Exception as exc:
                intake.append({"year": year, "status": f"ERROR:{type(exc).__name__}:{exc}", "file": str(path)})
        if selected is None or selected.empty:
            _v230_log("warning", f"[CHILE-v2.3] {year}: nenhum adulto S06 após leitura")
            continue
        selected.to_parquet(checkpoint, index=False, engine="pyarrow", compression="snappy")
        yearly.append(checkpoint)
        _v230_log("info", f"[CHILE-v2.3] {year}: {len(selected):,} adultos S06 | {selected['_source_file'].iloc[0]}")
        del selected
        _v230_gc.collect()
    pd.DataFrame(intake).to_csv(_V230Path(dirs["qc"]) / "intake_chile_v230.csv", index=False, encoding="utf-8-sig")
    if not yearly:
        return None
    frames = [pd.read_parquet(path) for path in yearly]
    all_years = pd.concat(frames, ignore_index=True, sort=False)
    all_years.to_parquet(inter_dir / "chile_clean_all_2015_2025_v230.parquet", index=False, compression="snappy")
    primary = all_years[pd.to_numeric(all_years["year"], errors="coerce").isin(PRIMARY_STUDY_YEARS_V230)].copy()
    extension = all_years[pd.to_numeric(all_years["year"], errors="coerce").isin([2024, 2025])].copy()
    primary.to_parquet(inter_dir / "chile_clean_v230.parquet", index=False, compression="snappy")
    if not extension.empty:
        extension.to_parquet(inter_dir / "chile_extension_2024_2025_v230.parquet", index=False, compression="snappy")
    return primary


def run_equador_ingestion_v230(config: dict = CONFIG, dirs: dict = DIRS) -> _V230Optional[pd.DataFrame]:
    if not config.get("countries", {}).get("equador", True):
        return None
    raw_dir = _V230Path(dirs["raw_ec"])
    inter_dir = _V230Path(dirs["intermediate"]) / "equador"
    inter_dir.mkdir(parents=True, exist_ok=True)
    patient_sources = _v230_find_sources(raw_dir, "equador", "PATIENT_EGRESOS", ECUADOR_SOURCE_YEARS_V230)
    capacity_sources = _v230_find_sources(raw_dir, "equador", "FACILITY_CAPACITY", ECUADOR_SOURCE_YEARS_V230)
    yearly = []
    intake = []
    linkage_frames = []
    capacity_frames = []
    for year in ECUADOR_SOURCE_YEARS_V230:
        checkpoint = inter_dir / f"equador_s06_{year}_v230.parquet"
        if checkpoint.exists() and checkpoint.stat().st_size > 1_000:
            yearly.append(checkpoint)
            continue
        patient_candidates = patient_sources.get(year, [])
        capacity_candidates = capacity_sources.get(year, [])
        if not patient_candidates:
            intake.append({"year": year, "role": "PATIENT_EGRESOS", "status": "MISSING", "file": ""})
            continue
        patients = None
        all_aggregate = pd.DataFrame()
        patient_path = None
        for path in patient_candidates:
            try:
                candidate, aggregate = _v230_read_ecuador_egresos(path, year)
                intake.append({"year": year, "role": "PATIENT_EGRESOS", "status": "READ", "file": str(path), "n_adult_s06": len(candidate)})
                if not candidate.empty:
                    patients, all_aggregate, patient_path = candidate, aggregate, path
                    break
            except Exception as exc:
                intake.append({"year": year, "role": "PATIENT_EGRESOS", "status": f"ERROR:{type(exc).__name__}:{exc}", "file": str(path)})
        if patients is None or patients.empty:
            _v230_log("warning", f"[EQUADOR-v2.3] {year}: nenhum adulto S06 após leitura")
            continue
        capacity = pd.DataFrame()
        for path in capacity_candidates:
            try:
                capacity = _v230_read_ecuador_camas(path, year)
                intake.append({"year": year, "role": "FACILITY_CAPACITY", "status": "READ", "file": str(path), "n_rows": len(capacity)})
                if not capacity.empty:
                    break
            except Exception as exc:
                intake.append({"year": year, "role": "FACILITY_CAPACITY", "status": f"ERROR:{type(exc).__name__}:{exc}", "file": str(path)})
        if not capacity.empty:
            patients, linkage = _v230_link_ecuador_capacity(patients, all_aggregate, capacity, year)
            linkage["year"] = year
            linkage_frames.append(linkage)
            capacity_frames.append(capacity)
        else:
            patients["facility_linkage_status"] = "CAPACITY_FILE_UNAVAILABLE"
            patients["facility_capacity_linked"] = pd.Series(0, index=patients.index, dtype="Int64")
            patients["facility_cell_volume_eligible"] = pd.Series(0, index=patients.index, dtype="Int64")
        patients.to_parquet(checkpoint, index=False, engine="pyarrow", compression="snappy")
        yearly.append(checkpoint)
        _v230_log("info", f"[EQUADOR-v2.3] {year}: {len(patients):,} adultos S06 | {patient_path}")
        del patients, all_aggregate, capacity
        _v230_gc.collect()
    pd.DataFrame(intake).to_csv(_V230Path(dirs["qc"]) / "intake_equador_v230.csv", index=False, encoding="utf-8-sig")
    if linkage_frames:
        pd.concat(linkage_frames, ignore_index=True, sort=False).to_csv(_V230Path(dirs["qc"]) / "equador_capacity_linkage_qc_v230.csv", index=False, encoding="utf-8-sig")
    if capacity_frames:
        pd.concat(capacity_frames, ignore_index=True, sort=False).to_parquet(inter_dir / "equador_capacity_2015_2019_v230.parquet", index=False, compression="snappy")
    if not yearly:
        return None
    clean = pd.concat([pd.read_parquet(path) for path in yearly], ignore_index=True, sort=False)
    clean.to_parquet(inter_dir / "equador_clean_v230.parquet", index=False, compression="snappy")
    return clean


V230_EXTRA_COLUMNS = [
    "record_id", "_source_file", "_source_row_number", "source_data_status",
    "stable_hospital_id", "hospital_volume_eligible", "lag_volume_eligible",
    "residence_health_service", "residence_municipality", "beneficiary_type",
    "any_surgical_intervention", "facility_cell_id", "facility_cell_volume_eligible",
    "facility_cell_tbi_volume_year", "facility_capacity_linked", "facility_linkage_status",
    "capacity_key_unique", "bed_total_available", "bed_total_normal", "bed_icu_normal",
    "bed_emergency_normal", "reported_discharges", "reported_deaths", "reported_los_sum",
    "rel_diff_discharges", "rel_diff_deaths", "rel_diff_los", "hospital_canton",
    "hospital_parish", "residence_canton", "residence_parish", "hospital_area",
    "residence_area", "facility_class", "facility_type", "facility_entity",
    "facility_sector", "ethnicity", "discharge_specialty", "insurance_type",
    "disability", "nationality", "dx_group3", "dx_chapter", "admission_date",
    "discharge_date",
]


def harmonize_all_v230(country_dfs: _V230Dict[str, _V230Optional[pd.DataFrame]]) -> _V230Tuple[pd.DataFrame, _V230List[str]]:
    frames = []
    provenance = []
    for country, frame in country_dfs.items():
        if frame is None or frame.empty:
            _v230_log("warning", f"[HARM-v2.3] {country}: sem dados válidos")
            continue
        final = globals()["finalize_country_df"](frame, country)
        frames.append(final)
        if "_source_file" in frame:
            for source_file, subset in frame.groupby("_source_file", dropna=False):
                provenance.append({"country": country, "source_file": source_file, "n_records": len(subset)})
    if not frames:
        raise RuntimeError("Nenhum país com microdados individuais válidos")
    cdm = pd.concat(frames, ignore_index=True, sort=False)
    cdm = apply_crosswalk_v200(cdm)
    cdm = compute_hospital_volume_v200(cdm)
    if "transfer_proxy" not in cdm:
        cdm["transfer_proxy"] = pd.Series(pd.NA, index=cdm.index, dtype="Int64")
    alerts = globals()["validate_cdm"](cdm)
    # Preserve the expanded country-specific fields instead of truncating to the original CDM.
    ordered = [c for c in CDM_SCHEMA if c in cdm.columns]
    ordered += [c for c in V230_EXTRA_COLUMNS if c in cdm.columns and c not in ordered]
    ordered += [c for c in cdm.columns if c not in ordered]
    cdm = cdm[ordered].copy()
    globals()["save_parquet"](cdm, _V230Path(DIRS["harmonized"]) / "tce_harmonized_cdm_v230.parquet", "CDM v2.3")
    globals()["save_parquet"](cdm, _V230Path(DIRS["harmonized"]) / "tce_harmonized_cdm.parquet", "CDM v2.3 active")
    globals()["save_csv_xlsx"](globals()["quick_audit"](cdm, "CDM v2.3"), _V230Path(DIRS["qc"]) / "audit_cdm_v230")
    if provenance:
        globals()["save_csv_xlsx"](pd.DataFrame(provenance), _V230Path(DIRS["qc"]) / "source_provenance_v230")
    return cdm, alerts


def build_cohorts_v230(df_cdm: pd.DataFrame) -> _V230Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    all_available = df_cdm[
        pd.to_numeric(df_cdm["age"], errors="coerce").ge(int(CONFIG.get("min_age", 18)))
        & df_cdm["dx_main"].astype("string").str.startswith("S06", na=False)
    ].copy()
    all_available.to_parquet(_V230Path(DIRS["harmonized"]) / "cohort_main_all_available_v230.parquet", index=False, compression="snappy")
    primary = all_available[pd.to_numeric(all_available["year"], errors="coerce").isin(PRIMARY_STUDY_YEARS_V230)].copy()
    surgical = primary[pd.to_numeric(primary.get("primary_acute_surgery", 0), errors="coerce").fillna(0).eq(1)].copy()
    dc_cran = surgical[surgical["procedure_group_v2"].isin(["DECOMPRESSIVE_CODED", "ACUTE_CRANIAL_SURGERY"])].copy()
    dc_cran["procedure_class_analysis"] = np.where(dc_cran["procedure_group_v2"].eq("DECOMPRESSIVE_CODED"), "DC", "CRAN")
    primary.to_parquet(_V230Path(DIRS["harmonized"]) / "cohort_main.parquet", index=False, compression="snappy")
    surgical.to_parquet(_V230Path(DIRS["harmonized"]) / "cohort_surgical.parquet", index=False, compression="snappy")
    dc_cran.to_parquet(_V230Path(DIRS["harmonized"]) / "cohort_dc_cran.parquet", index=False, compression="snappy")
    chile_surgery = primary[(primary.country == "chile") & primary.get("any_surgical_intervention", pd.Series(pd.NA, index=primary.index)).notna()].copy()
    if not chile_surgery.empty:
        chile_surgery.to_parquet(_V230Path(DIRS["harmonized"]) / "cohort_chile_any_surgery_v230.parquet", index=False, compression="snappy")
    ec_capacity = primary[(primary.country == "equador") & pd.to_numeric(primary.get("facility_capacity_linked", 0), errors="coerce").fillna(0).eq(1)].copy()
    if not ec_capacity.empty:
        ec_capacity.to_parquet(_V230Path(DIRS["harmonized"]) / "cohort_equador_capacity_linked_v230.parquet", index=False, compression="snappy")
    return primary, surgical, dc_cran


def _v230_native_for_model(df: pd.DataFrame) -> pd.DataFrame:
    return _v220_patsy_native(df)


def _v230_fit_exploratory_factor(data: pd.DataFrame, country: str, predictor: str, outcome: str = "death_in_hospital") -> _V230List[_V230Dict[str, _V230Any]]:
    needed = [outcome, predictor, "age", "sex", "year", "trauma_subtype"]
    subset = data[[c for c in needed if c in data]].copy()
    if predictor not in subset or subset[predictor].notna().sum() < 100:
        return []
    subset = _v230_native_for_model(subset)
    subset[outcome] = pd.to_numeric(subset[outcome], errors="coerce")
    subset["age"] = pd.to_numeric(subset["age"], errors="coerce")
    subset = subset.dropna(subset=[outcome, "age", predictor])
    if outcome == "death_in_hospital":
        subset = subset[subset[outcome].isin([0, 1])]
    if len(subset) < 500 or subset[outcome].nunique() < 2:
        return []
    predictor_numeric = pd.api.types.is_numeric_dtype(subset[predictor]) and subset[predictor].nunique() > 8
    term = predictor if predictor_numeric else f"C({predictor})"
    formula = f"{outcome} ~ {term} + bs(age, df=4, degree=3) + C(sex) + C(year) + C(trauma_subtype)"
    try:
        model = smf.glm(formula=formula, data=subset, family=sm.families.Binomial() if outcome == "death_in_hospital" else sm.families.NegativeBinomial()).fit(cov_type="HC1", maxiter=100)
    except Exception as exc:
        _v230_log("warning", f"[EXP-v2.3] {country}/{predictor}/{outcome}: {exc}")
        return []
    rows = []
    for term_name in model.params.index:
        if term_name == "Intercept" or predictor not in term_name:
            continue
        beta = float(model.params[term_name])
        se = float(model.bse[term_name])
        rows.append({
            "analysis_role": "EXPLORATORY_ASSOCIATION",
            "country": country,
            "outcome": outcome,
            "predictor": predictor,
            "term": term_name,
            "effect": float(np.exp(beta)),
            "ci_low": float(np.exp(beta - 1.96 * se)),
            "ci_high": float(np.exp(beta + 1.96 * se)),
            "p_value": float(model.pvalues[term_name]),
            "n": int(model.nobs),
            "model": "GLM_BINOMIAL_HC1" if outcome == "death_in_hospital" else "GLM_NEGATIVE_BINOMIAL_HC1",
        })
    return rows


def run_country_specific_analyses_v230(df_main: pd.DataFrame) -> _V230Dict[str, pd.DataFrame]:
    output: _V230Dict[str, pd.DataFrame] = {}
    tables_dir = _V230Path(CONFIG["base_dir"]) / "04_tables_v230"
    tables_dir.mkdir(parents=True, exist_ok=True)
    # Descriptive analyses retain all individual records and report actual variable availability.
    chile = df_main[df_main.country == "chile"].copy()
    if not chile.empty:
        variables = [c for c in ("year", "trauma_subtype", "insurance_type", "beneficiary_type", "transfer_proxy", "any_surgical_intervention", "external_cause", "discharge_specialty") if c in chile and chile[c].notna().any()]
        rows = []
        for variable in variables:
            grouped = chile.groupby(variable, dropna=False, observed=True).agg(
                n=("country", "size"),
                deaths=("death_in_hospital", "sum"),
                mortality=("death_in_hospital", "mean"),
                los_median=("los_days", "median"),
                los_q1=("los_days", lambda s: pd.to_numeric(s, errors="coerce").quantile(0.25)),
                los_q3=("los_days", lambda s: pd.to_numeric(s, errors="coerce").quantile(0.75)),
            ).reset_index().rename(columns={variable: "level"})
            grouped.insert(0, "variable", variable)
            rows.append(grouped)
        output["chile_descriptive"] = pd.concat(rows, ignore_index=True, sort=False) if rows else pd.DataFrame()
        if not output["chile_descriptive"].empty:
            output["chile_descriptive"].to_csv(tables_dir / "Chile_associations_descriptive_v230.csv", index=False, encoding="utf-8-sig")
    equador = df_main[df_main.country == "equador"].copy()
    if not equador.empty:
        variables = [c for c in ("year", "trauma_subtype", "facility_sector", "facility_class", "facility_type", "facility_entity", "ethnicity", "residence_area", "transfer_proxy", "discharge_specialty", "facility_linkage_status") if c in equador and equador[c].notna().any()]
        rows = []
        for variable in variables:
            grouped = equador.groupby(variable, dropna=False, observed=True).agg(
                n=("country", "size"),
                deaths=("death_in_hospital", "sum"),
                mortality=("death_in_hospital", "mean"),
                los_median=("los_days", "median"),
                bed_available_median=("bed_total_available", "median") if "bed_total_available" in equador else ("age", lambda s: np.nan),
            ).reset_index().rename(columns={variable: "level"})
            grouped.insert(0, "variable", variable)
            rows.append(grouped)
        output["equador_descriptive"] = pd.concat(rows, ignore_index=True, sort=False) if rows else pd.DataFrame()
        if not output["equador_descriptive"].empty:
            output["equador_descriptive"].to_csv(tables_dir / "Equador_associations_descriptive_v230.csv", index=False, encoding="utf-8-sig")
        capacity_linked = equador[pd.to_numeric(equador.get("facility_capacity_linked", 0), errors="coerce").fillna(0).eq(1)].copy()
        if not capacity_linked.empty:
            capacity_linked["bed_available_quartile"] = pd.qcut(pd.to_numeric(capacity_linked["bed_total_available"], errors="coerce").rank(method="first"), q=4, labels=["Q1", "Q2", "Q3", "Q4"], duplicates="drop")
            cap_table = capacity_linked.groupby("bed_available_quartile", observed=True).agg(n=("country", "size"), mortality=("death_in_hospital", "mean"), los_median=("los_days", "median"), beds_median=("bed_total_available", "median")).reset_index()
            output["equador_capacity"] = cap_table
            cap_table.to_csv(tables_dir / "Equador_capacity_outcomes_v230.csv", index=False, encoding="utf-8-sig")
    if CONFIG.get("run_exploratory_models", True):
        model_rows = []
        predictor_map = {
            "chile": ["insurance_type", "beneficiary_type", "transfer_proxy", "any_surgical_intervention", "discharge_specialty"],
            "equador": ["facility_sector", "facility_class", "facility_type", "facility_entity", "ethnicity", "residence_area", "transfer_proxy", "bed_total_available"],
        }
        for country, predictors in predictor_map.items():
            data = df_main[df_main.country == country].copy()
            for predictor in predictors:
                if predictor in data:
                    model_rows.extend(_v230_fit_exploratory_factor(data, country, predictor, "death_in_hospital"))
            del data
            _v230_gc.collect()
        model_table = pd.DataFrame(model_rows)
        if not model_table.empty:
            model_table["p_fdr_bh"] = _v200_bh_fdr(model_table["p_value"])
            model_table.to_csv(tables_dir / "Exploratory_adjusted_associations_v230.csv", index=False, encoding="utf-8-sig")
        output["exploratory_models"] = model_table
    return output


def run_pipeline_complete_v230(config_arg: _V230Optional[dict] = None, dirs_arg: _V230Optional[dict] = None):
    active_config = CONFIG if config_arg is None else config_arg
    active_dirs = DIRS if dirs_arg is None else dirs_arg
    active_config.setdefault("countries", {}).update({"brasil": True, "mexico": True, "chile": True, "equador": True})
    active_config["pipeline_version"] = TCE_MASTER_VERSION
    start = _v230_time.time()
    _v230_log("info", "▶▶▶ PIPELINE TCE MASTER v2.3.0 INICIADO ◀◀◀")
    source_audit = inspect_latam_sources_v230()
    country_dfs: _V230Dict[str, _V230Optional[pd.DataFrame]] = {}
    country_dfs["brasil"] = globals()["run_brasil_ingestion"](active_config, active_dirs)
    _v230_gc.collect()
    country_dfs["mexico"] = run_mexico_ingestion_v210(active_config, active_dirs)
    _v230_gc.collect()
    country_dfs["chile"] = run_chile_ingestion_v230(active_config, active_dirs)
    _v230_gc.collect()
    country_dfs["equador"] = run_equador_ingestion_v230(active_config, active_dirs)
    _v230_gc.collect()
    coverage = _v210_source_coverage(country_dfs)
    coverage.to_csv(_V230Path(active_dirs["qc"]) / "country_source_coverage_v230.csv", index=False, encoding="utf-8-sig")
    _v210_safe_stage("raw_audit_v230", globals()["run_raw_audit"], country_dfs)
    build_crosswalk_table_v200(active_dirs)
    df_cdm, alerts = harmonize_all_v230(country_dfs)
    df_main, df_surg, df_dc = build_cohorts_v230(df_cdm)
    df_main, hospital_year = add_volume_fields_v210(df_main)
    df_surg = _v210_attach_volume_to_subset(df_surg, df_main)
    df_dc = _v210_attach_volume_to_subset(df_dc, df_main)
    harm = _V230Path(active_dirs["harmonized"])
    df_cdm.to_parquet(harm / "tce_harmonized_cdm.parquet", index=False, compression="snappy")
    df_main.to_parquet(harm / "cohort_main.parquet", index=False, compression="snappy")
    df_surg.to_parquet(harm / "cohort_surgical.parquet", index=False, compression="snappy")
    df_dc.to_parquet(harm / "cohort_dc_cran.parquet", index=False, compression="snappy")
    hospital_year.to_parquet(harm / "hospital_year_v230.parquet", index=False, compression="snappy")
    _v210_safe_stage("legacy_tables_v230", globals()["run_all_tables"], df_main, df_surg, df_dc)
    volume_cohort = df_main[
        df_main["hospital_id"].notna()
        & pd.to_numeric(df_main.get("hospital_volume_eligible", 1), errors="coerce").fillna(0).eq(1)
        & pd.to_numeric(df_main["hospital_volume_year"], errors="coerce").notna()
    ].copy()
    models = {}
    run_models = bool(active_config.get("run_main_analysis", True))
    if run_models and not volume_cohort.empty:
        models = _v210_safe_stage("main_models_v230", globals()["run_main_models_v132"], _v230_native_for_model(volume_cohort)) or {}
    advanced = run_advanced_analysis_v220(df_cdm, df_main, run_models=run_models)
    country_specific = run_country_specific_analyses_v230(df_main) if active_config.get("run_country_specific_analysis", True) else {}
    advanced["country_specific_v230"] = country_specific
    elapsed = round((_v230_time.time() - start) / 60, 2)
    summary = {
        "version": TCE_MASTER_VERSION,
        "elapsed_minutes": elapsed,
        "primary_study_years": PRIMARY_STUDY_YEARS_V230,
        "source_years": active_config.get("source_years"),
        "records_by_country": df_main["country"].value_counts().to_dict(),
        "records_all_available_by_country": df_cdm["country"].value_counts().to_dict(),
        "country_source_coverage": coverage.to_dict(orient="records"),
        "cdm_alerts": alerts,
    }
    support = _V230Path(active_config["base_dir"]) / "10_manuscript_support_v230"
    support.mkdir(parents=True, exist_ok=True)
    (support / "master_run_summary_v230.json").write_text(_v230_json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    _v230_log("info", f"▶▶▶ PIPELINE TCE MASTER v2.3.0 CONCLUÍDO em {elapsed} min ◀◀◀")
    return df_cdm, df_main, df_surg, df_dc, models, advanced


def purge_latam_v230_checkpoints(remove_harmonized: bool = True) -> _V230List[str]:
    removed = []
    for country in ("chile", "equador"):
        folder = _V230Path(DIRS["intermediate"]) / country
        if folder.exists():
            for path in folder.glob("*v230.parquet"):
                path.unlink()
                removed.append(str(path))
    if remove_harmonized:
        for name in (
            "tce_harmonized_cdm.parquet", "tce_harmonized_cdm_v230.parquet",
            "cohort_main.parquet", "cohort_main_all_available_v230.parquet",
            "cohort_surgical.parquet", "cohort_dc_cran.parquet",
            "hospital_year_v230.parquet", "cohort_chile_any_surgery_v230.parquet",
            "cohort_equador_capacity_linked_v230.parquet",
        ):
            path = _V230Path(DIRS["harmonized"]) / name
            if path.exists():
                path.unlink()
                removed.append(str(path))
    _v230_log("info", f"[PURGE-v2.3] {len(removed)} derivados removidos; Brasil e México preservados.")
    return removed


def verify_tce_master_v230() -> _V230Dict[str, _V230Any]:
    status = {
        "version": TCE_MASTER_VERSION,
        "runner": getattr(globals().get("run_pipeline_complete"), "__name__", None),
        "chile_ingestion": getattr(globals().get("run_chile_ingestion"), "__name__", None),
        "equador_ingestion": getattr(globals().get("run_equador_ingestion"), "__name__", None),
        "harmonization": getattr(globals().get("harmonize_all"), "__name__", None),
        "primary_years": PRIMARY_STUDY_YEARS_V230,
        "chile_source_years": CHILE_SOURCE_YEARS_V230,
        "equador_source_years": ECUADOR_SOURCE_YEARS_V230,
    }
    expected = {
        "runner": "run_pipeline_complete_v230",
        "chile_ingestion": "run_chile_ingestion_v230",
        "equador_ingestion": "run_equador_ingestion_v230",
        "harmonization": "harmonize_all_v230",
    }
    bad = {key: (status[key], value) for key, value in expected.items() if status[key] != value}
    if bad:
        raise RuntimeError(f"MASTER v2.3 não está ativo: {bad}")
    print(_v230_json.dumps(status, ensure_ascii=False, indent=2, default=str))
    return status


globals()["CHILE_ALIASES_V230"] = CHILE_ALIASES_V230
globals()["ECUADOR_EGRESOS_ALIASES_V230"] = ECUADOR_EGRESOS_ALIASES_V230
globals()["ECUADOR_CAMAS_ALIASES_V230"] = ECUADOR_CAMAS_ALIASES_V230
globals()["inspect_latam_sources_v230"] = inspect_latam_sources_v230
globals()["run_chile_ingestion"] = run_chile_ingestion_v230
globals()["run_equador_ingestion"] = run_equador_ingestion_v230
globals()["harmonize_all"] = harmonize_all_v230
globals()["build_cohorts"] = build_cohorts_v230
globals()["run_pipeline_complete"] = run_pipeline_complete_v230
globals()["purge_latam_v230_checkpoints"] = purge_latam_v230_checkpoints
globals()["verify_tce_master_v230"] = verify_tce_master_v230
globals()["ACTIVE_TCE_PATCH"] = TCE_MASTER_VERSION
_v230_log("info", "[MASTER] TCE v2.3.0 ativado: manifestos locais exatos, Chile 2015–2025, Equador egresos+camas e CDM expandido.")


def run_latam_ingestion_only_v230(config_arg: _V230Optional[dict] = None, dirs_arg: _V230Optional[dict] = None) -> _V230Dict[str, _V230Optional[pd.DataFrame]]:
    """Inspect and ingest Chile/Ecuador only. Brasil and México are untouched."""
    active_config = CONFIG if config_arg is None else config_arg
    active_dirs = DIRS if dirs_arg is None else dirs_arg
    inspect_latam_sources_v230()
    chile = run_chile_ingestion_v230(active_config, active_dirs)
    _v230_gc.collect()
    equador = run_equador_ingestion_v230(active_config, active_dirs)
    return {"chile": chile, "equador": equador}


def resume_analysis_v230(run_models: bool = True, config_arg: _V230Optional[dict] = None, dirs_arg: _V230Optional[dict] = None):
    """Resume from country checkpoints without re-reading raw CSV/SAV files."""
    active_config = CONFIG if config_arg is None else config_arg
    active_dirs = DIRS if dirs_arg is None else dirs_arg
    active_config["run_main_analysis"] = bool(run_models)
    checkpoint_map = {
        "brasil": _V230Path(active_dirs["intermediate"]) / "brasil" / "brasil_clean.parquet",
        "mexico": _V230Path(active_dirs["intermediate"]) / "mexico" / "mexico_clean.parquet",
        "chile": _V230Path(active_dirs["intermediate"]) / "chile" / "chile_clean_v230.parquet",
        "equador": _V230Path(active_dirs["intermediate"]) / "equador" / "equador_clean_v230.parquet",
    }
    country_dfs = {}
    for country, path in checkpoint_map.items():
        country_dfs[country] = pd.read_parquet(path) if path.exists() else None
        _v230_log("info", f"[RESUME-v2.3] {country}: {path if path.exists() else 'checkpoint ausente'}")
    df_cdm, alerts = harmonize_all_v230(country_dfs)
    df_main, df_surg, df_dc = build_cohorts_v230(df_cdm)
    df_main, hospital_year = add_volume_fields_v210(df_main)
    df_surg = _v210_attach_volume_to_subset(df_surg, df_main)
    df_dc = _v210_attach_volume_to_subset(df_dc, df_main)
    harm = _V230Path(active_dirs["harmonized"])
    df_cdm.to_parquet(harm / "tce_harmonized_cdm.parquet", index=False, compression="snappy")
    df_main.to_parquet(harm / "cohort_main.parquet", index=False, compression="snappy")
    df_surg.to_parquet(harm / "cohort_surgical.parquet", index=False, compression="snappy")
    df_dc.to_parquet(harm / "cohort_dc_cran.parquet", index=False, compression="snappy")
    hospital_year.to_parquet(harm / "hospital_year_v230.parquet", index=False, compression="snappy")
    _v210_safe_stage("legacy_tables_resume_v230", globals()["run_all_tables"], df_main, df_surg, df_dc)
    volume_cohort = df_main[
        df_main["hospital_id"].notna()
        & pd.to_numeric(df_main.get("hospital_volume_eligible", 1), errors="coerce").fillna(0).eq(1)
        & pd.to_numeric(df_main["hospital_volume_year"], errors="coerce").notna()
    ].copy()
    models = {}
    if run_models and not volume_cohort.empty:
        models = _v210_safe_stage("main_models_resume_v230", globals()["run_main_models_v132"], _v230_native_for_model(volume_cohort)) or {}
    advanced = run_advanced_analysis_v220(df_cdm, df_main, run_models=run_models)
    advanced["country_specific_v230"] = run_country_specific_analyses_v230(df_main)
    return df_cdm, df_main, df_surg, df_dc, models, advanced


globals()["run_latam_ingestion_only_v230"] = run_latam_ingestion_only_v230
globals()["resume_analysis_v230"] = resume_analysis_v230
# ============================================================
# TCE MULTINACIONAL — MASTER PATCH v2.4.0
# Corrects real Chile grouped-age files and Ecuador cross-year
# Parquet schema conflicts. Must be executed after v2.3.0.
# ============================================================

from pathlib import Path as _V240Path
import gc as _v240_gc
import hashlib as _v240_hashlib
import json as _v240_json
import re as _v240_re
import time as _v240_time
import unicodedata as _v240_unicodedata
import numpy as np
import pandas as pd

TCE_MASTER_VERSION_V240 = "2.4.0"
PRIMARY_STUDY_YEARS_V240 = list(range(2015, 2024))
CHILE_SOURCE_YEARS_V240 = list(range(2015, 2026))
ECUADOR_SOURCE_YEARS_V240 = list(range(2015, 2020))

if "CONFIG" not in globals() or "DIRS" not in globals():
    raise RuntimeError("Carregue tce_master_v2_3.py antes deste patch, ou use tce_master_v2_4.py completo.")


def _v240_log(level: str, message: str) -> None:
    logger = globals().get("LOG")
    if logger is not None and hasattr(logger, level):
        getattr(logger, level)(message)
    else:
        print(f"[{level.upper()}] {message}")


def _v240_norm(value) -> str:
    text = "" if value is None else str(value)
    text = "".join(
        ch for ch in _v240_unicodedata.normalize("NFKD", text)
        if not _v240_unicodedata.combining(ch)
    )
    return _v240_re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def _v240_string_series(series: pd.Series) -> pd.Series:
    if pd.api.types.is_datetime64_any_dtype(series):
        return pd.to_datetime(series, errors="coerce").dt.strftime("%Y-%m-%d").astype("string")
    out = series.astype("string")
    out = out.replace({"nan": pd.NA, "None": pd.NA, "<NA>": pd.NA, "NaT": pd.NA})
    return out


V240_STRING_COLUMNS = {
    "country", "source", "record_id", "_source_file", "source_data_status",
    "hospital_id", "hospital_id_raw", "hospital_region", "hospital_canton",
    "hospital_parish", "hospital_area", "residence_region", "residence_canton",
    "residence_parish", "residence_area", "residence_health_service",
    "residence_municipality", "facility_class", "facility_type", "facility_entity",
    "facility_sector", "facility_cell_id", "facility_linkage_status", "sex",
    "sex_raw", "ethnicity", "nationality", "dx_main", "dx_secondary",
    "external_cause", "dx_group3", "dx_chapter", "trauma_subtype",
    "procedure_code_raw", "procedure_group_v2", "procedure_description",
    "discharge_specialty", "insurance_type", "beneficiary_type",
    "hospital_system_membership", "age_group_raw", "age_band_common",
    "admission_date", "discharge_date", "_capacity_source_file",
}

V240_INTEGER_COLUMNS = {
    "year", "month", "death_in_hospital", "icu_any", "icu_days",
    "urgent_admission", "stable_hospital_id", "hospital_volume_eligible",
    "lag_volume_eligible", "transfer_proxy", "any_surgical_intervention",
    "principal_surgical_intervention_recorded", "facility_capacity_linked",
    "facility_cell_volume_eligible", "capacity_key_unique", "capacity_key_rows",
    "adult_primary", "adult_sensitivity", "age_exact_available",
    "adult_threshold_exact", "_source_row_number",
}

V240_FLOAT_COLUMNS = {
    "age", "age_lower", "age_upper", "age_midpoint", "los_days",
    "cost_local_currency", "bed_total_available", "bed_total_normal",
    "bed_icu_normal", "bed_emergency_normal", "reported_discharges",
    "reported_deaths", "reported_deaths_lt48", "reported_deaths_ge48",
    "reported_los_sum", "rel_diff_discharges", "rel_diff_deaths",
    "rel_diff_los", "facility_cell_tbi_volume_year", "hospital_volume_year",
    "lag_volume", "log_volume", "log_lag_volume", "volume_z_country_year_v14",
    "lag_volume_z_country_year_v14",
}


def _v240_normalize_schema(df: pd.DataFrame, context: str = "generic") -> pd.DataFrame:
    """Make every cross-year column Arrow-safe without silently coercing clinical values."""
    if df is None:
        return df
    out = df.copy()
    for col in out.columns:
        if col in V240_STRING_COLUMNS:
            out[col] = _v240_string_series(out[col])
        elif col in V240_INTEGER_COLUMNS:
            out[col] = pd.to_numeric(out[col], errors="coerce").round().astype("Int64")
        elif col in V240_FLOAT_COLUMNS:
            out[col] = pd.to_numeric(out[col], errors="coerce").astype("float64")
        elif pd.api.types.is_datetime64_any_dtype(out[col]):
            out[col] = pd.to_datetime(out[col], errors="coerce").dt.strftime("%Y-%m-%d").astype("string")
        elif out[col].dtype == "object":
            non_null = out[col].dropna()
            type_names = {type(value).__name__ for value in non_null.head(10000)}
            if len(type_names) > 1 or any(name in type_names for name in {"str", "bytes", "Timestamp", "date", "datetime"}):
                out[col] = _v240_string_series(out[col])
    return out


def _v240_write_parquet(df: pd.DataFrame, path, context: str = "generic") -> pd.DataFrame:
    path = _V240Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    safe = _v240_normalize_schema(df, context=context)
    safe.to_parquet(path, index=False, engine="pyarrow", compression="snappy")
    return safe


# ----------------------------
# Chile: aliases for real files
# ----------------------------
CHILE_ALIASES_V240 = dict(globals().get("CHILE_ALIASES_V230", {}))
CHILE_ALIASES_V240.update({
    "year": ["ANO_EGRESO", "ANIO_EGRESO", "AÑO_EGRESO", "ANO", "ANIO", "AÑO"],
    "age_group_raw": ["GRUPO_EDAD", "GRUPO_DE_EDAD", "TRAMO_EDAD", "RANGO_EDAD"],
    "age": ["EDAD", "EDAD_ANOS", "EDAD_AÑOS"],
    "sex_raw": ["SEXO"],
    "dx_main": ["DIAG1", "DIAG_1", "DIAGNOSTICO_PRINCIPAL"],
    "external_cause": ["DIAG2", "DIAG_2", "CAUSA_EXTERNA"],
    "los_days": ["DIAS_ESTADA", "DIAS_ESTAD", "DIAS_ESTANCIA"],
    "discharge_condition": ["CONDICION_EGRESO", "COND_EGR", "CONDICION_EGR"],
    "surgery_recorded_raw": ["INTERV_Q", "INTERVENCION_Q", "INTERVENCION_QUIRURGICA"],
    "surgery_description": ["GLOSA_INTERV_Q_PPAL", "GLOSA_INTERVENCION_Q_PPAL"],
    "procedure_description": ["GLOSA_PROCED_PPAL", "PROCED", "PROCEDIMIENTO_PRINCIPAL"],
    "insurance_code": ["PREVISION"],
    "insurance_type": ["GLOSA_PREVISION", "PREVISION_GLOSA", "PREVI"],
    "ethnicity": ["ETNIA"],
    "nationality": ["GLOSA_PAIS_ORIGEN", "PAIS_ORIGEN"],
    "residence_municipality": ["COMUNA_RESIDENCIA", "COMUNA_RES", "COMUNA"],
    "residence_municipality_name": ["GLOSA_COMUNA_RESIDENCIA"],
    "residence_region": ["REGION_RESIDENCIA", "REGION_RES", "REGION"],
    "residence_region_name": ["GLOSA_REGION_RESIDENCIA"],
    "hospital_system_membership": [
        "PERTENENCIA_ESTABLECIMIENTO_SALUD",
        "PERTENENCIA_ESTABLECIMIENTO_SALU",
        "PERTENENCIA_ESTABLECIMIENTO",
    ],
})


def _v240_alias_lookup(columns, aliases, labels=None):
    labels = labels or {}
    normalized = {_v240_norm(c): c for c in columns}
    label_normalized = {_v240_norm(labels.get(c, "")): c for c in columns if labels.get(c)}
    selected = {}
    for canonical, candidates in aliases.items():
        for candidate in [canonical] + list(candidates):
            key = _v240_norm(candidate)
            if key in normalized:
                selected[canonical] = normalized[key]
                break
            if key in label_normalized:
                selected[canonical] = label_normalized[key]
                break
    return selected


def _v240_parse_chile_age_group(series: pd.Series) -> pd.DataFrame:
    raw = series.astype("string").str.strip()
    norm = raw.map(_v240_norm).astype("string")
    lower = pd.Series(np.nan, index=series.index, dtype="float64")
    upper = pd.Series(np.nan, index=series.index, dtype="float64")

    extracted = norm.str.extract(r"^(\d{1,3})_a_(\d{1,3})$")
    valid_range = extracted[0].notna() & extracted[1].notna()
    lower.loc[valid_range] = pd.to_numeric(extracted.loc[valid_range, 0], errors="coerce")
    upper.loc[valid_range] = pd.to_numeric(extracted.loc[valid_range, 1], errors="coerce")

    infant = norm.isin(["menor_de_un_ano", "menor_de_un_ano", "menor_1_ano", "menor_de_1_ano"])
    lower.loc[infant], upper.loc[infant] = 0.0, 0.0

    open_90 = norm.str.match(r"^90_(y|o)_mas$", na=False) | norm.isin(["90_y_mas", "90_o_mas", "90_mas"])
    lower.loc[open_90], upper.loc[open_90] = 90.0, 110.0

    midpoint = (lower + upper) / 2.0
    midpoint.loc[open_90] = 95.0
    adult_primary = lower.ge(20)
    adult_sensitivity = upper.ge(18)

    age_band = pd.Series(pd.NA, index=series.index, dtype="string")
    for lo, hi, label in (
        (20, 29, "20-29"), (30, 39, "30-39"), (40, 49, "40-49"),
        (50, 59, "50-59"), (60, 69, "60-69"), (70, 79, "70-79"),
        (80, 89, "80-89"), (90, 110, "90+"),
    ):
        age_band.loc[lower.eq(lo) & upper.eq(hi)] = label
    # Also support non-decennial groups in future releases.
    age_band.loc[age_band.isna() & lower.ge(20)] = (
        lower.loc[age_band.isna() & lower.ge(20)].round().astype("Int64").astype("string")
        + "-" + upper.loc[age_band.isna() & lower.ge(20)].round().astype("Int64").astype("string")
    )
    age_band.loc[open_90] = "90+"

    return pd.DataFrame({
        "age_group_raw": raw,
        "age_lower": lower,
        "age_upper": upper,
        "age_midpoint": midpoint,
        "age_band_common": age_band,
        "adult_primary": adult_primary.astype("Int64"),
        "adult_sensitivity": adult_sensitivity.astype("Int64"),
    })


def _v240_chile_candidate_info(path: _V240Path, year: int) -> dict:
    info = {
        "year": year, "path": str(path), "size_mb": round(path.stat().st_size / 1024**2, 3),
        "eligible": False, "score": -9999, "reason": "UNPROBED", "n_columns": 0,
        "matched_fields": "",
    }
    try:
        encoding, separator, columns = globals()["_v230_probe_csv"](path)
        lookup = _v240_alias_lookup(columns, CHILE_ALIASES_V240)
        has_age = "age" in lookup or "age_group_raw" in lookup
        has_dx = "dx_main" in lookup
        ncols = len(columns)
        size_mb = info["size_mb"]
        canonical_dir = f"egresos_{year}" in _v240_norm(str(path.parent))
        canonical_name = bool(_v240_re.search(rf"^(egre|egr|egresos).*{year}", _v240_norm(path.stem)))
        aggregate_like = size_mb < 1.0 or ncols < 10
        score = (
            (100 if has_dx else -500) + (80 if has_age else -500)
            + (30 if canonical_dir else 0) + (20 if canonical_name else 0)
            + min(size_mb, 100.0) / 10.0 + ncols
            - (200 if aggregate_like else 0)
        )
        eligible = has_dx and has_age and not aggregate_like
        reason = "ELIGIBLE_ANNUAL_MICRODATA" if eligible else (
            "REJECT_AGGREGATE_OR_SMALL" if aggregate_like else "REJECT_REQUIRED_FIELDS"
        )
        info.update({
            "eligible": eligible, "score": score, "reason": reason,
            "n_columns": ncols, "matched_fields": ",".join(sorted(lookup)),
            "encoding": encoding, "separator": repr(separator),
        })
    except Exception as exc:
        info.update(reason=f"ERROR:{type(exc).__name__}:{exc}")
    return info


def _v240_find_chile_sources(raw_dir: _V240Path, years) -> tuple[dict, pd.DataFrame]:
    by_year = {int(y): [] for y in years}
    audits = []
    for path in _V240Path(raw_dir).rglob("*"):
        if not path.is_file() or path.suffix.lower() not in {".csv", ".txt", ".tsv"}:
            continue
        year = globals()["_v230_year_from_path"](path)
        if year not in by_year:
            continue
        # Only names plausibly representing egresos are probed.
        name = _v240_norm(path.name)
        if "egre" not in name and not name.startswith("egr_"):
            continue
        info = _v240_chile_candidate_info(path, year)
        audits.append(info)
        if info["eligible"]:
            by_year[year].append((info["score"], path))
    selected = {}
    for year, items in by_year.items():
        selected[year] = [p for _, p in sorted(items, key=lambda x: (x[0], x[1].stat().st_size), reverse=True)]
    audit_df = pd.DataFrame(audits)
    return selected, audit_df


def _v240_standardize_chile_chunk(chunk: pd.DataFrame, path: _V240Path, year: int, row_offset: int = 0) -> pd.DataFrame:
    lookup = _v240_alias_lookup(list(chunk.columns), CHILE_ALIASES_V240)
    if "dx_main" not in lookup or ("age" not in lookup and "age_group_raw" not in lookup):
        return pd.DataFrame()
    source_rows = pd.Series(range(row_offset, row_offset + len(chunk)), index=chunk.index, dtype="int64")
    frame = chunk[list(dict.fromkeys(lookup.values()))].rename(columns={v: k for k, v in lookup.items()}).copy()
    frame["dx_main"] = (
        frame["dx_main"].astype("string").str.upper().str.strip()
        .str.replace(".", "", regex=False).str.replace(r"\s+", "", regex=True)
    )
    frame = frame[frame["dx_main"].str.startswith("S06", na=False)].copy()
    if frame.empty:
        return frame

    if "age" in frame and pd.to_numeric(frame["age"], errors="coerce").notna().any():
        exact_age = pd.to_numeric(frame["age"], errors="coerce")
        frame["age"] = exact_age
        frame["age_lower"] = exact_age
        frame["age_upper"] = exact_age
        frame["age_midpoint"] = exact_age
        frame["adult_primary"] = exact_age.ge(int(CONFIG.get("min_age", 18))).astype("Int64")
        frame["adult_sensitivity"] = frame["adult_primary"]
        frame["age_exact_available"] = 1
        frame["adult_threshold_exact"] = 1
        frame["age_band_common"] = pd.cut(
            exact_age,
            bins=[18, 20, 30, 40, 50, 60, 70, 80, 90, np.inf],
            right=False,
            labels=["18-19", "20-29", "30-39", "40-49", "50-59", "60-69", "70-79", "80-89", "90+"],
        ).astype("string")
        frame["age_group_raw"] = frame["age_band_common"]
    else:
        parsed = _v240_parse_chile_age_group(frame["age_group_raw"])
        for col in parsed.columns:
            frame[col] = parsed[col]
        frame["age"] = frame["age_midpoint"]
        frame["age_exact_available"] = 0
        frame["adult_threshold_exact"] = 0

    # Primary Chile adult cohort is conservatively restricted to groups whose lower bound is >=20.
    # The 10-19 stratum cannot separate ages 18-19 from minors and is retained only in QC counts.
    frame = frame[frame["adult_primary"].eq(1)].copy()
    if frame.empty:
        return frame

    frame["year"] = int(year)
    frame["month"] = pd.Series(pd.NA, index=frame.index, dtype="Int64")
    sex = frame.get("sex_raw", pd.Series(pd.NA, index=frame.index)).astype("string").str.upper().str.strip().str.replace(r"\.0$", "", regex=True)
    frame["sex"] = sex.map({"1": "M", "2": "F", "M": "M", "F": "F", "H": "M", "HOMBRE": "M", "MUJER": "F"}).fillna("unknown")
    frame["los_days"] = pd.to_numeric(frame.get("los_days"), errors="coerce").astype("float64") if "los_days" in frame else np.nan
    condition = frame.get("discharge_condition", pd.Series(pd.NA, index=frame.index)).astype("string").str.upper().str.strip().str.replace(r"\.0$", "", regex=True)
    death = pd.Series(pd.NA, index=frame.index, dtype="Int64")
    death.loc[condition.isin(["1", "MEJORADO", "VIVO", "ALTA"])] = 0
    death.loc[condition.isin(["2"]) | condition.str.contains("MUER|FALLEC|DEFUNC", na=False)] = 1
    frame["death_in_hospital"] = death

    # The anonymized open files inspected in 2017/2022/2023 have no ESTAB field.
    hid = frame.get("hospital_id_raw", pd.Series(pd.NA, index=frame.index)).map(globals()["_v230_clean_code"]).astype("string")
    frame["hospital_id"] = hid.map(lambda x: f"CL_{x}" if pd.notna(x) else pd.NA).astype("string")
    frame["stable_hospital_id"] = frame["hospital_id"].notna().astype("Int64")
    frame["hospital_volume_eligible"] = frame["stable_hospital_id"]
    frame["lag_volume_eligible"] = frame["stable_hospital_id"]

    frame["principal_surgical_intervention_recorded"] = pd.Series(pd.NA, index=frame.index, dtype="Int64")
    if "surgery_recorded_raw" in frame:
        surg = frame["surgery_recorded_raw"].astype("string").str.upper().str.strip().str.replace(r"\.0$", "", regex=True)
        frame.loc[surg.isin(["1", "SI", "S", "YES"]), "principal_surgical_intervention_recorded"] = 1
        frame.loc[surg.isin(["2", "NO", "N"]), "principal_surgical_intervention_recorded"] = 0
    if "surgery_description" in frame:
        desc = frame["surgery_description"].astype("string").str.strip()
        has_desc = desc.notna() & desc.ne("")
        frame.loc[has_desc, "principal_surgical_intervention_recorded"] = 1
        frame["surgery_description"] = desc
    frame["any_surgical_intervention"] = frame["principal_surgical_intervention_recorded"]

    frame["country"] = "chile"
    frame["source"] = "DEIS-MINSAL-ANONYMIZED-EGRESOS"
    frame["source_data_status"] = "PRIMARY_2015_2023" if year in PRIMARY_STUDY_YEARS_V240 else "CHILE_EXTENSION_2024_2025"
    frame["_source_file"] = str(path)
    frame["_source_row_number"] = source_rows.loc[frame.index].astype("Int64")
    frame["record_id"] = globals()["_v230_common_record_id"]("chile", path, source_rows.loc[frame.index])
    frame["transfer_proxy"] = pd.Series(pd.NA, index=frame.index, dtype="Int64")

    defaults = {
        "dx_secondary": pd.NA, "procedure_code_raw": pd.NA, "icu_any": pd.NA,
        "icu_days": pd.NA, "urgent_admission": pd.NA, "cost_local_currency": pd.NA,
        "admission_date": pd.NA, "discharge_date": pd.NA, "hospital_region": pd.NA,
        "hospital_canton": pd.NA, "hospital_parish": pd.NA, "hospital_area": pd.NA,
        "residence_health_service": pd.NA, "discharge_specialty": pd.NA,
        "beneficiary_type": pd.NA,
    }
    for col, value in defaults.items():
        if col not in frame:
            frame[col] = value
    return _v240_normalize_schema(frame, context=f"chile_{year}")


def _v240_read_chile_file(path: _V240Path, year: int, chunk_size: int = 150_000):
    encoding, separator, columns = globals()["_v230_probe_csv"](path)
    lookup = _v240_alias_lookup(columns, CHILE_ALIASES_V240)
    if "dx_main" not in lookup or ("age" not in lookup and "age_group_raw" not in lookup):
        raise RuntimeError(f"Chile {year}: DIAG1 e idade/faixa etária não encontrados; colunas={columns[:30]}")
    usecols = list(dict.fromkeys(lookup.values()))
    pieces = []
    offset = 0
    raw_rows = 0
    s06_all = 0
    s06_10_19 = 0
    for chunk in pd.read_csv(
        path, sep=separator, encoding=encoding, encoding_errors="replace", dtype=str,
        usecols=usecols, chunksize=chunk_size, low_memory=True, on_bad_lines="skip",
    ):
        raw_rows += len(chunk)
        dx_col = lookup["dx_main"]
        dx = chunk[dx_col].astype("string").str.upper().str.strip().str.replace(".", "", regex=False).str.replace(r"\s+", "", regex=True)
        s06_mask = dx.str.startswith("S06", na=False)
        s06_all += int(s06_mask.sum())
        if "age_group_raw" in lookup:
            age_group = chunk.loc[s06_mask, lookup["age_group_raw"]].astype("string").str.strip().map(_v240_norm)
            s06_10_19 += int(age_group.eq("10_a_19").sum())
        filtered = _v240_standardize_chile_chunk(chunk, path, year, offset)
        if not filtered.empty:
            pieces.append(filtered)
        offset += len(chunk)
        del chunk, filtered
        _v240_gc.collect()
    out = pd.concat(pieces, ignore_index=True, sort=False) if pieces else pd.DataFrame()
    out = _v240_normalize_schema(out, context=f"chile_{year}_concat")
    qc = {
        "year": year, "file": str(path), "raw_rows": raw_rows,
        "s06_all_ages": s06_all, "s06_adult_primary": len(out),
        "s06_age_10_19_excluded": s06_10_19,
        "age_definition": "lower bound >=20 for grouped-age Chile files",
        "hospital_id_available": int("hospital_id" in out and out["hospital_id"].notna().any()) if not out.empty else 0,
    }
    return out, qc


def inspect_latam_sources_v240():
    chile_sources, chile_audit = _v240_find_chile_sources(_V240Path(DIRS["raw_cl"]), CHILE_SOURCE_YEARS_V240)
    rows = []
    for year in CHILE_SOURCE_YEARS_V240:
        candidates = chile_sources.get(year, [])
        rows.append({
            "country": "chile", "year": year,
            "eligible_patient_files": len(candidates),
            "selected_path": str(candidates[0]) if candidates else "",
        })
    # Reuse SAV metadata discovery for Ecuador, which was successful in v2.3.
    ec_pat = globals()["_v230_find_sources"](_V240Path(DIRS["raw_ec"]), "equador", "PATIENT_EGRESOS", ECUADOR_SOURCE_YEARS_V240)
    ec_cap = globals()["_v230_find_sources"](_V240Path(DIRS["raw_ec"]), "equador", "FACILITY_CAPACITY", ECUADOR_SOURCE_YEARS_V240)
    for year in ECUADOR_SOURCE_YEARS_V240:
        rows.append({
            "country": "equador", "year": year,
            "eligible_patient_files": len(ec_pat.get(year, [])),
            "eligible_capacity_files": len(ec_cap.get(year, [])),
            "selected_path": str(ec_pat.get(year, [""])[0]) if ec_pat.get(year) else "",
        })
    coverage = pd.DataFrame(rows)
    qc_dir = _V240Path(DIRS["qc"])
    qc_dir.mkdir(parents=True, exist_ok=True)
    chile_audit.to_csv(qc_dir / "chile_candidate_audit_v240.csv", index=False, encoding="utf-8-sig")
    coverage.to_csv(qc_dir / "expected_source_coverage_v240.csv", index=False, encoding="utf-8-sig")
    _v240_log("info", "[INSPECT-v2.4] fontes elegíveis:\n" + coverage.to_string(index=False))
    return {"coverage": coverage, "chile_candidate_audit": chile_audit}


def run_chile_ingestion_v240(config=CONFIG, dirs=DIRS):
    if not config.get("countries", {}).get("chile", True):
        return None
    raw_dir = _V240Path(dirs["raw_cl"])
    inter_dir = _V240Path(dirs["intermediate"]) / "chile"
    inter_dir.mkdir(parents=True, exist_ok=True)
    sources, source_audit = _v240_find_chile_sources(raw_dir, CHILE_SOURCE_YEARS_V240)
    source_audit.to_csv(_V240Path(dirs["qc"]) / "chile_candidate_audit_v240.csv", index=False, encoding="utf-8-sig")
    yearly_frames = []
    intake = []
    for year in CHILE_SOURCE_YEARS_V240:
        checkpoint = inter_dir / f"chile_s06_{year}_v240.parquet"
        if checkpoint.exists() and checkpoint.stat().st_size > 1_000:
            frame = _v240_normalize_schema(pd.read_parquet(checkpoint), context=f"chile_checkpoint_{year}")
            yearly_frames.append(frame)
            intake.append({"year": year, "status": "CHECKPOINT", "file": str(checkpoint), "n_adult_s06": len(frame)})
            continue
        candidates = sources.get(year, [])
        if not candidates:
            intake.append({"year": year, "status": "MISSING_ELIGIBLE_ANNUAL_FILE", "file": ""})
            _v240_log("warning", f"[CHILE-v2.4] {year}: base anual individual elegível não encontrada")
            continue
        selected = None
        for path in candidates:
            try:
                frame, qc = _v240_read_chile_file(path, year)
                intake.append({**qc, "status": "READ"})
                if not frame.empty:
                    selected = frame
                    break
            except Exception as exc:
                intake.append({"year": year, "status": f"ERROR:{type(exc).__name__}:{exc}", "file": str(path)})
        if selected is None or selected.empty:
            _v240_log("warning", f"[CHILE-v2.4] {year}: nenhum S06 na faixa adulta conservadora após leitura")
            continue
        selected = _v240_write_parquet(selected, checkpoint, context=f"chile_{year}")
        yearly_frames.append(selected)
        _v240_log("info", f"[CHILE-v2.4] {year}: {len(selected):,} S06 com faixa etária >=20 | {selected['_source_file'].iloc[0]}")
        del selected
        _v240_gc.collect()
    pd.DataFrame(intake).to_csv(_V240Path(dirs["qc"]) / "intake_chile_v240.csv", index=False, encoding="utf-8-sig")
    if not yearly_frames:
        return None
    all_years = _v240_normalize_schema(pd.concat(yearly_frames, ignore_index=True, sort=False), "chile_all")
    _v240_write_parquet(all_years, inter_dir / "chile_clean_all_2015_2025_v240.parquet", "chile_all")
    primary = all_years[pd.to_numeric(all_years["year"], errors="coerce").isin(PRIMARY_STUDY_YEARS_V240)].copy()
    extension = all_years[pd.to_numeric(all_years["year"], errors="coerce").isin([2024, 2025])].copy()
    primary = _v240_write_parquet(primary, inter_dir / "chile_clean_v240.parquet", "chile_primary")
    if not extension.empty:
        _v240_write_parquet(extension, inter_dir / "chile_extension_2024_2025_v240.parquet", "chile_extension")
    return primary


# ----------------------------
# Ecuador: Arrow-safe schemas
# ----------------------------
def _v240_read_ecuador_camas(path: _V240Path, year: int) -> pd.DataFrame:
    frame = globals()["_v230_read_ecuador_camas"](path, year)
    # Codes vary between numeric and string across years; all institutional classifiers are categorical.
    for col in (
        "hospital_region", "hospital_canton", "hospital_parish", "hospital_area",
        "facility_class", "facility_type", "facility_entity", "facility_sector",
        "facility_cell_id", "_capacity_source_file",
    ):
        if col in frame:
            frame[col] = _v240_string_series(frame[col])
    return _v240_normalize_schema(frame, context=f"ecuador_capacity_{year}")


def _v240_normalize_ecuador_patient(df: pd.DataFrame, year: int) -> pd.DataFrame:
    out = _v240_normalize_schema(df, context=f"ecuador_patient_{year}")
    # Explicit cross-year canonical types for columns that triggered Arrow errors.
    for col in (
        "facility_class", "facility_type", "facility_entity", "facility_sector",
        "admission_date", "discharge_date", "hospital_region", "hospital_canton",
        "hospital_parish", "hospital_area", "residence_region", "residence_canton",
        "residence_parish", "residence_area", "facility_cell_id",
    ):
        if col in out:
            out[col] = _v240_string_series(out[col])
    if "year" in out:
        out["year"] = pd.to_numeric(out["year"], errors="coerce").fillna(year).astype("Int64")
    return out


def run_equador_ingestion_v240(config=CONFIG, dirs=DIRS):
    if not config.get("countries", {}).get("equador", True):
        return None
    raw_dir = _V240Path(dirs["raw_ec"])
    inter_dir = _V240Path(dirs["intermediate"]) / "equador"
    inter_dir.mkdir(parents=True, exist_ok=True)
    patient_sources = globals()["_v230_find_sources"](raw_dir, "equador", "PATIENT_EGRESOS", ECUADOR_SOURCE_YEARS_V240)
    capacity_sources = globals()["_v230_find_sources"](raw_dir, "equador", "FACILITY_CAPACITY", ECUADOR_SOURCE_YEARS_V240)
    yearly_frames = []
    capacity_frames = []
    linkage_frames = []
    intake = []

    for year in ECUADOR_SOURCE_YEARS_V240:
        checkpoint = inter_dir / f"equador_s06_{year}_v240.parquet"
        old_checkpoint = inter_dir / f"equador_s06_{year}_v230.parquet"
        patients = None
        patient_path = None
        all_aggregate = pd.DataFrame()

        if checkpoint.exists() and checkpoint.stat().st_size > 1_000:
            patients = _v240_normalize_ecuador_patient(pd.read_parquet(checkpoint), year)
            intake.append({"year": year, "role": "PATIENT_EGRESOS", "status": "CHECKPOINT_V240", "file": str(checkpoint), "n_adult_s06": len(patients)})
        elif old_checkpoint.exists() and old_checkpoint.stat().st_size > 1_000:
            patients = _v240_normalize_ecuador_patient(pd.read_parquet(old_checkpoint), year)
            patients = _v240_write_parquet(patients, checkpoint, context=f"ecuador_migrated_{year}")
            intake.append({"year": year, "role": "PATIENT_EGRESOS", "status": "MIGRATED_V230_CHECKPOINT", "file": str(old_checkpoint), "n_adult_s06": len(patients)})
        else:
            for path in patient_sources.get(year, []):
                try:
                    candidate, aggregate = globals()["_v230_read_ecuador_egresos"](path, year)
                    candidate = _v240_normalize_ecuador_patient(candidate, year)
                    intake.append({"year": year, "role": "PATIENT_EGRESOS", "status": "READ_RAW", "file": str(path), "n_adult_s06": len(candidate)})
                    if not candidate.empty:
                        patients, all_aggregate, patient_path = candidate, aggregate, path
                        break
                except Exception as exc:
                    intake.append({"year": year, "role": "PATIENT_EGRESOS", "status": f"ERROR:{type(exc).__name__}:{exc}", "file": str(path)})

        if patients is None or patients.empty:
            _v240_log("warning", f"[EQUADOR-v2.4] {year}: nenhum adulto S06 após leitura")
            continue

        # Capacity files are small and can be re-read safely to create a consistent combined table.
        capacity = pd.DataFrame()
        for path in capacity_sources.get(year, []):
            try:
                capacity = _v240_read_ecuador_camas(path, year)
                intake.append({"year": year, "role": "FACILITY_CAPACITY", "status": "READ", "file": str(path), "n_rows": len(capacity)})
                if not capacity.empty:
                    capacity_frames.append(capacity)
                    break
            except Exception as exc:
                intake.append({"year": year, "role": "FACILITY_CAPACITY", "status": f"ERROR:{type(exc).__name__}:{exc}", "file": str(path)})

        # Only re-link when records do not already carry the linkage generated by v2.3.
        if not capacity.empty and ("facility_linkage_status" not in patients or patients["facility_linkage_status"].isna().all()):
            if all_aggregate.empty and patient_path is None:
                # Reconstructing all-discharge aggregates would require rereading ~1M records.
                # Preserve patient data and mark linkage as deferred instead of fabricating validation.
                patients["facility_linkage_status"] = "DEFERRED_CHECKPOINT_MIGRATION"
                patients["facility_capacity_linked"] = 0
                patients["facility_cell_volume_eligible"] = 0
            else:
                patients, linkage = globals()["_v230_link_ecuador_capacity"](patients, all_aggregate, capacity, year)
                if not linkage.empty:
                    linkage["year"] = year
                    linkage_frames.append(_v240_normalize_schema(linkage, f"ecuador_linkage_{year}"))
        elif "facility_linkage_status" not in patients:
            patients["facility_linkage_status"] = "CAPACITY_FILE_UNAVAILABLE"
            patients["facility_capacity_linked"] = 0
            patients["facility_cell_volume_eligible"] = 0

        patients = _v240_normalize_ecuador_patient(patients, year)
        patients = _v240_write_parquet(patients, checkpoint, context=f"ecuador_{year}")
        yearly_frames.append(patients)
        _v240_log("info", f"[EQUADOR-v2.4] {year}: {len(patients):,} adultos S06")
        del patients, capacity, all_aggregate
        _v240_gc.collect()

    pd.DataFrame(intake).to_csv(_V240Path(dirs["qc"]) / "intake_equador_v240.csv", index=False, encoding="utf-8-sig")
    if linkage_frames:
        pd.concat(linkage_frames, ignore_index=True, sort=False).to_csv(_V240Path(dirs["qc"]) / "equador_capacity_linkage_qc_v240.csv", index=False, encoding="utf-8-sig")
    if capacity_frames:
        capacity_all = _v240_normalize_schema(pd.concat(capacity_frames, ignore_index=True, sort=False), "ecuador_capacity_all")
        _v240_write_parquet(capacity_all, inter_dir / "equador_capacity_2015_2019_v240.parquet", "ecuador_capacity_all")
    if not yearly_frames:
        return None
    clean = _v240_normalize_schema(pd.concat(yearly_frames, ignore_index=True, sort=False), "ecuador_all")
    clean = _v240_write_parquet(clean, inter_dir / "equador_clean_v240.parquet", "ecuador_all")
    return clean


def harmonize_all_v240(country_dfs):
    normalized = {}
    for country, frame in country_dfs.items():
        normalized[country] = _v240_normalize_schema(frame, context=f"pre_harmonize_{country}") if frame is not None else None
    cdm, alerts = globals()["harmonize_all_v230"](normalized)
    cdm = _v240_normalize_schema(cdm, "cdm_v240")
    _v240_write_parquet(cdm, _V240Path(DIRS["harmonized"]) / "tce_harmonized_cdm_v240.parquet", "cdm_v240")
    _v240_write_parquet(cdm, _V240Path(DIRS["harmonized"]) / "tce_harmonized_cdm.parquet", "cdm_active")
    return cdm, alerts


def _v240_fit_exploratory_factor(data: pd.DataFrame, country: str, predictor: str, outcome: str = "death_in_hospital"):
    age_term = "C(age_band_common)" if country == "chile" and "age_band_common" in data and data["age_band_common"].notna().any() else "bs(age, df=4, degree=3)"
    needed = [outcome, predictor, "age", "age_band_common", "sex", "year", "trauma_subtype"]
    subset = data[[c for c in needed if c in data]].copy()
    if predictor not in subset or subset[predictor].notna().sum() < 100:
        return []
    subset = globals()["_v220_patsy_native"](subset)
    subset[outcome] = pd.to_numeric(subset[outcome], errors="coerce")
    if age_term.startswith("bs"):
        subset["age"] = pd.to_numeric(subset["age"], errors="coerce")
        subset = subset.dropna(subset=[outcome, "age", predictor])
    else:
        subset = subset.dropna(subset=[outcome, "age_band_common", predictor])
    if outcome == "death_in_hospital":
        subset = subset[subset[outcome].isin([0, 1])]
    if len(subset) < 500 or subset[outcome].nunique() < 2:
        return []
    predictor_numeric = pd.api.types.is_numeric_dtype(subset[predictor]) and subset[predictor].nunique() > 8
    term = predictor if predictor_numeric else f"C({predictor})"
    formula = f"{outcome} ~ {term} + {age_term} + C(sex) + C(year) + C(trauma_subtype)"
    try:
        model = smf.glm(
            formula=formula, data=subset,
            family=sm.families.Binomial() if outcome == "death_in_hospital" else sm.families.NegativeBinomial(),
        ).fit(cov_type="HC1", maxiter=100)
    except Exception as exc:
        _v240_log("warning", f"[EXP-v2.4] {country}/{predictor}/{outcome}: {exc}")
        return []
    rows = []
    for term_name in model.params.index:
        if term_name == "Intercept" or predictor not in term_name:
            continue
        beta = float(model.params[term_name])
        se = float(model.bse[term_name])
        rows.append({
            "analysis_role": "EXPLORATORY_ASSOCIATION", "country": country,
            "outcome": outcome, "predictor": predictor, "term": term_name,
            "effect": float(np.exp(beta)), "ci_low": float(np.exp(beta - 1.96 * se)),
            "ci_high": float(np.exp(beta + 1.96 * se)), "p_value": float(model.pvalues[term_name]),
            "n": int(model.nobs),
            "model": "GLM_BINOMIAL_HC1" if outcome == "death_in_hospital" else "GLM_NEGATIVE_BINOMIAL_HC1",
            "age_adjustment": age_term,
        })
    return rows


def run_pipeline_complete_v240(config_arg=None, dirs_arg=None):
    active_config = CONFIG if config_arg is None else config_arg
    active_dirs = DIRS if dirs_arg is None else dirs_arg
    active_config.setdefault("countries", {}).update({"brasil": True, "mexico": True, "chile": True, "equador": True})
    active_config["pipeline_version"] = TCE_MASTER_VERSION_V240
    start = _v240_time.time()
    _v240_log("info", "▶▶▶ PIPELINE TCE MASTER v2.4.0 INICIADO ◀◀◀")
    inspect_latam_sources_v240()
    country_dfs = {
        "brasil": globals()["run_brasil_ingestion"](active_config, active_dirs),
        "mexico": globals()["run_mexico_ingestion_v210"](active_config, active_dirs),
        "chile": run_chile_ingestion_v240(active_config, active_dirs),
        "equador": run_equador_ingestion_v240(active_config, active_dirs),
    }
    coverage = globals()["_v210_source_coverage"](country_dfs)
    coverage.to_csv(_V240Path(active_dirs["qc"]) / "country_source_coverage_v240.csv", index=False, encoding="utf-8-sig")
    globals()["_v210_safe_stage"]("raw_audit_v240", globals()["run_raw_audit"], country_dfs)
    globals()["build_crosswalk_table_v200"](active_dirs)
    df_cdm, alerts = harmonize_all_v240(country_dfs)
    df_main, df_surg, df_dc = globals()["build_cohorts_v230"](df_cdm)
    df_main, hospital_year = globals()["add_volume_fields_v210"](df_main)
    df_surg = globals()["_v210_attach_volume_to_subset"](df_surg, df_main)
    df_dc = globals()["_v210_attach_volume_to_subset"](df_dc, df_main)
    harm = _V240Path(active_dirs["harmonized"])
    df_cdm = _v240_write_parquet(df_cdm, harm / "tce_harmonized_cdm.parquet", "cdm")
    df_main = _v240_write_parquet(df_main, harm / "cohort_main.parquet", "main")
    df_surg = _v240_write_parquet(df_surg, harm / "cohort_surgical.parquet", "surgical")
    df_dc = _v240_write_parquet(df_dc, harm / "cohort_dc_cran.parquet", "dc")
    _v240_write_parquet(hospital_year, harm / "hospital_year_v240.parquet", "hospital_year")
    globals()["_v210_safe_stage"]("legacy_tables_v240", globals()["run_all_tables"], df_main, df_surg, df_dc)

    volume_cohort = df_main[
        df_main["hospital_id"].notna()
        & pd.to_numeric(df_main.get("hospital_volume_eligible", 1), errors="coerce").fillna(0).eq(1)
        & pd.to_numeric(df_main["hospital_volume_year"], errors="coerce").notna()
    ].copy()
    models = {}
    run_models = bool(active_config.get("run_main_analysis", True))
    if run_models and not volume_cohort.empty:
        models = globals()["_v210_safe_stage"](
            "main_models_v240", globals()["run_main_models_v132"],
            globals()["_v220_patsy_native"](volume_cohort),
        ) or {}

    advanced = globals()["_v210_safe_stage"](
        "advanced_analysis_v240", globals()["run_advanced_analysis_v220"],
        df_cdm, df_main, run_models,
    ) or {}
    country_specific = globals()["_v210_safe_stage"](
        "country_specific_v240", globals()["run_country_specific_analyses_v230"], df_main,
    ) or {}
    advanced["country_specific_v240"] = country_specific

    elapsed = round((_v240_time.time() - start) / 60, 2)
    summary = {
        "version": TCE_MASTER_VERSION_V240,
        "elapsed_minutes": elapsed,
        "records_by_country": df_main["country"].value_counts(dropna=False).to_dict(),
        "volume_model_countries": sorted(volume_cohort["country"].dropna().unique().tolist()),
        "chile_age_rule": "Primary Chile cohort excludes 10-19 because ages 18-19 cannot be separated from minors.",
        "chile_hospital_volume": "Unavailable unless an ESTAB field is present in a specific annual file.",
        "cdm_alerts": alerts,
    }
    support = _V240Path(active_config["base_dir"]) / "10_manuscript_support_v240"
    support.mkdir(parents=True, exist_ok=True)
    (support / "master_run_summary_v240.json").write_text(_v240_json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    _v240_log("info", f"▶▶▶ PIPELINE TCE MASTER v2.4.0 CONCLUÍDO em {elapsed} min ◀◀◀")
    return df_cdm, df_main, df_surg, df_dc, models, advanced


def run_latam_ingestion_only_v240(config_arg=None, dirs_arg=None):
    active_config = CONFIG if config_arg is None else config_arg
    active_dirs = DIRS if dirs_arg is None else dirs_arg
    inspect_latam_sources_v240()
    chile = run_chile_ingestion_v240(active_config, active_dirs)
    _v240_gc.collect()
    equador = run_equador_ingestion_v240(active_config, active_dirs)
    return {"chile": chile, "equador": equador}


def resume_analysis_v240(run_models: bool = True, config_arg=None, dirs_arg=None):
    active_config = CONFIG if config_arg is None else config_arg
    active_dirs = DIRS if dirs_arg is None else dirs_arg
    active_config["run_main_analysis"] = bool(run_models)
    checkpoint_map = {
        "brasil": _V240Path(active_dirs["intermediate"]) / "brasil" / "brasil_clean.parquet",
        "mexico": _V240Path(active_dirs["intermediate"]) / "mexico" / "mexico_clean.parquet",
        "chile": _V240Path(active_dirs["intermediate"]) / "chile" / "chile_clean_v240.parquet",
        "equador": _V240Path(active_dirs["intermediate"]) / "equador" / "equador_clean_v240.parquet",
    }
    country_dfs = {}
    for country, path in checkpoint_map.items():
        country_dfs[country] = _v240_normalize_schema(pd.read_parquet(path), f"resume_{country}") if path.exists() else None
        _v240_log("info", f"[RESUME-v2.4] {country}: {path if path.exists() else 'checkpoint ausente'}")
    df_cdm, alerts = harmonize_all_v240(country_dfs)
    df_main, df_surg, df_dc = globals()["build_cohorts_v230"](df_cdm)
    df_main, hospital_year = globals()["add_volume_fields_v210"](df_main)
    df_surg = globals()["_v210_attach_volume_to_subset"](df_surg, df_main)
    df_dc = globals()["_v210_attach_volume_to_subset"](df_dc, df_main)
    harm = _V240Path(active_dirs["harmonized"])
    df_cdm = _v240_write_parquet(df_cdm, harm / "tce_harmonized_cdm.parquet", "resume_cdm")
    df_main = _v240_write_parquet(df_main, harm / "cohort_main.parquet", "resume_main")
    df_surg = _v240_write_parquet(df_surg, harm / "cohort_surgical.parquet", "resume_surgical")
    df_dc = _v240_write_parquet(df_dc, harm / "cohort_dc_cran.parquet", "resume_dc")
    _v240_write_parquet(hospital_year, harm / "hospital_year_v240.parquet", "resume_hy")
    volume_cohort = df_main[
        df_main["hospital_id"].notna()
        & pd.to_numeric(df_main.get("hospital_volume_eligible", 1), errors="coerce").fillna(0).eq(1)
        & pd.to_numeric(df_main["hospital_volume_year"], errors="coerce").notna()
    ].copy()
    models = {}
    if run_models and not volume_cohort.empty:
        models = globals()["_v210_safe_stage"]("main_models_resume_v240", globals()["run_main_models_v132"], globals()["_v220_patsy_native"](volume_cohort)) or {}
    advanced = globals()["_v210_safe_stage"]("advanced_resume_v240", globals()["run_advanced_analysis_v220"], df_cdm, df_main, run_models) or {}
    advanced["country_specific_v240"] = globals()["_v210_safe_stage"]("country_specific_resume_v240", globals()["run_country_specific_analyses_v230"], df_main) or {}
    return df_cdm, df_main, df_surg, df_dc, models, advanced


def purge_latam_v240_checkpoints(remove_harmonized: bool = True):
    removed = []
    for country in ("chile", "equador"):
        folder = _V240Path(DIRS["intermediate"]) / country
        if folder.exists():
            for path in folder.glob("*v240.parquet"):
                path.unlink()
                removed.append(str(path))
    if remove_harmonized:
        for name in (
            "tce_harmonized_cdm.parquet", "tce_harmonized_cdm_v240.parquet",
            "cohort_main.parquet", "cohort_surgical.parquet", "cohort_dc_cran.parquet",
            "hospital_year_v240.parquet",
        ):
            path = _V240Path(DIRS["harmonized"]) / name
            if path.exists():
                path.unlink()
                removed.append(str(path))
    _v240_log("info", f"[PURGE-v2.4] {len(removed)} derivados v2.4 removidos; checkpoints Brasil/México preservados.")
    return removed


def verify_tce_master_v240():
    status = {
        "version": TCE_MASTER_VERSION_V240,
        "runner": getattr(globals().get("run_pipeline_complete"), "__name__", None),
        "chile_ingestion": getattr(globals().get("run_chile_ingestion"), "__name__", None),
        "equador_ingestion": getattr(globals().get("run_equador_ingestion"), "__name__", None),
        "harmonization": getattr(globals().get("harmonize_all"), "__name__", None),
    }
    expected = {
        "runner": "run_pipeline_complete_v240",
        "chile_ingestion": "run_chile_ingestion_v240",
        "equador_ingestion": "run_equador_ingestion_v240",
        "harmonization": "harmonize_all_v240",
    }
    bad = {k: (status[k], v) for k, v in expected.items() if status[k] != v}
    if bad:
        raise RuntimeError(f"MASTER v2.4 não está ativo: {bad}")
    print(_v240_json.dumps(status, ensure_ascii=False, indent=2))
    return status


# Activate v2.4 overrides.
CONFIG["pipeline_version"] = TCE_MASTER_VERSION_V240
CONFIG["primary_study_years"] = PRIMARY_STUDY_YEARS_V240
CONFIG["study_years"] = PRIMARY_STUDY_YEARS_V240
CONFIG.setdefault("countries", {}).update({"brasil": True, "mexico": True, "chile": True, "equador": True})
globals()["CHILE_ALIASES_V240"] = CHILE_ALIASES_V240
globals()["inspect_latam_sources_v240"] = inspect_latam_sources_v240
globals()["run_chile_ingestion"] = run_chile_ingestion_v240
globals()["run_equador_ingestion"] = run_equador_ingestion_v240
globals()["harmonize_all"] = harmonize_all_v240
globals()["_v230_fit_exploratory_factor"] = _v240_fit_exploratory_factor
globals()["run_pipeline_complete"] = run_pipeline_complete_v240
globals()["run_latam_ingestion_only_v240"] = run_latam_ingestion_only_v240
globals()["resume_analysis_v240"] = resume_analysis_v240
globals()["purge_latam_v240_checkpoints"] = purge_latam_v240_checkpoints
globals()["verify_tce_master_v240"] = verify_tce_master_v240
globals()["ACTIVE_TCE_PATCH"] = TCE_MASTER_VERSION_V240
_v240_log("info", "[MASTER] TCE v2.4.0 ativado: Chile por faixa etária real, fontes anuais priorizadas e Equador Arrow-safe.")
# ============================================================
# TCE MASTER v2.5.0 — FINALIZATION / CHILE-2021 / IO HARDENING
# Apply after tce_master_v2_4.py, or use the integrated master.
# ============================================================

from pathlib import Path as _V250Path
import json as _v250_json
import re as _v250_re
import unicodedata as _v250_unicodedata
import numpy as _v250_np
import pandas as _v250_pd

TCE_MASTER_VERSION_V250 = "2.5.0"


def _v250_log(level: str, message: str) -> None:
    logger = globals().get("LOG")
    if logger is not None and hasattr(logger, level):
        getattr(logger, level)(message)
    else:
        print(f"[{level.upper()}] {message}")


def _v250_ensure_dirs(config_arg=None, dirs_arg=None):
    config = globals().get("CONFIG", {}) if config_arg is None else config_arg
    dirs = globals().get("DIRS", {}) if dirs_arg is None else dirs_arg
    base = _V250Path(config.get("base_dir", "/content/drive/MyDrive/Projeto_TCE_Multinacional"))
    defaults = {
        "raw_br": base / "00_raw" / "brasil",
        "raw_mx": base / "00_raw" / "mexico",
        "raw_cl": base / "00_raw" / "chile",
        "raw_ec": base / "00_raw" / "equador",
        "intermediate": base / "01_intermediate",
        "harmonized": base / "02_harmonized",
        "qc": base / "03_qc",
        "tables": base / "04_tables",
        "fig_main": base / "05_figures_main",
        "fig_suppl": base / "06_figures_supplement",
        "models": base / "07_models",
        "logs": base / "08_logs",
        "metadata": base / "09_metadata",
        "manuscript": base / "10_manuscript_support",
    }
    for key, default in defaults.items():
        if key not in dirs:
            dirs[key] = default
        dirs[key] = _V250Path(dirs[key])
        dirs[key].mkdir(parents=True, exist_ok=True)
    globals()["DIRS"] = dirs
    return dirs


def save_csv_xlsx_v250(df, stem, sheet="Sheet1"):
    """Drop-in replacement that always creates the output directory."""
    stem = _V250Path(stem)
    stem.parent.mkdir(parents=True, exist_ok=True)
    if stem.suffix.lower() in {".csv", ".xlsx"}:
        stem = stem.with_suffix("")
    csv_path = stem.with_suffix(".csv")
    xlsx_path = stem.with_suffix(".xlsx")
    frame = df if isinstance(df, _v250_pd.DataFrame) else _v250_pd.DataFrame(df)
    frame.to_csv(csv_path, index=False, encoding="utf-8-sig")
    try:
        with _v250_pd.ExcelWriter(xlsx_path, engine="openpyxl") as writer:
            frame.to_excel(writer, sheet_name=str(sheet)[:31] or "Sheet1", index=False)
    except Exception as exc:
        _v250_log("warning", f"[SAVE-v2.5] XLSX não salvo em {xlsx_path}: {exc}")
    return csv_path, xlsx_path


def save_parquet_v250(df, path, label="dataset"):
    path = _V250Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    safe = _v250_normalize_schema(df, context=f"save_{label}")
    safe.to_parquet(path, index=False, engine="pyarrow", compression="snappy")
    _v250_log("info", f"[SAVE-PQ-v2.5] {label}: {len(safe):,} linhas | {path}")
    return path


def _v250_string_series(series: _v250_pd.Series) -> _v250_pd.Series:
    if _v250_pd.api.types.is_datetime64_any_dtype(series):
        return _v250_pd.to_datetime(series, errors="coerce").dt.strftime("%Y-%m-%d").astype("string")
    out = series.astype("string")
    return out.replace({"nan": _v250_pd.NA, "None": _v250_pd.NA, "<NA>": _v250_pd.NA, "NaT": _v250_pd.NA, "": _v250_pd.NA})


def _v250_nullable_int(series: _v250_pd.Series, col: str = "", context: str = "") -> _v250_pd.Series:
    """Nullable integer conversion without unsafe rounding of non-integral values."""
    numeric = _v250_pd.to_numeric(series, errors="coerce")
    arr = numeric.to_numpy(dtype="float64", na_value=_v250_np.nan)
    finite = _v250_np.isfinite(arr)
    nearest = _v250_np.rint(arr)
    equivalent = _v250_np.isclose(arr, nearest, atol=1e-9, rtol=0.0, equal_nan=True)
    bad = finite & ~equivalent
    if bad.any():
        examples = list(_v250_pd.Series(arr[bad]).dropna().head(5).astype(str))
        _v250_log(
            "warning",
            f"[INT-COERCE-v2.5] {context}/{col}: {int(bad.sum())} valor(es) não inteiros foram mantidos como NA; exemplos={examples}",
        )
        arr[bad] = _v250_np.nan
    nearest[bad] = _v250_np.nan
    return _v250_pd.Series(_v250_pd.array(nearest, dtype="Int64"), index=series.index, name=series.name)


def _v250_float_series(series: _v250_pd.Series) -> _v250_pd.Series:
    return _v250_pd.to_numeric(series, errors="coerce").astype("float64")


# Age is exact for Brazil/Mexico/Ecuador and a midpoint proxy for grouped-age Chile.
# Therefore it must not be stored as nullable integer.
if "CDM_SCHEMA" in globals():
    globals()["CDM_SCHEMA"]["age"] = ("REQUIRED", "float64")

V250_STRING_COLUMNS = set(globals().get("V240_STRING_COLUMNS", set()))
V250_INTEGER_COLUMNS = set(globals().get("V240_INTEGER_COLUMNS", set()))
V250_FLOAT_COLUMNS = set(globals().get("V240_FLOAT_COLUMNS", set())) | {"age"}
V250_INTEGER_COLUMNS.discard("age")


def _v250_normalize_schema(df, context: str = "generic"):
    if df is None:
        return None
    out = df.copy()
    for col in out.columns:
        if col in V250_STRING_COLUMNS:
            out[col] = _v250_string_series(out[col])
        elif col in V250_INTEGER_COLUMNS:
            out[col] = _v250_nullable_int(out[col], col=col, context=context)
        elif col in V250_FLOAT_COLUMNS:
            out[col] = _v250_float_series(out[col])
        elif _v250_pd.api.types.is_datetime64_any_dtype(out[col]):
            out[col] = _v250_pd.to_datetime(out[col], errors="coerce").dt.strftime("%Y-%m-%d").astype("string")
        elif out[col].dtype == "object":
            sample = out[col].dropna().head(10000)
            type_names = {type(value).__name__ for value in sample}
            if len(type_names) > 1 or any(name in type_names for name in {"str", "bytes", "Timestamp", "date", "datetime"}):
                out[col] = _v250_string_series(out[col])
    return out


def _v250_write_parquet(df, path, context: str = "generic"):
    path = _V250Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    safe = _v250_normalize_schema(df, context=context)
    safe.to_parquet(path, index=False, engine="pyarrow", compression="snappy")
    return safe


def finalize_country_df_v250(df, country: str):
    """Canonical country finalizer, replacing the legacy unsafe Int64 cast."""
    _v250_ensure_dirs()
    frame = df.copy()
    plausibility = globals().get("apply_plausibility_filters")
    if callable(plausibility):
        frame = plausibility(frame, country)

    schema = globals().get("CDM_SCHEMA", {})
    for col, (_, dtype) in schema.items():
        if col not in frame.columns:
            frame[col] = _v250_pd.NA
        if dtype == "Int64":
            frame[col] = _v250_nullable_int(frame[col], col=col, context=f"finalize_{country}")
        elif dtype == "float64":
            frame[col] = _v250_float_series(frame[col])
        elif dtype == "str":
            frame[col] = _v250_string_series(frame[col])

    frame["country"] = _v250_string_series(frame["country"]).fillna(country)
    frame = _v250_normalize_schema(frame, context=f"finalize_{country}_extras")
    ordered = [col for col in schema if col in frame.columns]
    extras = [col for col in frame.columns if col not in ordered]
    return frame[ordered + extras]


# ---------- Chile grouped-age parser ----------
_V250_CHILE_AGE_1_BASED = {
    1: (0, 0, "<1"), 2: (1, 4, "1-4"), 3: (5, 9, "5-9"),
    4: (10, 19, "10-19"), 5: (20, 29, "20-29"), 6: (30, 39, "30-39"),
    7: (40, 49, "40-49"), 8: (50, 59, "50-59"), 9: (60, 69, "60-69"),
    10: (70, 79, "70-79"), 11: (80, 89, "80-89"), 12: (90, 110, "90+"),
}
_V250_CHILE_AGE_0_BASED = {key - 1: value for key, value in _V250_CHILE_AGE_1_BASED.items()}


def _v250_ascii(value) -> str:
    if _v250_pd.isna(value):
        return ""
    text = str(value).strip().lower().replace("–", "-").replace("—", "-")
    text = "".join(ch for ch in _v250_unicodedata.normalize("NFKD", text) if not _v250_unicodedata.combining(ch))
    text = _v250_re.sub(r"\s+", " ", text)
    return text.strip()


def _v250_parse_age_label(text: str):
    if not text:
        return (_v250_np.nan, _v250_np.nan, _v250_pd.NA, "UNPARSED_EMPTY")
    original = text
    # Remove an ordinal prefix only when punctuation and a textual label follow it.
    text = _v250_re.sub(r"^\s*\d{1,2}\s*[\.)\]:]\s*(?=[a-z<])", "", text)
    text = text.replace("anos", "").replace("ano", "").strip()
    if _v250_re.search(r"menor|menos|<\s*1", text) and "1" in text:
        return (0.0, 0.0, "<1", "TEXT_INFANT")

    open_match = _v250_re.search(r"(\d{1,3})\s*(?:y|o)?\s*(?:mas|\+|o mas|y mas|y mas de edad)", text)
    if open_match:
        lo = float(open_match.group(1))
        label = "90+" if lo >= 90 else f"{int(lo)}+"
        return (lo, 110.0, label, "TEXT_OPEN")

    range_match = _v250_re.search(r"(\d{1,3})\s*(?:a|al|hasta|[-_/])\s*(\d{1,3})", text)
    if not range_match:
        numbers = _v250_re.findall(r"\d{1,3}", text)
        if len(numbers) >= 2 and any(marker in text for marker in [" a ", "-", "_", "/", "hasta"]):
            range_match = (numbers[0], numbers[1])
    if range_match:
        if isinstance(range_match, tuple):
            lo, hi = map(float, range_match)
        else:
            lo, hi = float(range_match.group(1)), float(range_match.group(2))
        if 0 <= lo <= hi <= 120:
            midpoint = (lo + hi) / 2.0
            if midpoint >= 90:
                label = "90+"
            elif midpoint >= 20:
                decade = int(midpoint // 10) * 10
                label = f"{decade}-{decade + 9}"
            else:
                label = f"{int(lo)}-{int(hi)}"
            return (lo, hi, label, "TEXT_RANGE")

    # Single exact age is accepted only when the value is explicitly age-like, not an ordinal code.
    exact_match = _v250_re.fullmatch(r"(?:edad\s*)?(\d{1,3})(?:\s*anos)?", text)
    if exact_match:
        value = float(exact_match.group(1))
        if 0 <= value <= 120:
            return (value, value, str(int(value)), "TEXT_EXACT_OR_CODE")
    return (_v250_np.nan, _v250_np.nan, _v250_pd.NA, f"UNPARSED:{original[:40]}")


def _v250_parse_chile_age_group(series: _v250_pd.Series) -> _v250_pd.DataFrame:
    raw = series.astype("string").str.strip()
    normalized = raw.map(_v250_ascii)
    nonempty = normalized[normalized.ne("") & normalized.notna()]

    # Some annual releases use only ordinal group codes. Activate a code map only
    # when the whole non-empty field is numeric and contains several distinct groups.
    numeric_codes = _v250_pd.to_numeric(nonempty.str.replace(r"\.0$", "", regex=True), errors="coerce")
    all_numeric = len(nonempty) > 0 and numeric_codes.notna().all()
    unique_codes = sorted(set(numeric_codes.dropna().astype(int).tolist())) if all_numeric else []
    code_map = None
    code_mode = "NONE"
    if unique_codes and 0 in unique_codes and set(unique_codes).issubset(set(_V250_CHILE_AGE_0_BASED)):
        code_map, code_mode = _V250_CHILE_AGE_0_BASED, "ORDINAL_0_TO_11"
    elif unique_codes and set(unique_codes).issubset(set(_V250_CHILE_AGE_1_BASED)):
        code_map, code_mode = _V250_CHILE_AGE_1_BASED, "ORDINAL_1_TO_12"
    unknown_numeric_scheme = bool(all_numeric and code_map is None and unique_codes)

    parsed_rows = []
    for value in normalized:
        if code_map is not None and value:
            try:
                code = int(float(value))
            except Exception:
                code = None
            if code in code_map:
                lo, hi, label = code_map[code]
                parsed_rows.append((float(lo), float(hi), label, code_mode))
                continue
        if unknown_numeric_scheme and value:
            parsed_rows.append((_v250_np.nan, _v250_np.nan, _v250_pd.NA, "UNMAPPED_NUMERIC_GROUP_CODE"))
            continue
        parsed_rows.append(_v250_parse_age_label(value))

    parsed = _v250_pd.DataFrame(parsed_rows, columns=["age_lower", "age_upper", "age_band_common", "age_parse_method"], index=series.index)
    parsed["age_group_raw"] = raw
    parsed["age_lower"] = _v250_pd.to_numeric(parsed["age_lower"], errors="coerce").astype("float64")
    parsed["age_upper"] = _v250_pd.to_numeric(parsed["age_upper"], errors="coerce").astype("float64")
    parsed["age_midpoint"] = (parsed["age_lower"] + parsed["age_upper"]) / 2.0
    parsed.loc[parsed["age_lower"].ge(90), "age_midpoint"] = 95.0
    parsed["adult_primary"] = parsed["age_lower"].ge(20).astype("Int64")
    parsed["adult_sensitivity"] = parsed["age_upper"].ge(18).astype("Int64")
    return parsed[[
        "age_group_raw", "age_lower", "age_upper", "age_midpoint", "age_band_common",
        "adult_primary", "adult_sensitivity", "age_parse_method",
    ]]


def _v250_chile_age_qc(df, year: int, path) -> _v250_pd.DataFrame:
    if df is None or df.empty or "age_group_raw" not in df.columns:
        return _v250_pd.DataFrame()
    parsed = _v250_parse_chile_age_group(df["age_group_raw"])
    out = (
        _v250_pd.DataFrame({"age_group_raw": df["age_group_raw"].astype("string"), "age_parse_method": parsed["age_parse_method"], "adult_primary": parsed["adult_primary"]})
        .groupby(["age_group_raw", "age_parse_method", "adult_primary"], dropna=False)
        .size().rename("n").reset_index()
    )
    out.insert(0, "year", int(year))
    out["source_file"] = str(path)
    return out


# Override the parser called by the v2.4 Chile chunk standardizer.
globals()["_v240_parse_chile_age_group"] = _v250_parse_chile_age_group


def run_chile_ingestion_v250(config=None, dirs=None):
    _v250_ensure_dirs(config, dirs)
    config = globals().get("CONFIG") if config is None else config
    dirs = globals().get("DIRS") if dirs is None else dirs
    # v2.4 reader now uses the v2.5 parser through the global override.
    result = globals()["run_chile_ingestion_v240"](config, dirs)
    # Build explicit parsing QC from yearly checkpoints, including a 2021 diagnostic.
    qc_frames = []
    inter = _V250Path(dirs["intermediate"]) / "chile"
    for year in globals().get("CHILE_SOURCE_YEARS_V240", range(2015, 2026)):
        path = inter / f"chile_s06_{year}_v240.parquet"
        if path.exists():
            try:
                frame = _v250_pd.read_parquet(path, columns=["age_group_raw"])
            except Exception:
                frame = _v250_pd.DataFrame()
            if "age_group_raw" in frame.columns:
                qc_frames.append(_v250_chile_age_qc(frame, year, path))
    if qc_frames:
        _v250_pd.concat(qc_frames, ignore_index=True, sort=False).to_csv(
            _V250Path(dirs["qc"]) / "chile_age_group_parse_qc_v250.csv", index=False, encoding="utf-8-sig"
        )
    return result


def run_equador_ingestion_v250(config=None, dirs=None):
    _v250_ensure_dirs(config, dirs)
    config = globals().get("CONFIG") if config is None else config
    dirs = globals().get("DIRS") if dirs is None else dirs
    return globals()["run_equador_ingestion_v240"](config, dirs)


def harmonize_all_v250(country_dfs):
    _v250_ensure_dirs()
    globals()["CDM_SCHEMA"]["age"] = ("REQUIRED", "float64")
    globals()["finalize_country_df"] = finalize_country_df_v250
    normalized = {
        country: (_v250_normalize_schema(frame, context=f"pre_harmonize_{country}") if frame is not None else None)
        for country, frame in country_dfs.items()
    }
    cdm, alerts = globals()["harmonize_all_v230"](normalized)
    cdm = _v250_normalize_schema(cdm, context="cdm_v250")
    harm = _V250Path(globals()["DIRS"]["harmonized"])
    _v250_write_parquet(cdm, harm / "tce_harmonized_cdm_v250.parquet", "cdm_v250")
    _v250_write_parquet(cdm, harm / "tce_harmonized_cdm.parquet", "cdm_active")
    return cdm, alerts


def _v250_pipeline_tail(df_cdm, run_models: bool, active_config, active_dirs):
    df_main, df_surg, df_dc = globals()["build_cohorts_v230"](df_cdm)
    df_main, hospital_year = globals()["add_volume_fields_v210"](df_main)
    df_surg = globals()["_v210_attach_volume_to_subset"](df_surg, df_main)
    df_dc = globals()["_v210_attach_volume_to_subset"](df_dc, df_main)
    harm = _V250Path(active_dirs["harmonized"])
    df_cdm = _v250_write_parquet(df_cdm, harm / "tce_harmonized_cdm.parquet", "cdm")
    df_main = _v250_write_parquet(df_main, harm / "cohort_main.parquet", "main")
    df_surg = _v250_write_parquet(df_surg, harm / "cohort_surgical.parquet", "surgical")
    df_dc = _v250_write_parquet(df_dc, harm / "cohort_dc_cran.parquet", "dc")
    _v250_write_parquet(hospital_year, harm / "hospital_year_v250.parquet", "hospital_year")

    globals()["_v210_safe_stage"]("legacy_tables_v250", globals()["run_all_tables"], df_main, df_surg, df_dc)
    volume_eligible = _v250_pd.to_numeric(df_main.get("hospital_volume_eligible", 1), errors="coerce").fillna(0).eq(1)
    volume_cohort = df_main[
        df_main["hospital_id"].notna()
        & volume_eligible
        & _v250_pd.to_numeric(df_main["hospital_volume_year"], errors="coerce").notna()
    ].copy()
    models = {}
    if run_models and not volume_cohort.empty:
        models = globals()["_v210_safe_stage"](
            "main_models_v250", globals()["run_main_models_v132"], globals()["_v220_patsy_native"](volume_cohort)
        ) or {}
    advanced = globals()["_v210_safe_stage"](
        "advanced_analysis_v250", globals()["run_advanced_analysis_v220"], df_cdm, df_main, run_models
    ) or {}
    advanced["country_specific_v250"] = globals()["_v210_safe_stage"](
        "country_specific_v250", globals()["run_country_specific_analyses_v230"], df_main
    ) or {}
    return df_cdm, df_main, df_surg, df_dc, models, advanced


def run_pipeline_complete_v250(config_arg=None, dirs_arg=None):
    active_config = globals().get("CONFIG") if config_arg is None else config_arg
    active_dirs = globals().get("DIRS") if dirs_arg is None else dirs_arg
    _v250_ensure_dirs(active_config, active_dirs)
    active_config.setdefault("countries", {}).update({"brasil": True, "mexico": True, "chile": True, "equador": True})
    active_config["pipeline_version"] = TCE_MASTER_VERSION_V250
    _v250_log("info", "▶▶▶ PIPELINE TCE MASTER v2.5.0 INICIADO ◀◀◀")
    globals()["inspect_latam_sources_v240"]()
    country_dfs = {
        "brasil": globals()["run_brasil_ingestion"](active_config, active_dirs),
        "mexico": globals()["run_mexico_ingestion_v210"](active_config, active_dirs),
        "chile": run_chile_ingestion_v250(active_config, active_dirs),
        "equador": run_equador_ingestion_v250(active_config, active_dirs),
    }
    coverage = globals()["_v210_source_coverage"](country_dfs)
    coverage.to_csv(_V250Path(active_dirs["qc"]) / "country_source_coverage_v250.csv", index=False, encoding="utf-8-sig")
    globals()["_v210_safe_stage"]("raw_audit_v250", globals()["run_raw_audit"], country_dfs)
    globals()["build_crosswalk_table_v200"](active_dirs)
    df_cdm, alerts = harmonize_all_v250(country_dfs)
    outputs = _v250_pipeline_tail(df_cdm, bool(active_config.get("run_main_analysis", True)), active_config, active_dirs)
    summary = {
        "version": TCE_MASTER_VERSION_V250,
        "records_by_country": outputs[1]["country"].value_counts(dropna=False).to_dict(),
        "cdm_alerts": alerts,
        "age_policy": {
            "brasil_mexico_equador": "exact age when available",
            "chile": "midpoint retained for storage; all Chile inferential adjustment uses age_band_common",
            "chile_primary_adult": "lower age-group bound >=20; 10-19 excluded",
        },
    }
    support = _V250Path(active_config["base_dir"]) / "10_manuscript_support_v250"
    support.mkdir(parents=True, exist_ok=True)
    (support / "master_run_summary_v250.json").write_text(_v250_json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    _v250_log("info", "▶▶▶ PIPELINE TCE MASTER v2.5.0 CONCLUÍDO ◀◀◀")
    return outputs


def resume_analysis_v250(run_models: bool = True, config_arg=None, dirs_arg=None):
    active_config = globals().get("CONFIG") if config_arg is None else config_arg
    active_dirs = globals().get("DIRS") if dirs_arg is None else dirs_arg
    _v250_ensure_dirs(active_config, active_dirs)
    active_config["run_main_analysis"] = bool(run_models)
    checkpoint_map = {
        "brasil": _V250Path(active_dirs["intermediate"]) / "brasil" / "brasil_clean.parquet",
        "mexico": _V250Path(active_dirs["intermediate"]) / "mexico" / "mexico_clean.parquet",
        "chile": _V250Path(active_dirs["intermediate"]) / "chile" / "chile_clean_v240.parquet",
        "equador": _V250Path(active_dirs["intermediate"]) / "equador" / "equador_clean_v240.parquet",
    }
    country_dfs = {}
    for country, path in checkpoint_map.items():
        country_dfs[country] = _v250_normalize_schema(_v250_pd.read_parquet(path), f"resume_{country}") if path.exists() else None
        _v250_log("info", f"[RESUME-v2.5] {country}: {path if path.exists() else 'checkpoint ausente'}")
    df_cdm, _ = harmonize_all_v250(country_dfs)
    return _v250_pipeline_tail(df_cdm, bool(run_models), active_config, active_dirs)


def diagnose_chile_year_age_groups_v250(year: int = 2021):
    """Inspect the real raw Chile file and show how age groups are parsed before cohort filtering."""
    _v250_ensure_dirs()
    sources, _ = globals()["_v240_find_chile_sources"](
        _V250Path(globals()["DIRS"]["raw_cl"]), globals().get("CHILE_SOURCE_YEARS_V240", range(2015, 2026))
    )
    candidates = sources.get(int(year), [])
    if not candidates:
        raise FileNotFoundError(f"Chile {year}: nenhum CSV anual elegível encontrado")
    path = candidates[0]
    encoding, separator, columns = globals()["_v230_probe_csv"](path)
    lookup = globals()["_v240_alias_lookup"](columns, globals()["CHILE_ALIASES_V240"])
    if "dx_main" not in lookup or "age_group_raw" not in lookup:
        raise RuntimeError(f"Chile {year}: DIAG1/faixa etária não reconhecidos; colunas={columns}")
    counts = {}
    s06_total = 0
    for chunk in _v250_pd.read_csv(
        path, sep=separator, encoding=encoding, encoding_errors="replace", dtype=str,
        usecols=[lookup["dx_main"], lookup["age_group_raw"]], chunksize=200_000,
        low_memory=True, on_bad_lines="skip",
    ):
        dx = chunk[lookup["dx_main"]].astype("string").str.upper().str.replace(".", "", regex=False).str.replace(r"\s+", "", regex=True)
        subset = chunk.loc[dx.str.startswith("S06", na=False), lookup["age_group_raw"]].astype("string").str.strip()
        s06_total += len(subset)
        for value, n in subset.value_counts(dropna=False).items():
            key = "<NA>" if _v250_pd.isna(value) else str(value)
            counts[key] = counts.get(key, 0) + int(n)
    raw_values = _v250_pd.Series(list(counts), dtype="string")
    parsed = _v250_parse_chile_age_group(raw_values)
    report = _v250_pd.DataFrame({"age_group_raw": raw_values, "n_s06": [counts[str(x)] for x in raw_values]})
    for col in ["age_lower", "age_upper", "age_midpoint", "age_band_common", "adult_primary", "adult_sensitivity", "age_parse_method"]:
        report[col] = parsed[col].values
    report.insert(0, "year", int(year))
    report["source_file"] = str(path)
    report["s06_total_year"] = int(s06_total)
    report = report.sort_values(["adult_primary", "age_lower", "age_group_raw"], ascending=[False, True, True], na_position="last")
    out = _V250Path(globals()["DIRS"]["qc"]) / f"chile_age_group_raw_diagnostic_{year}_v250.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    report.to_csv(out, index=False, encoding="utf-8-sig")
    _v250_log("info", f"[CL-AGE-DIAG-v2.5] Chile {year}: {s06_total:,} S06; relatório={out}")
    return report


def refresh_chile_2021_v250():
    """Reprocess only the missing 2021 Chile year, preserving all other country checkpoints."""
    _v250_ensure_dirs()
    inter = _V250Path(globals()["DIRS"]["intermediate"]) / "chile"
    for path in [
        inter / "chile_s06_2021_v240.parquet",
        inter / "chile_clean_v240.parquet",
        inter / "chile_clean_all_2015_2025_v240.parquet",
    ]:
        if path.exists():
            path.unlink()
            _v250_log("info", f"[REFRESH-CL2021-v2.5] removido: {path}")
    return run_chile_ingestion_v250(globals()["CONFIG"], globals()["DIRS"])


def verify_tce_master_v250():
    status = {
        "version": TCE_MASTER_VERSION_V250,
        "runner": getattr(globals().get("run_pipeline_complete"), "__name__", None),
        "resume": getattr(globals().get("resume_analysis_v250"), "__name__", None),
        "finalizer": getattr(globals().get("finalize_country_df"), "__name__", None),
        "harmonization": getattr(globals().get("harmonize_all"), "__name__", None),
        "age_schema": globals().get("CDM_SCHEMA", {}).get("age"),
    }
    expected = {
        "runner": "run_pipeline_complete_v250",
        "finalizer": "finalize_country_df_v250",
        "harmonization": "harmonize_all_v250",
    }
    bad = {key: (status.get(key), value) for key, value in expected.items() if status.get(key) != value}
    if bad:
        raise RuntimeError(f"MASTER v2.5 não está ativo: {bad}")
    print(_v250_json.dumps(status, ensure_ascii=False, indent=2, default=str))
    return status


# Activate overrides.
_v250_ensure_dirs()
globals()["save_csv_xlsx"] = save_csv_xlsx_v250
globals()["save_parquet"] = save_parquet_v250
globals()["_v240_normalize_schema"] = _v250_normalize_schema
globals()["_v240_write_parquet"] = _v250_write_parquet
globals()["finalize_country_df"] = finalize_country_df_v250
globals()["harmonize_all_v250"] = harmonize_all_v250
globals()["harmonize_all"] = harmonize_all_v250
globals()["run_chile_ingestion_v250"] = run_chile_ingestion_v250
globals()["run_chile_ingestion"] = run_chile_ingestion_v250
globals()["run_equador_ingestion_v250"] = run_equador_ingestion_v250
globals()["run_equador_ingestion"] = run_equador_ingestion_v250
globals()["run_pipeline_complete_v250"] = run_pipeline_complete_v250
globals()["run_pipeline_complete"] = run_pipeline_complete_v250
globals()["resume_analysis_v250"] = resume_analysis_v250
globals()["CONFIG"]["pipeline_version"] = TCE_MASTER_VERSION_V250

_v250_log(
    "info",
    "[MASTER] TCE v2.5.0 ativado: idade chilena float/band-safe, Int64 estrito, Chile-2021 flexível e gravações parent-safe.",
)
