import logging
from datetime import datetime, timedelta
from typing import Tuple, Optional, List

from telegram import BotCommand, Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ConversationHandler, MessageHandler, filters, CommandHandler, ContextTypes, CallbackQueryHandler

from database.db_operations import (
    add_employee_to_db, add_vacation, get_upcoming_vacations, get_user_vacations, edit_vacation, check_vacation_overlap,
    list_employees_db, delete_employee, employee_exists, clear_all_employees, calculate_vacation_days,
    get_used_vacation_days, get_vacation_stats, get_all_vacations, get_employee_by_username, delete_vacation
)
from utils.helpers import identify_user, is_admin
import os
from dotenv import load_dotenv

load_dotenv()
ADMIN_ID = int(os.getenv('ADMIN_ID'))
GROUP_CHAT_ID = os.getenv('GROUP_CHAT_ID')

logger = logging.getLogger(__name__)

START_DATE, END_DATE, REPLACEMENT = range(3)
SELECT_VACATION, NEW_START_DATE, NEW_END_DATE, NEW_REPLACEMENT = range(4)
DELETE_EMPLOYEE_ID = 100
DELETE_VACATION_SELECT = 200

VACATION_LIMIT_DAYS = 28

def validate_date(date_str: str) -> Tuple[bool, str]:
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
        return True, ""
    except ValueError:
        return False, "Некорректный формат. Используйте YYYY-MM-DD (например, 2025-03-01)."

def validate_future_date(date_str: str, start_date: Optional[str] = None) -> Tuple[bool, str]:
    try:
        date = datetime.strptime(date_str, "%Y-%m-%d")
        if date <= datetime.now():
            return False, "Дата должна быть в будущем."
        if start_date and date <= datetime.strptime(start_date, "%Y-%m-%d"):
            return False, "Дата окончания должна быть позже даты начала."
        return True, ""
    except ValueError:
        return False, "Некорректный формат. Используйте YYYY-MM-DD (например, 2025-03-01)."

async def reset_state(context: ContextTypes.DEFAULT_TYPE) -> None:
    keys = ['name', 'user_id', 'username', 'start_date', 'end_date', 'replacement_username', 'vacation_id', 'vacations', 'action']
    for key in keys:
        context.user_data.pop(key, None)

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await reset_state(context)
    await update.message.reply_text("Действие отменено.")
    return ConversationHandler.END

async def handle_invalid_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    admin_commands = {'/list_employees', '/delete_employee', '/stats', '/export_employees', '/clear_all_employees'}
    if update.message.text in admin_commands and is_admin(update.effective_chat.id):
        return ConversationHandler.END  # Админские команды всегда проходят
    if context.user_data.get('action'):
        await update.message.reply_text(f"Вы уже начали {context.user_data['action']}. Завершите его или используйте /cancel.")
        return ConversationHandler.END
    await update.message.reply_text("Сначала начните действие с помощью команды (например, /add_vacation).")
    return ConversationHandler.END

async def handle_random_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.user_data.get('action'):
        await update.message.reply_text("Я не понимаю, что вы имеете в виду. Используйте команду, например, /add_vacation.")

async def add_vacation_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.effective_chat.type == 'private':
        await update.message.reply_text("Все команды доступны только в личных сообщениях. Напиши мне в личку!")
        return ConversationHandler.END
    user_id, username, full_name = identify_user(update)
    if not user_id or not username or not full_name:
        logger.error(f"Не удалось определить user_id, username или full_name для чата {update.effective_chat.id}")
        await update.message.reply_text("Не удалось определить пользователя. Обратитесь к @Admin за помощью.")
        return ConversationHandler.END
    db_user_id = add_employee_to_db(full_name, username)
    if db_user_id is None:
        logger.error(f"Не удалось добавить сотрудника для username={username}")
        await update.message.reply_text("Ошибка при добавлении сотрудника. Обратитесь к @Admin.")
        return ConversationHandler.END
    context.user_data['name'] = full_name
    context.user_data['user_id'] = db_user_id
    context.user_data['username'] = username
    context.user_data['action'] = "добавление отпуска"
    await update.message.reply_text(
        f"Привет, {full_name} (@{username})!\n\n"
        "Укажите дату начала отпуска (YYYY-MM-DD, например, 2025-03-01) или /cancel."
    )
    return START_DATE

