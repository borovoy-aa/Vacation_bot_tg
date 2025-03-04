import logging
import os
from telegram import BotCommand, Update
from telegram.ext import Application, ContextTypes
from dotenv import load_dotenv
from handlers.employee_handler import (
    registration_handler, add_vacation_handler, edit_vacation_handler, delete_vacation_handler,
    delete_employee_handler, clear_all_employees_handler, notify_handler, list_employees_handler,
    stats_handler, export_vacations_handler, export_employees_handler, my_vacations_handler,
    invalid_command_handler, random_text_handler, error_handler
)
from handlers.notification_handler import setup_notifications, test_notifications_handler
from database.db_operations import create_tables

# Настройка логирования
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
    """Обработчик ошибок с уведомлением администраторов."""
    error_msg = f"Произошла ошибка: {context.error}"
    logger.error(error_msg, exc_info=True)
    admin_ids = [int(id.strip()) for id in os.getenv('ADMIN_IDS', '').split(',') if id.strip()]
    for admin_id in admin_ids:
        try:
            await context.bot.send_message(chat_id=admin_id, text=error_msg[:4000])
        except Exception as e:
            logger.error(f"Не удалось отправить уведомление админу {admin_id}: {e}", exc_info=True)
    if update and update.message:
        await update.message.reply_text("Произошла ошибка. Обратитесь к @Admin.")

async def set_initial_commands(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Установка начальных команд до регистрации."""
    try:
        initial_commands = [
            BotCommand("start", "Зарегистрироваться и начать работу"),
        ]
        await context.bot.set_my_commands(initial_commands, scope={"type": "all_private_chats"})
        logger.info("Начальные команды бота установлены")
    except Exception as e:
        logger.error(f"Ошибка при установке начальных команд: {str(e)}", exc_info=True)

async def set_full_commands(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Установка полного списка команд после регистрации."""
    try:
        public_commands = [
            BotCommand("add_vacation", "Добавить свой отпуск"),
            BotCommand("edit_vacation", "Редактировать отпуск"),
            BotCommand("delete_vacation", "Удалить свой отпуск"),
            BotCommand("notify", "Уведомления"),
            BotCommand("my_vacations", "Мои отпуска"),
        ]
        admin_commands = [
            BotCommand("list_employees", "Список сотрудников"),
            BotCommand("delete_employee", "Удалить сотрудника"),
            BotCommand("stats", "Статистика"),
            BotCommand("export_vacations", "Экспорт отпусков"),
            BotCommand("export_employees", "Экспорт сотрудников"),
            BotCommand("clear_all_employees", "Очистить базу данных"),
        ]
        await context.bot.set_my_commands(public_commands, scope={"type": "all_private_chats"})
        for admin_id in admin_ids:
            await context.bot.set_my_commands(public_commands + admin_commands, scope={"type": "chat", "chat_id": admin_id})
        logger.info("Полный список команд установлен для админов")
    except Exception as e:
        logger.error(f"Ошибка установки команд: {e}", exc_info=True)

def main() -> None:
    """Запуск бота."""
    load_dotenv()
    token = os.getenv('TELEGRAM_TOKEN')
    group_chat_id = os.getenv('GROUP_CHAT_ID')
    admin_ids = [int(id.strip()) for id in os.getenv('ADMIN_IDS', '').split(',') if id.strip()]

    if not all([token, group_chat_id, admin_ids]):
        missing = [var for var, val in {
            'TELEGRAM_TOKEN': token, 'GROUP_CHAT_ID': group_chat_id, 'ADMIN_IDS': admin_ids and ','.join(map(str, admin_ids))
        }.items() if not val]
        logger.error(f"Отсутствуют переменные окружения: {', '.join(missing)}")
        return

    try:
        create_tables()
        logger.info("База данных инициализирована.")
    except Exception as e:
        logger.error(f"Ошибка инициализации базы данных: {e}", exc_info=True)
        return

    try:
        application = Application.builder().token(token).build()
    except Exception as e:
        logger.error(f"Ошибка создания приложения Telegram: {e}", exc_info=True)
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
    application.add_handler(export_vacations_handler)
    application.add_handler(export_employees_handler)
    application.add_handler(my_vacations_handler)
    application.add_handler(invalid_command_handler)
    application.add_handler(random_text_handler)
    application.add_error_handler(error_handler)
    logger.info("Обработчики зарегистрированы.")

    try:
        setup_notifications(application, int(group_chat_id), admin_ids)
        logger.info("Уведомления настроены.")
    except Exception as e:
        logger.error(f"Ошибка настройки уведомлений: {e}", exc_info=True)

    async def send_welcome(context: ContextTypes.DEFAULT_TYPE):
        """Отправка приветствия администраторам."""
        message = "👋 Бот запущен!\n\nЯ готов. Используйте /start для регистрации или команд."
        for admin_id in admin_ids:
            try:
                await context.bot.send_message(chat_id=admin_id, text=message)
                logger.info(f"Приветствие отправлено админу {admin_id}")
            except Exception as e:
                logger.error(f"Ошибка приветствия админу {admin_id}: {e}", exc_info=True)

    application.job_queue.run_once(send_welcome, when=1)
    application.job_queue.run_once(set_initial_commands, when=0)

    logger.info("Запуск бота...")
    try:
        application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)
    except Exception as e:
        logger.error(f"Ошибка запуска бота: {e}", exc_info=True)
        raise

if __name__ == '__main__':
    main()