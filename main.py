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

from telegram import BotCommand
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

async def post_init(application):
    commands = [
        BotCommand("start", "Start the quiz"),
        BotCommand("stop", "Stop the quiz"),
        BotCommand("pause", "Pause the quiz")
    ]
    await application.bot.set_my_commands(commands)

MODE_OPTIONS = [
    [
        InlineKeyboardButton("🧠 Learning Mode", callback_data="mode_learning"),
        InlineKeyboardButton("📝 Exam Mode", callback_data="mode_exam"),
    ]
]

LANG_OPTIONS = [
    [
        InlineKeyboardButton("🇬🇧 English", callback_data="lang_en"),
        InlineKeyboardButton("🇬🇧 English + 🇺🇦 Українська", callback_data="lang_bilingual"),
    ]
]

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # If paused, add Continue button
    lang_options = LANG_OPTIONS.copy()
    if context.chat_data.get("paused"):
        lang_options.append([InlineKeyboardButton("▶️ Continue", callback_data="RESUME_PAUSE")])
    # Add Main Menu button
    lang_options.append([InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")])
    await update.effective_chat.send_message(
        "Please choose your language / Будь ласка, оберіть мову:",
        reply_markup=InlineKeyboardMarkup(lang_options)
    )
async def handle_main_menu(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    import telegram.error
    try:
        await query.answer()
    except telegram.error.BadRequest as e:
        if "Query is too old" in str(e):
            logger.warning("Callback query too old; skipping answer.")
            return
        else:
            raise
    context.chat_data.clear()
    await start(update, context)

async def handle_language(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    import telegram.error
    try:
        await query.answer()
    except telegram.error.BadRequest as e:
        if "Query is too old" in str(e):
            logger.warning("Callback query too old; skipping answer.")
        else:
            raise
    lang_mode = "en" if query.data == "lang_en" else "bilingual"
    context.chat_data["lang_mode"] = lang_mode
    context.chat_data["current_index"] = 0
    context.chat_data["score"] = 0
    # New logic for text assignment based on lang_mode
    if lang_mode == "bilingual":
        text = (
            "🧠 <b>Learning Mode</b> – shows the correct answer and explanation immediately after each question. Includes all 120 questions.\n"
            "🧠 <b>Навчальний режим</b> – показує правильну відповідь і пояснення одразу після кожного питання. Усього 120 питань.\n\n"
            "📝 <b>Exam Mode</b> – 30 random questions, no hints. You must answer at least 25 correctly to pass.\n"
            "📝 <b>Режим іспиту</b> – 30 випадкових питань, без підказок. Для успішного складання потрібно дати щонайменше 25 правильних відповідей.\n\n"
            "Please choose mode / Будь ласка, оберіть режим:"
        )
    elif lang_mode == "en":
        text = (
            "🧠 <b>Learning Mode</b> – shows the correct answer and explanation immediately after each question. Includes all 120 questions.\n\n"
            "📝 <b>Exam Mode</b> – 30 random questions, no hints. You must answer at least 25 correctly to pass.\n\n"
            "Please choose a mode:"
        )
    elif lang_mode == "learning":
        text = (
            "🧠 <b>Learning Mode</b> – shows the correct answer and explanation immediately after each question. Includes all 120 questions."
        )
    elif lang_mode == "exam":
        text = (
            "📝 <b>Exam Mode</b> – 30 random questions, no hints. You must answer at least 25 correctly to pass."
        )
    else:
        text = "Please choose a mode:"

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(MODE_OPTIONS),
        parse_mode=ParseMode.HTML
    )

async def handle_mode(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    import telegram.error
    try:
        await query.answer()
        await query.edit_message_reply_markup(reply_markup=None)
    except telegram.error.BadRequest as e:
        if "Query is too old" in str(e):
            logger.warning("Callback query too old; skipping answer.")
        else:
            raise
    mode = "learning" if query.data == "mode_learning" else "exam"
    context.chat_data["mode"] = mode
    context.chat_data["current_index"] = 0
    context.chat_data["score"] = 0
    context.chat_data["paused"] = False
    # Reset used_questions only on new exam start
    if mode == "exam":
        import random
        if len(QUESTIONS) < 30:
            await query.edit_message_text("❌ Not enough questions to start the exam. Please add more questions.")
            return
        sample = random.sample(range(len(QUESTIONS)), 30)
        context.chat_data["exam_questions"] = sample
        context.chat_data["used_questions"] = []

    # Show only selected mode's description after setting mode
    lang = context.chat_data.get("lang_mode", "en")
    selected_mode = mode
    if lang == "en":
        await query.edit_message_text(
            "📝 <b>Exam Mode</b> – 30 random questions, no hints. You must answer at least 25 correctly to pass."
            if selected_mode == "exam"
            else "🧠 <b>Learning Mode</b> – shows the correct answer and explanation immediately after each question. Includes all 120 questions.",
            parse_mode=ParseMode.HTML
        )
    elif lang == "bilingual":
        await query.edit_message_text(
            "📝 <b>Exam Mode</b> – 30 random questions, no hints. You must answer at least 25 correctly to pass.\n"
            "📝 <b>Режим іспиту</b> – 30 випадкових питань, без підказок. Для успішного складання потрібно дати щонайменше 25 правильних відповідей."
            if selected_mode == "exam"
            else "🧠 <b>Learning Mode</b> – shows the correct answer and explanation immediately after each question. Includes all 120 questions.\n"
                 "🧠 <b>Навчальний режим</b> – показує правильну відповідь і пояснення одразу після кожного питання. Усього 120 питань.",
            parse_mode=ParseMode.HTML
        )

    await send_question(query.message.chat.id, context)

def build_option_keyboard() -> InlineKeyboardMarkup:
    # Buttons show plain letters; labels in question text are bolded
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("A", callback_data="A"),
            InlineKeyboardButton("B", callback_data="B"),
            InlineKeyboardButton("C", callback_data="C"),
            InlineKeyboardButton("D", callback_data="D")
        ]
    ])


async def send_question(chat_id: int, context: CallbackContext) -> None:
    chat_data = context.chat_data
    index = chat_data.get("current_index", 0)
    lang_mode = chat_data.get("lang_mode", "en")

    # Do not remove previous inline keyboard here to avoid UI flicker.

    mode = chat_data.get("mode", "learning")
    if mode == "exam":
        exam_questions = chat_data.get("exam_questions", [])
        # used_questions as a list for persistence
        used_questions = chat_data.get("used_questions", [])
        used_ids = set(used_questions)
        # Exclude used questions
        remaining_questions = [qidx for qidx in exam_questions if qidx not in used_ids]
        if not remaining_questions:
            await send_score(chat_id, context)
            return
        # Find the next question index to ask
        # Use current_index to preserve ordering, but skip used
        # Find the first not-yet-used question at or after current_index
        next_qidx = None
        for i in range(chat_data.get("current_index", 0), len(exam_questions)):
            if exam_questions[i] not in used_ids:
                next_qidx = exam_questions[i]
                chat_data["current_index"] = i
                break
        if next_qidx is None:
            # If all questions from current_index are used, try from beginning
            for i, qidx in enumerate(exam_questions):
                if qidx not in used_ids:
                    next_qidx = qidx
                    chat_data["current_index"] = i
                    break
        if next_qidx is None:
            await send_score(chat_id, context)
            return
        # Add this question to used_questions
        chat_data.setdefault("used_questions", []).append(next_qidx)
        q = QUESTIONS[next_qidx]
    else:
        if index >= len(QUESTIONS):
            await send_score(chat_id, context)
            return
        q = QUESTIONS[index]

    total_questions = len(chat_data.get("exam_questions", [])) if mode == 'exam' else len(QUESTIONS)
    lines = [f"<i><b>Question {index + 1} of {total_questions}</b></i>", ""]

    if lang_mode == "bilingual":
        lines.append(f"<b>🇬🇧 {q['question']}</b>")
        lines.append(f"<b>🇺🇦 {q['question_uk']}</b>")
    else:
        # If mode is 'en', do not show the 🇬🇧 flag
        if lang_mode == "en":
            lines.append(f"<b>{q['question']}</b>")
        else:
            lines.append(f"<b>🇬🇧 {q['question']}</b>")

    lines.append("------------------------------")

    option_labels = ["A", "B", "C", "D"]
    options_en = q["options"]
    options_uk = q.get("options_uk", [])

    for idx, label in enumerate(option_labels):
        if lang_mode == "bilingual" and options_uk:
            lines.append(f"       <b>{label}.</b> {options_en[idx]} / {options_uk[idx]}")
        else:
            lines.append(f"       <b>{label}.</b> {options_en[idx]}")

    text = "\n".join(lines)
    keyboard = build_option_keyboard()

    # --- New image sending logic before sending question text ---
    import os
    question_number = q.get("question_number")
    possible_extensions = [".jpg", ".jpeg", ".png", ".webp"]
    image_path = None
    if question_number is not None:
        for ext in possible_extensions:
            filename = f"images/{question_number}{ext}"
            if os.path.exists(filename):
                image_path = filename
                break
    if image_path:
        await context.bot.send_photo(chat_id=chat_id, photo=open(image_path, "rb"))

    # Send the question text and options (image will appear above)
    msg = await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard
    )
    context.chat_data["last_message_id"] = msg.message_id

