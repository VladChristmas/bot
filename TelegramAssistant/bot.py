import logging
import os
import sys
import nest_asyncio
import asyncio
import telegram
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from database import Database
from navigation_manager import NavigationManager
import sqlite3

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Получаем ID администратора из переменных окружения
ADMIN_ID = int(os.environ.get('ADMIN_ID', '0'))

# Инициализация базы данных и навигационного менеджера
db = Database()
nav_manager = NavigationManager()

class TelegramBot:
    """Основной класс бота"""

    def __init__(self):
        """Инициализация бота"""
        self.token = os.environ.get('BOT_TOKEN')
        if not self.token:
            raise ValueError("BOT_TOKEN не найден в переменных окружения")
        self.app = None
        self._running = False
        self.nav_manager = nav_manager

    async def start(self):
        """Запуск бота"""
        try:
            # Включаем поддержку вложенных циклов событий
            nest_asyncio.apply()

            # Инициализация приложения
            self.app = Application.builder().token(self.token).build()

            # Регистрация обработчиков
            self.register_handlers()

            # Регистрация обработчика ошибок
            self.app.add_error_handler(self.error_handler)

            # Запуск бота
            self._running = True
            logger.info("Запуск Telegram бота...")

            # Добавляем обработку сигналов для корректного завершения
            import signal
            for sig in (signal.SIGTERM, signal.SIGINT):
                signal.signal(sig, lambda s, f: asyncio.create_task(self.stop()))

            await self.app.run_polling(drop_pending_updates=True)

        except telegram.error.Conflict as e:
            logger.error("Обнаружен конфликт: другой экземпляр бота уже запущен")
            await self.stop()
            raise
        except Exception as e:
            logger.error(f"Ошибка запуска бота: {e}", exc_info=True)
            raise

    async def stop(self):
        """Остановка бота"""
        try:
            if self.app and self._running:
                logger.info("Останавливаем бота...")
                await self.app.stop()
                self._running = False
                logger.info("Бот успешно остановлен")
        except Exception as e:
            logger.error(f"Ошибка при остановке бота: {e}", exc_info=True)

    def register_handlers(self):
        """Регистрация обработчиков команд"""
        try:
            # Базовые команды
            self.app.add_handler(CommandHandler("start", self.start_command))
            self.app.add_handler(CommandHandler("help", self.help_command))
            self.app.add_handler(CommandHandler("addchat", self.add_chat_command))

            # Обработчики для фото и документов
            self.app.add_handler(MessageHandler(
                filters.PHOTO,
                self.handle_media_message
            ))
            self.app.add_handler(MessageHandler(
                filters.Document.ALL,
                self.handle_media_message
            ))

            # Обработчик текстовых сообщений
            self.app.add_handler(MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                self.handle_message
            ))

            logger.info("Обработчики команд успешно зарегистрированы")
        except Exception as e:
            logger.error(f"Ошибка регистрации обработчиков: {e}", exc_info=True)
            raise

    async def handle_back_button(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Обработчик кнопки Назад"""
        try:
            if not update.effective_user or update.effective_user.id != ADMIN_ID:
                logger.warning(f"Попытка неавторизованного доступа к кнопке 'Назад': {update.effective_user.id if update.effective_user else 'Unknown'}")
                return

            current_state = context.user_data.get('state', 'main_menu')
            logger.info(f"Нажата кнопка 'Назад'. Текущее состояние: {current_state}")

            # Если мы уже в главном меню, то никуда не переходим
            if current_state == 'main_menu':
                logger.info("Уже в главном меню, остаемся здесь")
                await self.show_main_menu(update, context)
                return

            # Получаем предыдущее состояние
            previous_state = self.nav_manager.get_previous_state(current_state)
            logger.info(f"Предыдущее состояние определено как: {previous_state}")

            # Очищаем временные данные состояния, сохраняя историю навигации
            self.nav_manager.clear_user_state(context.user_data)

            # Устанавливаем новое состояние
            context.user_data['state'] = previous_state
            self.nav_manager.add_to_history(context.user_data, previous_state)

            # Обработка перехода в зависимости от предыдущего состояния
            if previous_state == 'main_menu':
                await self.show_main_menu(update, context)
            elif previous_state == 'settings':
                await self.show_settings(update, context)
            elif previous_state == 'viewing_tasks':
                await self.show_active_tasks(update, context)
            elif previous_state == 'viewing_chats':
                await self.show_chat_list(update, context)
            elif previous_state == 'creating_chat_group':
                await self.start_create_chat_group(update, context)
            elif previous_state == 'adding_chats_to_group':
                await self.start_create_chat_group(update, context) #fallback to start, as there is no direct previous state
            else:
                logger.warning(f"Неизвестное предыдущее состояние: {previous_state}, возвращаемся в главное меню")
                await self.show_main_menu(update, context)

            logger.info(f"Успешно выполнен переход в состояние: {previous_state}")

        except Exception as e:
            logger.error(f"Ошибка при обработке кнопки 'Назад': {e}", exc_info=True)
            await self.error_handler(update, context)

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Обработчик текстовых сообщений"""
        try:
            if not update.message or not update.effective_user:
                logger.error("Не удалось получить сообщение или информацию о пользователе")
                return

            message_text = update.message.text
            user_id = update.effective_user.id
            chat_id = update.effective_chat.id

            # Проверяем, является ли сообщение ответом на задание
            if update.message.reply_to_message and update.message.reply_to_message.from_user.id == context.bot.id:
                # Получаем оригинальное сообщение с заданием
                original_message = update.message.reply_to_message.text
                logger.info(f"Получен ответ в чате {chat_id}. Оригинальное сообщение: {original_message}")

                # Проверяем формат сообщения
                if not original_message or not original_message.startswith("📝 Новое задание:"):
                    logger.error(f"Неверный формат оригинального сообщения: {original_message}")
                    await update.message.reply_text(
                        "❌ Ошибка: это сообщение не является заданием. "
                        "Пожалуйста, убедитесь, что вы отвечаете на сообщение с заданием."
                    )
                    return

                # Извлекаем текст задания
                parts = original_message.split("📝 Новое задание:", 1)
                if len(parts) != 2:
                    logger.error(f"Не удалось извлечь текст задания из сообщения: {original_message}")
                    await update.message.reply_text("❌ Ошибка при обработке текста задания")
                    return

                task_text = parts[1].strip()
                logger.info(f"Извлеченный текст задания: '{task_text}'")

                conn = None
                try:
                    conn = db.get_connection()
                    cursor = conn.cursor()

                    # Получаем информацию о чате
                    cursor.execute("SELECT title FROM chats WHERE chat_id = ?", (chat_id,))
                    chat_info = cursor.fetchone()
                    if not chat_info:
                        logger.error(f"Чат {chat_id} не найден в базе данных")
                        await update.message.reply_text("❌ Ошибка: чат не зарегистрирован в системе")
                        return

                    # Получаем активные задания для чата
                    cursor.execute("""
                        SELECT t.id, t.text, tr.status
                        FROM tasks t
                        JOIN task_recipients tr ON t.id = tr.task_id
                        WHERE tr.chat_id = ?
                          AND t.status = 'active'
                          AND tr.status != 'completed'
                        ORDER BY t.created_at DESC
                    """, (chat_id,))

                    active_tasks = cursor.fetchall()
                    logger.info(f"Найдено активных заданий для чата {chat_id}: {len(active_tasks)}")

                    # Ищем совпадающее задание
                    matching_task = None
                    for task in active_tasks:
                        stored_text = task[1].strip()
                        logger.info(f"Сравнение текстов:\nИз сообщения: '{task_text}'\nИз базы: '{stored_text}'")
                        if stored_text == task_text:
                            matching_task = task
                            break

                    if matching_task:
                        task_id = matching_task[0]
                        logger.info(f"Найдено активное задание {task_id}")

                        cursor.execute("BEGIN TRANSACTION")
                        # Обновляем статус для этого получателя
                        cursor.execute("""
                            UPDATE task_recipients 
                            SET status = 'completed' 
                            WHERE task_id = ? AND chat_id = ?
                        """, (task_id, chat_id))

                        # Проверяем статус выполнения у всех получателей
                        cursor.execute("""
                            SELECT COUNT(*) as total,
                                   SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed
                            FROM task_recipients
                            WHERE task_id = ?
                        """, (task_id,))

                        stats = cursor.fetchone()
                        if stats and stats[0] == stats[1]:  # Если все получатели выполнили задание
                            cursor.execute("""
                                UPDATE tasks 
                                SET status = 'completed' 
                                WHERE id = ?
                            """, (task_id,))

                        conn.commit()
                        await update.message.reply_text("✅ Ответ принят. Задание отмечено как выполненное.")
                        logger.info(f"Задание {task_id} обновлено для чата {chat_id}")
                    else:
                        logger.warning(f"Не найдено активное задание для чата {chat_id} с текстом: {task_text}")
                        error_msg = [
                            "❌ Не удалось найти активное задание для этого чата.",
                            "Возможные причины:",
                            "1. Задание уже выполнено",
                            "2. Текст задания был изменен",
                            "3. Вы отвечаете на неактуальное задание"
                        ]
                        if active_tasks:
                            error_msg.append("\nАктивные задания для этого чата:")
                            for task in active_tasks:
                                error_msg.append(f"- {task[1]}")
                        await update.message.reply_text("\n".join(error_msg))

                except Exception as e:
                    logger.error(f"Ошибка при обработке ответа на задание: {e}", exc_info=True)
                    if conn:
                        conn.rollback()
                    await update.message.reply_text("❌ Произошла ошибка при обработке ответа")
                finally:
                    if conn:
                        conn.close()
                return

            # Если сообщение не от администратора - обрабатываем только ответы на задания
            if user_id != ADMIN_ID:
                return

            current_state = context.user_data.get('state')
            logger.info(f"Обработка сообщения. Текущее состояние: {current_state}, Сообщение: {message_text}")

            # Обработка кнопки "Назад" и "Отмена"
            if message_text in ["🔙 Назад", "🔙 Отмена"]:
                logger.info("Получена команда возврата")
                await self.handle_back_button(update, context)
                return

            # Обработка состояний создания задания
            if current_state == 'awaiting_task_text':
                logger.info("Обработка ввода текста задания")
                await self.handle_task_text(update, context)
                return
            elif current_state == 'choosing_recipient_type':
                logger.info("Обработка выбора типа получателей")
                await self.handle_recipient_type(update, context)
                return
            elif current_state == 'selecting_recipients':
                logger.info("Обработка выбора получателей")
                await self.handle_recipient_selection(update, context)
                return
            elif current_state == 'creating_chat_group':
                logger.info("Обработка ввода названия группы чатов")
                await self.handle_group_name(update, context)
                return
            elif current_state == 'adding_chats_to_group':
                logger.info("Обработка выбора чатов для группы")
                await self.handle_chat_selection_for_group(update, context)
                return


            # Обработка основных команд меню
            if message_text == "📝 Создать новое задание":
                logger.info("Начало создания нового задания")
                context.user_data['state'] = 'awaiting_task_text'
                self.nav_manager.add_to_history(context.user_data, 'awaiting_task_text')
                await self.start_new_task(update, context)
            elif message_text == "📋 Просмотр активных заданий":
                context.user_data['state'] = 'viewing_tasks'
                self.nav_manager.add_to_history(context.user_data, 'viewing_tasks')
                await self.show_active_tasks(update, context)
            elif message_text == "👥 Просмотр списка подключенных чатов":
                context.user_data['state'] = 'viewing_chats'
                self.nav_manager.add_to_history(context.user_data, 'viewing_chats')
                await self.show_chat_list(update, context)
            elif message_text == "⚙️ Настройки":
                context.user_data['state'] = 'settings'
                self.nav_manager.add_to_history(context.user_data, 'settings')
                await self.show_settings(update, context)
            elif message_text == "❓ Помощь":
                await self.help_command(update, context)
            elif message_text == "🏠 Главное меню":
                context.user_data.clear()
                context.user_data['state'] = 'main_menu'
                self.nav_manager.add_to_history(context.user_data, 'main_menu')
                await self.show_main_menu(update, context)
            elif message_text == "👥 Создать группу чатов":
                logger.info("Начало создания группы чатов")
                await self.start_create_chat_group(update, context)
            else:
                logger.warning(f"Получено неопознанное сообщение: {message_text}")
                await update.message.reply_text("❓ Неизвестная команда. Используйте меню для навигации.")

        except Exception as e:
            logger.error(f"Ошибка обработки сообщения: {e}", exc_info=True)
            await self.error_handler(update, context)

    async def handle_media_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Обработчик медиа-сообщений (фото, документы) для создания задания и ответов"""
        try:
            if not update.message or not update.effective_user:
                logger.error("Не удалось получить сообщение или информацию о пользователе")
                return

            # Получаем информацию о файле
            file_id = None
            file_type = None
            if update.message.photo:
                file_id = update.message.photo[-1].file_id
                file_type = 'photo'
            elif update.message.document:
                file_id = update.message.document.file_id
                file_type = 'document'
            else:
                await update.message.reply_text("❌ Неподдерживаемый тип файла")
                return

            # Если сообщение является ответом на задание
            if update.message.reply_to_message and update.message.reply_to_message.from_user.id == context.bot.id:
                original_message = None

                # Получаем текст оригинального сообщения с заданием
                if update.message.reply_to_message.text:
                    original_message = update.message.reply_to_message.text
                elif update.message.reply_to_message.caption:
                    original_message = update.message.reply_to_message.caption

                logger.info(f"Получен медиафайл в ответ на сообщение: {original_message}")

                if not original_message:
                    logger.warning("Получено пустое сообщение или сообщение без текста")
                    await update.message.reply_text(
                        "❌ Ошибка: не удалось получить текст задания. "
                        "Убедитесь, что отвечаете на сообщение с заданием, а не на медиафайл."
                    )
                    return

                # Извлекаем текст задания из сообщения
                task_text = None
                logger.info(f"Анализ формата сообщения: {original_message}")

                # Обработка различных форматов сообщений
                if "📝 Новое задание:" in original_message:
                    parts = original_message.split("📝 Новое задание:", 1)
                    if len(parts) > 1:
                        task_text = parts[1].strip()
                elif "📝 Задание №" in original_message:
                    lines = original_message.split("\n")
                    task_lines = []
                    found_task = False

                    for line in lines:
                        if "📝 Задание №" in line:
                            found_task = True
                            continue
                        if found_task and line.strip() and not line.startswith("Получатели:"):
                            task_lines.append(line.strip())
                        elif found_task and line.startswith("Получатели:"):
                            break

                    if task_lines:
                        task_text = "\n".join(task_lines)

                if not task_text:
                    logger.error(f"Не удалось извлечь текст задания из сообщения:\n{original_message}")
                    await update.message.reply_text(
                        "❌ Ошибка: не удалось найти текст задания в сообщении. "
                        "Убедитесь, что отвечаете на сообщение с текстом задания, а не на медиафайл."
                    )
                    return

                chat_id = update.effective_chat.id
                logger.info(f"Извлечен текст задания: '{task_text}' для чата {chat_id}")

                try:
                    conn = db.get_connection()
                    cursor = conn.cursor()

                    try:
                        cursor.execute("BEGIN TRANSACTION")

                        # Поиск активного задания
                        cursor.execute("""
                            SELECT t.id, tr.status
                            FROM tasks t
                            JOIN task_recipients tr ON t.id = tr.task_id
                            WHERE tr.chat_id = ? 
                              AND t.text = ?
                              AND t.status = 'active'
                              AND tr.status != 'completed'
                        """, (chat_id, task_text))

                        task = cursor.fetchone()
                        if not task:
                            logger.warning(f"Не найдено активное задание для чата {chat_id}")
                            await update.message.reply_text(
                                "❌ Не найдено активное задание. Возможно, оно уже выполнено или отменено."
                            )
                            return

                        task_id = task[0]
                        logger.info(f"Найдено активное задание {task_id}")

                        # Сохраняем информацию о медиафайле
                        cursor.execute("""
                            INSERT INTO response_media (task_id, chat_id, file_id, file_type)
                            VALUES (?, ?, ?, ?)
                        """, (task_id, chat_id, file_id, file_type))

                        # Обновляем статус задания
                        cursor.execute("""
                            UPDATE task_recipients 
                            SET status = 'completed' 
                            WHERE task_id = ? AND chat_id = ?
                        """, (task_id, chat_id))

                        # Проверяем статус всех получателей
                        cursor.execute("""
                            SELECT COUNT(*) as total,
                                   SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed
                            FROM task_recipients
                            WHERE task_id = ?
                        """, (task_id,))

                        stats = cursor.fetchone()
                        if stats and stats[0] == stats[1]:  # Если все получатели выполнили задание
                            cursor.execute("""
                                UPDATE tasks 
                                SET status = 'completed' 
                                WHERE id = ?
                            """, (task_id,))

                        conn.commit()
                        await update.message.reply_text(
                            "✅ Медиафайл успешно прикреплен к ответу. "
                            "Задание отмечено как выполненное."
                        )

                    except Exception as e:
                        conn.rollback()
                        raise e
                    finally:
                        conn.close()

                except Exception as e:
                    logger.error(f"Ошибка при сохранении медиафайла ответа: {e}", exc_info=True)
                    await update.message.reply_text(
                        "❌ Произошла ошибка при сохранении файла. "
                        "Пожалуйста, попробуйте еще раз позже."
                    )

            else:
                # Обработка медиафайла при создании задания
                if not update.effective_user.id == ADMIN_ID:
                    return

                current_state = context.user_data.get('state')
                if current_state != 'awaiting_task_text':
                    return

                # Сохраняем информацию о медиафайле в контексте
                if 'media_files' not in context.user_data:
                    context.user_data['media_files'] = []

                context.user_data['media_files'].append({
                    'file_id': file_id,
                    'file_type': file_type
                })

                keyboard = [
                    [KeyboardButton("👥 Выбрать получателей")],
                    [KeyboardButton("📎 Добавить еще файл")],
                    [KeyboardButton("🔙 Отмена")]
                ]
                reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

                await update.message.reply_text(
                    "✅ Файл успешно прикреплен к заданию!\n"
                    "Выберите дальнейшее действие:",
                    reply_markup=reply_markup
                )

        except Exception as e:
            logger.error(f"Ошибка обработки медиафайла: {e}", exc_info=True)
            await self.error_handler(update, context)

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.show_main_menu(update, context)

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("Это бот для управления заданиями.")

    async def show_main_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        keyboard = [
            [KeyboardButton("📝 Создать новое задание")],
            [KeyboardButton("📋 Просмотр активных заданий")],
            [KeyboardButton("👥 Просмотр списка подключенных чатов")],
            [KeyboardButton("👥 Создать группу чатов")],
            [KeyboardButton("⚙️ Настройки")],
            [KeyboardButton("❓ Помощь")]
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        await update.message.reply_text("Главное меню:", reply_markup=reply_markup)

    async def show_active_tasks(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Отображение списка активных заданий"""
        try:
            tasks = db.get_active_tasks()
            logger.info(f"Получено активных заданий: {len(tasks) if tasks else 0}")

            if not tasks:
                keyboard = [[KeyboardButton("🔙 Назад")]]
                reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
                await update.message.reply_text(
                    "📋 Нет активных заданий",
                    reply_markup=reply_markup
                )
                return

            # Для каждого задания формируем детальное сообщение
            for task_id, task_info in tasks.items():
                message_parts = [
                    f"📝 Задание №{task_id}:",
                    f"{task_info['text']}\n"
                ]

                # Добавляем информацию о медиафайлах задания
                if task_info['media']:
                    message_parts.append("📎 Прикрепленные файлы к заданию:")
                    for media in task_info['media']:
                        icon = "🖼" if media['file_type'] == 'photo' else "📄"
                        message_parts.append(f"{icon} {media['file_type']}")
                    message_parts.append("")

                # Добавляем информацию о получателях
                message_parts.append("Получатели:")
                for chat_id, recipient in task_info['recipients'].items():
                    status_emoji = '✅' if recipient['status'] == 'completed' else '⏳'
                    message_parts.append(f"{status_emoji} {recipient['chat_title']}")

                    # Если есть медиафайлы в ответе
                    if recipient['media']:
                        message_parts.append("  📎 Прикрепленные файлы в ответе:")
                        for media in recipient['media']:
                            icon = "🖼" if media['file_type'] == 'photo' else "📄"
                            message_parts.append(f"  {icon} {media['file_type']}")

                message_parts.extend([
                    f"\nСоздано: {task_info['created_at']}\n"
                ])

                # Отправляем информацию о задании
                await update.message.reply_text("\n".join(message_parts))

                # Отправляем медиафайлы задания
                try:
                    for media in task_info['media']:
                        logger.info(f"Отправка медиафайла задания: {media['file_type']}, file_id: {media['file_id']}")
                        if media['file_type'] == 'photo':
                            await context.bot.send_photo(
                                chat_id=update.effective_chat.id,
                                photo=media['file_id']
                            )
                        elif media['file_type'] == 'document':
                            await context.bot.send_document(
                                chat_id=update.effective_chat.id,
                                document=media['file_id']
                            )
                except Exception as e:
                    logger.error(f"Ошибка при отправке медиафайла задания: {e}", exc_info=True)

                # Отправляем медиафайлы ответов
                for recipient in task_info['recipients'].values():
                    if recipient['media']:
                        await update.message.reply_text(
                            f"📎 Медиафайлы от {recipient['chat_title']}:"
                        )
                        try:
                            for media in recipient['media']:
                                logger.info(f"Отправка медиафайла ответа: {media['file_type']}, file_id: {media['file_id']}")
                                if media['file_type'] == 'photo':
                                    await context.bot.send_photo(
                                        chat_id=update.effective_chat.id,
                                        photo=media['file_id']
                                    )
                                elif media['file_type'] == 'document':
                                    await context.bot.send_document(
                                        chat_id=update.effective_chat.id,
                                        document=media['file_id']
                                    )
                        except Exception as e:
                            logger.error(f"Ошибка при отправке медиафайла ответа: {e}", exc_info=True)

            keyboard = [[KeyboardButton("🔙 Назад")]]
            reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
            await update.message.reply_text(
                "Конец списка активных заданий",
                reply_markup=reply_markup
            )

        except Exception as e:
            logger.error(f"Ошибка при отображении активных заданий: {e}", exc_info=True)
            await self.error_handler(update, context)

    async def show_chat_list(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Отображение списка подключенных чатов"""
        try:
            logger.info("Начало получения списка чатов")

            # Получаем список всех чатов из базы данных
            chats = db.execute_query("""
                SELECT chat_id, title, is_group, added_at 
                FROM chats 
                ORDER BY is_group DESC, title ASC
            """)

            logger.info(f"Получено чатов из базы данных: {len(chats) if chats else 0}")

            if not chats:
                keyboard = [[KeyboardButton("🔙 Назад")]]
                reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
                await update.message.reply_text(
                    "📋 Нет подключенных чатов.\n"
                    "Добавьте чаты с помощью команды /addchat в нужном чате",
                    reply_markup=reply_markup
                )
                return

            # Формируем сообщение со списком чатов
            message_parts = ["📋 Список подключенных чатов:\n"]

            # Групповые чаты
            group_chats = [chat for chat in chats if chat['is_group']]
            if group_chats:
                message_parts.append("\n👥 Групповые чаты:")
                for chat in group_chats:
                    message_parts.append(
                        f"• {chat['title']}\n"
                        f"  ID: {chat['chat_id']}\n"
                        f"  Добавлен: {chat['added_at']}"
                    )

            # Личные чаты
            personal_chats = [chat for chat in chats if not chat['is_group']]
            if personal_chats:
                message_parts.append("\n👤 Личные чаты:")
                for chat in personal_chats:
                    message_parts.append(
                        f"• {chat['title']}\n"
                        f"  ID: {chat['chat_id']}\n"
                        f"  Добавлен: {chat['added_at']}"
                    )

            # Добавляем статистику
            message_parts.append(f"\nВсего чатов: {len(chats)}")
            message_parts.append(f"• Групповых: {len(group_chats)}")
            message_parts.append(f"• Личных: {len(personal_chats)}")

            keyboard = [[KeyboardButton("🔙 Назад")]]
            reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

            logger.info("Отправка списка чатов пользователю")
            await update.message.reply_text(
                "\n".join(message_parts),
                reply_markup=reply_markup
            )

        except Exception as e:
            logger.error(f"Ошибка при отображении списка чатов: {e}", exc_info=True)
            await self.error_handler(update, context)

    async def show_settings(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        keyboard = [
            [KeyboardButton("👥 Управление чатами"), KeyboardButton("🔔 Уведомления")],
            [KeyboardButton("🔐 Права доступа"), KeyboardButton("⚙️ Конфигурация")],
            [KeyboardButton("🔙 Назад")]
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        await update.message.reply_text("Настройки:", reply_markup=reply_markup)

    async def start_new_task(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Начало создания нового задания"""
        try:
            if not update.effective_user or update.effective_user.id != ADMIN_ID:
                return

            context.user_data['state'] = 'awaiting_task_text'
            keyboard = [
                [KeyboardButton("🔙 Отмена")]
            ]
            reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

            await update.message.reply_text(
                "📝 Введите задание:",
                reply_markup=reply_markup
            )
            logger.info(f"Запрошен ввод текста задания от пользователя {update.effective_user.id}")

        except Exception as e:
            logger.error(f"Ошибка начала создания задания: {e}", exc_info=True)
            await self.error_handler(update, context)

    asyncdef handle_task_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Обработка ввода текста задания"""
        try:
            if not update.effective_user or update.effective_user.id != ADMIN_ID:
                return

            message_text = update.message.text

            # Обработка специальных команд
            if message_text == "🔙 Отмена":
                context.user_data.clear()
                await self.show_main_menu(update, context)
                return
            elif message_text == "👥 Выбрать получателей":
                # Если есть текст задания, переходим к выбору получателей
                if 'task_text' in context.user_data:
                    context.user_data['state'] = 'choosing_recipient_type'
                    await self.show_recipient_options(update, context)
                else:
                    await update.message.reply_text("❌ Сначала введите текст задания")
                return
            elif message_text == "📎 Добавить файл" or message_text == "📎 Добавить еще файл":
                await update.message.reply_text(
                    "📎 Отправьте фото или документ, который нужно прикрепить к заданию"
                )
                return

            ## Сохраняем текст задания
            context.user_data['task_text'] = message_text
            logger.info(f"Сохранен текст задания от пользователя {update.effective_user.id}")

            # Предлагаем прикрепить файл или перейти к выбору получателей
            keyboard = [
                [KeyboardButton("👥 Выбрать получателей")],
                [KeyboardButton("📎 Добавить файл")],
                [KeyboardButton("🔙 Отмена")]
            ]
            reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

            await update.message.reply_text(
                "✅ Текст задания сохранен!\n"
                "Хотите прикрепить файл или перейти к выбору получателей?",
                reply_markup=reply_markup
            )

        except Exception as e:
            logger.error(f"Ошибка обработки текста задания: {e}", exc_info=True)
            await self.error_handler(update, context)

    async def show_recipient_options(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Показывает опции выбора получателей"""
        keyboard = [
            [KeyboardButton("👥 Группа чатов")],
            [KeyboardButton("👤 Отдельные чаты")],
            [KeyboardButton("🔙 Отмена")]
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        await update.message.reply_text(
            "📨 Выберите тип получателей:",
            reply_markup=reply_markup
        )

    async def handle_recipient_type(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка выбора типа получателей"""
        try:
            if not update.effective_user or update.effective_user.id != ADMIN_ID:
                logger.warning(f"Попытка неавторизованного доступа: {update.effective_user.id if update.effective_user else 'Unknown'}")
                return

            choice = update.message.text

            if choice == "🔙 Отмена":
                context.user_data.clear()
                await self.show_main_menu(update, context)
                return

            if choice == "👥 Группа чатов":
                # Получаем список групп
                groups = db.get_chat_groups(update.effective_user.id)
                if not groups:
                    # Если групп нет, предлагаем создать
                    keyboard = [
                        [KeyboardButton("👥 Создать группу чатов")],
                        [KeyboardButton("🔙 Назад")]
                    ]
                    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
                    await update.message.reply_text(
                        "❗️ Нет доступных групп чатов.\n"
                        "Вы можете создать новую группу, нажав кнопку ниже.",
                        reply_markup=reply_markup
                    )
                    context.user_data['state'] = 'choosing_recipient_type'
                    return

                # Создаем клавиатуру с группами
                keyboard = [[KeyboardButton(f"⬜ {group['name']}")] for group in groups]
                keyboard.extend([
                    [KeyboardButton("✅ Подтвердить")],
                    [KeyboardButton("🔙 Назад")]
                ])
                reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

                context.user_data['selection_type'] = 'group'
                context.user_data['state'] = 'selecting_recipients'
                context.user_data['selected_titles'] = set()

                await update.message.reply_text(
                    "Выберите группу чатов:",
                    reply_markup=reply_markup
                )

            elif choice == "👤 Отдельные чаты":
                # Получаем список чатов
                chats = db.execute_query("SELECT chat_id, title FROM chats ORDER BY title")
                if not chats:
                    keyboard = [[KeyboardButton("🔙 Назад")]]
                    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
                    await update.message.reply_text(
                        "❗️ Нет доступных чатов.\n"
                        "Добавьте чаты с помощью команды /addchat",
                        reply_markup=reply_markup
                    )
                    return

                # Создаем клавиатуру с чатами
                keyboard = [[KeyboardButton(f"⬜ {chat['title']}")] for chat in chats]
                keyboard.extend([
                    [KeyboardButton("✅ Подтвердить")],
                    [KeyboardButton("🔙 Назад")]
                ])
                reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

                context.user_data['selection_type'] = 'chat'
                context.user_data['state'] = 'selecting_recipients'
                context.user_data['selected_titles'] = set()

                await update.message.reply_text(
                    "Выберите чаты для отправки задания:",
                    reply_markup=reply_markup
                )
            else:
                await update.message.reply_text("❌ Неверный выбор. Используйте кнопки для навигации.")

        except Exception as e:
            logger.error(f"Ошибка при обработке выбора типа получателей: {e}", exc_info=True)
            await self.error_handler(update, context)

    async def send_task_to_chat(self, chat_id: int, task_text: str, media_files: list = None) -> bool:
        """Отправка задания в конкретный чат"""
        try:
            logger.info(f"Попытка отправки задания в чат {chat_id}")
            logger.info(f"Текст задания: '{task_text}'")

            # Проверяем доступ к чату
            try:
                chat = await self.app.bot.get_chat(chat_id)
                logger.info(f"Успешно получен доступ к чату {chat_id}, тип: {chat.type}")
            except Exception as e:
                logger.error(f"Ошибка доступа к чату {chat_id}: {e}", exc_info=True)
                return False

            # Формируем текст сообщения
            message_text = f"📝 Новое задание: {task_text}"

            # Сначала отправляем текст задания
            try:
                message = await self.app.bot.send_message(
                    chat_id=chat_id,
                    text=message_text,
                    reply_markup=None
                )

                # Если есть медиафайлы, отправляем их
                if media_files:
                    for media in media_files:
                        if media['file_type'] == 'photo':
                            await self.app.bot.send_photo(
                                chat_id=chat_id,
                                photo=media['file_id'],
                                reply_to_message_id=message.message_id
                            )
                        elif media['file_type'] == 'document':
                            await self.app.bot.send_document(
                                chat_id=chat_id,
                                document=media['file_id'],
                                reply_to_message_id=message.message_id
                            )

                logger.info(f"Задание успешно отправлено в чат {chat_id}")
                return True

            except Exception as e:
                logger.error(f"Ошибка приотправке сообщения в чат {chat_id}: {e}", exc_info=True)
                return False

        except Exception as e:
            logger.error(f"Общая ошибка при отправке задания в чат {chat_id}: {e}", exc_info=True)
            return False

    async def handle_recipient_selection(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Обработка выбора получателей для задания"""
        try:
            message_text = update.message.text

            if message_text == "✅ Подтвердить":
                task_text = context.user_data.get('task_text')
                if not task_text:
                    await update.message.reply_text("❌ Ошибка: текст задания не найден")
                    return

                # Создаем новое задание
                task_id = db.create_task(task_text, update.effective_user.id)

                # Добавляем медиафайлы к заданию, если они есть
                media_files = context.user_data.get('media_files', [])
                if media_files:
                    for media in media_files:
                        db.execute_query(
                            "INSERT INTO task_media (task_id, file_id, file_type) VALUES (?, ?, ?)",
                            (task_id, media['file_id'], media['file_type'])
                        )

                # Получаем выбранные чаты/группы
                selected_titles = context.user_data.get('selected_titles', set())
                selection_type = context.user_data.get('selection_type')

                success_count = 0
                total_count = 0

                if selection_type == "group":
                    # Для каждой выбранной группы получаем список чатов
                    for group_name in selected_titles:
                        group = db.execute_query(
                            "SELECT id FROM chat_groups WHERE name = ?",
                            (group_name,)
                        )
                        if group:
                            group_id = group[0]['id']
                            group_chats = db.get_group_chats(group_id)
                            for chat in group_chats:
                                total_count += 1
                                if await self.send_task_to_chat(chat['chat_id'], task_text, media_files):
                                    success_count += 1
                                    db.add_task_recipient(task_id, chat['chat_id'], group_id)
                else:
                    # Для выбранных отдельных чатов
                    for chat_title in selected_titles:
                        chat = db.execute_query(
                            "SELECT chat_id FROM chats WHERE title = ?",
                            (chat_title,)
                        )
                        if chat:
                            total_count += 1
                            chat_id = chat[0]['chat_id']
                            if await self.send_task_to_chat(chat_id, task_text, media_files):
                                success_count += 1
                                db.add_task_recipient(task_id, chat_id)

                # Отправляем отчет о результатах
                await update.message.reply_text(
                    f"✅ Задание отправлено успешно!\n"
                    f"Доставлено: {success_count} из {total_count} получателей."
                )

                # Очищаем данные задания
                context.user_data.clear()
                await self.show_main_menu(update, context)
                return

            # Обработка выбора получателей
            if message_text != "🔙 Назад":
                title = message_text[2:] if message_text.startswith(('⬜', '✅')) else message_text
                selected_titles = context.user_data.get('selected_titles', set())

                if title in selected_titles:
                    selected_titles.remove(title)
                    new_state = '⬜'
                else:
                    selected_titles.add(title)
                    new_state = '✅'

                context.user_data['selected_titles'] = selected_titles

                # Обновляем клавиатуру
                keyboard = []
                if context.user_data.get('selection_type') == 'group':
                    groups = db.get_chat_groups(update.effective_user.id)
                    for group in groups:
                        button_text = f"{new_state if group['name'] == title else ('✅' if group['name'] in selected_titles else '⬜')} {group['name']}"
                        keyboard.append([KeyboardButton(button_text)])
                else:
                    chats = db.execute_query("SELECT title FROM chats ORDER BY title")
                    for chat in chats:
                        button_text = f"{new_state if chat['title'] == title else ('✅' if chat['title'] in selected_titles else '⬜')} {chat['title']}"
                        keyboard.append([KeyboardButton(button_text)])

                keyboard.extend([
                    [KeyboardButton("✅ Подтвердить")],
                    [KeyboardButton("🔙 Назад")]
                ])

                reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
                await update.message.reply_text(
                    "Выберите получателей задания:",
                    reply_markup=reply_markup
                )

        except Exception as e:
            logger.error(f"Ошибка при выборе получателей: {e}", exc_info=True)
            await self.error_handler(update, context)

    async def start_create_chat_group(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Начало процесса создания группы чатов"""
        try:
            if not update.effective_user or update.effective_user.id != ADMIN_ID:
                return

            context.user_data['state'] = 'creating_chat_group'
            self.nav_manager.add_to_history(context.user_data, 'creating_chat_group')

            keyboard = [[KeyboardButton("🔙 Назад")]]
            reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

            await update.message.reply_text(
                "👥 Введите название для новой группы чатов:",
                reply_markup=reply_markup
            )

        except Exception as e:
            logger.error(f"Ошибка при начале создания группы чатов: {e}", exc_info=True)
            await self.error_handler(update, context)

    async def handle_group_name(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка ввода названия группы чатов"""
        try:
            if not update.effective_user or update.effective_user.id != ADMIN_ID:
                return

            group_name = update.message.text

            if group_name == "🔙 Назад":
                await self.handle_back_button(update, context)
                return

            # Проверяем, не существует ли уже группа с таким названием
            existing_group = db.execute_query(
                "SELECT id FROM chat_groups WHERE name = ?",
                (group_name,)
            )

            if existing_group:
                await update.message.reply_text(
                    "❌ Группа с таким названием уже существует.\n"
                    "Пожалуйста, выберите другое название."
                )
                return

            # Сохраняем название группы
            db.execute_query(
                "INSERT INTO chat_groups (name) VALUES (?)",
                (group_name,)
            )

            # Получаем ID созданной группы
            group_id = db.execute_query("SELECT last_insert_rowid() as id")[0]['id']
            context.user_data['current_group_id'] = group_id
            context.user_data['state'] = 'adding_chats_to_group'

            # Получаем список доступных чатов
            chats = db.execute_query(
                "SELECT chat_id, title FROM chats ORDER BY title"
            )

            if not chats:
                keyboard = [[KeyboardButton("🔙 Назад")]]
                reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
                await update.message.reply_text(
                    "❗️ Нет доступных чатов для добавления в группу.\n"
                    "Сначала добавьте чаты с помощью команды /addchat",
                    reply_markup=reply_markup
                )
                return

            # Создаем клавиатуру с чатами
            keyboard = [[KeyboardButton(f"⬜ {chat['title']}")] for chat in chats]
            keyboard.extend([
                [KeyboardButton("✅ Завершить")],
                [KeyboardButton("🔙 Назад")]
            ])

            reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
            await update.message.reply_text(
                f"Группа '{group_name}' создана!\n\n"
                "Теперь выберите чаты для добавления в группу:\n"
                "(нажмите на чат для выбора/отмены, затем 'Завершить')",
                reply_markup=reply_markup
            )

        except Exception as e:
            logger.error(f"Ошибка при обработке названия группы: {e}", exc_info=True)
            await self.error_handler(update, context)

    async def handle_chat_selection_for_group(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка выбора чатов для группы"""
        try:
            if not update.effective_user or update.effective_user.id != ADMIN_ID:
                return

            message_text = update.message.text
            group_id = context.user_data.get('current_group_id')

            if not group_id:
                await update.message.reply_text("❌ Ошибка: группа не найдена")
                await self.show_main_menu(update, context)
                return

            if message_text == "🔙 Назад":
                await self.handle_back_button(update, context)
                return

            if message_text == "✅ Завершить":
                selected_chats = context.user_data.get('selected_chats', [])
                if not selected_chats:
                    await update.message.reply_text(
                        "❗️ Вы не выбрали ни одного чата.\n"
                        "Выберите хотя бы один чат или нажмите 'Назад' для отмены."
                    )
                    return

                # Добавляем выбранные чаты в группу
                for chat_id in selected_chats:
                    db.execute_query(
                        "INSERT OR IGNORE INTO group_chats (group_id, chat_id) VALUES (?, ?)",
                        (group_id, chat_id)
                    )

                await update.message.reply_text("✅ Группа успешно создана и наполнена!")
                context.user_data.clear()
                await self.show_main_menu(update, context)
                return

            # Обработка выбора чата
            if message_text.startswith('⬜ ') or message_text.startswith('✅ '):
                title = message_text[2:]  # Убираем emoji
                chat = db.execute_query(
                    "SELECT chat_id FROM chats WHERE title = ?",
                    (title,)
                )

                if not chat:
                    await update.message.reply_text("❌ Чат не найден")
                    return

                chat_id = chat[0]['chat_id']
                selected_chats = context.user_data.get('selected_chats', [])

                if chat_id in selected_chats:
                    selected_chats.remove(chat_id)
                    new_state = '⬜'
                else:
                    selected_chats.append(chat_id)
                    new_state = '✅'

                context.user_data['selected_chats'] = selected_chats

                # Обновляем клавиатуру
                chats = db.execute_query("SELECT chat_id, title FROM chats ORDER BY title")
                keyboard = []
                for chat in chats:
                    button_text = f"{new_state if chat['chat_id'] in selected_chats else '⬜'} {chat['title']}"
                    keyboard.append([KeyboardButton(button_text)])

                keyboard.extend([
                    [KeyboardButton("✅ Завершить")],
                    [KeyboardButton("🔙 Назад")]
                ])

                reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
                await update.message.reply_text(
                    "Выберите чаты для добавления в группу:\n"
                    "(нажмите на чат для выбора/отмены, затем 'Завершить')",
                    reply_markup=reply_markup
                )

        except Exception as e:
            logger.error(f"Ошибка при выборе чатов для группы: {e}", exc_info=True)
            await self.error_handler(update, context)

    async def error_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("Произошла ошибка. Пожалуйста, попробуйте позже.")

    async def add_chat_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Обработчик команды /addchat для добавления чата в базу данных"""
        try:
            # Проверяем, есть ли у пользователя права администратора
            if not update.effective_user or update.effective_user.id != ADMIN_ID:
                logger.warning(f"Попытка неавторизованного доступа к команде addchat: {update.effective_user.id if update.effective_user else 'Unknown'}")
                return

            chat = update.effective_chat
            if not chat:
                logger.error("Не удалось получить информацию о чате")
                await update.message.reply_text("❌ Ошибка: не удалось получить информацию о чате")
                return

            logger.info(f"Попытка добавления чата: ID={chat.id}, Title={chat.title}, Type={chat.type}")

            # Определяем, является ли чат группой
            is_group = chat.type in ['group', 'supergroup']

            try:
                # Проверяем, существует ли уже такой чат
                result = db.execute_query(
                    "SELECT chat_id FROM chats WHERE chat_id = ?",
                    (chat.id,)
                )

                if result:
                    logger.info(f"Чат {chat.id} уже существует в базе данных")
                    await update.message.reply_text("✅ Этот чат уже добавлен в базу данных")
                    return

                # Добавляем новый чат
                db.execute_query(
                    "INSERT INTO chats (chat_id, title, is_group) VALUES (?, ?, ?)",
                    (chat.id, chat.title or str(chat.id), is_group)
                )

                logger.info(f"Чат успешно добавлен: ID={chat.id}, Title={chat.title}")
                await update.message.reply_text(
                    "✅ Чат успешно добавлен в базу данных\n"
                    "Теперь вы можете использовать этот чат для отправки заданий"
                )

            except sqlite3.Error as e:
                logger.error(f"Ошибка базы данных при добавлении чата: {e}", exc_info=True)
                await update.message.reply_text("❌ Произошла ошибка при добавлении чата в базу данных")
                return

        except Exception as e:
            logger.error(f"Ошибка при выполнении команды addchat: {e}", exc_info=True)
            await update.message.reply_text("❌ Произошла ошибка при обработке команды")

# Блок запуска вне класса
if __name__ == "__main__":
    logger.info("Запуск Telegram бота...")
    try:
        bot = TelegramBot()
        asyncio.run(bot.start())
    except KeyboardInterrupt:
        logger.info("Получен сигнал прерывания, останавливаем бота...")
        if bot and bot._running:
            asyncio.run(bot.stop())
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}", exc_info=True)
        sys.exit(1)