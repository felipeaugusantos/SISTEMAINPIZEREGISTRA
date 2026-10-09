# Plano de melhoria — Auditoria de filas e controles operacionais

Resposta à "Auditoria Técnica — Filas, recuperação e controles operacionais"
(revisão `b4f20c6`, 07/10/2026). Nenhuma falha crítica; prioridade é proteger
a fila contra perda de trabalho. Cada item cruzado com o código atual.

## Situação dos 12 achados

| # | Achado | Grav. | Situação |
|---|---|---|---|
| 1 | Redis `allkeys-lru` 128MB pode descartar jobs | Alta | A corrigir (Bloco 1) |
| 2 | Transferências de tarefas não atômicas | Alta | Parcial: consumo principal já atômico (`brpoplpush`+recuperação do `PROCESSING`); falta retry/reprocessamento (Bloco 1) |
| 3 | Dedup grava a chave antes de enfileirar | Média | A corrigir (Bloco 1) |
| 4 | Importação presa em "executando" com etapa | Média | Parcial (PR #160/#162); falta abandono por progresso estagnado (Bloco 2) |
| 5 | Cancelamento da importação com janela | Média | Parcial (PR #161); fechar janela com update condicional (Bloco 2) |
| 6 | E-mails essenciais compartilham cota | Média | Já OK em prod: essencial=provedor principal (500), comercial=secundário (100) |
| 7 | Cota de e-mail não reserva capacidade | Média | Conhecido/adiado (PR #164); margem 100<~111 cobre |
| 8 | Token de recuperação sem consumo atômico | Média | A corrigir (Bloco 3) |
| 9 | Re-salvar protocolo altera a data | Média | A corrigir (Bloco 3) |
| 10 | Backup parcial bloqueia nova tentativa | Média | Verificar/ajustar (Bloco 4) |
| 11 | `pip-audit`/`npm audit` não bloqueiam a CI | Média/seg | Proposital; definir gate (Bloco 4) |
| 12 | TLS da consulta de RPI difere do download | Baixa | A corrigir (Bloco 4) |

## Blocos (ordem recomendada)

### Bloco 1 — Fila à prova de perda (#1, #2, #3) — prioridade máxima
- Redis: trocar `allkeys-lru` por `noeviction` (rejeita escrita em vez de
  descartar job), subir `maxmemory` com folga e alertar uso.
- Transferências de retry (`promover_retentativas`) e reprocessamento
  (`reprocessar_falha`) atômicas (script Lua ou "adiciona antes de remover").
- Gravar a chave de dedup junto/depois do `rpush`, não antes.

### Bloco 2 — Recuperação de importação (#4, #5)
- Abandono por último progresso (etapa estagnada + heartbeat do worker),
  não só execução sem etapa.
- Cancelamento com atualização condicional; `cancelado` é terminal.

### Bloco 3 — Consumo único e rastreabilidade (#8, #9)
- Token de recuperação e códigos de MFA: UPDATE condicional atômico
  (`WHERE usado_em IS NULL`), rejeitando a segunda requisição.
- Protocolo de proposta: preservar data/número originais (write-once).

### Bloco 4 — Operação e CI (#10, #11, #12)
- Backup: gravar `.tmp` → validar → renomear atômico; pulo diário só olha
  arquivos finais.
- CI: definir severidades que bloqueiam `pip-audit`/`npm audit`.
- TLS: mesma cadeia de confiança (CA Fortinet) na consulta da última RPI e
  no download.

## Controles positivos já confirmados
Argon2, CSRF, MFA por perfil, contexto de organização reaplicado nas
transações (RLS via `inpi_app`), varredura de uploads com rejeição quando o
scanner está indisponível (ClamAV fail-closed, ligado em produção).
