import os
import re
import json
import html
import time
import logging
import traceback
from typing import Any, Dict, List, Optional, Tuple

import requests
from dotenv import load_dotenv
from supabase import create_client, Client
from groq import Groq

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
)

load_dotenv()

# =========================
# CONFIG
# =========================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "").strip()

APIFY_TOKEN = os.getenv("APIFY_TOKEN", "").strip()
APIFY_ACTOR_ID = os.getenv("APIFY_ACTOR_ID", "automation-lab/vinted-scraper").strip()

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant").strip()

CHECK_INTERVAL_SECONDS = int(os.getenv("CHECK_INTERVAL_SECONDS", "300"))
MAX_ITEMS_PER_SEARCH = int(os.getenv("MAX_ITEMS_PER_SEARCH", "10"))
MIN_AI_SCORE_TO_SEND = int(os.getenv("MIN_AI_SCORE_TO_SEND", "6"))

DEFAULT_COUNTRY_DOMAIN = "vinted.pl"

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger("vinted-ai-deal-hunter")

if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError("Missing TELEGRAM_BOT_TOKEN")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("Missing SUPABASE_URL or SUPABASE_KEY")

if not APIFY_TOKEN:
    raise RuntimeError("Missing APIFY_TOKEN")

if not GROQ_API_KEY:
    raise RuntimeError("Missing GROQ_API_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
groq_client = Groq(api_key=GROQ_API_KEY)


# =========================
# SMALL HELPERS
# =========================

def escape(value: Any) -> str:
    return html.escape(str(value or ""), quote=False)


def normalize_price(value: Any) -> Optional[float]:
    if value is None:
        return None

    if isinstance(value, (int, float)):
        return float(value)

    text = str(value)
    text = text.replace(",", ".")
    match = re.search(r"(\d+(?:\.\d+)?)", text)
    if not match:
        return None

    try:
        return float(match.group(1))
    except ValueError:
        return None


def extract_number(text: str) -> Optional[float]:
    match = re.search(r"(\d+(?:[,.]\d+)?)", text)
    if not match:
        return None
    return float(match.group(1).replace(",", "."))


def parse_add_command(raw_text: str) -> Tuple[Optional[str], Optional[float]]:
    """
    Supported:
    /add ipad до 1200
    /add iphone 13 | 1000
    /add apple watch series 7 max 450
    /add steam deck 1500
    """
    text = raw_text.replace("/add", "", 1).strip()

    if not text:
        return None, None

    max_price = None
    keyword = text

    # format: keyword | price
    if "|" in text:
        parts = [p.strip() for p in text.split("|", 1)]
        keyword = parts[0]
        max_price = extract_number(parts[1])
        return keyword.strip(), max_price

    # formats: до 1200, max 1200, price 1200
    price_patterns = [
        r"\bдо\s+(\d+(?:[,.]\d+)?)",
        r"\bmax\s+(\d+(?:[,.]\d+)?)",
        r"\bprice\s+(\d+(?:[,.]\d+)?)",
        r"\bц[іi]на\s+(\d+(?:[,.]\d+)?)",
    ]

    for pattern in price_patterns:
        m = re.search(pattern, text, flags=re.IGNORECASE)
        if m:
            max_price = float(m.group(1).replace(",", "."))
            keyword = re.sub(pattern, "", text, flags=re.IGNORECASE).strip()
            return keyword.strip(), max_price

    # If last word is a number, treat it as max price
    parts = text.split()
    if len(parts) >= 2 and re.fullmatch(r"\d+(?:[,.]\d+)?", parts[-1]):
        max_price = float(parts[-1].replace(",", "."))
        keyword = " ".join(parts[:-1]).strip()

    return keyword.strip(), max_price


def get_first_existing(data: Dict[str, Any], keys: List[str], default: Any = None) -> Any:
    for key in keys:
        if key in data and data[key] not in [None, ""]:
            return data[key]
    return default


def normalize_item(raw: Dict[str, Any]) -> Dict[str, Any]:
    """
    Different Apify Vinted actors return slightly different field names.
    This function tries to normalize the most common variants.
    """
    title = get_first_existing(raw, ["title", "name", "itemTitle", "productTitle"], "No title")
    url = get_first_existing(raw, ["url", "itemUrl", "link", "productUrl", "item_url"], "")
    item_id = get_first_existing(raw, ["id", "itemId", "item_id", "productId"], url)

    price_raw = get_first_existing(
        raw,
        ["price", "priceAmount", "amount", "totalPrice", "total_price", "priceWithCurrency"],
        None,
    )
    price = normalize_price(price_raw)

    currency = get_first_existing(raw, ["currency", "priceCurrency"], "PLN")
    brand = get_first_existing(raw, ["brand", "brandTitle", "brand_name"], "")
    size = get_first_existing(raw, ["size", "sizeTitle"], "")
    condition = get_first_existing(raw, ["condition", "status"], "")
    description = get_first_existing(raw, ["description", "desc"], "")

    seller = get_first_existing(raw, ["seller", "sellerName", "user", "username"], "")
    location = get_first_existing(raw, ["location", "city", "country"], "")

    image = get_first_existing(raw, ["image", "imageUrl", "photo", "thumbnail", "photoUrl"], "")

    if isinstance(seller, dict):
        seller = get_first_existing(seller, ["login", "username", "name"], "")

    return {
        "id": str(item_id),
        "title": str(title),
        "url": str(url),
        "price": price,
        "currency": str(currency),
        "brand": str(brand),
        "size": str(size),
        "condition": str(condition),
        "description": str(description),
        "seller": str(seller),
        "location": str(location),
        "image": str(image),
        "raw": raw,
    }


# =========================
# DATABASE
# =========================

def ensure_user(telegram_id: str) -> None:
    existing = supabase.table("users").select("id").eq("telegram_id", telegram_id).execute()

    if existing.data:
        return

    supabase.table("users").insert({
        "telegram_id": telegram_id,
    }).execute()


def add_search(telegram_id: str, keyword: str, max_price: Optional[float]) -> Dict[str, Any]:
    result = supabase.table("searches").insert({
        "telegram_id": telegram_id,
        "keyword": keyword,
        "max_price": max_price,
        "country": "pl",
        "active": True,
    }).execute()

    return result.data[0]


def get_active_searches(telegram_id: Optional[str] = None) -> List[Dict[str, Any]]:
    query = supabase.table("searches").select("*").eq("active", True)

    if telegram_id:
        query = query.eq("telegram_id", telegram_id)

    result = query.order("created_at", desc=True).execute()
    return result.data or []


def get_search_by_id(search_id: int, telegram_id: str) -> Optional[Dict[str, Any]]:
    result = (
        supabase.table("searches")
        .select("*")
        .eq("id", search_id)
        .eq("telegram_id", telegram_id)
        .execute()
    )
    return result.data[0] if result.data else None


def deactivate_search(search_id: int, telegram_id: str) -> bool:
    existing = get_search_by_id(search_id, telegram_id)
    if not existing:
        return False

    supabase.table("searches").update({"active": False}).eq("id", search_id).eq("telegram_id", telegram_id).execute()
    return True


def was_item_sent(telegram_id: str, item_id: str) -> bool:
    result = (
        supabase.table("sent_items")
        .select("id")
        .eq("telegram_id", telegram_id)
        .eq("item_id", item_id)
        .execute()
    )
    return bool(result.data)


def mark_item_sent(telegram_id: str, search_id: int, item_id: str, url: str) -> None:
    try:
        supabase.table("sent_items").insert({
            "telegram_id": telegram_id,
            "search_id": search_id,
            "item_id": item_id,
            "url": url,
        }).execute()
    except Exception:
        # Unique constraint may fail if two checks overlap. Safe to ignore.
        logger.warning("Could not mark item as sent, probably duplicate.")


# =========================
# VINTED / APIFY
# =========================

def build_apify_input(keyword: str, max_price: Optional[float]) -> Dict[str, Any]:
    """
    Input for deltaspider/vinted-scraper.
    It uses direct Vinted search URLs, so we force Polish Vinted.
    Price is still filtered locally after Apify returns items.
    """
    from urllib.parse import quote_plus

    search_url = f"https://www.vinted.pl/catalog?search_text={quote_plus(keyword)}&order=newest_first"

    return {
        "startUrls": [
            {
                "url": search_url
            }
        ],
        "maxResultsPerUrl": MAX_ITEMS_PER_SEARCH,
        "maxRetries": 2,
        "proxyConfiguration": {
            "useApifyProxy": True
        }
    }


def fetch_vinted_items(keyword: str, max_price: Optional[float]) -> List[Dict[str, Any]]:
    actor_id_for_url = APIFY_ACTOR_ID.replace("/", "~")
    url = (
        f"https://api.apify.com/v2/acts/"
        f"{actor_id_for_url}/run-sync-get-dataset-items"
        f"?token={APIFY_TOKEN}"
    )

    payload = build_apify_input(keyword, max_price)

    logger.info("Running Apify actor %s with input: %s", APIFY_ACTOR_ID, payload)

    response = requests.post(
        url,
        json=payload,
        timeout=120,
        headers={"Content-Type": "application/json"},
    )

    if response.status_code >= 400:
        logger.error("Apify error %s: %s", response.status_code, response.text[:1000])
        raise RuntimeError(f"Apify error {response.status_code}: {response.text[:500]}")

    data = response.json()

    if not isinstance(data, list):
        logger.warning("Unexpected Apify response: %s", str(data)[:1000])
        return []

    normalized = [normalize_item(item) for item in data if isinstance(item, dict)]

    # Local price filter
    if max_price:
        normalized = [
            item for item in normalized
            if item["price"] is not None and item["price"] <= float(max_price)
        ]

    return normalized


# =========================
# AI EVALUATION - GROQ
# =========================

def safe_json_loads(text: str) -> Dict[str, Any]:
    text = text.strip()

    if text.startswith("```"):
        text = text.replace("```json", "").replace("```", "").strip()

    try:
        return json.loads(text)
    except Exception:
        # Try to extract the first JSON object from a messy model response.
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(text[start:end + 1])
        raise


def evaluate_item_with_ai(item: Dict[str, Any], search: Dict[str, Any]) -> Dict[str, Any]:
    prompt = f"""
Ти AI-агент для оцінки оголошень з Vinted у Польщі.

Задача користувача:
- шукає: {search.get("keyword")}
- максимальна ціна: {search.get("max_price")} PLN

Оголошення:
- title: {item.get("title")}
- price: {item.get("price")} {item.get("currency")}
- brand: {item.get("brand")}
- size: {item.get("size")}
- condition: {item.get("condition")}
- seller: {item.get("seller")}
- location: {item.get("location")}
- description: {item.get("description")}
- url: {item.get("url")}

Оціни, чи це вигідна і безпечна оферта.
Особливо уважно шукай ризики:
- uszkodzony
- pęknięty
- nie działa
- iCloud
- blokada
- części
- brak ładowarki
- brak zdjęć
- podejrzanie niska cena
- занадто короткий опис
- техніка без перевірки

Відповідай тільки JSON без markdown:
{{
  "score": 0-10,
  "verdict": "дуже вигідна / хороша / нормальна / ризикована / не варто",
  "reason": "коротке пояснення українською",
  "risk_flags": ["..."],
  "message_to_seller_pl": "коротке повідомлення польською до продавця"
}}
"""

    try:
        completion = groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": "You are a careful marketplace deal evaluator. Return valid JSON only.",
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
            temperature=0.2,
        )

        content = completion.choices[0].message.content or "{}"
        result = safe_json_loads(content)

        return {
            "score": int(result.get("score", 0)),
            "verdict": str(result.get("verdict", "н/д")),
            "reason": str(result.get("reason", "")),
            "risk_flags": result.get("risk_flags", []),
            "message_to_seller_pl": str(result.get("message_to_seller_pl", "")),
        }

    except Exception as e:
        logger.error("Groq AI evaluation failed: %s", e)
        return {
            "score": 5,
            "verdict": "не вдалося повністю оцінити",
            "reason": "AI-оцінка Groq не спрацювала, але оголошення підходить під фільтр.",
            "risk_flags": [],
            "message_to_seller_pl": "Dzień dobry, czy oferta jest nadal aktualna?",
        }


