"""Build authorisation/discharge cohorts only from independently read sources."""
from pathlib import Path
import argparse,json,time
import numpy as np,pandas as pd
from cohort_rules import numeric,brazil_age,mexico_age,add_subtype,add_procedures
from raw_io import sha256,write_json,utc

UFS='AC AL AM AP BA CE DF ES GO MA MG MS MT PA PB PE PI PR RJ RN RO RR RS SC SE SP TO'.split()

def brazil(root):
    directory=root/'checkpoints/brazil_s06'
    files=[directory/f'RD{uf}{year%100:02d}{month:02d}.parquet' for year in range(2015,2024) for uf in UFS for month in range(1,13)]
    if not all(p.exists() for p in files):raise FileNotFoundError('All 2916 independently harmonised files are required')
    d=pd.concat((pd.read_parquet(p) for p in files),ignore_index=True)
    meta=[json.loads(p.with_suffix('.json').read_text(encoding='utf-8')) for p in files]
    flow=[]
    def step(name,count,unit='RD records'):
        flow.append(dict(country='brasil',stage=name,n=int(count),unit=unit))
    step('All records in 2015-2023 competency files',sum(x['source_records'] for x in meta))
    step('Active DBF records',sum(x['active_records'] for x in meta))
    step('Principal S06 records before AIH aggregation',len(d))
    # Explicitly replicate the historical administrative AIH grouping for the
    # comparison. It does not establish unique people or clinical episodes.
    d['N_AIH']=d.N_AIH.replace('',pd.NA)
    missing=d.N_AIH.isna()
    d.loc[missing,'N_AIH']='MISSING:'+d.loc[missing,'source_file']+':'+d.loc[missing,'source_record_number'].astype(str)
    repeated=d.N_AIH.duplicated(keep=False)
    repeated_summary=d.loc[repeated].groupby('N_AIH').agg(records=('N_AIH','size'),hospitals=('CNES','nunique'),admission_dates=('DT_INTER','nunique'),discharge_dates=('DT_SAIDA','nunique'),processing_years=('ANO_CMPT','nunique'),diagnoses=('DIAG_PRINC','nunique'),age_units=('COD_IDADE','nunique'))
    diagnostics=dict(source_s06_rows=len(d),unique_aih=d.N_AIH.nunique(),missing_aih_records=int(missing.sum()),repeated_aih_rows=int(repeated.sum()),repeated_aih_groups=len(repeated_summary),
                     repeated_authorisations_multiple_hospitals=int(repeated_summary.hospitals.gt(1).sum()),
                     repeated_authorisations_multiple_admission_dates=int(repeated_summary.admission_dates.gt(1).sum()),
                     repeated_authorisations_multiple_discharge_dates=int(repeated_summary.discharge_dates.gt(1).sum()),
                     repeated_authorisations_multiple_processing_years=int(repeated_summary.processing_years.gt(1).sum()),
                     repeated_authorisations_multiple_principal_diagnoses=int(repeated_summary.diagnoses.gt(1).sum()),
                     ident_counts=d.IDENT.value_counts().to_dict(),sequence_counts=d.SEQ_AIH5.value_counts().to_dict(),
                     duplicate_source_rows_excluding_source_coordinates=int(d.drop(columns=['source_file','source_record_number','source_uf','source_competence_year','source_competence_month']).duplicated().sum()))
    for col in ['DIAS_PERM','MORTE','UTI_MES_TO','UTI_INT_TO','VAL_TOT','VAL_UTI']:d[col]=numeric(d[col])
    # Retain the original administrative first-value/max-value grouping.
    # Numeric maxima correct the old character maximum for six length-of-stay
    # authorisations; the comparison is reported separately. No CNES merge is
    # required for the covariates used in these models.
    first=[c for c in d.columns if c not in ['N_AIH','DIAS_PERM','MORTE','UTI_MES_TO','UTI_INT_TO','VAL_TOT','VAL_UTI','PROC_REA','PROC_SOLIC']]
    agg={c:'first' for c in first}
    agg.update({c:'max' for c in ['DIAS_PERM','MORTE','UTI_MES_TO','UTI_INT_TO','VAL_TOT','VAL_UTI']})
    g=d.groupby('N_AIH',sort=True,dropna=False)
    a=g.agg(agg).reset_index()
    for col in ['PROC_REA','PROC_SOLIC']:
        pairs=d[['N_AIH',col]].drop_duplicates().sort_values(['N_AIH',col])
        values=pairs.groupby('N_AIH',sort=True)[col].agg('|'.join)
        a[col]=a.N_AIH.map(values)
    step('Distinct administrative AIH authorisations',len(a),'AIH authorisations')
    out=pd.DataFrame(dict(country='brasil',record_id=a.N_AIH,aih_type=a.IDENT,continuation_sequence=a.SEQ_AIH5,
        hospital_id=('brasil:BR_'+a.CNES.astype('string').str.zfill(7)).where(a.CNES.ne('')),
        year=numeric(a.ANO_CMPT),month=numeric(a.MES_CMPT),age=brazil_age(a.IDADE,a.COD_IDADE),
        age_unit=a.COD_IDADE,age_raw=numeric(a.IDADE),sex=a.SEXO.map({'1':'Male','3':'Female','0':'Other/unknown'}),
        dx_main=a.DIAG_PRINC,death_in_hospital=a.MORTE.where(a.MORTE.isin([0,1])),los_days=a.DIAS_PERM,
        hospital_region=a.MUNIC_MOV.str[:2],residence_region=a.MUNIC_RES.str[:2],
        admission_date=a.DT_INTER,discharge_date=a.DT_SAIDA,
        procedure_code_raw=a.PROC_REA,source_file=a.source_file,source_dataset='DATASUS SIH-SUS RD',
        icu_days_official=a.UTI_MES_TO,intermediate_care_days_official=a.UTI_INT_TO,
        admission_character=a.CAR_INT,cost_local_currency_official=a.VAL_TOT))
    for c in ['DIAG_SECUN']+['DIAGSEC'+str(i) for i in range(1,10)]:out[c.lower()]=a[c]
    out['processing_vs_admission_year_different']=numeric(out.admission_date.str[:4]).ne(out.year)
    diagnostics['invalid_death_codes']=int((a.MORTE.notna()&~a.MORTE.isin([0,1])).sum())
    diagnostics['age_units_after_aggregation']=a.COD_IDADE.value_counts().to_dict()
    diagnostics['centenarian_authorisations_added']=int((out.age_unit.eq('5')&out.age.ge(20)&out.year.between(2015,2023)).sum())
    diagnostics['s06_rows_not_collapsed_by_profile']=len(out)
    underage=(a.COD_IDADE.isin(['1','2','3']) | (a.COD_IDADE.eq('4')&numeric(a.IDADE).lt(20)))
    step('After excluding documented age below 20',int((~underage).sum()),'AIH authorisations')
    out=out[out.age.ge(20)].copy();step('Known age at least 20',len(out),'AIH authorisations')
    out=out[out.year.between(2015,2023)].copy();step('Processing year 2015-2023',len(out),'AIH authorisations')
    out=out[out.hospital_id.notna()].copy();step('Hospital identifier available',len(out),'AIH authorisations')
    out=add_procedures(add_subtype(out));out=out.reset_index(drop=True)
    diagnostics['processing_vs_admission_year_different_eligible']=int(out.processing_vs_admission_year_different.sum())
    diagnostics['eligible_type5_authorisations']=int(out.aih_type.eq('5').sum())
    diagnostics['eligible_authorisations']=len(out)
    write_json(root/'reports/brazil_authorisation_audit.json',diagnostics)
    # Old annual-only age rule is a declared compatibility benchmark, not the new source cohort.
    compat=out[out.age_unit.eq('4')].copy()
    outdir=root/'checkpoints/cohorts_without_exposure';outdir.mkdir(exist_ok=True)
    out.to_parquet(outdir/'cohort_brasil.parquet',index=False)
    compat.to_parquet(root/'checkpoints/brazil_legacy_rule_reconstruction.parquet',index=False)
    pd.DataFrame(flow).to_csv(root/'reports/flow_brasil_before_exposure.csv',index=False)
    print(json.dumps({'country':'brasil',**diagnostics}),flush=True)

