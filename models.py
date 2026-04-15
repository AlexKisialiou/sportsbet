вот from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()


class Tour(db.Model):
    __tablename__ = "tours"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)       # "Тур 12"
    season = db.Column(db.String(20), nullable=False)      # "2025/2026"
    round_number = db.Column(db.Integer, nullable=False)   # 12
    start_date = db.Column(db.Date, nullable=True)
    end_date = db.Column(db.Date, nullable=True)
    status = db.Column(db.String(20), default="upcoming")  # upcoming / active / finished

    matches = db.relationship("Match", backref="tour", lazy=True, order_by="Match.kickoff_time")

    def __repr__(self):
        return f"<Tour {self.round_number} {self.season}>"


class Match(db.Model):
    __tablename__ = "matches"

    id = db.Column(db.Integer, primary_key=True)
    tour_id = db.Column(db.Integer, db.ForeignKey("tours.id"), nullable=False)
    home_team = db.Column(db.String(100), nullable=False)
    away_team = db.Column(db.String(100), nullable=False)
    kickoff_time = db.Column(db.DateTime, nullable=True)
    status = db.Column(db.String(20), default="scheduled")  # scheduled / live / finished

    score = db.relationship("Score", backref="match", uselist=False, lazy=True)

    def __repr__(self):
        return f"<Match {self.home_team} vs {self.away_team}>"


class Score(db.Model):
    __tablename__ = "scores"

    id = db.Column(db.Integer, primary_key=True)
    match_id = db.Column(db.Integer, db.ForeignKey("matches.id"), nullable=False, unique=True)
    home_score = db.Column(db.Integer, nullable=False, default=0)
    away_score = db.Column(db.Integer, nullable=False, default=0)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<Score {self.home_score}:{self.away_score}>"
