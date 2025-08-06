import logging
import os
import json

from telegram import BotCommand
from typing import Dict

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, InputFile
import difflib
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CallbackContext,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    Defaults,
    PollAnswerHandler,  # for quiz polls
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
    # Initialize per-user state when using learning mode so that poll answers can be tracked
    if mode == "learning":
        context.user_data.clear()
        context.user_data["mode"] = mode
        context.user_data["current_question_index"] = 0
        context.user_data["score"] = 0
        context.user_data["lang_mode"] = context.chat_data.get("lang_mode", "en")
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
            # Learning mode uses quiz polls instead of inline keyboards
            if mode != "exam":
                total_questions = len(QUESTIONS)
                # Build question text based on language
                if lang_mode == "bilingual":
                    question_text = f"Q{index + 1}/{total_questions}:\n🇬🇧 {q['question']}\n🇺🇦 {q['question_uk']}"
                else:
                    question_text = f"Q{index + 1}/{total_questions}:\n{q['question']}"
                # Build options list
                options_en = q["options"]
                options_uk = q.get("options_uk", [])
                poll_options = []
                for i, opt_en in enumerate(options_en):
                    if lang_mode == "bilingual" and options_uk:
                        poll_options.append(f"{opt_en} / {options_uk[i]}")
                    else:
                        poll_options.append(opt_en)
                # Build explanation
                explanation_en = q.get("explanation_en") or q.get("explanation")
                explanation_uk = q.get("explanation_uk")
                explanation = None
                if explanation_en or explanation_uk:
                    if lang_mode == "bilingual":
                        parts = []
                        if explanation_en:
                            parts.append(explanation_en)
                        if explanation_uk:
                            parts.append(explanation_uk)
                        explanation = "\n".join(parts)
                    else:
                        explanation = explanation_en or ""
                # Send quiz poll
                poll_message = await context.bot.send_poll(
                    chat_id=chat_id,
                    question=question_text,
                    options=poll_options,
                    type="quiz",
                    correct_option_id=q["answer_index"],
                    explanation=explanation,
                    is_anonymous=False,
                )
                # Store mapping of poll ID to chat ID for poll answer handler
                context.bot_data.setdefault("polls", {})[poll_message.poll.id] = chat_id
                # Update per-user state for poll answers
                context.user_data["current_question_index"] = index
                context.user_data["mode"] = "learning"
                context.user_data["lang_mode"] = lang_mode
                context.user_data.setdefault("score", 0)
                return

    total_questions = len(chat_data.get("exam_questions", [])) if mode == 'exam' else len(QUESTIONS)
    fail_count = chat_data.get("current_index", 0) - chat_data.get("score", 0)
    lines = [f"<i><b>Question {index + 1} of {total_questions} ({fail_count} Fails)</b></i>", ""]

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

    # Load image based on question_number (matches file like '12.jpg' or '12.png')
    image_filename = None
    possible_extensions = [".jpg", ".jpeg", ".png", ".webp"]
    for ext in possible_extensions:
        path = f"images/{q['question_number']}{ext}"
        if os.path.exists(path):
            image_filename = path
            break

    text = "\n".join(lines)

    keyboard = build_option_keyboard()
    if image_filename:
        with open(image_filename, "rb") as photo:
            msg = await context.bot.send_photo(
                chat_id=chat_id,
                photo=photo,
                caption=text,
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard
            )
        context.chat_data["last_message_id"] = msg.message_id
    else:
        msg = await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard
        )
        context.chat_data["last_message_id"] = msg.message_id

