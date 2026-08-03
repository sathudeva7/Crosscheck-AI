# CrossCheck API.
#
# Written for Railway and Render, but nothing here is specific to either: both
# hand the container a $PORT to bind and expect the process to stay in the
# foreground.

FROM python:3.13-slim

# Bytecode files and buffered output help nobody in a container, and unbuffered
# stdout is what makes the host's log tail useful while a request is running.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

# Dependencies before source, so an edit to the application does not invalidate
# the layer that took minutes to install.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY migrations ./migrations

# Uploaded documents. Mount a volume here to keep them across deploys — without
# one the originals are lost on every restart. The extracted facts live in
# Postgres and survive either way; what goes missing is the source file behind
# a page, and any document still mid-ingestion when the container stopped.
ENV UPLOAD_DIR=/data/uploads
RUN mkdir -p /data/uploads && useradd --create-home crosscheck \
    && chown -R crosscheck /data /srv
USER crosscheck

EXPOSE 8000

# One worker, deliberately. Ingestion runs as a FastAPI background task inside
# the serving process, so a second worker would not share it — and every worker
# would race the others to apply migrations at startup.
#
# Shell form, because $PORT has to be expanded at run time; the host assigns it.
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1
