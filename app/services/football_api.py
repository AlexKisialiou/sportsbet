import os
import json
import urllib.request
from collections import defaultdict
from datetime import datetime

from ..models import db, Tour, Match, Score, Team
from ..data.teams_ru import TEAMS_RU
from .points import update_points_for_match

STAGE_MAP = {
    "GROUP_STAGE":    ("ЛЧ Групповой этап", 0),
    "LAST_16":        ("ЛЧ 1/8 финала",     100),
    "QUARTER_FINALS": ("ЛЧ 1/4 финала",     200),
    "SEMI_FINALS":    ("ЛЧ 1/2 финала",     300),
    "FINAL":          ("ЛЧ Финал",          400),
}

STATUS_MAP = {
    "FINISHED":  "finished",
    "IN_PLAY":   "live",
    "PAUSED":    "live",
    "SCHEDULED": "scheduled",
    "TIMED":     "scheduled",
}


def fetch_and_save_cl_matches():
    api_key = os.environ.get("FOOTBALL_API_KEY", "")
    if not api_key:
        raise ValueError("FOOTBALL_API_KEY not set")

    url = "https://api.football-data.org/v4/competitions/CL/matches"
    req = urllib.request.Request(url, headers={"X-Auth-Token": api_key})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode())

    season = data.get("competition", {}).get("currentSeason", {}).get("startDate", "2025")[:4]
    season_str = f"{season}/{int(season) + 1}"

    return _save_matches(data.get("matches", []), season_str)


def _get_or_create_team(team_data):
    ext_id = team_data.get("id")
    if not ext_id:
        return None

    team = Team.query.filter_by(external_id=ext_id).first()
    name = team_data.get("name", "")
    if not team:
        team = Team(
            external_id=ext_id,
            name=name,
            name_ru=TEAMS_RU.get(name),
            short_name=team_data.get("shortName") or team_data.get("tla"),
            crest=team_data.get("crest"),
        )
        db.session.add(team)
        db.session.flush()
    else:
        team.name = name
        team.crest = team_data.get("crest") or team.crest
        # Apply translation if not set manually
        if not team.name_ru:
            team.name_ru = TEAMS_RU.get(name)
    return team


def _get_or_create_tour(stage, matchday, season):
    name_base, round_base = STAGE_MAP.get(stage, (f"ЛЧ {stage}", 500))
    round_number = round_base + (matchday or 0)
    name = f"{name_base} - Тур {matchday}" if stage == "GROUP_STAGE" and matchday else name_base

    tour = Tour.query.filter_by(league="UCL", round_number=round_number, season=season).first()
    if not tour:
        tour = Tour(name=name, season=season, round_number=round_number,
                    league="UCL", status="active")
        db.session.add(tour)
        db.session.flush()
    return tour


def _save_matches(raw_matches, season):
    added = updated = 0

    groups = defaultdict(list)
    for m in raw_matches:
        groups[(m["stage"], m.get("matchday"))].append(m)

    for (stage, matchday), matches in groups.items():
        tour = _get_or_create_tour(stage, matchday, season)

        for m in matches:
            if not m["homeTeam"].get("name") or not m["awayTeam"].get("name"):
                continue

            home_team = _get_or_create_team(m["homeTeam"])
            away_team = _get_or_create_team(m["awayTeam"])
            if not home_team or not away_team:
                continue

            ext_id = m["id"]
            status = STATUS_MAP.get(m["status"], "scheduled")
            kickoff = datetime.fromisoformat(m["utcDate"].replace("Z", "+00:00")).replace(tzinfo=None)
            hs = m["score"]["fullTime"].get("home")
            as_ = m["score"]["fullTime"].get("away")

            existing = Match.query.filter_by(external_id=ext_id).first()
            if existing:
                existing.status = status
                if hs is not None:
                    if existing.score:
                        existing.score.home_score = hs
                        existing.score.away_score = as_
                        existing.score.updated_at = datetime.utcnow()
                    else:
                        db.session.add(Score(match_id=existing.id, home_score=hs, away_score=as_))
                db.session.flush()
                if status == "finished":
                    update_points_for_match(existing)
                updated += 1
            else:
                match = Match(
                    tour_id=tour.id,
                    external_id=ext_id,
                    home_team_id=home_team.id,
                    away_team_id=away_team.id,
                    kickoff_time=kickoff,
                    status=status,
                )
                db.session.add(match)
                db.session.flush()
                if hs is not None:
                    db.session.add(Score(match_id=match.id, home_score=hs, away_score=as_))
                added += 1

    db.session.commit()
    return added, updated
