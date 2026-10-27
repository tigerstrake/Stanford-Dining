# Stanford Dining Recommender

Fetches Stanford R&DE dining hall menus three times daily, scores them against your food preferences, and sends a recommendation to Telegram.

## How It Works

1. **Scraper** GETs `https://rdeapps.stanford.edu/dininghallmenu/Menu.aspx`, extracts ASP.NET form state, and POSTs for each dining hall to retrieve that meal's menu.
2. **Scorer** applies keyword-based rules loaded from `config/preferences.yml` to rank halls by whole foods, protein quality, vegetables, and legumes, while penalizing hard-avoid foods.
3. **Recommender** sends the scored menus to OpenAI GPT-4o-mini for a personalized recommendation (falls back to the deterministic score if the API call fails).
4. **Hours** fetches today's opening hours for every hall from the R&DE [Dining Locations & Hours](https://rde.stanford.edu/dining-hospitality/dining-locations-hours) page.
5. **Notifier** sends the result to Telegram (Bot API, HTML formatting) and always prints it to stdout. Each message shows the recommended meal's time window next to the best and backup hall, plus both halls' full hours for the day.

## Quick Start

### Prerequisites

- Python 3.12
- An OpenAI API key (optional — use `--no-ai` to skip)
- A Telegram bot token and your chat id (optional — recommendation prints to stdout without them; see [Telegram setup](#telegram-setup))

### Setup

```bash
git clone <this-repo>
cd stanford-dining-recommender
python3 -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env and fill in OPENAI_API_KEY, TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID
```

Load your environment variables:

```bash
export $(grep -v '^#' .env | xargs)
```

Or source a shell file that exports them.

### Run

```bash
# Detect the right meal automatically from current LA time
python -m src.main

# Fetch lunch for today (explicit)
python -m src.main --meal lunch --date today

# Fetch lunch for a specific date
python -m src.main --meal lunch --date 2026-05-12

# Skip AI, use scoring only
python -m src.main --meal dinner --no-ai

# Don't send Telegram notification (dry run)
python -m src.main --dry-run

# Combine flags
python -m src.main --meal auto --dry-run --no-ai --verbose
```

### CLI Reference

| Flag | Default | Description |
|------|---------|-------------|
| `--meal` | `auto` | `auto`, `breakfast`, `lunch`, `brunch`, `dinner`. `auto` picks by LA time; on weekends it uses `lunch` because the menu site files the brunch/lunch sitting under Lunch (its Brunch option is always empty). If the picked meal has no menu, `auto` tries the nearest alternative before giving up. |
| `--date YYYY-MM-DD\|today` | today (LA time) | Override the date; `today` resolves to current LA date |
| `--no-ai` | off | Skip OpenAI, use scoring-only recommendation |
| `--dry-run` | off | Run everything but skip Telegram notification |
| `--verbose` | off | Debug logging |
| `--data-dir` | `data` | Root directory for JSON output |

### Output

- Raw menu: `data/menus/YYYY-MM-DD/meal.json`
- Recommendation: `data/recommendations/YYYY-MM-DD/meal.json`
- Dining hours: `data/hours/YYYY-MM-DD.json`

## Telegram setup

1. In Telegram, message **@BotFather**, send `/newbot`, and follow the prompts.
   It replies with a token like `123456789:AAF...` — that is `TELEGRAM_BOT_TOKEN`.
2. Open a chat with your new bot and send it any message (for example `/start`).
   A bot cannot message you until you have messaged it first.
3. Find your chat id:

   ```bash
   curl -s "https://api.telegram.org/bot<TOKEN>/getUpdates" | python3 -m json.tool | grep -A2 '"chat"'
   ```

   The `"id"` under `"chat"` is `TELEGRAM_CHAT_ID`. For a group, add the bot to the
   group, send a message there, and use the group's id (it is negative).
4. Put both values in `.env` locally and in the GitHub Actions secrets (below).

`TELEGRAM_CHAT_ID` accepts a comma-separated list to send to several chats.
Messages use Telegram's HTML `parse_mode`; scraped dish names are HTML-escaped
before sending, and messages are cut at Telegram's 4096-character limit.

## Tests

```bash
python -m pytest tests/ -v
```

Tests cover:
- Scoring rules (positive/negative keywords)
- HTML parsing from a saved fixture
- Deterministic recommendation logic
- Edge cases (empty menus, malformed HTML)

## Running on a server (systemd)

Scheduled runs happen on a home Ubuntu server (the doorboard NUC) under a
systemd **user** timer. Each message goes out 30 minutes before the earliest
hall opens for that meal, in `America/Los_Angeles` with DST handled by systemd:

| | Breakfast | Lunch | Dinner |
|---|---|---|---|
| Mon–Fri | 7:00 AM | 10:30 AM | 4:30 PM |
| Sat–Sun | — | 9:00 AM (brunch/lunch) | 4:30 PM |

The times follow the current quarter's dining hours (see the comments in
`deploy/systemd/stanford-dining.timer`). When the hours page changes, the next
scheduled run sends a "Dining hours changed" message with the new openings and
the send times they imply (`src/hours_watch.py`). It doesn't edit the timer
itself.

**Pushing to `main` deploys.** Before each scheduled run, `deploy/update.sh`
pulls `main` and installs the pinned requirements. If anything changed, it also
copies updated units into `~/.config/systemd/user/` and restarts the bot.
If a run exits non-zero, `stanford-dining-failed.service` sends a short
"run failed" message to the same Telegram chat.

| File | Purpose |
|------|---------|
| `deploy/systemd/stanford-dining.service` | One run: `update.sh`, then `python -m src.main --meal auto` |
| `deploy/systemd/stanford-dining.timer` | The schedule |
| `deploy/systemd/stanford-dining-failed.service` | `OnFailure=` hook: `notify-failure.sh` plus a health-check `fail` ping |
| `deploy/systemd/stanford-dining-bot.service` | The always-on Telegram bot (`python -m src.bot`) |
| `deploy/update.sh` | Pull, install, sync units, restart the bot |
| `deploy/hc-ping.sh` | Optional dead-man's-switch pings (see [Health check](#health-check-optional)) |

The units expect the checkout at `~/dev/Stanford-Dining` with a `.venv` and a
`.env` (same keys as `.env.example`; systemd reads it via `EnvironmentFile=`).

```bash
git clone https://github.com/tigerstrake/Stanford-Dining.git ~/dev/Stanford-Dining
cd ~/dev/Stanford-Dining
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env && chmod 600 .env    # then fill it in
mkdir -p ~/.config/systemd/user && cp deploy/systemd/* ~/.config/systemd/user/
sudo loginctl enable-linger "$USER"       # run user units without a login session
systemctl --user daemon-reload
systemctl --user enable --now stanford-dining.timer stanford-dining-bot.service
```

Day-to-day:

```bash
systemctl --user list-timers stanford-dining.timer   # next run
systemctl --user start stanford-dining.service       # run now (sends to Telegram)
journalctl --user -u stanford-dining -n 50           # logs of recent runs
journalctl --user -u stanford-dining-bot -f          # the bot, live
```

### Talking to the bot

The bot answers messages from the chats in `TELEGRAM_CHAT_ID` and ignores
everyone else. It accepts commands or plain text:

| Ask | Get |
|-----|-----|
| `/now`, "I'm hungry" | A pick for the next meal (after 9 PM, tomorrow's first) |
| `/breakfast` `/lunch` `/dinner`, "dinner tomorrow" | That meal, today or tomorrow |
| `/tomorrow` | Tomorrow's first meal |
| `/hall flomo`, "what's at arrillaga for dinner" | One hall's whole menu, each dish marked ✅/⛔ |
| `/hours`, "when does wilbur open" | Every hall's hours |
| `/stats` | Your 👍/👎 per hall |

A meal that was already sent is answered from `data/`, with no scrape and no
OpenAI call. Anything new costs one scrape and, for picks, one OpenAI call.

### 👍/👎 feedback

Every pick has 👍/👎 buttons. Votes go to `data/feedback.jsonl` (the last
vote per meal wins) and feed back in three ways:

- each net 👍 or 👎 on a hall's recent picks moves that hall's score by 1.5
  points, capped at ±4.5 over the last 30 days;
- a hall that was already the best pick for the same meal on each of the last
  2 days loses 3 points, so close calls rotate;
- the AI sees your recent votes with the plates they were about.

All of these numbers are under `adjustments:` in `config/preferences.yml`.

### Health check (optional)

The failure alert only fires when a run starts and crashes. If the NUC is off
or offline, nothing runs and nothing alerts. A free
[healthchecks.io](https://healthchecks.io) check closes that gap:

1. Create a check with **Schedule: Simple, Period 18 hours, Grace 1 hour**.
   The longest gap between runs is about 16.5 hours (Friday 4:30 PM to
   Saturday 9:00 AM).
2. Under the check's **Integrations**, add Telegram or email so it can reach you.
3. Put the check's ping URL in the server's `.env` as `HC_PING_URL=...`.

Each run then pings `/start` and success (or `/fail`), and healthchecks.io
alerts you if the pings stop.

## GitHub Actions

The workflow is **manual only** since 2026-10-27; the schedule moved to the
server (see above). GitHub's cron was a poor fit for two reasons:

- It disables the `schedule` trigger in a repo with no commits for 60 days.
  That silently stopped the bot from 2026-07-11 to 2026-09-25, and it needed a
  `gh workflow enable` keepalive step afterwards.
- Runs started hours late under load. The 8 AM run was firing at about 1:50 PM.

### GitHub Secrets Required

Add these in **Settings → Secrets and variables → Actions**:

| Secret | Description |
|--------|-------------|
| `OPENAI_API_KEY` | Your OpenAI API key |
| `TELEGRAM_BOT_TOKEN` | Bot token from @BotFather |
| `TELEGRAM_CHAT_ID` | Chat id to send to (comma-separate several) |

All are optional. Without `OPENAI_API_KEY` the script falls back to deterministic scoring. Without both Telegram secrets the recommendation prints to the workflow log only.

### Manual Trigger

Go to **Actions → Stanford Dining Recommender → Run workflow** and optionally specify a meal, date, or `no_ai=true`.

## Failure Handling

| Failure | Behavior |
|---------|----------|
| No dining halls found on page | Hard exit (site structure may have changed) |
| Today's date not in dropdown | Hard exit with clear error message |
| All halls empty for the meal (not posted yet, holiday, break) | Warning, no message sent, exit 0 — scheduled runs do not fail |
| Fewer than 3 halls have menu items | Warning in output; `reliable: false` in JSON |
| OpenAI API fails | Falls back to deterministic scoring |
| Telegram send fails | Recommendation printed to stdout |
| Hours page unreachable or changed | Message is sent without the hours section (warning in log) |

## Dietary Preferences

All preferences live in **`config/preferences.yml`** — the single source of truth for both the deterministic scorer and the AI prompt. Edit that file to change what gets recommended or penalized; no code changes needed.

### Editing preferences

Open `config/preferences.yml`. It has three sections:

- **`description`** — free-text summary sent to the AI as its framing.
- **`notes`** — bullet-point rules also sent to the AI (e.g. "no fish", "no processed meat").
- **`scoring`** — keyword rules used by the deterministic scorer:
  - `bonuses` — categories with positive scores awarded when a keyword matches the item name or ingredients.
  - `penalties` — categories with negative scores.

Each rule looks like:

```yaml
- category: legumes
  score: 3.5
  description: Beans, lentils, chickpeas, and other legumes
  keywords:
    - lentil
    - chickpea
    - black bean
```

**To add a food:** append its keyword to the relevant category's `keywords` list.  
**To change the weight:** edit the `score` value.  
**To add a new category:** add a new entry under `bonuses` or `penalties`.

### Current scoring summary

Items are matched against both the item name and ingredient text (case-insensitive, plural-tolerant).

**Bonuses (per matching item):**
| Category | Score | Examples |
|----------|-------|---------|
| `minimally_processed_meat` | +4.0 | grilled chicken, turkey breast, lamb, sirloin |
| `legumes` | +3.5 | lentils, chickpeas, black beans, edamame |
| `vegetarian_protein` | +3.0 | tofu, tempeh, paneer, cottage cheese |
| `vegetables` | +2.5 | broccoli, spinach, kale, zucchini |
| `whole_grains` | +2.0 | brown rice, quinoa, farro, barley |
| `fruit` | +1.5 | berries, apple, mango, avocado |
| `nuts_seeds` | +1.5 | almonds, walnuts, tahini |
| `yogurt` | +1.5 | greek yogurt, kefir |
| `healthy_fats` | +1.0 | olive oil, avocado oil |
| `healthy_soup` | +0.5 | lentil soup, minestrone |

**Penalties (per matching item):**
| Category | Score | Examples |
|----------|-------|---------|
| `fish_seafood` *(hard avoid)* | −6.0 | salmon, tuna, shrimp, seafood |
| `ultra_processed_meat` *(hard avoid)* | −6.0 | hot dog, sausage, bacon, pepperoni |
| `breaded_processed_meat` *(hard avoid)* | −5.0 | chicken nugget, chicken tender, popcorn chicken |
| `pure_egg_dishes` *(hard avoid)* | −4.5 | scrambled eggs, omelette, frittata |
| `fried_foods` | −4.0 | fried chicken, french fries, donuts |
| `sugary_desserts` | −3.5 | cake, cookies, ice cream, pastry |
| `low_quality_food` | −3.0 | mac and cheese, alfredo, nachos |
| `refined_carbs` | −2.0 | white pasta, white rice, bagel |

## Project Structure

```
stanford-dining-recommender/
├── config/
│   └── preferences.yml   # Dietary preferences (scorer + AI prompt source of truth)
├── src/
│   ├── models.py         # Pydantic data models
│   ├── preferences.py    # YAML config loader
│   ├── scraper.py        # Stanford menu scraper
│   ├── scorer.py         # Deterministic keyword scoring (reads preferences.yml)
│   ├── recommender.py    # OpenAI integration + fallback (reads preferences.yml)
│   ├── hours.py          # Dining hall opening hours scraper (rde.stanford.edu)
│   ├── notifier.py       # Telegram sending, 👍/👎 buttons
│   ├── storage.py        # data/ paths and cached reads
│   ├── feedback.py       # Votes and the score nudges they cause
│   ├── hours_watch.py    # "Dining hours changed" alert
│   ├── bot.py            # Always-on Telegram bot
│   └── main.py           # CLI entry point (one scheduled run)
├── tests/
│   ├── fixtures/         # Saved HTML for offline testing
│   ├── test_hours.py
│   ├── test_notifier.py
│   ├── test_scorer.py
│   └── test_scraper.py
├── data/                 # Output JSON (gitignored)
├── .github/workflows/
│   ├── dining-recommender.yml   # Manual runs
│   └── tests.yml                # pytest on every push
├── requirements.in       # Direct dependencies (edit this)
├── requirements.txt      # Pinned lock compiled from requirements.in
├── .env.example
└── README.md
```

## Adding Email Notifications

Open `src/notifier.py` and add a function alongside `send_telegram`:

```python
def send_email(rec: Recommendation, smtp_host: str, ...) -> bool:
    ...
```

Then call it in `notify()`. The plain-text format from `format_recommendation_plain()` works well for email bodies.