async def send_score(chat_id: int, context: CallbackContext) -> None:
    chat_data = context.chat_data
    user_data = context.user_data
    # Determine which mode is active (learning mode state may reside in user_data)
    mode = user_data.get("mode") or chat_data.get("mode", "learning")
    if mode == "exam":
        score = chat_data.get("score", 0)
        total = len(chat_data.get("exam_questions", []))
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
        await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton("🔁 Start Again", callback_data="mode_exam"),
                        InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu"),
                    ]
                ]
            ),
        )
        chat_data.clear()
    else:
        # learning mode scoreboard
        score = user_data.get("score", 0)
        total = len(QUESTIONS)
        text = (
            f"<b>🎉 You scored {score} out of {total}!</b>\n"
            "Type /quiz to try again.<br/><br/>"
            f"<b>🇺🇦 Ви набрали {score} із {total} балів!</b>\n"
            "Наберіть /quiz, щоб спробувати ще раз."
        )
        await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=ParseMode.HTML,
        )
        user_data.clear()

async def quiz_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Reset state and behave exactly like /start
    context.chat_data.clear()
    await start(update, context)

async def answer_handler(update: Update, context: CallbackContext) -> None:
    # Support both button (callback_query) and text answers (update.message)
    import telegram.error
    chat_data = context.chat_data
    # If this is a callback query (button answer)
    if update.callback_query:
        query = update.callback_query
        try:
            await query.answer()
        except telegram.error.BadRequest as e:
            if "Query is too old" in str(e):
                logger.warning("Callback query too old; skipping answer.")
            else:
                raise
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
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔁 Start Again", callback_data="start_exam"),
         InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]])
                )
            return
        # Do not remove previous inline keyboard here to avoid UI flicker.
        current_index = chat_data.get("current_index", 0)
        if mode == "exam":
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
            for idx, opt_en in enumerate(options_en):
                opt_uk = options_uk[idx] if lang_mode == "bilingual" and options_uk else ""
                line = f"{opt_en}" if not opt_uk else f"{opt_en} / {opt_uk}"
                option_letter = option_labels[idx]
                if idx == selected_index and idx != correct_index:
                    options_text.append(f"❌ <b>{option_letter}. {line}</b>")
                elif idx == selected_index and idx == correct_index:
                    options_text.append(f"✅ <b>{option_letter}. {line}</b>")
                elif idx == correct_index:
                    options_text.append(f"✅ {option_letter}. {line}")
                else:
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
            # Do not show explanation in exam mode
            # Show explanation only in learning mode, and only if correct
            if mode == "exam" or not is_correct:
                pass
            else:
                if mode == "learning" and "explanation" in question:
                    full_text.append("------------------------------")
                    full_text.append("<b>Explanation:</b>")
                    full_text.append(f"*{question['explanation']}*")
            formatted_question = "\n".join(full_text)
            # Load image based on question_number
            index = chat_data.get("current_index", 0)
            image_filename = None
            possible_extensions = [".jpg", ".jpeg", ".png", ".webp"]
            for ext in possible_extensions:
                path = f"images/{question['question_number']}{ext}"
                if os.path.exists(path):
                    image_filename = path
                    break
            # Show result and explanation, then automatically move to next question
            if image_filename:
                try:
                    import telegram
                    with open(image_filename, "rb") as photo:
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
                    with open(image_filename, "rb") as photo:
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
        return