# =========================
# TELEGRAM MESSAGE FORMAT
# =========================

def format_item_message(item: Dict[str, Any], ai: Dict[str, Any], search: Dict[str, Any]) -> str:
    risks = ai.get("risk_flags") or []
    risks_text = ", ".join([str(r) for r in risks]) if risks else "не знайдено"

    price_text = "н/д"
    if item.get("price") is not None:
        price_text = f'{item.get("price")} {item.get("currency") or "PLN"}'

    url = item.get("url") or ""

    message = f"""
🔥 <b>Нова оферта з Vinted</b>

🔎 <b>Пошук:</b> {escape(search.get("keyword"))}
📦 <b>Назва:</b> {escape(item.get("title"))}
💰 <b>Ціна:</b> {escape(price_text)}
🏷 <b>Бренд:</b> {escape(item.get("brand") or "н/д")}
📌 <b>Стан:</b> {escape(item.get("condition") or "н/д")}

🤖 <b>AI-оцінка:</b> {escape(ai.get("score"))}/10
✅ <b>Вердикт:</b> {escape(ai.get("verdict"))}
🧠 <b>Причина:</b> {escape(ai.get("reason"))}
⚠️ <b>Ризики:</b> {escape(risks_text)}

✉️ <b>Написати продавцю:</b>
<code>{escape(ai.get("message_to_seller_pl"))}</code>
"""

    if url:
        message += f'\n🔗 <a href="{escape(url)}">Відкрити оголошення</a>'

    return message.strip()


