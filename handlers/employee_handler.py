import logging
from datetime import datetime, timedelta
from typing import Tuple, Optional, List

from telegram import BotCommand, ReplyKeyboardMarkup, Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ConversationHandler, MessageHandler, filters, CommandHandler, ContextTypes, CallbackQueryHandler
from telegram.error import BadRequest

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
GROUP_CHAT_ID = int(os.getenv('GROUP_CHAT_ID'))

logger = logging.getLogger(__name__)

# Состояния для ConversationHandler
START_DATE, END_DATE, REPLACEMENT = range(3)
SELECT_VACATION, NEW_START_DATE, NEW_END_DATE, NEW_REPLACEMENT = range(4)
DELETE_EMPLOYEE_ID = 100
DELETE_VACATION_SELECT = 200
CLEAR_ALL_CONFIRM = 300

VACATION_LIMIT_DAYS = 28

MONTHS = {
    "January": "Январь", "February": "Февраль", "March": "Март", "April": "Апрель",
    "May": "Май", "June": "Июнь", "July": "Июль", "August": "Август",
    "September": "Сентябрь", "October": "Октябрь", "November": "Ноябрь", "December": "Декабрь"
}

def validate_date_input(date_str: str, is_start_date: bool = True, reference_date: Optional[str] = None) -> Tuple[bool, str]:
    """Валидация даты с учётом будущего времени и сравнения с другой датой."""
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
    """Сбрасывает состояние пользователя в context.user_data."""
    context.user_data.clear()
    logger.info("Состояние пользователя полностью сброшено.")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Отмена текущего действия с логированием."""
    user_id = update.effective_user.id
    logger.info(f"Пользователь {user_id} отменил действие")
    await reset_state(context)
    await update.message.reply_text("Действие отменено.")
    return ConversationHandler.END

async def check_user_permissions(update: Update, context: ContextTypes.DEFAULT_TYPE, require_admin: bool = False) -> bool:
    """Проверка прав пользователя: членство в группе и админские права."""
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

async def handle_invalid_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка некорректных команд во время разговора."""
    command = update.message.text
    user_id = update.effective_user.id
    chat_type = update.effective_chat.type
    logger.info(f"Получена команда {command} от пользователя {user_id}, проверка на валидность")

    if chat_type != 'private':
        logger.info(f"Команда {command} проигнорирована в чате {update.effective_chat.id} (не личный чат)")
        await update.message.reply_text("Все команды доступны только в личных сообщениях. Напишите мне в личку!")
        return ConversationHandler.END

    if context.user_data.get('action'):
        action = context.user_data['action']
        state = context.user_data.get('state', ConversationHandler.END)
        if action == "добавление отпуска":
            if state == START_DATE:
                await update.message.reply_text("Жду дату начала (YYYY-MM-DD, например, 2025-03-01). Введи её или выйди через /cancel.")
            elif state == END_DATE:
                await update.message.reply_text("Жду дату окончания (YYYY-MM-DD, например, 2025-03-15). Введи её или выйди через /cancel.")
            elif state == REPLACEMENT:
                await update.message.reply_text("Жду @username замещающего или /skip. Введи или выйди через /cancel.")
        elif action == "редактирование отпуска":
            if state == SELECT_VACATION:
                await update.message.reply_text("Жду выбор отпуска из списка. Нажми кнопку или выйди через /cancel.")
            elif state == NEW_START_DATE:
                await update.message.reply_text("Жду новую дату начала (YYYY-MM-DD, например, 2025-03-01) или /skip. Введи или выйди через /cancel.")
            elif state == NEW_END_DATE:
                await update.message.reply_text("Жду новую дату окончания (YYYY-MM-DD, например, 2025-03-01) или /skip. Введи или выйди через /cancel.")
            elif state == NEW_REPLACEMENT:
                await update.message.reply_text("Жду @username нового замещающего, /skip или /remove. Введи или выйди через /cancel.")
        elif action == "удаление отпуска":
            if state == DELETE_VACATION_SELECT:
                await update.message.reply_text("Жду выбор отпуска для удаления. Нажми кнопку или выйди через /cancel.")
        elif action == "удаление сотрудника":
            if state == DELETE_EMPLOYEE_ID:
                await update.message.reply_text("Жду ID сотрудника для удаления (число). Введи его или выйди через /cancel.")
        elif action == "очистка всех данных":
            if state == CLEAR_ALL_CONFIRM:
                await update.message.reply_text("Жду подтверждение очистки (/yes или /no). Введи или выйди через /cancel.")
        logger.info(f"Пользователь {user_id} ввёл команду {command} на этапе {state} действия '{action}'")
        return state
    else:
        await update.message.reply_text("Сначала начни действие с помощью команды (например, /add_vacation).")
        return ConversationHandler.END

