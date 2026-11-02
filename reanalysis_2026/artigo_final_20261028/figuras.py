from pathlib import Path
import sys,os
ROOT=Path(__file__).resolve().parent;PREV=ROOT.parent/'artigo_revisado_20261028';AUD=ROOT.parent/'auditoria_20261028'
sys.path.insert(0,str(AUD/'dependencias'));os.environ['MPLCONFIGDIR']=str(ROOT/'cache_matplotlib')
import pandas as pd,numpy as np
import matplotlib;matplotlib.use('Agg')
import matplotlib.pyplot as plt
R=ROOT/'resultados';F=ROOT/'figuras';F.mkdir(exist_ok=True)
z=pd.read_csv(PREV/'resultados'/'efeitos_fixos_sensibilidades.csv');contrast=pd.read_csv(R/'contraste_pareado.csv');burden=pd.read_csv(R/'carga_concentrada_2023.csv')
plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10,'axes.spines.top':False,'axes.spines.right':False,'pdf.fonttype':42,'ps.fonttype':42})
def save(fig,name):
    fig.savefig(F/(name+'.png'),dpi=300,bbox_inches='tight');fig.savefig(F/(name+'.pdf'),bbox_inches='tight');plt.close(fig)
fig,axs=plt.subplots(1,2,figsize=(8.2,3.8),gridspec_kw={'width_ratios':[1.35,1]},layout='constrained')
for j,(c,lab) in enumerate([('brasil','Brazil'),('mexico','Mexico')]):
    for offset,spec,color in [(.13,'Conventional LPM same sample','#a55933'),(-.13,'Principal hospital and year FE','#14647b')]:
        a=z[(z.country==c)&(z.analysis==spec)].iloc[0];yy=1-j+offset
        axs[0].errorbar(a.estimate_pp,yy,xerr=[[a.estimate_pp-a.low_pp],[a.high_pp-a.estimate_pp]],fmt='o',color=color,capsize=4,label=('Conventional' if offset>0 else 'Hospital fixed effects') if j==0 else None)
    a=contrast[contrast.country==c].iloc[0]
    axs[1].errorbar(a.difference_pp,1-j,xerr=[[a.difference_pp-a.difference_low],[a.difference_high-a.difference_pp]],fmt='o',color='#14647b',capsize=4)
for ax in axs:
    ax.axvline(0,color='#777',lw=.8,ls='--');ax.set_yticks([1,0],['Brazil','Mexico']);ax.set_ylim(-.65,1.45);ax.set_xlabel('Percentage points\nper doubling of V + 1')
axs[0].set_title('A  Associations on identical samples',loc='left',fontsize=10);axs[1].set_title('B  Conventional minus\nhospital fixed effects',loc='left',fontsize=10);axs[0].legend(frameon=False,fontsize=9,loc='lower right')
save(fig,'Figure_1_associations')
fig,ax=plt.subplots(figsize=(8.2,4.6),layout='constrained')
metrics=['Admissions','Recorded deaths','Recorded bed-days','Bed-days capped at 90','Admissions age 70 or older','S061-S067 admissions','Coded acute cranial surgery']
labels=['All admissions','Recorded deaths','Recorded stay sum','Stay sum capped at 90 days','Admissions aged 70+','Admissions with S06.1-S06.7','Admissions with coded acute surgery']
for c,lab,color,offset,marker in [('brasil','Brazil','#14647b',-.13,'o'),('mexico','Mexico','#a55933',.13,'s')]:
    b=burden[burden.country==c].set_index('metric');mm=[m for m in metrics if m in b.index];vals=b.loc[mm,'top10_share_pct'].to_numpy();ys=np.arange(len(mm))
    ax.scatter(vals,ys+offset,color=color,s=30,marker=marker,label=f'{lab}: {int(b.iloc[0].top10_hospitals)} of {int(b.iloc[0].all_hospitals):,} hospitals')
    for yy,v in zip(ys,vals):ax.text(v+1.3,yy+offset,f'{v:.1f}',va='center',fontsize=9,color=color)
ax.set_yticks(np.arange(len(metrics)),labels,fontsize=9);ax.invert_yaxis();ax.set_xlim(0,100);ax.set_xlabel('Share in highest-volume 10% of hospitals (%)');ax.legend(frameon=False,fontsize=9,loc='lower left');ax.set_ylim(7.5,-.5);ax.xaxis.grid(True,alpha=.2);ax.set_axisbelow(True)
save(fig,'Figure_2_burden')
co=pd.read_csv(R/'codificacao_anual.csv');fig,axs=plt.subplots(1,2,figsize=(8.2,3.4),layout='constrained')
for ax,(c,lab) in zip(axs,[('brasil','Brazil'),('mexico','Mexico')]):
    a=co[co.country==c];ax.plot(a.year,a.S069_pct,'o-',color='#14647b',label='Admission-weighted proportion');ax.plot(a.year,a.hospital_median_S069_pct_n10,'s--',color='#a55933',label='Median hospital proportion');ax.set_title(lab,loc='left');ax.set_ylim(0,100);ax.set_ylabel('S06.9 (%)');ax.set_xticks([2015,2017,2019,2021,2023]);ax.legend(fontsize=9,frameon=False)
save(fig,'Figure_S1_coding')
print('Figures saved')
