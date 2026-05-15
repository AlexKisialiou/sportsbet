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
    'logout':               'Выход из системы',
    'note_set':             'Заметка на пользователя',
    'superuser_revoked':    'Снятие прав суперадмина',
}


def log_action(user_id, action, details=None):
    try:
        entry = ActivityLog(user_id=user_id, action=action, details=details)
        db.session.add(entry)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        print(f"[activity] log failed: {e}")
