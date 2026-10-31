FROM python:3.12-slim
RUN pip install --no-cache-dir mutmut==2.5.1 pytest==8.4.2
ENV PYTHONUNBUFFERED=1 PYTHONUTF8=1 PYTHONIOENCODING=utf-8
WORKDIR /workspace
CMD ["python", "-m", "specmind_prep.mutants.worker", "--help"]
