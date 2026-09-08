FROM python:3.12-slim-bookworm AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.lock /app/requirements.lock
RUN pip install --no-cache-dir -r requirements.lock
RUN groupadd -g 10001 social && useradd -u 10001 -g social --no-create-home social \
    && mkdir -p /app/data/staging && chown -R social:social /app
COPY --chown=social:social app /app/app
COPY --chown=social:social migrations /app/migrations
COPY --chown=social:social scripts /app/scripts
COPY --chown=social:social gunicorn.conf.py /app/gunicorn.conf.py
USER 10001:10001
EXPOSE 8000
CMD ["gunicorn", "--config", "gunicorn.conf.py", "app:create_app()"]

FROM base AS test
USER root
COPY requirements-dev.lock /app/requirements-dev.lock
RUN pip install --no-cache-dir -r requirements-dev.lock
COPY --chown=social:social tests /app/tests
COPY --chown=social:social pyproject.toml /app/pyproject.toml
USER 10001:10001
CMD ["pytest", "-q"]

FROM base AS production
