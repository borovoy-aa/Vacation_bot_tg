FROM python:3.10-slim

# Установка sqlite3
RUN apt-get update && apt-get install -y sqlite3 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && pip list | grep telegram

COPY . .

RUN mkdir -p /app/uploaded_files

CMD ["python", "main.py"]