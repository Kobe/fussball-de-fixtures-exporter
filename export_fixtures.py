#!/usr/bin/env python3
"""Export fixtures (Spielplan) from a fussball.de widget.

Fetches the match table behind a fussball.de widget key and exports it as
JSON, CSV and ICS (calendar). Works with the widget embed keys that
fussball.de hands out for club/team widgets, e.g.:

    <script src="//www.fussball.de/widget2/-/schluessel/<KEY>/..."></script>

Usage:
    python export_fixtures.py --key 1e84054a-be7f-4225-ae66-73bcc81e1528

Outputs are written to the --out-dir (default: ./out):
    fixtures.json, fixtures.csv, fixtures.ics, widget.html (raw, for debugging)

Notes:
- Future matches usually show "-:-" as score.
- fussball.de obfuscates final scores with a scrambled webfont in some
  views; if the parsed score looks like garbage characters, that is why.
  Dates, kick-off times and team names are plain text and always usable.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from dataclasses import dataclass, asdict, field
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

DEFAULT_KEY = "1e84054a-be7f-4225-ae66-73bcc81e1528"  # Fortuna Pankow, C2-Junioren
BASE = "https://www.fussball.de"
# The widget key is bound to the website registered in the fussball.de
# Widgetcenter. The server validates the /caller/<hostname> path segment
# against that registration, so we must present the registered domain.
DEFAULT_CALLERS = [
    "fortunapankow46ev.de",
    "www.fortunapankow46ev.de",
    "kobe.github.io",
]
WIDGET_URLS = [
    BASE + "/widget2/-/schluessel/{key}/target/widget/caller/{caller}",
    BASE + "/widget2/widget/-/schluessel/{key}/target/widget/caller/{caller}",
]
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept-Language": "de-DE,de;q=0.9",
    "Referer": BASE + "/",
}
TZ = ZoneInfo("Europe/Berlin")

DATE_RE = re.compile(r"(\d{2})\.(\d{2})\.(\d{2,4})")
TIME_RE = re.compile(r"(\d{1,2}):(\d{2})\s*Uhr")


@dataclass
class Fixture:
    date: str | None = None          # ISO date, e.g. 2026-08-22
    time: str | None = None          # e.g. 14:00
    weekday: str | None = None
    competition: str | None = None
    home: str | None = None
    away: str | None = None
    score: str | None = None
    raw: str = field(default="", repr=False)


def _looks_usable(html: str) -> bool:
    if "Uuups" in html:  # fussball.de widget error page
        return False
    return "<table" in html or "club-name" in html


def fetch_widget_html(
    key: str, session: requests.Session, callers: list[str]
) -> str:
    errors = []
    last_body = ""
    for caller in callers:
        for tmpl in WIDGET_URLS:
            url = tmpl.format(key=key, caller=caller)
            headers = dict(HEADERS, Referer=f"https://{caller}/")
            try:
                resp = session.get(url, headers=headers, timeout=30)
            except requests.RequestException as exc:
                errors.append(f"{url}: {exc}")
                continue
            if resp.ok and _looks_usable(resp.text):
                print(f"Widget served for caller {caller}", file=sys.stderr)
                return resp.text
            errors.append(
                f"{url}: HTTP {resp.status_code}, {len(resp.text)} bytes"
            )
            last_body = resp.text
    if last_body:
        print("--- DEBUG last response body ---", file=sys.stderr)
        print(last_body[:10000], file=sys.stderr)
        print("--- END DEBUG ---", file=sys.stderr)
    raise RuntimeError(
        "Could not fetch a usable widget page (is the caller domain the "
        "website registered in the fussball.de Widgetcenter?):\n  "
        + "\n  ".join(errors)
    )


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def parse_fixtures(html: str) -> list[Fixture]:
    soup = BeautifulSoup(html, "html.parser")
    fixtures: list[Fixture] = []
    current_date: str | None = None
    current_time: str | None = None
    current_weekday: str | None = None
    current_competition: str | None = None

    for tr in soup.find_all("tr"):
        classes = tr.get("class") or []
        text = _clean(tr.get_text(" "))
        if not text:
            continue

        # Headline rows carry date, kick-off time and competition, e.g.
        # "Samstag, 22.08.2026 - 14:00 Uhr | D-Junioren Kreisliga"
        if "row-headline" in classes or "row-competition" in classes:
            m = DATE_RE.search(text)
            if m:
                day, month, year = m.groups()
                if len(year) == 2:
                    year = "20" + year
                current_date = f"{year}-{month}-{day}"
                weekday_part = text.split(",", 1)[0]
                current_weekday = weekday_part if m.start() > 0 else None
            t = TIME_RE.search(text)
            current_time = f"{int(t.group(1)):02d}:{t.group(2)}" if t else None
            if "|" in text:
                current_competition = _clean(text.split("|", 1)[1])
            elif not m:
                # competition-only row
                current_competition = text
            continue

        clubs = tr.find_all("td", class_=re.compile("club"))
        if len(clubs) >= 2:
            home = _clean(clubs[0].get_text(" "))
            away = _clean(clubs[-1].get_text(" "))
            score_td = tr.find("td", class_=re.compile("score"))
            score = _clean(score_td.get_text(" ")) if score_td else None
            fixtures.append(
                Fixture(
                    date=current_date,
                    time=current_time,
                    weekday=current_weekday,
                    competition=current_competition,
                    home=home,
                    away=away,
                    score=score or None,
                    raw=text,
                )
            )

    # Fallback: unknown markup — keep raw rows so the user sees something.
    if not fixtures:
        for tr in soup.find_all("tr"):
            text = _clean(tr.get_text(" "))
            if text:
                fixtures.append(Fixture(raw=text))
    return fixtures


def write_json(fixtures: list[Fixture], path: Path) -> None:
    path.write_text(
        json.dumps([asdict(f) for f in fixtures], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def write_csv(fixtures: list[Fixture], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            ["date", "time", "weekday", "competition", "home", "away", "score"]
        )
        for f in fixtures:
            writer.writerow(
                [f.date, f.time, f.weekday, f.competition, f.home, f.away, f.score]
            )


def _ics_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;")


# Minimal VTIMEZONE so calendar clients resolve TZID=Europe/Berlin offline.
VTIMEZONE = [
    "BEGIN:VTIMEZONE",
    "TZID:Europe/Berlin",
    "BEGIN:DAYLIGHT",
    "TZOFFSETFROM:+0100",
    "TZOFFSETTO:+0200",
    "TZNAME:CEST",
    "DTSTART:19700329T020000",
    "RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=-1SU",
    "END:DAYLIGHT",
    "BEGIN:STANDARD",
    "TZOFFSETFROM:+0200",
    "TZOFFSETTO:+0100",
    "TZNAME:CET",
    "DTSTART:19701025T030000",
    "RRULE:FREQ=YEARLY;BYMONTH=10;BYDAY=-1SU",
    "END:STANDARD",
    "END:VTIMEZONE",
]


def write_ics(fixtures: list[Fixture], path: Path, cal_name: str) -> None:
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//fussball-de-fixtures-exporter//DE",
        "CALSCALE:GREGORIAN",
        f"X-WR-CALNAME:{_ics_escape(cal_name)}",
        "X-WR-TIMEZONE:Europe/Berlin",
        "REFRESH-INTERVAL;VALUE=DURATION:PT6H",
        "X-PUBLISHED-TTL:PT6H",
        *VTIMEZONE,
    ]
    stamp = datetime.now(tz=ZoneInfo("UTC")).strftime("%Y%m%dT%H%M%SZ")
    for f in fixtures:
        if not (f.date and f.home and f.away):
            continue
        time_part = f.time or "00:00"
        start = datetime.fromisoformat(f"{f.date}T{time_part}").replace(tzinfo=TZ)
        end = start + timedelta(hours=2)
        summary = f"{f.home} vs. {f.away}"
        if f.score and f.score != "-:-":
            summary += f" ({f.score})"
        # UID must stay stable across runs so subscribed clients update
        # events in place instead of duplicating them.
        uid_hash = hashlib.sha1(
            f"{f.date}|{f.home}|{f.away}".encode()
        ).hexdigest()[:16]
        lines += [
            "BEGIN:VEVENT",
            f"UID:{uid_hash}@fussball-de-fixtures-exporter",
            f"DTSTAMP:{stamp}",
            f"DTSTART;TZID=Europe/Berlin:{start.strftime('%Y%m%dT%H%M%S')}",
            f"DTEND;TZID=Europe/Berlin:{end.strftime('%Y%m%dT%H%M%S')}",
            f"SUMMARY:{_ics_escape(summary)}",
        ]
        if f.competition:
            lines.append(f"DESCRIPTION:{_ics_escape(f.competition)}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    path.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--key", default=DEFAULT_KEY, help="fussball.de widget key")
    parser.add_argument("--out-dir", default="out", type=Path)
    parser.add_argument(
        "--cal-name",
        default="Fortuna Pankow C2-Junioren – Spielplan",
        help="calendar name shown in subscribed clients (X-WR-CALNAME)",
    )
    parser.add_argument(
        "--caller",
        action="append",
        dest="callers",
        help=(
            "website domain registered for the widget in the fussball.de "
            "Widgetcenter (repeatable; defaults to known candidates)"
        ),
    )
    args = parser.parse_args(argv)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    session = requests.Session()

    print(f"Fetching widget {args.key} ...", file=sys.stderr)
    html = fetch_widget_html(args.key, session, args.callers or DEFAULT_CALLERS)
    (args.out_dir / "widget.html").write_text(html, encoding="utf-8")

    fixtures = parse_fixtures(html)
    if not fixtures:
        print("No fixtures found in widget HTML.", file=sys.stderr)
        return 1

    write_json(fixtures, args.out_dir / "fixtures.json")
    write_csv(fixtures, args.out_dir / "fixtures.csv")
    write_ics(fixtures, args.out_dir / "fixtures.ics", args.cal_name)

    for f in fixtures:
        when = f"{f.date or '?'} {f.time or ''}".strip()
        print(f"{when}  {f.home or f.raw} - {f.away or ''}  {f.score or ''}".rstrip())
    print(f"\n{len(fixtures)} fixtures written to {args.out_dir}/", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
