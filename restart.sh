#!/bin/bash

# Переход в директорию проекта (если нужно, скорректируй путь)
cd ~/vacation_bot

echo "Очищаем образы и кэш..."
docker system prune -f

# Остановка и удаление текущего контейнера
echo "Останавливаем текущий контейнер..."
docker-compose down

# Пересборка и запуск
echo "Пересобираем и запускаем контейнер..."
docker-compose up --build -d

# Подключение к логам
echo "Запускаем просмотр логов..."
docker-compose logs --follow vacation-bot