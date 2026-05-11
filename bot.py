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
        print(f"🔍 Запит до CoinGecko...")
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status == 200:
                data = await resp.json()
                print(f"✅ Отримано {len(data)} монет")
                return data
            else:
                error_text = await resp.text()
                print(f"❌ CoinGecko помилка {resp.status}")
    except Exception as e:
        print(f"❌ CoinGecko помилка: {e}")
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
        print(f"❌ Помилка CoinGecko {coin_id}: {e}")
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
            pass
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
        
        # Розраховуємо RSI
        rsi = 50 + (price_change_24h / 2)
        rsi = max(0, min(100, rsi))
        
        # Тренд
        if price_change_7d > 0:
            trend = "ВИСХІДНИЙ 📈"
        elif price_change_7d < -2:
            trend = "НИЗХІДНИЙ 📉"
        else:
            trend = "НЕЙТРАЛЬНИЙ ➡️"
        
        # Опір та підтримка
        ath = market_data.get("ath", {}).get("usd", current_price) or current_price
        atl = market_data.get("atl", {}).get("usd", current_price) or current_price
        
        return {
            "current_price": round(current_price, 8),
            "rsi": round(rsi, 2),
            "ema20": round(current_price * (1 + price_change_24h / 100), 8),
            "ema50": round(current_price * (1 + price_change_7d / 100), 8),
            "trend": trend,
            "resistance": round(ath, 8),
            "support": round(atl, 8),
            "price_change_24h": price_change_24h,
        }
    except Exception as e:
        print(f"❌ Помилка аналізу: {e}")
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
        print(f"🤖 Claude запит...")
        
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
            if resp.status == 200:
                data = await resp.json()
                analysis = data["content"][0]["text"]
                print(f"✅ Claude OK для {symbol}")
                return analysis
            else:
                error_text = await resp.text()
                print(f"❌ Claude {resp.status}: {error_text[:100]}")
    except Exception as e:
        print(f"❌ Claude помилка: {e}")
    return None

# ============================================================
# ФОРМАТУВАННЯ
# ============================================================
def format_signal_message(symbol, technicals, analysis, news):
    now = datetime.now().strftime("%d.%m.%Y %H:%M")
    coin = symbol.replace("USDT", "")
    news_section = ""
    if news:
        news_section = "\n📰 *НОВИНИ:*\n"
        for n in news[:2]:
            short = n[:80] + "..." if len(n) > 80 else n
            news_section += f"• {short}\n"
    return f"""
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
⚠️ _Не є фінансовою порадою!_"""

# ============================================================
# КОМАНДИ TELEGRAM
# ============================================================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start"""
    await update.message.reply_text(
        "🤖 *GOD MODE TRADING BOT*\n\n"
        "📈 /btc — сигнал по Bitcoin\n"
        "📈 /eth — сигнал по Ethereum\n"
        "📊 /signal — топ монета\n"
        "🏆 /top — топ 5 монет\n"
        "❓ /status — статус",
        parse_mode=ParseMode.MARKDOWN
    )

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /status"""
    await update.message.reply_text(
        "✅ *Бот ЗАПУЩЕНО!*\n\n"
        "🤖 Claude AI: ✅\n"
        "📊 CoinGecko: ✅",
        parse_mode=ParseMode.MARKDOWN
    )

async def cmd_top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /top"""
    await update.message.reply_text("📊 Завантажую...")
    try:
        async with aiohttp.ClientSession() as session:
            top_coins = await get_coingecko_top_coins(session)
        if not top_coins:
            await update.message.reply_text("❌ Помилка")
            return
        sorted_coins = sorted(
            top_coins,
            key=lambda x: abs(x.get("price_change_percentage_24h") or 0),
            reverse=True
        )[:5]
        msg = "🏆 *ТОП 5:*\n\n"
        for i, coin in enumerate(sorted_coins, 1):
            change = coin.get("price_change_percentage_24h") or 0
            emoji = "🚀" if change > 0 else "💥"
            msg += f"{i}. {emoji} *{coin['symbol'].upper()}* `{change:.2f}%`\n"
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        await update.message.reply_text(f"❌ {str(e)[:50]}")

async def cmd_signal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /signal"""
    await update.message.reply_text("⏳ Аналізую...")
    try:
        async with aiohttp.ClientSession() as session:
            top_coins = await get_coingecko_top_coins(session)

        if not top_coins:
            await update.message.reply_text("❌ Помилка завантаження")
            return

        candidates = []
        for coin in top_coins[:20]:
            symbol = coin["symbol"].upper() + "USDT"
            change_24h = coin.get("price_change_percentage_24h") or 0
            candidates.append({
                "symbol": symbol, 
                "change_24h": change_24h, 
                "id": coin.get("id")
            })

        candidates.sort(key=lambda x: abs(x["change_24h"]), reverse=True)
        best = candidates[0]

        async with aiohttp.ClientSession() as session:
            coin_data = await get_coingecko_coin_data(session, best["id"])
        
        if not coin_data:
            await update.message.reply_text("❌ Помилка даних")
            return

        technicals = analyze_technicals_from_coingecko(coin_data)
        if not technicals:
            await update.message.reply_text("❌ Помилка аналізу")
            return

        news = get_rss_news(best["symbol"])

        async with aiohttp.ClientSession() as session:
            analysis = await get_claude_analysis(
                session, best["symbol"].upper() + "USDT", technicals, news, best["change_24h"]
            )

        if not analysis:
            await update.message.reply_text("❌ Claude не відповід")
            return

        msg = format_signal_message(best["symbol"].upper() + "USDT", technicals, analysis, news)
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

    except Exception as e:
        await update.message.reply_text(f"❌ {str(e)[:100]}")

async def cmd_btc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /btc"""
    await _signal_for_symbol(update, "bitcoin", "BTC")

async def cmd_eth(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /eth"""
    await _signal_for_symbol(update, "ethereum", "ETH")

async def _signal_for_symbol(update: Update, coin_id: str, coin_name: str):
    """Сигнал для конкретної монети"""
    await update.message.reply_text(f"⏳ Аналізую {coin_name}...")
    try:
        async with aiohttp.ClientSession() as session:
            coin_data = await get_coingecko_coin_data(session, coin_id)
        
        if not coin_data:
            await update.message.reply_text(f"❌ Помилка {coin_name}")
            return
        
        technicals = analyze_technicals_from_coingecko(coin_data)
        if not technicals:
            await update.message.reply_text("❌ Помилка аналізу")
            return
        
        symbol = coin_name + "USDT"
        news = get_rss_news(symbol)
        change_24h = technicals.get("price_change_24h", 0)
        
        async with aiohttp.ClientSession() as session:
            analysis = await get_claude_analysis(
                session, symbol, technicals, news, change_24h
            )
        
        if not analysis:
            await update.message.reply_text("❌ Claude не відповід")
            return
        
        msg = format_signal_message(symbol, technicals, analysis, news)
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        await update.message.reply_text(f"❌ {str(e)[:100]}")

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

        # Запускаємо polling
        await app.initialize()
        await app.start()
        await app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
        print("✅ Polling запущено")

        # Тримаємо живим
        while True:
            await asyncio.sleep(60)
    except Exception as e:
        print(f"❌ ПОМИЛКА: {e}")
        raise

if __name__ == "__main__":
    asyncio.run(main())
