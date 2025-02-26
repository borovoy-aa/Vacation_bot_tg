import logging
import os
from telegram import Update
from telegram.ext import Application, ContextTypes
from dotenv import load_dotenv
from handlers.employee_handler import (
    registration_handler, add_vacation_handler, edit_vacation_handler, delete_employee_handler,
    list_employees_handler, stats_handler, export_employees_handler, notify_handler,
    invalid_command_handler, delete_vacation_handler, random_text_handler, clear_all_employees_handler,
    set_initial_commands, set_full_commands, my_vacations_handler
)
from handlers.notification_handler import setup_notifications, test_notifications_handler
from database.db_operations import create_tables

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('bot.log')
    ]
)
logger = logging.getLogger(__name__)

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    error_msg = f"Произошла ошибка: {context.error}"
    logger.error(error_msg, exc_info=True)
    admin_id = os.getenv('ADMIN_ID')
    if admin_id:
        try:
            await context.bot.send_message(chat_id=int(admin_id), text=error_msg[:4000])
        except Exception as e:
            logger.error(f"Не удалось отправить уведомление админу: {e}", exc_info=True)
    if update and update.message:
        await update.message.reply_text("Произошла ошибка. Обратитесь к @Admin.")

def main() -> None:
    load_dotenv()
    token = os.getenv('TELEGRAM_TOKEN')
    group_chat_id = os.getenv('GROUP_CHAT_ID')
    admin_id = os.getenv('ADMIN_ID')

    if not all([token, group_chat_id, admin_id]):
        missing = [var for var, val in {'TELEGRAM_TOKEN': token, 'GROUP_CHAT_ID': group_chat_id, 'ADMIN_ID': admin_id}.items() if not val]
        logger.error(f"Отсутствуют обязательные переменные окружения: {', '.join(missing)}")
        return

    try:
        create_tables()
        logger.info("База данных успешно инициализирована.")
    except Exception as e:
        logger.error(f"Ошибка при инициализации базы данных: {e}", exc_info=True)
        return

    try:
        application = Application.builder().token(token).build()
    except Exception as e:
        logger.error(f"Ошибка при создании приложения: {e}", exc_info=True)
        return

    logger.info("Регистрация обработчиков...")
    application.add_handler(registration_handler)
    application.add_handler(add_vacation_handler)
    application.add_handler(edit_vacation_handler)
    application.add_handler(delete_vacation_handler)
    application.add_handler(delete_employee_handler)
    application.add_handler(clear_all_employees_handler)
    application.add_handler(notify_handler)
    application.add_handler(test_notifications_handler)
    application.add_handler(list_employees_handler)
    application.add_handler(stats_handler)
    application.add_handler(export_employees_handler)
    application.add_handler(my_vacations_handler)  # Новая строка
    application.add_handler(invalid_command_handler)
    application.add_handler(random_text_handler)
    logger.info("Все обработчики зарегистрированы.")

    application.add_error_handler(error_handler)

    try:
        setup_notifications(application, group_chat_id, int(admin_id))
        logger.info("Уведомления настроены.")
    except Exception as e:
        logger.error(f"Ошибка при настройке уведомлений: {e}", exc_info=True)

    async def send_welcome(context: ContextTypes.DEFAULT_TYPE):
        admin_id = int(os.getenv('ADMIN_ID'))
        message = (
            "👋 Бот успешно запущен!\n\n"
            "Я готов к работе. Используйте /start для регистрации или доступа к командам."
        )
        try:
            await context.bot.send_message(chat_id=admin_id, text=message)
            logger.info(f"Приветственное сообщение отправлено админу {admin_id}")
        except Exception as e:
            logger.error(f"Ошибка при отправке приветствия админу {admin_id}: {e}", exc_info=True)

    application.job_queue.run_once(send_welcome, when=1)
    application.job_queue.run_once(set_initial_commands, when=0)

    logger.info("Запуск бота...")
    try:
        application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)
    except Exception as e:
        logger.error(f"Ошибка при запуске бота: {e}", exc_info=True)
        raise

if __name__ == '__main__':
    main()