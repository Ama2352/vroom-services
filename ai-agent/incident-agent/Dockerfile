FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Bake ONNX model into image — no download at runtime
RUN python -c "from fastembed import TextEmbedding; TextEmbedding('sentence-transformers/all-MiniLM-L6-v2')"

COPY . .
COPY runbooks/ /docs/

EXPOSE 5002
CMD ["python", "-u", "app.py"]
