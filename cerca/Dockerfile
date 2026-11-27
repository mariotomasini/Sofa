# Cerca: ricerca unificata dei film per mediabox.
ARG BASE_IMAGE=docker.io/library/python:3.13-slim
FROM ${BASE_IMAGE}
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# ffmpeg: per i siti diretti che danno il video a pezzi (HLS), come StreamingCommunity
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
COPY app ./app
EXPOSE 8000
# Caddy toglie /cerca dall'indirizzo; root-path serve per creare i link giusti.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--root-path", "/cerca", "--proxy-headers", "--forwarded-allow-ips", "*"]
