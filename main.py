import logging
import os
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from dotenv import load_dotenv
from handlers.employee_handler import (
    add_vacation_handler, edit_vacation_handler, delete_employee_handler, list_employees_handler,
    stats_handler, export_employees_handler, notify_handler, invalid_command_handler,
    set_bot_commands, delete_vacation_handler, random_text_handler, clear_all_employees_handler
)
from handlers.notification_handler import setup_notifications
from database.db_operations import create_tables  # Импортируем только create_tables

logger = logging.getLogger(__name__)

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка ошибок."""
    logger.error(f"Произошла ошибка: {context.error}", exc_info=True)
    if update and update.message:
        await update.message.reply_text("Произошла ошибка. Обратитесь к @Admin.")

def main() -> None:
    load_dotenv()
    token = os.getenv('TELEGRAM_TOKEN')
    if not token:
        logger.error("Токен бота не найден в .env")
        return

    # Создаем таблицы при запуске
    create_tables()
    logger.info("База данных готова к работе.")

    application = Application.builder().token(token).build()

    # Добавляем обработчики
    application.add_handler(add_vacation_handler)
    application.add_handler(edit_vacation_handler)
    application.add_handler(delete_vacation_handler)
    application.add_handler(delete_employee_handler)
    application.add_handler(list_employees_handler)
    application.add_handler(stats_handler)
    application.add_handler(export_employees_handler)
    application.add_handler(notify_handler)
    application.add_handler(invalid_command_handler)
    application.add_handler(random_text_handler)
    application.add_handler(clear_all_employees_handler)

    # Обработчик ошибок
    application.add_error_handler(error_handler)

    group_chat_id = os.getenv('GROUP_CHAT_ID')
    admin_id = os.getenv('ADMIN_ID')
    if not group_chat_id or not admin_id:
        logger.error("GROUP_CHAT_ID или ADMIN_ID не найдены в .env")
        return
    setup_notifications(application, group_chat_id, int(admin_id))

    application.job_queue.run_once(set_bot_commands, when=0)

    logger.info("Запуск бота...")
    try:
        application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)
    except Exception as e:
        logger.error(f"Ошибка при запуске бота: {e}", exc_info=True)

if __name__ == '__main__':
    main()