async def add_vacation_start_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if text == "/cancel":
        return await cancel(update, context)
    is_valid, error = validate_date(text)
    if not is_valid:
        await update.message.reply_text(error + " Попробуйте снова или /cancel.")
        return START_DATE
    is_future, error = validate_future_date(text)
    if not is_future:
        await update.message.reply_text(error + " Попробуйте снова или /cancel.")
        return START_DATE
    context.user_data['start_date'] = text
    await update.message.reply_text("Укажите дату окончания (YYYY-MM-DD, например, 2025-03-15) или /cancel.")
    return END_DATE

async def add_vacation_end_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if text == "/cancel":
        return await cancel(update, context)
    is_valid, error = validate_date(text)
    if not is_valid:
        await update.message.reply_text(error + " Попробуйте снова или /cancel.")
        return END_DATE
    is_future, error = validate_future_date(text, context.user_data.get('start_date'))
    if not is_future:
        await update.message.reply_text(error + " Попробуйте снова или /cancel.")
        return END_DATE
    context.user_data['end_date'] = text
    user_id = context.user_data.get('user_id')
    current_year = datetime.now().year
    used_days = get_used_vacation_days(user_id, current_year)
    days_requested = calculate_vacation_days(context.user_data['start_date'], text)
    if used_days + days_requested > VACATION_LIMIT_DAYS:
        await update.message.reply_text(f"Лимит превышен. Использовано {used_days} дней, запрос: {days_requested} дней. Укажите другие даты или /cancel.")
        return START_DATE
    if check_vacation_overlap(user_id, context.user_data['start_date'], text):
        await update.message.reply_text("Этот отпуск пересекается с вашим. Укажите другие даты или /cancel.")
        return START_DATE
    await update.message.reply_text("Укажите @username замещающего или /skip.")
    return REPLACEMENT

async def add_vacation_replacement(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if text == "/cancel":
        return await cancel(update, context)
    if text == "/skip":
        context.user_data['replacement_username'] = None
    elif text.startswith('@'):
        context.user_data['replacement_username'] = text
    else:
        await update.message.reply_text("Неверный формат. Введите @username, /skip или /cancel.")
        return REPLACEMENT

    user_id = context.user_data.get('user_id')
    start_date = context.user_data['start_date']
    end_date = context.user_data['end_date']
    if add_vacation(user_id, start_date, end_date, context.user_data['replacement_username']):
        current_year = datetime.now().year
        vacations = get_user_vacations(user_id)
        used_days = get_used_vacation_days(user_id, current_year)
        vacation_lines = [f"{i+1}. {start} – {end}" for i, (_, start, end, _) in enumerate(vacations)]
        vacation_info = f"Отпусков в {current_year}: {len(vacations)}\n" + "\n".join(vacation_lines) if vacations else "Нет запланированных отпусков."
        username = context.user_data['username']
        message = (
            "ОТПУСК ДОБАВЛЕН!\n\n"
            f"Сотрудник: {context.user_data['name']} (@{username})\n"
            f"Даты: {start_date} - {end_date}\n"
            f"Замещающий: {context.user_data['replacement_username'] or 'Нет'}\n"
            f"{vacation_info}\n"
            f"Использовано дней: {used_days}\n\n"
            "Вопросы? @Admin"
        )
        await update.message.reply_text(message)
        group_message = f"{context.user_data['name']} (@{username}) взял отпуск с {start_date} по {end_date}"
        if context.user_data['replacement_username']:
            group_message += f", замещающий: {context.user_data['replacement_username']}"
        await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=group_message)
        logger.info(f"User {user_id} added vacation: {start_date} - {end_date}")
    else:
        await update.message.reply_text("Ошибка при добавлении. Попробуйте снова или /cancel.")
    return ConversationHandler.END

