# Adapter addition: single-container deploy (built SPA served by FastAPI).
FROM node:22-slim AS web
WORKDIR /app
RUN corepack enable
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY index.html vite.config.ts tsconfig*.json components.json eslint.config.js ./
COPY public ./public
COPY src ./src
ENV VITE_API_BASE_URL=""
RUN pnpm build

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 && rm -rf /var/lib/apt/lists/*
WORKDIR /srv
COPY backend/pyproject.toml backend/uv.lock backend/
RUN cd backend && uv sync --frozen --no-dev --extra agent --extra langextract
COPY backend backend
COPY --from=web /app/dist dist
ENV PUBLIC_MODE=true OCR_DEFAULT_ENGINE=pdf-text STRUCTURING_PROVIDER=recorded DECISION_PROVIDER=rules
WORKDIR /srv/backend
CMD ["sh", "-c", "exec uv run --no-sync uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
