import logging
import os
import json

from telegram import BotCommand, Poll
from typing import Dict, List, Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, InputFile
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CallbackContext,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    Defaults,
    PollAnswerHandler,
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
    # Also initialize user_data for language selection
    context.user_data["lang_mode"] = lang_mode
    context.user_data["current_index"] = 0
    context.user_data["score"] = 0
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
    # Mirror mode state in user_data for poll-based quizzes
    context.user_data["mode"] = mode
    context.user_data["current_index"] = 0
    context.user_data["score"] = 0
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
    # Obtain the correct chat_data for the given chat_id.  When this function
    # is called from a PollAnswer update, ``context.chat_data`` may be empty
    # or belong to a different chat.  In that case, fall back to
    # ``context.application.chat_data``.
    chat_data = None
    try:
        # In typical handlers, context.chat_data refers to the current chat
        if context.chat_data:
            chat_data = context.chat_data
    except Exception:
        chat_data = None
    if chat_data is None or not chat_data:
        chat_data = context.application.chat_data.get(chat_id, {})
    index = chat_data.get("current_index", 0)
    lang_mode = chat_data.get("lang_mode", "en")

    # Do not remove previous inline keyboard here to avoid UI flicker.

    mode = chat_data.get("mode", "learning")
    # If not in exam mode, send an image (if available) and a quiz poll for the question.
    if mode != "exam":
        # End of questions: show score
        if index >= len(QUESTIONS):
            await send_score(chat_id, context)
            return
        q = QUESTIONS[index]
        # Build caption for the image. Include question number and bilingual text if needed.
        caption_lines: List[str] = [f"<i><b>Question {index + 1} of {len(QUESTIONS)}</b></i>"]
        if lang_mode == "bilingual":
            caption_lines.append(f"<b>🇬🇧 {q['question']}</b>")
            caption_lines.append(f"<b>🇺🇦 {q.get('question_uk', '')}</b>")
        else:
            caption_lines.append(f"<b>{q['question']}</b>")
        caption_text = "\n".join(caption_lines)
        # Determine image path based on question_number
        image_filename = None
        possible_extensions = [".jpg", ".jpeg", ".png", ".webp"]
        for ext in possible_extensions:
            path = f"images/{q['question_number']}{ext}"
            if os.path.exists(path):
                image_filename = path
                break
        # Send image with caption if available
        if image_filename:
            try:
                with open(image_filename, "rb") as photo:
                    await context.bot.send_photo(
                        chat_id=chat_id,
                        photo=photo,
                        caption=caption_text,
                        parse_mode=ParseMode.HTML
                    )
            except Exception as e:
                logger.warning(f"Failed to send image for question {q['question_number']}: {e}")
        else:
            # If no image, send the caption as a simple message to present the question
            await context.bot.send_message(
                chat_id=chat_id,
                text=caption_text,
                parse_mode=ParseMode.HTML
            )
        # Now prepare the poll question (reuse bilingual or english question). Trim if necessary
        if lang_mode == "bilingual":
            poll_question = f"{q['question']} / {q.get('question_uk', '')}"
        else:
            poll_question = q['question']
        # Ensure poll question length does not exceed 300 characters
        if len(poll_question) > 300:
            poll_question = poll_question[:297] + "..."
        # Prepare the answer options (English only, truncated if needed)
        options_en = q["options"]
        poll_options: List[str] = []
        for opt in options_en:
            trimmed = opt
            if len(trimmed) > 100:
                trimmed = trimmed[:97] + "..."
            poll_options.append(trimmed)
        # Prepare explanation text (English only) for non-bilingual mode and trim to 200 characters
        explanation_text: Optional[str] = None
        if lang_mode != "bilingual" and q.get("explanation_en"):
            exp_en = q["explanation_en"]
            if len(exp_en) > 200:
                exp_en = exp_en[:197] + "..."
            explanation_text = exp_en
        # Send the quiz poll. Polls must be non-anonymous to receive PollAnswer updates.
        poll_message = await context.bot.send_poll(
            chat_id=chat_id,
            question=poll_question,
            options=poll_options,
            type=Poll.QUIZ,
            correct_option_id=q['answer_index'],
            explanation=explanation_text,
            is_anonymous=False,
        )
        # Store the poll id with its associated chat and question index so that the answer can be processed later
        context.bot_data[poll_message.poll.id] = {
            "chat_id": chat_id,
            "question_index": index,
            "mode": "learning",
        }
        return
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
    """
    Send the final score summary to the user.

    This function attempts to retrieve the current chat's state from
    ``context.chat_data`` when it exists (i.e., for callback queries and
    commands). When invoked from a poll answer (where ``context.chat_data``
    may be empty), it falls back to ``context.application.chat_data`` using
    the provided ``chat_id``. This ensures that both exam and learning modes
    have access to the correct state without overwriting the chat_data mapping.
    """
    # Determine the appropriate chat_data source
    chat_data = None
    try:
        # In typical handlers (exam mode), context.chat_data holds the chat's state
        if context.chat_data:
            chat_data = context.chat_data
    except Exception:
        # context.chat_data may not be available (e.g., in poll answers)
        chat_data = None
    if chat_data is None:
        # Fallback to application-level chat_data mapping
        chat_data = context.application.chat_data.get(chat_id, {})

    # Extract mode and score information
    mode = chat_data.get("mode", "learning")
    score = chat_data.get("score", 0)
    total = len(chat_data.get("exam_questions", [])) if mode == "exam" else len(QUESTIONS)

    # Build the result message based on mode
    if mode == "exam":
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
            f"<b>🎉 You scored {score} out of {total}!</b>\n"
            "Type /quiz to try again.\n\n"
            f"<b>🇺🇦 Ви набрали {score} із {total} балів!</b>\n"
            "Наберіть /quiz, щоб спробувати ще раз."
        )

    # Choose the appropriate callback for the restart button
    start_again_callback = "mode_exam" if mode == "exam" else "mode_learning"

    # Send the score message
    await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🔁 Start Again", callback_data=start_again_callback),
                InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu"),
            ]
        ])
    )
    # Clear the chat-specific state to reset for a new session
    if chat_data:
        chat_data.clear()

