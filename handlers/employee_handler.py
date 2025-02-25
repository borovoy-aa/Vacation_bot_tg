import logging
import sqlite3  # Добавлен для работы с базой в get_user_vacations_count
from datetime import datetime, timedelta
from typing import Tuple, Optional, List

# Сторонние библиотеки
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ConversationHandler, MessageHandler, filters, CommandHandler, ContextTypes, CallbackQueryHandler

# Локальные модули
from database.db_operations import (
    add_employee_to_db,
    add_vacation,
    get_user_vacations,
    edit_vacation,
    check_vacation_overlap,
    list_employees_db,
    delete_employee,
    employee_exists,
    clear_all_employees,
    calculate_vacation_days,
    get_remaining_vacation_days,
    get_vacation_stats,
    get_all_vacations,
)
from utils.helpers import identify_user, is_admin
import os
from dotenv import load_dotenv

# Загрузка переменных из .env
load_dotenv()
ADMIN_ID = int(os.getenv('ADMIN_ID'))  # Явное определение для статического анализа
GROUP_CHAT_ID = os.getenv('GROUP_CHAT_ID')

logger = logging.getLogger(__name__)

# Состояния для диалогов
START_DATE, END_DATE, REPLACEMENT = range(3)  # Добавление отпуска
SELECT_VACATION, NEW_START_DATE, NEW_END_DATE, REPLACEMENT = range(4)  # Редактирование отпуска
DELETE_EMPLOYEE_ID = 100  # Состояние для удаления сотрудника

# Константы
VACATION_LIMIT_DAYS = 28  # Лимит отпусков в рабочих днях за год

# Функции валидации
def validate_date(date_str: str) -> Tuple[bool, str]:
    """Валидация формата даты."""
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
        return True, ""
    except ValueError:
        return False, "Некорректный формат. Используйте YYYY-MM-DD (например, 2025-03-01)."

def validate_future_date(date_str: str, start_date: Optional[str] = None) -> Tuple[bool, str]:
    """Проверка, что дата в будущем и позже start_date, если указана."""
    try:
        date = datetime.strptime(date_str, "%Y-%m-%d")
        if date <= datetime.now():
            return False, "Дата должна быть в будущем."
        if start_date and date <= datetime.strptime(start_date, "%Y-%m-%d"):
            return False, "Дата окончания должна быть позже даты начала."
        return True, ""
    except ValueError:
        return False, "Некорректный формат. Используйте YYYY-MM-DD (например, 2025-03-01)."

def get_user_vacations_count(user_id: int, year: int) -> Tuple[int, List[Tuple[str, str]]]:
    """Получение количества и списка отпусков пользователя за указанный год, отсортированных по дате начала."""
    try:
        with sqlite3.connect('employees.db') as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("""
                SELECT start_date, end_date 
                FROM vacations 
                WHERE user_id = ? 
                AND strftime('%Y', start_date) = ?
                ORDER BY start_date ASC
            """, (user_id, str(year)))
            vacations = [(row['start_date'], row['end_date']) for row in cursor.fetchall()]
            return len(vacations), vacations
    except sqlite3.Error as e:
        logger.error(f"Ошибка при получении количества отпусков для user_id={user_id}: {e}")
        return 0, []

