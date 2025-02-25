import logging
from datetime import datetime, timedelta
from typing import Tuple, Optional, List

from telegram import BotCommand, ReplyKeyboardMarkup, Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ConversationHandler, MessageHandler, filters, CommandHandler, ContextTypes, CallbackQueryHandler

from database.db_operations import (
    add_employee_to_db, add_vacation, get_upcoming_vacations, get_user_vacations, edit_vacation, check_vacation_overlap,
    list_employees_db, delete_employee, employee_exists, clear_all_employees, calculate_vacation_days,
    get_used_vacation_days, get_vacation_stats, get_all_vacations, get_employee_by_username, delete_vacation
)
from utils.helpers import escape_markdown_v2, identify_user, is_admin
import os
from dotenv import load_dotenv

load_dotenv()
ADMIN_ID = int(os.getenv('ADMIN_ID'))
GROUP_CHAT_ID = os.getenv('GROUP_CHAT_ID')

logger = logging.getLogger(__name__)

# Состояния для ConversationHandler
START_DATE, END_DATE, REPLACEMENT = range(3)
SELECT_VACATION, NEW_START_DATE, NEW_END_DATE, NEW_REPLACEMENT = range(4)
DELETE_EMPLOYEE_ID = 100
DELETE_VACATION_SELECT = 200

VACATION_LIMIT_DAYS = 28

# Словарь месяцев для перевода
MONTHS = {
    "January": "Январь", "February": "Февраль", "March": "Март", "April": "Апрель",
    "May": "Май", "June": "Июнь", "July": "Июль", "August": "Август",
    "September": "Сентябрь", "October": "Октябрь", "November": "Ноябрь", "December": "Декабрь"
}

def validate_date(date_str: str) -> Tuple[bool, str]:
    """Проверка формата даты (YYYY-MM-DD)."""
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
        return True, ""
    except ValueError:
        return False, "Некорректный формат. Используйте YYYY-MM-DD (например, 2025-03-01)."

def validate_future_date(date_str: str, start_date: Optional[str] = None) -> Tuple[bool, str]:
    """Проверка, что дата в будущем и корректна относительно даты начала."""
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
    """Сброс данных пользователя в context.user_data."""
    keys = ['name', 'user_id', 'username', 'start_date', 'end_date', 'replacement_username', 'vacation_id', 'vacations', 'action']
    for key in keys:
        context.user_data.pop(key, None)

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Отмена текущего действия."""
    await reset_state(context)
    await update.message.reply_text("Действие отменено.")
    return ConversationHandler.END

async def handle_invalid_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка некорректных команд."""
    admin_commands = {'/list_employees', '/delete_employee', '/stats', '/export_employees', '/clear_all_employees'}
    if update.message.text in admin_commands and is_admin(update.effective_chat.id):
        return ConversationHandler.END
    if context.user_data.get('action'):
        await update.message.reply_text(f"Вы уже начали {context.user_data['action']}. Завершите его или используйте /cancel.")
        return ConversationHandler.END
    await update.message.reply_text("Сначала начните действие с помощью команды (например, /add_vacation).")
    return ConversationHandler.END

