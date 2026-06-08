from ..models import db, ActivityLog

ACTION_LABELS = {
    'login':                'Вход в систему',
    'prediction_set':       'Ставка',
    'password_changed':     'Смена пароля',
    'profile_updated':      'Обновление профиля',
    'admin_password_reset': 'Сброс пароля пользователя',
    'admin_toggle':         'Изменение прав',
    'results_simulated':    'Симуляция матчей',
    'scores_reset':         'Сброс ставок и очков',
    'db_reset':             'Сброс базы данных',
    'theme_changed':        'Смена темы',
    'featured_set':         'Настройка матчей для ставок',
    'user_created':         'Создание пользователя',
    'user_deleted':         'Удаление пользователя',
    'pl_matches_loaded':    'Загрузка матчей АПЛ',
    'cl_matches_loaded':    'Загрузка матчей ЛЧ',
    'wc_matches_loaded':    'Загрузка матчей ЧМ',
    'logout':               'Выход из системы',
    'note_set':             'Заметка на пользователя',
    'superuser_revoked':    'Снятие прав суперадмина',
    'betting_lock':         'Блокировка ставок',
    'score_manual_set':     'Счёт матча установлен вручную',
    'score_manual_cleared': 'Счёт матча убран',
    'points_manual_set':    'Очки установлены вручную',
    'points_manual_unlocked': 'Очки разблокированы',
    'league_toggled':       'Переключение лиги',
    'league_order_changed': 'Порядок лиг изменён',
    'pred_days_changed':    'Глубина прогнозов изменена',
    'teams_ru_applied':     'Русские названия команд',
    'teams_ru_translated':  'Перевод через Groq',
}


def log_action(user_id, action, details=None):
    try:
        entry = ActivityLog(user_id=user_id, action=action, details=details)
        db.session.add(entry)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        print(f"[activity] log failed: {e}")
