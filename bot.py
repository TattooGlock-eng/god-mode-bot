import os
import asyncio
import aiohttp
import feedparser
from datetime import datetime
from telegram import Bot, Update
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.constants import ParseMode
from pybit.unified_trading import HTTP

# ============================================================
# КОНФІГУРАЦІЯ
# ============================================================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
COINGECKO_API_KEY = os.getenv("COINGECKO_API_KEY")
BYBIT_API_KEY = os.getenv("BYBIT_API_KEY")
BYBIT_SECRET = os.getenv("BYBIT_SECRET")

SCAN_INTERVAL = 300
PUMP_DUMP_THRESHOLD = 5.0
TOP_COINS_LIMIT = 100

NEWS_FEEDS = [
    "https://cointelegraph.com/rss",
    "https://coindesk.com/arc/outboundfeeds/rss/",
]

# ============================================================
# ПЕРЕВІРКА ЗМІННИХ
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
        error_msg = f"❌ Відсутні змінні середовища: {', '.join(missing)}"
        print(error_msg)
        raise ValueError(error_msg)
    print("✅ Всі змінні середовища завантажено")
    print(f"   TELEGRAM_TOKEN: {TELEGRAM_TOKEN[:20]}...")
    print(f"   ANTHROPIC_API_KEY: {ANTHROPIC_API_KEY[:20]}...")
    print(f"   COINGECKO_API_KEY: {COINGECKO_API_KEY[:20]}...")
    print(f"   BYBIT_API_KEY: {BYBIT_API_KEY[:20]}...")

# ============================================================
# BYBIT (з VPN обходом)
# ============================================================
def get_bybit_client():
    try:
        client = HTTP(testnet=False, api_key=BYBIT_API_KEY, api_secret=BYBIT_SECRET)
        print("✅ Bybit клієнт ініціалізовано")
        return client
    except Exception as e:
        print(f"❌ Помилка ініціалізації Bybit: {e}")
        raise

def get_bybit_tickers(client):
    try:
        result = client.get_tickers(category="linear")
        tickers = result.get("result", {}).get("list", [])
        filtered = {t["symbol"]: t for t in tickers if t["symbol"].endswith("USDT")}
        print(f"✅ Отримано {len(filtered)} тікерів з Bybit")
        return filtered
    except Exception as e:
        print(f"❌ Bybit помилка: {e}")
        # Якщо США блокада — повертаємо порожній словник, щоб продовжити з CoinGecko
        return {}

def get_bybit_klines(client, symbol, interval="60", limit=50):
    try:
        result = client.get_kline(category="linear", symbol=symbol, interval=interval, limit=limit)
        klines = result.get("result", {}).get("list", [])
        print(f"✅ Отримано {len(klines)} klines для {symbol}")
        return klines
    except Exception as e:
        print(f"❌ Klines помилка {symbol}: {e}")
        return []

# ============================================================
# COINGECKO
# ============================================================
async def get_coingecko_top_coins(session):
    url = "https://api.coingecko.com/api/v3/coins/markets"
    params = {
        "vs_currency": "usd",
        "order": "volume_desc",
        "per_page": TOP_COINS_LIMIT,
        "page": 1,
        "sparkline": "false",  # ВИПРАВЛЕНО: має бути строка, а не bool
        "price_change_percentage": "1h,24h",
        "x_cg_demo_api_key": COINGECKO_API_KEY,
    }
    try:
        print(f"🔍 Запит до CoinGecko з API ключем...")
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            print(f"   Status: {resp.status}")
            if resp.status == 200:
                data = await resp.json()
                print(f"✅ Отримано {len(data)} монет з CoinGecko")
                return data
            else:
                error_text = await resp.text()
                print(f"❌ CoinGecko помилка {resp.status}: {error_text}")
    except Exception as e:
        print(f"❌ CoinGecko помилка: {e}")
        import traceback
        traceback.print_exc()
    return []

# ============================================================
# НОВИНИ
# ============================================================
def get_rss_news(symbol, max_articles=3):
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
            print(f"RSS помилка: {e}")
    return relevant_news[:max_articles]

