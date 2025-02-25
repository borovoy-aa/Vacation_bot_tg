import logging
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import CommandHandler, ContextTypes
from utils.helpers import is_admin, escape_markdown_v2  # Добавлен импорт escape_markdown_v2

# Настройка логирования
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка команды /start."""
    chat_id = update.effective_chat.id
    user = update.effective_user
    full_name = user.full_name or user.username or "Сотрудник"
    logger.info(f"Команда /start вызвана пользователем {full_name} в чате {chat_id}")

    keyboard = [
        ["/add_vacation", "/edit_vacation"],
        ["/list_employees", "/notify"],
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)

    message = (
        f"👋 Привет, {escape_markdown_v2(full_name)}!\n"
        "Я бот для управления отпусками в нашем чате. Вот что я умею:\n\n"
        "📅 */add_vacation* — Добавить свой отпуск\n"
        "✏️ */edit_vacation* — Редактировать свой отпуск\n"
        "👥 */list_employees* — Показать список сотрудников (только для админа)\n"
        "🔔 */notify* — Проверить предстоящие отпуска\n"
        "🚫 */cancel* — Отменить текущее действие\n\n"
        "Все команды работают прямо здесь, в общем чате!"
    )
    if is_admin(chat_id):
        message += (
            "\n\nДля админа:\n"
            "📤 */upload_file* — Загрузить список сотрудников\n"
            "🗑️ */delete_employee <ID>* — Удалить сотрудника"
        )

    await update.message.reply_text(
        escape_markdown_v2(message),  # Экранируем всё сообщение
        reply_markup=reply_markup,
        parse_mode="MarkdownV2"
    )

start_handler = CommandHandler('start', start)