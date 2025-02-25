import sqlite3
import logging
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

VACATION_LIMIT_DAYS = 28  # Лимит отпусков в рабочих днях за год

def init_db() -> None:
    """Инициализация базы данных с корректной структурой и индексами."""
    try:
        with sqlite3.connect('employees.db') as conn:
            cursor = conn.cursor()
            # Создаём таблицу employees с уникальным id, именем и логином
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS employees (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    full_name TEXT NOT NULL,
                    username TEXT NOT NULL UNIQUE
                )
            """)
            # Создаём таблицу vacations с уникальным id, ссылкой на пользователя, датами и логином замещающего
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS vacations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    start_date TEXT NOT NULL,
                    end_date TEXT NOT NULL,
                    replacement TEXT DEFAULT NULL,
                    FOREIGN KEY (user_id) REFERENCES employees(id)
                )
            """)
            # Проверяем и создаём индексы, если их нет
            for index_name, table, column in [
                ('idx_vacations_user_id', 'vacations', 'user_id'),
                ('idx_vacations_start_date', 'vacations', 'start_date'),
                ('idx_vacations_end_date', 'vacations', 'end_date'),
            ]:
                cursor.execute("SELECT count(*) FROM sqlite_master WHERE type='index' AND name=?", (index_name,))
                if cursor.fetchone()[0] == 0:
                    cursor.execute(f"CREATE INDEX {index_name} ON {table}({column})")
            conn.commit()
            logger.info("База данных инициализирована.")
    except sqlite3.Error as e:
        logger.error(f"Ошибка при инициализации базы данных: {e}")

def add_employee_to_db(full_name: str, username: str) -> Optional[int]:
    """Добавление нового сотрудника в базу данных с уникальным id, именем и логином."""
    try:
        with sqlite3.connect('employees.db') as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT id FROM employees WHERE username = ?', (username,))
            if cursor.fetchone():
                logger.warning(f"Сотрудник с username={username} уже существует")
                return None
            cursor.execute('INSERT INTO employees (full_name, username) VALUES (?, ?)', (full_name, username))
            conn.commit()
            cursor.execute('SELECT last_insert_rowid()')
            user_id = cursor.fetchone()[0]
        logger.info(f"Сотрудник добавлен: id={user_id}, full_name={full_name}, username={username}")
        return user_id
    except sqlite3.Error as e:
        logger.error(f"Ошибка при добавлении сотрудника: {e}")
        return None

def add_vacation(user_id: int, start_date: str, end_date: str, replacement: Optional[str]) -> bool:
    """Добавление отпуска для сотрудника."""
    try:
        # Проверяем, существует ли сотрудник, если нет — добавляем с минимальными данными
        if not employee_exists(user_id):
            logger.warning(f"Сотрудник с user_id={user_id} не найден, добавляем в employees")
            # Здесь нужно получить имя и логин из Telegram, но для минимализма добавим заглушки
            add_employee_to_db(f"Сотрудник_{user_id}", f"@user_{user_id}")
        with sqlite3.connect('employees.db') as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO vacations (user_id, start_date, end_date, replacement)
                VALUES (?, ?, ?, ?)
            """, (user_id, start_date, end_date, replacement))
            conn.commit()
        logger.info(f"Отпуск добавлен для user_id={user_id}: {start_date} - {end_date}, замещающий: {replacement}")
        return True
    except sqlite3.Error as e:
        logger.error(f"Ошибка при добавлении отпуска для user_id={user_id}: {e}")
        return False

def get_user_vacations(user_id: int) -> List[Tuple[int, str, str, Optional[str]]]:
    """Получение списка отпусков пользователя, отсортированных по дате начала."""
    try:
        with sqlite3.connect('employees.db') as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, start_date, end_date, replacement
                FROM vacations
                WHERE user_id = ?
                ORDER BY start_date ASC
            """, (user_id,))
            vacations = [
                (row['id'], row['start_date'], row['end_date'], row['replacement'])
                for row in cursor.fetchall()
            ]
            logger.debug(f"Получены отпуска для user_id={user_id}: {vacations}")
        return vacations
    except sqlite3.Error as e:
        logger.error(f"Ошибка при получении отпусков для user_id={user_id}: {e}")
        return []

