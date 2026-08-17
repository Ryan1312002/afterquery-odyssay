FROM python:3.12-slim-bookworm

WORKDIR /app

RUN pip install --no-cache-dir pytest==8.3.4 setuptools==75.6.0

COPY . /app

RUN pip install --no-cache-dir -e /app

ENV PYTHONPATH=/app
ENV PYTHONDONTWRITEBYTECODE=1