def mexico(root):
    status=json.loads((root/'logs/mexico_harmonisation_status.json').read_text(encoding='utf-8'))
    if not status['complete']:raise RuntimeError('Mexico raw source has not been fully read and CRC verified')
    files=[root/'checkpoints/mexico_s06'/name for name in status['checkpoint_hashes']]
    if any(sha256(p)!=status['checkpoint_hashes'][p.name] for p in files):
        raise ValueError('Mexico harmonisation checkpoint changed')
    d=pd.concat((pd.read_parquet(p) for p in files),ignore_index=True)
    flow=[]
    def step(name,count):flow.append(dict(country='mexico',stage=name,n=int(count),unit='discharge records'))
    step('All records in consolidated source 2008-2024',status['source_records'])
    step('Discharge/source years 2015-2023',sum(int(v) for y,v in status['records_by_year'].items() if 2015<=int(y)<=2023))
    step('Principal S06 in 2015-2023',len(d))
    source_s06_count=len(d)
    d['age']=mexico_age(d.edad,d.cveedad)
    under=numeric(d.cveedad).isin([0,1,2]) | (numeric(d.cveedad).eq(3)&numeric(d.edad).lt(20))
    step('After excluding documented age below 20',int((~under).sum()))
    d=d[d.age.ge(20)].copy();step('Known age at least 20',len(d))
    sex=numeric(d.sexo);reason=numeric(d.motegre)
    # Non-death discharge categories 1-4 and 6-7 are documented; 8/9/blank
    # remain missing outcome and are never silently mapped to survival.
    outcome=pd.Series(np.nan,index=d.index);outcome.loc[reason.eq(5)]=1;outcome.loc[reason.isin([1,2,3,4,6,7])]=0
    clues=d.clues.astype('string').str.strip().str.upper().replace('',pd.NA)
    out=pd.DataFrame(dict(country='mexico',record_id=d.id_insp,source_original_id=d.id,
        hospital_id=('mexico:'+clues),year=numeric(d.anio_base),age=d.age,
        age_raw=numeric(d.edad),age_unit=numeric(d.cveedad),
        sex=sex.map({1:'Male',2:'Female',3:'Other/unknown',8:'Other/unknown',9:'Other/unknown'}),
        dx_main=d.afecprin,death_in_hospital=outcome,los_days=numeric(d.dias_esta),
        hospital_region=d.cedocve.astype('string').str.zfill(2),
        admission_date=d.ingre,discharge_date=d.egreso,discharge_reason=reason,
        admission_source=numeric(d.proced),referring_hospital=d.cluesproce,referred_hospital=d.cluesrefer,
        intensive_care_hours=numeric(d.sa_inten),intermediate_care_hours=numeric(d.sa_interm),
        source_file=d.source_file,source_dataset='INSP RIISP SSA 2008-2024',
        procedure_code_raw=pd.NA,procedure_group='UNCLASSIFIED',primary_acute_surgery=0))
    out['institution_code']=clues.str[2:5]
    out=add_subtype(out);out=out[out.hospital_id.notna()].copy();step('Hospital identifier available',len(out))
    # The consolidated CSV stores dates as US month/day/year strings. Extract
    # the explicit four-digit year without treating a leading month as year.
    recorded_discharge_year=numeric(out.discharge_date.str.extract(r'(?:^|/)([12][0-9]{3})(?:$|[ T-])',expand=False))
    # Series identity is fixed by the downloaded SSA catalogue, not guessed by
    # deleting non-SSA CLUES labels (e.g. institution transitions).
    diagnostics=dict(source_s06_rows=source_s06_count,eligible=len(out),duplicate_insp_ids=int(out.record_id.duplicated().sum()),
        institution_counts=out.institution_code.value_counts(dropna=False).to_dict(),
        discharge_reason_counts=reason.value_counts(dropna=False).to_dict(),
        missing_outcome=int(out.death_in_hospital.isna().sum()),
        discharge_year_missing_or_unparseable=int(recorded_discharge_year.isna().sum()),
        discharge_year_disagreement=int((recorded_discharge_year.notna()&recorded_discharge_year.ne(out.year)).sum()),
        hospital_identifier_format_exceptions=int((~clues.str.fullmatch(r'[A-Z]{5}[0-9]{6}',na=False)).sum()))
    write_json(root/'reports/mexico_discharge_audit.json',diagnostics)
    outdir=root/'checkpoints/cohorts_without_exposure';outdir.mkdir(exist_ok=True)
    out.to_parquet(outdir/'cohort_mexico.parquet',index=False)
    pd.DataFrame(flow).to_csv(root/'reports/flow_mexico_before_exposure.csv',index=False)
    print(json.dumps({'country':'mexico',**diagnostics}),flush=True)

def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,default=Path(__file__).resolve().parent.parent);p.add_argument('--country',choices=['brasil','mexico','both'],default='both');p.add_argument('--wait',action='store_true');a=p.parse_args()
    for country in (['brasil','mexico'] if a.country=='both' else [a.country]):
        status=a.root/'logs'/('brazil_harmonisation_status.json' if country=='brasil' else 'mexico_harmonisation_status.json')
        if a.wait:
            while not status.exists() or not json.loads(status.read_text(encoding='utf-8')).get('complete'):time.sleep(5)
        globals()['brazil' if country=='brasil' else country](a.root)
if __name__=='__main__':main()
