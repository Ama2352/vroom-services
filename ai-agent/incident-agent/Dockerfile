FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends gcc && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download model into image — no runtime download, no HuggingFace dependency at startup
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')"

COPY . .

# Seed source baked into image (same pattern as runbook-retriever)
COPY runbooks/ /docs/

EXPOSE 5002
CMD ["python", "app.py"]
