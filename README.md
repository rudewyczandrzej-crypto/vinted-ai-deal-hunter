# Vinted AI Deal Hunter

Telegram bot that monitors Vinted offers through Apify, evaluates them with AI, stores searches in Supabase, and sends good deals to Telegram.

## What it does

- `/start` - start bot
- `/add ipad до 1200` - add Vinted search
- `/add iphone 13 | 1000` - add search with max price
- `/list` - list active searches
- `/delete ID` - disable search
- `/check` - manual check
- `/debug ipad` - test Apify response

## 1. Create Telegram bot

1. Open Telegram.
2. Find `@BotFather`.
3. Send `/newbot`.
4. Copy your bot token.

## 2. Create Supabase database

1. Create project on Supabase.
2. Open `SQL Editor`.
3. Paste and run `supabase.sql` from this repo.
4. Copy:
   - `SUPABASE_URL`
   - `SUPABASE_KEY`

You can use anon key for a simple private bot. For production, use proper RLS policies.

## 3. Create Apify account

1. Create account on Apify.
2. Get `APIFY_TOKEN`.
3. Default actor:

```env
APIFY_ACTOR_ID=automation-lab/vinted-scraper
APIFY_INPUT_MODE=automation_lab
```

If that actor does not work with your account or has another input schema, try:

```env
APIFY_ACTOR_ID=kops-inc/vinted-product-search
APIFY_INPUT_MODE=kops
```

## 4. Create OpenAI API key

Create an API key and set:

```env
OPENAI_API_KEY=your_key
OPENAI_MODEL=gpt-4o-mini
```

## 5. Deploy on Railway

1. Create new Railway project.
2. Connect this GitHub repository.
3. In Railway service, open `Variables`.
4. Add variables:

```env
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
SUPABASE_URL=your_supabase_project_url
SUPABASE_KEY=your_supabase_key
APIFY_TOKEN=your_apify_token
APIFY_ACTOR_ID=automation-lab/vinted-scraper
APIFY_INPUT_MODE=automation_lab
OPENAI_API_KEY=your_openai_api_key
OPENAI_MODEL=gpt-4o-mini
CHECK_INTERVAL_SECONDS=300
MAX_ITEMS_PER_SEARCH=10
MIN_AI_SCORE_TO_SEND=6
```

Railway will use the `Procfile`:

```txt
worker: python main.py
```

## 6. First test

In Telegram:

```text
/start
/add ipad до 1200
/debug ipad
/check
```

## Important

This bot uses Apify as a data provider layer instead of directly scraping Vinted from your own IP. Do not enter your Vinted login or card data into this bot.

If `/debug ipad` returns an Apify error, the likely reason is that the selected Apify actor uses a different input schema. In that case, change only `build_apify_input()` in `main.py`, or switch `APIFY_ACTOR_ID` and `APIFY_INPUT_MODE`.
