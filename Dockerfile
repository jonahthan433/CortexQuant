FROM alpine/git:2.47.2 AS kronos-src
ARG KRONOS_COMMIT=67b630e67f6a18c9e9be918d9b4337c960db1e9a
RUN git init /opt/kronos && cd /opt/kronos && git remote add origin https://github.com/shiyu-coder/Kronos.git && git fetch --depth=1 origin ${KRONOS_COMMIT} && git checkout FETCH_HEAD

FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HF_HOME=/app/data/huggingface
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir torch==2.4.1 --index-url https://download.pytorch.org/whl/cpu && pip install --no-cache-dir -r requirements.txt
COPY --from=kronos-src /opt/kronos /opt/kronos
ENV PYTHONPATH=/app:/opt/kronos
COPY . .
RUN useradd -m -u 1000 trader && mkdir -p /app/data/telegram_session /app/logs && chown -R trader:trader /app
USER trader
HEALTHCHECK --interval=30s --timeout=5s --start-period=180s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health',timeout=3)" || exit 1
CMD ["python", "-m", "src.main"]
