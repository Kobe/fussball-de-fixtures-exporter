# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Single-file Python CLI (`export_fixtures.py`) that scrapes the match schedule behind a fussball.de team widget and exports it as `fixtures.json`, `fixtures.csv` and `fixtures.ics`. A GitHub Actions workflow publishes the files daily to GitHub Pages as a subscribable calendar. Default team: Fortuna Pankow C-Junioren II.

## Commands

```bash
pip install -r requirements.txt              # requests, fonttools
python export_fixtures.py                    # default widget key → out/
python export_fixtures.py --key <KEY> --out-dir out --cal-name "..." --caller https://verein.de/
python export_fixtures.py --no-venues        # skip one venue request per match (faster)
```

There are no tests and no linter config. Verify changes by running the script and checking the console output and the files in `out/` (gitignored). Running it needs internet access to `next.fussball.de` and `www.fussball.de`.

## How It Works

1. `fetch_widget` loads `next.fussball.de` widget pages (tries `team-matches`, `club-matches`, `matches`) and extracts `__NEXT_DATA__` JSON. The backend only answers when the `Referer` matches the website registered for the widget (`DEFAULT_CALLERS`, overridable via `--caller`); it retries because payloads are sometimes empty.
2. Visible text is obfuscated with a per-request web font. `fetch_font` + `build_deobfuscation_map` read the font's `cmap` and map Private-Use codepoints to real characters via the Adobe Glyph List; `deobfuscate` applies it.
3. `parse_matches` builds `Fixture` dataclasses. `fetch_venue` scrapes the plain-text venue from the classic match page (`VENUE_RE`).
4. `write_json` / `write_csv` / `write_ics` write the outputs. ICS events are 2h long in `Europe/Berlin` with an embedded VTIMEZONE.

## Invariants

- The ICS `UID` is the fussball.de match id so subscribed calendars update events in place; don't change the UID scheme.
- The widget sends explicit JSON `null`s (e.g. undrawn cup opponents): use `_get`, not `dict.get`. Missing opponents become `?` (`OPEN_OPPONENT`); only matches without a date are skipped.
- `_strip` removes zero-width characters the widget injects into names.

## Deployment

`.github/workflows/publish-ics.yml` runs on push to `main`, daily at 05:23 UTC and manually: it runs the script, copies `pages/index.html` (German landing page with webcal/Google Calendar links) plus the three outputs into `_site/` and deploys to GitHub Pages. A `keepalive` job re-enables the workflow so GitHub doesn't disable the schedule after 60 days of inactivity.

## Conventions

- Code, comments and CLI output in English; README, landing page and calendar texts in German.