async def send_score(chat_id: int, context: CallbackContext) -> None:
    chat_data = context.chat_data
    score = chat_data.get("score", 0)
    total = len(chat_data.get("exam_questions", [])) if chat_data.get("mode", "learning") == "exam" else len(QUESTIONS)
    if chat_data.get("mode", "learning") == "exam":
        passed = score >= 25
        result_en = "✅ You passed the exam!" if passed else "❌ You did not pass the exam."
        result_uk = "✅ Ви склали іспит!" if passed else "❌ Ви не склали іспит."

        text = (
            f"<b>🎉 You scored {score} out of {total}!</b>\n"
            f"{result_en}\n\n"
            f"<b>🇺🇦 Ви набрали {score} із {total} балів!</b>\n"
            f"{result_uk}\n\n"
            "Type /start to try again.\n"
            "Наберіть /start, щоб спробувати ще раз."
        )
    else:
        text = (
            f"<b>🎉 You scored {score} out of {total}!</b><br/>"
            "Type /quiz to try again.<br/><br/>"
            f"<b>🇺🇦 Ви набрали {score} із {total} балів!</b><br/>"
            "Наберіть /quiz, щоб спробувати ще раз."
        )
    await context.bot.send_message(chat_id=chat_id, text=text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔁 Start Again", callback_data="mode_exam")]]))
    chat_data.clear()

async def quiz_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Reset state and behave exactly like /start
    context.chat_data.clear()
    await start(update, context)

async def answer_handler(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    import telegram.error
    try:
        await query.answer()
    except telegram.error.BadRequest as e:
        if "Query is too old" in str(e):
            logger.warning("Callback query too old; skipping answer.")
        else:
            raise
    chat_data = context.chat_data

    if not chat_data:
        if query.message:
            await query.edit_message_text(
                "⏸ Quiz was interrupted. Resuming from last question...",
                reply_markup=None
            )
            context.chat_data["current_index"] = 0
            context.chat_data["score"] = 0
            context.chat_data["mode"] = "exam"
            context.chat_data["paused"] = False
            lang_mode = context.chat_data.get("lang_mode", "en")
            if lang_mode not in ("en", "bilingual"):
                context.chat_data["lang_mode"] = "en"
            if "exam_questions" not in context.chat_data:
                import random
                if len(QUESTIONS) < 30:
                    await query.edit_message_text("❌ Not enough questions to resume exam. Please add more questions.")
                    return
                sample = random.sample(range(len(QUESTIONS)), 30)
                context.chat_data["exam_questions"] = sample
            await send_question(query.message.chat.id, context)
        return

    mode = chat_data.get("mode", "learning")
    if mode == "exam" and "exam_questions" not in chat_data:
        if query.message:
            await query.edit_message_text(
                "❌ Exam data missing.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔁 Start Again", callback_data="mode_exam")]])
            )
        return

    # Do not remove previous inline keyboard here to avoid UI flicker.

    current_index = chat_data.get("current_index", 0)
    if mode == "exam":
        # Find the last asked question index (the one just presented)
        used_questions = chat_data.get("used_questions", [])
        if used_questions:
            question_index = used_questions[-1]
        else:
            question_index = chat_data["exam_questions"][current_index]
    else:
        question_index = current_index
    lang_mode = chat_data.get("lang_mode", "en")
    option_map: Dict[str, int] = {"A": 0, "B": 1, "C": 2, "D": 3}
    selected_letter = query.data
    selected_index = option_map.get(selected_letter, -1)

    if selected_letter == "STOP":
        current = chat_data.get("current_index", 0)
        total = 30 if mode == "exam" else len(QUESTIONS)
        progress_text = f"Progress: {current} out of {total} questions completed." if lang_mode == "en" else f"Пройдено: {current} з {total} питань."

        stop_text = (
            "🛑 Test stopped.\n\n" + progress_text + "\n\n"
            "Would you like to continue or restart?" if lang_mode == "en"
            else "🛑 Тест зупинено.\n\n" + progress_text + "\n\n"
            "Бажаєте продовжити чи почати спочатку?"
        )

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("▶️ Continue", callback_data="CONTINUE"),
                InlineKeyboardButton("🔁 Restart", callback_data="RESTART")
            ] if lang_mode == "en" else [
                InlineKeyboardButton("▶️ Продовжити", callback_data="CONTINUE"),
                InlineKeyboardButton("🔁 Почати спочатку", callback_data="RESTART")
            ]
        ])

        if query.message and query.message.text:
            await query.edit_message_text(stop_text, reply_markup=keyboard)

        chat_data["awaiting_next"] = True
        chat_data["resume_question"] = chat_data.get("current_index", 0)
        return

    max_questions = 30 if mode == "exam" else len(QUESTIONS)
    if current_index < max_questions and 0 <= selected_index < 4:
        question = QUESTIONS[question_index]
        correct_index = question["answer_index"]
        is_correct = selected_index == correct_index

        if is_correct:
            chat_data["score"] = chat_data.get("score", 0) + 1

        option_labels = ["A", "B", "C", "D"]
        options_en = question["options"]
        options_uk = question.get("options_uk", [])
        options_text = []

        # Format options per requirements:
        # - If selected and incorrect: ❌ <b>A. text</b>
        # - If selected and correct: ✅ <b>A. text</b>
        # - If correct but not selected: ✅ A. text
        # - All others: unformatted
        for idx, opt_en in enumerate(options_en):
            opt_uk = options_uk[idx] if lang_mode == "bilingual" and options_uk else ""
            line = f"{opt_en}" if not opt_uk else f"{opt_en} / {opt_uk}"
            option_letter = option_labels[idx]
            if idx == selected_index and idx != correct_index:
                # Selected and incorrect
                options_text.append(f"❌ <b>{option_letter}. {line}</b>")
            elif idx == selected_index and idx == correct_index:
                # Selected and correct
                options_text.append(f"✅ <b>{option_letter}. {line}</b>")
            elif idx == correct_index:
                # Correct but not selected
                options_text.append(f"✅ {option_letter}. {line}")
            else:
                # All others
                options_text.append(f"       {option_letter}. {line}")

        total_questions = 30 if mode == 'exam' else len(QUESTIONS)
        fail_count = current_index + 1 - chat_data.get("score", 0)
        # --- Insert fail fast logic for exam mode ---
        if mode == "exam" and fail_count >= 6:
            text = (
                f"<b>❌ You made {fail_count} mistakes. Test failed.</b>\n\n"
                f"<b>🇺🇦 Ви зробили {fail_count} помилок. Тест не складено.</b>\n\n"
            )
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔁 Try Again / Спробувати ще раз", callback_data="mode_exam")]
            ])
            await query.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
            chat_data.clear()
            return
        # --- End fail fast logic ---
        result_title = f"<i><b>Question {current_index + 1} of {total_questions} ({fail_count} Fails)</b></i>"
        full_text = [result_title, ""]

        if lang_mode == "bilingual":
            full_text += [
                f"<b>🇬🇧 {question['question']}</b>",
                f"<b>🇺🇦 {question['question_uk']}</b>"
            ]
        else:
            if lang_mode == "en":
                full_text.append(f"<b>{question['question']}</b>")
            else:
                full_text.append(f"<b>🇬🇧 {question['question']}</b>")

        full_text.append("------------------------------")
        full_text += options_text
        full_text.append("------------------------------")
        full_text.append("<b>Explanation:</b>")
        full_text.append(f"<i>{'🇬🇧 ' if lang_mode == 'bilingual' else ''}{question['explanation_en']}</i>")
        if lang_mode == "bilingual":
            full_text.append(f"<i>🇺🇦 {question['explanation_uk']}</i>")
        formatted_question = "\n".join(full_text)
        # --- New image sending logic: find by question_number and possible extensions ---
        import os
        question_number = question.get("question_number")
        possible_extensions = [".jpg", ".jpeg", ".png", ".webp"]
        image_path = None
        if question_number is not None:
            for ext in possible_extensions:
                filename = f"images/{question_number}{ext}"
                if os.path.exists(filename):
                    image_path = filename
                    break

        # Show result and explanation, then automatically move to next question
        if image_path:
            try:
                import telegram
                with open(image_path, "rb") as photo:
                    await context.bot.edit_message_media(
                        chat_id=query.message.chat.id,
                        message_id=query.message.message_id,
                        media=telegram.InputMediaPhoto(photo, caption=formatted_question, parse_mode=ParseMode.HTML),
                        reply_markup=None
                    )
                context.chat_data["last_message_id"] = query.message.message_id
            except Exception as e:
                logger.warning(f"Failed to edit photo, fallback to delete/send: {e}")
                if query.message:
                    await context.bot.delete_message(chat_id=query.message.chat.id, message_id=query.message.message_id)
                with open(image_path, "rb") as photo:
                    msg = await context.bot.send_photo(
                        chat_id=query.message.chat.id,
                        photo=photo,
                        caption=formatted_question,
                        parse_mode=ParseMode.HTML,
                        reply_markup=None
                    )
                context.chat_data["last_message_id"] = msg.message_id
        else:
            if query.message and query.message.text:
                msg = await query.edit_message_text(
                    text=formatted_question,
                    reply_markup=None,
                    parse_mode=ParseMode.HTML
                )
                context.chat_data["last_message_id"] = msg.message_id
        # Automatically proceed to next question after showing result
        import asyncio
        await asyncio.sleep(1.0)
        chat_data["current_index"] = chat_data.get("current_index", 0) + 1
        chat_data.pop("awaiting_next", None)
        max_questions = len(chat_data.get("exam_questions", [])) if chat_data.get("mode", "learning") == "exam" else len(QUESTIONS)
        if chat_data["current_index"] < max_questions:
            await send_question(query.message.chat.id, context)
        else:
            await send_score(query.message.chat.id, context)
        return
    else:
        if query.message and query.message.text:
            await query.edit_message_text("Invalid selection. Please try again.\n\nНеправильний вибір. Спробуйте ще раз.")

