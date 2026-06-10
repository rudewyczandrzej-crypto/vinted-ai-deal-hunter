# Vinted Direct Mode — no Apify

This version removes Apify. The bot queries Vinted directly via Python requests.

Replace:
- main.py
- requirements.txt

Remove Railway variables:
- APIFY_TOKEN
- APIFY_ACTOR_ID

Add/keep Railway variables:

TELEGRAM_BOT_TOKEN=...
SUPABASE_URL=...
SUPABASE_KEY=...
GROQ_API_KEY=...
GROQ_MODEL=llama-3.1-8b-instant

VINTED_BASE_URL=https://www.vinted.pl
VINTED_LOCALE=pl
VINTED_CURRENCY=PLN

CHECK_INTERVAL_SECONDS=300
MAX_ITEMS_PER_SEARCH=10
MIN_AI_SCORE_TO_SEND=5
ONLY_RECENT_MINUTES=15
SKIP_UNKNOWN_AGE=false
REJECT_BAD_CONDITIONS=true
REJECT_ACCESSORIES=true
EMPTY_ALERT_THRESHOLD_CYCLES=3

Important:
- SKIP_UNKNOWN_AGE=false is recommended for direct mode at first, because direct API may not always include a reliable listing age.
- If old listings appear, set it back to true.
- The bot will send Telegram health alerts if Vinted returns 403/captcha/API changed errors.
- If Vinted blocks direct requests, we may need to adjust headers/cookies or slow the interval.

Recommended searches:
/add ipad до 1100
/add apple watch se до 500
/add redmi pad pro до 850

Test:
/debug ipad
/check
