import os
import re
import json
import html
import time
import logging
import traceback
from datetime import datetime, timezone
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
MAX_ITEMS_PER_SEARCH = int(os.getenv("MAX_ITEMS_PER_SEARCH", "20"))
MIN_AI_SCORE_TO_SEND = int(os.getenv("MIN_AI_SCORE_TO_SEND", "4"))

# New freshness filter.
ONLY_RECENT_MINUTES = int(os.getenv("ONLY_RECENT_MINUTES", "5"))

# If Apify does not return age/date:
# true  = skip item, safer, avoids old listings
# false = allow item, may send old listings
SKIP_UNKNOWN_AGE = os.getenv("SKIP_UNKNOWN_AGE", "true").lower() in ["1", "true", "yes", "y"]

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
# HELPERS
# =========================

def escape(value: Any) -> str:
    return html.escape(str(value or ""), quote=False)


def normalize_price(value: Any) -> Optional[float]:
    if value is None:
        return None

    if isinstance(value, (int, float)):
        return float(value)

    if isinstance(value, dict):
        for key in ["amount", "value", "price", "numeric"]:
            if key in value:
                return normalize_price(value[key])
        return None

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
    text = raw_text.replace("/add", "", 1).strip()

    if not text:
        return None, None

    max_price = None
    keyword = text

    if "|" in text:
        parts = [p.strip() for p in text.split("|", 1)]
        keyword = parts[0]
        max_price = extract_number(parts[1])
        return keyword.strip(), max_price

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


def deep_find_key(data: Any, possible_keys: List[str]) -> Any:
    if isinstance(data, dict):
        for key in possible_keys:
            if key in data and data[key] not in [None, ""]:
                return data[key]
        for value in data.values():
            found = deep_find_key(value, possible_keys)
            if found not in [None, ""]:
                return found

    if isinstance(data, list):
        for item in data:
            found = deep_find_key(item, possible_keys)
            if found not in [None, ""]:
                return found

    return None


def flatten_text_values(data: Any, limit: int = 120) -> str:
    values = []

    def walk(x: Any):
        if len(values) >= limit:
            return
        if isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
        elif isinstance(x, str):
            s = x.strip()
            if 0 < len(s) <= 120:
                values.append(s)

    walk(data)
    return " | ".join(values)


def parse_age_minutes_from_text(text: str) -> Optional[int]:
    if not text:
        return None

    t = text.lower()

    m = re.search(r"(\d+)\s*(min|min\.|minut|minuty|minuta)", t)
    if m:
        return int(m.group(1))

    m = re.search(r"(\d+)\s*(h|godz|godz\.|godzin|godziny|godzinę)", t)
    if m:
        return int(m.group(1)) * 60

    m = re.search(r"(\d+)\s*(d|dzień|dni|dnia)", t)
    if m:
        return int(m.group(1)) * 24 * 60

    if any(word in t for word in ["tydz", "tydzień", "tygodni", "mies", "miesiąc", "rok", "lat"]):
        return 999999

    if any(word in t for word in ["przed chwilą", "teraz", "now", "just now"]):
        return 0

    return None


def parse_datetime_to_age_minutes(value: Any) -> Optional[int]:
    if value is None:
        return None

    now = datetime.now(timezone.utc)

    if isinstance(value, (int, float)):
        ts = float(value)
        if ts > 10_000_000_000:
            ts = ts / 1000
        try:
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            return max(0, int((now - dt).total_seconds() / 60))
        except Exception:
            return None

    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None

        text_age = parse_age_minutes_from_text(s)
        if text_age is not None:
            return text_age

        if re.fullmatch(r"\d{10,13}", s):
            return parse_datetime_to_age_minutes(int(s))

        try:
            iso = s.replace("Z", "+00:00")
            dt = datetime.fromisoformat(iso)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return max(0, int((now - dt.astimezone(timezone.utc)).total_seconds() / 60))
        except Exception:
            return None

    return None


