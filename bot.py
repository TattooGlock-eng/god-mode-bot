import os
import asyncio
import aiohttp
import feedparser
from datetime import datetime
from telegram import Bot
from telegram.constants import ParseMode
from pybit.unified_trading import HTTP

# ============================================================
# КОНФІГУРАЦІЯ — ключі беруться із змінних середовища Railway
# ============================================================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
COINGECKO_API_KEY = os.getenv("COINGECKO_API_KEY")
BYBIT_API_KEY = os.getenv("BYBIT_API_KEY")
BYBIT_SECRET = os.getenv("BYBIT_SECRET")

# Інтервал сканування в секундах (5 хвилин)
SCAN_INTERVAL = 300

# Поріг для виявлення пампу/дампу (%)
PUMP_DUMP_THRESHOLD = 5.0

# Кількість топ монет для аналізу
TOP_COINS_LIMIT = 100

# RSS стрічки новин
NEWS_FEEDS = [
    "https://cointelegraph.com/rss",
    "https://coindesk.com/arc/outboundfeeds/rss/",
]

# ============================================================
# ПЕРЕВІРКА ЗМІННИХ СЕРЕДОВИЩА
# ============================================================
def check_env_vars():
    required = {
        "TELEGRAM_TOKEN": TELEGRAM_TOKEN,
        "TELEGRAM_CHAT_ID": TELEGRAM_CHAT_ID,
        "ANTHROPIC_API_KEY": ANTHROPIC_API_KEY,
        "COINGECKO_API_KEY": COINGECKO_API_KEY,
        "BYBIT_API_KEY": BYBIT_API_KEY,
        "BYBIT_SECRET": BYBIT_SECRET,
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        raise ValueError(f"❌ Відсутні змінні середовища: {', '.join(missing)}")
    print("✅ Всі змінні середовища завантажено")

# ============================================================
# BYBIT КЛІЄНТ
# ============================================================
def get_bybit_client():
    return HTTP(
        testnet=False,
        api_key=BYBIT_API_KEY,
        api_secret=BYBIT_SECRET,
    )

# ============================================================
# ОТРИМАННЯ ДАНИХ З BYBIT
# ============================================================
def get_bybit_tickers(client):
    """Отримати всі тікери з Bybit USDT Perpetual"""
    try:
        result = client.get_tickers(category="linear")
        tickers = result.get("result", {}).get("list", [])
        return {t["symbol"]: t for t in tickers if t["symbol"].endswith("USDT")}
    except Exception as e:
        print(f"Bybit помилка: {e}")
        return {}

def get_bybit_klines(client, symbol, interval="60", limit=50):
    """Отримати свічки для символу"""
    try:
        result = client.get_kline(
            category="linear",
            symbol=symbol,
            interval=interval,
            limit=limit
        )
        return result.get("result", {}).get("list", [])
    except Exception as e:
        print(f"Klines помилка для {symbol}: {e}")
        return []

# ============================================================
# ОТРИМАННЯ ДАНИХ З COINGECKO
# ============================================================
async def get_coingecko_top_coins(session):
    """Отримати топ монети з CoinGecko"""
    url = "https://api.coingecko.com/api/v3/coins/markets"
    params = {
        "vs_currency": "usd",
        "order": "volume_desc",
        "per_page": TOP_COINS_LIMIT,
        "page": 1,
        "sparkline": False,
        "price_change_percentage": "1h,24h",
        "x_cg_demo_api_key": COINGECKO_API_KEY,
    }
    try:
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status == 200:
                return await resp.json()
            else:
                print(f"CoinGecko статус: {resp.status}")
    except Exception as e:
        print(f"CoinGecko помилка: {e}")
    return []

# ============================================================
# ОТРИМАННЯ НОВИН З RSS
# ============================================================
def get_rss_news(symbol, max_articles=3):
    """Шукати новини про монету в RSS стрічках"""
    coin = symbol.replace("USDT", "").upper()
    relevant_news = []

    for feed_url in NEWS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:20]:
                title = entry.get("title", "")
                if coin.lower() in title.lower():
                    relevant_news.append(title)
                    if len(relevant_news) >= max_articles:
                        break
        except Exception as e:
            print(f"RSS помилка {feed_url}: {e}")

    return relevant_news[:max_articles]

# ============================================================
# ТЕХНІЧНИЙ АНАЛІЗ
# ============================================================
def calculate_rsi(prices, period=14):
    """Розрахунок RSI"""
    if len(prices) < period + 1:
        return 50.0

    gains, losses = [], []
    for i in range(1, len(prices)):
        diff = prices[i] - prices[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))

    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)

def calculate_ema(prices, period):
    """Розрахунок EMA"""
    if not prices or len(prices) < period:
        return prices[-1] if prices else 0.0

    multiplier = 2 / (period + 1)
    ema = sum(prices[:period]) / period

    for price in prices[period:]:
        ema = (price * multiplier) + (ema * (1 - multiplier))

    return round(ema, 8)

