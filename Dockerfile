FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# SSL self-signed certs (for Cloudflare Full mode)
ENV SSL_KEYFILE=/app/key.pem
ENV SSL_CERTFILE=/app/cert.pem

EXPOSE 10842

CMD ["python", "main.py"]