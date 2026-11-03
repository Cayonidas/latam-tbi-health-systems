"""Full-precision benchmark comparison; discrepancies are reported, not hidden."""
from pathlib import Path
import argparse
import numpy as np,pandas as pd

def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,default=Path(__file__).resolve().parent.parent);p.add_argument('--old-results-root',type=Path);a=p.parse_args()
    old=a.old_results_root or a.root/'legacy/previous_public_supplement';new=a.root/'results/reconstructed';rows=[]
    specifications=[
        ('artigo_revisado_20261028/resultados','linear','efeitos_fixos_sensibilidades.csv',['country','analysis']),
        ('artigo_revisado_20261028/resultados','linear','logistica_FE.csv',['country']),
        ('artigo_revisado_20261028/resultados','linear','coorte.csv',['country']),
        ('artigo_revisado_20261028/resultados','linear','fluxo.csv',['country']),
        ('artigo_revisado_20261028/resultados','linear','concentracao_anual.csv',['country','year']),
        ('artigo_revisado_20261028/resultados','linear','suporte_exposicao.csv',['country']),
        ('artigo_final_20261028/resultados','robustness','contraste_pareado.csv',['country']),
        ('artigo_final_20261028/resultados','robustness','exclusoes_hospitais.csv',['country','removed_hospital_token']),
        ('artigo_final_20261028/resultados','robustness','codificacao_anual.csv',['country','year']),
        ('artigo_final_20261028/resultados','robustness','codificacao_transicoes.csv',['country']),
        ('artigo_final_20261028/resultados','robustness','carga_concentrada_2023.csv',['country','year','metric']),
        ('artigo_final_20261028/resultados','robustness','logistica_complementar.csv',['country','variant']),
        ('artigo_final_20261028/resultados','robustness','auditoria_permanencia.csv',['country']),
        ('cohort_diagnostics/results','diagnostics','design_information.csv',['country']),
        ('cohort_diagnostics/results','diagnostics','analytic_characteristics.csv',['country','sample']),
        ('cohort_diagnostics/results','diagnostics','cohort_by_year.csv',['country','sample','year'])]
    specifications.extend([
        ('cohort_diagnostics/results','diagnostics','identifier_diagnostics.csv',['country']),
        ('cohort_diagnostics/results','diagnostics','repeated_profiles.csv',['country'])])
    for oldfolder,newfolder,name,keys in specifications:
        leftpath=old/oldfolder/name;rightpath=new/newfolder/name
        if not leftpath.exists() or not rightpath.exists():
            rows.append(dict(table=name,selection='',metric='',status='comparison_file_absent',old_value=np.nan,new_value=np.nan,difference=np.nan,old_file_exists=leftpath.exists(),new_file_exists=rightpath.exists()));continue
        left=pd.read_csv(leftpath);right=pd.read_csv(rightpath)
        if 'country' in left:left=left[left.country.isin(['brasil','mexico'])]
        columns=sorted((set(left.select_dtypes(include='number'))|set(right.select_dtypes(include='number')))-set(keys))
        joined=left.merge(right,on=keys,how='outer',suffixes=('_old','_new'),indicator=True,validate='one_to_one')
        for _,item in joined.iterrows():
            selection='; '.join(f'{k}={item[k]}' for k in keys)
            for col in columns:
                l=item.get(col+'_old',np.nan) if col in right else item.get(col,np.nan)
                r=item.get(col+'_new',np.nan) if col in left else item.get(col,np.nan)
                if pd.isna(l) and pd.isna(r):status='both_missing'
                elif item['_merge']!='both':status=str(item['_merge'])
                elif pd.isna(l) or pd.isna(r):status='one_missing'
                else:status='concordant_1e-8' if np.isclose(l,r,atol=1e-8,rtol=0) else 'changed'
                interpretation='Reconstructed source population and documented harmonisation corrections'
                if item.get('status_new',item.get('status',''))=='not_estimable' and col in ['OR','OR_low','OR_high']:
                    status='not_estimable_new';interpretation='Planned fit retained; quasi-complete separation prevents a finite full maximum-likelihood estimate'
                if name=='repeated_profiles.csv':
                    interpretation='Definition changed: old all retained columns versus explicitly selected common clinical fields; not a duplicate-patient rate'
                    if status=='changed':status='different_profile_definition'
                rows.append(dict(table=name,selection=selection,metric=col,old_value=l,new_value=r,difference=r-l if pd.notna(l) and pd.notna(r) else np.nan,status=status,interpretation=interpretation))
    context_old=pd.read_csv(old/'auditoria_20261028/reanalise/modelos_recalculados.csv')
    context_old=context_old[context_old.analysis.eq('Published model common scale')].copy()
    context_new=pd.read_csv(new/'contextual/mean_centered_logistic.csv')
    for _,right in context_new.iterrows():
        term='between_volume_z' if right.component=='mean_log_volume' else 'within_volume_z'
        left=context_old[context_old.country.eq(right.country)&context_old.term.eq(term)].iloc[0]
        for oc,nc in [('estimate','OR'),('low','low'),('high','high'),('n','n'),('hospitals','hospitals'),('beta','log_OR'),('se','se_log_OR')]:
            lv=float(left[oc]);rv=float(right[nc]);rows.append(dict(table='mean_centered_logistic.csv',selection=f'country={right.country}; component={right.component}',metric=nc,old_value=lv,new_value=rv,difference=rv-lv,status='concordant_1e-8' if np.isclose(lv,rv,atol=1e-8,rtol=0) else 'changed',interpretation='Common doubling scale; contextual logistic model, not hospital fixed effects'))
    result=pd.DataFrame(rows);result.to_csv(a.root/'OLD_VS_NEW_RESULTS.csv',index=False)
    print(result.status.value_counts().to_dict(),flush=True)
if __name__=='__main__':main()
