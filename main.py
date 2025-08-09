import logging
import os
import json
import time

from telegram import BotCommand
from typing import Dict

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, InputFile
from telegram import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
import difflib
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CallbackContext,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    Defaults,
)
from telegram.ext import MessageHandler, filters

with open("questions.json", "r", encoding="utf-8") as f:
    QUESTIONS = json.load(f)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)


logger = logging.getLogger(__name__)
def clear_state(context: CallbackContext, preserve=("start_message_id", "menu_message_id", "last_start_ts")):
    data = context.chat_data
    keep = {k: data.get(k) for k in preserve if k in data}
    data.clear()
    data.update(keep)

# --- Debounce helper to avoid multiple parallel actions from menu ---
def is_debounced(context: CallbackContext, key: str = "action_lock_until", window: float = 2.0) -> bool:
    """
    Returns True if we should ignore this action because a recent one is still 'locked'.
    Sets lock for `window` seconds on first pass.
    """
    now = time.time()
    lock_until = context.chat_data.get(key, 0)
    if now < lock_until:
        return True
    context.chat_data[key] = now + window
    return False

# --- Helper: Upsert message to avoid duplicates ---
async def upsert_message(chat, context, message_id_key: str, text: str, reply_markup=None, parse_mode: str | None = ParseMode.HTML):
    """Edit an existing message if we already sent it; otherwise send a new one.
    This prevents duplicates when /start is tapped many times during lag.
    Stores the message_id in chat_data under `message_id_key`.
    """
    chat_data = context.chat_data
    mid = chat_data.get(message_id_key)
    if mid:
        try:
            # Try to edit the existing message
            await context.bot.edit_message_text(
                chat_id=chat.id,
                message_id=mid,
                text=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
            )
            return mid
        except Exception:
            # If edit fails (deleted/too old), fall back to sending a new one
            pass
    # Send a new message and remember its id
    if parse_mode:
        msg = await context.bot.send_message(chat_id=chat.id, text=text, reply_markup=reply_markup, parse_mode=parse_mode)
    else:
        msg = await context.bot.send_message(chat_id=chat.id, text=text, reply_markup=reply_markup)
    # Try to delete the previous message if it existed and is different
    if mid and mid != msg.message_id:
        try:
            await context.bot.delete_message(chat.id, mid)
        except Exception:
            pass
    chat_data[message_id_key] = msg.message_id
    return msg.message_id

# --- Helper: remove inline keyboards from previous interactive messages ---
async def remove_old_inline_keyboards(context: CallbackContext, chat_id: int, skip_message_id: int | None = None):
    """
    Remove inline keyboards from previously sent interactive messages to avoid many active keyboards.
    Stores/reads message ids in chat_data['active_message_ids'].
    """
    ids = context.chat_data.get("active_message_ids", [])
    if not isinstance(ids, list):
        ids = []
    for mid in ids:
        if skip_message_id is not None and mid == skip_message_id:
            continue
        try:
            await context.bot.edit_message_reply_markup(chat_id=chat_id, message_id=mid, reply_markup=None)
        except Exception:
            pass
    # After cleaning, reset the tracked list
    context.chat_data["active_message_ids"] = []

# === Persistent Reply Keyboard (Main Menu) ===
BTN_LEARNING = "🧠 Learning Mode"
BTN_EXAM     = "📝 Exam Mode"
BTN_CONTINUE = "▶️ Continue"
BTN_RESTART  = "🔁 Restart"
BTN_STOP     = "⛔ Stop"
BTN_HELP     = "❓ Help"

def build_main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton(BTN_LEARNING), KeyboardButton(BTN_EXAM)],
            [KeyboardButton(BTN_RESTART)],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
        input_field_placeholder="Choose an action…",
    )

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
    # Debounce flood: ignore extra taps for 2s
    now = time.time()
    last = context.chat_data.get("last_start_ts", 0)
    if now - last < 2:
        try:
            await upsert_message(
                update.effective_chat,
                context,
                "menu_message_id",
                "Use the menu below to navigate.",
                reply_markup=build_main_menu(),
                parse_mode=None,
            )
        except Exception:
            pass
        return
    context.chat_data["last_start_ts"] = now

    # Build language keyboard; add Resume if paused
    lang_options = [row[:] for row in LANG_OPTIONS]
    if context.chat_data.get("paused"):
        lang_options.append([InlineKeyboardButton("▶️ Continue", callback_data="RESUME_PAUSE")])

    # Show/refresh one single start screen message (no duplicates)
    await upsert_message(
        update.effective_chat,
        context,
        "start_message_id",
        "Please choose your language / Будь ласка, оберіть мову:",
        reply_markup=InlineKeyboardMarkup(lang_options),
        parse_mode=ParseMode.HTML,
    )

    # Show/refresh the persistent main menu keyboard via a single message too
    try:
        await upsert_message(
            update.effective_chat,
            context,
            "menu_message_id",
            "Use the menu below to navigate.",
            reply_markup=build_main_menu(),
            parse_mode=None,
        )
    except Exception as e:
        logger.warning(f"Failed to show main menu keyboard: {e}")
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
    clear_state(context)
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
    # Clear any duplicated state timestamps (debounce)
    context.chat_data.pop("last_start_ts", None)
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
        # Reset the debounce clock when mode is chosen
        context.chat_data.pop("last_start_ts", None)
    except telegram.error.BadRequest as e:
        if "Query is too old" in str(e):
            logger.warning("Callback query too old; skipping answer.")
        else:
            raise
    mode = "learning" if query.data == "mode_learning" else "exam"
    context.chat_data["mode"] = mode
    context.chat_data["current_index"] = 0
    context.chat_data["score"] = 0
    context.chat_data["answered"] = 0
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


