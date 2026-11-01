FROM python:3.12-slim
RUN pip install --no-cache-dir mutmut==2.5.1 pytest==8.4.2
ENV PYTHONUNBUFFERED=1 PYTHONUTF8=1 PYTHONIOENCODING=utf-8
ARG PROJECT_DEPENDENCIES=[]
RUN python -c "import json,os,subprocess,sys; deps=json.loads(os.environ['PROJECT_DEPENDENCIES']); subprocess.check_call([sys.executable,'-m','pip','install',*deps]) if deps else None"
WORKDIR /workspace
CMD ["python", "-m", "specmind_prep.mutants.worker", "--help"]
