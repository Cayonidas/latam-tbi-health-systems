"""Explicit harmonisation rules; no target cohort sizes appear in selection."""
import numpy as np
import pandas as pd

SUBTYPES=dict(zip(['S06'+str(i) for i in range(10)],[
    'CONCUSSION','TRAUMATIC_CEREBRAL_EDEMA','DIFFUSE_BRAIN_INJURY',
    'FOCAL_BRAIN_INJURY','EPIDURAL_HEMORRHAGE','SUBDURAL_HEMORRHAGE',
    'TRAUMATIC_SUBARACHNOID_HEMORRHAGE','INTRACRANIAL_INJURY_WITH_PROLONGED_COMA',
    'OTHER_INTRACRANIAL_INJURY','UNSPECIFIED_INTRACRANIAL_INJURY']))
PROCEDURES={
    '0403010020':('DECOMPRESSIVE_CODED',1),'0403010039':('DECOMPRESSIVE_CODED',1),
    '0403010268':('ACUTE_CRANIAL_SURGERY',1),'0403010276':('ACUTE_CRANIAL_SURGERY',1),
    '0403010284':('ACUTE_CRANIAL_SURGERY',1),'0403010292':('ACUTE_CRANIAL_SURGERY',1),
    '0403010306':('ACUTE_CRANIAL_SURGERY',1),'0403010314':('CHRONIC_SDH_SURGERY',0),
    '0403010349':('ICP_MONITORING_OR_TREPANATION',0),
    '0415020077':('GENERIC_NEUROSURGERY',0),'0415010012':('GENERIC_MULTIPLE_PROCEDURES',0)}

def numeric(s):return pd.to_numeric(s,errors='coerce').astype(float)

def brazil_age(value,unit,include_hundreds=True):
    value=numeric(value);unit=unit.astype('string').str.strip()
    age=value.where(unit.eq('4'))
    if include_hundreds:age=age.mask(unit.eq('5'),100+value)
    # Sub-year units cannot meet the adult threshold and stay separately labelled.
    return age

def mexico_age(value,unit):
    value=numeric(value);unit=numeric(unit)
    return value.where(unit.eq(3)&value.ne(999)&value.ge(0))

def mexico_annual_age(value,unit,year):
    """DGIS annual dictionaries: years=3 through 2019, years=5 from 2020."""
    value=numeric(value);unit=numeric(unit)
    return value.where(unit.eq(3 if int(year)<=2019 else 5)&value.ne(999)&value.ge(0))

def diagnosis(series):return series.astype('string').str.strip().str.upper().str.replace(r'[^A-Z0-9]','',regex=True)

def add_subtype(frame):
    frame['dx_main']=diagnosis(frame.dx_main)
    frame['trauma_subtype']=frame.dx_main.str[:4].map(SUBTYPES).fillna('OTHER_OR_UNSPECIFIED')
    return frame

def add_procedures(frame):
    first=frame.procedure_code_raw.astype('string').str.split('|').str[0]
    code=first.str.replace(r'\.0+$','',regex=True).str.replace(r'\D','',regex=True)
    frame['procedure_code_norm']=code.where(code.ne('')).str.zfill(10)
    frame['procedure_group']=frame.procedure_code_norm.map({k:v[0] for k,v in PROCEDURES.items()}).fillna('UNCLASSIFIED')
    frame['primary_acute_surgery']=frame.procedure_code_norm.map({k:v[1] for k,v in PROCEDURES.items()}).fillna(0).astype(int)
    return frame

def exposure(frame):
    d=frame.drop(columns=['hospital_volume_year','lag_volume','log_lag_volume'],errors='ignore').copy()
    if d.hospital_id.isna().any() or d.year.isna().any():raise ValueError('Hospital/year missing before exposure construction')
    hy=d.groupby(['hospital_id','year'],observed=True).size().rename('hospital_volume_year').reset_index().sort_values(['hospital_id','year'])
    contiguous=hy.groupby('hospital_id').year.diff().eq(1)
    hy['lag_volume']=hy.groupby('hospital_id').hospital_volume_year.shift().where(contiguous)
    hy['log_lag_volume']=np.log1p(hy.lag_volume)
    return d.merge(hy,on=['hospital_id','year'],how='left',validate='many_to_one'),hy

def eligible(d):
    cc=d.dropna(subset=['hospital_id','sex','trauma_subtype','age','year','death_in_hospital','log_lag_volume'])
    ny=cc.groupby('hospital_id').year.nunique()
    return cc[cc.hospital_id.isin(ny[ny.ge(2)].index)].copy()