async def edit_vacation_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.effective_chat.type == 'private':
        await update.message.reply_text("Все команды доступны только в личных сообщениях. Напиши мне в личку!")
        return ConversationHandler.END
    user_id, username, full_name = identify_user(update)
    db_user_id = get_employee_by_username(username)
    if not db_user_id:
        await update.message.reply_text("Сотрудник не найден. Обратитесь к @Admin.")
        return ConversationHandler.END
    vacations = get_user_vacations(db_user_id)
    if not vacations:
        await update.message.reply_text("У вас нет отпусков для редактирования.")
        return ConversationHandler.END
    context.user_data['vacations'] = vacations
    context.user_data['action'] = "редактирование отпуска"
    context.user_data['user_id'] = db_user_id
    context.user_data['username'] = username
    context.user_data['name'] = full_name
    keyboard = []
    for i, (vacation_id, start, end, replacement) in enumerate(vacations):
        replacement_text = f" (Замещает: {replacement})" if replacement else ""
        start_date = datetime.strptime(start, "%Y-%m-%d").strftime("%B %d").replace("January", "Январь").replace("February", "Февраль").replace("March", "Март").replace("April", "Апрель").replace("May", "Май").replace("June", "Июнь").replace("July", "Июль").replace("August", "Август").replace("September", "Сентябрь").replace("October", "Октябрь").replace("November", "Ноябрь").replace("December", "Декабрь")
        end_date = datetime.strptime(end, "%Y-%m-%d").strftime("%B %d").replace("January", "Январь").replace("February", "Февраль").replace("March", "Март").replace("April", "Апрель").replace("May", "Май").replace("June", "Июнь").replace("July", "Июль").replace("August", "Август").replace("September", "Сентябрь").replace("October", "Октябрь").replace("November", "Ноябрь").replace("December", "Декабрь")
        button_text = f"{i+1}. {start_date} – {end_date}{replacement_text}"
        keyboard.append([InlineKeyboardButton(button_text, callback_data=str(vacation_id))])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Выберите отпуск для редактирования:", reply_markup=reply_markup)
    return SELECT_VACATION

async def select_vacation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    vacation_id = int(query.data)
    context.user_data['vacation_id'] = vacation_id
    await query.edit_message_text("Укажите новую дату начала (YYYY-MM-DD, например, 2025-03-01) или /skip.")
    return NEW_START_DATE

async def edit_vacation_start_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip().lower()  # Приводим к нижнему регистру
    if text == "/cancel":
        return await cancel(update, context)
    if text == "/skip":
        context.user_data['new_start_date'] = None
        await update.message.reply_text("Дата начала пропущена. Укажите новую дату окончания (YYYY-MM-DD) или /skip.")
        return NEW_END_DATE
    is_valid, error = validate_date(text)
    if not is_valid:
        await update.message.reply_text(error + " Попробуйте снова или /cancel.")
        return NEW_START_DATE
    is_future, error = validate_future_date(text)
    if not is_future:
        await update.message.reply_text(error + " Попробуйте снова или /cancel.")
        return NEW_START_DATE
    context.user_data['new_start_date'] = text
    await update.message.reply_text("Укажите новую дату окончания (YYYY-MM-DD) или /skip.")
    return NEW_END_DATE

async def edit_vacation_end_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if text == "/cancel":
        return await cancel(update, context)
    if text.lower() == "/skip":
        context.user_data['new_end_date'] = None
        await update.message.reply_text("Укажите @username нового замещающего, /skip или /remove.")
        return NEW_REPLACEMENT
    is_valid, error = validate_date(text)
    if not is_valid:
        await update.message.reply_text(error + " Попробуйте снова или /cancel.")
        return NEW_END_DATE
    is_future, error = validate_future_date(text, context.user_data.get('new_start_date'))
    if not is_future:
        await update.message.reply_text(error + " Попробуйте снова или /cancel.")
        return NEW_END_DATE
    context.user_data['new_end_date'] = text
    user_id = context.user_data['user_id']
    vacation_id = context.user_data['vacation_id']
    current_year = datetime.now().year
    vacations = get_user_vacations(user_id)
    total_current_days = sum(calculate_vacation_days(start, end) for _, start, end, _ in vacations)
    new_start = context.user_data.get('new_start_date') or next((v[1] for v in vacations if v[0] == vacation_id), None)
    new_end = text
    days_requested = calculate_vacation_days(new_start, new_end)
    old_days = calculate_vacation_days(
        next((v[1] for v in vacations if v[0] == vacation_id), None),
        next((v[2] for v in vacations if v[0] == vacation_id), None)
    )
    new_total_days = total_current_days - old_days + days_requested
    if new_total_days > VACATION_LIMIT_DAYS:
        await update.message.reply_text(f"Лимит превышен. Использовано {total_current_days - old_days} дней, запрос: {days_requested} дней. Укажите другие даты или /cancel.")
        return NEW_START_DATE
    if check_vacation_overlap(user_id, new_start, new_end, vacation_id):
        await update.message.reply_text("Новый отпуск пересекается с вашим. Укажите другие даты или /cancel.")
        return NEW_START_DATE
    await update.message.reply_text("Укажите @username нового замещающего, /skip или /remove.")
    return NEW_REPLACEMENT

