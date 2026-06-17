# -*- coding: utf-8 -*-
"""
ETH Alert Bot
=============
بوت يفحص سعر الإيثيريوم (ETH) وأخباره، ويرسل تنبيهات عبر تليجرام
عند حدوث تحرك سعري كبير أو ظهور خبر جديد متعلق بـ ETH.

** نسخة "دورة واحدة" مخصّصة لـ PythonAnywhere Scheduled Tasks **
هذا السكريبت ينفّذ فحص واحد فقط ثم يخرج. منصة الاستضافة (PythonAnywhere)
هي اللي تستدعيه تلقائياً كل فترة محددة (مثلاً كل 5-15 دقيقة) عبر
خاصية Scheduled Tasks، فلا حاجة لحلقة لا نهائية أو Ctrl+C.

التشغيل اليدوي للتجربة فقط:
    pip install requests --break-system-packages
    python3 eth_alert_bot.py
"""

import json
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

# ============================================================
# الإعدادات (Config) — عدّل هذا القسم إذا احتجت
# ============================================================

TELEGRAM_BOT_TOKEN = "8908908572:AAG0ztHWr-Xww-UUZDNR6u6eljpCmNnWV2w"
TELEGRAM_CHAT_ID = "6566568708"

# ملاحظة: فترة الفحص لا تُضبط هنا — تُضبط من PythonAnywhere Scheduled Tasks
PRICE_MOVE_ALERT_PERCENT = 3.0           # تنبيه إذا تحرك السعر 3% أو أكثر
PRICE_HISTORY_WINDOW = 12                # عدد العينات المخزّنة (تُحسب بحسب فترة التشغيل المجدولة)

# مصادر أخبار RSS موثوقة
NEWS_FEEDS = [
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://cointelegraph.com/rss",
    "https://decrypt.co/feed",
]

# كلمات مفتاحية لفلترة الأخبار المتعلقة بـ ETH فقط
ETH_KEYWORDS = [
    "ethereum", "eth ", "eth/", "ether ", "vitalik", "buterin",
    "staking", "merge", "shapella", "dencun", "pectra", "fusaka",
    "layer 2", "l2", "rollup", "gas fee", "gwei", "defi",
    "smart contract", "evm", "eip-",
]

STATE_FILE = "eth_bot_state.json"

# ============================================================
# دوال تليجرام
# ============================================================

def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = json.dumps({
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as e:
        print(f"[خطأ تليجرام] {e}")
        return None


# ============================================================
# دوال السعر (CoinGecko - مجاني، بدون مفتاح API)
# ============================================================

def fetch_eth_price():
    """يرجع dict فيه السعر بالدولار والتغيّر اليومي %"""
    url = (
        "https://api.coingecko.com/api/v3/simple/price"
        "?ids=ethereum&vs_currencies=usd&include_24hr_change=true"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return {
                "price": data["ethereum"]["usd"],
                "change_24h": data["ethereum"].get("usd_24h_change", 0.0),
            }
    except Exception as e:
        print(f"[خطأ جلب السعر] {e}")
        return None


# ============================================================
# دوال الأخبار (RSS بدون مكتبات خارجية)
# ============================================================

def fetch_rss_items(feed_url):
    """يرجع قائمة tuples (title, link) من رابط RSS معيّن"""
    req = urllib.request.Request(feed_url, headers={"User-Agent": "Mozilla/5.0"})
    items = []
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read()
        root = ET.fromstring(raw)
        # RSS عادي: channel/item
        for item in root.findall(".//item"):
            title_el = item.find("title")
            link_el = item.find("link")
            title = title_el.text.strip() if title_el is not None and title_el.text else ""
            link = link_el.text.strip() if link_el is not None and link_el.text else ""
            if title:
                items.append((title, link))
    except Exception as e:
        print(f"[خطأ جلب RSS من {feed_url}] {e}")
    return items


def is_eth_related(title):
    t = title.lower()
    return any(kw in t for kw in ETH_KEYWORDS)


def fetch_eth_news():
    """يجمع الأخبار من كل المصادر ويفلترها لـ ETH فقط"""
    all_news = []
    for feed_url in NEWS_FEEDS:
        items = fetch_rss_items(feed_url)
        for title, link in items:
            if is_eth_related(title):
                all_news.append({"title": title, "link": link})
    return all_news


# ============================================================
# حفظ/تحميل الحالة (لمنع تكرار نفس الخبر ومتابعة تاريخ السعر)
# ============================================================

def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"seen_links": [], "price_history": []}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# ============================================================
# المنطق الرئيسي
# ============================================================

def check_price_movement(state, current_price):
    """يفحص تحرك السعر مقابل آخر عينة ويرسل تنبيه عند تجاوز الحد"""
    history = state["price_history"]

    if history:
        last_price = history[-1]
        change_pct = ((current_price - last_price) / last_price) * 100
        if abs(change_pct) >= PRICE_MOVE_ALERT_PERCENT:
            direction = "📈 ارتفاع" if change_pct > 0 else "📉 انخفاض"
            send_telegram_message(
                f"🚨 <b>تنبيه حركة سعرية كبيرة!</b>\n\n"
                f"{direction} بنسبة <b>{change_pct:+.2f}%</b>\n"
                f"السعر الحالي: <b>${current_price:,.2f}</b>\n"
                f"مقارنة بآخر فحص"
            )

    history.append(current_price)
    if len(history) > PRICE_HISTORY_WINDOW:
        history.pop(0)
    state["price_history"] = history


def run_cycle(state):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"\n--- فحص جديد: {now} ---")

    # 1) السعر
    price_data = fetch_eth_price()
    if price_data:
        price = price_data["price"]
        change_24h = price_data["change_24h"]
        print(f"السعر الحالي: ${price:,.2f} | تغيّر 24س: {change_24h:+.2f}%")
        check_price_movement(state, price)
    else:
        print("تعذّر جلب السعر هذه الدورة.")

    # 2) الأخبار
    news_items = fetch_eth_news()
    seen_links = set(state.get("seen_links", []))
    new_items = [n for n in news_items if n["link"] and n["link"] not in seen_links]

    for item in new_items:
        msg = f"📰 <b>خبر جديد عن ETH</b>\n\n{item['title']}\n\n{item['link']}"
        send_telegram_message(msg)
        seen_links.add(item["link"])
        print(f"خبر جديد: {item['title']}")

    if not new_items:
        print("لا توجد أخبار جديدة هذه الدورة.")

    # حفظ آخر 300 رابط فقط لمنع تضخم الملف
    state["seen_links"] = list(seen_links)[-300:]
    save_state(state)


def main():
    state = load_state()

    # نرسل رسالة "بدء التشغيل" مرة واحدة فقط (أول مرة يشتغل فيها السكريبت)
    if not state.get("started_before"):
        send_telegram_message(
            "✅ <b>بوت تنبيهات ETH بدأ التشغيل</b>\n\n"
            "سيفحص السعر والأخبار حسب الجدولة المضبوطة."
        )
        state["started_before"] = True
        save_state(state)

    try:
        run_cycle(state)
    except Exception as e:
        print(f"[خطأ غير متوقع] {e}")


if __name__ == "__main__":
    main()
