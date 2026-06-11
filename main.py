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

VINTED_BASE_URL = os.getenv("VINTED_BASE_URL", "https://www.vinted.pl").strip().rstrip("/")
VINTED_LOCALE = os.getenv("VINTED_LOCALE", "pl").strip()
VINTED_CURRENCY = os.getenv("VINTED_CURRENCY", "PLN").strip()

# Alert if direct Vinted API returns empty raw results for all searches this many cycles in a row.
EMPTY_ALERT_THRESHOLD_CYCLES = int(os.getenv("EMPTY_ALERT_THRESHOLD_CYCLES", "3"))

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant").strip()

CHECK_INTERVAL_SECONDS = int(os.getenv("CHECK_INTERVAL_SECONDS", "300"))
MAX_ITEMS_PER_SEARCH = int(os.getenv("MAX_ITEMS_PER_SEARCH", "20"))
MIN_AI_SCORE_TO_SEND = int(os.getenv("MIN_AI_SCORE_TO_SEND", "4"))

# Dynamic AI filters. When you add a search in Telegram, Groq creates a JSON filter
# and the bot stores it in Supabase searches.filter_json.
DYNAMIC_AI_FILTERS_ENABLED = os.getenv("DYNAMIC_AI_FILTERS_ENABLED", "true").lower() in ["1", "true", "yes", "y"]
FILTER_GENERATION_MODEL = os.getenv("FILTER_GENERATION_MODEL", GROQ_MODEL).strip()


# New freshness filter.
ONLY_RECENT_MINUTES = int(os.getenv("ONLY_RECENT_MINUTES", "5"))

# If Apify does not return age/date:
# true  = skip item, safer, avoids old listings
# false = allow item, may send old listings
SKIP_UNKNOWN_AGE = os.getenv("SKIP_UNKNOWN_AGE", "true").lower() in ["1", "true", "yes", "y"]

# Quality filter for electronics.
# This rejects risky Vinted listings before Groq AI evaluation, so it costs less.
REJECT_BAD_CONDITIONS = os.getenv("REJECT_BAD_CONDITIONS", "true").lower() in ["1", "true", "yes", "y"]

BAD_CONDITIONS = [
    x.strip().lower()
    for x in os.getenv("BAD_CONDITIONS", "zadowalający,zadowalajacy").split(",")
    if x.strip()
]

BAD_KEYWORDS = [
    x.strip().lower()
    for x in os.getenv(
        "BAD_KEYWORDS",
        "uszkodzony,uszkodzona,uszkodzone,"
        "pęknięty,pekniety,pęknięta,peknieta,pęknięcie,pekniecie,"
        "zbity ekran,zbita szybka,zbita,pęknięta szybka,peknieta szybka,"
        "porysowany ekran,rysy na ekranie,"
        "nie działa,nie dziala,niedziała,niedziala,"
        "części,czesci,na części,na czesci,"
        "blokada,icloud,apple id,appleid,"
        "zablokowany,zablokowana,zablokowane,"
        "zablokowany apple id,zablokowane apple id,blokada apple id,"
        "blokada icloud,icloud lock,activation lock,"
        "brak hasła,brak hasla,nie znam hasła,nie znam hasla,"
        "wylogowany nie jest,nie wylogowany,nie wylogowana,"
        "locked,account locked,apple id locked,"
        "cracked,broken,damaged,for parts,not working"
    ).split(",")
    if x.strip()
]

# Category/product filter.
# This lets you keep broad searches like:
# /add ipad до 1100
# /add apple watch se до 500
# /add redmi pad pro до 850
# and reject accessories like etui/folia/szkło/ładowarka/pasek.
REJECT_ACCESSORIES = os.getenv("REJECT_ACCESSORIES", "true").lower() in ["1", "true", "yes", "y"]

ACCESSORY_KEYWORDS = [
    x.strip().lower()
    for x in os.getenv(
        "ACCESSORY_KEYWORDS",
        "etui,case,cover,pokrowiec,obudowa,"
        "szkło,szklo,szkiełko,szkielko,folia,ochronna,ochronne,"
        "kabel,przewód,przewod,ładowarka,ladowarka,charger,zasilacz,"
        "pasek,strap,bransoleta,bransoletka,band,"
        "rysik,stylus,apple pencil,pencil,"
        "klawiatura,keyboard,"
        "pudełko,pudelko,box,samo pudełko,samo pudelko,"
        "uchwyt,stojak,holder"
    ).split(",")
    if x.strip()
]

# Allowlist model filter.
# Instead of blocking every old model, the bot allows only the models/phrases we care about.
ENABLE_MODEL_ALLOWLIST = os.getenv("ENABLE_MODEL_ALLOWLIST", "true").lower() in ["1", "true", "yes", "y"]

IPAD_ALLOWED_KEYWORDS = [
    x.strip().lower()
    for x in os.getenv(
        "IPAD_ALLOWED_KEYWORDS",
        "ipad 9,ipad 9 gen,ipad 9 generacji,ipad 9th,ipad 2021,"
        "ipad 10,ipad 10 gen,ipad 10 generacji,ipad 10th,ipad 2022,"
        "ipad a16,ipad 11,ipad 11 gen,ipad 11 generacji,ipad 2025,ipad 10.9,ipad 10,9"
    ).split(",")
    if x.strip()
]