# =========================
# DEAL CHECKER
# =========================

async def process_search(
    application: Application,
    search: Dict[str, Any],
    manual: bool = False,
) -> int:
    telegram_id = str(search["telegram_id"])
    search_id = int(search["id"])
    keyword = search["keyword"]
    max_price = search.get("max_price")

    sent_count = 0

    try:
        items = fetch_vinted_items(keyword, max_price)

        if not items:
            if manual:
                await application.bot.send_message(
                    chat_id=telegram_id,
                    text=f"Нічого не знайшов по пошуку: {keyword}",
                )
            return 0

        for item in items:
            item_id = item.get("id") or item.get("url")

            if not item_id:
                continue

            if was_item_sent(telegram_id, str(item_id)):
                continue

            ai = evaluate_item_with_ai(item, search)
            score = int(ai.get("score", 0))

            # Mark as sent even if score is low, to avoid repeated spam with bad offers.
            mark_item_sent(telegram_id, search_id, str(item_id), item.get("url") or "")

            if score < MIN_AI_SCORE_TO_SEND:
                logger.info("Skipped low score item: %s score=%s", item.get("title"), score)
                continue

            msg = format_item_message(item, ai, search)

            await application.bot.send_message(
                chat_id=telegram_id,
                text=msg,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=False,
            )

            sent_count += 1

            # Small pause to avoid Telegram flood
            time.sleep(0.5)

    except Exception as e:
        logger.error("Search processing error: %s", e)
        logger.error(traceback.format_exc())

        if manual:
            await application.bot.send_message(
                chat_id=telegram_id,
                text=f"Помилка при перевірці пошуку '{keyword}': {e}",
            )

    return sent_count


