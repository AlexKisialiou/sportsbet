# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# SportsBet

Flask web app for UCL match score predictions among a small group of users. UI is in Russian.

## Stack
- Python / Flask, SQLAlchemy (SQLite locally, PostgreSQL on Render)
- football-data.org API for Champions League data
- Groq API (`llama-3.1-8b-instant`) for AI commentary via Bender bot
- Bootstrap 5 dark theme (CDN, no static assets directory)
- Deployed on https://dashboard.render.com/

## Local Development
```
.\run_local.bat
```
Creates `.venv` on first run, installs deps, loads `.env` via python-dotenv. Runs on http://localhost:5000.

## Environment Variables
| Variable | Description |
|----------|-------------|
| `FOOTBALL_API_KEY` | football-data.org free tier key |
| `DATABASE_URL` | PostgreSQL URL on Render; omit for local SQLite |
| `GROQ_API_KEY` | Groq API key for Bender AI commentary |
| `RESET_DB` | Set to `1` to drop/recreate DB on startup (local only) |
| `SECRET_KEY` | Flask session secret |
| `ADMIN_USERNAME/PASSWORD/NICKNAME` | Seeded admin user credentials |
| `USER1_*/USER2_*/USER3_*` | Seeded regular user credentials (USERNAME/PASSWORD/NICKNAME) |

## Architecture

### App Factory (`app/__init__.py`)
`create_app()` runs in this order:
1. Configures Flask + SQLAlchemy
2. Applies inline migrations via raw `ALTER TABLE` + `try/except` (no Alembic)
3. Calls `seed.run()` — ensures bot user and real users exist
4. Calls `fetch_and_save_cl_matches()` — refreshes UCL data from API on every startup
5. Registers three blueprints: `main_bp`, `api_bp`, `auth_bp`
6. Injects `current_user` into all templates via context processor

### Data Models (`app/models.py`)
| Model | Key Fields / Notes |
|---|---|
| `Team` | `external_id`, `name`, `name_ru`, `short_name`, `crest`; `display_name` returns RU name when set |
| `Tour` | `name`, `season`, `round_number`, `league` (`"local"`/`"UCL"`), `status` |
| `Match` | `tour_id`, `home_team_id`, `away_team_id`, `kickoff_time`, `status`, `featured` (bool) |
| `Score` | 1:1 with Match; `home_score`, `away_score` |
| `User` | `username`, `password_hash`, `is_admin`, `nickname`, `is_bot`; `display_name` returns nickname or username |
| `Prediction` | `user_id`, `match_id`, `home_score`, `away_score`; unique on `(user_id, match_id)` |
| `PredictionPoints` | 1:1 with Prediction; `points` (0/1/3), `reason` (`"exact"`/`"winner"`/`"none"`) |
| `Commentary` | `match_label`, `text`; stores Bender's AI-generated comments; `"__standings__"` label for the leaderboard commentary |

### Routes
| Blueprint | Route | Description |
|---|---|---|
| `main` | `GET /` | Main page (login required): leaderboard, predictions table, featured matches for betting |
| `main` | `GET /admin` | Admin panel (admin required): manage featured matches, simulate results |
| `api` | `POST /api/cl-matches` | Fetch fresh data from football-data.org |
| `api` | `POST /api/prediction` | Upsert user's prediction (login required) |
| `api` | `POST /api/featured-matches` | Set featured matches + regenerate Bender AI picks (admin) |
| `api` | `POST /api/simulate-results` | Mark matches finished (1:0), recalculate points, generate standings commentary (admin) |
| `auth` | `GET/POST /login` | Session login; bot users blocked |
| `auth` | `GET /logout` | Clear session |

### Services
- **`app/services/football_api.py`** — Fetches UCL matches from `api.football-data.org/v4/competitions/CL/matches`, upserts Teams/Tours/Matches/Scores, calls `update_points_for_match()` on newly finished matches. `STAGE_MAP` maps API stage strings to Russian tour names + round numbers. `STATUS_MAP` translates API statuses.
- **`app/services/points.py`** — `calc_points()`: 3 pts exact, 1 pt correct winner/draw, 0 otherwise. `update_points_for_match()`: upserts `PredictionPoints` for all predictions on a finished match. `get_leaderboard(last_days=N)`: returns users sorted by total points with per-day breakdown.
- **`app/services/groq_api.py`** — `generate_bender_pick(home, away)` → parses `ТЕКСТ:` / `СЧЁТ: X:Y` from LLM response. `generate_bender_standings(text)` → short Russian leaderboard comment. Uses `llama-3.1-8b-instant`.

### Auth (`app/auth.py`)
`login_required` and `admin_required` decorators. `get_current_user()` reads `session["user_id"]`.

### Seed (`app/seed.py`)
Always creates the Bender bot user (`is_bot=True`). Creates 4 real users from env vars only if no non-bot users exist yet.

### Russian Team Names
`app/data/teams_ru.py` — `TEAMS_RU` dict mapping English → Russian names for ~50 clubs. Applied on save; does not overwrite manually set `name_ru`.

## Render Deployment
- **Build:** `pip install -r requirements.txt`
- **Start:** `bash start.sh` → `gunicorn wsgi:app`
- Set `FOOTBALL_API_KEY`, `GROQ_API_KEY`, `SECRET_KEY`, user credentials in Render env vars
- Do NOT set `RESET_DB` on Render

## football-data.org API
- **Docs:** https://docs.football-data.org/general/v4/index.html
- Free tier: 10 req/min; Auth header: `X-Auth-Token: <key>`
- Endpoint used: `GET /competitions/CL/matches`

### Stage → Tour mapping
| API stage | Tour name | round_number |
|-----------|-----------|--------------|
| `GROUP_STAGE` | ЛЧ Групповой этап - Тур N | N (1–8) |
| `LAST_16` | ЛЧ 1/8 финала | 100 |
| `QUARTER_FINALS` | ЛЧ 1/4 финала | 200 |
| `SEMI_FINALS` | ЛЧ 1/2 финала | 300 |
| `FINAL` | ЛЧ Финал | 400 |

### Match status mapping
| API value | Saved as |
|-----------|----------|
| `FINISHED` | `finished` |
| `IN_PLAY` / `PAUSED` | `live` |
| `SCHEDULED` / `TIMED` | `scheduled` |
