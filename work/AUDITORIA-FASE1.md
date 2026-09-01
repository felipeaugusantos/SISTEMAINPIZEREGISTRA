# Auditoria Fase 1 — Motor de decisão de registrabilidade

**Data**: 2026-08-31 · **Branch**: main · **Commit-base**: `62a364c` (após Frente 1 + Frente 2 desta sessão)
**Metodologia**: leitura de código com citação de file:line, consultas SQL de leitura contra o banco de produção (somente leitura, sem alteração de dados), e um teste de ablação/retrain isolado (não persistido, não promovido). Nenhuma alteração de produção foi feita durante esta auditoria.

---

## 1. Política atual de decisão — mapeamento completo do veredito

**Função central**: `analisar_registrabilidade()` em [`app/trademarks/agent.py:55-135`](app/trademarks/agent.py:55). Retorna `decisao ∈ {cenario_favoravel, cenario_desfavoravel, cenario_intermediario, dados_insuficientes}`.

### Caminho até as 4 superfícies

| Superfície | Fonte dos dados | Recalcula ou lê snapshot? |
|---|---|---|
| API interna (`obter_central_analise`, `app/api/analises.py:260-404`) | `analise_para_exibicao()` → lê `VersaoRelatorioMarca.payload["analise_consolidada"]` salvo | Lê snapshot (recalcula só se snapshot for legado) |
| Tela interna (`admin-analise.js::renderUnified`) | mesma API acima | Lê snapshot |
| PDF interno ("relatório completo", `app/api/leads.py:278-351`) | busca a versão mais recente e chama `analise_para_exibicao` de novo | Lê snapshot (nova query) |
| **PDF/JSON público do cliente** (`GET /relatorios/{id}`, `app/api/pesquisas.py:229-247,561-593`) | `ResumoPublicoMarcaResponse` / `versao.payload` **bruto**, sem passar por `analise_para_exibicao` | **Não usa `analise_consolidada` — usa os sinais antigos** |

**Achado crítico (ver item 8, severidade CRÍTICA)**: o endpoint público (o que o cliente final realmente vê) nunca chama `analise_para_exibicao`/`construir_analise_consolidada`. `ResumoPublicoMarcaResponse` (`app/schemas.py:460-478`) expõe apenas `conclusao` (triagem de `app/trademarks/relevance.py`) e `prognostico_registrabilidade` (de `app/trademarks/registrability.py`) — os **4 sinais paralelos antigos que a Frente 2 desta sessão deveria ter substituído continuam sendo exatamente o que o cliente vê**. Confirmado em `app/web/static/relatorio.js:57-74`, que renderiza `conclusao.titulo` e `prognostico.veredito` (mapeado para "Favorável"/"Atenção"/"Desfavorável") diretamente na página pública `/relatorios/{id}`.

### Existe um segundo caminho de decisão, paralelo e não sincronizado

`ExecucaoAgenteRegistrabilidade` (tabela de auditoria, `app/models.py:2092-2125`) é escrita por `registrar_execucao_agente()` (`agent.py:143-229`), que chama `analisar_registrabilidade()` **de novo, independentemente**, passando a `previsao` (estimativa estatística) **sem** o gate de elegibilidade mais estrito (`modelo_status == "ACTIVE"` e `cobertura_entrada >= 0.60`) que `construir_analise_consolidada()` aplica (`consolidated.py:170-181`). Isso significa que a `decisao` gravada na tabela de auditoria pode divergir da `decisao` mostrada na tela/relatório, para o mesmo snapshot, quando o modelo estiver em SHADOW/VALIDATION mas ainda assim gerar uma probabilidade.

### Sem trava de versão entre tela e PDF

`downloadFullReport()` no admin (`admin-analise.js`) não envia número de versão ao gerar o PDF — o endpoint busca a versão mais recente na hora, de novo. Se outra ação (ex.: alguém salvando dados complementares) criar uma nova versão entre o carregamento da tela e o clique em "gerar PDF", o PDF pode refletir uma versão diferente da que está na tela, sem aviso.

---

## 2. Onde "Atenção" ainda pode chegar ao cliente

Confirmado, com evidência direta:

1. **`conclusao.titulo`** (relevance.py) — ex.: *"Foram localizadas ocorrências que merecem atenção"* — renderizado literalmente em `relatorio.js:57-60` na página pública, sem qualquer relação com o sistema de 3 veredicts.
2. **`prognostico_registrabilidade.veredito == "atencao"`** (registrability.py:242-249) — renderizado em `relatorio.js:61-74` como **"Leitura técnica: Atenção"**, numa caixa colorida, como se fosse a classificação oficial do registro. Este é o único veredito de registrabilidade que a página pública mostra hoje.
3. `analise_consolidada.titulo == "Pontos de atenção identificados"` (consolidated.py, cenário intermediário) — esse é benigno: só aparece como subtítulo sob o veredito "Inconclusiva" já unificado, nas telas/PDFs internos que usam `analise_consolidada`. Não é um 4º veredito.

