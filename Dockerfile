FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Create data directory
RUN mkdir -p /data/logs /data/memory

ENV TRADING_MODE=paper
ENV DATA_DIR=/data
ENV PORT=8080

CMD ["python", "main.py"]
