# Configuratore di mediabox: genera i file e configura i servizi via API.
ARG BASE_IMAGE=docker.io/library/python:3.13-slim
FROM ${BASE_IMAGE}
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY configuratore ./configuratore
ENTRYPOINT ["python", "-m", "configuratore"]
