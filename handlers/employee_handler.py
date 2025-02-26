import logging
import sqlite3
import re
from datetime import datetime, timedelta
from typing import Tuple, Optional, List

from telegram import BotCommand, ReplyKeyboardMarkup, Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ConversationHandler, MessageHandler, filters, CommandHandler, ContextTypes, CallbackQueryHandler
from telegram.error import BadRequest

from database.db_operations import (
    add_employee_to_db, add_vacation, get_upcoming_vacations, get_user_vacations, edit_vacation, check_vacation_overlap,
    list_employees_db, delete_employee, clear_all_employees, calculate_vacation_days,
    get_used_vacation_days, get_vacation_stats, get_all_vacations, get_employee_by_username, delete_vacation, DB_PATH
)
from utils.helpers import escape_markdown_v2, identify_user, is_admin
import os
from dotenv import load_dotenv

load_dotenv()
ADMIN_ID = int(os.getenv('ADMIN_ID'))
GROUP_CHAT_ID = int(os.getenv('GROUP_CHAT_ID'))

logger = logging.getLogger(__name__)

# Состояния для ConversationHandler
REGISTER = 0
START_DATE, END_DATE, REPLACEMENT_FULL_NAME, REPLACEMENT = range(1, 5)
SELECT_VACATION, NEW_START_DATE, NEW_END_DATE, NEW_REPLACEMENT_FULL_NAME, NEW_REPLACEMENT = range(5, 10)
DELETE_EMPLOYEE_ID = 100
DELETE_VACATION_SELECT = 200
CLEAR_ALL_CONFIRM = 300

VACATION_LIMIT_DAYS = 28

MONTHS = {
    "January": "Январь", "February": "Февраль", "March": "Март", "April": "Апрель",
    "May": "Май", "June": "Июнь", "July": "Июль", "August": "Август",
    "September": "Сентябрь", "October": "Октябрь", "November": "Ноябрь", "December": "Декабрь"
}

def validate_full_name(full_name: str) -> Tuple[bool, str]:
    """Валидация ФИО: три слова, только русские буквы, минимум 2 буквы в каждом слове."""
    if not re.match(r'^[А-Яа-яЁё\s]+$', full_name):
        return False, "ФИО должно содержать только русские буквы и пробелы."
    parts = full_name.strip().split()
    if len(parts) != 3:
        return False, "ФИО должно состоять из трёх слов (например, Иванов Иван Иванович)."
    for part in parts:
        if len(part) < 2:
            return False, "Каждое слово в ФИО должно содержать минимум 2 буквы."
    return True, ""

def validate_date_input(date_str: str, is_start_date: bool = True, reference_date: Optional[str] = None) -> Tuple[bool, str]:
    """Валидация даты."""
    try:
        date = datetime.strptime(date_str, "%Y-%m-%d")
        if is_start_date and date <= datetime.now():
            return False, "Дата должна быть в будущем."
        if reference_date:
            ref_date = datetime.strptime(reference_date, "%Y-%m-%d")
            if date <= ref_date:
                return False, "Дата окончания должна быть позже даты начала."
        return True, ""
    except ValueError:
        return False, "Некорректный формат. Используйте YYYY-MM-DD (например, 2025-03-01)."