def get_item_age_minutes(raw: Dict[str, Any]) -> Tuple[Optional[int], str]:
    time_keys = [
        "createdAt", "created_at", "created", "creationDate", "creation_date",
        "publishedAt", "published_at", "published", "publicationDate", "publication_date",
        "updatedAt", "updated_at", "lastUpdated", "last_updated",
        "uploadedAt", "uploaded_at", "date", "time", "timestamp",
        "relativeDate", "relative_date", "createdAgo", "created_ago", "addedAgo", "added_ago",
        "added", "dodane"
    ]

    direct = deep_find_key(raw, time_keys)
    age = parse_datetime_to_age_minutes(direct)
    if age is not None:
        return age, f"field={direct}"

    flat = flatten_text_values(raw)
    age = parse_age_minutes_from_text(flat)
    if age is not None:
        return age, "flattened_text"

    return None, "unknown"


def is_recent_item(raw: Dict[str, Any]) -> Tuple[bool, str]:
    age_minutes, source = get_item_age_minutes(raw)

    if age_minutes is None:
        if SKIP_UNKNOWN_AGE:
            return False, f"unknown age skipped ({source})"
        return True, f"unknown age allowed ({source})"

    if age_minutes <= ONLY_RECENT_MINUTES:
        return True, f"{age_minutes} min old ({source})"

    return False, f"{age_minutes} min old, older than {ONLY_RECENT_MINUTES} min ({source})"


def normalize_item(raw: Dict[str, Any]) -> Dict[str, Any]:
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
    images = get_first_existing(raw, ["images", "photos", "photoUrls", "imageUrls"], [])

    if isinstance(seller, dict):
        seller = get_first_existing(seller, ["login", "username", "name"], "")

    age_minutes, age_source = get_item_age_minutes(raw)

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
        "images": images,
        "age_minutes": age_minutes,
        "age_source": age_source,
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
        logger.warning("Could not mark item as sent, probably duplicate.")


# =========================
# VINTED / APIFY
# =========================

def build_apify_input(keyword: str, max_price: Optional[float]) -> Dict[str, Any]:
    actor_input = {
        "searchQuery": keyword,
        "domain": DEFAULT_COUNTRY_DOMAIN,
        "maxItems": MAX_ITEMS_PER_SEARCH,
    }

    if max_price:
        actor_input["maxPrice"] = max_price

    return actor_input


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

    filtered_recent = []
    skipped_old = 0
    skipped_unknown = 0

    for raw_item in data:
        if not isinstance(raw_item, dict):
            continue

        ok, reason = is_recent_item(raw_item)

        if not ok:
            if "unknown age" in reason:
                skipped_unknown += 1
            else:
                skipped_old += 1
            logger.info("Skipped item by age: %s", reason)
            continue

        filtered_recent.append(raw_item)

    logger.info(
        "Freshness filter: input=%s kept=%s skipped_old=%s skipped_unknown=%s only_recent=%smin skip_unknown=%s",
        len(data), len(filtered_recent), skipped_old, skipped_unknown, ONLY_RECENT_MINUTES, SKIP_UNKNOWN_AGE
    )

    normalized = [normalize_item(item) for item in filtered_recent if isinstance(item, dict)]

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
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(text[start:end + 1])
        raise


def evaluate_item_with_ai(item: Dict[str, Any], search: Dict[str, Any]) -> Dict[str, Any]:
    images_info = "немає даних про фото"
    images = item.get("images")
    if isinstance(images, list):
        images_info = f"кількість фото: {len(images)}"
    elif item.get("image"):
        images_info = "є мінімум одне фото"

    prompt = f"""
Ти AI-агент для оцінки оголошень з Vinted у Польщі.

ВАЖЛИВО:
- Ти НЕ бачиш фото напряму.
- Якщо з тексту/метаданих не видно технічного стану, вважай це ризиком.
- Для Apple Watch / iPad / техніки завжди проси фото стану екрана, серійний номер/модель, iCloud/Apple ID logout, батарею якщо доступно.

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
- age_minutes: {item.get("age_minutes")}
- images_info: {images_info}
- description: {item.get("description")}
- url: {item.get("url")}

Оціни, чи це вигідна і безпечна оферта.
Особливо уважно шукай ризики:
- uszkodzony
- pęknięty
- zbity ekran
- porysowany ekran
- nie działa
- iCloud
- blokada
- części
- brak ładowarki
- brak zdjęć
- podejrzanie niska cena
- занадто короткий опис
- техніка без перевірки
- продавець вказав загальний стан, але без технічних деталей

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
            "message_to_seller_pl": "Dzień dobry, czy oferta jest nadal aktualna? Czy można prosić o więcej zdjęć i informacje o stanie technicznym?",
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

    age_text = "н/д"
    if item.get("age_minutes") is not None:
        age_text = f'{item.get("age_minutes")} хв тому'

    message = f"""