async def handle_random_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка случайного текста, если не начато действие."""
    if not context.user_data.get('action'):
        await update.message.reply_text("Я не понимаю, что вы имеете в виду. Используйте команду, например, /add_vacation.")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Отправка приветственного сообщения при команде /start."""
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    username = update.effective_user.username or "пользователь"
    full_name = update.effective_user.full_name or username
    logger.info(f"Команда /start вызвана пользователем {user_id} ({full_name}) в чате {chat_id}")

    # Клавиатура для удобства
    keyboard = [
        ["/add_vacation", "/edit_vacation"],
        ["/delete_vacation", "/notify"],
    ]
    if is_admin(user_id):
        keyboard.append(["/list_employees", "/stats"])
        keyboard.append(["/delete_employee", "/export_employees"])
        keyboard.append(["/clear_all_employees"])
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)

    # Общее сообщение для всех (без Markdown)
    message = (
        f"👋 Привет, {full_name} (@{username})!\n"
        "Я бот для управления отпусками. Вот что я умею:\n\n"
        "📅 /add_vacation — Добавить свой отпуск (укажите дату начала, окончания и замещающего)\n"
        "✏️ /edit_vacation — Изменить существующий отпуск (выберите отпуск, обновите даты или замещающего)\n"
        "🗑️ /delete_vacation — Удалить свой отпуск\n"
        "🔔 /notify — Показать предстоящие отпуска на 7 дней\n"
        "🚫 /cancel — Отменить текущее действие\n\n"
        "Все команды работают только в личных сообщениях. Даты вводите в формате YYYY-MM-DD (например, 2025-03-01)."
    )

    # Дополнительно для админа
    if is_admin(user_id):
        message += (
            "\n\nКоманды для администратора:\n"
            "👥 /list_employees — Показать список сотрудников и их отпусков\n"
            "🗑️ /delete_employee <ID> — Удалить сотрудника по ID\n"
            "📊 /stats — Статистика отпусков по месяцам\n"
            "📤 /export_employees — Выгрузить данные в Excel\n"
            "⚠️ /clear_all_employees — Удалить всех сотрудников и их отпуска\n\n"
            "Вы админ, так что управляйте всем через личку!"
        )
    else:
        message += "\nЕсли что-то непонятно, обратитесь к @Admin!"

    await update.message.reply_text(message, reply_markup=reply_markup)

async def add_vacation_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начало процесса добавления отпуска."""
    if update.effective_chat.type != 'private':
        await update.message.reply_text("Все команды доступны только в личных сообщениях. Напишите мне в личку!")
        return ConversationHandler.END
    user_id, username, full_name = identify_user(update)
    if not all([user_id, username, full_name]):
        logger.error(f"Не удалось определить данные пользователя для чата {update.effective_chat.id}: user_id={user_id}, username={username}, full_name={full_name}")
        await update.message.reply_text("Не удалось определить пользователя. Обратитесь к @Admin.")
        return ConversationHandler.END
    db_user_id = add_employee_to_db(full_name, username)
    if db_user_id is None:
        logger.error(f"Не удалось добавить сотрудника username={username}")
        await update.message.reply_text("Ошибка при добавлении сотрудника. Обратитесь к @Admin.")
        return ConversationHandler.END
    context.user_data.update({
        'name': full_name,
        'user_id': db_user_id,
        'username': username,
        'action': "добавление отпуска"
    })
    await update.message.reply_text(
        f"Привет, {full_name} (@{username})!\n\n"
        "Укажите дату начала отпуска (YYYY-MM-DD, например, 2025-03-01) или /cancel."
    )
    return START_DATE

async def add_vacation_start_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка даты начала отпуска."""
    text = update.message.text.strip()
    if text == "/cancel":
        return await cancel(update, context)
    is_valid, error = validate_date(text)
    if not is_valid:
        await update.message.reply_text(f"{error} Попробуйте снова или /cancel.")
        return START_DATE
    is_future, error = validate_future_date(text)
    if not is_future:
        await update.message.reply_text(f"{error} Попробуйте снова или /cancel.")
        return START_DATE
    context.user_data['start_date'] = text
    await update.message.reply_text("Укажите дату окончания (YYYY-MM-DD, например, 2025-03-15) или /cancel.")
    return END_DATE

