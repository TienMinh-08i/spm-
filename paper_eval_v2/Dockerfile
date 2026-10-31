FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends git build-essential && rm -rf /var/lib/apt/lists/*
WORKDIR /opt/evaluator
COPY . /opt/evaluator
RUN pip install --no-cache-dir -r paper_eval_v2/requirements.lock && pip install --no-cache-dir --no-deps .
ENV PYTHONUNBUFFERED=1 PYTHONUTF8=1 PYTHONIOENCODING=utf-8
WORKDIR /workspace
ENTRYPOINT ["python", "-m", "specmind_eval"]
