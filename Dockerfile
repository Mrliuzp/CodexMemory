FROM python:3.12-slim

WORKDIR /app
COPY . /app
RUN pip install --no-cache-dir .

ENV PYTHONUNBUFFERED=1
CMD ["uvicorn", "codex_memory.v1_app:app", "--host", "0.0.0.0", "--port", "8000"]
