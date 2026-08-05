# syntax=docker/dockerfile:1.7

FROM mcr.microsoft.com/devcontainers/python:1-3.13-bookworm

ARG UV_VERSION=0.11.25

LABEL org.opencontainers.image.source="https://github.com/umtong/SMC_ICT_4_LIVE" \
      org.opencontainers.image.description="Prebuilt NautilusTrader research environment for SMC/ICT 4"

ENV DEBIAN_FRONTEND=noninteractive \
    UV_PROJECT_ENVIRONMENT=/opt/smc4/.venv \
    VIRTUAL_ENV=/opt/smc4/.venv \
    PATH=/opt/smc4/.venv/bin:/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SMC4_PREBUILT_ENV=1

USER root

# The upstream devcontainer image may retain a Yarn APT entry whose signing key
# rotates independently. Yarn is not used here, so remove that unrelated source
# before installing the small shared tool set.
RUN find /etc/apt/sources.list.d -maxdepth 1 -type f -iname '*yarn*' -delete \
    && apt-get update \
    && apt-get install -y --no-install-recommends make jq \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip install --no-cache-dir "uv==${UV_VERSION}"

WORKDIR /opt/smc4/source

COPY pyproject.toml uv.lock README.md ./
COPY src ./src

RUN uv lock --check \
    && uv sync --locked \
    && /opt/smc4/.venv/bin/python -c "from importlib.metadata import version; assert version('nautilus_trader') == '1.230.0'" \
    && /opt/smc4/.venv/bin/smc4 doctor \
    && chown -R vscode:vscode /opt/smc4

USER vscode
WORKDIR /workspaces/SMC_ICT_4_LIVE

CMD ["sleep", "infinity"]