async def edit_vacation_replacement(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip().lower()
    if text == "/cancel":
        return await cancel(update, context)
    if text == "/skip" or text == "/remove":
        context.user_data['new_replacement_username'] = None
        # Завершаем редактирование
        vacation_id = context.user_data['vacation_id']
        user_id = context.user_data['user_id']
        if edit_vacation(vacation_id, context.user_data.get('new_start_date'), context.user_data.get('new_end_date'), None):
            username = context.user_data['username']
            name = context.user_data['name']
            start_date = context.user_data.get('new_start_date') or next((v[1] for v in get_user_vacations(user_id) if v[0] == vacation_id), None)
            end_date = context.user_data.get('new_end_date') or next((v[2] for v in get_user_vacations(user_id) if v[0] == vacation_id), None)
            current_year = datetime.now().year
            vacations = get_user_vacations(user_id)
            used_days = get_used_vacation_days(user_id, current_year)
            vacation_lines = [f"{i+1}. {start} – {end}" for i, (_, start, end, _) in enumerate(vacations)]
            vacation_info = f"Отпусков в {current_year}: {len(vacations)}\n" + "\n".join(vacation_lines) if vacations else "Нет запланированных отпусков."
            message = (
                "ОТПУСК ОТРЕДАКТИРОВАН!\n\n"
                f"Сотрудник: {name} (@{username})\n"
                f"Даты: {start_date} - {end_date}\n"
                f"Замещающий: Нет\n"
                f"{vacation_info}\n"
                f"Использовано дней: {used_days}\n\n"
                "Вопросы? @Admin"
            )
            await update.message.reply_text(message)
            group_message = f"{name} (@{username}) изменил отпуск: с {start_date} по {end_date}"
            await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=group_message)
            logger.info(f"User {user_id} edited vacation {vacation_id}")
        else:
            await update.message.reply_text("Ошибка при редактировании. Попробуйте снова или /cancel.")
        return ConversationHandler.END
    elif text.startswith('@'):
        context.user_data['new_replacement_username'] = text
        # Завершаем редактирование с новым замещающим
        vacation_id = context.user_data['vacation_id']
        user_id = context.user_data['user_id']
        if edit_vacation(vacation_id, context.user_data.get('new_start_date'), context.user_data.get('new_end_date'), text):
            username = context.user_data['username']
            name = context.user_data['name']
            start_date = context.user_data.get('new_start_date') or next((v[1] for v in get_user_vacations(user_id) if v[0] == vacation_id), None)
            end_date = context.user_data.get('new_end_date') or next((v[2] for v in get_user_vacations(user_id) if v[0] == vacation_id), None)
            current_year = datetime.now().year
            vacations = get_user_vacations(user_id)
            used_days = get_used_vacation_days(user_id, current_year)
            vacation_lines = [f"{i+1}. {start} – {end}" for i, (_, start, end, _) in enumerate(vacations)]
            vacation_info = f"Отпусков в {current_year}: {len(vacations)}\n" + "\n".join(vacation_lines) if vacations else "Нет запланированных отпусков."
            message = (
                "ОТПУСК ОТРЕДАКТИРОВАН!\n\n"
                f"Сотрудник: {name} (@{username})\n"
                f"Даты: {start_date} - {end_date}\n"
                f"Замещающий: {text}\n"
                f"{vacation_info}\n"
                f"Использовано дней: {used_days}\n\n"
                "Вопросы? @Admin"
            )
            await update.message.reply_text(message)
            group_message = f"{name} (@{username}) изменил отпуск: с {start_date} по {end_date}, замещающий: {text}"
            await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=group_message)
            logger.info(f"User {user_id} edited vacation {vacation_id}")
        else:
            await update.message.reply_text("Ошибка при редактировании. Попробуйте снова или /cancel.")
        return ConversationHandler.END
    else:
        await update.message.reply_text("Неверный формат. Введите @username, /skip, /remove или /cancel.")
        return NEW_REPLACEMENT

