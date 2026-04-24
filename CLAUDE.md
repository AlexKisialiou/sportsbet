# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# Mamkiny Gamers United

Flask web app for UCL match score predictions among a small group of users. UI is in Russian.

## Stack
- Python / Flask, SQLAlchemy (PostgreSQL — locally and on Render)
- football-data.org API for Champions League data
- Groq API (`llama-3.1-8b-instant`) for AI commentary via Bender bot
- Bootstrap 5 dark theme (CDN) + custom CSS in `app/static/css/main.css`
- Deployed on https://dashboard.render.com/

## Local Development
```
.\run_local.bat
```
Creates `.venv` on first run, installs deps, loads `.env` via python-dotenv. Runs on http://localhost:5000.

Local PostgreSQL: `postgresql://postgres:admin@localhost:5432/sportsbet`  
Create DB first: `createdb sportsbet`  
Schema is created automatically via `db.create_all()` on комиstartup.

## Environment Variables
| Variable | Description |
|----------|-------------|
| `DATABASE_URL` | PostgreSQL URL. `postgres://` is auto-converted to `postgresql://` (Render compat). Falls back to SQLite if unset. |
| `FOOTBALL_API_KEY` | football-data.org free tier key |
| `GROQ_API_KEY` | Groq API key for Bender AI commentary |
| `SECRET_KEY` | Flask session secret |
| `ADMIN_USERNAME/PASSWORD/NICKNAME` | Seeded admin user credentials |
| `USER1_*/USER2_*/USER3_*` | Seeded regular user credentials (USERNAME/PASSWORD/NICKNAME) |

Do NOT set `RESET_DB` — removed. Reset is done via admin panel.

## Architecture

### App Factory (`app/__init__.py`)
`create_app()` runs in this order:
1. Configures Flask + SQLAlchemy; fixes `postgres://` → `postgresql://`
2. `db.create_all()` — creates schema if not exists
3. Applies inline migrations via raw `ALTER TABLE` + `try/except` (no Alembic)
4. Calls `seed.run()` — ensures bot user and real users exist
5. Calls `fetch_and_save_cl_matches()` — refreshes UCL data from API on every startup
6. Registers three blueprints: `main_bp`, `api_bp`, `auth_bp`
7. Context processor injects: `current_user`, `APP_NAME`, `APP_VERSION`, `current_theme`

### Config (`app/config.py`)
Central constants: `APP_NAME`, `APP_VERSION`, `POINTS_EXACT/WINNER/NONE`, `CHAT_MAX_LENGTH`, `AVATAR_EMOJIS`, `AVATAR_COLORS`.

### Data Models (`app/models.py`)
| Model | Key Fields / Notes |
|---|---|
| `Team` | `external_id`, `name`, `name_ru`, `short_name`, `crest`; `display_name` returns RU name when set |
| `Tour` | `name`, `season`, `round_number`, `league` (`"local"`/`"UCL"`), `status` |
| `Match` | `tour_id`, `home_team_id`, `away_team_id`, `kickoff_time`, `status`, `featured` (bool) |
| `Score` | 1:1 with Match; `home_score`, `away_score` |
| `User` | `username`, `password_hash`, `is_admin`, `nickname`, `is_bot`, `avatar_emoji`, `avatar_color`; `display_name` returns nickname or username |
| `Prediction` | `user_id`, `match_id`, `home_score`, `away_score`; unique on `(user_id, match_id)` |
| `PredictionPoints` | 1:1 with Prediction; `points` (0/1/3), `reason` (`"exact"`/`"winner"`/`"none"`) |
| `Commentary` | `match_label`, `text`; Bender's AI comments; `"__standings__"` label for leaderboard commentary |
| `Setting` | `key` (PK), `value`; stores `theme` setting |

