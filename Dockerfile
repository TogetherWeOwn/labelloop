FROM ghcr.io/astral-sh/uv:0.13.6@sha256:af22a52f5eafef3d78e551599d405c02df61919d18028f0cea8eda93bc9986d0 AS uv
FROM docker.io/library/python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv
ENV PATH="/opt/venv/bin:$PATH"
WORKDIR /app
COPY --from=uv /uv /uvx /bin/
COPY pyproject.toml uv.lock ./
COPY packages packages
COPY apps/api apps/api
COPY workers workers

ARG UV_GROUPS=runtime
RUN uv sync --frozen --only-group ${UV_GROUPS} --no-install-project --no-install-workspace && \
    uv pip install --no-deps --no-editable ./packages/core && \
    if [ "${UV_GROUPS}" = "ml" ]; then \
      uv pip install --no-deps --no-editable ./workers/ml; \
    else \
      uv pip install --no-deps --no-editable \
        ./packages/compiler ./packages/platform ./packages/llm \
        ./apps/api ./workers/core; \
    fi

CMD ["labelloop-api"]