edit_vacation_handler = ConversationHandler(
    entry_points=[CommandHandler('edit_vacation', edit_vacation_start, filters.ChatType.PRIVATE)],
    states={
        SELECT_VACATION: [CallbackQueryHandler(select_vacation)],
        NEW_START_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_vacation_start_date)],
        NEW_END_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_vacation_end_date)],
        NEW_REPLACEMENT: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, edit_vacation_replacement),
            CommandHandler('skip', lambda update, context: edit_vacation_replacement(update, context)),  # Явная обработка /skip
            CommandHandler('remove', lambda update, context: edit_vacation_replacement(update, context)),  # Явная обработка /remove
        ],
    },
    fallbacks=[CommandHandler('cancel', cancel)],
    per_message=False
)

async def delete_vacation_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.effective_chat.type == 'private':
        await update.message.reply_text("Все команды доступны только в личных сообщениях. Напиши мне в личку!")
        return ConversationHandler.END
    user_id, username, full_name = identify_user(update)
    db_user_id = get_employee_by_username(username)
    if not db_user_id:
        await update.message.reply_text("Сотрудник не найден. Обратитесь к @Admin.")
        return ConversationHandler.END
    vacations = get_user_vacations(db_user_id)
    if not vacations:
        await update.message.reply_text("У вас нет отпусков для удаления.")
        return ConversationHandler.END
    context.user_data['vacations'] = vacations
    context.user_data['action'] = "удаление отпуска"
    context.user_data['user_id'] = db_user_id
    context.user_data['username'] = username
    context.user_data['name'] = full_name
    keyboard = []
    for i, (vacation_id, start, end, replacement) in enumerate(vacations):
        replacement_text = f" (Замещает: {replacement})" if replacement else ""
        start_date = datetime.strptime(start, "%Y-%m-%d").strftime("%B %d").replace("January", "Январь").replace("February", "Февраль").replace("March", "Март").replace("April", "Апрель").replace("May", "Май").replace("June", "Июнь").replace("July", "Июль").replace("August", "Август").replace("September", "Сентябрь").replace("October", "Октябрь").replace("November", "Ноябрь").replace("December", "Декабрь")
        end_date = datetime.strptime(end, "%Y-%m-%d").strftime("%B %d").replace("January", "Январь").replace("February", "Февраль").replace("March", "Март").replace("April", "Апрель").replace("May", "Май").replace("June", "Июнь").replace("July", "Июль").replace("August", "Август").replace("September", "Сентябрь").replace("October", "Октябрь").replace("November", "Ноябрь").replace("December", "Декабрь")
        button_text = f"{i+1}. {start_date} – {end_date}{replacement_text}"
        keyboard.append([InlineKeyboardButton(button_text, callback_data=str(vacation_id))])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Выберите отпуск для удаления:", reply_markup=reply_markup)
    return DELETE_VACATION_SELECT

async def delete_vacation_select(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    vacation_id = int(query.data)
    user_id = context.user_data['user_id']
    vacations = context.user_data['vacations']
    vacation = next((v for v in vacations if v[0] == vacation_id), None)
    if not vacation:
        await query.edit_message_text("Отпуск не найден.")
        return ConversationHandler.END
    if delete_vacation(vacation_id):
        start_date, end_date = vacation[1], vacation[2]
        username = context.user_data['username']
        name = context.user_data['name']
        await query.edit_message_text(f"Отпуск с {start_date} по {end_date} удалён.")
        group_message = f"{name} (@{username}) отменил отпуск с {start_date} по {end_date}"
        await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=group_message)
        logger.info(f"User {user_id} deleted vacation {vacation_id}")
    else:
        await query.edit_message_text("Ошибка при удалении отпуска. Обратитесь к @Admin.")
    return ConversationHandler.END

