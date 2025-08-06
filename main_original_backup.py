"""Telegram bot for Alberta Class 4 driver’s licence practice.

This bot asks 10 multiple‑choice questions about traffic rules and safe driving.
Each question is bilingual (English and Ukrainian) and uses inline keyboard
buttons for answer selection.  User progress and score are stored in
``context.chat_data`` so that each user can take the quiz independently.

To run locally, export your Telegram token in the environment variable
``BOT_TOKEN`` and execute ``python main.py``.  The bot uses long polling via
``run_polling``.
"""

import logging
import os
import json

from typing import Dict

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CallbackContext,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    Defaults,
)

with open("questions.json", "r", encoding="utf-8") as f:
    QUESTIONS = json.load(f)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the /start command by greeting the user."""
    welcome_message = (
        "👋 Welcome to the Alberta Class 4 Driver’s Licence Test Bot!\n\n"
        "This bot will quiz you on safe driving practices and traffic rules.\n"
        "Type /quiz to start the practice test.\n\n"
        "🇺🇦 Ласкаво просимо до бота для підготовки до іспиту на комерційне водійське "
        "посвідчення (Class 4) в Альберті!\n"
        "Цей бот перевірить ваші знання правил дорожнього руху. "
        "Наберіть /quiz, щоб розпочати тест."
    )
    await update.message.reply_text(welcome_message)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Provide assistance on how to use the bot."""
    help_text = (
        "Use /start to see the welcome message again.\n"
        "Use /quiz to begin a 10‑question multiple‑choice test.\n\n"
        "🇺🇦 Скористайтеся /start, щоб знову побачити привітання.\n"
        "Скористайтеся /quiz, щоб почати 10‑питаньний тест."
    )
    await update.message.reply_text(help_text)


def build_option_keyboard() -> InlineKeyboardMarkup:
    """Return a keyboard with four answer buttons labelled A–D."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("A", callback_data="A"),
                InlineKeyboardButton("B", callback_data="B"),
                InlineKeyboardButton("C", callback_data="C"),
                InlineKeyboardButton("D", callback_data="D"),
            ]
        ]
    )


async def send_question(chat_id: int, context: CallbackContext) -> None:
    """Send the current question to the given chat."""
    chat_data = context.chat_data
    index = chat_data.get("current_index", 0)
    if index >= len(QUESTIONS):
        await send_score(chat_id, context)
        return

    q = QUESTIONS[index]
    # Construct the message with both languages and numbered options
    lines = [
        f"**Question {index + 1} of {len(QUESTIONS)}**",
        f"**🇬🇧 {q['question_en']}**",
        f"**🇺🇦 {q['question_uk']}**",
    ]
    # Append options labelled A–D
    option_labels = ["A", "B", "C", "D"]
    for idx, opt in enumerate(q["options"]):
        lines.append(f"{option_labels[idx]}. {opt}")

    text = "\n".join(lines)
    await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=build_option_keyboard(),
    )


async def send_score(chat_id: int, context: CallbackContext) -> None:
    """Send the final score to the user and offer to restart."""
    chat_data = context.chat_data
    score = chat_data.get("score", 0)
    total = len(QUESTIONS)
    text = (
        f"🎉 You scored {score} out of {total}!\n"
        "Type /quiz to try again.\n\n"
        f"🇺🇦 Ви набрали {score} із {total} балів!\n"
        "Наберіть /quiz, щоб спробувати ще раз."
    )
    await context.bot.send_message(chat_id=chat_id, text=text)
    # Reset progress so user can restart seamlessly
    chat_data.clear()


async def quiz_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start or restart the quiz."""
    chat_data = context.chat_data
    chat_data["current_index"] = 0
    chat_data["score"] = 0
    await update.message.reply_text(
        "📝 Starting the quiz...\n\n🇺🇦 Розпочинаємо тест..."
    )
    await send_question(update.effective_chat.id, context)


