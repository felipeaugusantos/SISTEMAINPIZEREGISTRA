# Fase 8 — Testes e segurança

Levantamento completo (antes de escrever qualquer teste novo) do que já
existia cobrindo os 12 itens pedidos, pra não duplicar suíte. Resultado:
a maior parte já tinha cobertura real (a maioria das Fases 4-7 nasceu
já com testes, seguindo o hábito desta sessão), com 4 lacunas genuínas.

## Cobertura já existente (sem mudança)

| Item | Onde |
| --- | --- |
| Permissões (403) | `tests/test_permissions.py`, `tests/test_observabilidade.py`, `tests/test_feature_flags.py` e outros ~15 arquivos |
| Feature flag ligada/desligada | `tests/test_feature_flags.py` (lógica de `flag_ativa`/`flag_ativa_para_organizacao`) |
| Liberação só administradores | `tests/test_feature_flags.py`, `tests/test_feature_flags_rollout.py` |
| Rollout gradual | `tests/test_feature_flags_rollout.py` (17 testes: bucket estável, estágios, circuito de erro) |
| Desativação emergencial | `tests/test_observabilidade.py` (`desligar`/`religar`, idempotência, 403) |
| Confirmação de leitura | `tests/test_atualizacoes.py` (idempotência, auditoria por tenant) |
| Isolamento entre organizações | `tests/test_saas_rls_postgres.py` — matriz real contra Postgres (20+ tabelas), já cobre o achado D2 da auditoria original |

## Lacunas preenchidas nesta fase

1. **Isolamento real das tabelas novas** (Fases 3-5) — `test_saas_rls_postgres.py` não incluía `interacoes_versao_sistema`, `problemas_versoes_sistema` nem `feature_flags_organizacoes`. Novo arquivo `tests/test_fase8_seguranca_postgres.py`:
   - `test_rls_real_isola_interacoes_e_problemas_versao_entre_organizacoes` — mesma técnica do arquivo de referência (role de aplicação efêmera, `set_config`, `INSERT`/`UPDATE`/`DELETE` cruzados). Este é o teste que prova literalmente o critério de aceite da Fase 8 ("nenhuma organização consegue consultar ou alterar as configurações de outra") para os dados desta sessão.
2. **Imutabilidade real (trigger do banco, não só a camada de aplicação)** — `test_trigger_real_bloqueia_update_direto_em_versao_publicada`: um `UPDATE`/`DELETE` direto via SQL (bypassando a API inteira) numa versão publicada é bloqueado pelo próprio `trg_proteger_versao_sistema`.
3. **Concorrência real na ativação** — `test_concorrencia_real_ativacao_feature_flag_organizacao`: duas conexões Postgres reais tentando `INSERT` a mesma combinação `(feature_flag_id, organizacao_id)` ao mesmo tempo via `asyncio.gather` — confirma que exatamente uma vence e a outra recebe `UniqueViolationError` (nunca duas linhas).
4. **Unicidade real de confirmação de leitura** — `test_uq_interacao_versao_usuario_impede_confirmacao_duplicada`.
5. **Compatibilidade API/worker/banco** (`tests/test_fase8_regressao.py`) — nenhum teste importava `app.worker` diretamente; um import quebrado só aparecia em produção quando o container subia. Adicionado `test_worker_importa_sem_erro` / `test_main_importa_sem_erro`, e `test_migrations_tem_uma_unica_head` (evita uma migration "órfã"/branch divergente quebrar `alembic upgrade head` no deploy, sem precisar de banco).
6. **Auditoria — checklist único** — `test_acoes_criticas_das_fases_4_a_7_sempre_auditam` reúne num só lugar as ações de rollback/rollout (`FLAG_DESLIGAR`, `FLAG_RELIGAR`, `FLAG_INTERROMPER`, `FLAG_CRIAR`), fácil de estender quando uma ação crítica nova aparecer.
7. **Ausência de segredos — checagem transversal** — `_percorrer_chaves()` desce recursivamente em qualquer resposta (dict/list aninhado) procurando chaves como `senha`, `*_hash`, `anexo_caminho`, `api_token`, `webhook_secret`, em vez de checar campo a campo como os testes anteriores já faziam.

## Nota sobre onde os testes de Postgres real rodam

`tests/test_saas_rls_postgres.py`, `tests/test_phase7_postgres.py` e o novo
`tests/test_fase8_seguranca_postgres.py` usam `TEST_ADMIN_DATABASE_URL`
(padrão `postgresql://inpi:inpi@localhost:5432/inpi`) e fazem
`pytest.skip()` se o banco não estiver acessível. O profile Docker `test`
usado no fluxo de validação desta sessão (`docker compose --profile test
run --rm test ...`) roda contra o serviço `db-test`, não acessível como
`localhost` de dentro do container de teste — por isso esses testes
aparecem como "skipped" na validação local/VPS. Eles rodam de verdade no
GitHub Actions CI (`.github/workflows/ci.yml`), que sobe um Postgres como
service container mapeado pra `localhost:5432` do runner — mesmo padrão
já usado por `test_saas_rls_postgres.py` antes desta fase.