async def notify_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat.type == 'private':
        await update.message.reply_text("Все команды доступны только в личных сообщениях. Напиши мне в личку!")
        return
    current_date = datetime.now().date()
    seven_days_later = current_date + timedelta(days=7)
    vacations = get_upcoming_vacations(seven_days_later)
    if not vacations:
        await update.message.reply_text("На ближайшие 7 дней отпусков нет.")
        return
    message = "СПИСОК ПРЕДСТОЯЩИХ ОТПУСКОВ НА 7 ДНЕЙ:\n\n"
    for _, full_name, username, start_date, end_date, replacement in vacations:
        replacement = f" (Замещает: {replacement})" if replacement else ""
        message += f"{full_name} (@{username}): {start_date} - {end_date}{replacement}\n"
    message += "Вопросы? @Admin"
    await update.message.reply_text(message)

async def list_employees(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat.type == 'private' or not is_admin(update.effective_chat.id):
        await update.message.reply_text("Эта команда доступна только администратору в личных сообщениях.")
        return
    try:
        employees = list_employees_db()
        if employees:
            message = "СПИСОК СОТРУДНИКОВ:\n\n"
            for employee in employees:
                parts = employee.split(', ')
                message += f"ID: {parts[0]}\nЛогин: {parts[1]}\nФИО: {parts[2]}\nИспользовано дней: {parts[3]}\n\n"
            await update.message.reply_text(message.rstrip())
        else:
            await update.message.reply_text("Список сотрудников пуст.")
    except Exception as e:
        logger.error(f"Ошибка в list_employees: {e}", exc_info=True)
        await update.message.reply_text("Произошла ошибка при получении списка сотрудников. Обратитесь к @Admin.")

async def delete_employee_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.effective_chat.type == 'private' or not is_admin(update.effective_chat.id):
        await update.message.reply_text("Эта команда доступна только администратору в личных сообщениях.")
        return ConversationHandler.END
    await update.message.reply_text("Укажите ID сотрудника для удаления:")
    return DELETE_EMPLOYEE_ID

async def delete_employee_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if text == "/cancel":
        return await cancel(update, context)
    try:
        employee_id = int(text)
        if delete_employee(employee_id):
            await update.message.reply_text(f"СОТРУДНИК С ID {employee_id} УДАЛЁН.")
        else:
            await update.message.reply_text(f"Сотрудник с ID {employee_id} не найден.")
    except ValueError:
        await update.message.reply_text("ID должен быть числом. Попробуйте снова или /cancel.")
        return DELETE_EMPLOYEE_ID
    return ConversationHandler.END

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat.type == 'private' or not is_admin(update.effective_chat.id):
        await update.message.reply_text("Эта команда доступна только администратору в личных сообщениях.")
        return
    stats = get_vacation_stats()
    message = "СТАТИСТИКА ОТПУСКОВ:\n\n"
    for month, count, days in stats:
        message += f"Месяц {month}: {count} отпусков, {days:.0f} дней\n"
    total_vacations = sum(row[1] for row in stats)
    total_days = sum(row[2] for row in stats)
    message += f"\nВсего: {total_vacations} отпусков, {total_days:.0f} дней\n\nВопросы? @Admin"
    await update.message.reply_text(message)

async def export_employees(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat.type == 'private' or not is_admin(update.effective_chat.id):
        await update.message.reply_text("Эта команда доступна только администратору в личных сообщениях.")
        return
    import pandas as pd
    import io
    from telegram import InputFile
    employees = get_all_vacations()
    if not employees:
        await update.message.reply_text("Список сотрудников пуст.")
        return
    df = pd.DataFrame(employees, columns=['ID', 'ФИО', 'Логин', 'Дата начала', 'Дата окончания', 'Замещающий'])
    buffer = io.BytesIO()
    df.to_excel(buffer, index=False, engine='openpyxl')
    buffer.seek(0)
    await update.message.reply_document(
        document=InputFile(buffer, filename='employees_vacations.xlsx'),
        caption="Список сотрудников и отпусков выгружен.\n\nВопросы? @Admin"
    )
    buffer.close()

async def clear_all_employees_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat.type == 'private' or not is_admin(update.effective_chat.id):
        await update.message.reply_text("Эта команда доступна только администратору в личных сообщениях.")
        return
    if clear_all_employees():
        await update.message.reply_text("Все сотрудники и отпуска удалены.")
        await reset_state(context)
    else:
        await update.message.reply_text("Ошибка при очистке базы данных. Обратитесь к @Admin.")

async def set_bot_commands(context: ContextTypes.DEFAULT_TYPE) -> None:
    public_commands = [
        BotCommand("add_vacation", "Добавить отпуск"),
        BotCommand("edit_vacation", "Редактировать отпуск"),
        BotCommand("delete_vacation", "Удалить отпуск"),
        BotCommand("notify", "Показать предстоящие отпуска"),
    ]
    admin_commands = [
        BotCommand("list_employees", "Список сотрудников"),
        BotCommand("delete_employee", "Удалить сотрудника"),
        BotCommand("stats", "Статистика отпусков"),
        BotCommand("export_employees", "Выгрузить сотрудников"),
        BotCommand("clear_all_employees", "Очистить базу данных"),
    ]
    await context.bot.set_my_commands(public_commands, scope={"type": "all_private_chats"})
    await context.bot.set_my_commands(public_commands + admin_commands, scope={"type": "chat", "chat_id": ADMIN_ID})

add_vacation_handler = ConversationHandler(
    entry_points=[CommandHandler('add_vacation', add_vacation_start, filters.ChatType.PRIVATE)],
    states={
        START_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_vacation_start_date)],
        END_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_vacation_end_date)],
        REPLACEMENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_vacation_replacement)],
    },
    fallbacks=[CommandHandler('cancel', cancel)],
    per_message=False
)

