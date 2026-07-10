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
    'prompt_hint_updated':  'Хинт для Бендера изменён',
    'prompt_hint_applied':  'Хинт добавлен в промпт Бендера',
    'team_form_count_changed': 'Подсказка форма команды',
    'auto_fetch_changed':   'Автообновление матчей',
    'reveal_live_predictions': 'Ставки во время матча',
    'comment_set':             'Комментарий к матчу',
    'comment_deleted':         'Удаление комментария',
    'comment_max_length_changed': 'Макс. длина комментария',
    'match_live_set':            'Матч: статус «идёт»',
    'match_live_cleared':        'Матч: статус «запланирован»',
    'copy_prod_to_sandbox':      'Копирование прод → сэндбокс',
    'tg_remind':                 'TG-напоминание о ставках',
    'tg_remind_settings':        'Настройки TG-напоминания',
    'bender_manual':             'Ручной запрос прогнозов Бендера',
}


def log_action(user_id, action, details=None):
    try:
        entry = ActivityLog(user_id=user_id, action=action, details=details)
        db.session.add(entry)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        print(f"[activity] log failed: {e}")