async def scheduled_check(context: ContextTypes.DEFAULT_TYPE) -> None:
    application = context.application
    searches = get_active_searches()

    if not searches:
        logger.info("No active searches.")
        return

    logger.info("Scheduled check started. Searches: %s", len(searches))

    total_sent = 0

    for search in searches:
        sent = await process_search(application, search, manual=False)
        total_sent += sent

    logger.info("Scheduled check finished. Sent: %s", total_sent)


# =========================
# COMMAND HANDLERS
# =========================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat = update.effective_chat

    if not user or not chat:
        return

    telegram_id = str(chat.id)
    ensure_user(telegram_id)

    text = """
👋 Привіт! Я <b>Vinted AI Deal Hunter</b>.

Я можу шукати оферти на Vinted і оцінювати їх через Groq AI.

<b>Команди:</b>

/add ipad до 1200
/add iphone 13 | 1000
/list
/delete ID
/check
/debug ipad
/help

<b>Приклад:</b>
<code>/add apple watch series 7 до 450</code>

Я буду шукати нові оголошення, фільтрувати по ціні, оцінювати ризики і присилати тільки нормальні оферти.
"""

    await update.message.reply_text(text.strip(), parse_mode=ParseMode.HTML)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = """
<b>Як користуватись:</b>

1. Додати пошук:
<code>/add ipad до 1200</code>

2. Подивитися активні пошуки:
<code>/list</code>

3. Видалити пошук:
<code>/delete 3</code>

4. Перевірити вручну:
<code>/check</code>

5. Тест Apify:
<code>/debug ipad</code>

<b>Формати /add:</b>
<code>/add ipad до 1200</code>
<code>/add iphone 13 | 1000</code>
<code>/add apple watch series 7 max 450</code>
"""

    await update.message.reply_text(text.strip(), parse_mode=ParseMode.HTML)


