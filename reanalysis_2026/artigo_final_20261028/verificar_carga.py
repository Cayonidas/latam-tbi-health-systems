"""Audit stay extremes and cutoff ties without refitting mortality models."""
from fortalecer import load,R,pd,np
rows=[];burden=pd.read_csv(R/'carga_concentrada_2023.csv')
for c in ['brasil','mexico']:
    d=load(c);d=d[d.year.eq(2023)].copy();v=d.groupby('hospital_id').size();cut=int(burden.loc[burden.country.eq(c),'cutoff_volume'].iloc[0]);lo=d.hospital_id.isin(v[v.gt(cut)].index);hi=d.hospital_id.isin(v[v.ge(cut)].index)
    metrics={'Admissions':np.ones(len(d)),'Recorded deaths':d.death_in_hospital.fillna(0).to_numpy(),'Recorded bed-days':d.los_days.to_numpy(),'Bed-days capped at 90':d.los_days.clip(0,90).to_numpy(),'Admissions age 70 or older':d.age.ge(70).to_numpy(),'S061-S067 admissions':d.specified.to_numpy()}
    if c=='brasil':metrics['Coded acute cranial surgery']=d.primary_acute_surgery.eq(1).to_numpy(dtype=float)
    for name,a in metrics.items():
        select=burden.country.eq(c)&burden.metric.eq(name);den=np.sum(a)
        burden.loc[select,'share_excluding_all_cutoff_ties']=100*np.sum(a[lo])/den
        burden.loc[select,'share_including_all_cutoff_ties']=100*np.sum(a[hi])/den
        assert float(burden.loc[select,'top10_share_pct'].iloc[0])>=100*np.sum(a[lo])/den-1e-9
        assert float(burden.loc[select,'top10_share_pct'].iloc[0])<=100*np.sum(a[hi])/den+1e-9
    rows.append({'country':c,'year':2023,'n':len(d),'missing_stay':int(d.los_days.isna().sum()),'negative_stay':int(d.los_days.lt(0).sum()),'max_stay':d.los_days.max(),'admissions_over90':int(d.los_days.gt(90).sum()),'admissions_over365':int(d.los_days.gt(365).sum()),'total_stay_over365':d.loc[d.los_days.gt(365),'los_days'].sum(),'total_recorded_stay':d.los_days.sum(),'capped90_stay':d.los_days.clip(0,90).sum(),'tie_excluded_hospitals':int(v.gt(cut).sum()),'tie_included_hospitals':int(v.ge(cut).sum())})
burden.to_csv(R/'carga_concentrada_2023.csv',index=False);pd.DataFrame(rows).to_csv(R/'auditoria_permanencia.csv',index=False)
print(pd.DataFrame(rows).to_string(index=False))