### Routes
| Blueprint | Route | Description |
|---|---|---|
| `main` | `GET /` | Main page (login required): leaderboard, predictions table, featured matches |
| `main` | `GET /admin` | Admin panel (admin required) |
| `api` | `POST /api/cl-matches` | Fetch fresh data from football-data.org |
| `api` | `POST /api/prediction` | Upsert user's prediction (login required) |
| `api` | `POST /api/featured-matches` | Set featured matches + regenerate Bender AI picks (admin) |
| `api` | `POST /api/simulate-results` | Mark matches finished (1:0), recalculate points, generate standings commentary (admin) |
| `api` | `POST /api/settings/theme` | Set color theme: `navy`/`forest`/`purple`/`crimson` (admin) |
| `api` | `POST /api/reset-scores` | Delete all Predictions + PredictionPoints (admin, confirm=`"RESET"`) |
| `api` | `POST /api/reset-db` | Full DB drop+recreate+seed (admin, confirm=`"RESET"`) |
| `auth` | `GET/POST /login` | Session login; bot users blocked |
| `auth` | `GET /logout` | Clear session |
| `auth` | `GET/POST /profile` | Edit nickname, avatar emoji+color (login required) |

### Admin Panel (`/admin`)
- **Загрузить матчи ЛЧ** — fetches from football-data.org
- **Матчи для ставок** — checkbox list to mark featured matches; triggers Bender AI pick generation
- **Симуляция результатов** — set selected matches to finished (1:0), recalculate points
- **Тема оформления** — 4 color themes: Синяя (navy), Зелёная (forest), Фиолет (purple), Алая (crimson)
- **Опасная зона** — reset scores only, or full DB reset (with confirmations)

### CSS Theming (`app/static/css/main.css`)
CSS custom properties on `:root` (navy default) + `[data-theme="forest|purple|crimson"]` overrides.  
Key variables: `--bg`, `--surface`, `--surface-hi`, `--surface-deep`, `--border`, `--border-sub`, `--accent`, `--accent-rgb`, `--accent-muted`, `--accent-bg`, `--accent-dim`, `--text`, `--text-muted`, `--text-dim`, `--text-label`.  
Applied via `data-theme` on `<body>` from `current_theme` template variable.  
Bender panel colors (gold/green) are hardcoded — not theme-dependent.

### Layout (`index.html`)
- Two-column grid: left 420px leaderboard + fill status, right: featured scheduled matches for betting
- Full-width predictions table below: finished matches from last 4 game days
- Floating bottom tray: 📊 Бендер об очках (gold chip), 📋 Прогноз (green chip)

### Services
- **`app/services/football_api.py`** — Fetches UCL matches from `api.football-data.org/v4/competitions/CL/matches`, upserts Teams/Tours/Matches/Scores, calls `update_points_for_match()` on newly finished matches.
- **`app/services/points.py`** — `calc_points()`: 3 pts exact, 1 pt correct winner/draw, 0 otherwise. `update_points_for_match()`: upserts `PredictionPoints`. `get_leaderboard(last_days=N)`: users sorted by total with per-day breakdown.
- **`app/services/groq_api.py`** — `generate_bender_pick(home, away)` → analytical football forecast, parses `АНАЛИЗ:` / `СЧЁТ: X:Y`. `generate_bender_standings(text)` → Bender-persona leaderboard comment. Both use `llama-3.1-8b-instant`.

### Auth (`app/auth.py`)
`login_required` and `admin_required` decorators. `get_current_user()` reads `session["user_id"]`.

### Seed (`app/seed.py`)
Always creates the Bender bot user (`is_bot=True`). Creates up to 4 real users from env vars only if no non-bot users exist yet. USER2/USER3 are optional.

### Russian Team Names
`app/data/teams_ru.py` — `TEAMS_RU` dict mapping English → Russian names for ~50 clubs.

## Render Deployment
- **Build:** `pip install -r requirements.txt`
- **Start:** `bash start.sh` → `gunicorn wsgi:app`
- Set `DATABASE_URL`, `FOOTBALL_API_KEY`, `GROQ_API_KEY`, `SECRET_KEY`, user credentials in Render env vars
- Render provides `postgres://` URL — auto-fixed to `postgresql://` in `create_app()`

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
