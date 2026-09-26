# One image for the web app and the ingestion CLI.
#   docker compose up -d                                   # app on :8000, Postgres alongside
#   docker compose run --rm app budget ingest-all --publish
FROM node:22-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml alembic.ini ./
COPY pipeline ./pipeline
COPY api ./api
COPY config ./config
COPY sources ./sources
# editable install: the pipeline reads config/ and sources/ relative to the source tree
RUN pip install --no-cache-dir -e .
COPY --from=web /web/dist ./web/dist
ENV PORT=8000
EXPOSE 8000
# ACCESS_TOKEN must be set (see api/access.py); the app refuses to serve data without it
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn api.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
