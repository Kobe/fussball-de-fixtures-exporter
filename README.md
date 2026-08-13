# fussball-de-fixtures-exporter

Exportiert den Spielplan eines Teams aus einem fussball.de-Widget als
JSON, CSV und ICS (Kalender).

Standardmäßig ist der Widget-Key der **C2-Junioren von Fortuna Pankow**
hinterlegt (`1e84054a-be7f-4225-ae66-73bcc81e1528`).

## Installation

```bash
pip install -r requirements.txt
```

## Verwendung

```bash
# Spiele der C2-Junioren von Fortuna Pankow abrufen
python export_fixtures.py

# Anderes Team / anderer Widget-Key
python export_fixtures.py --key <WIDGET-KEY> --out-dir out
```

Die Ergebnisse landen im Ausgabeverzeichnis (`out/` per Default):

| Datei           | Inhalt                                        |
| --------------- | --------------------------------------------- |
| `fixtures.json` | Alle Spiele als strukturiertes JSON           |
| `fixtures.csv`  | Alle Spiele als CSV (Datum, Zeit, Teams, ...) |
| `fixtures.ics`  | Kalender-Datei zum Import (2h pro Spiel)      |
| `widget.html`   | Rohes Widget-HTML (zum Debuggen)              |

Zusätzlich wird der Spielplan auf der Konsole ausgegeben.

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
*Settings → Pages* als Source **GitHub Actions** auswählen. Danach den
Workflow einmal manuell starten (*Actions → Publish ICS to GitHub
Pages → Run workflow*) oder auf den nächsten automatischen Lauf warten.

## Hinweise

- Zukünftige Spiele zeigen als Ergebnis `-:-`.
- fussball.de verschleiert Endergebnisse teilweise über einen
  verwürfelten Webfont; Datum, Anstoßzeit und Teamnamen sind davon nicht
  betroffen und immer nutzbar.
- Der Abruf benötigt direkten Internetzugriff auf `www.fussball.de`.
