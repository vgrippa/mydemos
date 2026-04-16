FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        postgresql-client \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir psycopg2-binary==2.9.9

WORKDIR /demo
ENTRYPOINT []
CMD ["bash"]
