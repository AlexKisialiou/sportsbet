from datetime import datetime
from ..models import db, Prediction, PredictionPoints, Match, Tour
from sqlalchemy import func


def calc_points(pred_home, pred_away, real_home, real_away):
    """Return (points, reason) for a prediction vs real score."""
    if pred_home == real_home and pred_away == real_away:
        return 3, "exact"

    def winner(h, a):
        return "home" if h > a else ("away" if a > h else "draw")

    if winner(pred_home, pred_away) == winner(real_home, real_away):
        return 1, "winner"

    return 0, "none"


def update_points_for_match(match, commit=True):
    """Calculate and save/update PredictionPoints for all predictions on a finished match."""
    if not match.score or match.status != "finished":
        return 0

    real_home = match.score.home_score
    real_away = match.score.away_score
    count = 0

    for pred in Prediction.query.filter_by(match_id=match.id).all():
        points, reason = calc_points(pred.home_score, pred.away_score, real_home, real_away)

        if pred.result:
            pred.result.points = points
            pred.result.reason = reason
            pred.result.calculated_at = datetime.utcnow()
        else:
            db.session.add(PredictionPoints(
                prediction_id=pred.id,
                points=points,
                reason=reason,
            ))
        count += 1

    if commit:
        db.session.commit()
    return count


def get_leaderboard(last_days=None):
    """
    Returns list of dicts: { user, total, last_day }
    last_days: list of date objects — points for matches on any of those dates.
    """
    from ..models import User

    rows = (
        db.session.query(
            User,
            func.coalesce(func.sum(PredictionPoints.points), 0).label("total"),
        )
        .outerjoin(Prediction, User.id == Prediction.user_id)
        .outerjoin(PredictionPoints, Prediction.id == PredictionPoints.prediction_id)
        .group_by(User.id)
        .order_by(func.coalesce(func.sum(PredictionPoints.points), 0).desc())
        .all()
    )

    result = []
    for user, total in rows:
        day_pts = []
        for day in (last_days or []):
            pts = (
                db.session.query(func.coalesce(func.sum(PredictionPoints.points), 0))
                .join(Prediction, PredictionPoints.prediction_id == Prediction.id)
                .join(Match, Prediction.match_id == Match.id)
                .filter(
                    func.date(Match.kickoff_time) == day,
                    Prediction.user_id == user.id,
                )
                .scalar()
            ) or 0
            day_pts.append(int(pts))

        result.append({
            "user": user,
            "total": int(total),
            "days": day_pts,
        })

    return result
