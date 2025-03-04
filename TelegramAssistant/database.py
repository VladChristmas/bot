import sqlite3
import logging
from typing import List, Dict, Any, Optional, Union

logger = logging.getLogger(__name__)

class Database:
    def __init__(self, db_name: str = "bot.db"):
        """Инициализация подключения к базе данных"""
        self.db_name = db_name
        self.init_database()

    def get_connection(self) -> sqlite3.Connection:
        """Получение подключения к базе данных"""
        return sqlite3.connect(self.db_name)

    def init_database(self) -> None:
        """Инициализация структуры базы данных"""
        conn = None
        try:
            conn = self.get_connection()
            cursor = conn.cursor()

            # Существующие таблицы остаются без изменений
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS chats (
                    chat_id INTEGER PRIMARY KEY,
                    title TEXT NOT NULL,
                    is_group BOOLEAN NOT NULL,
                    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS chat_groups (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS group_chats (
                    group_id INTEGER,
                    chat_id INTEGER,
                    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (group_id, chat_id),
                    FOREIGN KEY (group_id) REFERENCES chat_groups(id),
                    FOREIGN KEY (chat_id) REFERENCES chats(chat_id)
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    text TEXT NOT NULL,
                    status TEXT DEFAULT 'active',
                    created_by INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS task_recipients (
                    task_id INTEGER,
                    chat_id INTEGER,
                    group_id INTEGER,
                    status TEXT DEFAULT 'pending',
                    PRIMARY KEY (task_id, chat_id),
                    FOREIGN KEY (task_id) REFERENCES tasks(id),
                    FOREIGN KEY (chat_id) REFERENCES chats(chat_id),
                    FOREIGN KEY (group_id) REFERENCES chat_groups(id)
                )
            """)

            # Новые таблицы для медиафайлов
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS task_media (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id INTEGER,
                    file_id TEXT NOT NULL,
                    file_type TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (task_id) REFERENCES tasks(id)
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS response_media (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id INTEGER,
                    chat_id INTEGER,
                    file_id TEXT NOT NULL,
                    file_type TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (task_id) REFERENCES tasks(id),
                    FOREIGN KEY (chat_id) REFERENCES chats(chat_id)
                )
            """)

            conn.commit()
            logger.info("База данных успешно инициализирована")

        except Exception as e:
            logger.error(f"Ошибка инициализации базы данных: {e}", exc_info=True)
            if conn:
                conn.rollback()
            raise
        finally:
            if conn:
                conn.close()

    def execute_query(self, query: str, params: tuple = ()) -> Union[List[Dict[str, Any]], None]:
        """Выполнение SQL-запроса"""
        conn = None
        try:
            conn = self.get_connection()
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute(query, params)

            if query.strip().upper().startswith("SELECT"):
                result = [dict(row) for row in cursor.fetchall()]
            else:
                conn.commit()
                result = None

            return result

        except Exception as e:
            logger.error(f"Ошибка выполнения запроса: {e}\nЗапрос: {query}\nПараметры: {params}", exc_info=True)
            if conn:
                conn.rollback()
            raise
        finally:
            if conn:
                conn.close()

    def get_chat_groups(self, user_id: int) -> List[Dict[str, Any]]:
        """Получение списка групп чатов"""
        result = self.execute_query("SELECT * FROM chat_groups ORDER BY name")
        return result if result is not None else []

    def get_group_chats(self, group_id: int) -> List[Dict[str, Any]]:
        """Получение списка чатов в группе"""
        result = self.execute_query("""
            SELECT c.* FROM chats c
            JOIN group_chats gc ON c.chat_id = gc.chat_id
            WHERE gc.group_id = ?
        """, (group_id,))
        return result if result is not None else []

    def get_active_tasks(self) -> List[Dict[str, Any]]:
        """Получение списка активных заданий с их получателями, статусами и медиафайлами"""
        try:
            query = """
                WITH task_info AS (
                    SELECT DISTINCT
                        t.id,
                        t.text,
                        t.created_at,
                        c.title as chat_title,
                        tr.status as recipient_status,
                        c.chat_id,
                        cg.name as group_name,
                        t.status as task_status
                    FROM tasks t
                    JOIN task_recipients tr ON t.id = tr.task_id
                    JOIN chats c ON tr.chat_id = c.chat_id
                    LEFT JOIN chat_groups cg ON tr.group_id = cg.id
                    WHERE t.status = 'active'
                )
                SELECT 
                    ti.*,
                    GROUP_CONCAT(DISTINCT tm.file_id || ':' || tm.file_type) as task_media,
                    GROUP_CONCAT(DISTINCT rm.file_id || ':' || rm.file_type) as response_media
                FROM task_info ti
                LEFT JOIN task_media tm ON ti.id = tm.task_id
                LEFT JOIN response_media rm ON ti.id = rm.task_id AND ti.chat_id = rm.chat_id
                GROUP BY ti.id, ti.chat_id
                ORDER BY ti.created_at DESC, ti.chat_title ASC
            """
            result = self.execute_query(query)
            logger.info(f"Получено активных заданий: {len(result) if result else 0}")

            # Группируем задания и их медиафайлы
            tasks_grouped = {}
            if result:
                for row in result:
                    task_id = row['id']
                    if task_id not in tasks_grouped:
                        tasks_grouped[task_id] = {
                            'text': row['text'],
                            'created_at': row['created_at'],
                            'recipients': {},
                            'media': []
                        }
                        # Добавляем медиафайлы задания
                        if row['task_media']:
                            for media_info in row['task_media'].split(','):
                                file_id, file_type = media_info.split(':')
                                tasks_grouped[task_id]['media'].append({
                                    'file_id': file_id,
                                    'file_type': file_type
                                })

                    chat_id = row['chat_id']
                    if chat_id not in tasks_grouped[task_id]['recipients']:
                        tasks_grouped[task_id]['recipients'][chat_id] = {
                            'chat_title': row['chat_title'],
                            'status': row['recipient_status'],
                            'group_name': row['group_name'],
                            'media': []
                        }
                        # Добавляем медиафайлы ответа
                        if row['response_media']:
                            for media_info in row['response_media'].split(','):
                                file_id, file_type = media_info.split(':')
                                tasks_grouped[task_id]['recipients'][chat_id]['media'].append({
                                    'file_id': file_id,
                                    'file_type': file_type
                                })

            logger.info(f"Сгруппировано заданий: {len(tasks_grouped)}")
            for task_id, task in tasks_grouped.items():
                logger.info(f"Задание {task_id}: {len(task['media'])} медиафайлов, {len(task['recipients'])} получателей")

            return tasks_grouped

        except Exception as e:
            logger.error(f"Ошибка получения активных заданий: {e}", exc_info=True)
            return {}

    def create_task(self, text: str, created_by: int) -> int:
        """Создание нового задания"""
        try:
            # Сначала создаем задание с явным указанием статуса active
            self.execute_query(
                "INSERT INTO tasks (text, created_by, status) VALUES (?, ?, 'active')",
                (text, created_by)
            )

            # Затем получаем его ID
            result = self.execute_query(
                "SELECT last_insert_rowid() as id"
            )

            if not result:
                raise ValueError("Failed to create task")

            logger.info(f"Создано новое задание с ID: {result[0]['id']}")
            return result[0]['id']

        except Exception as e:
            logger.error(f"Ошибка создания задания: {e}", exc_info=True)
            raise

    def add_task_recipient(self, task_id: int, chat_id: Optional[int] = None, group_id: Optional[int] = None) -> None:
        """Добавление получателя задания"""
        try:
            self.execute_query(
                "INSERT INTO task_recipients (task_id, chat_id, group_id) VALUES (?, ?, ?)",
                (task_id, chat_id, group_id)
            )
            logger.info(f"Добавлен получатель для задания {task_id}: chat_id={chat_id}, group_id={group_id}")
        except Exception as e:
            logger.error(f"Ошибка добавления получателя задания: {e}", exc_info=True)
            raise

    def update_task_status(self, task_id: int, chat_id: int, status: str = 'completed') -> None:
        """Обновление статуса задания для конкретного получателя"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()

            try:
                # Начинаем транзакцию
                conn.execute("BEGIN TRANSACTION")

                # Проверяем существование активного задания
                cursor.execute("""
                    SELECT tr.id, tr.status, t.text
                    FROM task_recipients tr
                    JOIN tasks t ON tr.task_id = t.id
                    WHERE t.id = ? 
                      AND tr.chat_id = ?
                      AND t.status = 'active'
                      AND tr.status != 'completed'
                """, (task_id, chat_id))

                task_recipient = cursor.fetchone()

                if not task_recipient:
                    logger.error(f"Не найдено активное задание {task_id} для чата {chat_id}")
                    conn.rollback()
                    return

                # Обновляем статус получателя задания
                cursor.execute("""
                    UPDATE task_recipients 
                    SET status = ? 
                    WHERE task_id = ? AND chat_id = ?
                """, (status, task_id, chat_id))

                logger.info(f"Обновлен статус задания {task_id} для чата {chat_id}: {status}")

                # Проверяем, все ли получатели выполнили задание
                cursor.execute("""
                    SELECT 
                        COUNT(*) as total,
                        SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed
                    FROM task_recipients
                    WHERE task_id = ?
                """, (task_id,))

                result = cursor.fetchone()
                total = result[0]
                completed = result[1]

                if total == completed:
                    # Если все получатели выполнили задание, обновляем статус самого задания
                    cursor.execute("""
                        UPDATE tasks 
                        SET status = 'completed' 
                        WHERE id = ?
                    """, (task_id,))
                    logger.info(f"Задание {task_id} полностью выполнено всеми получателями")

                # Фиксируем изменения
                conn.commit()

            except Exception as e:
                conn.rollback()
                raise e
            finally:
                conn.close()

        except Exception as e:
            logger.error(f"Ошибка обновления статуса задания: {e}", exc_info=True)
            raise