async def handle_random_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка случайного текста вне или во время действия."""
    user_id = update.effective_user.id
    chat_type = update.effective_chat.type
    text = update.message.text
    logger.info(f"Получен случайный текст от пользователя {user_id}: {text}")

    if chat_type != 'private':
        logger.info(f"Случайный текст проигнорирован в чате {update.effective_chat.id} (не личный чат)")
        return  # Молча игнорируем в группах

    if context.user_data.get('action'):
        action = context.user_data['action']
        state = context.user_data.get('state', ConversationHandler.END)
        if action == "редактирование отпуска" and state == SELECT_VACATION:
            await update.message.reply_text("Пожалуйста, выбери отпуск из списка кнопок или выйди через /cancel.")
        elif action == "удаление отпуска" and state == DELETE_VACATION_SELECT:
            await update.message.reply_text("Пожалуйста, выбери отпуск для удаления из списка кнопок или выйди через /cancel.")
        else:
            await update.message.reply_text("Я ожидаю корректный ввод для текущего действия. Используй /cancel, чтобы выйти.")
    else:
        await update.message.reply_text("Я не понимаю, что вы имеете в виду. Используйте команду, например, /add_vacation.")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Начало работы с ботом."""
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    chat_type = update.effective_chat.type
    username = update.effective_user.username or "пользователь"
    full_name = update.effective_user.full_name or username
    logger.info(f"Команда /start вызвана пользователем {user_id} ({full_name}) в чате {chat_id}")

    if chat_type != 'private':
        logger.info(f"Команда /start проигнорирована в чате {chat_id} (не личный чат)")
        await update.message.reply_text("Все команды доступны только в личных сообщениях. Напишите мне в личку!")
        return

    if not await check_user_permissions(update, context):
        return

    keyboard = [
        ["/add_vacation", "/edit_vacation"],
        ["/delete_vacation", "/notify"],
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
        "🚫 /cancel — Отменить текущее действие\n\n"
        "Все команды работают только в личных сообщениях. Даты вводите в формате YYYY-MM-DD (например, 2025-03-01)."
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
    else:
        message += "\nЕсли что-то непонятно, обратитесь к @Admin!"
    message += "\nВопросы? Пишите @Admin."

    await update.message.reply_text(message, reply_markup=reply_markup)
    logger.info(f"Пользователь {user_id} успешно получил приветственное сообщение")

async def handle_date_input(update: Update, context: ContextTypes.DEFAULT_TYPE, next_state: int, key: str, 
                          is_start_date: bool = True, check_overlap: bool = False) -> int:
    """Универсальная обработка ввода даты с валидацией."""
    user_id = update.effective_user.id
    input_text = update.message.text.strip()
    logger.info(f"Получен ввод даты от пользователя {user_id}: {input_text}")

    if input_text == "/cancel":
        return await cancel(update, context)

    reference_date = context.user_data.get('start_date') if not is_start_date else None
    is_valid, error = validate_date_input(input_text, is_start_date, reference_date)
    if not is_valid:
        await update.message.reply_text(f"{error} Введи заново или выйди через /cancel.")
        logger.warning(f"Некорректный ввод даты от пользователя {user_id}: {error}")
        return context.user_data['state']

    context.user_data[key] = input_text
    context.user_data['state'] = next_state

    if check_overlap:
        user_id_db = context.user_data.get('user_id')
        start_date = context.user_data.get('start_date')
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
            return START_DATE
        if check_vacation_overlap(user_id_db, start_date, end_date):
            await update.message.reply_text("Этот отпуск пересекается с твоим. Введи другие даты или выйди через /cancel.")
            logger.warning(f"Обнаружено пересечение отпусков для пользователя {user_id}: {start_date} - {end_date}")
            return START_DATE

    if next_state == END_DATE:
        await update.message.reply_text("Укажи дату окончания (YYYY-MM-DD, например, 2025-03-15) или /cancel.")
    elif next_state == REPLACEMENT:
        await update.message.reply_text("Укажи @username замещающего или /skip (если нет — пропусти).")
    elif next_state == NEW_END_DATE:
        await update.message.reply_text("Укажите новую дату окончания (YYYY-MM-DD) или /skip.")
    elif next_state == NEW_REPLACEMENT:
        await update.message.reply_text("Укажи @username нового замещающего, /skip или /remove.")
    return next_state

async def add_vacation_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начало процесса добавления отпуска."""
    user_id = update.effective_user.id
    chat_type = update.effective_chat.type
    logger.info(f"Пользователь {user_id} начал добавление отпуска")

    if chat_type != 'private':
        logger.info(f"Команда /add_vacation проигнорирована в чате {update.effective_chat.id} (не личный чат)")
        await update.message.reply_text("Все команды доступны только в личных сообщениях. Напишите мне в личку!")
        return ConversationHandler.END

    if not await check_user_permissions(update, context):
        return ConversationHandler.END
    if context.user_data.get('action'):
        await update.message.reply_text("Сначала заверши текущее действие или выйди через /cancel.")
        logger.warning(f"Пользователь {user_id} пытался начать новое действие поверх текущего")
        return ConversationHandler.END
    user_id, username, full_name = identify_user(update)
    if not all([user_id, username, full_name]):
        logger.error(f"Не удалось определить данные пользователя для чата {update.effective_chat.id}")
        await update.message.reply_text("Не удалось определить пользователя. Обратитесь к @Admin.")
        return ConversationHandler.END
    try:
        db_user_id = add_employee_to_db(full_name, username)
        if db_user_id is None:
            logger.error(f"Не удалось добавить сотрудника username={username} в базу данных")
            await update.message.reply_text("Ошибка при добавлении сотрудника в базу. Обратитесь к @Admin.")
            return ConversationHandler.END
    except Exception as e:
        logger.error(f"Ошибка при добавлении сотрудника username={username}: {str(e)}", exc_info=True)
        await update.message.reply_text(f"Произошла ошибка: {str(e)}. Обратитесь к @Admin.")
        return ConversationHandler.END
    context.user_data.update({
        'name': full_name,
        'user_id': db_user_id,
        'username': username,
        'action': "добавление отпуска",
        'state': START_DATE
    })
    await update.message.reply_text(
        f"Привет, {full_name} (@{username})!\n\n"
        "Укажите дату начала отпуска (YYYY-MM-DD, например, 2025-03-01) или /cancel."
    )
    logger.info(f"Пользователь {user_id} успешно начал добавление отпуска")
    return START_DATE

async def add_vacation_start_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await handle_date_input(update, context, END_DATE, 'start_date', is_start_date=True)

async def add_vacation_end_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await handle_date_input(update, context, REPLACEMENT, 'end_date', is_start_date=False, check_overlap=True)

async def add_vacation_replacement(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Завершение добавления отпуска с указанием замещающего."""
    user_id = update.effective_user.id
    input_text = update.message.text.strip()
    logger.info(f"Получен ввод замещающего от пользователя {user_id}: {input_text}")

    if input_text == "/cancel":
        return await cancel(update, context)
    elif input_text == "/skip":
        context.user_data['replacement_username'] = None
    elif input_text.startswith('@'):
        context.user_data['replacement_username'] = input_text
    else:
        await update.message.reply_text("Введи @username замещающего или /skip. Повтори или выйди через /cancel.")
        logger.warning(f"Пользователь {user_id} ввёл некорректный замещающий: {input_text}")
        return REPLACEMENT

    db_user_id = context.user_data['user_id']
    start_date = context.user_data['start_date']
    end_date = context.user_data['end_date']
    replacement = context.user_data['replacement_username']
    username = context.user_data['username']
    full_name = context.user_data['name']

    try:
        if add_vacation(db_user_id, start_date, end_date, replacement):
            current_year = datetime.now().year
            vacations = get_user_vacations(db_user_id)
            used_days = get_used_vacation_days(db_user_id, current_year)
            vacation_lines = [f"{i+1}. {start} – {end}" for i, (_, start, end, _) in enumerate(vacations)]
            vacation_info = f"Отпусков в {current_year}: {len(vacations)}\n" + "\n".join(vacation_lines) if vacations else "Нет запланированных отпусков."
            message = (
                "ОТПУСК ДОБАВЛЕН!\n\n"
                f"Сотрудник: {full_name} (@{username})\n"
                f"Даты: {start_date} - {end_date}\n"
                f"Замещающий: {replacement or 'Нет'}\n"
                f"{vacation_info}\n"
                f"Использовано дней: {used_days}\n\n"
                "Вопросы? @Admin"
            )
            await update.message.reply_text(message)
            group_message = (
                f"🌴 {full_name} (@{username}) взял отпуск:\n"
                f"📅 С {start_date} по {end_date}"
            )
            if replacement:
                group_message += f"\n👤 Замещающий: {replacement}"
            group_message += "\n\n🎯 Fyi @Admin"
            await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=group_message)
            logger.info(f"Пользователь {user_id} успешно добавил отпуск: {start_date} - {end_date}, replacement={replacement}")
        else:
            logger.error(f"Не удалось добавить отпуск для user_id={db_user_id}: неизвестная ошибка в базе данных")
            await update.message.reply_text("Ошибка при добавлении в базу данных. Начни заново с /add_vacation.")
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
        logger.info(f"Команда /edit_vacation проигнорирована в чате {update.effective_chat.id} (не личный чат)")
        await update.message.reply_text("Все команды доступны только в личных сообщениях. Напишите мне в личку!")
        return ConversationHandler.END

    if not await check_user_permissions(update, context):
        return ConversationHandler.END
    if context.user_data.get('action'):
        await update.message.reply_text("Сначала заверши текущее действие или выйди через /cancel.")
        logger.warning(f"Пользователь {user_id} пытался начать новое действие поверх текущего")
        return ConversationHandler.END
    user_id, username, full_name = identify_user(update)
    if not all([user_id, username, full_name]):
        logger.error(f"Не удалось определить данные пользователя для чата {update.effective_chat.id}")
        await update.message.reply_text("Не удалось определить пользователя. Обратитесь к @Admin.")
        return ConversationHandler.END
    try:
        db_user_id = get_employee_by_username(username)
        if not db_user_id:
            logger.error(f"Сотрудник с username={username} не найден в базе данных")
            await update.message.reply_text("Сотрудник не найден в базе. Обратитесь к @Admin.")
            return ConversationHandler.END
        vacations = get_user_vacations(db_user_id)
        if not vacations:
            await update.message.reply_text("У вас нет отпусков для редактирования.")
            logger.info(f"У пользователя {user_id} нет отпусков для редактирования")
            return ConversationHandler.END
    except Exception as e:
        logger.error(f"Ошибка при получении данных для редактирования отпуска user_id={user_id}: {str(e)}", exc_info=True)
        await update.message.reply_text(f"Произошла ошибка: {str(e)}. Обратитесь к @Admin.")
        return ConversationHandler.END
    context.user_data.update({
        'vacations': vacations,
        'action': "редактирование отпуска",
        'user_id': db_user_id,
        'username': username,
        'name': full_name,
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
    """Выбор отпуска для редактирования."""
    query = update.callback_query
    user_id = query.from_user.id
    vacation_id = int(query.data)
    logger.info(f"Пользователь {user_id} выбрал отпуск с ID={vacation_id} для редактирования")
    await query.answer()
    context.user_data['vacation_id'] = vacation_id
    context.user_data['state'] = NEW_START_DATE
    await query.edit_message_text("Вы выбрали отпуск для редактирования.")
    await query.message.reply_text("Укажите новую дату начала (YYYY-MM-DD, например, 2025-03-01) или /skip.")
    return NEW_START_DATE

async def edit_vacation_start_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка новой даты начала при редактировании."""
    user_id = update.effective_user.id
    input_text = update.message.text.strip().lower()
    logger.info(f"Получен ввод новой даты начала от пользователя {user_id}: {input_text}")

    if input_text == "/cancel":
        return await cancel(update, context)
    if input_text == "/skip":
        context.user_data['new_start_date'] = None
        context.user_data['state'] = NEW_END_DATE
        await update.message.reply_text("Дата начала пропущена. Укажите новую дату окончания (YYYY-MM-DD) или /skip.")
        logger.info(f"Пользователь {user_id} пропустил дату начала")
        return NEW_END_DATE
    is_valid, error = validate_date_input(input_text, is_start_date=True)
    if not is_valid:
        await update.message.reply_text(f"{error} Введи заново или выйди через /cancel.")
        logger.warning(f"Некорректный ввод даты начала от пользователя {user_id}: {error}")
        return NEW_START_DATE
    context.user_data['new_start_date'] = input_text
    context.user_data['state'] = NEW_END_DATE
    await update.message.reply_text("Укажите новую дату окончания (YYYY-MM-DD) или /skip.")
    logger.info(f"Пользователь {user_id} ввёл новую дату начала: {input_text}")
    return NEW_END_DATE

async def edit_vacation_end_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка новой даты окончания при редактировании."""
    user_id = update.effective_user.id
    input_text = update.message.text.strip().lower()
    logger.info(f"Получен ввод новой даты окончания от пользователя {user_id}: {input_text}")

    if input_text == "/cancel":
        return await cancel(update, context)
    if input_text == "/skip":
        context.user_data['new_end_date'] = None
        context.user_data['state'] = NEW_REPLACEMENT
        await update.message.reply_text("Дата окончания пропущена. Укажите @username нового замещающего, /skip или /remove.")
        logger.info(f"Пользователь {user_id} пропустил дату окончания")
        return NEW_REPLACEMENT
    db_user_id = context.user_data['user_id']
    vacation_id = context.user_data['vacation_id']
    current_year = datetime.now().year
    vacations = get_user_vacations(db_user_id)
    total_current_days = sum(calculate_vacation_days(start, end) for _, start, end, _ in vacations)
    new_start = context.user_data.get('new_start_date') or next((v[1] for v in vacations if v[0] == vacation_id), None)
    is_valid, error = validate_date_input(input_text, is_start_date=False, reference_date=new_start)
    if not is_valid:
        await update.message.reply_text(f"{error} Введи заново или выйди через /cancel.")
        logger.warning(f"Некорректный ввод даты окончания от пользователя {user_id}: {error}")
        return NEW_END_DATE
    new_end = input_text
    days_requested = calculate_vacation_days(new_start, new_end)
    old_days = calculate_vacation_days(
        next((v[1] for v in vacations if v[0] == vacation_id), None),
        next((v[2] for v in vacations if v[0] == vacation_id), None)
    )
    new_total_days = total_current_days - old_days + days_requested
    if new_total_days > VACATION_LIMIT_DAYS:
        await update.message.reply_text(
            f"Лимит превышен. Использовано {total_current_days - old_days} дней, запрос: {days_requested} дней. "
            "Введи другие даты или выйди через /cancel."
        )
        logger.warning(f"Превышен лимит отпусков для пользователя {user_id}: {total_current_days - old_days} + {days_requested} > {VACATION_LIMIT_DAYS}")
        return NEW_START_DATE
    if check_vacation_overlap(db_user_id, new_start, new_end, vacation_id):
        await update.message.reply_text("Новый отпуск пересекается с твоим. Введи другие даты или выйди через /cancel.")
        logger.warning(f"Обнаружено пересечение отпусков для пользователя {user_id}: {new_start} - {new_end}")
        return NEW_START_DATE
    context.user_data['new_end_date'] = new_end
    context.user_data['state'] = NEW_REPLACEMENT
    await update.message.reply_text("Укажи @username нового замещающего, /skip или /remove.")
    logger.info(f"Пользователь {user_id} ввёл новую дату окончания: {new_end}")
    return NEW_REPLACEMENT

async def edit_vacation_replacement(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Завершение редактирования отпуска с указанием замещающего."""
    user_id = update.effective_user.id
    input_text = update.message.text.strip().lower()
    logger.info(f"Получен ввод нового замещающего от пользователя {user_id}: {input_text}")

    if input_text == "/cancel":
        return await cancel(update, context)
    elif input_text in ["/skip", "/remove"]:
        context.user_data['new_replacement_username'] = None
    elif input_text.startswith('@'):
        context.user_data['new_replacement_username'] = input_text
    else:
        await update.message.reply_text("Введи @username, /skip, /remove или выйди через /cancel.")
        logger.warning(f"Пользователь {user_id} ввёл некорректный замещающий: {input_text}")
        return NEW_REPLACEMENT

    vacation_id = context.user_data['vacation_id']
    db_user_id = context.user_data['user_id']
    new_start_date = context.user_data.get('new_start_date')
    new_end_date = context.user_data.get('new_end_date')
    new_replacement = context.user_data['new_replacement_username']
    username = context.user_data['username']
    full_name = context.user_data['name']

    try:
        # Получаем старые данные отпуска перед редактированием
        vacations = get_user_vacations(db_user_id)
        old_vacation = next((v for v in vacations if v[0] == vacation_id), None)
        old_start_date = old_vacation[1] if old_vacation else "Неизвестно"
        old_end_date = old_vacation[2] if old_vacation else "Неизвестно"

        if edit_vacation(vacation_id, new_start_date, new_end_date, new_replacement):
            start_date = new_start_date or old_start_date
            end_date = new_end_date or old_end_date
            current_year = datetime.now().year
            vacations = get_user_vacations(db_user_id)
            used_days = get_used_vacation_days(db_user_id, current_year)
            vacation_lines = [f"{i+1}. {start} – {end}" for i, (_, start, end, _) in enumerate(vacations)]
            vacation_info = f"Отпусков в {current_year}: {len(vacations)}\n" + "\n".join(vacation_lines) if vacations else "Нет запланированных отпусков."
            message = (
                "ОТПУСК ОТРЕДАКТИРОВАН!\n\n"
                f"Сотрудник: {full_name} (@{username})\n"
                f"Даты: {start_date} - {end_date}\n"
                f"Замещающий: {new_replacement or 'Нет'}\n"
                f"{vacation_info}\n"
                f"Использовано дней: {used_days}\n\n"
                "Вопросы? @Admin"
            )
            await update.message.reply_text(message)
            group_message = (
                f"✏️ {full_name} (@{username}) изменил отпуск:\n"
                f"Было: С {old_start_date} по {old_end_date}\n"
                f"Стало: С {start_date} по {end_date}\n"
                "\n🎯 Fyi @Admin"
            )
            await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=group_message)
            logger.info(f"Пользователь {user_id} успешно отредактировал отпуск ID={vacation_id}: {start_date} - {end_date}, replacement={new_replacement}")
        else:
            logger.error(f"Не удалось отредактировать отпуск ID={vacation_id} для user_id={db_user_id}: неизвестная ошибка в базе данных")
            await update.message.reply_text("Ошибка при редактировании в базе данных. Начни заново с /edit_vacation.")
    except Exception as e:
        logger.error(f"Ошибка при редактировании отпуска ID={vacation_id} для user_id={db_user_id}: {str(e)}", exc_info=True)
        await update.message.reply_text(f"Произошла ошибка: {str(e)}. Обратитесь к @Admin.")
    await reset_state(context)
    return ConversationHandler.END

async def delete_vacation_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начало удаления отпуска."""
    user_id = update.effective_user.id
    chat_type = update.effective_chat.type
    logger.info(f"Пользователь {user_id} начал удаление отпуска")

    if chat_type != 'private':
        logger.info(f"Команда /delete_vacation проигнорирована в чате {update.effective_chat.id} (не личный чат)")
        await update.message.reply_text("Все команды доступны только в личных сообщениях. Напишите мне в личку!")
        return ConversationHandler.END

    if not await check_user_permissions(update, context):
        return ConversationHandler.END
    if context.user_data.get('action'):
        await update.message.reply_text("Сначала заверши текущее действие или выйди через /cancel.")
        logger.warning(f"Пользователь {user_id} пытался начать новое действие поверх текущего")
        return ConversationHandler.END
    user_id, username, full_name = identify_user(update)
    if not all([user_id, username, full_name]):
        logger.error(f"Не удалось определить данные пользователя для чата {update.effective_chat.id}")
        await update.message.reply_text("Не удалось определить пользователя. Обратитесь к @Admin.")
        return ConversationHandler.END
    try:
        db_user_id = get_employee_by_username(username)
        if not db_user_id:
            logger.error(f"Сотрудник с username={username} не найден в базе данных")
            await update.message.reply_text("Сотрудник не найден в базе. Обратитесь к @Admin.")
            return ConversationHandler.END
        vacations = get_user_vacations(db_user_id)
        if not vacations:
            await update.message.reply_text("У вас нет отпусков для удаления.")
            logger.info(f"У пользователя {user_id} нет отпусков для удаления")
            return ConversationHandler.END
    except Exception as e:
        logger.error(f"Ошибка при получении данных для удаления отпуска user_id={user_id}: {str(e)}", exc_info=True)
        await update.message.reply_text(f"Произошла ошибка: {str(e)}. Обратитесь к @Admin.")
        return ConversationHandler.END
    context.user_data.update({
        'vacations': vacations,
        'action': "удаление отпуска",
        'user_id': db_user_id,
        'username': username,
        'name': full_name,
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
    """Завершение удаления отпуска."""
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
            logger.error(f"Не удалось удалить отпуск ID={vacation_id} для пользователя {user_id}: неизвестная ошибка в базе данных")
            await query.edit_message_text("Ошибка при удалении отпуска в базе данных. Обратитесь к @Admin.")
    except Exception as e:
        logger.error(f"Ошибка при удалении отпуска ID={vacation_id} для пользователя {user_id}: {str(e)}", exc_info=True)
        await query.edit_message_text(f"Произошла ошибка: {str(e)}. Обратитесь к @Admin.")
    await reset_state(context)
    return ConversationHandler.END

async def notify_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показ предстоящих отпусков."""
    user_id = update.effective_user.id
    chat_type = update.effective_chat.type
    logger.info(f"Пользователь {user_id} запросил уведомления")

    if chat_type != 'private':
        logger.info(f"Команда /notify проигнорирована в чате {update.effective_chat.id} (не личный чат)")
        await update.message.reply_text("Все команды доступны только в личных сообщениях. Напишите мне в личку!")
        return

    if not await check_user_permissions(update, context):
        return
    if context.user_data.get('action'):
        await update.message.reply_text("Сначала заверши текущее действие или выйди через /cancel.")
        logger.warning(f"Пользователь {user_id} пытался запросить уведомления во время другого действия")
        return
    try:
        current_date = datetime.now().date()
        seven_days_later = current_date + timedelta(days=7)
        vacations = get_upcoming_vacations(seven_days_later)
        if not vacations:
            await update.message.reply_text("На ближайшие 7 дней отпусков нет.")
            logger.info(f"Для пользователя {user_id} нет предстоящих отпусков")
            return
        message = "СПИСОК ПРЕДСТОЯЩИХ ОТПУСКОВ НА 7 ДНЕЙ:\n\n"
        for _, full_name, username, start_date, end_date, replacement in vacations:
            replacement_text = f" (Замещает: {replacement})" if replacement else ""
            message += f"{full_name} (@{username}): {start_date} - {end_date}{replacement_text}\n"
        message += "\nВопросы? @Admin"
        await update.message.reply_text(message)
        logger.info(f"Пользователь {user_id} получил список предстоящих отпусков")
    except Exception as e:
        logger.error(f"Ошибка при получении уведомлений для пользователя {user_id}: {str(e)}", exc_info=True)
        await update.message.reply_text(f"Произошла ошибка: {str(e)}. Обратитесь к @Admin.")

async def list_employees(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Список сотрудников (для админа)."""
    user_id = update.effective_user.id
    chat_type = update.effective_chat.type
    logger.info(f"Пользователь {user_id} запросил список сотрудников")

    if chat_type != 'private':
        logger.info(f"Команда /list_employees проигнорирована в чате {update.effective_chat.id} (не личный чат)")
        await update.message.reply_text("Все команды доступны только в личных сообщениях. Напишите мне в личку!")
        return

    if not await check_user_permissions(update, context, require_admin=True):
        return
    if context.user_data.get('action'):
        await update.message.reply_text("Сначала заверши текущее действие или выйди через /cancel.")
        logger.warning(f"Пользователь {user_id} пытался запросить список сотрудников во время другого действия")
        return
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

async def delete_employee_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начало удаления сотрудника (для админа)."""
    user_id = update.effective_user.id
    chat_type = update.effective_chat.type
    logger.info(f"Пользователь {user_id} начал удаление сотрудника")

    if chat_type != 'private':
        logger.info(f"Команда /delete_employee проигнорирована в чате {update.effective_chat.id} (не личный чат)")
        await update.message.reply_text("Все команды доступны только в личных сообщениях. Напишите мне в личку!")
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
    """Завершение удаления сотрудника."""
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
            await update.message.reply_text(f"Сотрудник с ID {employee_id} не найден. Введи заново или выйди через /cancel.")
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

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Статистика отпусков (для админа)."""
    user_id = update.effective_user.id
    chat_type = update.effective_chat.type
    logger.info(f"Пользователь {user_id} запросил статистику отпусков")

    if chat_type != 'private':
        logger.info(f"Команда /stats проигнорирована в чате {update.effective_chat.id} (не личный чат)")
        await update.message.reply_text("Все команды доступны только в личных сообщениях. Напишите мне в личку!")
        return

    if not await check_user_permissions(update, context, require_admin=True):
        return
    if context.user_data.get('action'):
        await update.message.reply_text("Сначала заверши текущее действие или выйди через /cancel.")
        logger.warning(f"Пользователь {user_id} пытался запросить статистику во время другого действия")
        return
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

async def export_employees(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Экспорт списка сотрудников (для админа)."""
    user_id = update.effective_user.id
    chat_type = update.effective_chat.type
    logger.info(f"Пользователь {user_id} запросил выгрузку данных сотрудников")

    if chat_type != 'private':
        logger.info(f"Команда /export_employees проигнорирована в чате {update.effective_chat.id} (не личный чат)")
        await update.message.reply_text("Все команды доступны только в личных сообщениях. Напишите мне в личку!")
        return

    if not await check_user_permissions(update, context, require_admin=True):
        return
    if context.user_data.get('action'):
        await update.message.reply_text("Сначала заверши текущее действие или выйди через /cancel.")
        logger.warning(f"Пользователь {user_id} пытался запросить выгрузку во время другого действия")
        return
    try:
        import pandas as pd
        import io
        from telegram import InputFile
        employees = get_all_vacations()
        if not employees:
            await update.message.reply_text("Список сотрудников пуст.")
            logger.info(f"Список сотрудников пуст для выгрузки пользователем {user_id}")
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
        logger.info(f"Пользователь {user_id} успешно выгрузил данные сотрудников")
    except Exception as e:
        logger.error(f"Ошибка при выгрузке данных для пользователя {user_id}: {str(e)}", exc_info=True)
        await update.message.reply_text(f"Произошла ошибка: {str(e)}. Обратитесь к @Admin.")

async def clear_all_employees_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начало очистки всех сотрудников и их отпусков (для админа)."""
    user_id = update.effective_user.id
    chat_type = update.effective_chat.type
    logger.info(f"Пользователь {user_id} начал очистку всех сотрудников")

    if chat_type != 'private':
        logger.info(f"Команда /clear_all_employees проигнорирована в чате {update.effective_chat.id} (не личный чат)")
        await update.message.reply_text("Все команды доступны только в личных сообщениях. Напишите мне в личку!")
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
    """Подтверждение очистки всех данных."""
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
                logger.error(f"Не удалось очистить базу данных для пользователя {user_id}: неизвестная ошибка")
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

async def set_bot_commands(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Установка команд бота для интерфейса Telegram."""
    try:
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
        logger.info("Команды бота успешно установлены")
    except Exception as e:
        logger.error(f"Ошибка при установке команд бота: {str(e)}", exc_info=True)

# Определение обработчиков
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
invalid_command_handler = MessageHandler(filters.COMMAND & ~filters.Regex(r'^/(cancel|start|help)$'), handle_invalid_command, filters.ChatType.PRIVATE)
random_text_handler = MessageHandler(filters.TEXT & ~filters.COMMAND, handle_random_text, filters.ChatType.PRIVATE)
start_handler = CommandHandler('start', start)