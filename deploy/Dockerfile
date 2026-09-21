# Образ ИИ-анализатора диалогов (пакет crmai)
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements-crmai.txt ./
RUN pip install --no-cache-dir -r requirements-crmai.txt

COPY crmai ./crmai
COPY prompts ./prompts

# База и токены живут в томе: контейнер пересоздаётся без потери истории
VOLUME ["/data"]
ENV CRM_DB_PATH=/data/crm_analyzer.db AMO_TOKEN_FILE=/data/amo_tokens.json

EXPOSE 8080
CMD ["python", "-m", "crmai", "serve"]