APPLE_WATCH_SE_ALLOWED_KEYWORDS = [
    x.strip().lower()
    for x in os.getenv(
        "APPLE_WATCH_SE_ALLOWED_KEYWORDS",
        "apple watch se,watch se,se 2,se 2 generacji,se 2gen,se 2 gen,"
        "se 2nd,se second,se 2022,se 2023,apple watch se 2,apple watch se2"
    ).split(",")
    if x.strip()
]

REDMI_PAD_PRO_ALLOWED_KEYWORDS = [
    x.strip().lower()
    for x in os.getenv(
        "REDMI_PAD_PRO_ALLOWED_KEYWORDS",
        "redmi pad pro,xiaomi redmi pad pro,pad pro 12.1,pad pro 12,1,"
        "redmi pad pro 6/128,redmi pad pro 8/256,redmi pad pro 8gb,redmi pad pro 6gb"
    ).split(",")
    if x.strip()
]

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

if not GROQ_API_KEY:
    raise RuntimeError("Missing GROQ_API_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
groq_client = Groq(api_key=GROQ_API_KEY)

vinted_session = requests.Session()
vinted_session.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "pl-PL,pl;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": f"{VINTED_BASE_URL}/catalog",
})

EMPTY_ALL_SEARCHES_CYCLES = 0
LAST_HEALTH_ALERT_TS = 0.0
HEALTH_ALERT_COOLDOWN_SECONDS = 1800


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
    """
    Try to detect listing age safely.

    Important direct-mode fix:
    Do NOT scan every nested timestamp in raw JSON, because Vinted returns many unrelated
    old timestamps in metadata. We only trust explicit item date/relative age fields.
    """

    # 1. Direct explicit item-level fields only.
    explicit_keys = [
        "createdAt", "created_at", "created",
        "created_at_ts", "created_ts",
        "publishedAt", "published_at", "published",
        "updatedAt", "updated_at",
        "uploadedAt", "uploaded_at",
        "date", "time",
        "relativeDate", "relative_date",
        "createdAgo", "created_ago",
        "addedAgo", "added_ago",
        "added", "dodane"
    ]

    for key in explicit_keys:
        if isinstance(raw, dict) and key in raw and raw[key] not in [None, ""]:
            age = parse_datetime_to_age_minutes(raw[key])
            if age is not None:
                return age, f"explicit_field={key}:{raw[key]}"

    # 2. Direct nested item object, only if known wrappers exist.
    for wrapper_key in ["item", "listing", "product"]:
        nested = raw.get(wrapper_key) if isinstance(raw, dict) else None
        if isinstance(nested, dict):
            for key in explicit_keys:
                if key in nested and nested[key] not in [None, ""]:
                    age = parse_datetime_to_age_minutes(nested[key])
                    if age is not None:
                        return age, f"nested_field={wrapper_key}.{key}:{nested[key]}"

    # 3. Text-based relative age from visible listing text.
    # This catches "Dodane 8 min.", "Uploaded 8 min ago", "2 godz." if present.
    visible_text_parts = []
    for key in [
        "title", "name", "description", "desc", "status", "condition",
        "createdAgo", "created_ago", "addedAgo", "added_ago",
        "relativeDate", "relative_date", "added", "dodane"
    ]:
        if isinstance(raw, dict) and key in raw and isinstance(raw[key], str):
            visible_text_parts.append(raw[key])

    # Some Vinted direct responses may include visible text in a few nested safe places.
    for wrapper_key in ["item", "listing", "product"]:
        nested = raw.get(wrapper_key) if isinstance(raw, dict) else None
        if isinstance(nested, dict):
            for key in ["title", "description", "status", "createdAgo", "addedAgo", "relativeDate", "added", "dodane"]:
                if key in nested and isinstance(nested[key], str):
                    visible_text_parts.append(nested[key])

    visible_text = " | ".join(visible_text_parts)
    age = parse_age_minutes_from_text(visible_text)
    if age is not None:
        return age, "visible_text"

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


def item_searchable_text(raw: Dict[str, Any]) -> str:
    """
    Build text from title/description/condition and raw small text fragments.
    Used to catch risky words like locked Apple ID / iCloud / damaged.
    """
    title = str(get_first_existing(raw, ["title", "name", "itemTitle", "productTitle"], ""))
    description = str(get_first_existing(raw, ["description", "desc"], ""))
    condition = str(get_first_existing(raw, ["condition", "status"], ""))
    brand = str(get_first_existing(raw, ["brand", "brandTitle", "brand_name"], ""))
    flat = flatten_text_values(raw)
    return f"{title} | {description} | {condition} | {brand} | {flat}".lower()


