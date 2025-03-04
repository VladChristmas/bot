class NavigationManager:
    """Менеджер навигации для отслеживания состояний и истории перемещений в меню"""

    def __init__(self):
        self.menu_states = {
            'main_menu': {
                'keyboard': [
                    ["📝 Создать новое задание"],
                    ["📋 Просмотр активных заданий"],
                    ["👥 Просмотр списка подключенных чатов"],
                    ["👥 Создать группу чатов"],
                    ["⚙️ Настройки", "❓ Помощь"]
                ],
                'text': "📋 Выберите действие:"
            },
            'settings': {
                'keyboard': [
                    ["👥 Управление чатами", "🔔 Уведомления"],
                    ["🔐 Права доступа", "⚙️ Конфигурация"],
                    ["🔙 Назад", "🏠 Главное меню"]
                ],
                'text': "⚙️ Настройки бота\nВыберите раздел настроек:"
            },
            'creating_chat_group': {
                'keyboard': [
                    ["🔙 Отмена"]
                ],
                'text': "👥 Введите название для новой группы чатов:"
            },
            'adding_chats_to_group': {
                'keyboard': [
                    ["✅ Завершить", "🔙 Назад"]
                ],
                'text': "👥 Выберите чаты для добавления в группу:"
            },
            'statistics': {
                'keyboard': [
                    ["📊 Активные задания", "📈 Общая статистика"],
                    ["🔙 Назад"]
                ],
                'text': "📊 Выберите тип статистики:"
            }
        }

    def get_previous_state(self, current_state):
        """Определяет предыдущее состояние на основе текущего"""
        state_hierarchy = {
            'awaiting_task_text': 'main_menu',
            'choosing_recipient_type': 'awaiting_task_text',
            'selecting_recipients': 'choosing_recipient_type',
            'creating_chat_group': 'main_menu',
            'adding_chats_to_group': 'creating_chat_group',
            'settings': 'main_menu',
            'statistics': 'main_menu',
            'viewing_tasks': 'main_menu',
            'viewing_chats': 'main_menu'
        }
        return state_hierarchy.get(current_state, 'main_menu')

    def get_menu_markup(self, state):
        """Возвращает разметку клавиатуры для указанного состояния"""
        if state in self.menu_states:
            return self.menu_states[state]['keyboard'], self.menu_states[state]['text']
        return None, None

    def clear_user_state(self, user_data):
        """Очищает данные пользовательской сессии, сохраняя важные данные"""
        if not user_data:
            return

        keys_to_preserve = {'navigation_history', 'state'}
        preserved_data = {k: user_data[k] for k in keys_to_preserve if k in user_data}
        user_data.clear()
        user_data.update(preserved_data)

    def add_to_history(self, user_data, state):
        """Добавляет состояние в историю навигации"""
        if not user_data:
            return

        if 'navigation_history' not in user_data:
            user_data['navigation_history'] = []

        # Не добавляем повторяющиеся состояния подряд
        if not user_data['navigation_history'] or user_data['navigation_history'][-1] != state:
            user_data['navigation_history'].append(state)

        # Ограничиваем историю последними 10 состояниями
        if len(user_data['navigation_history']) > 10:
            user_data['navigation_history'] = user_data['navigation_history'][-10:]

    def get_last_state(self, user_data):
        """Получает последнее состояние из истории"""
        if user_data and 'navigation_history' in user_data and user_data['navigation_history']:
            return user_data['navigation_history'][-1]
        return 'main_menu'