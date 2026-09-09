# Fase 10 — Encerramento e lições aprendidas

Fecha o arco de trabalho desta sessão: Fase 4 (feature flags por
organização) até Fase 9 (runbook de deploy), mais as correções que
apareceram no meio do caminho (RAG local, permissões da RPI, `data/
uploads`, `MissingGreenlet`). Este documento não é mais um manual de
como usar alguma coisa — é o registro honesto do que funcionou, do que
quebrou, e do que fica pendente.

## O que foi entregue

| Fase | Do que se trata | Onde |
| --- | --- | --- |
| 4 | Feature flags por organização (4 estados, kill-switch, dependências) | `app/feature_flags.py`, `app/api/feature_flags.py` |
| 5 | Liberação gradual por grupo (5 estágios cumulativos), monitoramento por grupo, circuito de interrupção automática | `docs/fase5-liberacao-gradual.md` |
| 6 | Reporte de problema estruturado (etapas/resultado/gravidade/anexo) vinculado à versão | `docs/fase6-reporte-problemas.md` |
| 7 | Painel técnico único (versão/commit/migration/saúde/erros por versão/flags ativas/orgs afetadas) + rollback de emergência | `docs/fase7-observabilidade-rollback.md` |
| 8 | Regressão de segurança: isolamento real (RLS), imutabilidade real (trigger), concorrência real, auditoria, ausência de segredos | `docs/fase8-testes-seguranca.md` |
| 9 | Runbook de deploy com ordem recomendada + validação real de backup | `docs/fase9-deploy-producao.md` |

Cada fase seguiu o mesmo ciclo: implementar → testar em Docker isolado
(ruff, migration em ciclo upgrade/downgrade/upgrade, suíte completa) →
mesclar em `main` → deploy em produção → **verificar ao vivo** (não só
confiar no teste) → registrar a versão na Central de Atualizações.

## O que essa disciplina pegou de verdade (não é teórico)

O passo "verificar ao vivo" não foi formalidade — pegou três problemas
reais que nenhum teste unitário veria, porque eram lacunas de
integração/infraestrutura, não bugs de lógica:

1. **`MissingGreenlet` intermitente em `/publicar` e `/arquivar`**
   (`VersaoSistema`). Causa raiz: uma coluna com `onupdate=func.now()`
   fica marcada como expirada pelo SQLAlchemy depois de um `UPDATE`,
   **mesmo com `expire_on_commit=False`** na sessão — isso só desliga a
   expiração em massa no commit, não a expiração individual de colunas
   geradas pelo banco. O acesso síncrono a essa coluna, fora de um
   `await session.X()`, tentava um lazy-load fora do bridge async/
   greenlet do SQLAlchemy. Só foi possível achar a linha exata porque o
   middleware de observabilidade não guardava traceback nenhum — a
   primeira correção real foi *melhorar o diagnóstico* (capturar o
   traceback completo), não o bug em si; só depois, reproduzindo o erro
   de propósito em produção com o traceback novo, a causa apareceu.
   Corrigido com `eager_defaults=True`, e aplicado preventivamente a
   `FeatureFlag`, `Organizacao` e `ProcessoHeartbeat` (mesma classe de
   bug, antes de acontecer de novo).
2. **`data/uploads` com dono `root` em produção**, bloqueando qualquer
   upload novo (não só o da Fase 6). Um teste unitário nunca pegaria
   isso — é permissão de sistema de arquivos do host, não algo que
   `FakeSession` simula. Só apareceu ao testar o fluxo de anexo de
   verdade em produção, logo depois do deploy.
3. **Kill-switch de emergência sem volta** — `POST .../desligar` (Fase
   7) foi implementado, testado com `FakeSession`, passou na validação
   Docker... e ao testar ao vivo descobri que não existia nenhum jeito
   de religar a flag pela API. Precisei de um `UPDATE` manual no banco
   pra desfazer o próprio teste. O endpoint `/religar` foi escrito,
   testado e deployado antes de considerar a Fase 7 encerrada.

Lição prática: **testes com `FakeSession` provam que a lógica está
certa; não provam que a funcionalidade está completa ou que o ambiente
de produção está configurado do jeito que o código assume.** As duas
coisas são necessárias, nenhuma substitui a outra.

## Outras lições, menores mas recorrentes

- **`EventoAuditoria.acao` trunca em 20 caracteres, silenciosamente.**
  Voltou a morder mais de uma vez ao longo da sessão (feature flags,
  tela de auditoria de problemas) até virar hábito checar o tamanho do
  nome da ação antes de escrever o código, não depois.
- **Cache-busting (`?v=N`) em 47 arquivos HTML só funciona se for
  atualizado em todos ao mesmo tempo.** Bumps parciais (só nos arquivos
  "óbvios") deixaram versões divergentes acumuladas; a correção que
  colou foi um `sed` global por padrão de arquivo, não lembrar
  manualmente de cada HTML.
- **Colisão de trabalho paralelo com outro agente (Codex) na Fase 3.**
  Descoberta a tempo checando `git log origin/main` antes de aprofundar
  — virou hábito repetir essa checagem no início de toda fase nova
  desde então.
- **Meu próprio teste novo pegou um bug no teste, não no código**: o
  regex de `test_migrations_tem_uma_unica_head` (Fase 8) não reconhecia
  uma migration antiga escrita antes da convenção `revision: str = `
  (só `revision = `) pegar. Escrever o teste já valeu a pena mesmo
  achando um problema na *forma de checar*, não no sistema em si —
  é exatamente o tipo de "migration órfã silenciosa" que o teste existe
  pra prevenir.

## O que fica registrado como limitação conhecida (não escondido)

- **Circuito de interrupção automática roda de hora em hora**, não em
  tempo real — depende da única cadência de tarefas periódicas que já
  existe no worker. Documentado em `docs/fase5-liberacao-gradual.md`.
- **"Erros por versão" e "impacto nos módulos existentes" são proxy por
  janela de tempo**, não atribuição causal de verdade (duas mudanças no
  mesmo intervalo são indistinguíveis). Documentado nas Fases 5 e 7.
- **Os testes de isolamento real (RLS), concorrência real e trigger real
  (Fase 8) só rodam no GitHub Actions CI**, não na validação Docker
  local/VPS usada nesta sessão (o profile `test` local conecta em
  `db-test`, não em `localhost:5432`, onde esses testes procuram o
  banco). Rodar "tudo verde" localmente não prova que essa bateria
  específica passou — só o CI prova isso hoje.
- **Não existe UI dedicada para marcar uma organização como "ambiente
  interno"** (Fase 5) — feito hoje via `PATCH
  /v1/admin/saas/organizacoes/{id}` direto, mesmo padrão de qualquer
  outro campo de `OrganizacaoUpdate`.
- **Rollback de aplicação continua manual e restrito** (SSH + git +
  `docker/deploy.sh`), por decisão explícita do usuário na Fase 7 — não
  é uma lacuna, é a escolha de manter esse poder fora de um botão
  self-service.

## Se alguém for continuar depois

A infraestrutura de feature flags + rollout gradual (Fases 4-5) não é
específica de nenhuma funcionalidade — está pronta pra qualquer
próxima feature nova nascer atrás dela, sem repetir o trabalho de base.
O painel técnico (Fase 7) e o runbook de deploy (Fase 9) são o ponto de
partida natural pra diagnosticar qualquer regressão futura antes de
abrir uma investigação do zero.
