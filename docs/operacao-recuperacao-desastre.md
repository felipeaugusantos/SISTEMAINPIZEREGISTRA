# Operação e recuperação de desastre

Este procedimento usa somente a infraestrutura existente. Execute primeiro em ambiente descartável e registre operador, horário, origem do backup e resultado das validações.

## 1. Preparar o ambiente

1. Confirme espaço para o dump, a restauração e a reconstrução dos índices.
2. Recupere os secrets pelo cofre operacional: `APP_DB_PASSWORD`, `SECURITY_MASTER_KEY` e sua versão, chave anterior durante rotação, credenciais OAuth/SMTP e senha inicial administrativa. Nunca copie o `.env` para o Git.
3. Suba `db` e `redis` e aguarde os healthchecks. Não inicie API, worker ou RPI sync contra um banco parcialmente restaurado.

## 2. Backup e restauração

Crie um backup pelo script real:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\backup-banco.ps1
```

Para restaurar no ambiente definido pelo Compose, use confirmação explícita. O script cria antes um backup de segurança, para API/worker/RPI sync, executa `pg_restore --clean --if-exists --no-owner` e reinicia os serviços:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\restaurar-banco.ps1 -Arquivo .\backups\inpi-AAAAMMDD-HHMMSS.dump -Confirmar
```

**Na VPS** (sem PowerShell), use os equivalentes em bash (Fase 0, item 8/9 — `docs/fase0-baseline-controle-desenvolvimento.md`):

```bash
./docker/backup-banco.sh
./docker/restaurar-banco.sh backups/inpi-AAAAMMDD-HHMMSS.dump              # restaura em db-test (efêmero, sem risco)
./docker/restaurar-banco.sh backups/inpi-AAAAMMDD-HHMMSS.dump --producao   # restaura em produção -- exige confirmação digitada
```

`docker/deploy.sh` já chama `backup-banco.sh` automaticamente antes de toda migration.

## 3. Validar schema e dados

```powershell
.venv\Scripts\alembic.exe current
.venv\Scripts\alembic.exe heads
.venv\Scripts\alembic.exe check
```

O `current` deve coincidir com o único `head`. Compare com a origem as contagens de `processos`, `movimentacoes`, `rpi_importacoes`, `organizacoes`, `usuarios_operacoes`, `leads`, `pesquisas_marca` e `lancamentos_financeiros`. Não corrija divergência ambígua automaticamente.

## 4. Subir e validar a aplicação

```powershell
docker compose up -d
docker compose ps
```

Confirme `db`, `redis` e `api` saudáveis, `migrate` concluído com código zero e `worker`/`rpi-sync` em execução. Depois valide:

1. `GET /health` com banco e Redis `ok`;
2. `GET /health/rpi`, registrando status, idade, integridade e anomalias;
3. login de uma conta administrativa previamente autorizada;
4. consulta autenticada de um registro conhecido;
5. logs estruturados sem senha, token, MFA secret ou integration key completa;
6. uma operação com `X-Request-ID` e o mesmo identificador na auditoria.

## 5. Critérios de interrupção

Não libere tráfego se o restore falhar, Alembic divergir, contagens essenciais não coincidirem, login falhar, RLS estiver desativado ou `/health` não estiver `ok`. Preserve o dump e os logs para diagnóstico e reverta para o ambiente anterior sem apagar o banco restaurado.
