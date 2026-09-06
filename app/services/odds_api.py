import os
import json
from urllib.request import urlopen, Request
from urllib.parse import urlencode
from difflib import SequenceMatcher

LEAGUE_SPORT_KEY = {
    "UCL":    "soccer_uefa_champs_league",
    "UCL2627":"soccer_uefa_champs_league",
    "PL":     "soccer_epl",
    "WC":     "soccer_fifa_world_cup",
}


def _sim(a, b):
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _find_match(home_en, away_en, odds_list):
    best, best_item = 0.0, None
    for item in odds_list:
        h = _sim(home_en, item["home_team"])
        a = _sim(away_en, item["away_team"])
        if h < 0.30 or a < 0.30:
            continue
        score = (h + a) / 2
        if score > best:
            best, best_item = score, item
    return best_item if best >= 0.45 else None


def _extract_avg_odds(item):
    """Average h2h decimal odds across all bookmakers. Returns {home, draw, away} or None."""
    if not item or not item.get("bookmakers"):
        return None
    home_team = item["home_team"]
    buckets = {"home": [], "draw": [], "away": []}
    for bm in item["bookmakers"]:
        for market in bm.get("markets", []):
            if market["key"] != "h2h":
                continue
            for outcome in market["outcomes"]:
                name, price = outcome["name"], outcome["price"]
                if name == home_team:
                    buckets["home"].append(price)
                elif name == "Draw":
                    buckets["draw"].append(price)
                else:
                    buckets["away"].append(price)
    h, a = buckets["home"], buckets["away"]
    if not (h and a):
        return None
    result = {
        "home": round(sum(h) / len(h), 2),
        "away": round(sum(a) / len(a), 2),
    }
    if buckets["draw"]:
        d = buckets["draw"]
        result["draw"] = round(sum(d) / len(d), 2)
    return result


def fetch_odds_for_matches(match_data, league):
    """Fetch h2h odds from The Odds API for a list of matches.

    match_data: list of (match_id, home_name_en, away_name_en)
    Returns: {match_id: {"home": float, "draw": float|None, "away": float}}
    """
    api_key = os.environ.get("ODDS_API_KEY")
    if not api_key:
        print("[odds] ODDS_API_KEY not set — skipping")
        return {}

    sport = LEAGUE_SPORT_KEY.get(league)
    if not sport:
        print(f"[odds] unknown league: {league}")
        return {}

    params = urlencode({
        "apiKey": api_key,
        "regions": "eu",
        "markets": "h2h",
        "oddsFormat": "decimal",
    })
    url = f"https://api.the-odds-api.com/v4/sports/{sport}/odds?{params}"

    try:
        req = Request(url, headers={"Accept": "application/json"})
        with urlopen(req, timeout=10) as resp:
            remaining = resp.headers.get("x-requests-remaining", "?")
            print(f"[odds] {sport}: {remaining} requests remaining")
            data = json.loads(resp.read().decode())
    except Exception as e:
        print(f"[odds] fetch error ({league}): {e}")
        return {}

    if not isinstance(data, list):
        print(f"[odds] unexpected response: {data!r:.120}")
        return {}

    result = {}
    for match_id, home_en, away_en in match_data:
        item = _find_match(home_en, away_en, data)
        odds = _extract_avg_odds(item)
        if odds:
            result[match_id] = odds
            print(f"[odds] matched {home_en} vs {away_en}: {odds}")
        else:
            print(f"[odds] no match for {home_en} vs {away_en}")
    return result