async def next_handler(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    import telegram.error
    try:
        await query.answer()
    except telegram.error.BadRequest as e:
        if "Query is too old" in str(e):
            logger.warning("Callback query too old; skipping answer.")
        else:
            raise
    chat_data = context.chat_data
    if not chat_data or not chat_data.get("awaiting_next"):
        if query.message:
            await query.edit_message_text(
                "❗️Quiz not active. Please start again.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔁 Start Again", callback_data="mode_exam")]])
            )
        return
    if query.data == "RESTART":
        chat_data.clear()
        await query.edit_message_reply_markup(reply_markup=None)
        await context.bot.send_message(chat_id=update.effective_chat.id, text="🔁 Restarting test...")
        await start(update, context)
        return
    if query.data == "CONTINUE":
        resume_index = chat_data.pop("resume_question", 0)
        chat_data["current_index"] = resume_index
        chat_data.pop("awaiting_next", None)
        await send_question(update.effective_chat.id, context)
        return
    # For all other cases, immediately move to next question (no NEXT button logic)
    current_index = chat_data.get("current_index", 0) + 1
    chat_data["current_index"] = current_index
    chat_data.pop("awaiting_next", None)
    max_questions = len(chat_data.get("exam_questions", [])) if chat_data.get("mode", "learning") == "exam" else len(QUESTIONS)
    if current_index < max_questions:
        await send_question(update.effective_chat.id, context)
    else:
        await send_score(update.effective_chat.id, context)


