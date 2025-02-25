from telegram import Update
from typing import Tuple, Optional
import logging

logger = logging.getLogger(__name__)

def escape_markdown_v2(text: str) -> str:
    """Экранирование символов для MarkdownV2."""
    escape_chars = '_*[]()~`>#+-=|{}.!'
    return ''.join(f'\\{char}' if char in escape_chars else char for char in text)

def identify_user(update: Update) -> Tuple[Optional[int], Optional[str], Optional[str]]:
    """Идентификация пользователя по обновлению Telegram."""
    try:
        user = update.effective_user
        if not user:
            logger.error(f"Не удалось определить пользователя для update {update}")
            return 0, "unknown", "unknown"  # Возвращаем значения по умолчанию
        return user.id, user.username, user.full_name
    except AttributeError:
        logger.error(f"Ошибка при доступе к данным пользователя в update {update}")
        return 0, "unknown", "unknown"

def is_admin(chat_id: int) -> bool:
    """Проверка, является ли пользователь администратором."""
    import os
    from dotenv import load_dotenv
    load_dotenv()
    ADMIN_ID = int(os.getenv('ADMIN_ID'))
    return chat_id == ADMIN_ID