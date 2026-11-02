from pathlib import Path
import argparse, subprocess, sys, shutil
R=Path(__file__).resolve().parent
p=argparse.ArgumentParser();p.add_argument('--core',action='store_true');p.add_argument('--validate',action='store_true');a=p.parse_args()
if not (a.core or a.validate):p.error('Choose --core or --validate')
def run(relative):subprocess.run([sys.executable,str(R/relative)],cwd=R,check=True)
if a.core:
    run('artigo_revisado_20261028/analise_expandida.py')
    run('artigo_revisado_20261028/logistica_efeitos_fixos.py')
    run('artigo_final_20261028/fortalecer.py')
    run('artigo_final_20261028/verificar_carga.py')
    for f in (R/'artigo_revisado_20261028/resultados').glob('*'):
        if f.suffix in ['.csv','.json']:
            name='validacao_etapa_anterior.json' if f.name=='validacao.json' else f.name
            shutil.copy2(f,R/'artigo_final_20261028/resultados'/name)
    run('artigo_final_20261028/figuras.py')
if a.validate:
    run('artigo_revisado_20261028/validar_expandida.py')
    sys.path.insert(0,str(R/'artigo_final_20261028'))
    from fortalecer import validation
    print(validation())
