from typing import List, Optional, Tuple
import sqlite3
import logging
from datetime import datetime, date

logger = logging.getLogger(__name__)

VACATION_LIMIT_DAYS = 28
DB_PATH = '/app/data/employees.db'  # Можно вынести в .env позже

def create_tables():
    """Создание таблиц в базе данных, если их нет."""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS employees (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    full_name TEXT NOT NULL,
                    username TEXT NOT NULL UNIQUE,
                    telegram_id INTEGER UNIQUE,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS vacations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    start_date TEXT NOT NULL,
                    end_date TEXT NOT NULL,
                    replacement TEXT,
                    replacement_full_name TEXT,
                    FOREIGN KEY (user_id) REFERENCES employees(id) ON DELETE CASCADE
                )
            """)
            conn.commit()
            logger.info("Таблицы в базе данных созданы или уже существуют.")
    except sqlite3.Error as e:
        logger.error(f"Ошибка при создании таблиц: {e}", exc_info=True)
        raise

def add_employee_to_db(full_name: str, username: str, telegram_id: int) -> Optional[int]:
    """Добавление нового сотрудника в базу данных."""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO employees (full_name, username, telegram_id, created_at)
                VALUES (?, ?, ?, datetime('now'))
            """, (full_name, username, telegram_id))
            conn.commit()
            logger.info(f"Добавлен сотрудник: {full_name} (@{username}), telegram_id={telegram_id}")
            return cursor.lastrowid
    except sqlite3.IntegrityError as e:
        logger.error(f"Ошибка уникальности при добавлении сотрудника: {e}")
        return None
    except sqlite3.Error as e:
        logger.error(f"Ошибка при добавлении сотрудника: {e}", exc_info=True)
        return None

