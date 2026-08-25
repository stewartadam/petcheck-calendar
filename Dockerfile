FROM python:3.12-slim-trixie

ENV PATH="/opt/venv/bin:${PATH}" \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /app

RUN pip install --no-cache-dir uv==0.12.5

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project --no-cache \
    && playwright install --with-deps --only-shell chromium \
    && rm -rf /var/lib/apt/lists/*

COPY src ./src
RUN uv sync --frozen --no-dev --no-cache \
    && groupadd --gid 1001 pwuser \
    && useradd --uid 1001 --gid pwuser --create-home pwuser \
    && mkdir -p /app/data /app/diagnostics \
    && chown -R pwuser:pwuser /app/data /app/diagnostics

USER pwuser

EXPOSE 8765

CMD ["petcheck-calendar", "serve"]