**Conclusão do item 2**: os pontos (1) e (2) são exatamente o problema que o prompt pediu para eliminar, e afetam a **superfície mais importante — o que o cliente pagante vê**, não uma tela interna.

---

## 3. Mensagens duplicadas

`apresentacao_analise()` (consolidated.py:75-141) tem lógica de dedup por regex contra as strings de `agent.py:78-83`. Testado por comparação direta de string: **funciona corretamente** para "N possível(is) impedimento(s)" e "N ponto(s) de atenção" — não duplicam.

**Não filtrado** (achado médio): a contagem *"N critério(s) ainda não analisado(s)"* não tem filtro equivalente — ela aparece tanto na lista resumida (`fundamentos_tecnicos`) quanto, em detalhe, na seção separada de `pendencias`. É a mesma informação mostrada duas vezes em duas seções da mesma tela/PDF.

`"modelo estatístico indisponível"` é corretamente filtrado antes de chegar em qualquer UI/PDF — confirmado que nunca pode ser confundido com impedimento legal nas superfícies renderizadas hoje.

---

## 4. Cenários em que "Favorável" ainda sai com evidência insuficiente

Rastreamento linha a linha de `agent.py:90-115` (ver relatório completo do agente para a árvore de decisão). **Confirmado, reproduzível**: com `cobertura_matriz` a exatamente 61% (1 ponto acima do mínimo de 60%), zero alertas, zero pendências, `nivel_risco == "baixo"` e **nenhum modelo estatístico disponível**, o sistema retorna `cenario_favoravel` — ou seja, "Cenário preliminar favorável" pode sair com quase 40% da matriz de regras não coberta, sustentado só pela ausência de sinais negativos, não por evidência positiva suficiente. Na prática esse caminho é raro (baixa cobertura normalmente já gera `pendentes > 0`, que força "Inconclusiva"), mas o buraco existe e é o comportamento exato que o prompt pediu para fechar.

---

## 5. Auditoria de rótulos (dataset de treino)

**Códigos usados** (`extrair_rotulo()`, `app/trademarks/learning.py:221-311`): positivo = `IPAS029` (deferimento) ou `IPAS237` (recurso provido); negativo = `IPAS024` (indeferimento), com correspondência auxiliar por texto normalizado.

**Achado ALTO — bug real, sem contaminação confirmada em produção**: não existe filtro explícito para "arquivamento definitivo após deferimento" (ex.: `IPAS157`, arquivamento por falta de pagamento da concessão). A lógica varre movimentações da mais recente para a mais antiga e para no primeiro código que reconhece — se o arquivamento por falta de pagamento não bate em nenhum padrão de texto, a varredura "pula" ele e rotula o processo como `"deferida"` usando o `IPAS029` anterior, mesmo que a marca nunca tenha sido efetivamente registrada.
- **Medi o tamanho do problema no banco real**: 534.407 processos têm um `IPAS157` cronologicamente depois de um `IPAS029`.
- **Mas, checando a tabela real de treino (`rotulos_historicos_marca`)**: **0 desses processos estão rotulados como "deferida" no dataset atual.** Algum outro filtro de elegibilidade já os exclui na prática — não determinei qual exatamente, dado o tempo desta auditoria. **Classificação: ALTO no código (o bug existe e é fácil de reproduzir), mas NÃO CONFIRMADO como impacto real hoje** — precisa de mais uma investigação pontual para saber por que a contaminação não está acontecendo, antes de decidir se vale corrigir agora.

**Recursos**: tratados corretamente — a ordenação mais-recente-primeiro faz com que um `IPAS237` (recurso provido) posterior sempre vença sobre o indeferimento original que ele reverteu.

**Fundamento do indeferimento**: só 3 categorias reais existem na prática (`conflito_anterior`, `falta_distintividade`, `outra_proibicao` — este último é um balde genérico que absorve liceidade, veracidade, direitos de terceiros e alto renome, que o prompt pedia para diferenciar). Além disso, **só indeferimentos com fundamento `conflito_anterior` são elegíveis para treino** (`learning.py:539-541`) — os demais são rotulados mas descartados do treino.