def main() -> None:
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("The BOT_TOKEN environment variable is not set.")

    application = ApplicationBuilder().token(token).defaults(
        Defaults(parse_mode=ParseMode.MARKDOWN)
    ).post_init(post_init).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("pause", handle_pause))
    application.add_handler(CallbackQueryHandler(next_handler, pattern="^(NEXT|CONTINUE|RESTART)$"))
    application.add_handler(CallbackQueryHandler(answer_handler, pattern="^[ABCDSTOP]{1,4}$"))
    application.add_handler(CallbackQueryHandler(handle_language, pattern="^lang_.*$"))
    application.add_handler(CallbackQueryHandler(handle_mode, pattern="^mode_.*$"))
    application.add_handler(CallbackQueryHandler(handle_pause, pattern="^mode_pause$"))
    application.add_handler(CallbackQueryHandler(handle_resume_pause, pattern="^RESUME_PAUSE$"))
    application.add_handler(CallbackQueryHandler(handle_main_menu, pattern="^MAIN_MENU$"))

    port = int(os.environ.get("PORT", 10000))
    render_url = os.environ.get("RENDER_EXTERNAL_URL")
    if not render_url:
        raise RuntimeError("RENDER_EXTERNAL_URL is not set. Make sure your environment provides it.")

    # Add global error handler
    from telegram.error import TelegramError
    async def error_handler(update, context):
        logger.error(msg="Exception while handling an update:", exc_info=context.error)
    application.add_error_handler(error_handler)

    application.run_webhook(
        listen="0.0.0.0",
        port=port,
        webhook_url=f"{render_url}"
    )


# --- Pause/resume handlers ---
import telegram.error
async def handle_pause(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    try:
        await query.answer()
    except telegram.error.BadRequest as e:
        if "Query is too old" in str(e):
            logger.warning("Callback query too old; skipping answer.")
            return
        else:
            raise
    context.chat_data["paused"] = True
    context.chat_data["resume_question"] = context.chat_data.get("current_index", 0)
    await query.edit_message_text("⏸ Test paused. You can continue anytime by selecting Continue from the main menu.")

async def handle_resume_pause(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    try:
        await query.answer()
    except telegram.error.BadRequest as e:
        if "Query is too old" in str(e):
            logger.warning("Callback query too old; skipping answer.")
            return
        else:
            raise
    context.chat_data["paused"] = False
    context.chat_data["current_index"] = context.chat_data.get("resume_question", 0)
    await send_question(query.message.chat.id, context)


if __name__ == "__main__":
    main()