edit_vacation_handler = ConversationHandler(
    entry_points=[CommandHandler('edit_vacation', edit_vacation_start, filters.ChatType.PRIVATE)],
    states={
        SELECT_VACATION: [CallbackQueryHandler(select_vacation)],
        NEW_START_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_vacation_start_date)],
        NEW_END_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_vacation_end_date)],
        NEW_REPLACEMENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_vacation_replacement)],
    },
    fallbacks=[CommandHandler('cancel', cancel)],
    per_message=False
)

delete_vacation_handler = ConversationHandler(
    entry_points=[CommandHandler('delete_vacation', delete_vacation_start, filters.ChatType.PRIVATE)],
    states={
        DELETE_VACATION_SELECT: [CallbackQueryHandler(delete_vacation_select)],
    },
    fallbacks=[CommandHandler('cancel', cancel)],
    per_message=False
)

notify_handler = CommandHandler('notify', notify_handler, filters.ChatType.PRIVATE)
delete_employee_handler = ConversationHandler(
    entry_points=[CommandHandler('delete_employee', delete_employee_command, filters.ChatType.PRIVATE & filters.User(ADMIN_ID))],
    states={
        DELETE_EMPLOYEE_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, delete_employee_id)],
    },
    fallbacks=[CommandHandler('cancel', cancel)],
)
list_employees_handler = CommandHandler('list_employees', list_employees, filters.ChatType.PRIVATE & filters.User(ADMIN_ID))
stats_handler = CommandHandler('stats', stats, filters.ChatType.PRIVATE & filters.User(ADMIN_ID))
export_employees_handler = CommandHandler('export_employees', export_employees, filters.ChatType.PRIVATE & filters.User(ADMIN_ID))
clear_all_employees_handler = CommandHandler('clear_all_employees', clear_all_employees_command, filters.ChatType.PRIVATE & filters.User(ADMIN_ID))
invalid_command_handler = MessageHandler(filters.COMMAND & ~filters.Regex(r'^/(cancel|start|help)$'), handle_invalid_command, filters.ChatType.PRIVATE)
random_text_handler = MessageHandler(filters.TEXT & ~filters.COMMAND, handle_random_text, filters.ChatType.PRIVATE)