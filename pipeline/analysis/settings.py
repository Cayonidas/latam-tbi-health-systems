from pathlib import Path
import os
ROOT=Path(os.environ.get('TBI_ROOT',Path(__file__).resolve().parents[2]))
DATA=Path(os.environ.get('TBI_DATA_ROOT',ROOT/'checkpoints/cohorts'))
RESULTS=Path(os.environ.get('TBI_RESULTS_ROOT',ROOT/'results/reconstructed'))
LINEAR=RESULTS/'linear';ROBUST=RESULTS/'robustness';FIGURES=RESULTS/'figures'
for folder in [LINEAR,ROBUST,FIGURES]:folder.mkdir(parents=True,exist_ok=True)
os.environ.setdefault('OPENBLAS_NUM_THREADS','2')
os.environ.setdefault('OMP_NUM_THREADS','2')
os.environ.setdefault('MPLCONFIGDIR',str(ROOT/'cache/matplotlib'))