def analyze_technicals(client, symbol):
    """Технічний аналіз монети"""
    klines = get_bybit_klines(client, symbol, interval="60", limit=50)

    if len(klines) < 20:
        return None

    # Bybit повертає [startTime, open, high, low, close, volume, turnover]
    klines_sorted = list(reversed(klines))
    closes = [float(k[4]) for k in klines_sorted]
    volumes = [float(k[5]) for k in klines_sorted]
    highs = [float(k[2]) for k in klines_sorted]
    lows = [float(k[3]) for k in klines_sorted]

    current_price = closes[-1]
    rsi = calculate_rsi(closes)
    ema20 = calculate_ema(closes, 20)
    ema50 = calculate_ema(closes, min(50, len(closes)))

    avg_volume = sum(volumes[:-1]) / max(len(volumes) - 1, 1)
    volume_ratio = round(volumes[-1] / avg_volume, 2) if avg_volume > 0 else 1.0

    # Тренд
    if current_price > ema20 > ema50:
        trend = "ВИСХІДНИЙ 📈"
    elif current_price < ema20 < ema50:
        trend = "НИЗХІДНИЙ 📉"
    else:
        trend = "НЕЙТРАЛЬНИЙ ➡️"

    # Підтримка та опір
    recent_highs = sorted(highs[-20:], reverse=True)
    recent_lows = sorted(lows[-20:])
    resistance = recent_highs[min(2, len(recent_highs) - 1)]
    support = recent_lows[min(2, len(recent_lows) - 1)]

    return {
        "current_price": current_price,
        "rsi": rsi,
        "ema20": ema20,
        "ema50": ema50,
        "volume_ratio": volume_ratio,
        "trend": trend,
        "resistance": round(resistance, 8),
        "support": round(support, 8),
    }

# ============================================================
# ВИЯВЛЕННЯ ПАМПУ/ДАМПУ
# ============================================================
def detect_pump_dump(ticker_data):
    """Виявити аномальні рухи цін"""
    signals = []

    for symbol, ticker in ticker_data.items():
        try:
            price_24h = float(ticker.get("price24hPcnt", 0)) * 100
            volume_24h = float(ticker.get("volume24h", 0))
            price = float(ticker.get("lastPrice", 0))

            if abs(price_24h) >= PUMP_DUMP_THRESHOLD and volume_24h > 100000:
                signals.append({
                    "symbol": symbol,
                    "type": "🚀 ПАМП" if price_24h > 0 else "💥 ДАМП",
                    "change_24h": price_24h,
                    "price": price,
                    "volume_24h": volume_24h,
                })
        except Exception:
            continue

    signals.sort(key=lambda x: abs(x["change_24h"]), reverse=True)
    return signals[:5]

# ============================================================
# CLAUDE AI АНАЛІЗ
# ============================================================
async def get_claude_analysis(session, symbol, technicals, news, change_24h):
    """Отримати AI аналіз від Claude"""
    news_text = "\n".join(news) if news else "Новин не знайдено"
    coin = symbol.replace("USDT", "")

    prompt = f"""Ти - професійний крипто трейдер. Проаналізуй монету та дай чіткий торговий сигнал.

МОНЕТА: {coin}/USDT
ЦІНА: ${technicals['current_price']}
RSI: {technicals['rsi']}
EMA20: {technicals['ema20']}
EMA50: {technicals['ema50']}
ТРЕНД: {technicals['trend']}
ЗМІНА 24г: {change_24h:.2f}%
ОБ'ЄМ (відносно середнього): {technicals['volume_ratio']}x
ОПІР: ${technicals['resistance']}
ПІДТРИМКА: ${technicals['support']}

АКТУАЛЬНІ НОВИНИ:
{news_text}

Дай відповідь СТРОГО в такому форматі (без зайвого тексту):
СИГНАЛ: [LONG / SHORT / НЕЙТРАЛЬНО]
ВПЕВНЕНІСТЬ: [1-10]
ПРИЧИНА: [2-3 речення]
ВХІД: $[ціна]
ТЕЙК-ПРОФІТ 1: $[TP1]
ТЕЙК-ПРОФІТ 2: $[TP2]
ТЕЙК-ПРОФІТ 3: $[TP3]
СТОП-ЛОСС: $[SL]
РИЗИК: [НИЗЬКИЙ / СЕРЕДНІЙ / ВИСОКИЙ]"""

    try:
        async with session.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 600,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data["content"][0]["text"]
            else:
                error = await resp.text()
                print(f"Claude API помилка {resp.status}: {error}")
    except Exception as e:
        print(f"Claude помилка: {e}")

    return None

