# fussball-de-fixtures-exporter

Exportiert den Spielplan eines Teams aus einem fussball.de-Widget als
JSON, CSV und ICS (Kalender).

Standardmäßig ist der Widget-Key der **C-Junioren II von Fortuna Pankow**
hinterlegt (`1e84054a-be7f-4225-ae66-73bcc81e1528`).

## Installation

```bash
pip install -r requirements.txt
```

## Verwendung

```bash
# Spiele der C-Junioren II von Fortuna Pankow abrufen
python export_fixtures.py

# Anderes Team / anderer Widget-Key
python export_fixtures.py --key <WIDGET-KEY> --out-dir out

# Falls das Widget für eine andere Website registriert ist
python export_fixtures.py --caller https://meinverein.de/
```

Die Ergebnisse landen im Ausgabeverzeichnis (`out/` per Default):

| Datei           | Inhalt                                        |
| --------------- | --------------------------------------------- |
| `fixtures.json` | Alle Spiele als strukturiertes JSON           |
| `fixtures.csv`  | Alle Spiele als CSV (Datum, Zeit, Teams, ...) |
| `fixtures.ics`  | Kalender-Datei (2h pro Spiel, mit Spielort)   |

Zusätzlich wird der Spielplan auf der Konsole ausgegeben.

## Wie es funktioniert

fussball.de liefert die Spiele im aktuellen Widget-Backend unter
`next.fussball.de` als JSON aus (eingebettet in `__NEXT_DATA__`). Die
sichtbaren Texte (Datum, Uhrzeit, Team- und Wettbewerbsnamen, Ergebnisse)
sind mit einem **pro Request wechselnden Webfont** verschleiert, der
zufällige Private-Use-Unicode-Zeichen auf normale Glyphen abbildet. Das
Skript lädt diesen Font, liest seine `cmap` (Private-Use-Codepoint →
Glyphenname → echtes Zeichen über die Adobe Glyph List) und macht die
Verschleierung so rückgängig.

Das Backend liefert Daten nur, wenn der Request von der Website zu kommen
scheint, die im fussball.de-Widgetcenter für das Widget hinterlegt ist –
daher wird diese Domain als `Referer` gesendet (siehe `--caller`).

## Kalender-Abo über GitHub Pages

Der Workflow [`publish-ics.yml`](.github/workflows/publish-ics.yml) ruft
den Spielplan alle sechs Stunden ab und veröffentlicht ihn über GitHub
Pages. Den Kalender kann man dann in Google Kalender, Apple Kalender
oder Outlook als Abo hinzufügen:

```
https://kobe.github.io/fussball-de-fixtures-exporter/fixtures.ics
```

Auf der Seite <https://kobe.github.io/fussball-de-fixtures-exporter/>
gibt es außerdem einen Webcal-Link sowie JSON- und CSV-Downloads.

**Einmalige Einrichtung:** In den Repo-Einstellungen unter
*Settings → Pages* als Source **GitHub Actions** auswählen. Danach läuft
der Workflow bei jedem Push auf `main`, alle sechs Stunden und manuell
(*Actions → Publish ICS to GitHub Pages → Run workflow*).

## Hinweise

- Zukünftige Spiele zeigen als Ergebnis `-:-`.
- Der **Spielort inkl. Adresse** wird pro Spiel von der klassischen
  Spielseite geholt und landet als `LOCATION` im Kalender (in Google/Apple
  Kalender direkt navigierbar) sowie als Spalte in CSV/JSON. Mit
  `--no-venues` lässt sich das abschalten (spart pro Spiel einen Abruf).
- Der Abruf benötigt Internetzugriff auf `next.fussball.de` und
  `www.fussball.de` (Font + Spielort).