async def handle_poll_answer(update: Update, context: CallbackContext) -> None:
    """Process answers from quiz polls to advance the learning mode."""
    """
    Process answers from quiz polls to advance the learning mode.

    This handler retrieves the stored poll data to determine which chat and
    question the answer belongs to. It then updates the user and chat state
    in place rather than replacing the entire chat_data mapping. Replacing
    the chat_data mapping can break subsequent exam mode sessions, as the
    Telegram framework expects a special ChatData object. Updating in place
    preserves the underlying data structures.
    """
    # Retrieve the poll answer and associated data
    answer = update.poll_answer
    poll_id = answer.poll_id
    poll_data = context.bot_data.get(poll_id)
    if not poll_data:
        # No associated poll data found; nothing to do
        return
    chat_id_local = poll_data.get("chat_id")
    question_index = poll_data.get("question_index")
    # Remove the poll mapping now that it's been answered
    context.bot_data.pop(poll_id, None)

    # Identify the user who answered
    user_id = answer.user.id
    # Access existing per-user and per-chat state
    # Do not replace the stored state objects; update them in place to
    # preserve the ChatData wrappers used by the framework.
    user_state = context.application.user_data.get(user_id)
    if user_state is None:
        user_state = {}
        context.application.user_data[user_id] = user_state
    chat_state = context.application.chat_data.get(chat_id_local)
    if chat_state is None:
        # Initialize a fresh chat_state if none exists
        chat_state = {}
        context.application.chat_data[chat_id_local] = chat_state

    # Evaluate the answer
    question = QUESTIONS[question_index]
    correct_index = question["answer_index"]
    selected_indices = answer.option_ids
    selected_index = selected_indices[0] if selected_indices else -1
    if selected_index == correct_index:
        # Increment score for both user and chat state
        user_state["score"] = user_state.get("score", 0) + 1
        chat_state["score"] = chat_state.get("score", 0) + 1

    # Advance to the next question
    next_index = question_index + 1
    user_state["current_index"] = next_index
    chat_state["current_index"] = next_index
    # Preserve mode and language settings
    # These should already be set in chat_state; only set defaults if missing
    user_state.setdefault("mode", chat_state.get("mode", "learning"))
    chat_state.setdefault("mode", "learning")
    # Ensure the language mode persists across questions.  If chat_state does
    # not yet have a lang_mode (e.g., when handling poll answers), copy it
    # from the user_state or default to English.  Also propagate the value
    # into user_state if needed.
    lang_mode_existing = chat_state.get("lang_mode")
    if lang_mode_existing is None:
        lang_mode_existing = user_state.get("lang_mode", "en")
        chat_state["lang_mode"] = lang_mode_existing
    user_state.setdefault("lang_mode", lang_mode_existing)

    # Save updated state back (user_state and chat_state are already
    # stored in application.user_data and application.chat_data)

    # Send the next question or the final score
    if next_index < len(QUESTIONS):
        await send_question(chat_id_local, context)
    else:
        await send_score(chat_id_local, context)

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
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔁 Start Again", callback_data="start_exam"),
     InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]])
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
        # Load image based on question_number (matches file like '12.jpg' or '12.png')
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
    # Register handler for answers to quiz polls
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