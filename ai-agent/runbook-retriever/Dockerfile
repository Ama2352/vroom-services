ARG BASE_IMAGE_PREFIX=""
FROM ${BASE_IMAGE_PREFIX}python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .
COPY runbooks /docs
CMD ["python", "app.py"]
