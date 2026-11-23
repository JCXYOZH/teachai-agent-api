# TeachAI Agent API 后端生产镜像。
# 基础镜像标签与约定的运行时保持一致（Python 3.8.9 / py3.8-slim）。
FROM python:3.8-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# 固定非 root 身份：挂载的素材目录需一次性 chown 到 10001:10001。
RUN groupadd --gid 10001 app \
 && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin app

COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY . .
RUN chmod 0755 scripts/docker-entrypoint.sh \
 && chown -R app:app /app

USER app
EXPOSE 65534

# 仅做存活探针；数据库与存储的就绪状态由运维探针和 /health/ready 检查。
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request; raise SystemExit(0 if urllib.request.urlopen('http://127.0.0.1:65534/api/v1/health', timeout=3).getcode() == 200 else 1)"

ENTRYPOINT ["/app/scripts/docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "65534", "--proxy-headers"]
