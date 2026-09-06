# Incidente: perda de dados em produção — 05/09/2026

## Resumo

Entre o backup das 12:07 (05/09) e uma verificação por volta de 00:50 (06/09), as tabelas `leads`, `usuarios_operacoes`, `sessoes_operacoes` e `processos_monitorados` ficaram vazias em produção. `organizacoes` (1 linha) e as tabelas de referência compartilhadas da RPI permaneceram intactas. O sintoma inicial foi login administrativo falhando ("Usuário ou senha inválidos"); a causa real só apareceu ao consultar o banco diretamente.

## Detecção

Três tentativas de login com credenciais previamente válidas falharam. Em vez de seguir tentando (risco de bloqueio de conta), a investigação foi para o banco — que confirmou `usuarios_operacoes` vazia, não uma credencial incorreta ou conta bloqueada.

## Recuperação

Restauração do backup das 12:07 (05/09) em produção via `docker/restaurar-banco.sh --producao`. O primeiro `pg_restore --clean` falhou por dependência de chave estrangeira (a migration `zw08g6y0j519`, aplicada depois desse backup, criou `apontamentos_horas` referenciando tabelas que o `--clean` precisava recriar). Corrigido com `DROP TABLE apontamentos_horas CASCADE` antes do restore, seguido de `alembic upgrade head` para recriar a tabela vazia. Estado final verificado: organizacoes=1, leads=3, usuarios_operacoes=5, processos_monitorados=57, auditoria=2972, sessoes=41 — consistente com o backup das 12:07.

**Dado perdido:** qualquer alteração feita entre 12:07 e o momento do wipe (se houve) não está no backup restaurado. Não foi possível quantificar isso porque as próprias tabelas afetadas foram esvaziadas antes do backup seguinte.

## Causas descartadas (com evidência)

- **`app/bootstrap_admin.py`** — lido por completo; só cria o usuário admin se ausente, nunca apaga/recria a organização.
- **Cascata via exclusão de `organizacoes`** — tecnicamente impossível: `Lead.organizacao_id` e `UsuarioOperacoes.organizacao_id` usam `ondelete="RESTRICT"`, não `CASCADE`. O Postgres teria bloqueado a exclusão da organização com esses registros ainda vinculados.
- **Colisão de volume Docker entre ambientes** — `compose.yaml` declara os volumes (`postgres_data`, `test_postgres_data`, etc.) sem nome fixo/`external`, então o Compose já os isola por projeto.
- **Reinício do container às 22:00 UTC** — confirmado como desligamento/reinício limpo nos logs do Postgres (cron de reboot diário já conhecido), não uma queda abrupta; coincidência de horário, não causa.
- **Acesso SSH anômalo** — os logs de autenticação mostram só o padrão esperado de uso (múltiplas conexões do mesmo IP, consistente com automação/ferramentas rodando comandos individuais); não foi encontrado um evento isolado fora desse padrão.

## Causa raiz

**Não identificada com certeza.** A evidência mais direta (estado exato do banco no momento da perda, WAL, contadores de exclusão) foi sobrescrita pela própria restauração de emergência antes que desse para investigar mais fundo — trade-off aceito para não prolongar o downtime.

## Ações preventivas adotadas

- `docker/simulado-restauracao.sh` (ver `docs/operacao-recuperacao-desastre.md`, seção 6): simulado periódico que restaura o último backup em `db-test` (efêmero) e confere as contagens das tabelas centrais contra produção, para detectar backup corrompido ou restauração incompleta antes de uma emergência real.

## Ações futuras possíveis (não implementadas)

- Logging mais granular a nível de banco (ex.: `pgAudit` ou `log_statement` temporário) para conseguir reconstruir a sequência exata de comandos em um incidente futuro, já que o rastro atual (logs de aplicação + auditoria) não cobre operações feitas fora da aplicação.