**Processos pendentes**: corretamente excluídos (retorno `None`, sem rótulo default).

---

## 6. Vazamento temporal — auditoria feature a feature

Referência temporal usada em todo o pipeline: `processo.data_deposito` do exemplo histórico (não a data da decisão).

**2 vazamentos confirmados, 1 suspeito**:

| Feature | Vazamento? | Evidência |
|---|---|---|
| `portfolio_titular_candidata_norm` | **SIM — o mais grave** | `contar_marcas_por_titular()` (`learning.py:333-342`) conta TODOS os processos do titular até hoje, sem filtro de data, cacheado uma vez por titular pra todo o dataset. Um titular com 3 marcas em 2015 mas 40 hoje contamina exemplos de 2015 com o portfólio de 2026. |
| `marca_frequencia_max_norm` | **SIM** | Léxico de frequência (`app/cli/gerar_lexico_frequencia.py`) é um snapshot único do corpus atual, sem filtro de data, usado igualmente para exemplos de qualquer época. |
| `afinidade_conhecida` | **Suspeito, severidade incerta** | Lê a tabela `AfinidadeClasse` ao vivo, sem filtro de data — mas essa tabela é curada manualmente pela equipe de compliance; o risco real depende de quando/como ela é editada, não determinei isso nesta auditoria. |

Todas as demais 18 features (similaridade textual pura, `candidato_ativo`, `antiguidade_candidata_norm`, os 3 agregados derivados de busca) são corretamente filtradas pela data de referência — confirmado por citação direta de código.

---

## 7. Vazamento treino/teste (split)

O split é temporal puro (70/15/15 por `data_referencia` crescente, `learning.py:879-890`) — **sem nenhum agrupamento por titular ou família de marca**. Medi a sobreposição real via SQL contra os 5.000 rótulos elegíveis:

| Dimensão | Treino | Teste | Sobreposição |
|---|---|---|---|
| Titulares distintos | 2.730 | 504 | **1** (0,2%) |
| Marcas normalizadas distintas | 2.876 | 560 | **0** |

**Achado BAIXO**: apesar de não haver agrupamento explícito, a sobreposição real é essencialmente nula — o split temporal, na prática, já separa titulares/marcas quase perfeitamente porque poucos titulares têm marcas decididas em datas tão espaçadas que cruzem a fronteira treino/teste. Não é uma prioridade de correção.

---

## 8. Achados — classificação e recomendação

| # | Achado | Severidade | Confirmado? |
|---|---|---|---|
| 1 | Relatório público do cliente não usa `analise_consolidada` — ainda mostra "Atenção" como veredito de fato | **CRÍTICO** | Sim, código lido e confirmado |
| 2 | `portfolio_titular_candidata_norm` vaza portfólio atual em exemplos históricos | **ALTO** | Sim |
| 3 | `marca_frequencia_max_norm` vaza léxico de corpus atual em exemplos históricos | **ALTO** | Sim |
| 4 | Bug de rótulo: arquivamento após deferimento não é filtrado no código | **ALTO no código / não confirmado em produção** | Código sim, impacto real não |
| 5 | Taxonomia de fundamento colapsa liceidade/veracidade/terceiros/alto renome num balde só | MÉDIO | Sim |
| 6 | PDF pode ser gerado de uma versão diferente da que a tela mostra (sem trava de versão) | MÉDIO | Sim |
| 7 | Duplicação de "N pendências" em duas seções | BAIXO | Sim |
| 8 | "Favorável" alcançável com 61% de cobertura e sem modelo estatístico | MÉDIO | Sim, reproduzido na lógica |
| 9 | `afinidade_conhecida` lida sem gate de data | MÉDIO (severidade incerta) | Parcial |
| 10 | Sobreposição treino/teste por titular/marca | BAIXO | Sim — e é baixa na prática |

*(Seções de benchmark de busca e ablação de features seguem abaixo, pendentes de conclusão dos jobs em execução no momento da escrita deste documento.)*

---

## 9. Baseline do modelo (retrain com `regularizacao=0.05`, dataset atual de 5.000 rótulos / 65.701 pares)

Comando executado: `docker run ... python -m app.cli.treinar_registrabilidade --treinar` (sem `--dataset`, reaproveitando o dataset já construído em 2026-08-30).

