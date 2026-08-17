# Release Candidate 1 - checklist de qualidade

Data da validacao: 2026-08-17

## Gates

| Gate | Evidencia | Estado |
|---|---|---|
| Unitario e integracao | `uv run pytest -q` | aprovado - 354 testes |
| Benchmark juridico | `tests/test_search_benchmark.py` e `tests/test_search_ranking.py` | aprovado - 28 testes |
| E2E | `npm.cmd run test:e2e` | aprovado - 2 testes; 1 fluxo de login ignorado por credencial |
| Seguranca e isolamento | PostgreSQL real, tenants A/B e IDs cruzados | aprovado |
| Regressao visual | Playwright | pendente de aprovacao visual formal |
| Carga | volume representativo de homologacao | pendente de execucao com dados de producao |
| Banco/migrations | `uv run alembic current` | aprovado localmente ate `ace88w3p1z85` |
| Health | `GET /health` e containers Docker | aprovado - HTTP 200 |
| Rollback/DR | downgrade em banco descartavel e restauracao | pendente de evidencia operacional |

## Criterio de liberacao

O RC1 esta tecnicamente montado, mas nao deve ser promovido para producao ate
que regressao visual, carga, backup/restauracao e rollback tenham evidencias
assinadas. O dataset juridico tambem precisa de revisao de especialista antes
de qualquer publicacao de ranking.

## Procedimento de rollback

1. Preservar logs e o identificador da versao.
2. Colocar a aplicacao em manutencao.
3. Restaurar a imagem anterior.
4. Executar somente migrations de downgrade previamente testadas.
5. Validar health, login, RPI, financeiro e portal.
6. Reabrir o trafego apos aprovacao tecnica.
