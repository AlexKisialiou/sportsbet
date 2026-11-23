"""Render cron job: check av.by availability."""
import sys
from datetime import datetime, timedelta, timezone
from urllib.request import urlopen
from urllib.error import URLError

URL = "https://cars.av.by/filter"
TIMEOUT = 15
MINSK = timezone(timedelta(hours=3))


def main():
    now = datetime.now(MINSK).strftime("%d.%m.%Y %H:%M")
    try:
        with urlopen(URL, timeout=TIMEOUT) as resp:
            status = resp.status
        if status == 200:
            print(f"[{now}] OK — {URL} ({status})")
        else:
            print(f"[{now}] WARN — {URL} вернул {status}", file=sys.stderr)
            sys.exit(1)
    except URLError as e:
        print(f"[{now}] FAIL — {URL} недоступен: {e.reason}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"[{now}] ERROR — {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
