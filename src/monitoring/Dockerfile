FROM python:3.12-slim@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd --uid 10001 --create-home monitor
COPY review.py worker.py incidents.py ./
COPY prompts ./prompts
USER 10001
CMD ["python", "worker.py", "discover"]