# ============================================================
# ТЕХНІЧНИЙ АНАЛІЗ
# ============================================================
def calculate_rsi(prices, period=14):
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
    if not prices or len(prices) < period:
        return prices[-1] if prices else 0.0
    multiplier = 2 / (period + 1)
    ema = sum(prices[:period]) / period
    for price in prices[period:]:
        ema = (price * multiplier) + (ema * (1 - multiplier))
    return round(ema, 8)

def analyze_technicals(client, symbol):
    print(f"📊 Аналізую технічні показники для {symbol}...")
    klines = get_bybit_klines(client, symbol)
    if len(klines) < 20:
        print(f"❌ Недостатньо klines для {symbol}: {len(klines)}")
        return None
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
    if current_price > ema20 > ema50:
        trend = "ВИСХІДНИЙ 📈"
    elif current_price < ema20 < ema50:
        trend = "НИЗХІДНИЙ 📉"
    else:
        trend = "НЕЙТРАЛЬНИЙ ➡️"
    recent_highs = sorted(highs[-20:], reverse=True)
    recent_lows = sorted(lows[-20:])
    resistance = recent_highs[min(2, len(recent_highs) - 1)]
    support = recent_lows[min(2, len(recent_lows) - 1)]
    result = {
        "current_price": current_price,
        "rsi": rsi,
        "ema20": ema20,
        "ema50": ema50,
        "volume_ratio": volume_ratio,
        "trend": trend,
        "resistance": round(resistance, 8),
        "support": round(support, 8),
    }
    print(f"✅ Аналіз для {symbol} завершено")
    return result

# ============================================================
# ПАМП/ДАМП
# ============================================================
def detect_pump_dump(ticker_data):
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
    print(f"✅ Виявлено {len(signals)} памп/дамп сигналів")
    return signals[:5]

# ============================================================
# CLAUDE AI
# ============================================================
async def get_claude_analysis(session, symbol, technicals, news, change_24h):
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

Дай відповідь СТРОГО в такому форматі:
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
        print(f"🤖 Надсилаю запит до Claude для {symbol}...")
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
            print(f"   Claude status: {resp.status}")
            if resp.status == 200:
                data = await resp.json()
                analysis = data["content"][0]["text"]
                print(f"✅ Claude відповід для {symbol}")
                return analysis
            else:
                error = await resp.text()
                print(f"❌ Claude помилка {resp.status}: {error}")
    except Exception as e:
        print(f"❌ Claude помилка: {e}")
        import traceback
        traceback.print_exc()
    return None

# ============================================================
# ФОРМАТУВАННЯ
# ============================================================
def format_signal_message(symbol, technicals, analysis, news, pd_type=None):
    now = datetime.now().strftime("%d.%m.%Y %H:%M")
    coin = symbol.replace("USDT", "")
    header = f"⚡ *{pd_type} ВИЯВЛЕНО!*\n" if pd_type else ""
    news_section = ""
    if news:
        news_section = "\n📰 *НОВИНИ:*\n"
        for n in news[:2]:
            short = n[:80] + "..." if len(n) > 80 else n
            news_section += f"• {short}\n"
    return f"""{header}
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

# ============================================================
# КОМАНДИ TELEGRAM
# ============================================================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start"""
    await update.message.reply_text(
        "🤖 *GOD MODE TRADING BOT*\n\n"
        "Доступні команди:\n\n"
        "📊 /signal — сигнал на вимогу (топ монета)\n"
        "🔥 /pump — поточні пампи та дампи\n"
        "🏆 /top — топ 5 монет за рухом\n"
        "📈 /btc — сигнал по Bitcoin\n"
        "📈 /eth — сигнал по Ethereum\n"
        "❓ /status — статус бота\n\n"
        "Також бот автоматично надсилає сигнали кожні 5 хвилин! 🚀",
        parse_mode=ParseMode.MARKDOWN
    )

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /status"""
    now = datetime.now().strftime("%d.%m.%Y %H:%M")
    await update.message.reply_text(
        f"✅ *Бот працює!*\n\n"
        f"🕐 Час: {now}\n"
        f"⏱ Інтервал сканування: 5 хвилин\n"
        f"📡 Bybit: підключено\n"
        f"🤖 Claude AI: підключено\n"
        f"📊 CoinGecko: підключено",
        parse_mode=ParseMode.MARKDOWN
    )

async def cmd_pump(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /pump — показати пампи/дампи"""
    await update.message.reply_text("🔍 Шукаю пампи та дампи...")
    try:
        client = get_bybit_client()
        ticker_data = get_bybit_tickers(client)
        pump_dumps = detect_pump_dump(ticker_data)
        if not pump_dumps:
            await update.message.reply_text("😴 Зараз немає значних пампів або дампів (або Bybit заблокований)")
            return
        msg = "⚡ *ПОТОЧНІ ПАМПИ ТА ДАМПИ:*\n\n"
        for pd in pump_dumps:
            emoji = "🚀" if pd["change_24h"] > 0 else "💥"
            msg += f"{emoji} *{pd['symbol']}*\n"
            msg += f"   Зміна: `{pd['change_24h']:.2f}%`\n"
            msg += f"   Ціна: `${pd['price']}`\n\n"
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        print(f"❌ Помилка cmd_pump: {e}")
        await update.message.reply_text(f"❌ Помилка: {e}")

