import logging
import os
from dotenv import load_dotenv
from telegram.ext import Application, CommandHandler, MessageHandler, filters
from telegram import Update
from telegram.ext import ContextTypes

# Загрузка переменных из .env
load_dotenv()
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')  # Используем TELEGRAM_TOKEN из .env
GROUP_CHAT_ID = os.getenv('GROUP_CHAT_ID')
ADMIN_ID = int(os.getenv('ADMIN_ID'))

# Проверка токена бота
if not TELEGRAM_TOKEN or not TELEGRAM_TOKEN.strip():
    raise ValueError("TELEGRAM_TOKEN не указан или пуст в .env файле. Убедись, что он корректен и взят из @BotFather в Telegram.")

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def main() -> None:
    """Запуск бота."""
    # Инициализация базы данных
    from database.db_operations import init_db
    init_db()
    logger.info("База данных инициализирована.")

    # Создание приложения
    application = Application.builder().token(TELEGRAM_TOKEN).build()

    # Обработчики для личных сообщений (PM)
    application.add_handler(CommandHandler('start', start_handler, filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler('help', help_handler, filters.ChatType.PRIVATE))
    application.add_handler(add_vacation_handler)
    application.add_handler(edit_vacation_handler)
    application.add_handler(CommandHandler('notify', notify_handler, filters.ChatType.PRIVATE))
    application.add_handler(delete_employee_handler)
    application.add_handler(list_employees_handler)
    application.add_handler(stats_handler)
    application.add_handler(export_employees_handler)

    # Обработчик сообщений в общем чате (только информационные)
    application.add_handler(MessageHandler(filters.Chat(GROUP_CHAT_ID) & ~filters.COMMAND, handle_group_message))

    # Настройка уведомлений
    from handlers.notification_handler import setup_notifications
    setup_notifications(application.job_queue, GROUP_CHAT_ID, str(ADMIN_ID))

    # Запуск бота
    logger.info("Запуск бота...")
    application.run_polling()  # Убрал allowed_updates=Application.ALL_UPDATE_TYPES

async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды /start в личных сообщениях."""
    from utils.helpers import identify_user
    user_id, username, full_name = identify_user(update)
    if not user_id or not username or not full_name:
        await update.message.reply_text("Не удалось определить пользователя. Обратитесь к @Admin.")
        return
    await update.message.reply_text(
        f"Привет, {full_name} (@{username})!\n\n"
        "Это бот для управления отпусками. Используй команды в личных сообщениях:\n"
        "/add_vacation — Добавить отпуск\n"
        "/edit_vacation — Редактировать отпуск\n"
        "/notify — Проверить предстоящие отпуска\n\n"
        "Для администратора доступны:\n"
        "/list_employees — Список сотрудников\n"
        "/delete_employee — Удалить сотрудника\n"
        "/stats — Статистика отпусков\n"
        "/export_employees — Выгрузить список сотрудников в Excel\n"
    )

async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды /help в личных сообщениях."""
    from utils.helpers import identify_user
    user_id, username, full_name = identify_user(update)
    if not user_id or not username or not full_name:
        await update.message.reply_text("Не удалось определить пользователя. Обратитесь к @Admin.")
        return
    await update.message.reply_text(
        f"Привет, {full_name} (@{username})!\n\n"
        "Команды для управления отпусками:\n"
        "/add_vacation — Добавить отпуск (укажи даты и, опционально, замещающего)\n"
        "/edit_vacation — Редактировать существующий отпуск\n"
        "/notify — Посмотреть предстоящие отпуска за 7 дней\n\n"
        "Для администратора (в личных сообщениях):\n"
        "/list_employees — Показать список сотрудников и их отпусков\n"
        "/delete_employee — Удалить сотрудника по ID\n"
        "/stats — Показать статистику отпусков\n"
        "/export_employees — Выгрузить список сотрудников в Excel\n\n"
        "В общем чате бот только отправляет уведомления о отпусках."
    )

async def handle_group_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик сообщений в общем чате (пустой, чтобы игнорировать команды)."""
    # Ничего не делаем, бот только отправляет информационные сообщения
    pass

from handlers.employee_handler import add_vacation_handler, edit_vacation_handler, delete_employee_handler, list_employees_handler, stats_handler, export_employees_handler, notify_handler

#Тест

if __name__ == '__main__':
    main()