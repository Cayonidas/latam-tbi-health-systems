# Plano de auditoria pós-hoc — 1 novembro 2026

Registrado antes da execução de `audit_processed_cohorts.py`. Os resultados anteriores eram conhecidos. Não é um protocolo prospectivo nem um registro público. A pergunta, a amostra e os estimadores principais permanecem os mesmos.

## 1. Perfis repetidos e unidade observacional

- Problema: 7.038 repetições brasileiras podem ser confundidas com duplicatas comprovadas ou passar sem avaliação.
- Pressuposto investigado: igualdade de todas as variáveis retidas não identifica uma pessoa nem um episódio único quando faltam identificadores originais.
- Dados: todas as colunas dos dois Parquet congelados; ano, hospital, desfecho, elegibilidade e tamanho de cada grupo de perfis iguais.
- Lacuna: o relatório anterior informa apenas o excesso de repetições.
- Execução: contar excesso além da primeira linha, linhas pertencentes a grupos repetidos, número/tamanho dos grupos; distribuição anual e por desfecho, fração na amostra principal e concentração hospitalar sem divulgar linhas ou perfis. Reconciliar com o número anterior e verificar quais identificadores faltam. Não excluir nem reponderar registros: sem identificadores, isso assumiria que pessoas distintas são a mesma admissão. Relatar qualquer divergência e não inferir ausência ou presença de fraude/erro.

## 2. População analítica, calendário e integridade

- Problema: a Tabela 1 descreve principalmente a coorte inicial; os modelos usam uma seleção longitudinal. Calendário de processamento e estabilidade de identificadores limitam interpretação.
- Dados: colunas de identificação, diagnóstico, desfecho, idade, sexo, ano, volume, lag e datas disponíveis.
- Execução: recontar por país/ano, completar fluxo sequencial, caracterizar amostra principal, reconstruir volumes e lag contíguo a partir de contagens. Descrever presença anual, região observada e campos de data/linhagem. Verificar domínios binários de desfecho e limites de idade/códigos. Não tratar estabilidade interna como validação do cadastro hospitalar; não recalendarizar sem datas clínicas.
- Interpretação pré-fixada: diferenças entre amostras são seleção descritiva, sem testes de significância ou recuperação da população nacional. Somar admissão administrativa não equivale a contar pacientes únicos.

## 3. Concentração da informação do coeficiente longitudinal

- Problema: muitos hospitais podem não implicar informação igualmente distribuída para o coeficiente de volume; influência por score mistura desfecho e desenho.
- Mecanismo: na regressão linear, resíduo da exposição após projeção sobre os outros regressores e efeitos hospitalares fornece a informação de desenho para sua inclinação.
- Dados: mesma amostra, matriz de spline/sexo/subtipo/ano e identificador hospitalar da análise primária.
- Execução: pelo teorema de Frisch–Waugh–Lovell, residualizar a exposição nos demais regressores após retirar médias hospitalares; reconciliar inclinação e erro-padrão com a implementação existente. Resumir q_g = soma de z_i² no hospital / soma total de z_i²; maior q_g, soma dos cinco maiores e 1/soma(q_g²). Conferir número de hospitais sem variação bruta de exposição e previsões fora de 0–1.
- Interpretação pré-fixada: q_g é participação na soma de quadrados residualizada, não peso causal, influência sobre o desfecho ou graus de liberdade corrigidos. Seu inverso de concentração é diagnóstico descritivo, não novo tamanho amostral ou critério de exclusão. Não alterar intervalos após observar o diagnóstico. Concentrar a discussão caso poucos hospitais contribuam muito; reportar também resultado não preocupante.

## Métodos que não serão acrescentados

Sem novas buscas de limiar, classificação de hospitais, seleção por p-valor, propensity scores sem gravidade, machine learning prognóstico, equivalência, deduplicação presumida ou inferência causal por variável instrumental sem instrumento defensável. A logística já executada testa forma funcional com mudança de amostra e não resolve confusão clínica. Nenhuma dessas extensões repara a ausência de GCS, pupilas, TC, transferências e identificadores de episódio.

Todas as saídas novas serão agregadas, rotuladas como pós-hoc e mantidas mesmo se desfavoráveis. Os arquivos por admissão ficam exclusivamente no estágio local fora da entrega.