def add_vacation(user_id: int, start_date: str, end_date: str, replacement: Optional[str] = None, replacement_full_name: Optional[str] = None) -> bool:
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO vacations (user_id, start_date, end_date, replacement, replacement_full_name)
                VALUES (?, ?, ?, ?, ?)
            """, (user_id, start_date, end_date, replacement, replacement_full_name))
            conn.commit()
            logger.info(f"Добавлен отпуск для user_id={user_id}: {start_date} - {end_date}, replacement={replacement}, replacement_full_name={replacement_full_name}")
            return True
    except sqlite3.Error as e:
        logger.error(f"Ошибка при добавлении отпуска для user_id={user_id}: {e}", exc_info=True)
        return False

def get_user_vacations(user_id: int) -> List[Tuple[int, str, str, str]]:
    """Получение списка отпусков сотрудника."""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("""
                SELECT v.id, v.start_date, v.end_date, v.replacement 
                FROM vacations v 
                WHERE v.user_id = ? 
                ORDER BY v.start_date ASC
            """, (user_id,))
            vacations = [(row['id'], row['start_date'], row['end_date'], row['replacement']) for row in cursor.fetchall()]
            return vacations
    except sqlite3.Error as e:
        logger.error(f"Ошибка при получении отпусков для user_id={user_id}: {e}", exc_info=True)
        return []

def edit_vacation(vacation_id: int, start_date: Optional[str] = None, end_date: Optional[str] = None, replacement: Optional[str] = None, replacement_full_name: Optional[str] = None) -> bool:
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            current = cursor.execute("SELECT start_date, end_date, replacement, replacement_full_name FROM vacations WHERE id = ?", (vacation_id,)).fetchone()
            if not current:
                return False
            new_start_date = start_date if start_date is not None else current[0]
            new_end_date = end_date if end_date is not None else current[1]
            new_replacement = replacement if replacement is not None else current[2]
            new_replacement_full_name = replacement_full_name if replacement_full_name is not None else current[3]
            cursor.execute("""
                UPDATE vacations 
                SET start_date = ?, end_date = ?, replacement = ?, replacement_full_name = ?
                WHERE id = ?
            """, (new_start_date, new_end_date, new_replacement, new_replacement_full_name, vacation_id))
            conn.commit()
            return True
    except sqlite3.Error as e:
        logger.error(f"Ошибка при редактировании отпуска id={vacation_id}: {e}", exc_info=True)
        return False

def check_vacation_overlap(user_id: int, start_date: str, end_date: str, vacation_id: Optional[int] = None) -> bool:
    """Проверка пересечения отпусков."""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            query = """
                SELECT COUNT(*) 
                FROM vacations 
                WHERE user_id = ? 
                AND (
                    (start_date <= ? AND end_date >= ?) OR
                    (start_date <= ? AND end_date >= ?) OR
                    (? <= start_date AND ? >= end_date)
                )
            """
            params = [user_id, start_date, start_date, end_date, end_date, start_date, end_date]
            if vacation_id:
                query += " AND id != ?"
                params.append(vacation_id)
            cursor.execute(query, params)
            count = cursor.fetchone()[0]
            return count > 0
    except sqlite3.Error as e:
        logger.error(f"Ошибка при проверке пересечения отпусков для user_id={user_id}: {e}", exc_info=True)
        return False

def list_employees_db() -> List[str]:
    """Получение списка всех сотрудников с информацией об использованных днях."""
    try:
        logger.info("Начало выполнения list_employees_db")
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("""
                SELECT e.id, e.full_name, e.username, 
                       GROUP_CONCAT(v.start_date || ' - ' || v.end_date, ', ') AS vacations
                FROM employees e
                LEFT JOIN vacations v ON e.id = v.user_id
                GROUP BY e.id, e.full_name, e.username
                ORDER BY e.id
            """)
            employees = []
            for row in cursor.fetchall():
                employee_id = row['id']
                full_name = row['full_name']
                username = row['username']
                vacation_str = row['vacations'] or ''
                used_days = 0
                if vacation_str:
                    vacation_pairs = vacation_str.split(', ')
                    used_days = sum(
                        calculate_vacation_days(start_end.split(' - ')[0], start_end.split(' - ')[1])
                        for start_end in vacation_pairs
                    )
                employees.append(f"{employee_id}, @{username}, {full_name}, {used_days}")
            logger.info("Конец выполнения list_employees_db")
            return employees
    except sqlite3.Error as e:
        logger.error(f"Ошибка при получении списка сотрудников: {e}", exc_info=True)
        return []
    except Exception as e:
        logger.error(f"Необработанная ошибка в list_employees_db: {e}", exc_info=True)
        return []

def delete_employee(employee_id: int) -> bool:
    """Удаление сотрудника и связанных отпусков."""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("BEGIN TRANSACTION")
            try:
                cursor.execute("DELETE FROM vacations WHERE user_id = ?", (employee_id,))
                cursor.execute("DELETE FROM employees WHERE id = ?", (employee_id,))
                conn.commit()
                if cursor.rowcount > 0:
                    logger.info(f"Сотрудник с ID {employee_id} удалён")
                    return True
                logger.warning(f"Сотрудник с ID {employee_id} не найден")
                return False
            except sqlite3.Error:
                conn.rollback()
                raise
    except sqlite3.Error as e:
        logger.error(f"Ошибка при удалении сотрудника ID={employee_id}: {e}", exc_info=True)
        return False

def employee_exists(username: str) -> bool:
    """Проверка существования сотрудника по username."""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM employees WHERE username = ?", (username,))
            exists = cursor.fetchone() is not None
            return exists
    except sqlite3.Error as e:
        logger.error(f"Ошибка при проверке существования сотрудника {username}: {e}", exc_info=True)
        return False

def clear_all_employees() -> bool:
    """Очистка всех сотрудников и отпусков из базы данных."""
    try:
        logger.info("Начало очистки всех сотрудников и отпусков")
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM vacations")
            cursor.execute("DELETE FROM employees")
            conn.commit()
            logger.info("База данных успешно очищена")
            return True
    except sqlite3.Error as e:
        logger.error(f"Ошибка при очистке базы данных: {e}", exc_info=True)
        return False
    except Exception as e:
        logger.error(f"Необработанная ошибка в clear_all_employees: {e}", exc_info=True)
        return False

def calculate_vacation_days(start_date: str, end_date: str) -> int:
    """Расчет количества дней отпуска."""
    try:
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d")
        if start > end:
            return 0
        days = (end - start).days + 1
        return days
    except ValueError as e:
        logger.error(f"Ошибка при расчёте дней между {start_date} и {end_date}: {e}", exc_info=True)
        return 0

def get_used_vacation_days(user_id: int, year: int) -> int:
    """Получение использованных дней отпуска за год."""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT start_date, end_date 
                FROM vacations 
                WHERE user_id = ? 
                AND strftime('%Y', start_date) = ?
            """, (user_id, str(year)))
            vacations = cursor.fetchall()
            used_days = sum(calculate_vacation_days(start, end) for start, end in vacations)
            return used_days
    except sqlite3.Error as e:
        logger.error(f"Ошибка при получении использованных дней для user_id={user_id}: {e}", exc_info=True)
        return 0