# --- Persistent Menu Handlers ---
async def start_mode_from_menu(update: Update, context: CallbackContext, mode: str) -> None:
    if is_debounced(context):
        try:
            await upsert_message(
                update.effective_chat,
                context,
                "menu_message_id",
                "Use the menu below to navigate.",
                reply_markup=build_main_menu(),
                parse_mode=None,
            )
        except Exception:
            pass
        return
    # Ensure a language default exists
    if "lang_mode" not in context.chat_data:
        context.chat_data["lang_mode"] = "en"
    context.chat_data["mode"] = mode
    context.chat_data["current_index"] = 0
    context.chat_data["score"] = 0
    context.chat_data["answered"] = 0
    context.chat_data["paused"] = False

    if mode == "exam":
        import random
        if len(QUESTIONS) < 30:
            await update.message.reply_text("❌ Not enough questions to start the exam.", reply_markup=build_main_menu())
            return
        context.chat_data["exam_questions"] = random.sample(range(len(QUESTIONS)), 30)
        context.chat_data["used_questions"] = []
    else:
        context.chat_data["exam_questions"] = []
        context.chat_data["used_questions"] = []

    await send_question(update.effective_chat.id, context)

async def menu_learning(update: Update, context: CallbackContext):
    if is_debounced(context):
        try:
            await upsert_message(
                update.effective_chat,
                context,
                "menu_message_id",
                "Use the menu below to navigate.",
                reply_markup=build_main_menu(),
                parse_mode=None,
            )
        except Exception:
            pass
        return
    await start_mode_from_menu(update, context, "learning")

async def menu_exam(update: Update, context: CallbackContext):
    if is_debounced(context):
        try:
            await upsert_message(
                update.effective_chat,
                context,
                "menu_message_id",
                "Use the menu below to navigate.",
                reply_markup=build_main_menu(),
                parse_mode=None,
            )
        except Exception:
            pass
        return
    await start_mode_from_menu(update, context, "exam")

async def menu_continue(update: Update, context: CallbackContext):
    if is_debounced(context):
        try:
            await upsert_message(
                update.effective_chat,
                context,
                "menu_message_id",
                "Use the menu below to navigate.",
                reply_markup=build_main_menu(),
                parse_mode=None,
            )
        except Exception:
            pass
        return
    if "mode" not in context.chat_data:
        await update.message.reply_text("No active session. Choose a mode first.", reply_markup=build_main_menu())
        return
    await send_question(update.effective_chat.id, context)

async def menu_restart(update: Update, context: CallbackContext):
    if is_debounced(context):
        try:
            await upsert_message(
                update.effective_chat,
                context,
                "menu_message_id",
                "Use the menu below to navigate.",
                reply_markup=build_main_menu(),
                parse_mode=None,
            )
        except Exception:
            pass
        return
    clear_state(context)
    await start(update, context)

async def stop_command(update: Update, context: CallbackContext):
    clear_state(context)
    await update.message.reply_text("⛔ Test stopped. Use /start to begin again.", reply_markup=build_main_menu())

async def menu_stop(update: Update, context: CallbackContext):
    if is_debounced(context):
        try:
            await upsert_message(
                update.effective_chat,
                context,
                "menu_message_id",
                "Use the menu below to navigate.",
                reply_markup=build_main_menu(),
                parse_mode=None,
            )
        except Exception:
            pass
        return
    await stop_command(update, context)

async def pause_command(update: Update, context: CallbackContext) -> None:
    context.chat_data["paused"] = True
    context.chat_data["resume_question"] = context.chat_data.get("current_index", 0)
    await update.message.reply_text(
        "⏸ Test paused. Use ▶️ Continue in the menu.",
        reply_markup=build_main_menu()
    )

async def menu_help(update: Update, context: CallbackContext):
    if is_debounced(context):
        try:
            await upsert_message(
                update.effective_chat,
                context,
                "menu_message_id",
                "Use the menu below to navigate.",
                reply_markup=build_main_menu(),
                parse_mode=None,
            )
        except Exception:
            pass
        return
    await update.message.reply_text(
        f"• *Learning Mode*: answers + explanations, 120 questions in order. Type a number (1–{len(QUESTIONS)}) to jump to that question.\n"
        "• *Exam Mode*: 30 random questions, no hints, pass with ≤5 errors.\n"
        "• *Continue*: resume current session.\n"
        "• *Restart*: reset and go to start screen.\n"
        "• *Stop*: end the current session.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=build_main_menu(),
    )

