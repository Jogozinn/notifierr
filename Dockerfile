FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements-cloud.txt .
RUN pip install --no-cache-dir --disable-pip-version-check -r requirements-cloud.txt \
    && groupadd --system notifierr && useradd --system --gid notifierr notifierr

COPY --chown=notifierr:notifierr backend ./backend
COPY --chown=notifierr:notifierr alembic ./alembic
COPY --chown=notifierr:notifierr alembic.ini ./alembic.ini

USER notifierr
EXPOSE 8000
CMD ["python", "-m", "backend.api"]
