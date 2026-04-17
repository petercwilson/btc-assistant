FROM python:3.12-slim

WORKDIR /app

# Install dependencies first for better layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py .

# signals.db and any other runtime files are written to /app/data
# (override DB_PATH=/app/data/signals.db via environment or docker-compose)
RUN mkdir -p /app/data

CMD ["python", "bot.py"]
