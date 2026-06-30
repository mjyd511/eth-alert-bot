# -*- coding: utf-8 -*-
"""
ETH Alert Bot
=============
بوت يفحص سعر الإيثيريوم (ETH) وأخباره، ويرسل تنبيهات عبر تليجرام
عند حدوث تحرك سعري كبير أو ظهور خبر جديد متعلق بـ ETH.

الميزات:
- تنبيه فوري عند تحرك السعر بنسبة كبيرة
- أخبار ETH مترجمة للعربية (عنوان + ملخص الخبر كامل)
- تحليل فني كل 3 ساعات: دعم/مقاومة + اتجاه عام + نسبة صعود/انخفاض

** نسخة "دورة واحدة" مخصّصة لـ GitHub Actions **
هذا السكريبت ينفّذ فحص واحد فقط ثم يخرج. GitHub Actions هو اللي
يستدعيه تلقائياً كل فترة محددة (عبر cron schedule في ملف الـ workflow).

التوكن والـ chat_id يُقرآن من متغيرات البيئة (environment variables)
TELEGRAM_BOT_TOKEN و TELEGRAM_CHAT_ID، اللي تُضبط من GitHub Secrets —
لا تُكتب هذي القيم مباشرة بالكود.

التشغيل اليدوي للتجربة فقط (تحتاج تصدير المتغيرات أولاً):
    export TELEGRAM_BOT_TOKEN="xxxx"
    export TELEGRAM_CHAT_ID="xxxx"
    pip install requests --break-system-packages
    python3 eth_alert_bot.py
"""

import json
import os
import re
import sys
import urllib.request
import urllib.error
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

# ============================================================
# الإعدادات (Config) — عدّل هذا القسم إذا احتجت
# ============================================================

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
    print("[خطأ] لم يتم العثور على TELEGRAM_BOT_TOKEN أو TELEGRAM_CHAT_ID في متغيرات البيئة.")
    print("تأكد من ضبط GitHub Secrets بشكل صحيح.")
    sys.exit(1)

# ملاحظة: فترة الفحص لا تُضبط هنا — تُضبط من جدولة GitHub Actions (cron)
PRICE_MOVE_ALERT_PERCENT = 3.0           # تنبيه إذا تحرك السعر 3% أو أكثر
PRICE_HISTORY_WINDOW = 12                # عدد العينات المخزّنة (تُحسب بحسب فترة التشغيل المجدولة)

TECHNICAL_ANALYSIS_INTERVAL_HOURS = 1    # إرسال تحليل فني كل ساعة
TECHNICAL_ANALYSIS_LOOKBACK_DAYS = 7      # عدد الأيام المستخدمة لحساب الدعم/المقاومة/الاتجاه

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


