Fixed direct mode files.

Replace only main.py if your requirements.txt is already:

python-telegram-bot[job-queue]==21.8
supabase==2.10.0
python-dotenv==1.0.1
requests==2.32.3
groq==0.13.1

Railway variables for direct mode:

VINTED_BASE_URL=https://www.vinted.pl
VINTED_LOCALE=pl
VINTED_CURRENCY=PLN
EMPTY_ALERT_THRESHOLD_CYCLES=3

Recommended:

CHECK_INTERVAL_SECONDS=300
MAX_ITEMS_PER_SEARCH=10
MIN_AI_SCORE_TO_SEND=5
ONLY_RECENT_MINUTES=15
SKIP_UNKNOWN_AGE=false
REJECT_BAD_CONDITIONS=true
REJECT_ACCESSORIES=true
