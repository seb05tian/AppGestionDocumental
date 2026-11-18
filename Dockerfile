FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PRIVATE_MEDIA_ROOT=/data/private \
    TESSDATA_PREFIX=/usr/share/tesseract-ocr/5/tessdata

RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng tesseract-ocr-spa \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# Collects CSS/JS into staticfiles/ so WhiteNoise can serve them when DEBUG=0.
RUN DEBUG=1 python manage.py collectstatic --noinput \
    && useradd --create-home --uid 10001 app \
    && mkdir -p /data/private \
    && chown -R app:app /app /data/private
USER app

EXPOSE 8000