| Métrica | Treino/ajuste | Validação (usada pros gates) |
|---|---|---|
| Amostras | 750 (bootstrap de avaliação) | 750 |
| Acurácia | 60,7% | 61,6% |
| Precisão | 75,8% | 72,2% |
| **Recall** | 64,8% | **65,4%** (gate: ≥80%) |
| **Especificidade** | 50,9% | **54,9%** (gate: ≥70%) |
| Acurácia balanceada | 57,8% | 60,1% |
| F1 | 69,9% | 68,6% |
| **Brier** | 0,205 | **0,224** (gate: ≤0,25 — passa) |
| **ECE** | 0,054 | **0,038** (gate: ≤0,12 — passa) |
| Matriz de confusão (validação) | TP=315 FP=121 TN=147 FN=167 | |

**Status**: modelo permanece em `SHADOW` — recall e especificidade abaixo dos gates, apesar do ajuste de regularização. Brier e ECE (calibração) já passam confortavelmente, então o problema não é calibração — é discriminação (o modelo não separa bem as duas classes).

**Limitação desta auditoria**: não produzi as quebras por classe Nice, período e fundamento jurídico pedidas no escopo — o código já calcula agrupamentos parecidos (`por_classe`/`por_periodo` em `learning.py`) mas não estão conectados à saída de `treinar_modelo()` hoje; extrair isso exigiria uma mudança de código pontual, que não fiz para manter esta auditoria só-leitura. Fica como item pendente para quem for mexer na Fase 3.

---

## 10. Benchmark de busca (200 casos reais de conflito citados pelo INPI)

Comparação já feita nesta sessão, código com Frente 1 (desconto por termo comum) vs. sem:

| Métrica | Sem Frente 1 | Com Frente 1 |
|---|---|---|
| Recall@5 | 20,5% | 20,5% |
| Recall@10 | 31% | **33%** |
| Recall@20 | 41% | 41% |
| Precision@10 | 3,7% | **3,9%** |
| MRR | 0,1020 | **0,1057** |
| Falsos negativos críticos | 118/200 | 118/200 |

**Falsos positivos por termo comum**: o caso real investigado ("Prenúncio o sinal antes do fato" vs. "SINAFRESP") mostrou que o desconto por frequência de palavra não cobre falsos positivos por **radical truncado curto** (ex.: "SINA", de "SINAL") — testei duas abordagens baseadas em frequência do léxico e nenhuma capturou esse padrão; documentado em `app/trademarks/lexico.py`. O motor **não reage excessivamente a uma única palavra isolada** de forma geral — reage especificamente a radicais de até 4 letras, que têm alta chance de colisão por acaso independente de frequência no corpus.

**Atualização — resultado da correção por comprimento de radical (testada e descartada)**: implementei um desconto para radicais truncados de até 4 letras (independente de frequência) e rodei o mesmo benchmark de 200 casos. Resultado: **regressão real**, não melhora.

| Métrica | Sem regra de comprimento (produção) | Com regra de comprimento |
|---|---|---|
| Recall@5 | 20,5% | 17,5% (-3pp) |
| Recall@10 | 33% | 29,5% (-3,5pp) |
| Recall@20 | 41% | 41% (igual) |
| MRR | 0,1057 | 0,0928 (-12%) |
| Falsos negativos críticos | 118 | 118 (igual) |

Confirma a hipótese de risco levantada antes de testar: descontar todo radical curto penaliza colisões curtas **legítimas** (marcas que realmente disputam um elemento de 4 letras), e esse custo superou o ganho no caso SINAFRESP. **Revertido — nunca chegou a ser commitado nem deployado.** O caso SINAFRESP/radical curto fica sem solução automática por ora; a recomendação é deixar para o parecer humano (HitL, já implementado na Frente 2) resolver caso a caso.

---

## 11. Ablação de features (item 7 do escopo)

Retreinei o modelo 4 vezes, cada vez removendo um subconjunto de `ATRIBUTOS_MODELO` (via monkeypatch isolado, nada persistido, nada promovido), comparando contra o baseline da seção 9 (recall=65,4%, especificidade=54,9%, brier=0,224, ece=0,038):

| Variante | Recall | Especificidade | Brier | ECE |
|---|---|---|---|---|
| Baseline (todas as features) | 65,4% | 54,9% | 0,224 | 0,038 |
| Sem `portfolio_titular_candidata_norm` | 65,1% | 54,9% | 0,225 | 0,032 |
| Sem `marca_frequencia_max_norm` | 68,3% | 51,9% | 0,224 | 0,040 |
| Sem as duas anteriores juntas | 66,8% | 53,4% | 0,225 | 0,035 |
| Sem `afinidade_conhecida` | 65,4% | 54,9% | 0,224 | 0,038 |

