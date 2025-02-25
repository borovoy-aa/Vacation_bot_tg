FROM python:3.11-slim

# Установка sqlite3 и очистка кэша
RUN apt-get update && apt-get install -y sqlite3 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Создаём директорию для данных
RUN mkdir -p /app/data

VOLUME /app/data

CMD ["python", "main.py"]