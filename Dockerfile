# ---------------------------------------------------------------------------
# Stage 1 — build the static frontend with Node.
# ---------------------------------------------------------------------------
FROM node:22-alpine AS frontend-build
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---------------------------------------------------------------------------
# Stage 2 — Python runtime with uv + Node (for the Memory MCP via npx).
# ---------------------------------------------------------------------------
FROM python:3.14-slim-trixie AS runtime

# uv binary from the official image.
COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /usr/local/bin/uv

# Node.js for `npx -y @modelcontextprotocol/server-memory`.
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates git \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python deps (cached when pyproject/uv.lock unchanged).
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen

# Massive MCP installed as a uv tool — same as on macOS dev.
RUN uv tool install --no-cache "mcp_massive @ git+https://github.com/massive-com/mcp_massive@v0.9.1"
ENV PATH="/root/.local/bin:${PATH}"

# Backend source.
COPY backend/ ./backend/

# Built frontend (the FastAPI app mounts this dir at /).
COPY --from=frontend-build /app/frontend/dist ./frontend_dist

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "--factory", "backend.api.app:create_app", \
     "--host", "0.0.0.0", "--port", "8000"]
