"""Render cron job: check av.by availability."""
import sys
from datetime import datetime, timedelta, timezone

import requests

URL = "https://cars.av.by/filter"
TIMEOUT = 15
MINSK = timezone(timedelta(hours=3))
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


def main():
    now = datetime.now(MINSK).strftime("%d.%m.%Y %H:%M")
    try:
        resp = requests.get(URL, headers=HEADERS, timeout=TIMEOUT)
        if resp.status_code == 200:
            print(f"[{now}] OK — {URL} ({resp.status_code})")
        else:
            print(f"[{now}] WARN — {URL} вернул {resp.status_code}", file=sys.stderr)
            sys.exit(1)
    except requests.ConnectionError as e:
        print(f"[{now}] FAIL — {URL} недоступен: {e}", file=sys.stderr)
        sys.exit(1)
    except requests.Timeout:
        print(f"[{now}] FAIL — {URL} таймаут ({TIMEOUT}s)", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"[{now}] ERROR — {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