async def reset_state(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Сбрасывает состояние пользователя."""
    context.user_data.clear()
    logger.info("Состояние пользователя сброшено.")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Отмена текущего действия."""
    user_id = update.effective_user.id
    logger.info(f"Пользователь {user_id} отменил действие")
    await reset_state(context)
    await update.message.reply_text("Действие отменено.")
    return ConversationHandler.END

async def check_user_permissions(update: Update, context: ContextTypes.DEFAULT_TYPE, require_admin: bool = False) -> bool:
    """Проверка прав пользователя."""
    user_id = update.effective_user.id
    chat_type = update.effective_chat.type
    if chat_type != 'private':
        logger.info(f"Команда проигнорирована в чате {update.effective_chat.id} (не личный чат)")
        await update.message.reply_text("Все команды доступны только в личных сообщениях. Напишите мне в личку!")
        return False
    try:
        member = await context.bot.get_chat_member(chat_id=GROUP_CHAT_ID, user_id=user_id)
        if member.status not in ["member", "administrator", "creator"]:
            await update.message.reply_text("Вы не состоите в разрешённой группе. Обратитесь к @Admin для доступа.")
            logger.warning(f"Пользователь {user_id} не состоит в группе {GROUP_CHAT_ID}")
            return False
    except BadRequest as e:
        logger.error(f"Ошибка проверки членства пользователя {user_id} в группе {GROUP_CHAT_ID}: {e}")
        await update.message.reply_text("Ошибка проверки доступа. Обратитесь к @Admin.")
        return False
    if require_admin and not is_admin(user_id):
        await update.message.reply_text("Эта команда доступна только администратору.")
        logger.warning(f"Пользователь {user_id} без прав админа пытался выполнить админскую команду")
        return False
    return True

async def load_user_data(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Загрузка данных пользователя из базы."""
    user_id = update.effective_user.id
    username = update.effective_user.username
    if not username:
        await update.message.reply_text("Не удалось определить ваш username. Обратитесь к @Admin.")
        return False

    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, full_name, username FROM employees WHERE telegram_id = ?", (user_id,))
        result = cursor.fetchone()

    if not result:
        if context.user_data.get('state') == REGISTER:
            await update.message.reply_text(
                "Вы не зарегистрированы. Введите ваше полное ФИО ещё раз для завершения регистрации (например, Иванов Иван Иванович)."
            )
        else:
            await update.message.reply_text("Вы не зарегистрированы. Используйте /start для регистрации.")
        return False

    db_user_id, full_name, db_username = result
    context.user_data['user_id'] = db_user_id
    context.user_data['name'] = full_name
    context.user_data['username'] = db_username
    logger.info(f"Данные пользователя {user_id} загружены: {full_name} (@{db_username})")
    return True

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начало работы с ботом и регистрация."""
    user_id = update.effective_user.id
    chat_type = update.effective_chat.type
    username = update.effective_user.username
    logger.info(f"Команда /start вызвана пользователем {user_id}")

    if chat_type != 'private':
        logger.info(f"Команда /start проигнорирована в чате {update.effective_chat.id}")
        await update.message.reply_text("Все команды доступны только в личных сообщениях.")
        return ConversationHandler.END

    if not username:
        await update.message.reply_text("Не удалось определить ваш username. Обратитесь к @Admin.")
        return ConversationHandler.END

    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, full_name FROM employees WHERE telegram_id = ?", (user_id,))
        result = cursor.fetchone()

    if result:
        context.user_data['user_id'], context.user_data['name'] = result
        context.user_data['username'] = username
        logger.info(f"Пользователь {user_id} уже зарегистрирован: {context.user_data['name']} (@{username})")
        await set_full_commands(context)
        await show_menu(update, context)
        return ConversationHandler.END

    # Если уже в процессе регистрации, напоминаем
    if context.user_data.get('awaiting_fio'):
        await update.message.reply_text(
            f"@{username}, вы уже начали регистрацию. Введите ваше полное ФИО (например, Иванов Иван Иванович) или отмените с помощью /cancel."
        )
        return REGISTER

    context.user_data['username'] = username
    context.user_data['awaiting_fio'] = True
    await update.message.reply_text(
        f"Привет, @{username}!\n\n"
        "Это бот для управления отпусками. Чтобы начать, укажите ваше полное ФИО (например, Иванов Иван Иванович).\n"
        "После регистрации вы получите доступ ко всем командам."
    )
    logger.info(f"Пользователь {user_id} начал регистрацию")
    return REGISTER

async def register(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка регистрации пользователя."""
    user_id = update.effective_user.id
    input_text = update.message.text.strip()
    logger.info(f"Получен ввод ФИО от пользователя {user_id}: {input_text}")

    if input_text == "/cancel":
        return await cancel(update, context)

    is_valid, error = validate_full_name(input_text)
    if not is_valid:
        await update.message.reply_text(
            f"Ошибка: {error}\n"
            "Введите ФИО заново (например, Иванов Иван Иванович)."
        )
        logger.warning(f"Некорректный ввод ФИО от пользователя {user_id}: {input_text} - {error}")
        return REGISTER

    full_name = input_text
    username = context.user_data['username']
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM employees WHERE telegram_id = ?", (user_id,))
            existing = cursor.fetchone()
            if existing:
                cursor.execute("UPDATE employees SET full_name = ? WHERE telegram_id = ?", (full_name, user_id))
                conn.commit()
                db_user_id = existing[0]
                logger.info(f"Обновлено ФИО для пользователя {user_id}: {full_name}")
            else:
                db_user_id = add_employee_to_db(full_name, username, user_id)
                if db_user_id is None:
                    logger.error(f"Не удалось добавить сотрудника username={username}")
                    await update.message.reply_text("Ошибка при регистрации. Обратитесь к @Admin.")
                    return ConversationHandler.END
                logger.info(f"Добавлен новый пользователь {user_id}: {full_name} (@{username})")

        context.user_data['user_id'] = db_user_id
        context.user_data['name'] = full_name
        context.user_data['username'] = username
        context.user_data.pop('awaiting_fio', None)  # Очищаем флаг
        await update.message.reply_text(
            f"Регистрация завершена! Добро пожаловать, {full_name} (@{username}).\n"
            "Теперь вы можете использовать команды бота."
        )
        await set_full_commands(context)
        await show_menu(update, context)
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"Ошибка при регистрации пользователя {user_id}: {str(e)}", exc_info=True)
        await update.message.reply_text(f"Произошла ошибка: {str(e)}. Обратитесь к @Admin.")
        return ConversationHandler.END

async def show_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Показ меню после регистрации."""
    user_id = update.effective_user.id
    full_name = context.user_data['name']
    username = context.user_data['username']

    if not await check_user_permissions(update, context):
        return ConversationHandler.END

    keyboard = [
        ["/add_vacation", "/edit_vacation"],
        ["/delete_vacation", "/notify"],
        ["/my_vacations"],
    ]
    if is_admin(user_id):
        keyboard.append(["/list_employees", "/stats"])
        keyboard.append(["/delete_employee", "/export_employees"])
        keyboard.append(["/clear_all_employees"])
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)

    message = (
        f"👋 Привет, {full_name} (@{username})!\n"
        "Я бот для управления отпусками. Вот что я умею:\n\n"
        "📅 /add_vacation — Добавить свой отпуск\n"
        "✏️ /edit_vacation — Изменить существующий отпуск\n"
        "🗑️ /delete_vacation — Удалить свой отпуск\n"
        "🔔 /notify — Показать предстоящие отпуска на 7 дней\n"
        "📋 /my_vacations — Показать список ваших отпусков\n"  # Новая строка
        "🚫 /cancel — Отменить текущее действие\n\n"
        "Даты вводите в формате YYYY-MM-DD (например, 2025-03-01)."
    )
    if is_admin(user_id):
        message += (
            "\n\nКоманды для администратора:\n"
            "👥 /list_employees — Показать список сотрудников\n"
            "🗑️ /delete_employee — Удалить сотрудника по ID\n"
            "📊 /stats — Статистика отпусков по месяцам\n"
            "📤 /export_employees — Выгрузить данные в Excel\n"
            "⚠️ /clear_all_employees — Удалить всех сотрудников и их отпуска\n\n"
            "Вы админ, так что управляйте всем через личку!"
        )
    message += "\nВопросы? Пишите @Admin."

    await update.message.reply_text(message, reply_markup=reply_markup)
    logger.info(f"Пользователь {user_id} получил меню команд")
    return ConversationHandler.END

async def handle_invalid_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка некорректных команд."""
    user_id = update.effective_user.id
    chat_type = update.effective_chat.type
    command = update.message.text
    logger.info(f"Получена команда {command} от пользователя {user_id}")

    if chat_type != 'private':
        logger.info(f"Команда {command} проигнорирована в чате {update.effective_chat.id}")
        await update.message.reply_text("Все команды доступны только в личных сообщениях.")
        return ConversationHandler.END

    if not await load_user_data(update, context):
        return ConversationHandler.END

    if context.user_data.get('action'):
        action = context.user_data['action']
        state = context.user_data.get('state', ConversationHandler.END)
        if action == "добавление отпуска":
            if state == START_DATE:
                await update.message.reply_text("Жду дату начала (YYYY-MM-DD, например, 2025-03-01) или выйди через /cancel.")
            elif state == END_DATE:
                await update.message.reply_text("Жду дату окончания (YYYY-MM-DD, например, 2025-03-15) или выйди через /cancel.")
            elif state == REPLACEMENT_FULL_NAME:
                await update.message.reply_text("Жду ФИО замещающего или /skip или выйди через /cancel.")
            elif state == REPLACEMENT:
                await update.message.reply_text("Жду @username замещающего или /skip или выйди через /cancel.")
        elif action == "редактирование отпуска":
            if state == SELECT_VACATION:
                await update.message.reply_text("Жду выбор отпуска из списка или выйди через /cancel.")
            elif state == NEW_START_DATE:
                await update.message.reply_text("Жду новую дату начала (YYYY-MM-DD) или /skip или выйди через /cancel.")
            elif state == NEW_END_DATE:
                await update.message.reply_text("Жду новую дату окончания (YYYY-MM-DD) или /skip или выйди через /cancel.")
            elif state == NEW_REPLACEMENT_FULL_NAME:
                await update.message.reply_text("Жду ФИО нового замещающего или /skip или выйди через /cancel.")
            elif state == NEW_REPLACEMENT:
                await update.message.reply_text("Жду @username нового замещающего, /skip или /remove или выйди через /cancel.")
        elif action == "удаление отпуска":
            if state == DELETE_VACATION_SELECT:
                await update.message.reply_text("Жду выбор отпуска для удаления или выйди через /cancel.")
        elif action == "удаление сотрудника":
            if state == DELETE_EMPLOYEE_ID:
                await update.message.reply_text("Жду ID сотрудника для удаления (число) или выйди через /cancel.")
        elif action == "очистка всех данных":
            if state == CLEAR_ALL_CONFIRM:
                await update.message.reply_text("Жду подтверждение очистки (/yes или /no).")
        logger.info(f"Пользователь {user_id} ввёл команду {command} на этапе {state} действия '{action}'")
        return state
    else:
        await update.message.reply_text("Неизвестная команда. Используйте кнопки меню или /start.")
        return ConversationHandler.END

async def handle_random_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка случайного текста."""
    user_id = update.effective_user.id
    chat_type = update.effective_chat.type
    text = update.message.text
    logger.info(f"Получен случайный текст от пользователя {user_id}: {text}")

    if chat_type != 'private':
        logger.info(f"Случайный текст проигнорирован в чате {update.effective_chat.id}")
        return

    if not await load_user_data(update, context):
        return

    if context.user_data.get('action'):
        await update.message.reply_text("Ожидаю корректный ввод для текущего действия. Используй /cancel, чтобы выйти.")
    else:
        await update.message.reply_text("Я не понимаю, что вы имеете в виду. Используйте команды из меню.")

async def add_vacation_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начало процесса добавления отпуска."""
    user_id = update.effective_user.id
    chat_type = update.effective_chat.type
    logger.info(f"Пользователь {user_id} начал добавление отпуска")

    if chat_type != 'private':
        logger.info(f"Команда /add_vacation проигнорирована в чате {update.effective_chat.id}")
        await update.message.reply_text("Все команды доступны только в личных сообщениях.")
        return ConversationHandler.END

    if not await load_user_data(update, context):
        return ConversationHandler.END

    if not await check_user_permissions(update, context):
        return ConversationHandler.END
    if context.user_data.get('action'):
        await update.message.reply_text("Сначала заверши текущее действие или выйди через /cancel.")
        logger.warning(f"Пользователь {user_id} пытался начать новое действие поверх текущего")
        return ConversationHandler.END

    context.user_data['action'] = "добавление отпуска"
    context.user_data['state'] = START_DATE
    await update.message.reply_text(
        f"Привет, {context.user_data['name']} (@{context.user_data['username']})!\n\n"
        "Укажите дату начала отпуска (YYYY-MM-DD, например, 2025-03-01) или /cancel."
    )
    logger.info(f"Пользователь {user_id} успешно начал добавление отпуска")
    return START_DATE

async def add_vacation_start_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await handle_date_input(update, context, END_DATE, 'start_date', is_start_date=True)

async def add_vacation_end_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await handle_date_input(update, context, REPLACEMENT_FULL_NAME, 'end_date', is_start_date=False, check_overlap=True)

async def add_vacation_replacement_full_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Сбор ФИО замещающего."""
    user_id = update.effective_user.id
    input_text = update.message.text.strip()
    logger.info(f"Получен ввод ФИО замещающего от пользователя {user_id}: {input_text}")

    if input_text == "/cancel":
        return await cancel(update, context)
    elif input_text == "/skip":
        context.user_data['replacement_full_name'] = None
    else:
        is_valid, error = validate_full_name(input_text)
        if not is_valid:
            await update.message.reply_text(
                f"Ошибка: {error}\n"
                "Введите ФИО замещающего заново (например, Петров Пётр Петрович) или /skip."
            )
            logger.warning(f"Некорректный ввод ФИО замещающего от пользователя {user_id}: {input_text} - {error}")
            return REPLACEMENT_FULL_NAME
        context.user_data['replacement_full_name'] = input_text

    context.user_data['state'] = REPLACEMENT
    await update.message.reply_text("Укажи @username замещающего или /skip, если нет.")
    return REPLACEMENT

async def add_vacation_replacement(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Завершение добавления отпуска с указанием замещающего."""
    user_id = update.effective_user.id
    input_text = update.message.text.strip()
    logger.info(f"Получен ввод логина замещающего от пользователя {user_id}: {input_text}")

    if input_text == "/cancel":
        return await cancel(update, context)
    elif input_text == "/skip":
        context.user_data['replacement_username'] = None
    elif input_text.startswith('@'):
        context.user_data['replacement_username'] = input_text
    else:
        await update.message.reply_text("Введи @username замещающего или /skip.")
        logger.warning(f"Пользователь {user_id} ввёл некорректный логин замещающего: {input_text}")
        return REPLACEMENT

    db_user_id = context.user_data['user_id']
    start_date = context.user_data['start_date']
    end_date = context.user_data['end_date']
    replacement = context.user_data['replacement_username']
    replacement_full_name = context.user_data['replacement_full_name']
    username = context.user_data['username']
    full_name = context.user_data['name']

    try:
        if add_vacation(db_user_id, start_date, end_date, replacement, replacement_full_name):
            current_year = datetime.now().year
            vacations = get_user_vacations(db_user_id)
            used_days = get_used_vacation_days(db_user_id, current_year)
            vacation_lines = [f"{i+1}. {start} – {end}" for i, (_, start, end, _) in enumerate(vacations)]
            vacation_info = f"Отпусков в {current_year}: {len(vacations)}\n" + "\n".join(vacation_lines) if vacations else "Нет запланированных отпусков."
            message = (
                "ОТПУСК ДОБАВЛЕН!\n\n"
                f"Сотрудник: {full_name} (@{username})\n"
                f"Даты: {start_date} - {end_date}\n"
                f"Замещающий: {replacement_full_name or replacement or 'Нет'}\n"
                f"{vacation_info}\n"
                f"Использовано дней: {used_days}\n\n"
                "Вопросы? @Admin"
            )
            await update.message.reply_text(message)
            group_message = (
                f"🌴 {full_name} (@{username}) взял отпуск:\n"
                f"📅 С {start_date} по {end_date}"
            )
            if replacement_full_name or replacement:
                replacement_text = replacement_full_name or ''
                if replacement:
                    replacement_text += f" ({replacement})" if replacement_full_name else replacement
                group_message += f"\n👤 Замещающий: {replacement_text}"
            group_message += "\n\n🎯 Fyi @Admin"
            await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=group_message)
            logger.info(f"Пользователь {user_id} успешно добавил отпуск: {start_date} - {end_date}")
        else:
            logger.error(f"Не удалось добавить отпуск для user_id={db_user_id}")
            await update.message.reply_text("Ошибка при добавлении. Начни заново с /add_vacation.")
    except Exception as e:
        logger.error(f"Ошибка при добавлении отпуска для user_id={db_user_id}: {str(e)}", exc_info=True)
        await update.message.reply_text(f"Произошла ошибка: {str(e)}. Обратитесь к @Admin.")
    await reset_state(context)
    return ConversationHandler.END

async def edit_vacation_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начало редактирования отпуска."""
    user_id = update.effective_user.id
    chat_type = update.effective_chat.type
    logger.info(f"Пользователь {user_id} начал редактирование отпуска")

    if chat_type != 'private':
        logger.info(f"Команда /edit_vacation проигнорирована в чате {update.effective_chat.id}")
        await update.message.reply_text("Все команды доступны только в личных сообщениях.")
        return ConversationHandler.END

    if not await load_user_data(update, context):
        return ConversationHandler.END

    if not await check_user_permissions(update, context):
        return ConversationHandler.END
    if context.user_data.get('action'):
        await update.message.reply_text("Сначала заверши текущее действие или выйди через /cancel.")
        logger.warning(f"Пользователь {user_id} пытался начать новое действие поверх текущего")
        return ConversationHandler.END

    db_user_id = context.user_data['user_id']
    try:
        vacations = get_user_vacations(db_user_id)
        if not vacations:
            await update.message.reply_text("У вас нет отпусков для редактирования.")
            logger.info(f"У пользователя {user_id} нет отпусков для редактирования")
            return ConversationHandler.END
    except Exception as e:
        logger.error(f"Ошибка при получении отпусков для user_id={user_id}: {str(e)}", exc_info=True)
        await update.message.reply_text(f"Произошла ошибка: {str(e)}. Обратитесь к @Admin.")
        return ConversationHandler.END

    context.user_data.update({
        'vacations': vacations,
        'action': "редактирование отпуска",
        'state': SELECT_VACATION
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
    logger.info(f"Пользователь {user_id} получил список отпусков для редактирования")
    return SELECT_VACATION

async def select_vacation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    user_id = query.from_user.id
    vacation_id = int(query.data)
    logger.info(f"Пользователь {user_id} выбрал отпуск с ID={vacation_id} для редактирования")
    await query.answer()
    context.user_data['vacation_id'] = vacation_id

    # Получаем информацию о выбранном отпуске
    vacations = context.user_data.get('vacations', [])
    selected_vacation = next((v for v in vacations if v[0] == vacation_id), None)
    if not selected_vacation:
        await query.edit_message_text("Ошибка: выбранный отпуск не найден. Начни заново с /edit_vacation.")
        logger.error(f"Отпуск ID={vacation_id} не найден в списке отпусков пользователя {user_id}")
        await reset_state(context)
        return ConversationHandler.END

    start_date, end_date, replacement = selected_vacation[1], selected_vacation[2], selected_vacation[3]
    replacement_text = f" (Замещает: {replacement})" if replacement else ""
    message = (
        f"Вы выбрали отпуск для редактирования:\n"
        f"📅 {start_date} – {end_date}{replacement_text}"
    )
    await query.edit_message_text(message)
    context.user_data['state'] = NEW_START_DATE
    await query.message.reply_text("Укажите новую дату начала (YYYY-MM-DD, например, 2025-03-01) или /skip.")
    return NEW_START_DATE

async def edit_vacation_start_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await handle_date_input(update, context, NEW_END_DATE, 'new_start_date', is_start_date=True)

async def edit_vacation_end_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await handle_date_input(update, context, NEW_REPLACEMENT_FULL_NAME, 'new_end_date', is_start_date=False, check_overlap=True)

async def edit_vacation_replacement_full_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    input_text = update.message.text.strip()
    logger.info(f"Получен ввод ФИО нового замещающего от пользователя {user_id}: {input_text}")

    if input_text == "/cancel":
        return await cancel(update, context)
    elif input_text == "/skip":
        context.user_data['new_replacement_full_name'] = None
    else:
        is_valid, error = validate_full_name(input_text)
        if not is_valid:
            await update.message.reply_text(
                f"Ошибка: {error}\n"
                "Введите ФИО замещающего заново (например, Петров Пётр Петрович) или /skip."
            )
            logger.warning(f"Некорректный ввод ФИО замещающего от пользователя {user_id}: {input_text} - {error}")
            return NEW_REPLACEMENT_FULL_NAME
        context.user_data['new_replacement_full_name'] = input_text

    context.user_data['state'] = NEW_REPLACEMENT
    await update.message.reply_text("Укажи @username нового замещающего, /skip или /remove.")
    return NEW_REPLACEMENT

async def edit_vacation_replacement(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    input_text = update.message.text.strip().lower()
    logger.info(f"Получен ввод нового логина замещающего от пользователя {user_id}: {input_text}")

    if input_text == "/cancel":
        return await cancel(update, context)
    elif input_text in ["/skip", "/remove"]:
        context.user_data['new_replacement_username'] = None
        if input_text == "/remove":
            context.user_data['new_replacement_full_name'] = None
    elif input_text.startswith('@'):
        context.user_data['new_replacement_username'] = input_text
    else:
        await update.message.reply_text("Введи @username, /skip, /remove или выйди через /cancel.")
        logger.warning(f"Пользователь {user_id} ввёл некорректный логин замещающего: {input_text}")
        return NEW_REPLACEMENT

    vacation_id = context.user_data['vacation_id']
    db_user_id = context.user_data['user_id']
    new_start_date = context.user_data.get('new_start_date')
    new_end_date = context.user_data.get('new_end_date')
    new_replacement = context.user_data['new_replacement_username']
    new_replacement_full_name = context.user_data['new_replacement_full_name']
    username = context.user_data['username']
    full_name = context.user_data['name']

    try:
        vacations = get_user_vacations(db_user_id)
        old_vacation = next((v for v in vacations if v[0] == vacation_id), None)
        if not old_vacation:
            await update.message.reply_text("Отпуск не найден. Начни заново с /edit_vacation.")
            logger.error(f"Отпуск ID={vacation_id} не найден для пользователя {user_id}")
            await reset_state(context)
            return ConversationHandler.END
        old_start_date, old_end_date = old_vacation[1], old_vacation[2]

        # Определяем конечные даты
        start_date = new_start_date or old_start_date
        end_date = new_end_date or old_end_date

        # Проверка корректности диапазона дат
        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")
        if end_dt <= start_dt:
            await update.message.reply_text(
                f"Ошибка: дата окончания ({end_date}) должна быть позже даты начала ({start_date}). "
                "Начни заново с /edit_vacation."
            )
            logger.warning(f"Некорректный диапазон дат для пользователя {user_id}: {start_date} - {end_date}")
            await reset_state(context)
            return ConversationHandler.END

        # Проверка лимита и пересечений
        current_year = datetime.now().year
        used_days = get_used_vacation_days(db_user_id, current_year)
        days_requested = calculate_vacation_days(start_date, end_date)
        total_days = used_days - calculate_vacation_days(old_start_date, old_end_date) + days_requested
        if total_days > VACATION_LIMIT_DAYS:
            await update.message.reply_text(
                f"Лимит превышен. Использовано {used_days} дней, запрос: {days_requested} дней. "
                "Начни заново с /edit_vacation."
            )
            logger.warning(f"Превышен лимит отпусков для пользователя {user_id}: {total_days} > {VACATION_LIMIT_DAYS}")
            await reset_state(context)
            return ConversationHandler.END
        if check_vacation_overlap(db_user_id, start_date, end_date, vacation_id):
            await update.message.reply_text(
                "Новый отпуск пересекается с твоим. Начни заново с /edit_vacation."
            )
            logger.warning(f"Обнаружено пересечение отпусков для пользователя {user_id}: {start_date} - {end_date}")
            await reset_state(context)
            return ConversationHandler.END

        if edit_vacation(vacation_id, new_start_date, new_end_date, new_replacement, new_replacement_full_name):
            vacations = get_user_vacations(db_user_id)
            used_days = get_used_vacation_days(db_user_id, current_year)
            vacation_lines = [f"{i+1}. {start} – {end}" for i, (_, start, end, _) in enumerate(vacations)]
            vacation_info = f"Отпусков в {current_year}: {len(vacations)}\n" + "\n".join(vacation_lines) if vacations else "Нет запланированных отпусков."
            message = (
                "ОТПУСК ОТРЕДАКТИРОВАН!\n\n"
                f"Сотрудник: {full_name} (@{username})\n"
                f"Даты: {start_date} - {end_date}\n"
                f"Замещающий: {new_replacement_full_name or new_replacement or 'Нет'}\n"
                f"{vacation_info}\n"
                f"Использовано дней: {used_days}\n\n"
                "Вопросы? @Admin"
            )
            await update.message.reply_text(message)
            group_message = (
                f"✏️ {full_name} (@{username}) изменил отпуск:\n"
                f"Было: С {old_start_date} по {old_end_date}\n"
                f"Стало: С {start_date} по {end_date}\n"
            )
            if new_replacement_full_name or new_replacement:
                replacement_text = new_replacement_full_name or ''
                if new_replacement:
                    replacement_text += f" ({new_replacement})" if new_replacement_full_name else new_replacement
                group_message += f"👤 Замещающий: {replacement_text}"
            group_message += "\n\n🎯 Fyi @Admin"
            await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=group_message)
            logger.info(f"Пользователь {user_id} успешно отредактировал отпуск ID={vacation_id}")
        else:
            logger.error(f"Не удалось отредактировать отпуск ID={vacation_id} для user_id={db_user_id}")
            await update.message.reply_text("Ошибка при редактировании. Начни заново с /edit_vacation.")
    except Exception as e:
        logger.error(f"Ошибка при редактировании отпуска ID={vacation_id} для user_id={db_user_id}: {str(e)}", exc_info=True)
        await update.message.reply_text(f"Произошла ошибка: {str(e)}. Обратитесь к @Admin.")
    await reset_state(context)
    return ConversationHandler.END

async def delete_vacation_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    chat_type = update.effective_chat.type
    logger.info(f"Пользователь {user_id} начал удаление отпуска")

    if chat_type != 'private':
        logger.info(f"Команда /delete_vacation проигнорирована в чате {update.effective_chat.id}")
        await update.message.reply_text("Все команды доступны только в личных сообщениях.")
        return ConversationHandler.END

    if not await load_user_data(update, context):
        return ConversationHandler.END

    if not await check_user_permissions(update, context):
        return ConversationHandler.END
    if context.user_data.get('action'):
        await update.message.reply_text("Сначала заверши текущее действие или выйди через /cancel.")
        logger.warning(f"Пользователь {user_id} пытался начать новое действие поверх текущего")
        return ConversationHandler.END

    db_user_id = context.user_data['user_id']
    try:
        vacations = get_user_vacations(db_user_id)
        if not vacations:
            await update.message.reply_text("У вас нет отпусков для удаления.")
            logger.info(f"У пользователя {user_id} нет отпусков для удаления")
            return ConversationHandler.END
    except Exception as e:
        logger.error(f"Ошибка при получении отпусков для user_id={user_id}: {str(e)}", exc_info=True)
        await update.message.reply_text(f"Произошла ошибка: {str(e)}. Обратитесь к @Admin.")
        return ConversationHandler.END

    context.user_data.update({
        'vacations': vacations,
        'action': "удаление отпуска",
        'state': DELETE_VACATION_SELECT
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
    logger.info(f"Пользователь {user_id} получил список отпусков для удаления")
    return DELETE_VACATION_SELECT

async def delete_vacation_select(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    user_id = query.from_user.id
    vacation_id = int(query.data)
    logger.info(f"Пользователь {user_id} выбрал отпуск с ID={vacation_id} для удаления")
    await query.answer()
    vacations = context.user_data['vacations']
    vacation = next((v for v in vacations if v[0] == vacation_id), None)
    if not vacation:
        await query.edit_message_text("Отпуск не найден.")
        logger.warning(f"Отпуск с ID={vacation_id} не найден для пользователя {user_id}")
        await reset_state(context)
        return ConversationHandler.END

    try:
        if delete_vacation(vacation_id):
            start_date, end_date = vacation[1], vacation[2]
            username = context.user_data['username']
            full_name = context.user_data['name']
            await query.edit_message_text(f"Отпуск с {start_date} по {end_date} удалён.")
            group_message = (
                f"🚫 {full_name} (@{username}) отменил отпуск:\n"
                f"📅 С {start_date} по {end_date}\n\n"
                f"🎯 Fyi @Admin"
            )
            await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=group_message)
            logger.info(f"Пользователь {user_id} успешно удалил отпуск ID={vacation_id}")
        else:
            logger.error(f"Не удалось удалить отпуск ID={vacation_id} для пользователя {user_id}")
            await query.edit_message_text("Ошибка при удалении отпуска. Обратитесь к @Admin.")
    except Exception as e:
        logger.error(f"Ошибка при удалении отпуска ID={vacation_id} для пользователя {user_id}: {str(e)}", exc_info=True)
        await query.edit_message_text(f"Произошла ошибка: {str(e)}. Обратитесь к @Admin.")
    await reset_state(context)
    return ConversationHandler.END

async def notify_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    chat_type = update.effective_chat.type
    logger.info(f"Пользователь {user_id} запросил уведомления")

    if chat_type != 'private':
        logger.info(f"Команда /notify проигнорирована в чате {update.effective_chat.id}")
        await update.message.reply_text("Все команды доступны только в личных сообщениях.")
        return ConversationHandler.END

    if not await load_user_data(update, context):
        return ConversationHandler.END

    if not await check_user_permissions(update, context):
        return ConversationHandler.END
    if context.user_data.get('action'):
        await update.message.reply_text("Сначала заверши текущее действие или выйди через /cancel.")
        logger.warning(f"Пользователь {user_id} пытался запросить уведомления во время другого действия")
        return ConversationHandler.END

    try:
        current_date = datetime.now().date()
        seven_days_later = current_date + timedelta(days=7)
        vacations = get_upcoming_vacations(seven_days_later)
        # Фильтруем отпуска, которые начинаются или идут в ближайшие 7 дней
        upcoming = [
            v for v in vacations
            if datetime.strptime(v[3], "%Y-%m-%d").date() <= seven_days_later
            and datetime.strptime(v[4], "%Y-%m-%d").date() >= current_date
        ]
        if not upcoming:
            await update.message.reply_text("На ближайшие 7 дней отпусков нет.")
            logger.info(f"Для пользователя {user_id} нет предстоящих отпусков")
            return ConversationHandler.END
        message = "СПИСОК ПРЕДСТОЯЩИХ ОТПУСКОВ НА 7 ДНЕЙ:\n\n"
        for _, full_name, username, start_date, end_date, replacement in upcoming:
            with sqlite3.connect(DB_PATH) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT replacement_full_name FROM vacations WHERE start_date = ? AND user_id = (SELECT id FROM employees WHERE username = ?)", (start_date, username))
                result = cursor.fetchone()
                replacement_full_name = result[0] if result else None
            replacement_text = f" (Замещает: {replacement_full_name or replacement})" if replacement else ""
            message += f"{full_name} (@{username}): {start_date} - {end_date}{replacement_text}\n"
        message += "\nВопросы? @Admin"
        await update.message.reply_text(message)
        logger.info(f"Пользователь {user_id} получил список предстоящих отпусков")
    except Exception as e:
        logger.error(f"Ошибка при получении уведомлений для пользователя {user_id}: {str(e)}", exc_info=True)
        await update.message.reply_text(f"Произошла ошибка: {str(e)}. Обратитесь к @Admin.")
    return ConversationHandler.END

async def my_vacations(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    chat_type = update.effective_chat.type
    logger.info(f"Пользователь {user_id} запросил список своих отпусков")

    if chat_type != 'private':
        logger.info(f"Команда /my_vacations проигнорирована в чате {update.effective_chat.id}")
        await update.message.reply_text("Все команды доступны только в личных сообщениях.")
        return ConversationHandler.END

    if not await load_user_data(update, context):
        return ConversationHandler.END

    if not await check_user_permissions(update, context):
        return ConversationHandler.END
    if context.user_data.get('action'):
        await update.message.reply_text("Сначала заверши текущее действие или выйди через /cancel.")
        logger.warning(f"Пользователь {user_id} пытался запросить список отпусков во время другого действия")
        return ConversationHandler.END

    try:
        db_user_id = context.user_data['user_id']
        vacations = get_user_vacations(db_user_id)
        if not vacations:
            await update.message.reply_text("У вас нет запланированных отпусков.")
            logger.info(f"У пользователя {user_id} нет отпусков")
            return ConversationHandler.END

        message = "ВАШИ УСТАНОВЛЕННЫЕ ОТПУСКА:\n\n"
        for i, (_, start, end, replacement) in enumerate(vacations):
            with sqlite3.connect(DB_PATH) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT replacement_full_name FROM vacations WHERE start_date = ? AND user_id = ?", (start, db_user_id))
                result = cursor.fetchone()
                replacement_full_name = result[0] if result else None
            replacement_text = f" (Замещает: {replacement_full_name or replacement})" if replacement else ""
            message += f"{i+1}. {start} – {end}{replacement_text}\n"
        message += "\nВопросы? @Admin"
        await update.message.reply_text(message)
        logger.info(f"Пользователь {user_id} получил список своих отпусков")
    except Exception as e:
        logger.error(f"Ошибка при получении списка отпусков для пользователя {user_id}: {str(e)}", exc_info=True)
        await update.message.reply_text(f"Произошла ошибка: {str(e)}. Обратитесь к @Admin.")
    return ConversationHandler.END

async def list_employees(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    chat_type = update.effective_chat.type
    logger.info(f"Пользователь {user_id} запросил список сотрудников")

    if chat_type != 'private':
        logger.info(f"Команда /list_employees проигнорирована в чате {update.effective_chat.id}")
        await update.message.reply_text("Все команды доступны только в личных сообщениях.")
        return ConversationHandler.END

    if not await load_user_data(update, context):
        return ConversationHandler.END

    if not await check_user_permissions(update, context, require_admin=True):
        return ConversationHandler.END
    if context.user_data.get('action'):
        await update.message.reply_text("Сначала заверши текущее действие или выйди через /cancel.")
        logger.warning(f"Пользователь {user_id} пытался запросить список сотрудников во время другого действия")
        return ConversationHandler.END

    try:
        employees = list_employees_db()
        if employees:
            message = "СПИСОК СОТРУДНИКОВ:\n\n"
            for employee in employees:
                parts = employee.split(', ')
                message += f"ID: {parts[0]}\nЛогин: {parts[1]}\nФИО: {parts[2]}\nИспользовано дней: {parts[3]}\n\n"
            await update.message.reply_text(message.rstrip())
            logger.info(f"Пользователь {user_id} получил список сотрудников")
        else:
            await update.message.reply_text("Список сотрудников пуст.")
            logger.info(f"Список сотрудников пуст для пользователя {user_id}")
    except Exception as e:
        logger.error(f"Ошибка при получении списка сотрудников для пользователя {user_id}: {str(e)}", exc_info=True)
        await update.message.reply_text(f"Произошла ошибка: {str(e)}. Обратитесь к @Admin.")
    return ConversationHandler.END

async def delete_employee_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    chat_type = update.effective_chat.type
    logger.info(f"Пользователь {user_id} начал удаление сотрудника")

    if chat_type != 'private':
        logger.info(f"Команда /delete_employee проигнорирована в чате {update.effective_chat.id}")
        await update.message.reply_text("Все команды доступны только в личных сообщениях.")
        return ConversationHandler.END

    if not await load_user_data(update, context):
        return ConversationHandler.END

    if not await check_user_permissions(update, context, require_admin=True):
        return ConversationHandler.END
    if context.user_data.get('action'):
        await update.message.reply_text("Сначала заверши текущее действие или выйди через /cancel.")
        logger.warning(f"Пользователь {user_id} пытался начать новое действие поверх текущего")
        return ConversationHandler.END

    context.user_data['action'] = "удаление сотрудника"
    context.user_data['state'] = DELETE_EMPLOYEE_ID
    await update.message.reply_text("Укажите ID сотрудника для удаления:")
    logger.info(f"Пользователь {user_id} запросил ID сотрудника для удаления")
    return DELETE_EMPLOYEE_ID

async def delete_employee_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    input_text = update.message.text.strip()
    logger.info(f"Получен ввод ID сотрудника от пользователя {user_id}: {input_text}")

    if input_text == "/cancel":
        return await cancel(update, context)
    try:
        employee_id = int(input_text)
        if delete_employee(employee_id):
            await update.message.reply_text(f"👾 СОТРУДНИК С ID {employee_id} УДАЛЁН!")
            logger.info(f"Пользователь {user_id} успешно удалил сотрудника с ID={employee_id}")
        else:
            await update.message.reply_text(f"Сотрудник с ID {employee_id} не найден. Укажите ID сотрудника для удаления:")
            logger.warning(f"Сотрудник с ID={employee_id} не найден для пользователя {user_id}")
            return DELETE_EMPLOYEE_ID
    except ValueError:
        await update.message.reply_text("ID должен быть числом. Введи заново или выйди через /cancel.")
        logger.warning(f"Пользователь {user_id} ввёл некорректный ID сотрудника: {input_text}")
        return DELETE_EMPLOYEE_ID
    except Exception as e:
        logger.error(f"Ошибка при удалении сотрудника для пользователя {user_id}: {str(e)}", exc_info=True)
        await update.message.reply_text(f"Произошла ошибка: {str(e)}. Обратитесь к @Admin.")
    await reset_state(context)
    return ConversationHandler.END

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    chat_type = update.effective_chat.type
    logger.info(f"Пользователь {user_id} запросил статистику отпусков")

    if chat_type != 'private':
        logger.info(f"Команда /stats проигнорирована в чате {update.effective_chat.id}")
        await update.message.reply_text("Все команды доступны только в личных сообщениях.")
        return ConversationHandler.END

    if not await load_user_data(update, context):
        return ConversationHandler.END

    if not await check_user_permissions(update, context, require_admin=True):
        return ConversationHandler.END
    if context.user_data.get('action'):
        await update.message.reply_text("Сначала заверши текущее действие или выйди через /cancel.")
        logger.warning(f"Пользователь {user_id} пытался запросить статистику во время другого действия")
        return ConversationHandler.END

    try:
        stats = get_vacation_stats()
        message = "СТАТИСТИКА ОТПУСКОВ:\n\n"
        for month, count, days, employee_count in stats:
            message += f"Месяц {month}: {count} отпусков, {days:.0f} дней, {employee_count} сотрудников\n"
        total_vacations = sum(row[1] for row in stats)
        total_days = sum(row[2] for row in stats)
        all_vacations = get_all_vacations()
        unique_employees = len({vac[0] for vac in all_vacations if vac[3]})
        message += f"\nВсего: {total_vacations} отпусков, {total_days:.0f} дней, {unique_employees} сотрудников\n\nВопросы? @Admin"
        await update.message.reply_text(message)
        logger.info(f"Пользователь {user_id} получил статистику отпусков")
    except Exception as e:
        logger.error(f"Ошибка при получении статистики для пользователя {user_id}: {str(e)}", exc_info=True)
        await update.message.reply_text(f"Произошла ошибка: {str(e)}. Обратитесь к @Admin.")
    return ConversationHandler.END

async def export_employees(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    chat_type = update.effective_chat.type
    logger.info(f"Пользователь {user_id} запросил выгрузку данных сотрудников")

    if chat_type != 'private':
        logger.info(f"Команда /export_employees проигнорирована в чате {update.effective_chat.id}")
        await update.message.reply_text("Все команды доступны только в личных сообщениях.")
        return ConversationHandler.END

    if not await load_user_data(update, context):
        return ConversationHandler.END

    if not await check_user_permissions(update, context, require_admin=True):
        return ConversationHandler.END
    if context.user_data.get('action'):
        await update.message.reply_text("Сначала заверши текущее действие или выйди через /cancel.")
        logger.warning(f"Пользователь {user_id} пытался запросить выгрузку во время другого действия")
        return ConversationHandler.END

    try:
        import pandas as pd
        import io
        from telegram import InputFile
        employees = get_all_vacations()
        if not employees:
            await update.message.reply_text("Список сотрудников пуст.")
            logger.info(f"Список сотрудников пуст для выгрузки пользователем {user_id}")
            return ConversationHandler.END
        df = pd.DataFrame(employees, columns=['ID', 'ФИО', 'Логин', 'Дата начала', 'Дата окончания', 'Замещающий'])
        buffer = io.BytesIO()
        df.to_excel(buffer, index=False, engine='openpyxl')
        buffer.seek(0)
        await update.message.reply_document(
            document=InputFile(buffer, filename='employees_vacations.xlsx'),
            caption="Список сотрудников и отпусков выгружен.\n\nВопросы? @Admin"
        )
        buffer.close()
        logger.info(f"Пользователь {user_id} успешно выгрузил данные сотрудников")
    except Exception as e:
        logger.error(f"Ошибка при выгрузке данных для пользователя {user_id}: {str(e)}", exc_info=True)
        await update.message.reply_text(f"Произошла ошибка: {str(e)}. Обратитесь к @Admin.")
    return ConversationHandler.END

async def clear_all_employees_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    chat_type = update.effective_chat.type
    logger.info(f"Пользователь {user_id} начал очистку всех сотрудников")

    if chat_type != 'private':
        logger.info(f"Команда /clear_all_employees проигнорирована в чате {update.effective_chat.id}")
        await update.message.reply_text("Все команды доступны только в личных сообщениях.")
        return ConversationHandler.END

    if not await load_user_data(update, context):
        return ConversationHandler.END

    if not await check_user_permissions(update, context, require_admin=True):
        return ConversationHandler.END
    if context.user_data.get('action'):
        await update.message.reply_text("Сначала заверши текущее действие или выйди через /cancel.")
        logger.warning(f"Пользователь {user_id} пытался начать новое действие поверх текущего")
        return ConversationHandler.END

    context.user_data['action'] = "очистка всех данных"
    context.user_data['state'] = CLEAR_ALL_CONFIRM
    await update.message.reply_text("Ты уверен, что хочешь удалить всех сотрудников и их отпуска? Это действие необратимо.\n\nПодтверди: /yes или отмени: /no")
    logger.info(f"Пользователь {user_id} запросил подтверждение очистки")
    return CLEAR_ALL_CONFIRM

async def clear_all_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    input_text = update.message.text.strip().lower()
    logger.info(f"Получен ввод подтверждения очистки от пользователя {user_id}: {input_text}")

    if input_text == "/cancel":
        return await cancel(update, context)
    elif input_text == "/yes":
        try:
            result = clear_all_employees()
            if result:
                await update.message.reply_text("Все сотрудники и отпуска удалены.")
                logger.info(f"Пользователь {user_id} успешно очистил базу данных")
            else:
                logger.error(f"Не удалось очистить базу данных для пользователя {user_id}")
                await update.message.reply_text("Ошибка при очистке базы данных. Обратитесь к @Admin.")
        except Exception as e:
            logger.error(f"Ошибка при очистке базы данных для пользователя {user_id}: {str(e)}", exc_info=True)
            await update.message.reply_text(f"Произошла ошибка: {str(e)}. Обратитесь к @Admin.")
    elif input_text == "/no":
        await update.message.reply_text("Очистка отменена.")
        logger.info(f"Пользователь {user_id} отменил очистку")
    else:
        await update.message.reply_text("Введи /yes для подтверждения или /no для отмены.")
        logger.warning(f"Пользователь {user_id} ввёл некорректное подтверждение: {input_text}")
        return CLEAR_ALL_CONFIRM
    await reset_state(context)
    return ConversationHandler.END

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
            BotCommand("add_vacation", "Добавить отпуск"),
            BotCommand("edit_vacation", "Редактировать отпуск"),
            BotCommand("delete_vacation", "Удалить отпуск"),
            BotCommand("notify", "Показать предстоящие отпуска"),
            BotCommand("my_vacations", "Показать список ваших отпусков"),  # Новая строка
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
        logger.info("Полный список команд бота установлен")
    except Exception as e:
        logger.error(f"Ошибка при установке полного списка команд: {str(e)}", exc_info=True)

async def handle_date_input(update: Update, context: ContextTypes.DEFAULT_TYPE, next_state: int, key: str, 
                          is_start_date: bool = True, check_overlap: bool = False) -> int:
    """Универсальная обработка ввода даты."""
    user_id = update.effective_user.id
    input_text = update.message.text.strip()
    logger.info(f"Получен ввод даты от пользователя {user_id}: {input_text}")

    if input_text == "/cancel":
        return await cancel(update, context)
    if input_text == "/skip" and not is_start_date:  # Для даты окончания в редактировании
        context.user_data[key] = None
        context.user_data['state'] = next_state
        if next_state == NEW_REPLACEMENT_FULL_NAME:
            await update.message.reply_text("Укажи ФИО нового замещающего (например, Петров Пётр Петрович) или /skip.")
        return next_state

    # Определяем дату начала для проверки
    if is_start_date:
        reference_date = None
    else:
        vacation_id = context.user_data.get('vacation_id')
        if check_overlap and vacation_id:
            vacations = get_user_vacations(context.user_data['user_id'])
            old_vacation = next((v for v in vacations if v[0] == vacation_id), None)
            reference_date = context.user_data.get('new_start_date', old_vacation[1] if old_vacation else None)
            if not reference_date:
                await update.message.reply_text("Ошибка: дата начала отпуска не определена. Начни заново с /edit_vacation.")
                logger.error(f"Дата начала не найдена для отпуска ID={vacation_id} пользователя {user_id}")
                await reset_state(context)
                return ConversationHandler.END
        else:
            reference_date = context.user_data.get('start_date')

    is_valid, error = validate_date_input(input_text, is_start_date, reference_date)
    if not is_valid:
        await update.message.reply_text(f"{error} Введи заново или выйди через /cancel.")
        logger.warning(f"Некорректный ввод даты от пользователя {user_id}: {error}")
        return context.user_data['state']

    context.user_data[key] = input_text
    context.user_data['state'] = next_state

    if check_overlap:
        user_id_db = context.user_data['user_id']
        start_date = reference_date
        end_date = input_text
        current_year = datetime.now().year
        used_days = get_used_vacation_days(user_id_db, current_year)
        days_requested = calculate_vacation_days(start_date, end_date)
        if used_days + days_requested > VACATION_LIMIT_DAYS:
            await update.message.reply_text(
                f"Лимит превышен. Использовано {used_days} дней, запрос: {days_requested} дней. "
                "Введи другие даты или выйди через /cancel."
            )
            logger.warning(f"Превышен лимит отпусков для пользователя {user_id}: {used_days} + {days_requested} > {VACATION_LIMIT_DAYS}")
            return START_DATE if is_start_date else NEW_START_DATE
        vacation_id = context.user_data.get('vacation_id', None)
        if check_vacation_overlap(user_id_db, start_date, end_date, vacation_id):
            await update.message.reply_text("Этот отпуск пересекается с твоим. Введи другие даты или выйди через /cancel.")
            logger.warning(f"Обнаружено пересечение отпусков для пользователя {user_id}: {start_date} - {end_date}")
            return START_DATE if is_start_date else NEW_START_DATE

    if next_state == END_DATE:
        await update.message.reply_text("Укажи дату окончания (YYYY-MM-DD, например, 2025-03-15) или /cancel.")
    elif next_state == REPLACEMENT_FULL_NAME:
        await update.message.reply_text("Укажи ФИО замещающего (например, Петров Пётр Петрович) или /skip.")
    elif next_state == NEW_END_DATE:
        await update.message.reply_text("Укажите новую дату окончания (YYYY-MM-DD) или /skip.")
    elif next_state == NEW_REPLACEMENT_FULL_NAME:
        await update.message.reply_text("Укажи ФИО нового замещающего (например, Петров Пётр Петрович) или /skip.")
    return next_state

async def repeat_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Напоминание пользователю ввести ФИО при повторном /start."""
    user_id = update.effective_user.id
    username = context.user_data.get('username', update.effective_user.username)
    logger.info(f"Повторный /start вызван пользователем {user_id} в процессе регистрации")
    await update.message.reply_text(
        f"@{username}, вы уже начали регистрацию. Введите ваше полное ФИО (например, Иванов Иван Иванович) или отмените с помощью /cancel."
    )
    return REGISTER

# Обработчики
registration_handler = ConversationHandler(
    entry_points=[CommandHandler('start', start, filters.ChatType.PRIVATE)],
    states={
        REGISTER: [MessageHandler(filters.TEXT & ~filters.COMMAND, register)],
    },
    fallbacks=[CommandHandler('cancel', cancel)],
    per_message=False
)

add_vacation_handler = ConversationHandler(
    entry_points=[CommandHandler('add_vacation', add_vacation_start, filters.ChatType.PRIVATE)],
    states={
        START_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_vacation_start_date)],
        END_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_vacation_end_date)],
        REPLACEMENT_FULL_NAME: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, add_vacation_replacement_full_name),
            CommandHandler('skip', add_vacation_replacement_full_name),
        ],
        REPLACEMENT: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, add_vacation_replacement),
            CommandHandler('skip', add_vacation_replacement),
        ],
    },
    fallbacks=[CommandHandler('cancel', cancel), MessageHandler(filters.COMMAND, handle_invalid_command)],
    per_message=False
)

edit_vacation_handler = ConversationHandler(
    entry_points=[CommandHandler('edit_vacation', edit_vacation_start, filters.ChatType.PRIVATE)],
    states={
        SELECT_VACATION: [
            CallbackQueryHandler(select_vacation),
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_random_text)
        ],
        NEW_START_DATE: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, edit_vacation_start_date),
            CommandHandler('skip', edit_vacation_start_date)
        ],
        NEW_END_DATE: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, edit_vacation_end_date),
            CommandHandler('skip', edit_vacation_end_date)
        ],
        NEW_REPLACEMENT_FULL_NAME: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, edit_vacation_replacement_full_name),
            CommandHandler('skip', edit_vacation_replacement_full_name),
        ],
        NEW_REPLACEMENT: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, edit_vacation_replacement),
            CommandHandler('skip', edit_vacation_replacement),
            CommandHandler('remove', edit_vacation_replacement),
        ],
    },
    fallbacks=[CommandHandler('cancel', cancel), MessageHandler(filters.COMMAND, handle_invalid_command)],
    per_message=False
)

delete_vacation_handler = ConversationHandler(
    entry_points=[CommandHandler('delete_vacation', delete_vacation_start, filters.ChatType.PRIVATE)],
    states={
        DELETE_VACATION_SELECT: [
            CallbackQueryHandler(delete_vacation_select),
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_random_text)
        ],
    },
    fallbacks=[CommandHandler('cancel', cancel), MessageHandler(filters.COMMAND, handle_invalid_command)],
    per_message=False
)

delete_employee_handler = ConversationHandler(
    entry_points=[CommandHandler('delete_employee', delete_employee_command, filters.ChatType.PRIVATE & filters.User(user_id=ADMIN_ID))],
    states={
        DELETE_EMPLOYEE_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, delete_employee_id)],
    },
    fallbacks=[CommandHandler('cancel', cancel), MessageHandler(filters.COMMAND, handle_invalid_command)],
    per_message=False
)

clear_all_employees_handler = ConversationHandler(
    entry_points=[CommandHandler('clear_all_employees', clear_all_employees_command, filters.ChatType.PRIVATE & filters.User(user_id=ADMIN_ID))],
    states={
        CLEAR_ALL_CONFIRM: [
            CommandHandler('yes', clear_all_confirm),
            CommandHandler('no', clear_all_confirm),
            MessageHandler(filters.TEXT & ~filters.COMMAND, clear_all_confirm)
        ],
    },
    fallbacks=[CommandHandler('cancel', cancel), MessageHandler(filters.COMMAND, handle_invalid_command)],
    per_message=False
)

notify_handler = CommandHandler('notify', notify_handler, filters.ChatType.PRIVATE)
list_employees_handler = CommandHandler('list_employees', list_employees, filters.ChatType.PRIVATE & filters.User(user_id=ADMIN_ID))
stats_handler = CommandHandler('stats', stats, filters.ChatType.PRIVATE & filters.User(user_id=ADMIN_ID))
export_employees_handler = CommandHandler('export_employees', export_employees, filters.ChatType.PRIVATE & filters.User(user_id=ADMIN_ID))
invalid_command_handler = MessageHandler(filters.COMMAND & ~filters.Regex(r'^/(start|cancel)$'), handle_invalid_command, filters.ChatType.PRIVATE)
random_text_handler = MessageHandler(filters.TEXT & ~filters.COMMAND, handle_random_text, filters.ChatType.PRIVATE)
my_vacations_handler = CommandHandler('my_vacations', my_vacations, filters.ChatType.PRIVATE)