# Стандартные библиотеки
import logging
import pandas as pd
from datetime import datetime
from typing import Optional, List, Tuple

# Сторонние библиотеки
from telegram import Update
from telegram.ext import ContextTypes, CommandHandler, MessageHandler, filters
from telegram.constants import ParseMode  # Импорт ParseMode для Markdown

# Локальные модули
from database.db_operations import add_employee_to_db, add_vacation, employee_exists, check_vacation_overlap
from utils.helpers import is_admin

logger = logging.getLogger(__name__)

# Функции валидации
def validate_date(date_str: str) -> bool:
    """Проверка валидности формата даты."""
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
        return True
    except ValueError:
        return False

def validate_future_date(date_str: str) -> bool:
    """Проверка, что дата в будущем."""
    try:
        date = datetime.strptime(date_str, "%Y-%m-%d")
        return date > datetime.now()
    except ValueError:
        return False

async def upload_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка команды /upload_file для загрузки CSV файла (только для админа)."""
    if not is_admin(update.effective_chat.id):
        await update.message.reply_text("Эта команда доступна только администратору.")
        return

    if not update.message.document:
        await update.message.reply_text("Пожалуйста, загрузите CSV файл с данными сотрудников.")
        return

    file_id = update.message.document.file_id
    file = await context.bot.get_file(file_id)
    downloaded_file = await file.download_to_memory()

    try:
        df = pd.read_csv(downloaded_file)
        required_columns = {'name', 'start_date', 'end_date'}
        if not all(col in df.columns for col in required_columns):
            await update.message.reply_text("CSV файл должен содержать колонки: name, start_date, end_date, optional replacement_username.")
            return

        success_count = 0
        error_messages = []

        for _, row in df.iterrows():
            name = str(row['name']).strip()
            start_date = str(row['start_date']).strip()
            end_date = str(row['end_date']).strip()
            replacement_username = str(row.get('replacement_username', '')).strip() if 'replacement_username' in row else None

            # Валидация дат
            if not validate_date(start_date) or not validate_date(end_date):
                error_messages.append(f"Некорректный формат даты для {name}: {start_date} или {end_date}")
                continue
            if not validate_future_date(start_date) or not validate_future_date(end_date):
                error_messages.append(f"Дата должна быть в будущем для {name}: {start_date} или {end_date}")
                continue
            if datetime.strptime(end_date, "%Y-%m-%d") <= datetime.strptime(start_date, "%Y-%m-%d"):
                error_messages.append(f"Дата окончания должна быть позже даты начала для {name}: {start_date} - {end_date}")
                continue

            # Добавление сотрудника
            user_id = update.effective_user.id  # Используем user_id текущего пользователя как временное решение
            new_user_id = add_employee_to_db(name, None, user_id)
            if not new_user_id:
                error_messages.append(f"Ошибка при добавлении сотрудника: {name}")
                continue

            # Добавление отпуска
            if check_vacation_overlap(new_user_id, start_date, end_date):
                error_messages.append(f"Отпуск пересекается для {name}: {start_date} - {end_date}")
                continue

            if add_vacation(new_user_id, start_date, end_date, replacement_username):
                success_count += 1
            else:
                error_messages.append(f"Ошибка при добавлении отпуска для {name}")

        response = "*ОБРАБОТКА ЗАВЕРШЕНА. УСПЕШНО ДОБАВЛЕНО: " + str(success_count) + " ЗАПИСЕЙ.*\n"
        if error_messages:
            response += "*ОШИБКИ:*\n" + "\n".join(error_messages)
        await update.message.reply_text(response, parse_mode=ParseMode.MARKDOWN)

    except Exception as e:
        logger.error(f"Ошибка при обработке CSV файла: {e}")
        await update.message.reply_text("Произошла ошибка при обработке файла. Проверьте формат CSV и попробуйте снова.")

async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка загруженного файла (только для админа)."""
    if not is_admin(update.effective_chat.id):
        await update.message.reply_text("Эта команда доступна только администратору.")
        return
    await upload_file(update, context)

# Обработчики
upload_file_handler = CommandHandler('upload_file', upload_file)
handle_file_handler = MessageHandler(filters.Document.ALL & ~filters.COMMAND, handle_file)