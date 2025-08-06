"""Telegram bot for Alberta Class 4 driver’s licence practice.

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

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, InputFile
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

LANG_OPTIONS = [
    [
        InlineKeyboardButton("🇬🇧 English", callback_data="lang_en"),
        InlineKeyboardButton("🇬🇧 English + 🇺🇦 Українська", callback_data="lang_bilingual"),
    ]
]

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Please choose your language / Будь ласка, оберіть мову:",
        reply_markup=InlineKeyboardMarkup(LANG_OPTIONS)
    )

async def handle_language(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    await query.answer()
    lang_mode = "en" if query.data == "lang_en" else "bilingual"
    context.chat_data["lang_mode"] = lang_mode
    context.chat_data["current_index"] = 0
    context.chat_data["score"] = 0
    await send_question(update.effective_chat.id, context)

def build_option_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("A", callback_data="A"),
            InlineKeyboardButton("B", callback_data="B"),
            InlineKeyboardButton("C", callback_data="C"),
            InlineKeyboardButton("D", callback_data="D"),
        ]
    ])

def build_next_stop_keyboard(lang_mode: str) -> InlineKeyboardMarkup:
    if lang_mode == "bilingual":
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton("Next question / Наступне питання", callback_data="NEXT"),
                InlineKeyboardButton("🛑 Stop", callback_data="STOP")
            ]
        ])
    else:
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton("Next question", callback_data="NEXT"),
                InlineKeyboardButton("🛑 Stop", callback_data="STOP")
            ]
        ])

async def send_question(chat_id: int, context: CallbackContext) -> None:
    chat_data = context.chat_data
    index = chat_data.get("current_index", 0)
    lang_mode = chat_data.get("lang_mode", "en")

    if index >= len(QUESTIONS):
        await send_score(chat_id, context)
        return

    q = QUESTIONS[index]
    lines = [f"<i><b>Question {index + 1} of {len(QUESTIONS)}</b></i>", ""]

    if lang_mode == "bilingual":
        lines.append(f"<b>🇬🇧 {q['question_en']}</b>")
        lines.append(f"<b>🇺🇦 {q['question_uk']}</b>")
    else:
        lines.append(f"<b>{q['question_en']}</b>")

    lines.append("------------------------------")

    option_labels = ["A", "B", "C", "D"]
    for idx, opt in enumerate(q["options"]):
        lines.append(f"<b>{option_labels[idx]}</b>. {opt}")

    image_path_jpg = f"images/{index + 1}.jpg"
    image_path_png = f"images/{index + 1}.png"

    text = "\n".join(lines)

    if os.path.exists(image_path_jpg):
        with open(image_path_jpg, "rb") as photo:
            await context.bot.send_photo(chat_id=chat_id, photo=photo)
    elif os.path.exists(image_path_png):
        with open(image_path_png, "rb") as photo:
            await context.bot.send_photo(chat_id=chat_id, photo=photo)

    await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode=ParseMode.HTML,
        reply_markup=build_option_keyboard()
    )

async def send_score(chat_id: int, context: CallbackContext) -> None:
    chat_data = context.chat_data
    score = chat_data.get("score", 0)
    total = len(QUESTIONS)
    text = (
        f"<b>🎉 You scored {score} out of {total}!</b><br/>"
        "Type /quiz to try again.<br/><br/>"
        f"<b>🇺🇦 Ви набрали {score} із {total} балів!</b><br/>"
        "Наберіть /quiz, щоб спробувати ще раз."
    )
    await context.bot.send_message(chat_id=chat_id, text=text, parse_mode=ParseMode.HTML)
    chat_data.clear()

async def quiz_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_data = context.chat_data
    chat_data["current_index"] = 0
    chat_data["score"] = 0
    await send_question(update.effective_chat.id, context)

async def answer_handler(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    await query.answer()
    chat_data = context.chat_data

    if not chat_data:
        await query.edit_message_text("Please start the quiz with /quiz.\n\n🇺🇦 Будь ласка, почніть тест, використовуючи /quiz.")
        return

    current_index = chat_data.get("current_index", 0)
    lang_mode = chat_data.get("lang_mode", "en")
    option_map: Dict[str, int] = {"A": 0, "B": 1, "C": 2, "D": 3}
    selected_letter = query.data
    selected_index = option_map.get(selected_letter, -1)

    if selected_letter == "STOP":
        await query.edit_message_text("🛑 Test stopped." if lang_mode == "en" else "🛑 Тест зупинено.")
        chat_data.clear()
        return

    if current_index < len(QUESTIONS) and 0 <= selected_index < 4:
        question = QUESTIONS[current_index]
        correct_index = question["answer_index"]
        is_correct = selected_index == correct_index

        if is_correct:
            chat_data["score"] = chat_data.get("score", 0) + 1

        option_labels = ["A", "B", "C", "D"]
        options_text = []
        for idx, opt in enumerate(question["options"]):
            emoji = ""
            if idx == correct_index:
                emoji = "✅"
            elif idx == selected_index:
                emoji = "❌"
            options_text.append(f"<b>{option_labels[idx]}</b>. {emoji} {opt}" if emoji else f"<b>{option_labels[idx]}</b>. {opt}")

        result_title = f"<i><b>Question {current_index + 1} of {len(QUESTIONS)} ({'✅ Correct!' if is_correct else '❌ Incorrect!'})</b></i>"
        full_text = [result_title, ""]

        if lang_mode == "bilingual":
            full_text += [
                f"<b>🇬🇧 {question['question_en']}</b>",
                f"<b>🇺🇦 {question['question_uk']}</b>"
            ]
        else:
            full_text.append(f"<b>{question['question_en']}</b>")

        full_text.append("------------------------------")
        full_text += options_text
        full_text.append("------------------------------")
        full_text.append("<b>Explanation:</b>")
        full_text.append(f"<i>{'🇬🇧 ' if lang_mode == 'bilingual' else ''}{question['explanation_en']}</i>")
        if lang_mode == "bilingual":
            full_text.append(f"<i>🇺🇦 {question['explanation_uk']}</i>")

        keyboard = build_next_stop_keyboard(lang_mode)

        await query.edit_message_text(
            text="\n".join(full_text),
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML
        )
        chat_data["awaiting_next"] = True
    else:
        await query.edit_message_text("Invalid selection. Please try again.\n\nНеправильний вибір. Спробуйте ще раз.")

async def next_handler(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    await query.answer()
    chat_data = context.chat_data
    if not chat_data or not chat_data.get("awaiting_next"):
        await query.edit_message_text(
            "To start the quiz press /start.\n\n🇺🇦 Щоб почати тест натисніть /start."
        )
        return
    current_index = chat_data.get("current_index", 0) + 1
    chat_data["current_index"] = current_index
    chat_data.pop("awaiting_next", None)
    if current_index < len(QUESTIONS):
        await send_question(update.effective_chat.id, context)
    else:
        await send_score(update.effective_chat.id, context)

def main() -> None:
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("The BOT_TOKEN environment variable is not set.")

    application = ApplicationBuilder().token(token).defaults(
        Defaults(parse_mode=ParseMode.MARKDOWN)
    ).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("quiz", quiz_command))
    application.add_handler(CallbackQueryHandler(next_handler, pattern="^NEXT$"))
    application.add_handler(CallbackQueryHandler(answer_handler, pattern="^[ABCDSTOP]{1,4}$"))
    application.add_handler(CallbackQueryHandler(handle_language, pattern="^lang_.*$"))

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