async def add_vacation_end_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка даты окончания отпуска."""
    text = update.message.text.strip()
    if text == "/cancel":
        return await cancel(update, context)
    is_valid, error = validate_date(text)
    if not is_valid:
        await update.message.reply_text(f"{error} Попробуйте снова или /cancel.")
        return END_DATE
    is_future, error = validate_future_date(text, context.user_data.get('start_date'))
    if not is_future:
        await update.message.reply_text(f"{error} Попробуйте снова или /cancel.")
        return END_DATE
    context.user_data['end_date'] = text
    user_id = context.user_data.get('user_id')
    current_year = datetime.now().year
    used_days = get_used_vacation_days(user_id, current_year)
    days_requested = calculate_vacation_days(context.user_data['start_date'], text)
    if used_days + days_requested > VACATION_LIMIT_DAYS:
        await update.message.reply_text(
            f"Лимит превышен. Использовано {used_days} дней, запрос: {days_requested} дней. Укажите другие даты или /cancel."
        )
        return START_DATE
    if check_vacation_overlap(user_id, context.user_data['start_date'], text):
        await update.message.reply_text("Этот отпуск пересекается с вашим. Укажите другие даты или /cancel.")
        return START_DATE
    await update.message.reply_text("Укажите @username замещающего или /skip.")
    return REPLACEMENT

async def add_vacation_replacement(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Завершение добавления отпуска с указанием замещающего."""
    text = update.message.text.strip()
    if text == "/cancel":
        return await cancel(update, context)
    elif text == "/skip":
        context.user_data['replacement_username'] = None
    elif text.startswith('@'):
        context.user_data['replacement_username'] = text
    else:
        await update.message.reply_text("Неверный формат. Введите @username, /skip или /cancel.")
        return REPLACEMENT

    user_id = context.user_data['user_id']
    start_date = context.user_data['start_date']
    end_date = context.user_data['end_date']
    replacement = context.user_data['replacement_username']
    if add_vacation(user_id, start_date, end_date, replacement):
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
            f"Замещающий: {replacement or 'Нет'}\n"
            f"{vacation_info}\n"
            f"Использовано дней: {used_days}\n\n"
            "Вопросы? @Admin"
        )
        await update.message.reply_text(message)
        group_message = f"{context.user_data['name']} (@{username}) \nВзял отпуск с {start_date} по {end_date}"
        if replacement:
            group_message += f", замещающий: {replacement} \nFYI @Admin!"
        await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=group_message)
        logger.info(f"User {user_id} added vacation: {start_date} - {end_date}")
    else:
        logger.error(f"Ошибка добавления отпуска для user_id={user_id}: {start_date} - {end_date}")
        await update.message.reply_text("Ошибка при добавлении. Попробуйте снова или /cancel.")
    await reset_state(context)
    return ConversationHandler.END

async def edit_vacation_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начало редактирования отпуска."""
    if update.effective_chat.type != 'private':
        await update.message.reply_text("Все команды доступны только в личных сообщениях. Напишите мне в личку!")
        return ConversationHandler.END
    user_id, username, full_name = identify_user(update)
    if not all([user_id, username, full_name]):
        logger.error(f"Не удалось определить данные пользователя для чата {update.effective_chat.id}")
        await update.message.reply_text("Не удалось определить пользователя. Обратитесь к @Admin.")
        return ConversationHandler.END
    db_user_id = get_employee_by_username(username)
    if not db_user_id:
        await update.message.reply_text("Сотрудник не найден. Обратитесь к @Admin.")
        return ConversationHandler.END
    vacations = get_user_vacations(db_user_id)
    if not vacations:
        await update.message.reply_text("У вас нет отпусков для редактирования.")
        return ConversationHandler.END
    context.user_data.update({
        'vacations': vacations,
        'action': "редактирование отпуска",
        'user_id': db_user_id,
        'username': username,
        'name': full_name
    })
    keyboard = []
    for i, (vacation_id, start, end, replacement) in enumerate(vacations):
        replacement_text = f" (Замещает: {replacement})" if replacement else ""
        start_date = datetime.strptime(start, "%Y-%m-%d").strftime("%B %d")
        start_date = f"{MONTHS[start_date.split()[0]]} {start_date.split()[1]}"
        end_date = datetime.strptime(end, "%Y-%m-%d").strftime("%B %d")
        end_date = f"{MONTHS[end_date.split()[0]]} {end_date.split()[1]}"
        button_text = f"{i+1}. {start_date} – {end_date}{replacement_text}"
        keyboard.append([InlineKeyboardButton(button_text, callback_data=str(vacation_id))])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Выберите отпуск для редактирования:", reply_markup=reply_markup)
    return SELECT_VACATION

async def select_vacation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Выбор отпуска для редактирования."""
    query = update.callback_query
    await query.answer()
    vacation_id = int(query.data)
    context.user_data['vacation_id'] = vacation_id
    await query.edit_message_text("Укажите новую дату начала (YYYY-MM-DD, например, 2025-03-01) или /skip.")
    return NEW_START_DATE