def fetch_eth_market_chart(days=7):
    """يرجع قائمة بأسعار ETH خلال آخر N يوم (لحساب الدعم/المقاومة/الاتجاه)"""
    url = (
        f"https://api.coingecko.com/api/v3/coins/ethereum/market_chart"
        f"?vs_currency=usd&days={days}"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            # كل عنصر [timestamp_ms, price]
            return [point[1] for point in data.get("prices", [])]
    except Exception as e:
        print(f"[خطأ جلب البيانات التاريخية] {e}")
        return None


def simple_moving_average(prices, window):
    if len(prices) < window:
        return None
    return sum(prices[-window:]) / window


def build_technical_analysis(current_price):
    """يبني نص التحليل الفني: دعم/مقاومة + اتجاه + نسبة التغيّر"""
    prices = fetch_eth_market_chart(TECHNICAL_ANALYSIS_LOOKBACK_DAYS)
    if not prices or len(prices) < 10:
        return None

    support = min(prices)
    resistance = max(prices)

    # اتجاه عام: نقارن متوسط النصف الأول بمتوسط النصف الثاني من الفترة
    half = len(prices) // 2
    first_half_avg = sum(prices[:half]) / half
    second_half_avg = sum(prices[half:]) / (len(prices) - half)

    change_pct = ((second_half_avg - first_half_avg) / first_half_avg) * 100

    if change_pct > 2:
        trend_label = "📈 اتجاه عام صاعد"
    elif change_pct < -2:
        trend_label = "📉 اتجاه عام نازل"
    else:
        trend_label = "➡️ اتجاه عام عرضي (متذبذب)"

    # موقع السعر الحالي بين الدعم والمقاومة (نسبة %)
    price_range = resistance - support
    if price_range > 0:
        position_pct = ((current_price - support) / price_range) * 100
    else:
        position_pct = 50

    sma_short = simple_moving_average(prices, min(24, len(prices)))  # تقريباً يوم واحد لو بيانات كل ساعة
    sma_long = simple_moving_average(prices, len(prices))

    momentum_label = ""
    if sma_short and sma_long:
        if sma_short > sma_long:
            momentum_label = "🟢 الزخم القصير أقوى من المتوسط العام (ميل للصعود)"
        else:
            momentum_label = "🔴 الزخم القصير أضعف من المتوسط العام (ميل للانخفاض)"

    text = (
        f"📊 <b>تحليل فني لـ ETH</b>\n\n"
        f"💰 السعر الحالي: <b>${current_price:,.2f}</b>\n\n"
        f"🟩 الدعم (أدنى سعر خلال {TECHNICAL_ANALYSIS_LOOKBACK_DAYS} أيام): <b>${support:,.2f}</b>\n"
        f"🟥 المقاومة (أعلى سعر خلال {TECHNICAL_ANALYSIS_LOOKBACK_DAYS} أيام): <b>${resistance:,.2f}</b>\n\n"
        f"{trend_label}\n"
        f"نسبة التغيّر بين بداية ونهاية الفترة: <b>{change_pct:+.2f}%</b>\n"
    )
    if momentum_label:
        text += f"{momentum_label}\n"

    text += f"\n📍 موقع السعر الحالي بين الدعم والمقاومة: <b>{position_pct:.0f}%</b>"

    return text




def translate_to_arabic(text):
    """يترجم نص من الإنجليزية للعربية عبر خدمة ترجمة مجانية"""
    if not text:
        return text
    try:
        url = (
            "https://translate.googleapis.com/translate_a/single"
            "?client=gtx&sl=en&tl=ar&dt=t&q=" + urllib.parse.quote(text)
        )
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            # الاستجابة قائمة متشعبة، نجمع كل أجزاء الترجمة
            translated = "".join(part[0] for part in data[0] if part[0])
            return translated
    except Exception as e:
        print(f"[خطأ ترجمة] {e}")
        return text  # رجّع النص الأصلي لو فشلت الترجمة


# ============================================================
# دوال تليجرام
# ============================================================

def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = json.dumps({
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
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
    """يرجع قائمة dicts (title, link, summary) من رابط RSS معيّن"""
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
            desc_el = item.find("description")
            title = title_el.text.strip() if title_el is not None and title_el.text else ""
            link = link_el.text.strip() if link_el is not None and link_el.text else ""
            summary = desc_el.text.strip() if desc_el is not None and desc_el.text else ""
            # تنظيف بسيط من أكواد HTML المتبقية بالملخص
            summary = re.sub(r"<[^>]+>", "", summary).strip()
            if title:
                items.append({"title": title, "link": link, "summary": summary})
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
        for item in items:
            if is_eth_related(item["title"]):
                all_news.append(item)
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


def maybe_send_technical_analysis(state, current_price):
    """يرسل تحليل فني فقط لو مرّت TECHNICAL_ANALYSIS_INTERVAL_HOURS منذ آخر تحليل"""
    last_sent_iso = state.get("last_technical_analysis")
    now = datetime.now(timezone.utc)

    should_send = True
    if last_sent_iso:
        try:
            last_sent = datetime.fromisoformat(last_sent_iso)
            hours_passed = (now - last_sent).total_seconds() / 3600
            should_send = hours_passed >= TECHNICAL_ANALYSIS_INTERVAL_HOURS
        except ValueError:
            should_send = True

    if not should_send:
        return

    analysis_text = build_technical_analysis(current_price)
    if analysis_text:
        send_telegram_message(analysis_text)
        state["last_technical_analysis"] = now.isoformat()
        print("تم إرسال التحليل الفني الدوري.")
    else:
        print("تعذّر بناء التحليل الفني هذه الدورة.")


def run_cycle(state):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"\n--- فحص جديد: {now} ---")

    # 1) السعر
    price_data = fetch_eth_price()
    current_price = None
    if price_data:
        current_price = price_data["price"]
        change_24h = price_data["change_24h"]
        print(f"السعر الحالي: ${current_price:,.2f} | تغيّر 24س: {change_24h:+.2f}%")
        check_price_movement(state, current_price)
    else:
        print("تعذّر جلب السعر هذه الدورة.")

    # 2) الأخبار (مترجمة للعربية: العنوان + الملخص)
    news_items = fetch_eth_news()
    seen_links = set(state.get("seen_links", []))
    new_items = [n for n in news_items if n["link"] and n["link"] not in seen_links]

    for item in new_items:
        title_ar = translate_to_arabic(item["title"])
        summary_ar = translate_to_arabic(item["summary"]) if item["summary"] else ""

        msg = f"📰 <b>خبر جديد عن ETH</b>\n\n<b>{title_ar}</b>"
        if summary_ar:
            msg += f"\n\n{summary_ar}"
        msg += f"\n\n🔗 {item['link']}"

        send_telegram_message(msg)
        seen_links.add(item["link"])
        print(f"خبر جديد (مترجم): {title_ar}")

    if not new_items:
        print("لا توجد أخبار جديدة هذه الدورة.")

    # حفظ آخر 300 رابط فقط لمنع تضخم الملف
    state["seen_links"] = list(seen_links)[-300:]

    # 3) التحليل الفني الدوري (كل 3 ساعات)
    if current_price is not None:
        maybe_send_technical_analysis(state, current_price)

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
