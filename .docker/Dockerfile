FROM python:3.11-slim

# curl is required by the compose healthcheck
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

# Copy project definition first for layer caching
COPY pyproject.toml .python-version uv.lock ./

# Install dependencies into the project venv (no dev extras)
RUN uv sync --no-dev --frozen

COPY gateway/ gateway/

CMD ["uv", "run", "uvicorn", "gateway.main:app", "--host", "0.0.0.0", "--port", "8182"]