def get_vacation_stats() -> List[Tuple[str, int, float, int]]:
    """Получение статистики по отпускам: месяц, количество отпусков, дни, количество сотрудников."""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("""
                SELECT strftime('%m', start_date) as month,
                       COUNT(*) as vacation_count,
                       COUNT(DISTINCT user_id) as employee_count,
                       start_date,
                       end_date
                FROM vacations
                GROUP BY month
                ORDER BY month
            """)
            stats = []
            for row in cursor.fetchall():
                month = row['month']
                month_name = datetime(2023, int(month), 1).strftime('%B').replace("January", "Январь").replace("February", "Февраль").replace("March", "Март").replace("April", "Апрель").replace("May", "Май").replace("June", "Июнь").replace("July", "Июль").replace("August", "Август").replace("September", "Сентябрь").replace("October", "Октябрь").replace("November", "Ноябрь").replace("December", "Декабрь")
                count = row['vacation_count']
                employee_count = row['employee_count']
                cursor.execute("SELECT start_date, end_date FROM vacations WHERE strftime('%m', start_date) = ?", (month,))
                days = sum(calculate_vacation_days(row['start_date'], row['end_date']) for row in cursor.fetchall())
                stats.append((month_name, count, days, employee_count))
            return stats
    except sqlite3.Error as e:
        logger.error(f"Ошибка при получении статистики отпусков: {e}", exc_info=True)
        return []

def get_all_vacations() -> List[Tuple[int, str, str, str, str, str]]:
    """Получение всех отпусков с информацией о сотрудниках."""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("""
                SELECT e.id, e.full_name, e.username, v.start_date, v.end_date, v.replacement 
                FROM employees e 
                LEFT JOIN vacations v ON e.id = v.user_id 
                ORDER BY e.id, v.start_date
            """)
            vacations = [(row['id'], row['full_name'], row['username'], row['start_date'] or '', row['end_date'] or '', row['replacement'] or '') for row in cursor.fetchall()]
            return vacations
    except sqlite3.Error as e:
        logger.error(f"Ошибка при получении всех отпусков: {e}", exc_info=True)
        return []

def get_employee_by_username(username: str) -> Optional[int]:
    """Получение ID сотрудника по username."""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM employees WHERE username = ?", (username,))
            result = cursor.fetchone()
            return result[0] if result else None
    except sqlite3.Error as e:
        logger.error(f"Ошибка при поиске сотрудника по username={username}: {e}", exc_info=True)
        return None

def get_upcoming_vacations(target_date: date) -> List[Tuple[int, str, str, str, str, str]]:
    """Получение предстоящих отпусков в диапазоне до указанной даты."""
    if not isinstance(target_date, date):
        logger.error(f"Некорректный тип target_date: {type(target_date)}")
        return []
    current_date = datetime.now().date()
    logger.info(f"Поиск отпусков с {current_date} до {target_date}")
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("""
                SELECT e.id, e.full_name, e.username, v.start_date, v.end_date, v.replacement 
                FROM employees e 
                JOIN vacations v ON e.id = v.user_id 
                WHERE v.start_date <= ?
                AND v.end_date >= ?
                ORDER BY v.start_date
            """, (target_date.isoformat(), current_date.isoformat()))
            vacations = [(row['id'], row['full_name'], row['username'], row['start_date'], row['end_date'], row['replacement']) for row in cursor.fetchall()]
            logger.info(f"Найдено отпусков: {len(vacations)}")
            for v in vacations:
                logger.info(f"Отпуск: {v}")
            return vacations
    except sqlite3.Error as e:
        logger.error(f"Ошибка при получении предстоящих отпусков: {e}", exc_info=True)
        return []

def delete_vacation(vacation_id: int) -> bool:
    """Удаление отпуска по ID."""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM vacations WHERE id = ?", (vacation_id,))
            conn.commit()
            if cursor.rowcount > 0:
                logger.info(f"Отпуск с ID {vacation_id} удалён")
                return True
            logger.warning(f"Отпуск с ID {vacation_id} не найден")
            return False
    except sqlite3.Error as e:
        logger.error(f"Ошибка при удалении отпуска ID={vacation_id}: {e}", exc_info=True)
        return False
    
def get_all_employees_with_registration() -> List[Tuple[int, str, str, str]]:
    """Получение списка всех сотрудников с датой регистрации."""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("""
                SELECT e.id, e.full_name, e.username, e.created_at
                FROM employees e
                ORDER BY e.id
            """)
            employees = [(row['id'], row['full_name'], row['username'], row['created_at']) for row in cursor.fetchall()]
            logger.info(f"Найдено сотрудников: {len(employees)}")
            for e in employees:
                logger.info(f"Сотрудник: {e}")
            return employees
    except sqlite3.Error as e:
        logger.error(f"Ошибка при получении списка сотрудников: {e}", exc_info=True)
        return []