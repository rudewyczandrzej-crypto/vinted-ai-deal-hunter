# Direct mode time patch

Replace only main.py.

Fix:
- The bot no longer uses random nested Vinted timestamps as listing creation date.
- It only trusts explicit listing date/relative-age fields.
- If no reliable age exists, age becomes unknown.

Recommended Railway variables for direct mode:

SKIP_UNKNOWN_AGE=false
ONLY_RECENT_MINUTES=15
CHECK_INTERVAL_SECONDS=300
MAX_ITEMS_PER_SEARCH=10
MIN_AI_SCORE_TO_SEND=5

Why SKIP_UNKNOWN_AGE=false:
Direct Vinted API often does not provide reliable listing age in the catalog response.
If this is true, the bot may reject too many good offers.