**Achado contraintuitivo, mas importante**: remover as features com vazamento temporal confirmado (seção 6) **não melhora as métricas de validação de forma relevante** — a variação fica dentro do ruído (±3pp), sem nenhuma combinação cruzando os gates de 80%/70%. Isso faz sentido tecnicamente: o vazamento contamina treino e validação da mesma forma (ambos usam o snapshot atual do banco), então não infla artificialmente o desempenho medido hoje — o risco real do vazamento é sobre generalização em produção no futuro, não sobre as métricas que estamos vendo agora.

**Conclusão prática**: os dois vazamentos confirmados (seção 6) continuam sendo bugs reais que vale corrigir por corretude — mas corrigi-los **não é o que vai destravar os gates de governança**. O teto de desempenho do modelo parece estar limitado por outra coisa (capacidade do modelo — regressão logística simples —, riqueza do conjunto de features, ou sobreposição real entre as classes nos dados), não pelas duas features vazadas.

---

## 12. Conclusão e recomendação final

### Recomendação: **B — fazer apenas correções pontuais na Fase 3, não uma reconstrução completa**

**Evidência**: a ablação (seção 11) mostra que os vazamentos temporais confirmados, mesmo sendo reais, não são a causa do modelo falhar os gates — removê-los não move a agulha. Isso reduz a expectativa de ganho de uma reconstrução completa do dataset (Fase 3 inteira). Ao mesmo tempo, os achados críticos/altos que *realmente* importam (item 1: relatório público desatualizado; item 4: bug de rótulo no código, mesmo sem impacto confirmado hoje) não têm nada a ver com o modelo de ML — são bugs de integração e de robustez de código, corrigíveis pontualmente e com risco baixo.

**Itens que valem correção pontual imediata** (ordenados por impacto):
1. **Conectar o relatório público (`ResumoPublicoMarcaResponse`/`relatorio.js`) ao `analise_consolidada`** — impacto: o cliente passa a ver o veredito unificado Favorável/Desfavorável/Inconclusiva de verdade, elimina "Atenção" como pseudo-veredito. Custo: baixo-médio (schema + endpoint + JS, sem mudança de motor). Risco: baixo, é aditivo. Teste que comprova: `test_web.py` novo garantindo que `GET /relatorios/{id}` inclui `analise_consolidada` e que `relatorio.js` nunca renderiza `prognostico_registrabilidade.veredito=="atencao"` como texto solto.
2. **Corrigir a lacuna de rótulo (arquivamento após deferimento)** — impacto: hoje 0 confirmado, mas o bug existe e pode passar a contaminar dados no futuro conforme mais processos avançam para esse estado. Custo: baixo (adicionar um filtro explícito de arquivamento em `extrair_rotulo`). Risco: baixo. Teste: caso sintético com movimentação 029 seguida de 157, confirmando `extrair_rotulo()` retorna `None` em vez de "deferida".
3. **Corrigir os 2 vazamentos temporais confirmados** (portfólio e frequência de termo) — vale fazer por corretude/auditabilidade mesmo sem ganho de métrica hoje; documentar claramente que o ganho esperado é sobre risco futuro, não sobre os números atuais.
4. **Travar a versão do PDF contra a tela** (achado médio, item 6) — evita a divergência tela-vs-PDF.

**Não recomendo agora**: reconstrução completa do dataset (snapshots temporais por-anterioridade, split agrupado por titular/família, taxonomia completa de 7 fundamentos) — o item 7 (sobreposição treino/teste) já está baixo na prática (seção 7), e a ablação não sustenta a expectativa de ganho grande. Se, depois das correções pontuais acima, ainda houver apetite para melhorar o modelo, a próxima aposta de maior alavancagem provavelmente é **mais dados de treino com rótulo `conflito_anterior` de qualidade** (hoje só esse fundamento é elegível — seção 5) ou revisar a capacidade do modelo (regressão logística simples pode estar no teto), não o pipeline de features.

### Comandos executados nesta auditoria
```
# Consultas de banco (leitura)
docker exec zeregistra-db-1 psql -U inpi -d inpi -c "..."  # overlap treino/teste, tally de despacho, bug de rótulo

# Retrain baseline
docker run ... python -m app.cli.treinar_registrabilidade --treinar

# Ablação (não persistida)
docker run ... python /app/ablation.py   # monkeypatch de ATRIBUTOS_MODELO, session.rollback() após cada variante

# Benchmark de busca (200 casos)
docker run ... python -m app.cli.avaliar_qualidade_busca --limite-resultados 20
```

### Arquivos gerados por esta auditoria
- `work/AUDITORIA-FASE1.md` (este documento)
- Nenhuma mudança de produção, nenhum modelo promovido, nenhum dado de produção alterado.