async def edit_vacation_start_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка новой даты начала при редактировании."""
    text = update.message.text.strip().lower()
    if text == "/cancel":
        return await cancel(update, context)
    if text == "/skip":
        context.user_data['new_start_date'] = None
        await update.message.reply_text("Дата начала пропущена. Укажите новую дату окончания (YYYY-MM-DD) или /skip.")
        return NEW_END_DATE
    is_valid, error = validate_date(text)
    if not is_valid:
        await update.message.reply_text(f"{error} Попробуйте снова или /cancel.")
        return NEW_START_DATE
    is_future, error = validate_future_date(text)
    if not is_future:
        await update.message.reply_text(f"{error} Попробуйте снова или /cancel.")
        return NEW_START_DATE
    context.user_data['new_start_date'] = text
    await update.message.reply_text("Укажите новую дату окончания (YYYY-MM-DD) или /skip.")
    return NEW_END_DATE

async def edit_vacation_end_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка новой даты окончания при редактировании."""
    text = update.message.text.strip().lower()
    if text == "/cancel":
        return await cancel(update, context)
    if text == "/skip":
        context.user_data['new_end_date'] = None
        await update.message.reply_text("Укажите @username нового замещающего, /skip или /remove.")
        return NEW_REPLACEMENT
    is_valid, error = validate_date(text)
    if not is_valid:
        await update.message.reply_text(f"{error} Попробуйте снова или /cancel.")
        return NEW_END_DATE
    is_future, error = validate_future_date(text, context.user_data.get('new_start_date'))
    if not is_future:
        await update.message.reply_text(f"{error} Попробуйте снова или /cancel.")
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
        await update.message.reply_text(
            f"Лимит превышен. Использовано {total_current_days - old_days} дней, запрос: {days_requested} дней. Укажите другие даты или /cancel."
        )
        return NEW_START_DATE
    if check_vacation_overlap(user_id, new_start, new_end, vacation_id):
        await update.message.reply_text("Новый отпуск пересекается с вашим. Укажите другие даты или /cancel.")
        return NEW_START_DATE
    await update.message.reply_text("Укажите @username нового замещающего, /skip или /remove.")
    return NEW_REPLACEMENT

