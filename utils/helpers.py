from telegram import Update
from typing import Tuple, Optional
import logging

logger = logging.getLogger(__name__)

def identify_user(update: Update) -> Tuple[Optional[int], Optional[str], Optional[str]]:
    """Идентификация пользователя по обновлению Telegram."""
    try:
        user = update.effective_user
        # Проверяем, существует ли user, перед доступом к его атрибутам
        if not user:
            logger.error(f"Не удалось определить пользователя для update {update}")
            return None, None, None
        user_id = user.id
        username = user.username if user.username else None
        full_name = user.full_name if user.full_name else None
        logger.info(f"Identified user: ID={user_id}, Username={username}, Full Name={full_name}")
        return user_id, username, full_name
    except AttributeError:
        logger.error(f"Ошибка при доступе к данным пользователя в update {update}")
        return None, None, None

def is_admin(chat_id: int) -> bool:
    """Проверка, является ли пользователь администратором."""
    import os
    from dotenv import load_dotenv
    load_dotenv()
    ADMIN_ID = int(os.getenv('ADMIN_ID'))
    return chat_id == ADMIN_ID