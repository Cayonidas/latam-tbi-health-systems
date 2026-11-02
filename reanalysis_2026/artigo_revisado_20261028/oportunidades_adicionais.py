from pathlib import Path
import sys,os,json
OUT=Path(__file__).resolve().parent;AUD=OUT.parent/'auditoria_20261028'
sys.path.insert(0,str(AUD/'dependencias'));os.environ['MPLCONFIGDIR']=str(OUT/'cache_matplotlib')
import pandas as pd,numpy as np
import matplotlib;matplotlib.use('Agg')
import matplotlib.pyplot as plt
RES=OUT/'resultados';RES.mkdir(exist_ok=True);DATA=AUD/'base_final'/'analysis_v284_final'/'01_data'
burden=[];chile=[];std=[]
for country in ['brasil','mexico','chile','equador']:
    d=pd.read_parquet(DATA/f'cohort_main_{country}_v280.parquet')
    d['specified']=d.dx_main.astype(str).str.replace('.','',regex=False).str[:4].isin(['S061','S062','S063','S064','S065','S066','S067'])
    for variant in ['All admissions','Specified lesions','Exclude chronic procedure']:
        if variant=='Exclude chronic procedure' and country!='brasil':continue
        e=d[d.specified] if variant=='Specified lesions' else d[d.procedure_group.ne('CHRONIC_SDH_SURGERY')] if variant=='Exclude chronic procedure' else d
        for year,g in e.groupby('year',observed=True):
            older=g.age.ge(70); los=pd.to_numeric(g.los_days,errors='coerce')
            burden.append({'country':country,'variant':variant,'year':int(year),'n':len(g),'age70_n':int(older.sum()),'age70_share':older.mean(),'bed_days':los.sum(),'older_bed_days':los[older].sum(),'older_bed_share':los[older].sum()/los.sum(),'bed_days_capped90':los.clip(0,90).sum(),'older_bed_days_capped90':los[older].clip(0,90).sum(),'older_bed_share_capped90':los[older].clip(0,90).sum()/los.clip(0,90).sum()})
    if country!='chile':continue
    s=d.dx_secondary.astype('string').str.upper().str.replace(r'[^A-Z0-9]','',regex=True).str[:3]
    digits=pd.to_numeric(s.str[1:],errors='coerce');d['fall']=s.str.startswith('W',na=False)&digits.between(0,19)
    d['indeterminate']=s.eq('X59')|(s.str.startswith('Y',na=False)&digits.between(10,34))
    d['samelevel']=s.isin(['W00','W01','W03','W18']);d['unspecified_fall']=s.eq('W19')
    # W03 other fall on same level due to collision with another person; W18 other same level.
    for subset in ['All adults','Age70plus','Specified lesions']:
        e=d[d.age.ge(70)] if subset=='Age70plus' else d[d.specified] if subset=='Specified lesions' else d
        e=e.copy();e['cell']=e.age_band_common.astype(str)+'|'+e.sex.astype(str)
        w=e[e.year.eq(2015)].cell.value_counts(normalize=True)
        for year,g in e.groupby('year',observed=True):
            n=len(g);f=int(g.fall.sum());u=int(g.indeterminate.sum())
            chile.append({'subset':subset,'year':int(year),'n':n,'falls':f,'indeterminate':u,'observed_lower':f/n,'upper_if_all_indeterminate_falls':(f+u)/n,'share_if_half_indeterminate_falls':(f+.5*u)/n,'fall_share_among_nonindeterminate':f/(n-u),'fall_deaths':int(g.loc[g.fall,'death_in_hospital'].sum()),'fall_bed_days':g.loc[g.fall,'los_days'].sum(),'fall_bed_days_capped90':g.loc[g.fall,'los_days'].clip(0,90).sum(),'unspecified_fall_W19':int(g.unspecified_fall.sum()),'selected_samelevel_codes':int(g.samelevel.sum())})
            rates=g.groupby('cell')[['fall','indeterminate']].mean()
            assert set(w.index)<=set(rates.index)
            fstd=float(w@rates.loc[w.index,'fall']);ustd=float(w@rates.loc[w.index,'indeterminate'])
            std.append({'subset':subset,'year':int(year),'standard':'2015 age-band and sex distribution within subset','fall_std':fstd,'indeterminate_std':ustd,'upper_std':fstd+ustd,'half_std':fstd+.5*ustd})
pd.DataFrame(burden).to_csv(RES/'carga_idosos_sensibilidades.csv',index=False,encoding='utf-8-sig')
pd.DataFrame(chile).to_csv(RES/'chile_quedas_limites.csv',index=False,encoding='utf-8-sig')
pd.DataFrame(std).to_csv(RES/'chile_quedas_padronizacao.csv',index=False,encoding='utf-8-sig')
z=pd.DataFrame(chile);a=z[z.subset.eq('Age70plus')]
fig,ax=plt.subplots(figsize=(7,4));ax.fill_between(a.year,100*a.observed_lower,100*a.upper_if_all_indeterminate_falls,color='#d9e9ec',label='Observed falls to falls plus indeterminate causes')
ax.plot(a.year,100*a.observed_lower,color='#166277',label='Observed fall codes');ax.plot(a.year,100*a.share_if_half_indeterminate_falls,color='#9b4c33',ls='--',label='Scenario: 50% of indeterminate codes are falls')
ax.set_ylabel('% of TBI admissions aged 70 or older');ax.set_xlabel('Year');ax.set_ylim(0,100);ax.legend(fontsize=8,frameon=False,loc='lower left');ax.set_title('Chile: coding-sensitive interpretation of falls');fig.tight_layout();fig.savefig(RES/'chile_limites.png',dpi=220)
print(z[z.year.isin([2015,2023])].to_string(index=False));print(pd.DataFrame(std).query('year in [2015,2023]').to_string(index=False))