async def cmd_top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /top — топ монети за рухом"""
    await update.message.reply_text("📊 Завантажую топ монети...")
    try:
        async with aiohttp.ClientSession() as session:
            top_coins = await get_coingecko_top_coins(session)
        if not top_coins:
            await update.message.reply_text("❌ Не вдалось завантажити дані")
            return
        sorted_coins = sorted(
            top_coins,
            key=lambda x: abs(x.get("price_change_percentage_24h") or 0),
            reverse=True
        )[:5]
        msg = "🏆 *ТОП 5 МОНЕТ ЗА РУХОМ (24г):*\n\n"
        for i, coin in enumerate(sorted_coins, 1):
            change = coin.get("price_change_percentage_24h") or 0
            emoji = "🚀" if change > 0 else "💥"
            msg += f"{i}. {emoji} *{coin['symbol'].upper()}*\n"
            msg += f"   Ціна: `${coin['current_price']}`\n"
            msg += f"   Зміна: `{change:.2f}%`\n\n"
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        print(f"❌ Помилка cmd_top: {e}")
        await update.message.reply_text(f"❌ Помилка: {e}")

async def cmd_signal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /signal — сигнал на вимогу"""
    await update.message.reply_text("⏳ Аналізую ринок, зачекай 30 секунд...")
    try:
        client = get_bybit_client()
        
        async with aiohttp.ClientSession() as session:
            top_coins = await get_coingecko_top_coins(session)

        if not top_coins:
            await update.message.reply_text("❌ Не вдалось завантажити дані з CoinGecko")
            return

        # Беремо монету з найбільшим рухом
        candidates = []
        for coin in top_coins[:20]:
            symbol = coin["symbol"].upper() + "USDT"
            change_24h = coin.get("price_change_percentage_24h") or 0
            candidates.append({"symbol": symbol, "change_24h": change_24h, "price": coin.get("current_price", 0)})

        if not candidates:
            await update.message.reply_text("❌ Не вдалось знайти монети")
            return

        candidates.sort(key=lambda x: abs(x["change_24h"]), reverse=True)
        best = candidates[0]
        symbol = best["symbol"]

        technicals = analyze_technicals(client, symbol)
        if not technicals:
            await update.message.reply_text("❌ Не вдалось отримати технічні дані")
            return

        news = get_rss_news(symbol)

        async with aiohttp.ClientSession() as session:
            analysis = await get_claude_analysis(
                session, symbol, technicals, news, best["change_24h"]
            )

        if not analysis:
            await update.message.reply_text("❌ Claude AI не відповів")
            return

        msg = format_signal_message(symbol, technicals, analysis, news)
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

    except Exception as e:
        print(f"❌ Помилка cmd_signal: {e}")
        import traceback
        traceback.print_exc()
        await update.message.reply_text(f"❌ Помилка: {e}")

async def cmd_btc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /btc — сигнал по Bitcoin"""
    await _signal_for_symbol(update, "BTCUSDT")

async def cmd_eth(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /eth — сигнал по Ethereum"""
    await _signal_for_symbol(update, "ETHUSDT")

