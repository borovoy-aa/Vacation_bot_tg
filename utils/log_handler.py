import os
import logging
from telegram import Update
from telegram.ext import CommandHandler, ContextTypes
from utils.helpers import is_admin

logger = logging.getLogger(__name__)

async def get_logs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Выгрузка последних логов (для админа)."""
    if not is_admin(update.effective_chat.id):
        await update.message.reply_text("Эта команда доступна только администратору.")
        return
    try:
        num_messages = int(context.args[0]) if context.args else 10
        if num_messages < 1 or num_messages > 50:
            await update.message.reply_text("Введите число от 1 до 50.")
            return
        user_logs = []
        if os.path.exists("user_logs.log") and os.stat("user_logs.log").st_size > 0:
            with open("user_logs.log", "r", encoding="utf-8") as file:
                user_logs = file.readlines()[-num_messages:]
        bot_logs = []
        if os.path.exists("bot_responses.log") and os.stat("bot_responses.log").st_size > 0:
            with open("bot_responses.log", "r", encoding="utf-8") as file:
                bot_logs = file.readlines()[-num_messages:]
        combined_logs = user_logs + bot_logs
        combined_logs.sort()
        log_file_path = "last_logs.txt"
        with open(log_file_path, "w", encoding="utf-8") as file:
            file.writelines(combined_logs)
        with open(log_file_path, "rb") as file:
            await update.message.reply_document(file, caption=f"Последние {num_messages} сообщений.")
        os.remove(log_file_path)
    except FileNotFoundError:
        await update.message.reply_text("Логи не найдены.")
    except Exception as e:
        logger.error(f"Ошибка при выгрузке логов: {e}")
        await update.message.reply_text("Произошла ошибка при выгрузке логов.")

get_logs_handler = CommandHandler('get_logs', get_logs)