async def edit_vacation_replacement(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Завершение редактирования отпуска с указанием замещающего."""
    text = update.message.text.strip().lower()
    if text == "/cancel":
        return await cancel(update, context)
    elif text in ["/skip", "/remove"]:
        context.user_data['new_replacement_username'] = None
    elif text.startswith('@'):
        context.user_data['new_replacement_username'] = text
    else:
        await update.message.reply_text("Неверный формат. Введите @username, /skip, /remove или /cancel.")
        return NEW_REPLACEMENT

    vacation_id = context.user_data['vacation_id']
    user_id = context.user_data['user_id']
    new_start_date = context.user_data.get('new_start_date')
    new_end_date = context.user_data.get('new_end_date')
    new_replacement = context.user_data.get('new_replacement_username')
    if edit_vacation(vacation_id, new_start_date, new_end_date, new_replacement):
        username = context.user_data['username']
        name = context.user_data['name']
        start_date = new_start_date or next((v[1] for v in get_user_vacations(user_id) if v[0] == vacation_id), None)
        end_date = new_end_date or next((v[2] for v in get_user_vacations(user_id) if v[0] == vacation_id), None)
        current_year = datetime.now().year
        vacations = get_user_vacations(user_id)
        used_days = get_used_vacation_days(user_id, current_year)
        vacation_lines = [f"{i+1}. {start} – {end}" for i, (_, start, end, _) in enumerate(vacations)]
        vacation_info = f"Отпусков в {current_year}: {len(vacations)}\n" + "\n".join(vacation_lines) if vacations else "Нет запланированных отпусков."
        message = (
            "ОТПУСК ОТРЕДАКТИРОВАН!\n\n"
            f"Сотрудник: {name} (@{username})\n"
            f"Даты: {start_date} - {end_date}\n"
            f"Замещающий: {new_replacement or 'Нет'}\n"
            f"{vacation_info}\n"
            f"Использовано дней: {used_days}\n\n"
            "Вопросы? @Admin"
        )
        await update.message.reply_text(message)
        group_message = f"{name} (@{username}) изменил отпуск: с {start_date} по {end_date} \nFYI @Admin!"
        if new_replacement:
            group_message += f", замещающий: {new_replacement} \nFYI @Admin!"
        await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=group_message)
        logger.info(f"User {user_id} edited vacation {vacation_id}")
    else:
        logger.error(f"Ошибка редактирования отпуска ID={vacation_id} для user_id={user_id}")
        await update.message.reply_text("Ошибка при редактировании. Попробуйте снова или /cancel.")
    await reset_state(context)
    return ConversationHandler.END

async def delete_vacation_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начало удаления отпуска."""
    if update.effective_chat.type != 'private':
        await update.message.reply_text("Все команды доступны только в личных сообщениях. Напишите мне в личку!")
        return ConversationHandler.END
    user_id, username, full_name = identify_user(update)
    if not all([user_id, username, full_name]):
        logger.error(f"Не удалось определить данные пользователя для чата {update.effective_chat.id}")
        await update.message.reply_text("Не удалось определить пользователя. Обратитесь к @Admin.")
        return ConversationHandler.END
    db_user_id = get_employee_by_username(username)
    if not db_user_id:
        await update.message.reply_text("Сотрудник не найден. Обратитесь к @Admin.")
        return ConversationHandler.END
    vacations = get_user_vacations(db_user_id)
    if not vacations:
        await update.message.reply_text("У вас нет отпусков для удаления.")
        return ConversationHandler.END
    context.user_data.update({
        'vacations': vacations,
        'action': "удаление отпуска",
        'user_id': db_user_id,
        'username': username,
        'name': full_name
    })
    keyboard = []
    for i, (vacation_id, start, end, replacement) in enumerate(vacations):
        replacement_text = f" (Замещает: {replacement})" if replacement else ""
        start_date = datetime.strptime(start, "%Y-%m-%d").strftime("%B %d")
        start_date = f"{MONTHS[start_date.split()[0]]} {start_date.split()[1]}"
        end_date = datetime.strptime(end, "%Y-%m-%d").strftime("%B %d")
        end_date = f"{MONTHS[end_date.split()[0]]} {end_date.split()[1]}"
        button_text = f"{i+1}. {start_date} – {end_date}{replacement_text}"
        keyboard.append([InlineKeyboardButton(button_text, callback_data=str(vacation_id))])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Выберите отпуск для удаления:", reply_markup=reply_markup)
    return DELETE_VACATION_SELECT

async def delete_vacation_select(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Завершение удаления отпуска."""
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
        group_message = f"{name} (@{username}) отменил отпуск с {start_date} по {end_date} \nFYI @Admin!"
        await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=group_message)
        logger.info(f"User {user_id} deleted vacation {vacation_id}")
    else:
        logger.error(f"Ошибка удаления отпуска ID={vacation_id} для user_id={user_id}")
        await query.edit_message_text("Ошибка при удалении отпуска. Обратитесь к @Admin.")
    await reset_state(context)
    return ConversationHandler.END

async def notify_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показ предстоящих отпусков."""
    if update.effective_chat.type != 'private':
        await update.message.reply_text("Все команды доступны только в личных сообщениях. Напишите мне в личку!")
        return
    current_date = datetime.now().date()
    seven_days_later = current_date + timedelta(days=7)
    vacations = get_upcoming_vacations(seven_days_later)
    if not vacations:
        await update.message.reply_text("На ближайшие 7 дней отпусков нет.")
        return
    message = "СПИСОК ПРЕДСТОЯЩИХ ОТПУСКОВ НА 7 ДНЕЙ:\n\n"
    for _, full_name, username, start_date, end_date, replacement in vacations:
        replacement_text = f" (Замещает: {replacement})" if replacement else ""
        message += f"{full_name} (@{username}): {start_date} - {end_date}{replacement_text}\n"
    message += "\nВопросы? @Admin"
    await update.message.reply_text(message)

async def list_employees(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Список сотрудников (для админа)."""
    if update.effective_chat.type != 'private' or not is_admin(update.effective_chat.id):
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
    """Начало удаления сотрудника (для админа)."""
    if update.effective_chat.type != 'private' or not is_admin(update.effective_chat.id):
        await update.message.reply_text("Эта команда доступна только администратору в личных сообщениях.")
        return ConversationHandler.END
    await update.message.reply_text("Укажите ID сотрудника для удаления:")
    return DELETE_EMPLOYEE_ID

async def delete_employee_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Завершение удаления сотрудника."""
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
    """Статистика отпусков (для админа): количество отпусков, дней и сотрудников по месяцам."""
    if update.effective_chat.type != 'private' or not is_admin(update.effective_user.id):
        await update.message.reply_text("Эта команда доступна только администратору в личных сообщениях.")
        return
    stats = get_vacation_stats()
    message = "СТАТИСТИКА ОТПУСКОВ:\n\n"
    for month, count, days, employee_count in stats:
        message += f"Месяц {month}: {count} отпусков, {days:.0f} дней, {employee_count} сотрудников\n"
    total_vacations = sum(row[1] for row in stats)
    total_days = sum(row[2] for row in stats)
    # Подсчёт уникальных сотрудников за всё время через get_all_vacations()
    all_vacations = get_all_vacations()
    total_employees = len(set(vac[0] for vac in all_vacations if vac[3]))  # vac[0] — user_id, vac[3] — start_date (проверка на наличие отпуска)
    message += f"\nВсего: {total_vacations} отпусков, {total_days:.0f} дней, {total_employees} сотрудников\n\nВопросы? @Admin"
    await update.message.reply_text(message)

async def export_employees(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Экспорт списка сотрудников (для админа)."""
    if update.effective_chat.type != 'private' or not is_admin(update.effective_chat.id):
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
    """Очистка всех сотрудников и их отпусков (для админа)."""
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    logger.info(f"Получена команда /clear_all_employees от пользователя {user_id} в чате {chat_id}")
    
    # Проверка на администратора
    if not is_admin(user_id):
        logger.warning(f"Команда /clear_all_employees вызвана не админом: {user_id}")
        await update.message.reply_text("Эта команда доступна только администратору.")
        return
    
    # Выполнение очистки с явным логированием
    try:
        result = clear_all_employees()
        logger.info(f"Результат очистки базы данных: {result}")
        if result:
            await update.message.reply_text("Все сотрудники и отпуска удалены.")
            await reset_state(context)
        else:
            logger.error(f"Функция clear_all_employees вернула False для user_id={user_id}")
            await update.message.reply_text("Ошибка при очистке базы данных. Обратитесь к @Admin.")
    except Exception as e:
        logger.error(f"Ошибка при выполнении clear_all_employees: {e}", exc_info=True)
        await update.message.reply_text("Произошла ошибка при очистке. Обратитесь к @Admin.")

async def set_bot_commands(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Установка команд бота для интерфейса Telegram."""
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

# Определение ConversationHandler'ов
add_vacation_handler = ConversationHandler(
    entry_points=[CommandHandler('add_vacation', add_vacation_start, filters.ChatType.PRIVATE)],
    states={
        START_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_vacation_start_date)],
        END_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_vacation_end_date)],
        REPLACEMENT: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, add_vacation_replacement),
            CommandHandler('skip', add_vacation_replacement),
        ],
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
        NEW_REPLACEMENT: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, edit_vacation_replacement),
            CommandHandler('skip', edit_vacation_replacement),
            CommandHandler('remove', edit_vacation_replacement),
        ],
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

