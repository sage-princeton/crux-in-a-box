FROM python:3.12-slim@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f
WORKDIR /app
COPY requirements.txt .
RUN apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir --upgrade pip==26.2.1 \
    && pip install --no-cache-dir -r requirements.txt \
    && pip uninstall --yes pip \
    && rm -rf /usr/local/lib/python3.12/ensurepip \
    && useradd --uid 10001 --create-home monitor
COPY review.py worker.py fleet.py lifecycle.py ./
COPY status_worker.py status_review.py status_store.py ./
COPY prompts ./prompts
USER 10001
CMD ["python", "worker.py", "discover"]