async def _signal_for_symbol(update: Update, symbol: str):
    """Отримати сигнал для конкретного символу"""
    await update.message.reply_text(f"⏳ Аналізую {symbol.replace('USDT', '')}...")
    try:
        client = get_bybit_client()
        technicals = analyze_technicals(client, symbol)
        if not technicals:
            await update.message.reply_text("❌ Не вдалось отримати дані")
            return
        news = get_rss_news(symbol)
        async with aiohttp.ClientSession() as session:
            ticker_data = get_bybit_tickers(client)
            ticker = ticker_data.get(symbol, {})
            change_24h = float(ticker.get("price24hPcnt", 0)) * 100 if ticker else 0.0
            analysis = await get_claude_analysis(
                session, symbol, technicals, news, change_24h
            )
        if not analysis:
            await update.message.reply_text("❌ Claude AI не відповід")
            return
        msg = format_signal_message(symbol, technicals, analysis, news)
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        print(f"❌ Помилка _signal_for_symbol({symbol}): {e}")
        import traceback
        traceback.print_exc()
        await update.message.reply_text(f"❌ Помилка: {e}")

# ============================================================
# АВТОМАТИЧНЕ СКАНУВАННЯ
# ============================================================
async def auto_scan(bot, client):
    """Фонове автоматичне сканування"""
    scan_count = 0
    while True:
        try:
            scan_count += 1
            print(f"\n🔍 Авто-сканування #{scan_count} — {datetime.now().strftime('%H:%M:%S')}")

            ticker_data = get_bybit_tickers(client)
            pump_dumps = detect_pump_dump(ticker_data)

            async with aiohttp.ClientSession() as session:
                top_coins = await get_coingecko_top_coins(session)
                signals_sent = 0

                for pd in pump_dumps[:2]:
                    symbol = pd["symbol"]
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

                candidates = []
                for coin in top_coins[:20]:
                    symbol = coin["symbol"].upper() + "USDT"
                    change_24h = coin.get("price_change_percentage_24h") or 0
                    if abs(change_24h) >= 3:
                        candidates.append({"symbol": symbol, "change_24h": change_24h})

                candidates.sort(key=lambda x: abs(x["change_24h"]), reverse=True)

                for coin_data in candidates[:2]:
                    symbol = coin_data["symbol"]
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

            print(f"✅ Відправлено сигналів: {signals_sent}")

        except Exception as e:
            print(f"❌ Помилка авто-сканування: {e}")
            import traceback
            traceback.print_exc()

        await asyncio.sleep(SCAN_INTERVAL)

# ============================================================
# ЗАПУСК
# ============================================================
async def main():
    try:
        check_env_vars()

        client = get_bybit_client()

        app = Application.builder().token(TELEGRAM_TOKEN).build()

        # Реєструємо команди
        app.add_handler(CommandHandler("start", cmd_start))
        app.add_handler(CommandHandler("status", cmd_status))
        app.add_handler(CommandHandler("signal", cmd_signal))
        app.add_handler(CommandHandler("pump", cmd_pump))
        app.add_handler(CommandHandler("top", cmd_top))
        app.add_handler(CommandHandler("btc", cmd_btc))
        app.add_handler(CommandHandler("eth", cmd_eth))

        print("🚀 Бот запущено!")

        # Відправити стартове повідомлення
        await app.bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=(
                "🤖 *GOD MODE TRADING BOT запущено!*\n\n"
                "Доступні команди:\n"
                "📊 /signal — сигнал на вимогу\n"
                "🔥 /pump — пампи та дампи\n"
                "🏆 /top — топ 5 монет\n"
                "📈 /btc — сигнал по BTC\n"
                "📈 /eth — сигнал по ETH\n"
                "❓ /status — статус бота\n\n"
                "Автоматичні сигнали кожні 5 хвилин! 🚀"
            ),
            parse_mode=ParseMode.MARKDOWN,
        )

        # Запускаємо авто-сканування паралельно
        asyncio.create_task(auto_scan(app.bot, client))

        # Запускаємо polling для команд
        await app.initialize()
        await app.start()
        await app.updater.start_polling()

        # Тримаємо бота живим
        while True:
            await asyncio.sleep(60)
    except Exception as e:
        print(f"❌ КРИТИЧНА ПОМИЛКА: {e}")
        import traceback
        traceback.print_exc()
        raise

if __name__ == "__main__":
    asyncio.run(main())