def passes_quality_filter(raw: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Reject obviously bad electronics before spending Groq tokens.
    """
    if not REJECT_BAD_CONDITIONS:
        return True, "quality filter disabled"

    text = item_searchable_text(raw)

    condition = str(get_first_existing(raw, ["condition", "status"], "")).strip().lower()
    if condition and any(bad == condition or bad in condition for bad in BAD_CONDITIONS):
        return False, f"bad condition: {condition}"

    for keyword in BAD_KEYWORDS:
        if keyword and keyword in text:
            return False, f"bad keyword: {keyword}"

    return True, "quality ok"


def detect_search_profile(search_keyword: str) -> str:
    """
    Map user's broad search into product profile.
    """
    k = (search_keyword or "").lower()

    if "apple watch" in k or "watch se" in k:
        return "apple_watch_se"

    if "redmi" in k and "pad" in k:
        return "redmi_pad_pro"

    if "ipad" in k:
        return "ipad"

    return "generic"


def has_any(text: str, words: List[str]) -> bool:
    return any(word in text for word in words if word)


def passes_product_profile_filter(raw: Dict[str, Any], search_keyword: str) -> Tuple[bool, str]:
    """
    Simpler product filter:
    - uses title + description + brand + raw text from Vinted
    - avoids overly strict filters for iPad/Redmi
    - but Apple Watch search is specifically for SE 2, not SE 1
    - lets Groq AI make the final judgement after basic matching
    """
    if not REJECT_ACCESSORIES:
        return True, "product filter disabled"

    text = item_searchable_text(raw)
    profile = detect_search_profile(search_keyword)

    title = str(get_first_existing(raw, ["title", "name", "itemTitle", "productTitle"], "")).lower()
    description = str(get_first_existing(raw, ["description", "desc"], "")).lower()
    brand = str(get_first_existing(raw, ["brand", "brandTitle", "brand_name"], "")).lower()
    condition = str(get_first_existing(raw, ["condition", "status"], "")).lower()

    combined = f"{title} | {description} | {brand} | {condition} | {text}"

    def title_has_accessory() -> Optional[str]:
        for keyword in ACCESSORY_KEYWORDS:
            if keyword and keyword in title:
                return keyword
        return None

    if profile == "ipad":
        if "ipad" not in combined:
            return False, "missing ipad keyword in title/description"

        if has_any(combined, ["iphone", "macbook", "airpods", "apple watch"]):
            return False, "wrong Apple product"

        accessory = title_has_accessory()
        if accessory:
            device_hints = [
                "gb", "wifi", "wi-fi", "cellular", "tablet", "generacji",
                "gen", "a16", "2021", "2022", "10.9", "10,9"
            ]
            if not has_any(combined, device_hints):
                return False, f"likely ipad accessory only: {accessory}"

        return True, "ipad broad filter ok"

    if profile == "apple_watch_se":
        # We are looking specifically for Apple Watch SE 2.
        # Sellers may write SE 2 only in description, so check combined text.
        if not ("apple" in combined and "watch" in combined):
            return False, "missing apple watch keywords in title/description"

        # Reject obvious other lines.
        if has_any(combined, [
            "series 1", "series 2", "series 3", "series 4", "series 5",
            "series 6", "series 7", "series 8", "series 9", "series 10",
            "series 11", "ultra"
        ]):
            return False, "wrong apple watch series"

        # SE 2 allowlist. This is intentionally stricter than before.
        # Plain "Apple Watch SE 40mm" is probably SE 1, so reject it.
        se2_hints = [
            "se 2", "se2", "se 2gen", "se 2 gen", "se gen 2",
            "se 2 generacji", "se 2. generacji",
            "2 generacji", "2. generacji", "drugiej generacji",
            "2nd gen", "2nd generation", "second generation",
            "se 2022", "se 2023", "apple watch se 2", "apple watch se2"
        ]

        if not has_any(combined, se2_hints):
            return False, "apple watch is not clearly SE 2"

        accessory = title_has_accessory()
        if accessory:
            device_hints = [
                "se 2", "2 generacji", "2. generacji", "gps", "40mm", "44mm",
                "40 mm", "44 mm", "watch se", "kondycja baterii", "bateria", "zegarek"
            ]
            if not has_any(combined, device_hints):
                return False, f"likely apple watch accessory only: {accessory}"

        return True, "apple watch se 2 filter ok"

    if profile == "redmi_pad_pro":
        # Keep Redmi strict: must be Redmi + Pad + Pro.
        if not ("redmi" in combined and "pad" in combined):
            return False, "missing redmi pad keywords in title/description"

        if "pro" not in combined:
            return False, "missing pro keyword for redmi pad pro"

        if has_any(combined, ["redmi note", "xiaomi note", "telefon", "smartfon", "phone"]):
            return False, "wrong redmi product"

        accessory = title_has_accessory()
        if accessory:
            device_hints = ["tablet", "gb", "6/128", "8/256", "12.1", "12,1", "hyperos", "android"]
            if not has_any(combined, device_hints):
                return False, f"likely redmi accessory only: {accessory}"

        return True, "redmi pad pro broad filter ok"

    return True, "generic profile ok"

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
# DYNAMIC AI FILTERS
# =========================

def as_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(x).strip().lower() for x in value if str(x).strip()]
    if isinstance(value, str):
        return [x.strip().lower() for x in value.split(",") if x.strip()]
    return []


def get_filter_profile(search: Dict[str, Any]) -> Dict[str, Any]:
    raw = search.get("filter_json") or search.get("ai_filter") or {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            return json.loads(raw)
        except Exception:
            return {}
    return {}


def text_contains_any(text: str, keywords: List[str]) -> Optional[str]:
    text = (text or "").lower()
    for kw in keywords:
        kw = (kw or "").lower().strip()
        if kw and kw in text:
            return kw
    return None


def text_contains_all_groups(text: str, groups: Any) -> Tuple[bool, str]:
    """
    groups example: [["ipad", "i pad"], ["10 gen", "10 generacji", "10th", "2022"]]
    At least one phrase from every group must exist.
    """
    text = (text or "").lower()
    if not isinstance(groups, list):
        return True, "no required groups"

    for group in groups:
        words = as_list(group)
        if not words:
            continue
        if not any(w in text for w in words):
            return False, "missing one required group: " + "/".join(words[:6])
    return True, "required groups ok"


def build_ai_filter_prompt(keyword: str, max_price: Optional[float]) -> str:
    return f"""
Ти створюєш JSON-фільтр для Telegram-бота, який шукає товари на Vinted у Польщі.
Користувач НЕ буде сам писати фільтри. Він дає тільки людський запит.
Твоя задача — самостійно згенерувати правила пошуку і відсіювання сміття.

Запит користувача: {keyword}
Максимальна ціна, якщо є: {max_price} PLN

Поверни ТІЛЬКИ валідний JSON без markdown. Формат:
{{
  "vinted_query": "короткий пошуковий запит для Vinted польською/англійською, без ціни",
  "filter_summary_ua": "коротко українською що саме шукаємо і що відсікаємо",
  "required_groups": [
    ["синоніми головного товару"],
    ["синоніми конкретної моделі/покоління/версії"]
  ],
  "include_any": ["додаткові корисні слова, які можуть підтвердити що це правильний товар"],
  "reject_any": ["слова, які треба відсікти: аксесуари, інші моделі, поломки, блокування"],
  "wrong_product_any": ["слова інших товарів, які схожі але не підходять"],
  "quality_risk_any": ["uszkodzony", "pęknięty", "zbity", "icloud", "blokada", "nie działa"],
  "min_ai_score": 4,
  "message_to_seller_pl": "коротке питання продавцю польською, що перевірити перед покупкою"
}}

Правила:
- Для iPad 10 генерації додай варіанти: ipad 10, 10 gen, 10 generacji, 10th, 2022, 10.9, 10,9, A2696, A2757, A2777.
- Для техніки завжди відсікай аксесуари: etui, case, szkło, folia, kabel, ładowarka, pudełko, rysik, klawiatura, pokrowiec, uchwyt.
- Для Apple відсікай iCloud/Apple ID lock і зламані/на частини.
- Якщо запит про конкретну модель, відсікай старі/інші моделі.
- Не роби занадто вузький фільтр: продавці можуть писати назву неточно.
- required_groups мають бути достатньо широкі, щоб не пропускати сміття, але не блокувати нормальні оголошення.
""".strip()


def fallback_filter(keyword: str, max_price: Optional[float]) -> Dict[str, Any]:
    k = (keyword or "").lower()
    required = [[keyword.lower()]] if keyword else []
    if "ipad" in k and ("10" in k or "десят" in k or "gener" in k):
        required = [["ipad", "i pad"], ["10", "10 gen", "10 generacji", "10th", "2022", "10.9", "10,9"]]
    return {
        "vinted_query": keyword,
        "filter_summary_ua": "AI-фільтр fallback: шукаю основний запит, відсікаю аксесуари, поломки і блокування.",
        "required_groups": required,
        "include_any": [],
        "reject_any": [
            "etui", "case", "cover", "pokrowiec", "szkło", "szklo", "folia", "kabel",
            "ładowarka", "ladowarka", "charger", "pudełko", "pudelko", "rysik", "stylus",
            "klawiatura", "uchwyt", "stojak", "uszkodzony", "uszkodzona", "pęknięty",
            "pekniety", "zbity", "nie działa", "nie dziala", "części", "czesci", "icloud",
            "apple id", "blokada", "zablokowany", "locked", "broken", "damaged", "for parts"
        ],
        "wrong_product_any": [],
        "quality_risk_any": ["uszkodzony", "pęknięty", "zbity", "icloud", "blokada", "nie działa"],
        "min_ai_score": MIN_AI_SCORE_TO_SEND,
        "message_to_seller_pl": "Dzień dobry, czy oferta jest aktualna? Czy urządzenie jest w pełni sprawne, bez blokady konta i czy można prosić o zdjęcia ekranu oraz numer modelu?"
    }


def generate_filter_with_ai(keyword: str, max_price: Optional[float]) -> Dict[str, Any]:
    if not DYNAMIC_AI_FILTERS_ENABLED:
        return fallback_filter(keyword, max_price)

    try:
        completion = groq_client.chat.completions.create(
            model=FILTER_GENERATION_MODEL,
            messages=[
                {"role": "system", "content": "You generate strict but practical marketplace search filters. Return valid JSON only."},
                {"role": "user", "content": build_ai_filter_prompt(keyword, max_price)},
            ],
            temperature=0.15,
        )
        content = completion.choices[0].message.content or "{}"
        data = safe_json_loads(content)
        if not isinstance(data, dict):
            raise ValueError("AI did not return object")

        data.setdefault("vinted_query", keyword)
        data.setdefault("filter_summary_ua", "AI створив фільтр для цього пошуку.")
        data.setdefault("required_groups", [])
        data.setdefault("include_any", [])
        data.setdefault("reject_any", [])
        data.setdefault("wrong_product_any", [])
        data.setdefault("quality_risk_any", [])
        data.setdefault("min_ai_score", MIN_AI_SCORE_TO_SEND)
        data.setdefault("message_to_seller_pl", "Dzień dobry, czy oferta jest aktualna i czy przedmiot jest w pełni sprawny?")
        return data
    except Exception as e:
        logger.error("AI filter generation failed: %s", e)
        return fallback_filter(keyword, max_price)


def passes_dynamic_ai_filter(raw: Dict[str, Any], search: Dict[str, Any]) -> Tuple[bool, str]:
    profile = get_filter_profile(search)
    if not profile:
        # Old searches without generated filter still work with old generic filtering.
        return passes_product_profile_filter(raw, search.get("keyword", ""))

    text = item_searchable_text(raw)
    title = str(get_first_existing(raw, ["title", "name", "itemTitle", "productTitle"], "")).lower()
    combined = f"{title} | {text}".lower()

    reject_words = as_list(profile.get("reject_any")) + as_list(profile.get("wrong_product_any")) + as_list(profile.get("quality_risk_any"))
    bad = text_contains_any(combined, reject_words)
    if bad:
        return False, f"AI DB filter rejected keyword: {bad}"

    ok, reason = text_contains_all_groups(combined, profile.get("required_groups"))
    if not ok:
        return False, f"AI DB filter: {reason}"

    # include_any is optional; it boosts confidence but does not block by itself.
    return True, "AI DB filter ok"

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
    ai_filter = generate_filter_with_ai(keyword, max_price)
    vinted_query = str(ai_filter.get("vinted_query") or keyword).strip() or keyword
    min_ai_score = int(ai_filter.get("min_ai_score") or MIN_AI_SCORE_TO_SEND)

    payload = {
        "telegram_id": telegram_id,
        "keyword": keyword,
        "vinted_query": vinted_query,
        "max_price": max_price,
        "country": "pl",
        "active": True,
        "filter_json": ai_filter,
        "filter_summary": str(ai_filter.get("filter_summary_ua") or ""),
        "min_ai_score": min_ai_score,
    }

    try:
        result = supabase.table("searches").insert(payload).execute()
    except Exception as e:
        logger.error("Could not insert dynamic filter search. Did you run the new supabase.sql migration? %s", e)
        # Fallback for old database schema. Search will still work, but filters will not be stored.
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


def regenerate_search_filter(search_id: int, telegram_id: str) -> Optional[Dict[str, Any]]:
    existing = get_search_by_id(search_id, telegram_id)
    if not existing:
        return None

    ai_filter = generate_filter_with_ai(existing.get("keyword", ""), existing.get("max_price"))
    payload = {
        "filter_json": ai_filter,
        "filter_summary": str(ai_filter.get("filter_summary_ua") or ""),
        "vinted_query": str(ai_filter.get("vinted_query") or existing.get("keyword", "")),
        "min_ai_score": int(ai_filter.get("min_ai_score") or MIN_AI_SCORE_TO_SEND),
    }
    supabase.table("searches").update(payload).eq("id", search_id).eq("telegram_id", telegram_id).execute()
    return ai_filter


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
# VINTED DIRECT API
# =========================

class VintedFetchError(Exception):
    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind
        self.message = message


def init_vinted_session() -> None:
    """
    Initializes cookies. Vinted often requires a normal page request before API calls.
    """
    try:
        response = vinted_session.get(
            f"{VINTED_BASE_URL}/catalog",
            timeout=20,
            headers={"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"},
        )
        logger.info("Initialized Vinted session: status=%s", response.status_code)
    except Exception as e:
        logger.warning("Could not initialize Vinted session: %s", e)


def build_vinted_params(keyword: str, max_price: Optional[float]) -> Dict[str, Any]:
    """
    Direct Vinted catalog API params.
    We keep price filtering local because Vinted parameter names can change.
    """
    return {
        "search_text": keyword,
        "order": "newest_first",
        "per_page": MAX_ITEMS_PER_SEARCH,
        "page": 1,
        "locale": VINTED_LOCALE,
        "currency": VINTED_CURRENCY,
    }


def detect_vinted_block(response: requests.Response) -> Optional[str]:
    text = (response.text or "")[:2000].lower()

    if response.status_code in [401, 403, 429]:
        return f"HTTP {response.status_code}"

    if "captcha" in text or "cf-challenge" in text or "cloudflare" in text:
        return "captcha/cloudflare detected"

    if "access denied" in text or "forbidden" in text:
        return "access denied"

    return None


def direct_vinted_request(keyword: str, max_price: Optional[float]) -> List[Dict[str, Any]]:
    endpoint = f"{VINTED_BASE_URL}/api/v2/catalog/items"
    params = build_vinted_params(keyword, max_price)

    logger.info("Direct Vinted request: %s params=%s", endpoint, params)

    response = vinted_session.get(endpoint, params=params, timeout=30)

    block_reason = detect_vinted_block(response)
    if block_reason:
        # Try once with refreshed cookies.
        logger.warning("Vinted blocked or rate-limited request: %s. Refreshing session and retrying once.", block_reason)
        init_vinted_session()
        response = vinted_session.get(endpoint, params=params, timeout=30)
        block_reason = detect_vinted_block(response)
        if block_reason:
            raise VintedFetchError(
                "blocked",
                f"Vinted direct API blocked request ({block_reason}). Status={response.status_code}"
            )

    if response.status_code >= 500:
        raise VintedFetchError(
            "server_error",
            f"Vinted server error {response.status_code}: {response.text[:300]}"
        )

    if response.status_code >= 400:
        raise VintedFetchError(
            "http_error",
            f"Vinted HTTP error {response.status_code}: {response.text[:300]}"
        )

    try:
        data = response.json()
    except Exception:
        raise VintedFetchError(
            "bad_json",
            f"Vinted returned non-JSON response. First chars: {response.text[:300]}"
        )

    if isinstance(data, dict):
        items = data.get("items")
        if isinstance(items, list):
            return items

        # Sometimes APIs wrap differently. Treat this as format break.
        keys = ", ".join(list(data.keys())[:15])
        raise VintedFetchError(
            "api_changed",
            f"Vinted API format changed or unexpected. JSON keys: {keys}"
        )

    if isinstance(data, list):
        return data

    raise VintedFetchError(
        "api_changed",
        f"Vinted API returned unexpected type: {type(data).__name__}"
    )


def normalize_vinted_direct_item(raw: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalize Vinted direct API item to our existing format.
    """
    item_id = get_first_existing(raw, ["id", "item_id"], "")
    title = get_first_existing(raw, ["title", "name"], "No title")

    url = get_first_existing(raw, ["url"], "")
    if url and isinstance(url, str) and url.startswith("/"):
        url = f"{VINTED_BASE_URL}{url}"
    if not url and item_id:
        # Fallback URL, not always perfect but usually usable.
        slug = re.sub(r"[^a-z0-9]+", "-", str(title).lower()).strip("-")
        url = f"{VINTED_BASE_URL}/items/{item_id}-{slug}"

    price_raw = get_first_existing(raw, ["price", "price_amount", "total_item_price"], None)
    price = normalize_price(price_raw)

    currency = VINTED_CURRENCY
    if isinstance(price_raw, dict):
        currency = str(get_first_existing(price_raw, ["currency_code", "currency"], VINTED_CURRENCY))

    brand = get_first_existing(raw, ["brand_title", "brand", "brand_name"], "")
    if isinstance(brand, dict):
        brand = get_first_existing(brand, ["title", "name"], "")

    size = get_first_existing(raw, ["size_title", "size"], "")
    status = get_first_existing(raw, ["status", "condition", "status_title"], "")

    description = get_first_existing(raw, ["description", "desc"], "")
    seller = get_first_existing(raw, ["user", "seller"], "")
    if isinstance(seller, dict):
        seller = get_first_existing(seller, ["login", "username", "name"], "")

    location = get_first_existing(raw, ["city", "location", "country"], "")

    photo = get_first_existing(raw, ["photo"], "")
    image = ""
    if isinstance(photo, dict):
        image = get_first_existing(photo, ["url", "full_size_url", "thumb_url"], "")
    else:
        image = get_first_existing(raw, ["image", "imageUrl", "thumbnail"], "")

    photos = get_first_existing(raw, ["photos"], [])
    images = []
    if isinstance(photos, list):
        for p in photos:
            if isinstance(p, dict):
                u = get_first_existing(p, ["url", "full_size_url", "thumb_url"], "")
                if u:
                    images.append(u)

    age_minutes, age_source = get_item_age_minutes(raw)

    return {
        "id": str(item_id or url),
        "title": str(title),
        "url": str(url),
        "price": price,
        "currency": str(currency),
        "brand": str(brand),
        "size": str(size),
        "condition": str(status),
        "description": str(description),
        "seller": str(seller),
        "location": str(location),
        "image": str(image),
        "images": images,
        "age_minutes": age_minutes,
        "age_source": age_source,
        "raw": raw,
    }


def fetch_vinted_items(keyword: str, max_price: Optional[float], search: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    active_search = search or {"keyword": keyword, "max_price": max_price}
    vinted_query = str(active_search.get("vinted_query") or keyword).strip() or keyword
    raw_items = direct_vinted_request(vinted_query, max_price)

    filtered_recent = []
    skipped_old = 0
    skipped_unknown = 0
    skipped_quality = 0
    skipped_product = 0

    for raw_item in raw_items:
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

        quality_ok, quality_reason = passes_quality_filter(raw_item)
        if not quality_ok:
            skipped_quality += 1
            logger.info("Skipped item by quality: %s", quality_reason)
            continue

        product_ok, product_reason = passes_dynamic_ai_filter(raw_item, active_search)
        if not product_ok:
            skipped_product += 1
            logger.info("Skipped item by AI DB product filter: %s", product_reason)
            continue

        filtered_recent.append(raw_item)

    logger.info(
        "Direct Vinted filter: input=%s kept=%s skipped_old=%s skipped_unknown=%s skipped_quality=%s skipped_product=%s",
        len(raw_items), len(filtered_recent), skipped_old, skipped_unknown, skipped_quality, skipped_product
    )

    normalized = [normalize_vinted_direct_item(item) for item in filtered_recent if isinstance(item, dict)]

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
        items = fetch_vinted_items(keyword, max_price, search)

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
            min_score = int(search.get("min_ai_score") or get_filter_profile(search).get("min_ai_score") or MIN_AI_SCORE_TO_SEND)

            mark_item_sent(telegram_id, search_id, str(item_id), item.get("url") or "")

            if score < min_score:
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

    except VintedFetchError as e:
        logger.error("Vinted fetch error: %s %s", e.kind, e.message)
        logger.error(traceback.format_exc())

        if manual:
            await application.bot.send_message(
                chat_id=telegram_id,
                text=f"⚠️ Проблема з Vinted direct при пошуку '{keyword}': {e.kind}\n{e.message}",
            )
        else:
            raise

    except Exception as e:
        logger.error("Search processing error: %s", e)
        logger.error(traceback.format_exc())

        if manual:
            await application.bot.send_message(
                chat_id=telegram_id,
                text=f"Помилка при перевірці пошуку '{keyword}': {e}",
            )
        else:
            raise

    return sent_count


async def send_health_alert(application: Application, telegram_id: str, title: str, details: str) -> None:
    """
    Notify user when direct Vinted access probably broke.
    Cooldown prevents spam.
    """
    global LAST_HEALTH_ALERT_TS

    now = time.time()
    if now - LAST_HEALTH_ALERT_TS < HEALTH_ALERT_COOLDOWN_SECONDS:
        return

    LAST_HEALTH_ALERT_TS = now

    text = f"""
⚠️ <b>Vinted bot health alert</b>

<b>{escape(title)}</b>

{escape(details)}

Ймовірно, треба втрутитись: Vinted міг дати 403/captcha, змінити API або повертати порожні результати.
"""

    try:
        await application.bot.send_message(
            chat_id=telegram_id,
            text=text.strip(),
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        logger.error("Failed to send health alert: %s", e)


async def scheduled_check(context: ContextTypes.DEFAULT_TYPE) -> None:
    global EMPTY_ALL_SEARCHES_CYCLES

    application = context.application
    searches = get_active_searches()

    if not searches:
        logger.info("No active searches.")
        return

    logger.info("Scheduled check started. Searches: %s", len(searches))

    total_sent = 0
    had_vinted_error = False
    all_searches_empty_or_failed = True

    # Use first user's telegram_id as alert recipient.
    alert_telegram_id = str(searches[0]["telegram_id"])

    for search in searches:
        try:
            sent = await process_search(application, search, manual=False)
            total_sent += sent

            # If no exception, at least direct call worked.
            # Empty detection is approximate; /debug can confirm.
            all_searches_empty_or_failed = False

        except VintedFetchError as e:
            had_vinted_error = True
            logger.error("Vinted health error during scheduled check: %s %s", e.kind, e.message)
            await send_health_alert(
                application,
                alert_telegram_id,
                f"Vinted direct error: {e.kind}",
                e.message,
            )
        except Exception as e:
            had_vinted_error = True
            logger.error("Unexpected scheduled check error: %s", e)
            logger.error(traceback.format_exc())
            await send_health_alert(
                application,
                alert_telegram_id,
                "Unexpected bot error",
                str(e),
            )

    if all_searches_empty_or_failed and not had_vinted_error:
        EMPTY_ALL_SEARCHES_CYCLES += 1
    else:
        EMPTY_ALL_SEARCHES_CYCLES = 0

    if EMPTY_ALL_SEARCHES_CYCLES >= EMPTY_ALERT_THRESHOLD_CYCLES:
        await send_health_alert(
            application,
            alert_telegram_id,
            "Vinted повертає порожні результати",
            f"Уже {EMPTY_ALL_SEARCHES_CYCLES} циклів підряд усі пошуки виглядають порожніми. Можливо, Vinted змінив API або блокує запити.",
        )

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

Я шукаю свіжі оферти напряму на Vinted без Apify і оцінюю їх через Groq AI.

<b>Зараз фільтр:</b>
тільки оголошення приблизно за останні <b>{ONLY_RECENT_MINUTES} хв.</b>\nТакож відсікаю ризики: <b>Zadowalający, uszkodzony, pęknięty, iCloud/Apple ID lock</b>\nІ відкидаю аксесуари: <b>etui, folia, szkło, ładowarka, pasek</b>\nФільтр спрощений: перевіряю назву + опис + бренд, а фінальну оцінку дає AI.

<b>Команди:</b>

/add ipad до 1200
/add iphone 13 | 1000
/list
/delete ID
/check
/filter ID
/refreshfilter ID
/debug ipad
/help

<b>Приклад:</b>
<code>/add ipad 10 генерації до 1200</code>
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

5. Подивитись AI-фільтр у базі:
<code>/filter 3</code>

6. Перегенерувати AI-фільтр:
<code>/refreshfilter 3</code>

7. Тест Vinted direct:
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

    await message.reply_text("🤖 Створюю AI-фільтр і записую його в базу...")
    created = add_search(telegram_id, keyword, max_price)

    price_text = f"до {max_price} PLN" if max_price else "без ліміту ціни"
    profile = get_filter_profile(created)
    summary = profile.get("filter_summary_ua") or created.get("filter_summary") or "AI-фільтр створено."
    vinted_query = created.get("vinted_query") or profile.get("vinted_query") or keyword

    await message.reply_text(
        f"✅ Додав пошук #{created['id']}:\n"
        f"🔎 Твій запит: {keyword}\n"
        f"🔍 Запит для Vinted: {vinted_query}\n"
        f"💰 {price_text}\n"
        f"🧠 AI-фільтр: {summary}\n"
        f"🕒 Тільки останні {ONLY_RECENT_MINUTES} хв.\n\n"
        f"Подивитись правила: /filter {created['id']}\n"
        f"Перевірити вручну: /check",
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
        vq = s.get("vinted_query") or get_filter_profile(s).get("vinted_query") or s.get("keyword")
        lines.append(
            f"#{s['id']} — <b>{escape(s['keyword'])}</b> — {escape(price_text)}\n"
            f"   🔍 Vinted: <code>{escape(vq)}</code>"
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

    await message.reply_text(f"Тестую Vinted direct по запиту: {keyword}")

    try:
        raw_items = direct_vinted_request(keyword, None)

        if not raw_items:
            await message.reply_text("Vinted direct повернув 0 raw items.")
            return

        first_raw = raw_items[0]
        first = normalize_vinted_direct_item(first_raw)
        ok, reason = is_recent_item(first_raw)
        quality_ok, quality_reason = passes_quality_filter(first_raw)
        product_ok, product_reason = passes_product_profile_filter(first_raw, keyword)

        text = f"""
<b>Перший item:</b>

title: {escape(first.get("title"))}
price: {escape(first.get("price"))}
url: {escape(first.get("url"))}
brand: {escape(first.get("brand"))}
condition: {escape(first.get("condition"))}
age_minutes: {escape(first.get("age_minutes"))}
recent_filter: {escape(ok)}
recent_reason: {escape(reason)}
quality_filter: {escape(quality_ok)}
quality_reason: {escape(quality_reason)}
product_filter: {escape(product_ok)}
product_reason: {escape(product_reason)}
"""

        await message.reply_text(text.strip(), parse_mode=ParseMode.HTML)

    except VintedFetchError as e:
        await message.reply_text(
            f"⚠️ Vinted direct problem: {escape(e.kind)}\\n{escape(e.message)}",
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        await message.reply_text(f"Debug error: {e}")



async def filter_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    message = update.message
    if not chat or not message:
        return

    telegram_id = str(chat.id)
    if not context.args or not context.args[0].isdigit():
        await message.reply_text("Напиши так: /filter 3")
        return

    search = get_search_by_id(int(context.args[0]), telegram_id)
    if not search:
        await message.reply_text("Не знайшов такого пошуку.")
        return

    profile = get_filter_profile(search)
    if not profile:
        await message.reply_text("У цього пошуку ще немає AI-фільтра в базі. Можеш створити: /refreshfilter ID")
        return

    def short_list(name: str, value: Any, limit: int = 20) -> str:
        items = []
        if isinstance(value, list):
            for x in value:
                if isinstance(x, list):
                    items.append(" / ".join([str(i) for i in x[:8]]))
                else:
                    items.append(str(x))
        text = ", ".join(items[:limit])
        return text or "—"

    text = f"""
🧠 <b>AI-фільтр пошуку #{escape(search.get('id'))}</b>

🔎 <b>Твій запит:</b> {escape(search.get('keyword'))}
🔍 <b>Vinted query:</b> <code>{escape(profile.get('vinted_query') or search.get('vinted_query') or search.get('keyword'))}</code>
💰 <b>Max price:</b> {escape(search.get('max_price') or 'без ліміту')}

<b>Опис:</b>
{escape(profile.get('filter_summary_ua') or search.get('filter_summary') or '—')}

<b>Обовʼязкові групи:</b>
{escape(short_list('required', profile.get('required_groups')))}

<b>Відсікаю:</b>
{escape(short_list('reject', profile.get('reject_any')))}

<b>Інші неправильні товари:</b>
{escape(short_list('wrong', profile.get('wrong_product_any')))}

<b>Ризики якості:</b>
{escape(short_list('risk', profile.get('quality_risk_any')))}

♻️ Перегенерувати: <code>/refreshfilter {escape(search.get('id'))}</code>
"""
    await message.reply_text(text.strip(), parse_mode=ParseMode.HTML)


async def refreshfilter_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    message = update.message
    if not chat or not message:
        return

    telegram_id = str(chat.id)
    if not context.args or not context.args[0].isdigit():
        await message.reply_text("Напиши так: /refreshfilter 3")
        return

    search_id = int(context.args[0])
    await message.reply_text("♻️ Перегенеровую AI-фільтр і записую в базу...")

    profile = regenerate_search_filter(search_id, telegram_id)
    if not profile:
        await message.reply_text("Не знайшов такого пошуку.")
        return

    await message.reply_text(
        f"✅ AI-фільтр оновлено для пошуку #{search_id}.\n"
        f"🔍 Vinted query: {profile.get('vinted_query')}\n"
        f"🧠 {profile.get('filter_summary_ua')}\n\n"
        f"Подивитись правила: /filter {search_id}"
    )

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
    application.add_handler(CommandHandler("filter", filter_command))
    application.add_handler(CommandHandler("refreshfilter", refreshfilter_command))
    application.add_handler(CommandHandler("debug", debug_command))

    application.job_queue.run_repeating(
        scheduled_check,
        interval=CHECK_INTERVAL_SECONDS,
        first=30,
        name="scheduled_vinted_check",
    )

    init_vinted_session()
    logger.info("Bot started in DIRECT VINTED mode. Freshness filter: ONLY_RECENT_MINUTES=%s SKIP_UNKNOWN_AGE=%s", ONLY_RECENT_MINUTES, SKIP_UNKNOWN_AGE)
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
