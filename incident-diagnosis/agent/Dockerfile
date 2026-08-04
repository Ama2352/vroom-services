FROM python:3.12-slim AS model
WORKDIR /build
ENV PYTHONPATH=/build
COPY requirements.txt .
RUN pip install --no-cache-dir "huggingface-hub>=0.36.2,<1.0"
COPY retrieval/ retrieval/
COPY scripts/ scripts/
RUN python scripts/fetch_reranker_model.py --output /opt/models/minilm

FROM python:3.12-slim AS runtime
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
COPY runbooks/ /docs/
COPY --from=model /opt/models/minilm /opt/models/minilm

FROM runtime AS verification
COPY requirements-test.txt .
RUN pip install --no-cache-dir -r requirements-test.txt
RUN python -m pytest tests/test_app.py tests/test_interpreter.py tests/test_retrieval_*.py -q
RUN python -c "from pathlib import Path; from retrieval.reranker import load_model_spec, OnnxCrossEncoder; s=load_model_spec(Path('retrieval/model_manifest.json')); OnnxCrossEncoder(Path('/opt/models/minilm'), s); print('reranker artifact verified')"
RUN RERANKER_MODEL_DIR=/opt/models/minilm python -m pytest tests/test_retrieval_benchmark.py -q -m model

FROM runtime AS production
EXPOSE 5002
CMD ["python", "-u", "app.py"]
