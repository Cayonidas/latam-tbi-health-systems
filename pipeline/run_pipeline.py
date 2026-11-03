"""Execute numbered stages with persistent logs and explicit data paths."""
from pathlib import Path
import argparse,subprocess,sys,os,json,time
STAGES=['00_download_sources','01_validate_sources','02_harmonise_brazil','03_harmonise_mexico',
        '04_build_cohorts','05_construct_exposure','06_run_models','07_run_sensitivities',
        '08_generate_outputs','09_validate_publication']

def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,default=Path(__file__).resolve().parent.parent)
    p.add_argument('--stage',default='all',help='all, 00 through 09, or a complete stage name')
    p.add_argument('--from-stage',type=int,default=0);p.add_argument('--to-stage',type=int,default=9)
    p.add_argument('--data-root',type=Path);p.add_argument('--results-root',type=Path)
    p.add_argument('--verify-dgis-annual',action='store_true');p.add_argument('--allow-source-version-change',action='store_true');a=p.parse_args()
    root=a.root.resolve();code=Path(__file__).resolve().parent
    env=os.environ.copy();env['TBI_ROOT']=str(root)
    data=a.data_root or root/'checkpoints/cohorts';results=a.results_root or root/'results/reconstructed'
    env.update(TBI_DATA_ROOT=str(data.resolve()),TBI_RESULTS_ROOT=str(results.resolve()),OPENBLAS_NUM_THREADS='2',OMP_NUM_THREADS='2')
    logs=root/'logs/stages';logs.mkdir(parents=True,exist_ok=True)
    chosen=STAGES[a.from_stage:a.to_stage+1] if a.stage=='all' else [next(s for s in STAGES if s==a.stage or s.startswith(a.stage+'_'))]
    for stage in chosen:
        if stage[:2] in ['00','01','02','03','04','05']:commands=[[str(code/(stage+'.py')),'--root',str(root)]]
        elif stage.startswith('06'):commands=[[str(code/'analysis/linear_models.py')]]
        elif stage.startswith('07'):
            commands=[[str(code/'analysis/logistic_models.py')],[str(code/'analysis/paired_and_workload.py')],
                      [str(code/'analysis/validate_workload.py')],[str(code/'analysis/cohort_diagnostics.py'),'--data-root',str(data),'--output',str(results/'diagnostics')],
                      [str(code/'analysis/mean_centered_logistic.py')]]
        elif stage.startswith('08'):commands=[[str(code/'analysis/generate_figures.py')]]
        else:commands=[[str(code/'test_pipeline.py')],[str(code/'09_validate_publication.py'),'--root',str(root),'--data-root',str(data),'--results-root',str(results)]]
        if stage.startswith('00') and a.verify_dgis_annual:
            commands.extend([[str(code/'00_download_dgis_annual.py'),'--root',str(root)],[str(code/'00_download_source_dictionaries.py'),'--root',str(root)]])
        if stage.startswith('03') and a.verify_dgis_annual:
            commands.extend([[str(code/'03_verify_dgis_annual.py'),'--root',str(root)],[str(code/'compare_mexico_sources.py'),'--root',str(root)]])
        if stage.startswith('05'):commands.append([str(code/'audit_recovered_fields.py'),'--root',str(root)])
        if stage.startswith('01') and a.allow_source_version_change:commands[0].append('--allow-source-version-change')
        started=time.time()
        with (logs/(stage+'.log')).open('a',encoding='utf-8') as log:
            log.write('\nRUN '+time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())+'\n');log.flush()
            for command in commands:
                process=subprocess.Popen([sys.executable,*command],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace',env=env,cwd=root)
                for line in process.stdout:log.write(line);log.flush();print(line,end='',flush=True)
                code_return=process.wait()
                if code_return:raise RuntimeError(f'{stage} failed with exit {code_return}; see {log.name}')
        state=dict(stage=stage,completed=True,seconds=time.time()-started,at_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()))
        (logs/(stage+'.json')).write_text(json.dumps(state,indent=2),encoding='utf-8')
if __name__=='__main__':main()