async def add_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    message = update.message

    if not chat or not message:
        return

    telegram_id = str(chat.id)
    ensure_user(telegram_id)

    keyword, max_price = parse_add_command(message.text or "")

    if not keyword:
        await message.reply_text(
            "Напиши так:\n/add ipad до 1200\nабо\n/add iphone 13 | 1000"
        )
        return

    created = add_search(telegram_id, keyword, max_price)

    price_text = f"до {max_price} PLN" if max_price else "без ліміту ціни"

    await message.reply_text(
        f"✅ Додав пошук #{created['id']}:\n"
        f"🔎 {keyword}\n"
        f"💰 {price_text}\n\n"
        f"Можеш вручну перевірити командою /check",
    )


async def list_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    message = update.message

    if not chat or not message:
        return

    telegram_id = str(chat.id)
    searches = get_active_searches(telegram_id)

    if not searches:
        await message.reply_text("У тебе поки немає активних пошуків. Додай: /add ipad до 1200")
        return

    lines = ["📋 <b>Твої активні пошуки:</b>\n"]

    for s in searches:
        price = s.get("max_price")
        price_text = f"до {price} PLN" if price else "без ліміту"
        lines.append(
            f"#{s['id']} — <b>{escape(s['keyword'])}</b> — {escape(price_text)}"
        )

    lines.append("\nВидалити: <code>/delete ID</code>")

    await message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def delete_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    message = update.message

    if not chat or not message:
        return

    telegram_id = str(chat.id)
    args = context.args

    if not args or not args[0].isdigit():
        await message.reply_text("Напиши так: /delete 3")
        return

    search_id = int(args[0])
    ok = deactivate_search(search_id, telegram_id)

    if not ok:
        await message.reply_text("Не знайшов такого активного пошуку.")
        return

    await message.reply_text(f"🗑 Видалив пошук #{search_id}")


async def check_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    message = update.message

    if not chat or not message:
        return

    telegram_id = str(chat.id)
    searches = get_active_searches(telegram_id)

    if not searches:
        await message.reply_text("У тебе немає активних пошуків. Додай: /add ipad до 1200")
        return

    await message.reply_text("🔍 Перевіряю Vinted...")

    total_sent = 0

    for search in searches:
        sent = await process_search(context.application, search, manual=True)
        total_sent += sent

    if total_sent == 0:
        await message.reply_text("Поки не знайшов нових нормальних оферт.")
    else:
        await message.reply_text(f"✅ Готово. Нових оферт: {total_sent}")


async def debug_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Optional command for testing Apify response.
    Use: /debug ipad
    """
    chat = update.effective_chat
    message = update.message

    if not chat or not message:
        return

    keyword = " ".join(context.args).strip() or "ipad"

    await message.reply_text(f"Тестую Apify по запиту: {keyword}")

    try:
        items = fetch_vinted_items(keyword, None)

        if not items:
            await message.reply_text("Apify повернув 0 items.")
            return

        first = items[0]

        text = f"""
<b>Перший item:</b>

title: {escape(first.get("title"))}
price: {escape(first.get("price"))}
url: {escape(first.get("url"))}
brand: {escape(first.get("brand"))}
condition: {escape(first.get("condition"))}
"""

        await message.reply_text(text.strip(), parse_mode=ParseMode.HTML)

    except Exception as e:
        await message.reply_text(f"Debug error: {e}")


# =========================
# MAIN
# =========================

def main() -> None:
    application = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("add", add_command))
    application.add_handler(CommandHandler("list", list_command))
    application.add_handler(CommandHandler("delete", delete_command))
    application.add_handler(CommandHandler("check", check_command))
    application.add_handler(CommandHandler("debug", debug_command))

    # JobQueue is provided by python-telegram-bot[job-queue].
    # It lets us run periodic checks inside the bot process.
    application.job_queue.run_repeating(
        scheduled_check,
        interval=CHECK_INTERVAL_SECONDS,
        first=30,
        name="scheduled_vinted_check",
    )

    logger.info("Bot started.")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