def edit_vacation(vacation_id: int, new_start_date: Optional[str], new_end_date: Optional[str], replacement: Optional[str]) -> bool:
    """Редактирование отпуска."""
    try:
        with sqlite3.connect('employees.db') as conn:
            cursor = conn.cursor()
            updates = []
            params = []
            if new_start_date is not None:
                updates.append("start_date = ?")
                params.append(new_start_date)
            if new_end_date is not None:
                updates.append("end_date = ?")
                params.append(new_end_date)
            if replacement is not None:
                updates.append("replacement = ?")
                params.append(replacement)
            if not updates:
                return True
            params.append(vacation_id)
            cursor.execute(f"UPDATE vacations SET {', '.join(updates)} WHERE id = ?", params)
            conn.commit()
        logger.info(f"Отпуск {vacation_id} отредактирован: start_date={new_start_date}, end_date={new_end_date}, replacement={replacement}")
        return True
    except sqlite3.Error as e:
        logger.error(f"Ошибка при редактировании отпуска {vacation_id}: {e}")
        return False

def check_vacation_overlap(user_id: int, new_start: str, new_end: str, vacation_id: Optional[int] = None) -> bool:
    """Проверка пересечения отпусков для пользователя."""
    try:
        with sqlite3.connect('employees.db') as conn:
            cursor = conn.cursor()
            query = """
                SELECT COUNT(*) 
                FROM vacations 
                WHERE user_id = ? 
                AND (
                    (start_date <= ? AND end_date >= ?) OR
                    (start_date <= ? AND end_date >= ?)
                )
            """
            params = (user_id, new_end, new_start, new_start, new_end)
            if vacation_id:
                query += " AND id != ?"
                params += (vacation_id,)
            cursor.execute(query, params)
            count = cursor.fetchone()[0]
            return count > 0
    except sqlite3.Error as e:
        logger.error(f"Ошибка при проверке пересечения отпусков для user_id={user_id}: {e}")
        return False

def list_employees_db() -> List[str]:
    """Получение списка всех сотрудников и их отпусков."""
    try:
        with sqlite3.connect('employees.db') as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("""
                SELECT e.id, e.full_name, e.username, v.start_date, v.end_date, v.replacement 
                FROM employees e 
                LEFT JOIN vacations v ON e.id = v.user_id 
                WHERE v.start_date IS NOT NULL 
                ORDER BY v.start_date ASC
            """)
            employees = []
            for row in cursor.fetchall():
                employee_id = row['id']
                name = row['full_name']
                username = row['username']
                start_date = row['start_date']
                end_date = row['end_date'] if row['end_date'] else "Нет отпуска"
                replacement = row['replacement'] if row['replacement'] else "Нет"
                employees.append(f"{employee_id}, {name} (@{username}), {start_date} – {end_date}, {replacement}")
            return employees
    except sqlite3.Error as e:
        logger.error(f"Ошибка при получении списка сотрудников: {e}")
        return []

def delete_employee(user_id: int) -> bool:
    """Удаление сотрудника и его отпусков."""
    try:
        with sqlite3.connect('employees.db') as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM vacations WHERE user_id = ?', (user_id,))
            cursor.execute('DELETE FROM employees WHERE id = ?', (user_id,))
            conn.commit()
        logger.info(f"Сотрудник с ID {user_id} удалён.")
        return True
    except sqlite3.Error as e:
        logger.error(f"Ошибка при удалении сотрудника: {e}")
        return False

def employee_exists(user_id: int) -> bool:
    """Проверка существования сотрудника по user_id."""
    try:
        with sqlite3.connect('employees.db') as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT COUNT(*) FROM employees WHERE id = ?', (user_id,))
            exists = cursor.fetchone()[0] > 0
            logger.debug(f"Проверка существования сотрудника user_id={user_id}: {exists}")
        return exists
    except sqlite3.Error as e:
        logger.error(f"Ошибка при проверке существования сотрудника: {e}")
        return False

def get_employee_by_username(username: str) -> Optional[int]:
    """Получение id сотрудника по username."""
    try:
        with sqlite3.connect('employees.db') as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT id FROM employees WHERE username = ?', (username,))
            result = cursor.fetchone()
            if result:
                return result[0]
            return None
    except sqlite3.Error as e:
        logger.error(f"Ошибка при поиске сотрудника по username={username}: {e}")
        return None