# --- Poll answer handler ---
async def handle_poll_answer(update: Update, context: CallbackContext) -> None:
    """
    Handle answers from quiz polls in learning mode.
    When a user answers a poll, update their score and send the next question.
    """
    poll_answer = update.poll_answer
    # Retrieve chat_id stored when poll was sent
    poll_id = poll_answer.poll_id
    chat_id = context.bot_data.get("polls", {}).pop(poll_id, None)
    if chat_id is None:
        return
    user_data = context.user_data
    # Get the index of the question that was answered
    index = user_data.get("current_question_index", 0)
    if index < len(QUESTIONS):
        question = QUESTIONS[index]
        correct_index = question["answer_index"]
        selected_indices = poll_answer.option_ids
        selected_index = selected_indices[0] if selected_indices else -1
        if selected_index == correct_index:
            user_data["score"] = user_data.get("score", 0) + 1
        # Move to next question index
        user_data["current_question_index"] = index + 1
    # Determine whether to send next question or final score
    next_index = user_data.get("current_question_index", 0)
    if next_index < len(QUESTIONS):
        await send_question(chat_id, context)
    else:
        await send_score(chat_id, context)

    # --- Text answer logic ---
    # If this is a text message (user sends answer as text)
    if update.message and update.message.text:
        user_msg = update.message.text.strip()
        # Defensive: skip if no quiz running
        if not chat_data or "mode" not in chat_data:
            return
        mode = chat_data.get("mode", "learning")
        current_index = chat_data.get("current_index", 0)
        if mode == "exam":
            used_questions = chat_data.get("used_questions", [])
            if used_questions:
                question_index = used_questions[-1]
            else:
                question_index = chat_data["exam_questions"][current_index]
        else:
            question_index = current_index
        question = QUESTIONS[question_index]
        options_en = question["options"]
        options_uk = question.get("options_uk", [])
        option_labels = ["A", "B", "C", "D"]
        # Accept answers as full text or letter (A/B/C/D)
        all_possible_answers = []
        # Add English and Ukrainian options (case-insensitive)
        for idx, opt in enumerate(options_en):
            all_possible_answers.append((opt, idx))
        for idx, opt in enumerate(options_uk):
            all_possible_answers.append((opt, idx))
        # Also support A/B/C/D as answer
        for idx, label in enumerate(option_labels):
            all_possible_answers.append((label, idx))
        # Lowercase mapping for fuzzy match
        user_text = user_msg.lower()
        answer_candidates = [ans.lower() for ans, _ in all_possible_answers]
        # Use difflib to get close matches (allowing for typos)
        matches = difflib.get_close_matches(user_text, answer_candidates, n=1, cutoff=0.7)
        selected_index = -1
        if matches:
            match = matches[0]
            for i, (ans, idx) in enumerate(all_possible_answers):
                if ans.lower() == match:
                    selected_index = idx
                    break
        else:
            # fallback: try if user typed number 1-4
            if user_text in ["1", "2", "3", "4"]:
                selected_index = int(user_text) - 1
        # If not recognized, reply and do NOT advance
        if selected_index < 0 or selected_index >= 4:
            await update.message.reply_text("❌ Could not recognize your answer. Please reply with the full text or letter (A, B, C, D).")
            return
        correct_index = question["answer_index"]
        is_correct = selected_index == correct_index
        if is_correct:
            chat_data["score"] = chat_data.get("score", 0) + 1
        # Prepare feedback message
        feedback_lines = []
        if is_correct:
            feedback_lines.append("✅ Correct!")
        else:
            feedback_lines.append("❌ Incorrect.")
        # In learning mode, show explanation if correct
        if mode == "learning" and "explanation" in question:
            feedback_lines.append("------------------------------")
            feedback_lines.append("<b>Explanation:</b>")
            feedback_lines.append(f"*{question['explanation']}*")
        # Reply to user
        await update.message.reply_text(
            "\n".join(feedback_lines),
            parse_mode=ParseMode.HTML
        )
        # Advance to next question
        chat_data["current_index"] = chat_data.get("current_index", 0) + 1
        max_questions = len(chat_data.get("exam_questions", [])) if chat_data.get("mode", "learning") == "exam" else len(QUESTIONS)
        if chat_data["current_index"] < max_questions:
            await send_question(update.effective_chat.id, context)
        else:
            await send_score(update.effective_chat.id, context)
        return

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
    # Allow users to answer by sending A/B/C/D as text
    from telegram.ext import MessageHandler, filters
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_answer))
    # Register handler for quiz poll answers
    application.add_handler(PollAnswerHandler(handle_poll_answer))

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


# --- Text answer handler for A/B/C/D replies ---
async def handle_text_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if "quiz_type" not in context.user_data or "current_question_index" not in context.user_data:
        return

    user_input = update.message.text.strip().upper()
    if user_input in ["A", "B", "C", "D"]:
        option_index = ["A", "B", "C", "D"].index(user_input)
        question_list = context.user_data["question_list"]
        index = context.user_data["current_question_index"]
        if index < len(question_list):
            question = question_list[index]
            await handle_answer(update, context, str(option_index))