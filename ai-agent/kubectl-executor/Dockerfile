ARG BASE_IMAGE_PREFIX=""
FROM ${BASE_IMAGE_PREFIX}python:3.12-slim
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates && \
    curl -fsSL "https://dl.k8s.io/release/v1.31.0/bin/linux/amd64/kubectl" \
      -o /usr/local/bin/kubectl && \
    chmod +x /usr/local/bin/kubectl && \
    apt-get purge -y curl && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .
CMD ["python", "app.py"]