# Сброс состояния
async def reset_state(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Сбрасывает состояние диалога."""
    keys = ['name', 'user_id', 'username', 'start_date', 'end_date', 'replacement_username', 'vacation_id', 'vacations', 'action']
    for key in keys:
        context.user_data.pop(key, None)

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Отмена диалога."""
    await reset_state(context)
    await update.message.reply_text("Действие отменено.")
    return ConversationHandler.END

# Обработка некорректных команд во время диалога
async def handle_invalid_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка некорректных команд во время активного диалога."""
    if context.user_data.get('name'):
        await update.message.reply_text(f"Вы уже начали {context.user_data.get('action', 'действие')}. Завершите его или используйте /cancel.")
    else:
        await update.message.reply_text("Сначала завершите текущее действие или используйте /cancel.")
    return ConversationHandler.END

# Добавление отпуска (доступно всем пользователям в PM)
async def add_vacation_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.effective_chat.type == 'private':
        await update.message.reply_text("Все команды доступны только в личных сообщениях. Напиши мне в личку!")
        return ConversationHandler.END
    user_id, username, full_name = identify_user(update)
    if not user_id or not username or not full_name:
        logger.error(f"Не удалось определить user_id, username или full_name для чата {update.effective_chat.id}")
        await update.message.reply_text("Не удалось определить пользователя. Обратитесь к @Admin за помощью.")
        return ConversationHandler.END
    # Добавляем сотрудника в базу, если его нет, и получаем автоинкрементный id
    from database.db_operations import add_employee_to_db
    db_user_id = add_employee_to_db(full_name, username)  # Используем автоинкрементный id
    if db_user_id is None:
        logger.error(f"Не удалось добавить сотрудника для username={username}")
        await update.message.reply_text("Ошибка при добавлении сотрудника. Обратитесь к @Admin.")
        return ConversationHandler.END
    context.user_data['name'] = full_name
    context.user_data['user_id'] = db_user_id  # Сохраняем автоинкрементный id
    context.user_data['username'] = username  # Сохраняем username для уведомлений
    context.user_data['action'] = "добавление отпуска"
    await update.message.reply_text(
        f"Привет, {full_name} (@{username})!\n\n"
        "Укажите дату начала отпуска (YYYY-MM-DD, например, 2025-03-01) или /cancel."
    )
    return START_DATE

async def add_vacation_start_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.effective_chat.type == 'private':
        await update.message.reply_text("Все команды доступны только в личных сообщениях. Напиши мне в личку!")
        return ConversationHandler.END
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
    await update.message.reply_text("Укажите дату окончания (YYYY-MM-DD, например, 2025-03-15) или /cancel. Убедитесь, что дата позже начала.")
    return END_DATE

async def add_vacation_end_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.effective_chat.type == 'private':
        await update.message.reply_text("Все команды доступны только в личных сообщениях. Напиши мне в личку!")
        return ConversationHandler.END
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
    if not user_id:
        logger.error(f"Не удалось определить user_id в контексте для чата {update.effective_chat.id}")
        await update.message.reply_text("Ошибка: пользователь не определён. Обратитесь к @Admin.")
        return ConversationHandler.END
    current_year = datetime.now().year
    remaining_days = get_remaining_vacation_days(user_id, current_year)
    days_requested = calculate_vacation_days(context.user_data['start_date'], text)
    if days_requested > remaining_days:
        await update.message.reply_text(f"Лимит (28 дней) превышен. Осталось {remaining_days} дней, запрос: {days_requested} дней. Укажите другие даты или /cancel.")
        return START_DATE
    if check_vacation_overlap(user_id, context.user_data['start_date'], text):
        await update.message.reply_text("Этот отпуск пересекается с вашим. Укажите другие даты или /cancel.")
        return START_DATE
    await update.message.reply_text("Укажите @username замещающего или /skip.")
    return REPLACEMENT

async def add_vacation_replacement(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.effective_chat.type == 'private':
        await update.message.reply_text("Все команды доступны только в личных сообщениях. Напиши мне в личку!")
        return ConversationHandler.END
    if not update.message or not update.message.text:
        logger.error(f"No message or text received in add_vacation_replacement for chat {update.effective_chat.id}")
        await update.message.reply_text("Ошибка. Попробуйте снова или /cancel.")
        logger.info(f"User {context.user_data['user_id']} failed to add vacation due to message error.")
        return ConversationHandler.END
    text = update.message.text.strip()
    logger.debug(f"Received replacement input: '{text}'")
    if text == "/skip":
        context.user_data['replacement_username'] = None
        await update.message.reply_text("Отпуск добавлен без замещающего.")
        user_id = context.user_data.get('user_id')
        if not user_id:
            logger.error(f"Не удалось определить user_id в контексте для чата {update.effective_chat.id}")
            await update.message.reply_text("Ошибка: пользователь не определён. Обратитесь к @Admin.")
            return ConversationHandler.END
        start_date = context.user_data['start_date']
        end_date = context.user_data['end_date']
        if add_vacation(user_id, start_date, end_date, context.user_data['replacement_username']):
            current_year = datetime.now().year
            vacation_count, vacation_list = get_user_vacations_count(user_id, current_year)
            vacation_lines = [f"{i+1}. {start} – {end}" for i, (start, end) in enumerate(vacation_list)]
            total_days = sum(calculate_vacation_days(start, end) for start, end in vacation_list)
            vacation_info = f"Отпусков в {current_year}: {vacation_count}\n" + "\n".join(vacation_lines) if vacation_list else "Нет запланированных отпусков."
            username = context.user_data['username']  # Используем сохранённый username
            message = (
                "ОТПУСК ДОБАВЛЕН!\n\n"
                f"Сотрудник: {context.user_data['name']} (@{username})\n"
                f"Даты: {start_date} - {end_date}\n"
                f"Замещающий: Нет\n"
                f"{vacation_info}\n"
                f"Использовано дней: {total_days} из 28\n\n"
                "Вопросы? @Admin"
            )
            try:
                await update.message.reply_text(message)
                # Отправляем уведомление в общий чат
                group_message = f"{context.user_data['name']} (@{username}) взял отпуск с {start_date} по {end_date}"
                await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=group_message)
            except Exception as e:
                logger.error(f"Ошибка при отправке сообщения: {e}")
                await update.message.reply_text("Отпуск добавлен, но возникла ошибка при отправке подтверждения. Обратитесь к @Admin.")
            logger.info(f"User {user_id} added vacation: {start_date} - {end_date}")
        else:
            await update.message.reply_text("Ошибка при добавлении. Попробуйте снова или /cancel.")
            logger.error(f"User {context.user_data['user_id']} failed to add vacation: database error.")
        return ConversationHandler.END
    if text == "/cancel":
        return await cancel(update, context)
    # Жёсткое ограничение: принимаем только текст, начинающийся с @, иначе ошибка
    if text.startswith('@'):
        context.user_data['replacement_username'] = text
        user_id = context.user_data.get('user_id')
        if not user_id:
            logger.error(f"Не удалось определить user_id в контексте для чата {update.effective_chat.id}")
            await update.message.reply_text("Ошибка: пользователь не определён. Обратитесь к @Admin.")
            return ConversationHandler.END
        start_date = context.user_data['start_date']
        end_date = context.user_data['end_date']
        if add_vacation(user_id, start_date, end_date, context.user_data['replacement_username']):
            current_year = datetime.now().year
            vacation_count, vacation_list = get_user_vacations_count(user_id, current_year)
            vacation_lines = [f"{i+1}. {start} – {end}" for i, (start, end) in enumerate(vacation_list)]
            total_days = sum(calculate_vacation_days(start, end) for start, end in vacation_list)
            vacation_info = f"Отпусков в {current_year}: {vacation_count}\n" + "\n".join(vacation_lines) if vacation_list else "Нет запланированных отпусков."
            username = context.user_data['username']  # Используем сохранённый username
            message = (
                "ОТПУСК ДОБАВЛЕН!\n\n"
                f"Сотрудник: {context.user_data['name']} (@{username})\n"
                f"Даты: {start_date} - {end_date}\n"
                f"Замещающий: {context.user_data['replacement_username']}\n"
                f"{vacation_info}\n"
                f"Использовано дней: {total_days} из 28\n\n"
                "Вопросы? @Admin"
            )
            try:
                await update.message.reply_text(message)
                # Отправляем уведомление в общий чат
                group_message = f"{context.user_data['name']} (@{username}) взял отпуск с {start_date} по {end_date}, замещающий: {context.user_data['replacement_username']}"
                await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=group_message)
            except Exception as e:
                logger.error(f"Ошибка при отправке сообщения: {e}")
                await update.message.reply_text("Отпуск добавлен, но возникла ошибка при отправке подтверждения. Обратитесь к @Admin.")
            logger.info(f"User {user_id} added vacation: {start_date} - {end_date}, replacement: {text}")
        else:
            await update.message.reply_text("Ошибка при добавлении. Попробуйте снова или /cancel.")
            logger.error(f"User {context.user_data['user_id']} failed to add vacation: database error.")
        return ConversationHandler.END
    await update.message.reply_text("Неверный формат. Введите @username, /skip или /cancel.")
    return REPLACEMENT

# Редактирование отпуска (доступно всем пользователям в PM)
async def edit_vacation_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.effective_chat.type == 'private':
        await update.message.reply_text("Все команды доступны только в личных сообщениях. Напиши мне в личку!")
        return ConversationHandler.END
    logger.info(f"User {identify_user(update)[0]} started editing vacation.")
    user_id, username, _ = identify_user(update)
    if not user_id or not username:
        logger.error(f"Не удалось определить user_id или username для чата {update.effective_chat.id}")
        await update.message.reply_text("Не удалось определить пользователя. Убедитесь, что вы авторизованы в Telegram и бот имеет доступ к вашим данным. Обратитесь к @Admin за помощью.")
        return ConversationHandler.END
    # Получаем автоинкрементный id сотрудника по username
    from database.db_operations import get_employee_by_username
    db_user_id = get_employee_by_username(username)
    if not db_user_id:
        logger.error(f"Не удалось найти сотрудника для username={username}")
        await update.message.reply_text("Сотрудник не найден. Обратитесь к @Admin.")
        return ConversationHandler.END
    vacations = get_user_vacations(db_user_id)
    if not vacations:
        await update.message.reply_text("У вас нет отпусков для редактирования.")
        return ConversationHandler.END
    context.user_data['vacations'] = vacations
    context.user_data['action'] = "редактирование отпуска"
    context.user_data['user_id'] = db_user_id  # Сохраняем автоинкрементный id
    context.user_data['username'] = username  # Сохраняем username для уведомлений
    context.user_data['name'] = identify_user(update)[2]  # Сохраняем full_name
    keyboard = []
    for i, (vacation_id, start, end, replacement) in enumerate(vacations):
        replacement_text = f" (Замещает: {replacement})" if replacement else ""
        # Форматируем даты, показывая только месяц и день (например, "Февраль 26 – Февраль 28")
        start_date = datetime.strptime(start, "%Y-%m-%d")
        end_date = datetime.strptime(end, "%Y-%m-%d")
        start_month_day = start_date.strftime("%B %d").replace("January", "Январь").replace("February", "Февраль").replace("March", "Март").replace("April", "Апрель").replace("May", "Май").replace("June", "Июнь").replace("July", "Июль").replace("August", "Август").replace("September", "Сентябрь").replace("October", "Октябрь").replace("November", "Ноябрь").replace("December", "Декабрь")
        end_month_day = end_date.strftime("%B %d").replace("January", "Январь").replace("February", "Февраль").replace("March", "Март").replace("April", "Апрель").replace("May", "Май").replace("June", "Июнь").replace("July", "Июль").replace("August", "Август").replace("September", "Сентябрь").replace("October", "Октябрь").replace("November", "Ноябрь").replace("December", "Декабрь")
        button_text = f"{i+1}. {start_month_day} – {end_month_day}{replacement_text}"
        keyboard.append([InlineKeyboardButton(button_text, callback_data=str(vacation_id))])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Выберите отпуск для редактирования:", reply_markup=reply_markup)
    return SELECT_VACATION

async def select_vacation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.effective_chat.type == 'private':
        await update.callback_query.answer("Все команды доступны только в личных сообщениях. Напиши мне в личку!")
        return ConversationHandler.END
    query = update.callback_query
    await query.answer()
    vacation_id = int(query.data)
    context.user_data['vacation_id'] = vacation_id
    user_id = context.user_data.get('user_id')
    if not user_id:
        logger.error(f"Не удалось определить user_id в контексте для чата {query.message.chat.id}")
        await query.edit_message_text("Ошибка: пользователь не определён. Обратитесь к @Admin.")
        return ConversationHandler.END
    await query.edit_message_text("Укажите новую дату начала (YYYY-MM-DD, например, 2025-03-01) или /skip.")
    return NEW_START_DATE

async def edit_vacation_start_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.effective_chat.type == 'private':
        await update.message.reply_text("Все команды доступны только в личных сообщениях. Напиши мне в личку!")
        return ConversationHandler.END
    text = update.message.text.strip()
    if text == "/cancel":
        return await cancel(update, context)
    if text.lower() == "/skip":
        context.user_data['new_start_date'] = None
        await update.message.reply_text("Дата начала пропущена. Укажите новую дату окончания (YYYY-MM-DD, например, 2025-03-15) или /skip.")
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
    await update.message.reply_text("Укажите новую дату окончания (YYYY-MM-DD, например, 2025-03-15) или /skip. Убедитесь, что дата позже начала.")
    return NEW_END_DATE

async def edit_vacation_end_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.effective_chat.type == 'private':
        await update.message.reply_text("Все команды доступны только в личных сообщениях. Напиши мне в личку!")
        return ConversationHandler.END
    text = update.message.text.strip()
    if text == "/cancel":
        return await cancel(update, context)
    if text.lower() == "/skip":
        context.user_data['new_end_date'] = None
        user_id = context.user_data.get('user_id')
        vacation_id = context.user_data['vacation_id']
        if edit_vacation(vacation_id, context.user_data.get('new_start_date'), None, context.user_data.get('new_replacement_username')):
            username = context.user_data['username']
            name = context.user_data['name']
            start_date = context.user_data.get('new_start_date') or get_user_vacations(user_id)[0][1]
            end_date = get_user_vacations(user_id)[0][2]
            await update.message.reply_text("Отпуск отредактирован без изменения даты окончания.")
            # Отправляем уведомление в общий чат
            group_message = f"{name} (@{username}) изменил отпуск: с {start_date} по {end_date}"
            replacement = context.user_data.get('new_replacement_username')
            if replacement:
                group_message += f", замещающий: {replacement}"
            await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=group_message)
            logger.info(f"User {user_id} edited vacation {vacation_id} without end date change.")
        else:
            await update.message.reply_text("Ошибка при редактировании. Попробуйте снова или /cancel.")
            logger.error(f"User {context.user_data['user_id']} failed to edit vacation {vacation_id}: database error.")
        return ConversationHandler.END
    is_valid, error = validate_date(text)
    if not is_valid:
        await update.message.reply_text(error + " Попробуйте снова или /cancel.")
        return NEW_END_DATE
    is_future, error = validate_future_date(text, context.user_data.get('new_start_date'))
    if not is_future:
        await update.message.reply_text(error + " Попробуйте снова или /cancel.")
        return NEW_END_DATE
    context.user_data['new_end_date'] = text
    user_id = context.user_data.get('user_id')
    if not user_id:
        logger.error(f"Не удалось определить user_id в контексте для чата {update.effective_chat.id}")
        await update.message.reply_text("Ошибка: пользователь не определён. Обратитесь к @Admin.")
        return ConversationHandler.END
    vacation_id = context.user_data['vacation_id']
    current_year = datetime.now().year
    _, current_vacations = get_user_vacations_count(user_id, current_year)
    total_current_days = sum(calculate_vacation_days(start, end) for start, end in current_vacations)
    new_start = context.user_data.get('new_start_date') or next((v[1] for v in get_user_vacations(user_id) if v[0] == vacation_id), None)
    new_end = text
    days_requested = calculate_vacation_days(new_start, new_end)
    remaining_days = VACATION_LIMIT_DAYS - total_current_days + calculate_vacation_days(
        next((v[1] for v in get_user_vacations(user_id) if v[0] == vacation_id), None),
        next((v[2] for v in get_user_vacations(user_id) if v[0] == vacation_id), None)
    )
    if days_requested > remaining_days:
        await update.message.reply_text(f"Лимит (28 дней) превышен. Осталось {remaining_days} дней, запрос: {days_requested} дней. Укажите другие даты или /cancel.")
        return NEW_START_DATE
    if check_vacation_overlap(
        user_id,
        context.user_data.get('new_start_date') or get_user_vacations(user_id)[0][1],
        context.user_data.get('new_end_date') or get_user_vacations(user_id)[0][2],
        vacation_id
    ):
        await update.message.reply_text("Новый отпуск пересекается с вашим. Укажите другие даты или /cancel.")
        return NEW_START_DATE
    await update.message.reply_text("Укажите @username нового замещающего, /skip или /remove.")
    return REPLACEMENT

async def edit_vacation_replacement(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.effective_chat.type == 'private':
        await update.message.reply_text("Все команды доступны только в личных сообщениях. Напиши мне в личку!")
        return ConversationHandler.END
    if not update.message or not update.message.text:
        logger.error(f"No message or text received in edit_vacation_replacement for chat {update.effective_chat.id}")
        await update.message.reply_text("Ошибка. Попробуйте снова или /cancel.")
        logger.info(f"User {context.user_data['user_id']} failed to edit vacation due to message error.")
        return ConversationHandler.END
    text = update.message.text.strip()
    logger.debug(f"Received replacement input: '{text}'")
    if text == "/skip":
        context.user_data['new_replacement_username'] = None
        vacation_id = context.user_data['vacation_id']
        if edit_vacation(vacation_id, context.user_data.get('new_start_date'), context.user_data.get('new_end_date'), None):
            user_id = context.user_data.get('user_id')
            username = context.user_data['username']
            name = context.user_data['name']
            start_date = context.user_data.get('new_start_date') or get_user_vacations(user_id)[0][1]
            end_date = context.user_data.get('new_end_date') or get_user_vacations(user_id)[0][2]
            await update.message.reply_text("Отпуск отредактирован.")
            # Отправляем уведомление в общий чат
            group_message = f"{name} (@{username}) изменил отпуск: с {start_date} по {end_date}"
            if context.user_data.get('new_replacement_username'):
                group_message += f", замещающий: {context.user_data['new_replacement_username']}"
            await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=group_message)
            logger.info(f"User {user_id} edited vacation {vacation_id} without replacement.")
        else:
            await update.message.reply_text("Ошибка при редактировании. Попробуйте снова или /cancel.")
            logger.error(f"User {context.user_data['user_id']} failed to edit vacation {vacation_id}: database error.")
        return ConversationHandler.END
    if text == "/remove":
        context.user_data['new_replacement_username'] = None
        vacation_id = context.user_data['vacation_id']
        if edit_vacation(vacation_id, context.user_data.get('new_start_date'), context.user_data.get('new_end_date'), None):
            user_id = context.user_data.get('user_id')
            username = context.user_data['username']
            name = context.user_data['name']
            start_date = context.user_data.get('new_start_date') or get_user_vacations(user_id)[0][1]
            end_date = context.user_data.get('new_end_date') or get_user_vacations(user_id)[0][2]
            await update.message.reply_text("Замещающий удалён. Укажите нового или /skip.")
            # Отправляем уведомление в общий чат
            group_message = f"{name} (@{username}) изменил отпуск: с {start_date} по {end_date}"
            await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=group_message)
            logger.info(f"User {user_id} removed replacement for vacation {vacation_id}.")
        else:
            await update.message.reply_text("Ошибка при удалении. Попробуйте снова или /cancel.")
            logger.error(f"User {context.user_data['user_id']} failed to remove replacement for vacation {vacation_id}: database error.")
        return REPLACEMENT
    if text == "/cancel":
        return await cancel(update, context)
    # Жёсткое ограничение: принимаем только текст, начинающийся с @, иначе ошибка
    if text.startswith('@'):
        context.user_data['new_replacement_username'] = text
        vacation_id = context.user_data['vacation_id']
        user_id = context.user_data.get('user_id')
        if not user_id:
            logger.error(f"Не удалось определить user_id в контексте для чата {update.effective_chat.id}")
            await update.message.reply_text("Ошибка: пользователь не определён. Обратитесь к @Admin.")
            return ConversationHandler.END
        if edit_vacation(
            vacation_id,
            context.user_data.get('new_start_date'),
            context.user_data.get('new_end_date'),
            context.user_data['new_replacement_username']
        ):
            current_year = datetime.now().year
            vacation_count, vacation_list = get_user_vacations_count(user_id, current_year)
            vacation_lines = [f"{i+1}. {start} – {end}" for i, (start, end) in enumerate(vacation_list)]
            total_days = sum(calculate_vacation_days(start, end) for start, end in vacation_list)
            vacation_info = f"Отпусков в {current_year}: {vacation_count}\n" + "\n".join(vacation_lines) if vacation_list else "Нет запланированных отпусков."
            username = context.user_data['username']
            name = context.user_data['name']
            start_date = context.user_data.get('new_start_date') or get_user_vacations(user_id)[0][1]
            end_date = context.user_data.get('new_end_date') or get_user_vacations(user_id)[0][2]
            message = (
                "ОТПУСК ОТРЕДАКТИРОВАН!\n\n"
                f"Сотрудник: {name} (@{username})\n"
                f"Даты: {start_date} - {end_date}\n"
                f"Замещающий: {context.user_data['new_replacement_username']}\n"
                f"{vacation_info}\n"
                f"Использовано дней: {total_days} из 28\n\n"
                "Вопросы? @Admin"
            )
            try:
                await update.message.reply_text(message)
                # Отправляем уведомление в общий чат
                group_message = f"{name} (@{username}) изменил отпуск: с {start_date} по {end_date}"
                if context.user_data['new_replacement_username']:
                    group_message += f", замещающий: {context.user_data['new_replacement_username']}"
                await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=group_message)
            except Exception as e:
                logger.error(f"Ошибка при отправке сообщения: {e}")
                await update.message.reply_text("Отпуск отредактирован, но возникла ошибка при отправке подтверждения. Обратитесь к @Admin.")
            logger.info(f"User {user_id} edited vacation {vacation_id}: {context.user_data.get('new_start_date')} - {context.user_data.get('new_end_date')}, replacement: {text}")
        else:
            await update.message.reply_text("Ошибка при редактировании. Попробуйте снова или /cancel.")
            logger.error(f"User {context.user_data['user_id']} failed to edit vacation {vacation_id}: database error.")
        return ConversationHandler.END
    await update.message.reply_text("Неверный формат. Введите @username, /skip, /remove или /cancel.")
    return REPLACEMENT

# Уведомления и команды для всех (в PM)
async def notify_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat.type == 'private':
        await update.message.reply_text("Все команды доступны только в личных сообщениях. Напиши мне в личку!")
        return
    try:
        from database.db_operations import get_upcoming_vacations
        current_date = datetime.now().date()
        seven_days_later = current_date + timedelta(days=7)  # Используем date
        vacations = get_upcoming_vacations(seven_days_later)
        
        if not vacations:
            await update.message.reply_text("На ближайшие 7 дней отпусков нет.")
            return
        
        message = "СПИСОК ПРЕДСТОЯЩИХ ОТПУСКОВ НА 7 ДНЕЙ:\n\n"
        for user_id, full_name, username, start_date, end_date, replacement in vacations:
            replacement = f" (Замещает: {replacement})" if replacement else ""
            message += f"{full_name} (@{username}): {start_date} - {end_date}{replacement}\n"
        message += "Вопросы? @Admin"
        
        await update.message.reply_text(message)
    except Exception as e:
        logger.error(f"Ошибка при обработке /notify: {e}")
        await update.message.reply_text("Ошибка при получении отпусков. Попробуйте позже.")

# Админские команды (скрыты для обычных пользователей, доступны только в PM)
async def list_employees(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat.type == 'private' or not is_admin(update.effective_chat.id):
        await update.message.reply_text("Эта команда доступна только администратору в личных сообщениях. Напиши мне в личку!")
        return
    from database.db_operations import list_employees_db
    employees = list_employees_db()
    if employees:
        message = "СПИСОК СОТРУДНИКОВ:\n\n"
        for employee in employees:
            # Форматируем с отступами: ID, ФИО, отпуск (даты), замещающий
            parts = employee.split(', ')
            message += f"ID: {parts[0]}\n"
            message += f"ФИО: {parts[1]}\n"
            message += f"Отпуск: {parts[2]}\n"
            message += f"Замещающий: {parts[3]}\n\n"
        await update.message.reply_text(message.rstrip())  # Убираем лишний перенос строки в конце
    else:
        await update.message.reply_text("Список сотрудников пуст.")
    logger.info(f"Admin {update.effective_chat.id} requested employee list.")

async def delete_employee_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.effective_chat.type == 'private' or not is_admin(update.effective_chat.id):
        await update.message.reply_text("Эта команда доступна только администратору в личных сообщениях. Напиши мне в личку!")
        return ConversationHandler.END
    await update.message.reply_text("Укажите ID сотрудника для удаления:")
    return DELETE_EMPLOYEE_ID

async def delete_employee_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text.strip()
    if text == "/cancel":
        await update.message.reply_text("Действие отменено.")
        return ConversationHandler.END
    try:
        employee_id = int(text)
        from database.db_operations import delete_employee
        if delete_employee(employee_id):
            await update.message.reply_text(f"СОТРУДНИК С ID {employee_id} УДАЛЁН.")
            logger.info(f"Admin {update.effective_chat.id} deleted employee {employee_id}.")
        else:
            await update.message.reply_text(f"Сотрудник с ID {employee_id} не найден.")
            logger.error(f"Admin {update.effective_chat.id} failed to delete employee {employee_id}: not found.")
    except ValueError:
        await update.message.reply_text("ID должен быть числом. Попробуйте снова или /cancel.")
        return DELETE_EMPLOYEE_ID
    return ConversationHandler.END

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat.type == 'private' or not is_admin(update.effective_chat.id):
        await update.message.reply_text("Эта команда доступна только администратору в личных сообщениях. Напиши мне в личку!")
        return
    from database.db_operations import get_vacation_stats
    stats = get_vacation_stats()
    message = "СТАТИСТИКА ОТПУСКОВ:\n\n"
    for month, count, days in stats:
        message += f"Месяц {month}: {count} отпусков, {days:.0f} дней\n"
    total_vacations = sum(row[1] for row in stats)
    total_days = sum(row[2] for row in stats)
    message += f"\nВсего: {total_vacations} отпусков, {total_days:.0f} дней\n\nВопросы? @Admin"
    await update.message.reply_text(message)
    logger.info(f"Admin {update.effective_chat.id} requested vacation stats.")

async def export_employees(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat.type == 'private' or not is_admin(update.effective_chat.id):
        await update.message.reply_text("Эта команда доступна только администратору в личных сообщениях. Напиши мне в личку!")
        return
    from database.db_operations import get_all_vacations
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
    try:
        await update.message.reply_document(
            document=InputFile(buffer, filename='employees_vacations.xlsx'),
            caption="Список сотрудников и отпусков выгружен.\n\nВопросы? @Admin"
        )
    except Exception as e:
        logger.error(f"Ошибка при отправке файла: {e}")
        await update.message.reply_text("Ошибка при выгрузке файла. Обратитесь к @Admin.")
    finally:
        buffer.close()
    logger.info(f"Admin {update.effective_chat.id} exported employees list to XLSX.")

# Обработчики (явное определение для импорта)
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
        REPLACEMENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_vacation_replacement)],
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

invalid_command_handler = MessageHandler(filters.COMMAND & ~filters.Regex(r'^/(cancel|start|help)$'), handle_invalid_command, filters.ChatType.PRIVATE)