🔥 <b>Нова оферта з Vinted</b>

🔎 <b>Пошук:</b> {escape(search.get("keyword"))}
📦 <b>Назва:</b> {escape(item.get("title"))}
💰 <b>Ціна:</b> {escape(price_text)}
🏷 <b>Бренд:</b> {escape(item.get("brand") or "н/д")}
📌 <b>Стан:</b> {escape(item.get("condition") or "н/д")}
🕒 <b>Додано:</b> {escape(age_text)}

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
                    text=(
                        f"Нічого свіжого не знайшов по пошуку: {keyword}\n"
                        f"Фільтр: тільки останні {ONLY_RECENT_MINUTES} хв."
                    ),
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

    text = f"""
👋 Привіт! Я <b>Vinted AI Deal Hunter</b>.

Я шукаю свіжі оферти на Vinted і оцінюю їх через Groq AI.

<b>Зараз фільтр:</b>
тільки оголошення приблизно за останні <b>{ONLY_RECENT_MINUTES} хв.</b>

<b>Команди:</b>

/add ipad до 1200
/add iphone 13 | 1000
/list
/delete ID
/check
/debug ipad
/help

<b>Приклад:</b>
<code>/add apple watch se до 500</code>
"""

    await update.message.reply_text(text.strip(), parse_mode=ParseMode.HTML)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = f"""
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

<b>Фільтр свіжості:</b>
тільки останні {ONLY_RECENT_MINUTES} хв.

<b>Формати /add:</b>
<code>/add ipad до 1200</code>
<code>/add iphone 13 | 1000</code>
<code>/add apple watch se max 500</code>
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
        f"💰 {price_text}\n"
        f"🕒 Тільки останні {ONLY_RECENT_MINUTES} хв.\n\n"
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

    lines = [f"📋 <b>Твої активні пошуки:</b>\n🕒 Фільтр: останні {ONLY_RECENT_MINUTES} хв.\n"]

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

    await message.reply_text(f"🔍 Перевіряю Vinted. Беру тільки останні {ONLY_RECENT_MINUTES} хв...")

    total_sent = 0

    for search in searches:
        sent = await process_search(context.application, search, manual=True)
        total_sent += sent

    if total_sent == 0:
        await message.reply_text("Поки не знайшов нових нормальних свіжих оферт.")
    else:
        await message.reply_text(f"✅ Готово. Нових оферт: {total_sent}")


async def debug_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    message = update.message

    if not chat or not message:
        return

    keyword = " ".join(context.args).strip() or "ipad"

    await message.reply_text(f"Тестую Apify по запиту: {keyword}")

    try:
        actor_id_for_url = APIFY_ACTOR_ID.replace("/", "~")
        url = (
            f"https://api.apify.com/v2/acts/"
            f"{actor_id_for_url}/run-sync-get-dataset-items"
            f"?token={APIFY_TOKEN}"
        )
        payload = build_apify_input(keyword, None)

        response = requests.post(
            url,
            json=payload,
            timeout=120,
            headers={"Content-Type": "application/json"},
        )

        if response.status_code >= 400:
            await message.reply_text(f"Debug error: Apify error {response.status_code}: {response.text[:800]}")
            return

        data = response.json()
        if not isinstance(data, list) or not data:
            await message.reply_text("Apify повернув 0 items.")
            return

        first_raw = data[0]
        first = normalize_item(first_raw)
        ok, reason = is_recent_item(first_raw)

        text = f"""
<b>Перший item:</b>

title: {escape(first.get("title"))}
price: {escape(first.get("price"))}
url: {escape(first.get("url"))}
brand: {escape(first.get("brand"))}
condition: {escape(first.get("condition"))}
age_minutes: {escape(first.get("age_minutes"))}
recent_filter: {escape(ok)}
reason: {escape(reason)}
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

    application.job_queue.run_repeating(
        scheduled_check,
        interval=CHECK_INTERVAL_SECONDS,
        first=30,
        name="scheduled_vinted_check",
    )

    logger.info("Bot started. Freshness filter: ONLY_RECENT_MINUTES=%s SKIP_UNKNOWN_AGE=%s", ONLY_RECENT_MINUTES, SKIP_UNKNOWN_AGE)
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
