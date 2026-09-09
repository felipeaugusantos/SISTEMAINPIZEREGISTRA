FROM python:3.14-slim AS base

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN pip install --no-cache-dir uv

# Estagio "test": inclui pytest/ruff (grupo dev) e a suite de testes, para uso
# via `docker compose run --rm test pytest ...`. Nunca e a imagem publicada.
FROM base AS test

RUN uv sync --frozen

COPY app ./app
COPY migrations ./migrations
COPY tests ./tests
COPY alembic.ini ./alembic.ini

CMD ["uv", "run", "pytest", "-q"]

# Estagio final (default de `docker build`/`docker compose build` sem --target):
# mesma imagem de producao de sempre, sem dependencias de dev.
FROM base AS production

# Commit exato empacotado nesta imagem (Fase 0, item 4) -- consultar com
# `docker inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' <imagem>`.
# Sem --build-arg (ex.: build local avulso) fica "unknown", nunca quebra o build.
ARG GIT_SHA=unknown
LABEL org.opencontainers.image.revision="${GIT_SHA}"
# Fase 7 (painel tecnico): ARG sozinho so existe em build-time -- promovido
# pra ENV aqui pra o processo em execucao conseguir ler seu proprio commit
# (os.environ["GIT_SHA"]), sem precisar de acesso ao socket do Docker.
ENV GIT_SHA="${GIT_SHA}"

RUN uv sync --frozen --no-dev

COPY app ./app
COPY migrations ./migrations
COPY alembic.ini ./alembic.ini
COPY docker-entrypoint.sh ./docker-entrypoint.sh

RUN sed -i 's/\r$//' docker-entrypoint.sh \
    && chmod +x docker-entrypoint.sh \
    && adduser --disabled-password --gecos "" --uid 1000 appuser \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

ENTRYPOINT ["/app/docker-entrypoint.sh"]
