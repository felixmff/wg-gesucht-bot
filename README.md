# WG-Gesucht Bot

Bot, der WG-Gesucht durchsucht und passende Inserate automatisch anschreibt. Läuft als dauerhafter Prozess mit Playwright-Browser-Session, SQLite-Tracking und optionaler KI für Codewörter in Anzeigen.

## Features

- Pollt eine konfigurierte Such-URL in regelmäßigen Abständen
- Schreibt nur Inserate an, die noch nicht kontaktiert wurden
- Wiederholt fehlgeschlagene Versuche automatisch
- Erkennt Codewörter, Emojis und Pflicht-Betreffzeilen per DeepSeek
- Eine Browser-Session für Login, Suche und Nachrichtenversand
- Zufällige Pausen zwischen Nachrichten und Polls

## Voraussetzungen

- Python 3.10+
- [uv](https://docs.astral.sh/uv/)
- Chromium (via Playwright)

## Installation

```bash
git clone https://github.com/felixmff/wg-gesucht-bot.git
cd wg-gesucht-bot

uv sync
uv run playwright install chromium
```

Auf Linux/Raspberry Pi zusätzlich:

```bash
sudo uv run playwright install-deps chromium
```

## Konfiguration

### `.env` (Credentials & KI)

Datei `.env` im Projektroot anlegen:

```env
WG_GESUCHT_EMAIL=deine@email.de
WG_GESUCHT_PASSWORD=dein-passwort
DEEPSEEK_API_KEY=sk-...
DEEPSEEK_MODEL=deepseek-chat
DEEPSEEK_BASE_URL=https://api.deepseek.com
```

`DEEPSEEK_API_KEY` ist optional. Ohne Key wird die Nachricht ohne Codewort-Erkennung versendet.

### `config.yaml`

```yaml
message_file: "text.txt"
url: "https://www.wg-gesucht.de/wg-zimmer-in-Innsbruck....html?...&dFr=...&dTo=..."
run_headless: true
poll_interval_seconds: 300
poll_interval_jitter_seconds: 30
message_delay_min_seconds: 45
message_delay_max_seconds: 120
```

| Option | Beschreibung |
|--------|--------------|
| `message_file` | Pfad zur Bewerbungsnachricht |
| `url` | Such-URL von WG-Gesucht mit allen Filtern (`dFr` / `dTo` für Mietbeginn) |
| `run_headless` | `true` auf Server/Pi, `false` zum Debuggen lokal |
| `poll_interval_seconds` | Sekunden zwischen Polls |
| `poll_interval_jitter_seconds` | Zufälliger Zusatz (0–N s) pro Poll |
| `message_delay_min_seconds` | Mindestpause zwischen zwei Nachrichten |
| `message_delay_max_seconds` | Maximalpause zwischen zwei Nachrichten |
| `db_path` | Optional, Standard: `bot.db` |
| `attachment_file` | Optional, PDF/Datei als Anhang |
| `rental_start` | Optional, zusätzlicher Datumsfilter im Bot (meist über URL `dFr`/`dTo` abgedeckt) |

**Tipp:** Filter (Stadt, Preis, Mietbeginn) direkt in der WG-Gesucht-URL setzen — der Bot übernimmt die Ergebnisseite.

### `text.txt` (Nachrichtenvorlage)

```text
---variables---
# {{codeword_line}} — optional, wird automatisch vor die Nachricht gesetzt
---message---
Hallo zusammen,
...
```

Codewörter aus der Anzeige werden von der KI erkannt und als erste Zeile eingefügt.

## Nutzung

```bash
# Bot starten (Endlosschleife)
uv run wg-gesucht.py

# Testlauf ohne Senden und ohne DB-Änderungen
uv run wg-gesucht.py --dry-run
```

Stoppen mit `Ctrl+C` — der aktuelle Durchlauf wird noch beendet.

## Datenbank (`bot.db`)

| Tabelle | Inhalt |
|---------|--------|
| `seen_listings` | Alle Inserate, die auf der Suchseite gesehen wurden |
| `contacted_listings` | Erfolgreich angeschriebene Inserate (Name + Adresse) |
| `failed_listings` | Fehlgeschlagene Sendeversuche inkl. Fehlergrund |

Abfragen:

```bash
sqlite3 bot.db "SELECT * FROM contacted_listings;"
sqlite3 bot.db "SELECT * FROM failed_listings;"
```

## Deployment auf Raspberry Pi (pm2)

```bash
# Auf dem Pi
cd ~/wg-gesucht-bot
uv sync
uv run playwright install chromium
sudo uv run playwright install-deps chromium

# .env, config.yaml, text.txt und bot.db manuell kopieren (nicht im Repo)

npx pm2 start ~/.local/bin/uv --name wg-gesucht-bot \
  --cwd ~/wg-gesucht-bot -- run wg-gesucht.py

npx pm2 save
sudo env PATH=$PATH pm2 startup systemd -u pi --hp /home/pi
```

Logs:

```bash
npx pm2 logs wg-gesucht-bot
npx pm2 restart wg-gesucht-bot
```

**Wichtig:** Bot nur auf **einer** Maschine laufen lassen (Mac oder Pi), sonst kollidieren `bot.db` und WG-Gesucht-Session.

## Projektstruktur

```
wg-gesucht.py          # Hauptschleife, Filter, Delays
src/submit_wg.py       # Playwright: Login, Sicherheitsmodal, Senden
src/listing_getter.py  # Suchseite parsen
src/listing_info_getter.py  # Inserattext für KI
src/message_generator.py    # DeepSeek Codewort-Erkennung
src/message_template.py     # text.txt laden
src/db.py              # SQLite
config.yaml            # Lokal (gitignored)
.env                   # Credentials (gitignored)
text.txt               # Nachricht (gitignored)
bot.db                 # State (gitignored)
```

## Hinweise

- Der Bot klickt das WG-Gesucht-Sicherheitsmodal („Ich habe die Sicherheitstipps gelesen“) automatisch weg.
- Verifizierte Unternehmen werden übersprungen.
- Bereits gesendete Nachrichten auf WG-Gesucht werden erkannt und nicht erneut verschickt.
