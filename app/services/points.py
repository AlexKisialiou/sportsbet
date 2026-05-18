from datetime import datetime
from ..models import db, Prediction, PredictionPoints, Match, Tour
from ..config import POINTS_EXACT, POINTS_WINNER, POINTS_NONE
from sqlalchemy import func


def calc_points(pred_home, pred_away, real_home, real_away):
    if pred_home == real_home and pred_away == real_away:
        return POINTS_EXACT, "exact"

    def winner(h, a):
        return "home" if h > a else ("away" if a > h else "draw")

    if winner(pred_home, pred_away) == winner(real_home, real_away):
        return POINTS_WINNER, "winner"

    return POINTS_NONE, "none"


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


def get_leaderboard(last_days=None, league=None):
    from ..models import User

    if league:
        pts_sq = (
            db.session.query(
                Prediction.user_id,
                func.coalesce(func.sum(PredictionPoints.points), 0).label("total"),
            )
            .join(PredictionPoints, Prediction.id == PredictionPoints.prediction_id)
            .join(Match, Prediction.match_id == Match.id)
            .join(Tour, Match.tour_id == Tour.id)
            .filter(Tour.league == league)
            .group_by(Prediction.user_id)
            .subquery()
        )
        rows = (
            db.session.query(User, func.coalesce(pts_sq.c.total, 0).label("total"))
            .outerjoin(pts_sq, User.id == pts_sq.c.user_id)
            .order_by(func.coalesce(pts_sq.c.total, 0).desc())
            .all()
        )
    else:
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

    pts_map = {}
    has_set = set()
    if last_days:
        from datetime import date as _date
        pts_bulk = (
            db.session.query(
                Prediction.user_id,
                func.date(Match.kickoff_time).label("day"),
                func.coalesce(func.sum(PredictionPoints.points), 0).label("pts"),
            )
            .join(Prediction, PredictionPoints.prediction_id == Prediction.id)
            .join(Match, Prediction.match_id == Match.id)
            .filter(func.date(Match.kickoff_time).in_(last_days))
            .group_by(Prediction.user_id, func.date(Match.kickoff_time))
        )
        has_bulk = (
            db.session.query(Prediction.user_id, func.date(Match.kickoff_time).label("day"))
            .join(Match, Prediction.match_id == Match.id)
            .filter(func.date(Match.kickoff_time).in_(last_days), Match.status == "finished")
            .distinct()
        )
        if league:
            pts_bulk = pts_bulk.join(Tour, Match.tour_id == Tour.id).filter(Tour.league == league)
            has_bulk = has_bulk.join(Tour, Match.tour_id == Tour.id).filter(Tour.league == league)

        def _as_date(v):
            return _date.fromisoformat(v) if isinstance(v, str) else v

        pts_map = {(r.user_id, _as_date(r.day)): int(r.pts) for r in pts_bulk.all()}
        has_set = {(r.user_id, _as_date(r.day)) for r in has_bulk.all()}

    result = []
    for user, total in rows:
        day_pts = [
            {"pts": pts_map.get((user.id, day), 0), "has_pred": (user.id, day) in has_set}
            for day in (last_days or [])
        ]
        result.append({"user": user, "total": int(total), "days": day_pts})

    return result
