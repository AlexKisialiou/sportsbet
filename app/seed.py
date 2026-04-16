from datetime import datetime, timedelta
from .models import db, Team, Tour, Match, Score, User, Prediction

# Test teams with negative external_ids to avoid collision with real API data
TEST_TEAMS = [
    (-1,  "Real Madrid CF",             "Реал Мадрид"),
    (-2,  "FC Barcelona",               "Барселона"),
    (-3,  "FC Bayern München",          "Бавария"),
    (-4,  "Arsenal FC",                 "Арсенал"),
    (-5,  "Paris Saint-Germain FC",     "ПСЖ"),
    (-6,  "FC Internazionale Milano",   "Интер"),
    (-7,  "Borussia Dortmund",          "Боруссия Дортмунд"),
    (-8,  "Chelsea FC",                 "Челси"),
]

now = datetime.utcnow()

TEST_MATCHES = [
    # (home_ext_id, away_ext_id, kickoff_delta_days, status, home_score, away_score)
    (-1, -4,  -7, "finished", 3, 1),   # Реал - Арсенал (завершён)
    (-3, -6,  -7, "finished", 0, 2),   # Бавария - Интер (завершён)
    (-2, -5,  -3, "live",     None, None),   # Барселона - ПСЖ (идёт)
    (-7, -8,   3, "scheduled", None, None),  # Боруссия - Челси (предстоит)
    (-4, -1,   3, "scheduled", None, None),  # Арсенал - Реал (предстоит)
    (-5, -2,  10, "scheduled", None, None),  # ПСЖ - Барселона (предстоит)
]

# Predictions for test user on test matches (by match index in TEST_MATCHES)
# (match_index, predicted_home, predicted_away)
TEST_PREDICTIONS = [
    (0, 2, 1),  # Реал - Арсенал: угадал домашнего
    (1, 1, 1),  # Бавария - Интер: промахнулся
    (3, 1, 0),  # Боруссия - Челси: ставка на предстоящий
    (4, 2, 2),  # Арсенал - Реал: ставка на предстоящий
]


def run():
    if User.query.count() == 0:
        db.session.add(User(username="test"))
        db.session.commit()

    user = User.query.filter_by(username="test").first()

    # Skip if test data already exists
    if Team.query.filter(Team.external_id < 0).count() > 0:
        return

    # Create test teams
    teams = {}
    for ext_id, name_en, name_ru in TEST_TEAMS:
        t = Team(external_id=ext_id, name=name_en, name_ru=name_ru)
        db.session.add(t)
        teams[ext_id] = t
    db.session.flush()

    # Create test tour
    tour = Tour(name="ЛЧ 1/4 финала", season="2025/2026",
                round_number=200, league="UCL", status="active")
    db.session.add(tour)
    db.session.flush()

    # Create test matches
    matches = []
    for home_ext, away_ext, delta_days, status, hs, as_ in TEST_MATCHES:
        kickoff = now + timedelta(days=delta_days)
        m = Match(tour_id=tour.id,
                  home_team_id=teams[home_ext].id,
                  away_team_id=teams[away_ext].id,
                  kickoff_time=kickoff,
                  status=status)
        db.session.add(m)
        db.session.flush()
        if hs is not None:
            db.session.add(Score(match_id=m.id, home_score=hs, away_score=as_))
        matches.append(m)

    # Create test predictions
    for match_idx, pred_home, pred_away in TEST_PREDICTIONS:
        db.session.add(Prediction(
            user_id=user.id,
            match_id=matches[match_idx].id,
            home_score=pred_home,
            away_score=pred_away,
        ))

    db.session.commit()
