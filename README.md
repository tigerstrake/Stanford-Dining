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
| `--meal` | `auto` | `auto`, `breakfast`, `lunch`, `brunch`, `dinner` |
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

## GitHub Actions

The workflow runs automatically:

| GitHub Actions time (UTC) | LA time (PST) | LA time (PDT) |
|---------------------------|---------------|---------------|
| `0 16 * * *`              | 8:00 AM ✓     | 9:00 AM ✓ (still breakfast) |
| `0 19 * * *`              | 11:00 AM ✓    | 12:00 PM ✓ (still lunch) |
| `0 0 * * *`               | 4:00 PM ✓     | 5:00 PM ✓ (still dinner) |

GitHub Actions cron uses UTC only. The schedule above fires at the correct LA time during standard time (PST, November–March). During daylight saving time (PDT, March–November), runs fire 1 hour later but remain within the correct meal window. The `--meal auto` flag uses the actual LA clock to pick the right meal regardless of when the cron fires.

### Why it stopped in July 2026, and the keepalive

GitHub automatically disables the `schedule` trigger in any repository that has
had no commits for 60 days. This repo's last commit was 2026-05-11, and the last
scheduled run was 2026-07-11 — exactly 60 days later. No code had broken.

The workflow now ends every scheduled run with `gh workflow enable`, which
resets GitHub's inactivity timer without a dummy commit (this is why the job
declares `permissions: actions: write`). If the workflow is ever disabled again
(Actions tab shows a yellow banner), re-enable it once by hand:

```bash
gh workflow enable dining-recommender.yml -R tigerstrake/Stanford-Dining
```

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
│   ├── notifier.py       # Telegram + stdout notifications
│   └── main.py           # CLI entry point
├── tests/
│   ├── fixtures/         # Saved HTML for offline testing
│   ├── test_hours.py
│   ├── test_notifier.py
│   ├── test_scorer.py
│   └── test_scraper.py
├── data/                 # Output JSON (gitignored)
├── .github/workflows/
│   └── dining-recommender.yml
├── requirements.txt
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
