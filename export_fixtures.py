#!/usr/bin/env python3
"""Export fixtures (Spielplan) from a fussball.de team widget.

Fetches the match list behind a fussball.de widget key and exports it as
JSON, CSV and ICS (calendar). It targets the current widget backend at
``next.fussball.de``, which the official embed snippet loads in an iframe:

    <script src="https://www.fussball.de/widgets.js"></script>
    <div class="fussballde_widget" data-id="<KEY>" data-type="team-matches">

Usage:
    python export_fixtures.py --key 1e84054a-be7f-4225-ae66-73bcc81e1528

Outputs are written to the --out-dir (default: ./out):
    fixtures.json, fixtures.csv, fixtures.ics

How it works
------------
The widget page ships all matches as JSON inside ``__NEXT_DATA__``. The
human-readable text (dates, kick-off times, team and competition names,
results) is obfuscated with a per-request web font that maps random
Private-Use codepoints onto ordinary glyphs. We download that font, read
its ``cmap`` (Private-Use codepoint -> PostScript glyph name -> real
character via the Adobe Glyph List) and decode the text back.

The widget only serves data when the request looks like it comes from the
website registered for the widget in the fussball.de Widgetcenter, so we
send that domain as ``Referer`` (see --caller).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import io
import json
import re
import sys
import time
from dataclasses import dataclass, asdict, field
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from fontTools.agl import toUnicode
from fontTools.ttLib import TTFont

DEFAULT_KEY = "1e84054a-be7f-4225-ae66-73bcc81e1528"  # Fortuna Pankow C-Junioren II
NEXT_BASE = "https://next.fussball.de"
FONT_BASE = "https://www.fussball.de"
# Widget flavours that carry a match list; team-matches is the usual one.
WIDGET_TYPES = ["team-matches", "club-matches", "matches"]
# Website registered for the widget in the fussball.de Widgetcenter. The
# backend validates the request's Referer/host against it.
DEFAULT_CALLERS = [
    "https://fortunapankow46ev.de/",
    "https://www.fortunapankow46ev.de/",
]
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
TZ = ZoneInfo("Europe/Berlin")
# Stands in for a team the pairing has not produced yet (cup rounds, byes).
OPEN_OPPONENT = "?"

NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S
)
DATE_RE = re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d{4})")
TIME_RE = re.compile(r"(\d{1,2}):(\d{2})")
# The classic match page carries the venue as the text of the location link,
# e.g. `<a ...> Sportplatz X, Musterstr. 1, 13129 Berlin <span
# class="icon-location">`. It is plain text (not font-obfuscated).
VENUE_RE = re.compile(r'<a\b[^>]*>\s*([^<]+?)\s*<span[^>]*icon-location', re.I | re.S)


@dataclass
class Fixture:
    date: str | None = None          # ISO date, e.g. 2026-08-22
    time: str | None = None          # e.g. 14:00
    weekday: str | None = None
    competition: str | None = None
    home: str | None = None
    away: str | None = None
    score: str | None = None
    venue: str | None = None         # Spielstätte incl. address
    status: str | None = None        # scheduled / acknowledged / ...
    match_id: str = field(default="", repr=False)


def _strip(text: str) -> str:
    """Drop zero-width joiners the widget sprinkles into names, tidy spaces."""
    text = text.replace("​", "").replace("‎", "").replace("‏", "")
    return re.sub(r"\s+", " ", text).strip()


def _get(obj: dict, key: str, default):
    """``dict.get`` that also treats an explicit ``null`` as missing.

    The widget ships keys with a JSON ``null`` rather than omitting them --
    e.g. ``homeTeam`` on a fixture whose opponent is not drawn yet -- and
    plain ``.get(key, default)`` would hand that ``None`` on to the caller.
    """
    value = obj.get(key)
    return default if value is None else value


def build_deobfuscation_map(font_bytes: bytes) -> dict[str, str]:
    """Map each Private-Use codepoint in the font to its real character."""
    font = TTFont(io.BytesIO(font_bytes))
    mapping: dict[str, str] = {}
    for codepoint, glyph_name in font.getBestCmap().items():
        real = toUnicode(glyph_name)
        if real:
            mapping[chr(codepoint)] = real
    return mapping


def deobfuscate(text: str, mapping: dict[str, str]) -> str:
    return "".join(mapping.get(ch, ch) for ch in text)


def fetch_widget(
    key: str, session: requests.Session, attempts: int = 4
) -> tuple[dict, list[str]]:
    """Return (pageProps, callers-that-worked) for the first usable widget.

    The backend occasionally serves a payload without the match list; retry
    a few times before giving up.
    """
    errors: list[str] = []
    for attempt in range(attempts):
        for caller in DEFAULT_CALLERS:
            host = caller.rstrip("/") + "/"
            headers = {
                "User-Agent": USER_AGENT,
                "Accept-Language": "de-DE,de;q=0.9",
                "Referer": host,
            }
            for wtype in WIDGET_TYPES:
                url = f"{NEXT_BASE}/widget/{wtype}/{key}"
                try:
                    resp = session.get(url, headers=headers, timeout=30)
                except requests.RequestException as exc:
                    errors.append(f"{url} ({host}): {exc}")
                    continue
                match = NEXT_DATA_RE.search(resp.text)
                if not match:
                    errors.append(
                        f"{url} ({host}): no __NEXT_DATA__ (HTTP {resp.status_code})"
                    )
                    continue
                page = json.loads(match.group(1))["props"]["pageProps"]
                if page.get("invalidReferrer"):
                    errors.append(f"{url} ({host}): invalidReferrer")
                    continue
                if page.get("previousMatches") or page.get("nextMatches"):
                    return page, [host]
                errors.append(f"{url} ({host}): no match list in payload")
        if attempt + 1 < attempts:
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(
        "Could not fetch widget data. Is the caller domain the website "
        "registered for the widget in the fussball.de Widgetcenter?\n  "
        + "\n  ".join(errors[-8:])
    )


def fetch_font(font_id: str, referer: str, session: requests.Session) -> bytes:
    for fmt in ("woff", "ttf"):
        url = f"{FONT_BASE}/export.fontface/-/format/{fmt}/id/{font_id}/type/font"
        try:
            resp = session.get(
                url,
                headers={"User-Agent": USER_AGENT, "Referer": referer},
                timeout=30,
            )
        except requests.RequestException:
            continue
        if resp.ok and resp.content:
            try:
                TTFont(io.BytesIO(resp.content))  # validate
                return resp.content
            except Exception:
                continue
    raise RuntimeError(f"Could not download obfuscation font {font_id}")


def fetch_venue(match_id: str, session: requests.Session) -> str | None:
    """Read the venue (Spielstätte incl. address) from the match page."""
    if not match_id:
        return None
    url = f"{FONT_BASE}/spiel/-/spiel/{match_id}"
    try:
        resp = session.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    except requests.RequestException:
        return None
    if not resp.ok:
        return None
    match = VENUE_RE.search(resp.text)
    if not match:
        return None
    venue = _strip(html.unescape(match.group(1)))
    return venue or None


def _team_name(team: dict | None, mapping: dict[str, str]) -> str:
    if not team:
        return ""
    return _strip(deobfuscate(_get(team, "name", ""), mapping))


def parse_matches(page: dict, mapping: dict[str, str]) -> list[Fixture]:
    fixtures: list[Fixture] = []
    raw = (page.get("previousMatches") or []) + (page.get("nextMatches") or [])
    for m in raw:
        if not m:
            continue
        kickoff = _get(m, "kickoff", {})
        date_text = _strip(deobfuscate(_get(kickoff, "date", ""), mapping))
        weekday_text = _strip(
            deobfuscate(_get(kickoff, "dateWithWeekday", ""), mapping)
        )
        time_text = _strip(deobfuscate(_get(kickoff, "time", ""), mapping))

        iso_date = None
        dm = DATE_RE.search(date_text) or DATE_RE.search(weekday_text)
        if dm:
            day, month, year = dm.groups()
            iso_date = f"{year}-{int(month):02d}-{int(day):02d}"

        iso_time = None
        tm = TIME_RE.search(time_text)
        if tm:
            iso_time = f"{int(tm.group(1)):02d}:{tm.group(2)}"

        weekday = None
        if "," in weekday_text:
            weekday = weekday_text.split(",", 1)[0].strip()

        result = _get(m, "result", {})
        score = _strip(deobfuscate(_get(result, "text", ""), mapping)).replace(" ", "")
        score = score or None

        fixtures.append(
            Fixture(
                date=iso_date,
                time=iso_time,
                weekday=weekday,
                competition=_strip(
                    deobfuscate(_get(m, "competitionName", ""), mapping)
                )
                or None,
                home=_team_name(_get(m, "homeTeam", None), mapping) or None,
                away=_team_name(_get(m, "guestTeam", None), mapping) or None,
                score=score,
                status=m.get("status"),
                match_id=_get(m, "id", ""),
            )
        )
    fixtures.sort(key=lambda f: (f.date or "9999", f.time or ""))
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
            ["date", "time", "weekday", "competition", "home", "away",
             "score", "venue", "status"]
        )
        for f in fixtures:
            writer.writerow(
                [f.date, f.time, f.weekday, f.competition, f.home, f.away,
                 f.score, f.venue, f.status]
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
    skipped = 0
    open_opponent = 0
    for f in fixtures:
        # Without a date there is nothing to put in a calendar. A missing
        # opponent is fine: the date is already worth blocking off, and the
        # event updates in place once the pairing is drawn (same UID).
        if not f.date:
            skipped += 1
            continue
        time_part = f.time or "00:00"
        start = datetime.fromisoformat(f"{f.date}T{time_part}").replace(tzinfo=TZ)
        end = start + timedelta(hours=2)
        home = f.home or OPEN_OPPONENT
        away = f.away or OPEN_OPPONENT
        if not (f.home and f.away):
            open_opponent += 1
        summary = f"{home} vs. {away}"
        if f.score and f.score not in ("-:-", ":"):
            summary += f" ({f.score})"
        # Prefer the stable match id so subscribed clients update events in
        # place; fall back to a content hash when it is missing.
        uid = f.match_id or hashlib.sha1(
            f"{f.date}|{home}|{away}".encode()
        ).hexdigest()[:16]
        lines += [
            "BEGIN:VEVENT",
            f"UID:{uid}@fussball-de-fixtures-exporter",
            f"DTSTAMP:{stamp}",
            f"DTSTART;TZID=Europe/Berlin:{start.strftime('%Y%m%dT%H%M%S')}",
            f"DTEND;TZID=Europe/Berlin:{end.strftime('%Y%m%dT%H%M%S')}",
            f"SUMMARY:{_ics_escape(summary)}",
        ]
        if f.venue:
            lines.append(f"LOCATION:{_ics_escape(f.venue)}")
        # ICS folds a multi-line value onto one line with a literal \n.
        description = [_ics_escape(f.competition)] if f.competition else []
        if not (f.home and f.away):
            description.append("Gegner steht noch nicht fest.")
        if description:
            lines.append("DESCRIPTION:" + "\\n".join(description))
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    path.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")
    if open_opponent:
        print(
            f"Note: {open_opponent} of {len(fixtures)} matches have no "
            f"opponent yet and show as \"{OPEN_OPPONENT}\" in the calendar.",
            file=sys.stderr,
        )
    if skipped:
        print(
            f"Note: {skipped} of {len(fixtures)} matches have no date yet "
            "and are not in the calendar.",
            file=sys.stderr,
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--key", default=DEFAULT_KEY, help="fussball.de widget key")
    parser.add_argument("--out-dir", default="out", type=Path)
    parser.add_argument(
        "--cal-name",
        default="Fortuna Pankow C-Junioren – Spielplan",
        help="calendar name shown in subscribed clients (X-WR-CALNAME)",
    )
    parser.add_argument(
        "--caller",
        action="append",
        dest="callers",
        help=(
            "website registered for the widget in the fussball.de "
            "Widgetcenter, sent as Referer (repeatable)"
        ),
    )
    parser.add_argument(
        "--no-venues",
        action="store_true",
        help="skip fetching the venue/address for each match (faster)",
    )
    args = parser.parse_args(argv)

    if args.callers:
        DEFAULT_CALLERS[:] = [
            c if c.startswith("http") else f"https://{c}/" for c in args.callers
        ]

    args.out_dir.mkdir(parents=True, exist_ok=True)
    session = requests.Session()

    print(f"Fetching widget {args.key} ...", file=sys.stderr)
    page, callers = fetch_widget(args.key, session)

    team = _strip(_get(page, "teamName", "")) or "?"
    print(f"Team: {team}", file=sys.stderr)

    font_id = page.get("obfuscatedFont")
    mapping: dict[str, str] = {}
    if font_id:
        font_bytes = fetch_font(font_id, callers[0], session)
        mapping = build_deobfuscation_map(font_bytes)

    fixtures = parse_matches(page, mapping)
    if not fixtures:
        print("No matches found in widget payload.", file=sys.stderr)
        return 1

    if not args.no_venues:
        print("Fetching venues ...", file=sys.stderr)
        for f in fixtures:
            f.venue = fetch_venue(f.match_id, session)

    write_json(fixtures, args.out_dir / "fixtures.json")
    write_csv(fixtures, args.out_dir / "fixtures.csv")
    write_ics(fixtures, args.out_dir / "fixtures.ics", args.cal_name)

    for f in fixtures:
        when = f"{f.date or '????-??-??'} {f.time or '--:--'}"
        line = f"{when}  {f.home or '?'} - {f.away or '?'}  {f.score or ''}".rstrip()
        if f.venue:
            line += f"  @ {f.venue}"
        print(line)
    print(f"\n{len(fixtures)} matches written to {args.out_dir}/", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
