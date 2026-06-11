# Dynamic AI Filters for Vinted Deal Hunter

This version removes product-specific hardcoded filters from the user workflow.

You write only a natural Telegram request, for example:

```text
/add ipad 10 генерації до 1200
/add apple watch se 2 до 500
/add redmi pad pro до 850
```

The bot will:

1. Ask Groq AI to generate a filter profile.
2. Store that profile in Supabase `searches.filter_json`.
3. Use `vinted_query` from the AI profile for Vinted search.
4. Use `required_groups`, `reject_any`, `wrong_product_any`, and `quality_risk_any` from the database to filter listings.
5. Then run the usual AI deal evaluation before sending Telegram alerts.

## Required Supabase migration

Run `supabase.sql` in Supabase SQL Editor. It adds:

- `searches.vinted_query`
- `searches.filter_json`
- `searches.filter_summary`
- `searches.min_ai_score`

The SQL is safe to run multiple times because it uses `add column if not exists`.

## New Telegram commands

```text
/filter ID
```

Shows the AI-generated filter stored in the database.

```text
/refreshfilter ID
```

Regenerates the filter with AI and updates Supabase.

## Example

User writes:

```text
/add ipad 10 генерації до 1200
```

AI may create and store something like:

```json
{
  "vinted_query": "ipad 10",
  "filter_summary_ua": "Шукаю iPad 10 покоління, відсікаю аксесуари, старі iPad, поламані пристрої та iCloud lock.",
  "required_groups": [["ipad", "i pad"], ["10 gen", "10 generacji", "10th", "2022", "10.9", "10,9", "A2696", "A2757", "A2777"]],
  "reject_any": ["etui", "case", "folia", "szkło", "ładowarka", "uszkodzony", "icloud", "blokada"],
  "wrong_product_any": ["ipad 9", "ipad 8", "ipad mini", "ipad pro", "ipad air"],
  "quality_risk_any": ["pęknięty", "zbity", "nie działa", "części"],
  "min_ai_score": 4
}
```

No real `.env` file is included. Do not commit API keys to GitHub.
