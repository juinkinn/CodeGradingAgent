FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app

RUN groupadd --system app && useradd --system --gid app --home-dir /app app \
    && mkdir /data \
    && chown app:app /data

COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=app:app backend/app ./app
COPY --chown=app:app backend/migrations ./migrations
COPY --chown=app:app backend/alembic.ini ./alembic.ini

USER app
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
