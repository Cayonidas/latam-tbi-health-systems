# TCE LATAM — Preflight Repair v2.8.3

## Por que esta correção é necessária

O audit v2.8.2 terminou sem erro de execução, mas revelou três problemas de interpretação/implementação:

1. **México 2015–2017 foi bloqueado por falso negativo.** A tabela usada como referência não continha mortalidade, embora o arquivo bruto possua `MOTEGRE`; o código oficial de óbito é 5. A fonte consolidada 2013–2020 também contém idade exata e unidade etária, ao contrário dos anuais antigos.
2. **Equador 2022–2024 foi marcado como PASS apesar de mortalidade não resolvida.** A v2.8.3 diferencia resultado completo, parcial e apenas contagens.
3. **Chile e Equador não possuem identificador hospitalar público exato validado.** Chaves compostas equatorianas ficam restritas a análise ecológica/capacidade; não podem representar volume hospitalar primário.

## O que o script faz

- reaproveita integralmente `analysis_v282_preflight`;
- lê o consolidado mexicano 2013–2020 **uma única vez**, extraindo somente TCE S06 adulto de 2015–2017;
- usa unidade etária mexicana `5 = anos` e `MOTEGRE 5 = defunción`;
- deixa código de egresso mexicano desconhecido como ausente, nunca como sobrevivente;
- tenta CSV e SAV equatorianos com fallback robusto de encoding;
- usa no Equador `cod_edad 4 = anos` e `con_egrpa 2/3 = óbito`, incluindo labels textuais quando presentes;
- cria perfis dos valores brutos de idade e condição de egresso;
- impede que ano sem mortalidade seja classificado como outcome completo;
- gera interpretação defensável do Chile e do linkage equatoriano.

## Pré-requisitos

A pasta abaixo deve continuar no Google Drive:

```text
/content/drive/MyDrive/Projeto_TCE_Multinacional/analysis_v282_preflight
```

Os arquivos brutos originais de México e Equador também devem permanecer nos caminhos usados pelo audit.

## Como rodar no Colab

Faça upload de `tce_latam_preflight_repair_v283.py` para `/content` e execute:

```python
from google.colab import drive
drive.mount('/content/drive')

%pip install -q pyarrow pyreadstat

%run /content/tce_latam_preflight_repair_v283.py

verify_latam_preflight_repair_v283()

repair = run_latam_preflight_repair_v283(
    base_dir="/content/drive/MyDrive/Projeto_TCE_Multinacional",
    clean_output=True,
    repair_mexico=True,
    repair_ecuador_sources=True,
)

repair
```

## Saída esperada

```text
/content/drive/MyDrive/Projeto_TCE_Multinacional/analysis_v283_preflight_repair/
/content/drive/MyDrive/Projeto_TCE_Multinacional/analysis_v283_preflight_repair.zip
```

Arquivos mais importantes:

```text
01_mexico/Mexico_2015_2017_recovery_v283.csv
01_mexico/Mexico_coding_consensus_v283.csv
03_ecuador/Ecuador_recovery_manifest_v283.csv
03_ecuador/Ecuador_source_attempts_v283.csv
03_ecuador/Ecuador_age_unit_profile_v283.csv
03_ecuador/Ecuador_outcome_profile_v283.csv
03_ecuador/Ecuador_linkage_interpretation_v283.csv
02_chile/Chile_analysis_use_v283.csv
04_summary/Preflight_repair_recommendations_v283.md
```

## Regra de decisão após a execução

- México 2015–2017 deve aparecer como `PASS_STRICT` ou, no máximo, `PASS_WITH_QC_REVIEW` com contagens explicáveis.
- Um ano equatoriano só poderá entrar na análise de mortalidade se aparecer como `PASS_INDIVIDUAL_OUTCOMES`.
- `PASS_COUNTS_ONLY_OUTCOME_UNRESOLVED` permite epidemiologia/contagens, mas não mortalidade.
- Chile não entra em modelo de volume hospitalar.
- Equador não entra em volume hospitalar primário sem identificador exato; linkage composto é apenas sensibilidade ecológica/capacidade.

**Não rode ainda o master analítico final.** Primeiro revise e envie `analysis_v283_preflight_repair.zip`.