async def answer_handler(update: Update, context: CallbackContext) -> None:
    """Handle answer selection via callback queries with full question review."""
    query = update.callback_query
    await query.answer()
    chat_data = context.chat_data

    if not chat_data:
        await query.edit_message_text("Please start the quiz with /quiz.\n\n🇺🇦 Будь ласка, почніть тест, використовуючи /quiz.")
        return

    current_index = chat_data.get("current_index", 0)
    option_map: Dict[str, int] = {"A": 0, "B": 1, "C": 2, "D": 3}
    selected_letter = query.data
    selected_index = option_map.get(selected_letter, -1)

    if current_index < len(QUESTIONS) and 0 <= selected_index < 4:
        question = QUESTIONS[current_index]
        correct_index = question["answer_index"]
        is_correct = selected_index == correct_index

        if is_correct:
            chat_data["score"] = chat_data.get("score", 0) + 1

        # Construct full message with answer markers
        option_labels = ["A", "B", "C", "D"]
        options_text = []
        for idx, opt in enumerate(question["options"]):
            emoji = ""
            if idx == correct_index:
                emoji = "✅"
            elif idx == selected_index:
                emoji = "❌"
            options_text.append(f"{option_labels[idx]}. {emoji} {opt}" if emoji else f"{option_labels[idx]}. {opt}")

        full_text = [
          f"{'✅ Correct!' if is_correct else '❌ Incorrect!'}\n",
          f"**Question {current_index + 1} of {len(QUESTIONS)}**",
          f"**🇬🇧 {question['question_en']}**",
          f"**🇺🇦 {question['question_uk']}**\n",
          *options_text,
          "\n*Explanation:*",
          f"*🇬🇧 {question['explanation_en']}*",
          f"*🇺🇦 {question['explanation_uk']}*",
        ]

        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("Next question / Наступне питання", callback_data="NEXT")
        ]])

        await query.edit_message_text(
            text="\n".join(full_text),
            reply_markup=keyboard,
            parse_mode=ParseMode.MARKDOWN
        )
        chat_data["awaiting_next"] = True
    else:
        await query.edit_message_text("Invalid selection. Please try again.\n\nНеправильний вибір. Спробуйте ще раз.")


async def next_handler(update: Update, context: CallbackContext) -> None:
    """Handle the 'Next question' button press."""
    query = update.callback_query
    await query.answer()
    chat_data = context.chat_data
    if not chat_data or not chat_data.get("awaiting_next"):
        await query.edit_message_text(
            "Please start the quiz with /quiz.\n\n🇺🇦 Будь ласка, почніть тест, використовуючи /quiz."
        )
        return
    # Advance to next question
    current_index = chat_data.get("current_index", 0)
    current_index += 1
    chat_data["current_index"] = current_index
    # Remove awaiting_next flag
    chat_data.pop("awaiting_next", None)
    # If there are more questions, send the next one; otherwise show score
    if current_index < len(QUESTIONS):
        await send_question(update.effective_chat.id, context)
    else:
        await send_score(update.effective_chat.id, context)


def main() -> None:
    """Run the bot with webhook on Render."""
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("The BOT_TOKEN environment variable is not set.")

    application = ApplicationBuilder().token(token).defaults(
        Defaults(parse_mode=ParseMode.MARKDOWN)
    ).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("quiz", quiz_command))
    application.add_handler(CallbackQueryHandler(next_handler, pattern="^NEXT$"))
    application.add_handler(CallbackQueryHandler(answer_handler, pattern="^[ABCD]$"))

    port = int(os.environ.get("PORT", 10000))
    render_url = os.environ.get("RENDER_EXTERNAL_URL")
    if not render_url:
        raise RuntimeError("RENDER_EXTERNAL_URL is not set. Make sure your environment provides it.")

    application.run_webhook(
        listen="0.0.0.0",
        port=port,
        webhook_url=f"{render_url}"
    )


if __name__ == "__main__":
    main()