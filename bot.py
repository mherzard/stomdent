#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
STOM Dental Clinic Telegram Bot — MVP
Ассистент стоматологической клиники «STOM» (г. Гавар, Армения).
Поддерживает LLM-провайдеры: anthropic, openai, ollama.
"""

import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

SCRIPT_DIR = Path(__file__).parent.resolve()
load_dotenv(SCRIPT_DIR / ".env")

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOGS_DIR = SCRIPT_DIR / "logs"
LOGS_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(LOGS_DIR / "bot.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("stom_bot")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "ollama").strip().lower()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()

GOOGLE_CALENDAR_URL = os.getenv("GOOGLE_CALENDAR_URL", "").strip()
MAX_HISTORY = int(os.getenv("MAX_HISTORY_MESSAGES", "10"))

ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434").strip().rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1").strip()

if not TELEGRAM_BOT_TOKEN:
    logger.error("TELEGRAM_BOT_TOKEN не найден в .env")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Load system prompt + knowledge base
# ---------------------------------------------------------------------------
def load_text_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception as exc:
        logger.warning("Не удалось прочитать %s: %s", path, exc)
        return ""


SYSTEM_PROMPT_PATH = SCRIPT_DIR / "stdn.txt"
KNOWLEDGE_DIR = SCRIPT_DIR / "knowledge"

SYSTEM_PROMPT = load_text_file(SYSTEM_PROMPT_PATH)

KNOWLEDGE_PIECES: List[str] = []
if KNOWLEDGE_DIR.exists():
    for md_file in sorted(KNOWLEDGE_DIR.glob("*.md")):
        content = load_text_file(md_file)
        if content:
            KNOWLEDGE_PIECES.append(f"=== {md_file.name} ===\n{content}")

KNOWLEDGE_BLOCK = "\n\n".join(KNOWLEDGE_PIECES)

FULL_SYSTEM_PROMPT = (
    SYSTEM_PROMPT
    + "\n\n=== БАЗА ЗНАНИЙ КЛИНИКИ ===\n"
    + KNOWLEDGE_BLOCK
    + "\n\n=== ИНСТРУКЦИЯ ДЛЯ МОДЕЛИ ===\n"
    + "Отвечай строго на основе базы знаний выше. "
    + "Если информации недостаточно — скажи, что нужно уточнить у администратора по телефону +37412xxxx78. "
    + "Не выдумывай цены, сроки и медицинские диагнозы. "
    + "Если пациент хочет записаться — предложи ссылку на онлайн-календарь или номер телефона."
)

logger.info("System prompt loaded: %d chars", len(FULL_SYSTEM_PROMPT))

# ---------------------------------------------------------------------------
# Conversation history (in-memory)
# ---------------------------------------------------------------------------
user_histories: Dict[int, List[Dict[str, str]]] = {}

# ---------------------------------------------------------------------------
# LLM clients / setup
# ---------------------------------------------------------------------------
llm_client: Any = None

if LLM_PROVIDER == "anthropic":
    if not ANTHROPIC_API_KEY or ANTHROPIC_API_KEY.startswith("YOUR_"):
        logger.error("ANTHROPIC_API_KEY не задан в .env")
        sys.exit(1)
    try:
        import anthropic

        llm_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        logger.info("Anthropic client initialized")
    except Exception as exc:
        logger.error("Ошибка инициализации Anthropic: %s", exc)
        sys.exit(1)

elif LLM_PROVIDER == "openai":
    if not OPENAI_API_KEY or OPENAI_API_KEY.startswith("YOUR_"):
        logger.error("OPENAI_API_KEY не задан в .env")
        sys.exit(1)
    try:
        import openai

        llm_client = openai.AsyncOpenAI(api_key=OPENAI_API_KEY)
        logger.info("OpenAI client initialized")
    except Exception as exc:
        logger.error("Ошибка инициализации OpenAI: %s", exc)
        sys.exit(1)

elif LLM_PROVIDER == "ollama":
    try:
        import requests

        resp = requests.get(f"{OLLAMA_HOST}/api/tags", timeout=5)
        if resp.status_code == 200:
            models = [m.get("name", "") for m in resp.json().get("models", [])]
            logger.info("Ollama доступен. Модели: %s", models)
            if OLLAMA_MODEL not in models and not any(
                OLLAMA_MODEL in m for m in models
            ):
                logger.warning(
                    "Модель '%s' не найдена в Ollama. Выполните: ollama pull %s",
                    OLLAMA_MODEL,
                    OLLAMA_MODEL,
                )
        else:
            logger.warning("Ollama вернул статус %s", resp.status_code)
    except Exception as exc:
        logger.warning("Не удалось подключиться к Ollama (%s): %s", OLLAMA_HOST, exc)
        logger.warning("Убедитесь, что Ollama запущен: ollama serve")
    logger.info("Ollama provider ready (host=%s, model=%s)", OLLAMA_HOST, OLLAMA_MODEL)

else:
    logger.error(
        "Неизвестный LLM_PROVIDER: %s. Используйте anthropic, openai или ollama.",
        LLM_PROVIDER,
    )
    sys.exit(1)


# ---------------------------------------------------------------------------
# LLM call helpers
# ---------------------------------------------------------------------------
async def ask_anthropic(messages: List[Dict[str, str]]) -> str:
    loop = asyncio.get_event_loop()

    def _call():
        return llm_client.messages.create(
            model=ANTHROPIC_MODEL,
            system=FULL_SYSTEM_PROMPT,
            messages=messages,
            max_tokens=2048,
            temperature=0.7,
        )

    resp = await loop.run_in_executor(None, _call)
    return resp.content[0].text


async def ask_openai(messages: List[Dict[str, str]]) -> str:
    system_msg = {"role": "system", "content": FULL_SYSTEM_PROMPT}
    full_messages = [system_msg] + messages
    resp = await llm_client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=full_messages,
        max_tokens=2048,
        temperature=0.7,
    )
    return resp.choices[0].message.content or "Извините, не удалось сформировать ответ."


async def ask_ollama(messages: List[Dict[str, str]]) -> str:
    import requests

    system_msg = {"role": "system", "content": FULL_SYSTEM_PROMPT}
    full_messages = [system_msg] + messages

    payload = {
        "model": OLLAMA_MODEL,
        "messages": full_messages,
        "stream": False,
        "options": {
            "temperature": 0.7,
            "num_predict": 2048,
        },
    }

    loop = asyncio.get_event_loop()

    def _call():
        resp = requests.post(
            f"{OLLAMA_HOST}/api/chat",
            json=payload,
            timeout=120,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("message", {}).get("content", "")

    try:
        answer = await loop.run_in_executor(None, _call)
    except Exception as exc:
        logger.error("Ошибка Ollama: %s", exc)
        raise

    if not answer:
        return "Извините, не удалось получить ответ от модели. Попробуйте ещё раз."
    return answer


async def ask_llm(messages: List[Dict[str, str]]) -> str:
    if LLM_PROVIDER == "anthropic":
        return await ask_anthropic(messages)
    if LLM_PROVIDER == "openai":
        return await ask_openai(messages)
    return await ask_ollama(messages)


# ---------------------------------------------------------------------------
# Telegram handlers
# ---------------------------------------------------------------------------
WELCOME_TEXT = (
    "Здравствуйте! 👋\n\n"
    "Я — ассистент стоматологической клиники **STOM** в Гаваре.\n\n"
    "Чем могу помочь?\n"
    "• Отвечу на вопросы об услугах и подготовке\n"
    "• Подскажу цены и сроки процедур\n"
    "• Помогу с записью на приём\n\n"
    "📞 Телефон: +37412xxxx78\n"
    "🕘 Часы работы: 09:00–21:00\n\n"
    "Используйте команды:\n"
    "/help — справка по командам\n"
    "/calendar — онлайн-запись\n"
    "/prices — прайс-лист\n"
    "/contacts — контакты клиники"
)

HELP_TEXT = (
    "*Команды бота:*\n"
    "/start — приветствие и основная информация\n"
    "/help — эта справка\n"
    "/calendar — ссылка на онлайн-запись через Google Calendar\n"
    "/prices — отправить прайс-листом в чат\n"
    "/contacts — телефон, адрес, email\n\n"
    "Просто напишите вопрос — я отвечу на основе базы знаний клиники.\n"
    "Для срочных случаев (сильная боль, отёк, кровотечение) — звоните: +37498xxxx32"
)

CONTACTS_TEXT = (
    "*Клиника STOM*\n"
    "📍 Адрес: Gavar\n"
    "📞 Телефон: +37412xxxx78\n"
    "🚨 Экстренный: +37498xxxx32\n"
    "✉️ Email: your@emailxxxx.xx\n"
    "🕘 Часы работы: 09:00–21:00"
)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(WELCOME_TEXT, parse_mode="Markdown")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(HELP_TEXT, parse_mode="Markdown")


async def cmd_calendar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if GOOGLE_CALENDAR_URL:
        keyboard = [
            [InlineKeyboardButton("🗓 Записаться онлайн", url=GOOGLE_CALENDAR_URL)]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "Запишитесь на удобное время через календарь:",
            reply_markup=reply_markup,
        )
    else:
        await update.message.reply_text(
            "Онлайн-запись временно недоступна. Позвоните: +37412xxxxx78"
        )


async def cmd_prices(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    prices_path = KNOWLEDGE_DIR / "prices.md"
    if prices_path.exists():
        text = prices_path.read_text(encoding="utf-8")
        chunks = [text[i : i + 4000] for i in range(0, len(text), 4000)]
        for idx, chunk in enumerate(chunks):
            header = "*Прайс-лист STOM*\n\n" if idx == 0 else ""
            await update.message.reply_text(header + chunk, parse_mode="Markdown")
    else:
        await update.message.reply_text(
            "Прайс-лист временно недоступен. Уточните цены по телефону: +37412xxxxx78"
        )


async def cmd_contacts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(CONTACTS_TEXT, parse_mode="Markdown")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    user_id = user.id
    text = update.message.text

    logger.info(
        "Message from %s (%s): %s", user_id, user.username or "no_username", text[:100]
    )

    if user_id not in user_histories:
        user_histories[user_id] = []

    user_histories[user_id].append({"role": "user", "content": text})

    if len(user_histories[user_id]) > MAX_HISTORY:
        user_histories[user_id] = user_histories[user_id][-MAX_HISTORY:]

    try:
        reply = await ask_llm(user_histories[user_id])
    except Exception as exc:
        logger.exception("LLM error")
        reply = (
            "Извините, произошла ошибка при обработке запроса. "
            "Пожалуйста, позвоните в клинику: +37412xxxx78"
        )

    user_histories[user_id].append({"role": "assistant", "content": reply})

    if len(user_histories[user_id]) > MAX_HISTORY:
        user_histories[user_id] = user_histories[user_id][-MAX_HISTORY:]

    await update.message.reply_text(reply)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    logger.info("Starting STOM bot... Provider=%s", LLM_PROVIDER)
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("calendar", cmd_calendar))
    app.add_handler(CommandHandler("prices", cmd_prices))
    app.add_handler(CommandHandler("contacts", cmd_contacts))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("Bot polling started")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
