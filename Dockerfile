FROM python:3.11-slim

ARG OCI_VERSION=dev
ARG OCI_REVISION=unknown
ARG OCI_SOURCE=https://github.com/estelledc/bj-pal

LABEL org.opencontainers.image.title="BJ-Pal" \
    org.opencontainers.image.description="Evidence-driven Beijing short-trip planning API" \
    org.opencontainers.image.source="${OCI_SOURCE}" \
    org.opencontainers.image.version="${OCI_VERSION}" \
    org.opencontainers.image.revision="${OCI_REVISION}" \
    org.opencontainers.image.licenses="NOASSERTION"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    BJ_PAL_LLM=mock

WORKDIR /app

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY . .
RUN python scripts/build_mock_data.py --profile demo \
    && python src/loader.py \
    && groupadd --system --gid 10001 bjpal \
    && useradd --system --uid 10001 --gid 10001 --create-home bjpal \
    && mkdir -p runtime \
    && chown -R bjpal:bjpal /app

USER 10001:10001

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).read()"]

CMD ["python", "-m", "uvicorn", "http_api.app:app", "--app-dir", "src", "--host", "0.0.0.0", "--port", "8000"]