delete_employee_handler = ConversationHandler(
    entry_points=[CommandHandler('delete_employee', delete_employee_command, filters.ChatType.PRIVATE & filters.User(user_id=ADMIN_ID))],
    states={
        DELETE_EMPLOYEE_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, delete_employee_id)],
    },
    fallbacks=[CommandHandler('cancel', cancel)],
    per_message=False
)

# Простые обработчики команд
notify_handler = CommandHandler('notify', notify_handler, filters.ChatType.PRIVATE)
list_employees_handler = CommandHandler('list_employees', list_employees, filters.ChatType.PRIVATE & filters.User(user_id=ADMIN_ID))
stats_handler = CommandHandler('stats', stats, filters.ChatType.PRIVATE & filters.User(user_id=ADMIN_ID))
export_employees_handler = CommandHandler('export_employees', export_employees, filters.ChatType.PRIVATE & filters.User(user_id=ADMIN_ID))
clear_all_employees_handler = CommandHandler('clear_all_employees', clear_all_employees_command)
invalid_command_handler = MessageHandler(filters.COMMAND & ~filters.Regex(r'^/(cancel|start|help)$'), handle_invalid_command, filters.ChatType.PRIVATE)
random_text_handler = MessageHandler(filters.TEXT & ~filters.COMMAND, handle_random_text, filters.ChatType.PRIVATE)
start_handler = CommandHandler('start', start)