FROM node:22-bookworm-slim AS frontend
WORKDIR /build
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY scripts/build_frontend.mjs ./scripts/build_frontend.mjs
COPY prototype ./prototype
RUN npm run build

FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UPKINSEY_HOST=0.0.0.0 \
    UPKINSEY_STATIC_DIR=/app/frontend-dist \
    UPKINSEY_DATA_DIR=/app/data \
    PORT=5173
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
COPY scripts ./scripts
COPY --from=frontend /build/frontend-dist ./frontend-dist
RUN python -m pip install --no-cache-dir '.[persona]' \
    && groupadd --gid 10001 upkinsey \
 && useradd --create-home --uid 10001 --gid upkinsey upkinsey \
    && mkdir -p /app/data \
    && chown upkinsey:upkinsey /app/data
USER upkinsey
EXPOSE 5173
CMD ["upkinsey"]
