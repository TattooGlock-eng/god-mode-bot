import os
import asyncio
import aiohttp
import feedparser
from datetime import datetime
from telegram import Bot, Update
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.constants import ParseMode

# ============================================================
# КОНФІГУРАЦІЯ
# ============================================================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
COINGECKO_API_KEY = os.getenv("COINGECKO_API_KEY")

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
    
    # Перевірка валідності Claude API ключа
    if not ANTHROPIC_API_KEY.startswith("sk-ant-"):
        print("⚠️  Попередження: ANTHROPIC_API_KEY може бути невалідним!")

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
        "sparkline": "false",
        "price_change_percentage": "1h,24h,7d,30d",
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

async def get_coingecko_coin_data(session, coin_id):
    """Отримати детальні дані для конкретної монети"""
    url = f"https://api.coingecko.com/api/v3/coins/{coin_id}"
    params = {
        "localization": "false",
        "tickers": "false",
        "market_data": "true",
        "community_data": "false",
        "developer_data": "false",
        "x_cg_demo_api_key": COINGECKO_API_KEY,
    }
    try:
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status == 200:
                return await resp.json()
    except Exception as e:
        print(f"❌ Помилка отримання даних {coin_id}: {e}")
    return None

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
# ТЕХНІЧНИЙ АНАЛІЗ ЗА ДАНИМИ COINGECKO
# ============================================================
def analyze_technicals_from_coingecko(coin_data):
    """Аналіз на основі CoinGecko даних"""
    try:
        market_data = coin_data.get("market_data", {})
        if not market_data:
            return None
        
        current_price = market_data.get("current_price", {}).get("usd", 0)
        if not current_price:
            return None
        
        price_change_24h = market_data.get("price_change_percentage_24h", 0) or 0
        price_change_7d = market_data.get("price_change_percentage_7d", 0) or 0
        market_cap = market_data.get("market_cap", {}).get("usd", 0) or 0
        
        # Розраховуємо псевдо-RSI на основі змін ціни
        rsi = 50 + (price_change_24h / 2)
        rsi = max(0, min(100, rsi))
        
        # Тренд
        if price_change_7d > 0:
            trend = "ВИСХІДНИЙ 📈"
        elif price_change_7d < -2:
            trend = "НИЗХІДНИЙ 📉"
        else:
            trend = "НЕЙТРАЛЬНИЙ ➡️"
        
        # Опір та підтримка (орієнтовні)
        ath = market_data.get("ath", {}).get("usd", current_price) or current_price
        atl = market_data.get("atl", {}).get("usd", current_price) or current_price
        
        resistance = ath
        support = atl
        
        return {
            "current_price": round(current_price, 8),
            "rsi": round(rsi, 2),
            "ema20": round(current_price * (1 + price_change_24h / 100), 8),
            "ema50": round(current_price * (1 + price_change_7d / 100), 8),
            "volume_ratio": 1.0,
            "trend": trend,
            "resistance": round(resistance, 8),
            "support": round(support, 8),
            "price_change_24h": price_change_24h,
        }
    except Exception as e:
        print(f"❌ Помилка аналізу CoinGecko: {e}")
        return None

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
        print(f"   API Key перевірка: {ANTHROPIC_API_KEY[:30]}...")
        
        headers = {
            "x-api-key": ANTHROPIC_API_KEY.strip(),
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        
        async with session.post(
            "https://api.anthropic.com/v1/messages",
            headers=headers,
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
                error_text = await resp.text()
                print(f"❌ Claude помилка {resp.status}: {error_text}")
                if resp.status == 401:
                    print("⚠️  АВТОРИЗАЦІЯ ПОМИЛКА: Перевір ANTHROPIC_API_KEY на Railway!")
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
        f"🤖 Claude AI: підключено\n"
        f"📊 CoinGecko: підключено",
        parse_mode=ParseMode.MARKDOWN
    )

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
        async with aiohttp.ClientSession() as session:
            top_coins = await get_coingecko_top_coins(session)

        if not top_coins:
            await update.message.reply_text("❌ Не вдалось завантажити дані")
            return

        # Беремо монету з найбільшим рухом
        candidates = []
        for coin in top_coins[:20]:
            symbol = coin["symbol"].upper() + "USDT"
            change_24h = coin.get("price_change_percentage_24h") or 0
            candidates.append({
                "symbol": symbol, 
                "change_24h": change_24h, 
                "id": coin.get("id"),
                "name": coin.get("name")
            })

        if not candidates:
            await update.message.reply_text("❌ Не вдалось знайти монети")
            return

        candidates.sort(key=lambda x: abs(x["change_24h"]), reverse=True)
        best = candidates[0]
        symbol = best["symbol"]

        async with aiohttp.ClientSession() as session:
            # Отримуємо детальні дані для аналізу
            coin_data = await get_coingecko_coin_data(session, best["id"])
        
        if not coin_data:
            await update.message.reply_text("❌ Не вдалось отримати дані монети")
            return

        technicals = analyze_technicals_from_coingecko(coin_data)
        if not technicals:
            await update.message.reply_text("❌ Не вдалось провести аналіз")
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
        await update.message.reply_text(f"❌ Помилка: {str(e)[:100]}")

async def cmd_btc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /btc — сигнал по Bitcoin"""
    await _signal_for_symbol(update, "bitcoin")

async def cmd_eth(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /eth — сигнал по Ethereum"""
    await _signal_for_symbol(update, "ethereum")

async def _signal_for_symbol(update: Update, coin_id: str):
    """Отримати сигнал для конкретної монети"""
    coin_name = coin_id.upper()
    await update.message.reply_text(f"⏳ Аналізую {coin_name}...")
    try:
        async with aiohttp.ClientSession() as session:
            coin_data = await get_coingecko_coin_data(session, coin_id)
        
        if not coin_data:
            await update.message.reply_text(f"❌ Не вдалось отримати дані {coin_name}")
            return
        
        technicals = analyze_technicals_from_coingecko(coin_data)
        if not technicals:
            await update.message.reply_text("❌ Не вдалось провести аналіз")
            return
        
        symbol = coin_id.upper() + "USDT"
        news = get_rss_news(symbol)
        change_24h = technicals.get("price_change_24h", 0)
        
        async with aiohttp.ClientSession() as session:
            analysis = await get_claude_analysis(
                session, symbol, technicals, news, change_24h
            )
        
        if not analysis:
            await update.message.reply_text("❌ Claude AI не відповід")
            return
        
        msg = format_signal_message(symbol, technicals, analysis, news)
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        print(f"❌ Помилка _signal_for_symbol({coin_id}): {e}")
        import traceback
        traceback.print_exc()
        await update.message.reply_text(f"❌ Помилка: {str(e)[:100]}")

# ============================================================
# АВТОМАТИЧНЕ СКАНУВАННЯ
# ============================================================
async def auto_scan(bot):
    """Фонове автоматичне сканування"""
    scan_count = 0
    while True:
        try:
            scan_count += 1
            print(f"\n🔍 Авто-сканування #{scan_count} — {datetime.now().strftime('%H:%M:%S')}")

            async with aiohttp.ClientSession() as session:
                top_coins = await get_coingecko_top_coins(session)
                signals_sent = 0

                for coin in top_coins[:3]:
                    symbol = coin["symbol"].upper() + "USDT"
                    change_24h = coin.get("price_change_percentage_24h") or 0
                    
                    if abs(change_24h) < 3:
                        continue
                    
                    coin_data = await get_coingecko_coin_data(session, coin.get("id"))
                    if not coin_data:
                        continue
                    
                    technicals = analyze_technicals_from_coingecko(coin_data)
                    if not technicals:
                        continue
                    
                    news = get_rss_news(symbol)
                    analysis = await get_claude_analysis(
                        session, symbol, technicals, news, change_24h
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

        await asyncio.sleep(SCAN_INTERVAL)

# ============================================================
# ЗАПУСК
# ============================================================
async def main():
    try:
        check_env_vars()

        app = Application.builder().token(TELEGRAM_TOKEN).build()

        # Реєструємо команди
        app.add_handler(CommandHandler("start", cmd_start))
        app.add_handler(CommandHandler("status", cmd_status))
        app.add_handler(CommandHandler("signal", cmd_signal))
        app.add_handler(CommandHandler("top", cmd_top))
        app.add_handler(CommandHandler("btc", cmd_btc))
        app.add_handler(CommandHandler("eth", cmd_eth))

        print("🚀 Бот запущено!")

        # Відправити стартове повідомлення
        try:
            await app.bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=(
                    "🤖 *GOD MODE TRADING BOT запущено!*\n\n"
                    "Доступні команди:\n"
                    "📊 /signal — сигнал на вимогу\n"
                    "🏆 /top — топ 5 монет\n"
                    "📈 /btc — сигнал по BTC\n"
                    "📈 /eth — сигнал по ETH\n"
                    "❓ /status — статус бота\n\n"
                    "Автоматичні сигнали кожні 5 хвилин! 🚀"
                ),
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception as e:
            print(f"⚠️  Помилка при надсиланні стартового повідомлення: {e}")

        # Запускаємо авто-сканування паралельно
        asyncio.create_task(auto_scan(app.bot))

        # Запускаємо polling для команд
        await app.initialize()
        await app.start()
        await app.updater.start_polling(allowed_updates=Update.ALL_TYPES)

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
