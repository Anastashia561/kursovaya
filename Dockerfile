FROM python:3.12-slim
WORKDIR /opt/ubi193
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd --uid 10001 --create-home app
COPY common ./common
COPY services ./services
COPY scripts ./scripts
USER 10001
EXPOSE 8081 8082 8083 8000
CMD ["sh", "-c", "exec uvicorn services.entrypoint:create_app --factory --host 0.0.0.0 --port ${PORT:-8081}"]