# ============================================================
# ФОРМАТУВАННЯ ПОВІДОМЛЕННЯ
# ============================================================
def format_signal_message(symbol, technicals, analysis, news, pd_type=None):
    """Форматувати повідомлення для Telegram"""
    now = datetime.now().strftime("%d.%m.%Y %H:%M")
    coin = symbol.replace("USDT", "")

    header = f"⚡ *{pd_type} ВИЯВЛЕНО!*\n" if pd_type else ""

    news_section = ""
    if news:
        news_section = "\n📰 *НОВИНИ:*\n"
        for n in news[:2]:
            short = n[:80] + "..." if len(n) > 80 else n
            news_section += f"• {short}\n"

    message = f"""{header}
🎯 *ТОРГОВИЙ СИГНАЛ — {coin}/USDT*
🕐 {now}

💰 *Ціна:* `${technicals['current_price']}`
📊 *RSI:* `{technicals['rsi']}`
📈 *Тренд:* {technicals['trend']}
📦 *Обʼєм:* `{technicals['volume_ratio']}x від середнього`
🔴 *Опір:* `${technicals['resistance']}`
🟢 *Підтримка:* `${technicals['support']}`
{news_section}
━━━━━━━━━━━━━━━━━━
🤖 *AI АНАЛІЗ:*

{analysis}
━━━━━━━━━━━━━━━━━━
⚠️ _Не є фінансовою порадою. Торгуй відповідально!_"""

    return message

# ============================================================
# ГОЛОВНИЙ ЦИКЛ
# ============================================================
async def scan_market():
    """Основна функція сканування ринку"""
    check_env_vars()

    bot = Bot(token=TELEGRAM_TOKEN)
    client = get_bybit_client()

    print("🚀 Бот запущено!")

    await bot.send_message(
        chat_id=TELEGRAM_CHAT_ID,
        text=(
            "🤖 *GOD MODE TRADING BOT запущено!*\n\n"
            "Сканую ринок кожні 5 хвилин...\n\n"
            "✅ Bybit API підключено\n"
            "✅ CoinGecko підключено\n"
            "✅ Claude AI підключено\n"
            "✅ RSS новини підключено"
        ),
        parse_mode=ParseMode.MARKDOWN,
    )

    scan_count = 0

    async with aiohttp.ClientSession() as session:
        while True:
            try:
                scan_count += 1
                print(f"\n🔍 Сканування #{scan_count} — {datetime.now().strftime('%H:%M:%S')}")

                # 1. Дані з Bybit
                print("📡 Завантажую дані з Bybit...")
                ticker_data = get_bybit_tickers(client)

                # 2. Пампи/дампи
                print("🔎 Шукаю пампи/дампи...")
                pump_dumps = detect_pump_dump(ticker_data)

                # 3. Топ монети з CoinGecko
                print("📊 Завантажую топ монети з CoinGecko...")
                top_coins = await get_coingecko_top_coins(session)

                signals_sent = 0

                # 4. Аналіз пампів/дампів (пріоритет)
                for pd in pump_dumps[:3]:
                    symbol = pd["symbol"]
                    print(f"⚡ Аналізую {pd['type']}: {symbol}")

                    technicals = analyze_technicals(client, symbol)
                    if not technicals:
                        continue

                    news = get_rss_news(symbol)
                    analysis = await get_claude_analysis(
                        session, symbol, technicals, news, pd["change_24h"]
                    )

                    if analysis:
                        msg = format_signal_message(
                            symbol, technicals, analysis, news, pd_type=pd["type"]
                        )
                        await bot.send_message(
                            chat_id=TELEGRAM_CHAT_ID,
                            text=msg,
                            parse_mode=ParseMode.MARKDOWN,
                        )
                        signals_sent += 1
                        await asyncio.sleep(2)

                # 5. Топ монети за рухом ціни
                candidates = []
                for coin in top_coins[:20]:
                    symbol = coin["symbol"].upper() + "USDT"
                    if symbol in ticker_data:
                        change_24h = coin.get("price_change_percentage_24h") or 0
                        if abs(change_24h) >= 3:
                            candidates.append({
                                "symbol": symbol,
                                "change_24h": change_24h,
                            })

                candidates.sort(key=lambda x: abs(x["change_24h"]), reverse=True)

                for coin_data in candidates[:3]:
                    symbol = coin_data["symbol"]
                    print(f"📈 Аналізую {symbol}...")

                    technicals = analyze_technicals(client, symbol)
                    if not technicals:
                        continue

                    news = get_rss_news(symbol)
                    analysis = await get_claude_analysis(
                        session, symbol, technicals, news, coin_data["change_24h"]
                    )

                    if analysis:
                        msg = format_signal_message(symbol, technicals, analysis, news)
                        await bot.send_message(
                            chat_id=TELEGRAM_CHAT_ID,
                            text=msg,
                            parse_mode=ParseMode.MARKDOWN,
                        )
                        signals_sent += 1
                        await asyncio.sleep(2)

                print(f"✅ Готово. Відправлено сигналів: {signals_sent}")
                print(f"⏳ Наступне сканування через {SCAN_INTERVAL // 60} хвилин...")
                await asyncio.sleep(SCAN_INTERVAL)

            except Exception as e:
                print(f"❌ Помилка: {e}")
                await asyncio.sleep(60)

# ============================================================
# ЗАПУСК
# ============================================================
if __name__ == "__main__":
    asyncio.run(scan_market())
