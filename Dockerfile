FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.12.1 /uv /usr/local/bin/uv
WORKDIR /work
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-install-project
COPY . .
RUN uv sync --frozen
EXPOSE 8077
CMD ["uv", "run", "uvicorn", "freshflow.app:app", "--host", "0.0.0.0", "--port", "8077"]