# --- Numeric jump to question in Learning Mode ---
async def jump_to_number(update: Update, context: CallbackContext):
    """Allow jumping to a specific question number in learning mode by typing a number."""
    if not update.message or not update.message.text:
        return
    text = update.message.text.strip()
    if not text.isdigit():
        return
    # Only in learning mode
    if context.chat_data.get("mode") != "learning":
        return
    n = int(text)
    total = len(QUESTIONS)
    if n < 1 or n > total:
        await update.message.reply_text(
            f"Enter a number from 1 to {total} to jump to a question in Learning Mode.",
            reply_markup=build_main_menu(),
        )
        return
    # Set index (0-based) and send that question
    context.chat_data["current_index"] = n - 1
    await send_question(update.effective_chat.id, context)

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

    if mode == "exam":
        answered = len(chat_data.get("used_questions", []))
        total_questions = len(chat_data.get("exam_questions", []))
        question_no = answered if answered > 0 else 1
        fail_count = max(0, answered - chat_data.get("score", 0))
    else:
        total_questions = len(QUESTIONS)
        question_no = index + 1
        answered = chat_data.get("answered", 0)
        fail_count = max(0, answered - chat_data.get("score", 0))

    lines = [f"<i><b>Question {question_no} of {total_questions} ({fail_count} Fails)</b></i>", ""]

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

    # Before sending a new question with buttons, remove keyboards from older ones
    try:
        await remove_old_inline_keyboards(context, chat_id)
    except Exception:
        pass

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
        # Track as active (has inline keyboard)
        context.chat_data["active_message_ids"] = [msg.message_id]
    else:
        msg = await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard
        )
        context.chat_data["last_message_id"] = msg.message_id
        # Track as active (has inline keyboard)
        context.chat_data["active_message_ids"] = [msg.message_id]

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
            f"<b>🎉 You scored {score} out of {total}!</b>\n"
            "Type /quiz to try again.<br/><br/>"
            f"<b>🇺🇦 Ви набрали {score} із {total} балів!</b>\n"
            "Наберіть /quiz, щоб спробувати ще раз."
        )
    await context.bot.send_message(chat_id=chat_id, text=text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔁 Start Again", callback_data="mode_exam"), InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]]))
    chat_data.clear()

async def quiz_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Reset state and behave exactly like /start
    clear_state(context)
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
            if mode == "learning":
                chat_data["answered"] = chat_data.get("answered", 0) + 1
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
            if mode == "exam":
                fail_count = current_index + 1 - chat_data.get("score", 0)
            else:
                fail_count = max(0, chat_data.get("answered", 0) - chat_data.get("score", 0))
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
                    # No more inline keyboard on this message
                    context.chat_data["active_message_ids"] = []
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
                    # No more inline keyboard on this message
                    context.chat_data["active_message_ids"] = []
            else:
                if query.message and query.message.text:
                    msg = await query.edit_message_text(
                        text=formatted_question,
                        reply_markup=None,
                        parse_mode=ParseMode.HTML
                    )
                    context.chat_data["last_message_id"] = msg.message_id
                    context.chat_data["active_message_ids"] = []
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
        if mode == "learning":
            chat_data["answered"] = chat_data.get("answered", 0) + 1
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
    application.add_handler(CommandHandler("pause", pause_command))
    application.add_handler(CommandHandler("stop", stop_command))
    application.add_handler(CallbackQueryHandler(next_handler, pattern="^(NEXT|CONTINUE|RESTART)$"))
    application.add_handler(CallbackQueryHandler(answer_handler, pattern="^[ABCD]$"))
    application.add_handler(CallbackQueryHandler(handle_language, pattern="^lang_.*$"))
    application.add_handler(CallbackQueryHandler(handle_mode, pattern="^mode_.*$"))
    application.add_handler(CallbackQueryHandler(handle_pause, pattern="^mode_pause$"))
    application.add_handler(CallbackQueryHandler(handle_resume_pause, pattern="^RESUME_PAUSE$"))
    application.add_handler(CallbackQueryHandler(handle_main_menu, pattern="^MAIN_MENU$"))

    # Persistent menu reply keyboard handlers
    application.add_handler(MessageHandler(filters.TEXT & filters.Regex(f"^{BTN_LEARNING}$"), menu_learning))
    application.add_handler(MessageHandler(filters.TEXT & filters.Regex(f"^{BTN_EXAM}$"),     menu_exam))
    application.add_handler(MessageHandler(filters.TEXT & filters.Regex(f"^{BTN_CONTINUE}$"), menu_continue))
    application.add_handler(MessageHandler(filters.TEXT & filters.Regex(f"^{BTN_RESTART}$"),  menu_restart))
    application.add_handler(MessageHandler(filters.TEXT & filters.Regex(f"^{BTN_STOP}$"),     menu_stop))
    application.add_handler(MessageHandler(filters.TEXT & filters.Regex(f"^{BTN_HELP}$"),     menu_help))

    # Numeric jump in Learning Mode
    application.add_handler(MessageHandler(filters.TEXT & filters.Regex(r"^\d{1,3}$"), jump_to_number))

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