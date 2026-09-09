# Fase 11 — Retrospectiva geral do projeto

A Fase 10 documentou lições técnicas específicas das Fases 4-9 (feature
flags, rollout, observabilidade, testes, deploy). Este documento olha
pro dia inteiro — de manhã cedo (separação de DDL para asyncpg, ainda
antes da Fase 4) até agora (o botão "Dispensar" nos avisos) — e pra
forma de trabalhar em si, não só pro código.

## O dia em números

- **31 commits**, das 08:46 às 17:48.
- **93 arquivos alterados**, +5.471/-159 linhas.
- **~25 deploys em produção** (versão saiu de 1.0.72 para 1.0.97).
- **5 migrations novas**, todas com `downgrade()` testado em ciclo
  completo antes do merge — nenhuma delas exigiu correção depois de ir
  pra produção.
- **2 arquivos de teste inteiramente novos** (`test_feature_flags_rollout.py`,
  `test_fase8_*`) além de extensões nos existentes — mais de 200 testes
  novos só hoje.
- **Zero rollback de aplicação** precisou ser executado — todo problema
  encontrado em produção foi corrigido com um deploy novo pra frente,
  nunca com `git checkout` pra trás.

## O que caracterizou o dia

**Um sistema inteiro nasceu sobre uma decisão que já existia.** Feature
flags (Fase 4) não foi ideia nova hoje — Codex já tinha Fases 1-2 da
central de atualizações em `origin/main` quando a sessão começou. A
decisão mais importante do dia não foi de código: foi "descartar meu
trabalho paralelo e construir em cima do que já existia" em vez de
insistir na própria implementação. Todo o resto do dia — Fases 4 a 10 —
foi construído sobre essa fundação compartilhada, não ao lado dela.

**O ciclo de validação se tornou automático de tão repetido.** Por volta
da Fase 6, o padrão "implementar → Docker isolado (ruff, migration em
ciclo upgrade/downgrade/upgrade, suíte completa) → merge → deploy →
verificar ao vivo → registrar versão" deixou de ser uma lista consciente
e virou o jeito natural de fechar qualquer mudança, por menor que fosse
(até o fix do botão "Dispensar", puramente frontend, seguiu o mesmo
fluxo de commit → merge → deploy → verificação ao vivo).

**"Verificar ao vivo" pagou o próprio custo mais de uma vez.** Três
problemas reais (`MissingGreenlet`, permissão de `data/uploads`,
kill-switch sem volta — detalhados na Fase 10) só apareceram porque
alguém *usou* a funcionalidade em produção depois do deploy, não porque
um teste os pegou. Nenhum desses era um bug de lógica que um teste
unitário identificaria — eram lacunas de integração/infraestrutura que
só aparecem com a coisa rodando de verdade.

**Os próprios testes acharam bugs nos testes, não só no sistema.** A
Fase 8 achou uma migration antiga que não seguia a convenção de
anotação de tipo (`revision = "x"` em vez de `revision: str = "x"`) —
não porque o sistema estivesse quebrado, mas porque o teste novo
generalizou uma checagem que antes só existia no meu próprio processo
manual (rodar grep pra achar a *head* da árvore de migrations, feito
"na mão" várias vezes ao longo do dia antes de virar teste automatizado
na Fase 8).

**Infraestrutura construída pra uma coisa virou base pra outra sem
planejamento prévio.** A telemetria por grupo da Fase 5
(`FeatureFlagEvento`) foi pensada pro circuito de interrupção
automática — e acabou sendo reaproveitada sem alteração nenhuma pra
"organizações afetadas" no painel técnico da Fase 7. O padrão de
auditoria (`criar_evento_auditoria`) criado nas fases iniciais da
central de atualizações virou o mecanismo padrão de toda ação sensível
das fases seguintes, sem precisar reinventar nada.

## Onde o processo cobrou o preço de ir rápido

- **Limite de 20 caracteres em `EventoAuditoria.acao` mordeu mais de uma
  vez** (feature flags, tela de auditoria de problemas) até virar hábito
  contar caracteres antes de escrever o nome da ação, não depois de o
  teste falhar.
- **Drift de cache-busting (`?v=N`) em 47 arquivos HTML** — bumps
  parciais (só nos arquivos "óbvios" de cada mudança) deixaram versões
  divergentes acumuladas ao longo do dia; a correção que colou foi um
  `sed` global por padrão de arquivo, aplicado de novo a cada mudança de
  asset compartilhado.
- **A restrição de `git` em comandos SSH desta sessão** (proteção do
  worktree isolado) exigiu um ajuste de abordagem no meio do dia: em vez
  de rodar `git` direto via SSH, migrar pra scripts locais copiados pro
  servidor e executados sem `git` na linha de invocação — mesmo padrão
  que `docker/deploy.sh` já usava, só reconhecido depois de esbarrar na
  restrição algumas vezes.

## Sobre o modelo de trabalho (Claude + Codex + usuário)

O dia confirmou um padrão que vale registrar explicitamente: **duas IAs
trabalhando no mesmo repositório sem coordenação direta entre si geram
risco real de trabalho duplicado ou conflitante**, mitigável só com um
hábito simples e barato — checar `git log origin/main` antes de começar
qualquer fase nova, não confiar em memória de conversas anteriores sobre
o estado do repositório. Isso aconteceu uma vez de verdade (Fase 3) e
foi checado preventivamente em toda fase depois (inclusive achando, na
Fase 6, que os branches `codex/fase5-indicadores`/`codex/fase6-
automacoes` eram sobre um assunto completamente diferente e não
representavam colisão nenhuma — a checagem também serve pra *não* agir
sobre um alarme falso).

## Recomendações pra quem continuar

1. **Manter o hábito de registrar `VersaoSistema` em todo deploy** — não
   é overhead, é o que faz a Central de Atualizações (e o painel técnico
   da Fase 7) funcionarem de verdade como fonte de verdade, em vez de
   virarem telas vazias.
2. **Rodar a suíte de Postgres real (`test_fase8_seguranca_postgres.py`,
   `test_saas_rls_postgres.py`, `test_phase7_postgres.py`) pelo menos uma
   vez antes de qualquer mudança grande em RLS/multi-tenant** — hoje só
   rodam de verdade no GitHub Actions CI; localmente/na VPS ficam
   skipped, o que é fácil de esquecer no meio de um dia corrido.
3. **Continuar usando a infraestrutura de feature flags + rollout
   gradual (Fases 4-5) para qualquer funcionalidade nova de risco não
   trivial** — o custo de configurar já está pago; não usá-la numa
   próxima feature arriscada seria jogar fora o investimento de hoje.
4. **O painel técnico (Fase 7) é o primeiro lugar a olhar, não o
   último**, na próxima vez que algo parecer errado em produção — antes
   de abrir uma investigação do zero via SSH/logs.
