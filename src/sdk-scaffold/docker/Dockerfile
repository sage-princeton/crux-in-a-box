# The scaffold and the product-change demo in one image, for running the demo end to end on a laptop.
# Build context: src/sdk-scaffold. See README.md, "Run the demo in Docker".
FROM python:3.12-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git nodejs npm \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /opt/crux-sdk-scaffold/requirements.txt
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir -r /opt/crux-sdk-scaffold/requirements.txt

RUN useradd --create-home crux && install -d -o crux /work
USER crux
RUN npm config set prefer-offline true && npx -y slack-mcp-server@1.3.0 --help >/dev/null

COPY crux_scaffold /opt/crux-sdk-scaffold/crux_scaffold
COPY examples/product-change /opt/crux-sdk-scaffold/examples/product-change
COPY docker/entrypoint.sh /usr/local/bin/crux-demo

ENV PATH=/opt/venv/bin:$PATH PYTHONPATH=/opt/crux-sdk-scaffold PYTHONUNBUFFERED=1
WORKDIR /work
VOLUME /work
ENTRYPOINT ["crux-demo"]
CMD ["run"]
