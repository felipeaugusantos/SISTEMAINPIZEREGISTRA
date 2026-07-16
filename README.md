# INPI API

API somente leitura para consulta de marcas e patentes do INPI Brasil.

## Pré-requisitos

- Python 3.11
- `uv`
- Docker Desktop

## Executar com Docker

```powershell
docker compose up --build -d
```

As migrações do banco são aplicadas automaticamente no início do contêiner
(`alembic upgrade head`), depois que o Postgres fica saudável.

Acesse:

- Tela de consulta: http://localhost:8000
- Swagger: http://localhost:8000/docs
- Saúde: http://localhost:8000/health

## Base histórica de marcas

A pesquisa completa por nome usa os microdados oficiais BADEPI. Baixe o pacote de
marcas disponibilizado pelo INPI, extraia `badepiv11_mrc_deposito.csv` para
`data/raw/badepi` e execute:

```powershell
docker compose exec api /app/.venv/bin/python -m app.cli.importar_badepi_marcas `
  --arquivo data/raw/badepi/badepiv11_mrc_deposito.csv
```

O importador trabalha em lotes e pode ser executado novamente sem duplicar os
processos. A versão 11 do BADEPI cobre os anos de 2000 a 2024.

Para carregar os titulares e países associados às marcas:

```powershell
docker compose exec api /app/.venv/bin/python -m app.cli.importar_badepi_titulares `
  --arquivo data/raw/badepi/badepiv11_mrc_depositante.csv
```

## Atualizações semanais da RPI

Os parsers de marcas e patentes são separados porque os XMLs oficiais têm
estruturas diferentes.

```powershell
docker compose exec api /app/.venv/bin/python -m app.cli.importar_rpi `
  --tipo marca --arquivo data/raw/rpi2897/RM2897.xml --limite 100
docker compose exec api /app/.venv/bin/python -m app.cli.importar_rpi `
  --tipo patente --arquivo data/raw/rpi2897/Patente_2897_14072026.xml --limite 100
```

Remova `--limite` somente depois de validar a edição e o resultado da amostra.

Para sincronizar todas as RPIs de 2025 até a edição 2897, de 14/07/2026:

```powershell
docker compose exec api /app/.venv/bin/python -m app.cli.sincronizar_rpis `
  --inicio 2818 --fim 2897 --tipo ambos
```

Cada edição concluída fica registrada no banco. Assim, uma nova execução ignora
automaticamente as revistas já importadas.

## Endpoints

- `GET /health`
- `GET /v1/processos?nome={nome}&tipo={marca|patente}`
- `GET /v1/processos/{numero}`
- `POST /v1/leads`

## Painel interno de leads

A consulta é gratuita e não exige cadastro. Após uma busca, o visitante pode
solicitar contato de um especialista informando nome, e-mail e telefone; esse
envio é opcional e independente da pesquisa. Os leads são armazenados no
PostgreSQL e podem ser acompanhados em:

- Painel: http://localhost:8000/admin/leads
- Aviso de privacidade: http://localhost:8000/privacidade

Defina `ADMIN_USERNAME` e `ADMIN_PASSWORD` no arquivo `.env` antes de publicar
o sistema. Em ambiente público, use HTTPS e substitua o canal de privacidade do
aviso pelos dados reais do responsável pela plataforma.

A busca considera o nome da marca, o título da patente e o nome do titular, sem
diferenciar maiúsculas, minúsculas ou acentuação. Os números de processo também
são normalizados; `BR 10 2013 010193-1` pode ser consultado como
`BR1020130101931`.

## Desenvolvimento

```powershell
uv sync --python 3.11
uv run pytest
uv run ruff check .
uv run uvicorn app.main:app --reload
```
