FROM python:3.12-slim

WORKDIR /app

# Install CPU-only PyTorch BEFORE requirements.txt so sentence-transformers does
# not pull the CUDA variant (~2 GB). CPU torch is ~200 MB.
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Bake model into image — no HuggingFace download at runtime
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')"

COPY . .
COPY runbooks/ /docs/

EXPOSE 5002
# -u: unbuffered stdout so kubectl logs shows output immediately
CMD ["python", "-u", "app.py"]
