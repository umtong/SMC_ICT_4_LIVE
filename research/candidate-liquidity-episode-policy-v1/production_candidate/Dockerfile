# syntax=docker/dockerfile:1.7

FROM python:3.13-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_PROJECT_ENVIRONMENT=/opt/lep/.venv \
    VIRTUAL_ENV=/opt/lep/.venv \
    PATH=/opt/lep/.venv/bin:/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin

WORKDIR /opt/lep

COPY pyproject.toml uv.lock README.md ./
COPY src ./src

RUN python -m pip install --no-cache-dir "uv==0.11.25" \
    && uv lock --check \
    && uv sync --locked \
    && python -c "from importlib.metadata import version; assert version('nautilus_trader') == '1.230.0'" \
    && mkdir -p /var/lib/lep

VOLUME ["/var/lib/lep"]
ENTRYPOINT ["lep"]
CMD ["verify", "--build-node", "--state", "/var/lib/lep/verify.sqlite", "--output", "/var/lib/lep/verify.json"]
