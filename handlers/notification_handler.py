import logging
from datetime import datetime, timedelta
import os
from telegram import Update
from telegram.ext import ContextTypes, Application, CommandHandler, filters

logger = logging.getLogger(__name__)

async def send_vacation_notification(context: ContextTypes.DEFAULT_TYPE, days_before: int) -> None:
    """Отправка уведомлений о предстоящих отпусках за days_before дней."""
    try:
        from database.db_operations import get_upcoming_vacations
        current_date = datetime.now().date()
        notification_date = current_date + timedelta(days=days_before)
        vacations = get_upcoming_vacations(notification_date)

        group_chat_id = context.bot_data.get('group_chat_id')
        admin_id = context.bot_data.get('admin_id')

        if not vacations or not group_chat_id or not admin_id:
            logger.info(f"Нет данных для уведомления за {days_before} дней: vacations={len(vacations)}, group_chat_id={group_chat_id}, admin_id={admin_id}")
            return

        message = f"УВЕДОМЛЕНИЕ: Отпуска, начинающиеся через {days_before} дней ({notification_date}):\n\n"
        for user_id, full_name, username, start_date, end_date, replacement in vacations:
            replacement_text = f" (Замещает: {replacement})" if replacement else ""
            message += f"{full_name} (@{username}): {start_date} - {end_date}{replacement_text}\n"
        message += "\nВопросы? @Admin"

        # Отправка в общий чат
        await context.bot.send_message(chat_id=group_chat_id, text=message)
        logger.info(f"Уведомление отправлено в общий чат {group_chat_id} за {days_before} дней.")

        # Отправка администратору
        await context.bot.send_message(chat_id=admin_id, text=message)
        logger.info(f"Уведомление отправлено администратору {admin_id} за {days_before} дней.")
    except Exception as e:
        logger.error(f"Ошибка при отправке уведомления за {days_before} дней: {e}", exc_info=True)

def setup_notifications(application: Application, group_chat_id: str, admin_id: int) -> None:
    """Настройка автоматических уведомлений о предстоящих отпусках."""
    try:
        # Сохраняем данные в контексте бота
        application.bot_data['group_chat_id'] = group_chat_id
        application.bot_data['admin_id'] = admin_id

        # Получаем job_queue из application
        job_queue = application.job_queue

        # Проверяем существующие задачи
        existing_jobs = job_queue.jobs()
        job_names = {job.name for job in existing_jobs if hasattr(job, 'name')}

        # Настройка задач на уведомления
        notify_time = datetime.strptime("09:00", "%H:%M").time()
        if 'vacation_notify_7' not in job_names:
            job_queue.run_daily(
                lambda context: send_vacation_notification(context, 7),
                time=notify_time,
                name='vacation_notify_7'
            )
            logger.info("Уведомления за 7 дней настроены.")
        if 'vacation_notify_3' not in job_names:
            job_queue.run_daily(
                lambda context: send_vacation_notification(context, 3),
                time=notify_time,
                name='vacation_notify_3'
            )
            logger.info("Уведомления за 3 дня настроены.")
        if 'vacation_notify_1' not in job_names:
            job_queue.run_daily(
                lambda context: send_vacation_notification(context, 1),
                time=notify_time,
                name='vacation_notify_1'
            )
            logger.info("Уведомления за 1 день настроены.")
    except Exception as e:
        logger.error(f"Ошибка при настройке уведомлений: {e}", exc_info=True)

async def test_notifications(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Ручной тест уведомлений."""
    user_id = update.effective_user.id
    logger.info(f"Пользователь {user_id} запросил тест уведомлений")
    admin_id = int(os.getenv('ADMIN_ID'))
    
    if user_id != admin_id:
        await update.message.reply_text("Эта команда доступна только администратору.")
        logger.warning(f"Пользователь {user_id} без прав админа пытался выполнить /test_notifications")
        return

    for days in [7, 3, 1]:
        await send_vacation_notification(context, days)
    await update.message.reply_text("Тест уведомлений выполнен.")
    logger.info(f"Пользователь {user_id} успешно выполнил тест уведомлений")

# Исправляем фильтры и добавляем обработчик без состояния
test_notifications_handler = CommandHandler('test_notifications', test_notifications)