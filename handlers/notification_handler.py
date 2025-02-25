import logging
from datetime import datetime, timedelta

# Сторонние библиотеки
from telegram import Update
from telegram.ext import ContextTypes, JobQueue
from telegram.error import BadRequest

# Локальные модули
from database.db_operations import get_upcoming_vacations

logger = logging.getLogger(__name__)

async def vacation_notify(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Уведомление о предстоящих отпусках за 7, 3 и 1 день в общем чате."""
    chat_id = '-1002368339454'  # ID общего чата для уведомлений
    admin_id = 303982483  # ID администратора
    
    for days_ahead in [7, 3, 1]:
        current_date = datetime.now().date()
        target_date = current_date + timedelta(days=days_ahead)  # Используем date
        vacations = get_upcoming_vacations(target_date)
        
        if vacations:
            message = f"УВЕДОМЛЕНИЕ ОБ ОТПУСКАХ ЗА {days_ahead} ДЕНЬ(-ЕЙ):\n\n"
            for user_id, full_name, username, start_date, end_date, replacement in vacations:
                if datetime.strptime(start_date, "%Y-%m-%d").date() == target_date:
                    replacement = f" (Замещает: {replacement})" if replacement else ""
                    message += f"{full_name} (@{username}): {start_date} - {end_date}{replacement}\n"
            message += "Вопросы? @Admin"
            
            try:
                await context.bot.send_message(chat_id=chat_id, text=message)
                await context.bot.send_message(chat_id=admin_id, text=message)
            except BadRequest as e:
                logger.error(f"Ошибка при отправке уведомления о отпусках за {days_ahead} дней: {e}")
        else:
            message = f"На ближайшие {days_ahead} дней отпусков нет."
            try:
                await context.bot.send_message(chat_id=chat_id, text=message)
                await context.bot.send_message(chat_id=admin_id, text=message)
            except BadRequest as e:
                logger.error(f"Ошибка при отправке уведомления о отсутствии отпусков за {days_ahead} дней: {e}")

def setup_notifications(job_queue: JobQueue, group_chat_id: str, admin_chat_id: str) -> None:
    """Настройка периодических уведомлений."""
    # Проверяем, существуют ли задачи с такими именами
    existing_jobs = job_queue.jobs()
    existing_job_names = [job.name for job in existing_jobs] if existing_jobs else []
    for days in [7, 3, 1]:
        job_name = f"vacation_notify_{days}"
        if job_name not in existing_job_names:
            job_queue.run_daily(
                lambda context: vacation_notify(context),
                time=datetime.now().replace(hour=9, minute=0, second=0, microsecond=0).time(),
                name=job_name
            )
    logger.info(f"Уведомления настроены: чат {group_chat_id}, админ {admin_chat_id}.")