def clear_all_employees() -> bool:
    """Очистка всех данных о сотрудниках и отпусках."""
    try:
        with sqlite3.connect('employees.db') as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM vacations')
            cursor.execute('DELETE FROM employees')
            conn.commit()
        logger.info("Все данные о сотрудниках успешно удалены")
        return True
    except sqlite3.Error as e:
        logger.error(f"Ошибка при очистке базы данных: {e}")
        return False

def get_upcoming_vacations(target_date: datetime.date) -> List[Tuple[int, str, str, str, str, Optional[str]]]:
    """Получение списка предстоящих отпусков до указанной даты, отсортированных по дате начала."""
    try:
        with sqlite3.connect('employees.db') as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            current_date = datetime.now().date()
            cursor.execute("""
                SELECT e.id, e.full_name, e.username, v.start_date, v.end_date, v.replacement
                FROM employees e
                JOIN vacations v ON e.id = v.user_id
                WHERE v.start_date <= ? AND v.end_date >= ?
                ORDER BY v.start_date ASC
            """, (target_date.strftime('%Y-%m-%d'), current_date.strftime('%Y-%m-%d')))
            vacations = [
                (row['id'], row['full_name'], row['username'], row['start_date'], row['end_date'], row['replacement'])
                for row in cursor.fetchall()
            ]
            logger.debug(f"Получены предстоящие отпуска до {target_date}: {vacations}")
        return vacations
    except sqlite3.Error as e:
        logger.error(f"Ошибка при получении предстоящих отпусков: {e}")
        return []

def get_all_vacations() -> List[Tuple[int, str, str, str, Optional[str]]]:
    """Получение списка всех сотрудников и их отпусков."""
    try:
        with sqlite3.connect('employees.db') as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("""
                SELECT e.id, e.full_name, e.username, v.start_date, v.end_date, v.replacement 
                FROM employees e 
                LEFT JOIN vacations v ON e.id = v.user_id 
                WHERE v.start_date IS NOT NULL 
                ORDER BY v.start_date ASC
            """)
            return [(row['id'], row['full_name'], row['username'], row['start_date'], row['end_date'], row['replacement']) for row in cursor.fetchall()]
    except sqlite3.Error as e:
        logger.error(f"Ошибка при получении списка всех отпусков: {e}")
        return []

def get_vacation_stats() -> List[Tuple[str, int, float]]:
    """Получение статистики по отпускам (месяц, количество, дни)."""
    try:
        with sqlite3.connect('employees.db') as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("""
                SELECT strftime('%Y-%m', start_date) as month, COUNT(*) as count, 
                       SUM(julianday(end_date) - julianday(start_date)) as days
                FROM vacations
                GROUP BY strftime('%Y-%m', start_date)
                ORDER BY month
            """)
            stats = [(row['month'], row['count'], row['days']) for row in cursor.fetchall()]
            return stats
    except sqlite3.Error as e:
        logger.error(f"Ошибка при получении статистики отпусков: {e}")
        return []

def calculate_vacation_days(start_date: str, end_date: str) -> int:
    """Рассчитывает количество рабочих дней между датами, исключая выходные."""
    start = datetime.strptime(start_date, "%Y-%m-%d").date()
    end = datetime.strptime(end_date, "%Y-%m-%d").date()
    days = 0
    current = start
    while current <= end:
        if current.weekday() < 5:  # Пн-Пт (0-4)
            days += 1
        current += timedelta(days=1)
    return days

def get_remaining_vacation_days(user_id: int, year: int) -> int:
    """Получение оставшихся дней отпуска за указанный год."""
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
        total_days = sum(calculate_vacation_days(start, end) for start, end in vacations)
        return VACATION_LIMIT_DAYS - total_days  # Лимит 28 рабочих дня
    except sqlite3.Error as e:
        logger.error(f"Ошибка при расчёте оставшихся дней отпуска для user_id={user_id}: {e}")
        return VACATION_LIMIT_DAYS  # Возвращаем лимит по умолчанию в случае ошибки