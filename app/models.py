from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()


class Team(db.Model):
    __tablename__ = "teams"

    id = db.Column(db.Integer, primary_key=True)
    external_id = db.Column(db.Integer, unique=True, nullable=False)
    name = db.Column(db.String(100), nullable=False)        # English name from API
    name_ru = db.Column(db.String(100), nullable=True)      # Russian translation
    short_name = db.Column(db.String(50), nullable=True)    # e.g. "PSG"
    crest = db.Column(db.String(255), nullable=True)        # logo URL from API

    @property
    def display_name(self):
        return self.name_ru or self.name


class Tour(db.Model):
    __tablename__ = "tours"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    season = db.Column(db.String(20), nullable=False)
    round_number = db.Column(db.Integer, nullable=False)
    league = db.Column(db.String(20), default="local")      # "local" / "UCL"
    start_date = db.Column(db.Date, nullable=True)
    end_date = db.Column(db.Date, nullable=True)
    status = db.Column(db.String(20), default="upcoming")   # upcoming / active / finished

    matches = db.relationship("Match", backref="tour", lazy=True, order_by="Match.kickoff_time")


class Match(db.Model):
    __tablename__ = "matches"

    id = db.Column(db.Integer, primary_key=True)
    tour_id = db.Column(db.Integer, db.ForeignKey("tours.id"), nullable=False)
    external_id = db.Column(db.Integer, unique=True, nullable=True)
    home_team_id = db.Column(db.Integer, db.ForeignKey("teams.id"), nullable=False)
    away_team_id = db.Column(db.Integer, db.ForeignKey("teams.id"), nullable=False)
    kickoff_time = db.Column(db.DateTime, nullable=True)
    status = db.Column(db.String(20), default="scheduled")  # scheduled / live / finished

    home_team = db.relationship("Team", foreign_keys=[home_team_id])
    away_team = db.relationship("Team", foreign_keys=[away_team_id])
    score = db.relationship("Score", backref="match", uselist=False, lazy=True)


class Score(db.Model):
    __tablename__ = "scores"

    id = db.Column(db.Integer, primary_key=True)
    match_id = db.Column(db.Integer, db.ForeignKey("matches.id"), nullable=False, unique=True)
    home_score = db.Column(db.Integer, nullable=False, default=0)
    away_score = db.Column(db.Integer, nullable=False, default=0)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    predictions = db.relationship("Prediction", backref="user", lazy=True)


class Prediction(db.Model):
    __tablename__ = "predictions"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    match_id = db.Column(db.Integer, db.ForeignKey("matches.id"), nullable=False)
    home_score = db.Column(db.Integer, nullable=False)
    away_score = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint("user_id", "match_id"),)
