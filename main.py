import logging
import os
import json
import time

from telegram import BotCommand
from typing import Dict
from functools import wraps

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
    MessageHandler,
    filters,
)

with open("questions.json", "r", encoding="utf-8") as f:
    QUESTIONS = json.load(f)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

BOT_TOKEN = os.getenv("BOT_TOKEN")

logger = logging.getLogger(__name__)

# --- anti-spam / per-chat lock & decorator ---

LOCK_TTL = 1.5  # seconds to ignore repeated taps / messages

# Extra debounce specifically for answer taps and question sending
ANSWER_DEBOUNCE = 0.8  # seconds

def _debounce_answer(chat_data) -> bool:
    """Return True if we should handle this answer now; False if still cooling down."""
    now = time.monotonic()
    last = chat_data.get("_answer_at", 0.0)
    if now - last < ANSWER_DEBOUNCE:
        return False
    chat_data["_answer_at"] = now
    return True

def _is_stale_callback(chat_data, msg_id: int) -> bool:
    """Callback that doesn't belong to the last question with active keyboard."""
    return (
        msg_id != chat_data.get("last_message_id")
        or not chat_data.get("last_has_kb", False)
    )

def _try_acquire_lock(chat_data, ttl: float = LOCK_TTL) -> bool:
    """Return True if lock acquired; False if busy within ttl."""
    now = time.monotonic()
    lock_at = chat_data.get("_lock_at", 0.0)
    if now - lock_at < ttl:
        return False
    chat_data["_lock_at"] = now
    return True

def _release_lock(chat_data) -> None:
    chat_data["_lock_at"] = 0.0

def antispam(handler):
    @wraps(handler)
    async def wrapper(update: Update, context: CallbackContext, *args, **kwargs):
        # якщо замок активний, але це командне повідомлення — пропускаємо
        if not _try_acquire_lock(context.chat_data):
            # ввічливо «глушимо» спінер на старих callback'ах
            if update.callback_query:
                try:
                    await update.callback_query.answer("⏳ Please wait…")
                except Exception:
                    pass
            # команди (починаються з /) не блокуємо: даємо пройти обробнику
            if not (getattr(update, "message", None)
                    and update.message.text
                    and update.message.text.startswith("/")):
                return
        try:
            return await handler(update, context, *args, **kwargs)
        finally:
            # замок тримаємо до спливу TTL — спеціально нічого не робимо
            pass
    return wrapper  

@antispam
async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    # прибрати попередні повідомлення з кнопками
    await _purge_old_ui(context, chat_id)
    pid = context.chat_data.pop("lang_prompt_id", None)
    if pid:
        await _safe_delete(context.bot, chat_id, pid)

    # повний ресет стану + скинути анти-спам лічильник
    context.chat_data.clear()
    context.chat_data["_lock_at"] = 0.0

    await update.message.reply_text("🛑 Stopped. Send /start to begin again.")

# --- helpers to keep only current UI ---
async def _safe_delete(bot, chat_id: int, message_id: int):
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        # Ignore if already deleted or cannot delete
        pass



async def _purge_old_ui(context: CallbackContext, chat_id: int):
    # Delete previously stored question/summary messages if they exist
    last_id = context.chat_data.pop("last_message_id", None)
    if last_id:
        await _safe_delete(context.bot, chat_id, last_id)
    summary_id = context.chat_data.pop("summary_message_id", None)
    if summary_id:
        await _safe_delete(context.bot, chat_id, summary_id)

# --- Helper: soft purge UI (delete only last open question if has kb, and summary) ---
async def _purge_ui_soft(context: CallbackContext, chat_id: int):
    # Delete only the last question message if it still has an inline keyboard.
    last_id = context.chat_data.get("last_message_id")
    last_has_kb = context.chat_data.get("last_has_kb")
    if last_id and last_has_kb:
        await _safe_delete(context.bot, chat_id, last_id)
        context.chat_data["last_message_id"] = None
        context.chat_data["last_has_kb"] = False
        # Clear any pending send (in-flight question)
        context.chat_data.pop("_sending_question", None)
    summary_id = context.chat_data.pop("summary_message_id", None)
    if summary_id:
        await _safe_delete(context.bot, chat_id, summary_id)

# --- New helper: delete only last open question (with keyboard), not already-answered ones ---
async def _purge_open_question(context: CallbackContext, chat_id: int):
    """Delete only the last question message if it still has an inline keyboard.
    This prevents wiping already-answered questions (which no longer have buttons)."""
    try:
        last_id = context.chat_data.get("last_message_id")
        if last_id and context.chat_data.get("last_has_kb"):
            await _safe_delete(context.bot, chat_id, last_id)
            # reset flags so we don't delete answered messages later
            context.chat_data["last_message_id"] = None
            context.chat_data["last_has_kb"] = False
    except Exception:
        pass

# --- small helper to draw a unicode box around text, wrapping long lines ---
from textwrap import wrap

def _box(text: str, width: int = 48) -> str:
    """Return a Unicode box with the given text, wrapped to a fixed width so
    it looks good in Telegram bubbles.

    - `width` is the maximum characters per line inside the box.
    - Preserves blank lines between paragraphs.
    """
    # Build wrapped lines while preserving paragraph breaks
    wrapped_lines = []
    for para in text.splitlines():
        if not para.strip():
            wrapped_lines.append("")
            continue
        wrapped_lines.extend(wrap(para.strip(), width=width))

    # Compute effective width from the wrapped lines
    eff = min(width, max((len(l) for l in wrapped_lines), default=0))

    top = "┌" + "─" * (eff + 2) + "┐"
    bottom = "└" + "─" * (eff + 2) + "┘"
    body = [f"│ {l.ljust(eff)} │" for l in wrapped_lines]
    return "\n".join([top, *body, bottom])

async def post_init(application):
    commands = [
        BotCommand("start", "Start the quiz"),
        BotCommand("stop", "Stop the quiz")
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

@antispam
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Debounce repeated /start commands (network lag, user double-taps)
    now = time.monotonic()
    last = context.chat_data.get("_last_start_at", 0.0)
    if now - last < LOCK_TTL:
        return
    context.chat_data["_last_start_at"] = now
    # Reset counters/state so a fresh /start never inherits from previous runs
    context.chat_data["wrong_count"] = 0
    context.chat_data["score"] = 0
    context.chat_data["current_index"] = 0
    context.chat_data["paused"] = False
    # Drop any stale exam state
    context.chat_data.pop("used_questions", None)
    context.chat_data.pop("exam_questions", None)

        # --- force clean any dangling UI before we show language picker ---
    chat_id = update.effective_chat.id

    # 1) Try to strip inline keyboard from the last question message (if any).
    last_id = context.chat_data.get("last_message_id")
    if last_id:
        try:
            # Remove inline keyboard if it still exists
            await context.bot.edit_message_reply_markup(
                chat_id=chat_id,
                message_id=last_id,
                reply_markup=None
            )
        except Exception:
            # If we cannot edit (photo/old/changed), just delete it
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=last_id)
            except Exception:
                pass
        finally:
            # Make sure we won't consider it as "open with kb"
            context.chat_data["last_has_kb"] = False
            # Optionally also clear the id to avoid later reuse
            # context.chat_data["last_message_id"] = None

    # 2) Remove lingering summary (finish) message if present
    summary_id = context.chat_data.pop("summary_message_id", None)
    if summary_id:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=summary_id)
        except Exception:
            pass


    # If paused, add Continue button
    lang_options = LANG_OPTIONS.copy()
    if context.chat_data.get("paused"):
        lang_options.append([InlineKeyboardButton("▶️ Continue", callback_data="RESUME_PAUSE")])

    # Remove any previous question/summary with buttons so user can't press old ones
    await _purge_ui_soft(context, update.effective_chat.id)
    # Remove previously sent language prompt if it exists
    old_lang_msg = context.chat_data.pop("lang_prompt_id", None)
    if old_lang_msg:
        await _safe_delete(context.bot, update.effective_chat.id, old_lang_msg)

    # Send quick feedback for cold starts and then morph into the menu
    warm_msg = await update.effective_chat.send_message("⏳ Waking up…")

    edited = await context.bot.edit_message_text(
        chat_id=update.effective_chat.id,
        message_id=warm_msg.message_id,
        text="Please choose your language / Будь ласка, оберіть мову:",
        reply_markup=InlineKeyboardMarkup(lang_options)
    )
    # Remember prompt id (use edited message id if available)
    context.chat_data["lang_prompt_id"] = getattr(edited, "message_id", warm_msg.message_id)
    _release_lock(context.chat_data)
@antispam
async def handle_main_menu(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    import telegram.error
    try:
        await query.answer()
        # Remove the pressed message's buttons and delete it
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        await _purge_ui_soft(context, query.message.chat.id)
        await _safe_delete(context.bot, query.message.chat.id, query.message.message_id)
    except telegram.error.BadRequest as e:
        if "Query is too old" in str(e):
            logger.warning("Callback query too old; skipping answer.")
            return
        else:
            raise
    # Housekeeping: clear stored lang_prompt_id since we delete the message anyway
    context.chat_data.pop("lang_prompt_id", None)
    context.chat_data.clear()
    await start(update, context)

@antispam
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
    await _purge_ui_soft(context, query.message.chat.id)
    # Clear stored language prompt id so old prompts don't linger
    context.chat_data.pop("lang_prompt_id", None)
    lang_mode = "en" if query.data == "lang_en" else "bilingual"
    context.chat_data["lang_mode"] = lang_mode
    context.chat_data["current_index"] = 0
    context.chat_data["score"] = 0
    # New logic for text assignment based on lang_mode
    if lang_mode == "bilingual":
        total = len(QUESTIONS)
        text = (
            "🧠 <b>Learning Mode</b> – shows the correct answer and explanation immediately after each question. Includes all 120 questions.\n"
            f"💡 <i>Tip:</i> send a number (1–{total}) to jump to that question.\n"
            "🧠 <b>Навчальний режим</b> – показує правильну відповідь і пояснення одразу після кожного питання. Усього 120 питань.\n"
            f"💡 <i>Порада:</i> надішліть число (1–{total}), щоб перейти до відповідного питання.\n\n"
            "📝 <b>Exam Mode</b> – 30 random questions, no hints. You must answer at least 25 correctly to pass.\n"
            "📝 <b>Режим іспиту</b> – 30 випадкових питань, без підказок. Для успішного складання потрібно дати щонайменше 25 правильних відповідей.\n\n"
            "Please choose mode / Будь ласка, оберіть режим:"
        )
    elif lang_mode == "en":
        total = len(QUESTIONS)
        text = (
            "🧠 <b>Learning Mode</b> – shows the correct answer and explanation immediately after each question. Includes all 120 questions.\n"
            f"💡 <i>Tip:</i> send a number (1–{total}) to jump to that question.\n\n"
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

@antispam
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
    # Reset mistake counter whenever a mode is (re)started
    context.chat_data["wrong_count"] = 0
    # Make sure no previous question/summary message with buttons remains
    await _purge_ui_soft(context, query.message.chat.id)
    # Reset used_questions only on new exam start
    if mode == "exam":
        # Fresh exam state — do not inherit from Learning mode
        context.chat_data["wrong_count"] = 0
        context.chat_data["score"] = 0
        context.chat_data["current_index"] = 0
        context.chat_data["used_questions"] = []
        import random
        if len(QUESTIONS) < 30:
            await query.edit_message_text("❌ Not enough questions to start the exam. Please add more questions.")
            return
        sample = random.sample(range(len(QUESTIONS)), 30)
        context.chat_data["exam_questions"] = sample

    # Show only selected mode's description after setting mode
    lang = context.chat_data.get("lang_mode", "en")
    selected_mode = mode
    if lang == "en":
        total = len(QUESTIONS)
        await query.edit_message_text(
            "📝 <b>Exam Mode</b> – 30 random questions, no hints. You must answer at least 25 correctly to pass."
            if selected_mode == "exam"
            else "🧠 <b>Learning Mode</b> – shows the correct answer and explanation immediately after each question. Includes all 120 questions.\n"
                 f"💡 <i>Tip:</i> send a number (1–{total}) to jump to that question.",
            parse_mode=ParseMode.HTML
        )
    elif lang == "bilingual":
        total = len(QUESTIONS)
        await query.edit_message_text(
            "📝 <b>Exam Mode</b> – 30 random questions, no hints. You must answer at least 25 correctly to pass.\n"
            "📝 <b>Режим іспиту</b> – 30 випадкових питань, без підказок. Для успішного складання потрібно дати щонайменше 25 правильних відповідей."
            if selected_mode == "exam"
            else "🧠 <b>Learning Mode</b> – shows the correct answer and explanation immediately after each question. Includes all 120 questions.\n"
                 f"💡 <i>Tip:</i> send a number (1–{total}) to jump to that question.\n"
                 "🧠 <b>Навчальний режим</b> – показує правильну відповідь і пояснення одразу після кожного питання. Усього 120 питань.\n"
                 f"💡 <i>Порада:</i> надішліть число (1–{total}), щоб перейти до відповідного питання.",
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

    # Не дозволяємо паралельні відправки питання (анти-спам/дубль-тиски)
    if chat_data.get("_sending_question"):
        return
    chat_data["_sending_question"] = True

    try:
        # Видаляємо лише останнє відкрите питання з клавіатурою (якщо є)
        await _purge_open_question(context, chat_id)

        mode = chat_data.get("mode", "learning")
        if mode == "exam":
            exam_questions = chat_data.get("exam_questions", [])
            used_questions = chat_data.get("used_questions", [])
            used_ids = set(used_questions)

            # залишки ще не використаних
            remaining = [qidx for qidx in exam_questions if qidx not in used_ids]
            if not remaining:
                await send_score(chat_id, context)
                return

            # шукаємо перше не використане починаючи з current_index
            next_qidx = None
            start = chat_data.get("current_index", 0)
            for i in range(start, len(exam_questions)):
                if exam_questions[i] not in used_ids:
                    next_qidx = exam_questions[i]
                    chat_data["current_index"] = i
                    break

            # якщо після start нічого не знайшлось — беремо з початку
            if next_qidx is None:
                for i, qidx in enumerate(exam_questions):
                    if qidx not in used_ids:
                        next_qidx = qidx
                        chat_data["current_index"] = i
                        break

            if next_qidx is None:
                await send_score(chat_id, context)
                return

            chat_data.setdefault("used_questions", []).append(next_qidx)
            q = QUESTIONS[next_qidx]
        else:
            if index >= len(QUESTIONS):
                await send_score(chat_id, context)
                return
            q = QUESTIONS[index]

        # Заголовок
        if mode == "exam":
            total_questions = len(chat_data.get("exam_questions", []))
            wrong_count = chat_data.get("wrong_count", 0)
            position = len(chat_data.get("used_questions", []))
            header = f"<i><b>Question {position} of {total_questions} ({wrong_count} Fails ❌)</b></i>"
        else:
            total_questions = len(QUESTIONS)
            position = index + 1
            wrong_count = chat_data.get("wrong_count", 0)
            correct_count = chat_data.get("score", 0)
            header = (
                f"<i><b>Question {position} of {total_questions} "
                f"({wrong_count} Fails, {correct_count} Correct)</b></i>"
            )

        lines = [header, ""]

        # Текст питання
        if lang_mode == "bilingual":
            lines.append(f"<b>🇬🇧 {q['question']}</b>")
            lines.append(f"<b>🇺🇦 {q['question_uk']}</b>")
        else:
            # якщо 'en' — прапор не показуємо
            if lang_mode == "en":
                lines.append(f"<b>{q['question']}</b>")
            else:
                lines.append(f"<b>🇬🇧 {q['question']}</b>")

        lines.append("🚗═════════════════════════🚦")

        # Варіанти
        option_labels = ["A", "B", "C", "D"]
        options_en = q["options"]
        options_uk = q.get("options_uk", [])
        for idx, label in enumerate(option_labels):
            if lang_mode == "bilingual" and options_uk:
                lines.append(f"       <b>{label}.</b> {options_en[idx]} / {options_uk[idx]}")
            else:
                lines.append(f"       <b>{label}.</b> {options_en[idx]}")

        # Картинка (якщо є)
        image_filename = None
        for ext in [".jpg", ".jpeg", ".png", ".webp"]:
            path = f"images/{q['question_number']}{ext}"
            if os.path.exists(path):
                image_filename = path
                break

        text = "\n".join(lines)
        keyboard = build_option_keyboard()

        # Відправка
        if image_filename:
            with open(image_filename, "rb") as photo:
                msg = await context.bot.send_photo(
                    chat_id=chat_id,
                    photo=photo,
                    caption=text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=keyboard
                )
        else:
            msg = await context.bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard
            )

        # Позначаємо, що є активна клавіатура в останньому повідомленні
        chat_data["last_message_id"] = msg.message_id
        chat_data["last_has_kb"] = True
        chat_data.pop("summary_message_id", None)
        # Reset per-message consume guard so next question can be handled
        chat_data["_consumed_msg_id"] = None

    except Exception:
        logger.exception("Failed to send question")
    finally:
        # Гарантовано знімаємо прапорець відправки
        chat_data["_sending_question"] = False

async def send_score(chat_id: int, context: CallbackContext) -> None:
    chat_data = context.chat_data
    mode = chat_data.get("mode", "learning")
    score = chat_data.get("score", 0)
    lang = chat_data.get("lang_mode", "en")

    if mode == "exam":
        total = len(chat_data.get("exam_questions", []))
        passed = score >= 25
        result_en = "✅ You passed the exam!" if passed else "❌ You did not pass the exam."
        result_uk = "✅ Ви склали іспит!" if passed else "❌ Ви не склали іспит."

        text = (
            f"<b>🎉 You scored {score} out of {total}!</b>\n"
            f"{result_en}\n\n"
            f"<b>🇺🇦 Ви набрали {score} із {total} балів!</b>\n"
            f"{result_uk}"
        )
        buttons = [
            [InlineKeyboardButton("🔁 Start Exam Again", callback_data="mode_exam")],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")],
        ]
    else:
        # Learning mode summary
        total = len(QUESTIONS)
        wrong = chat_data.get("wrong_count", 0)
        correct = score

        if lang == "bilingual":
            text = (
                f"<b>📚 Learning finished!</b>\n"
                f"✅ Correct: <b>{correct}</b>\n❌ Fails: <b>{wrong}</b>\n"
                f"— — — — — — — — — —\n"
                f"<b>📚 Навчання завершено!</b>\n"
                f"✅ Правильних: <b>{correct}</b>\n❌ Помилок: <b>{wrong}</b>"
            )
        else:
            text = (
                f"<b>📚 Learning finished!</b>\n"
                f"✅ Correct: <b>{correct}</b>\n❌ Fails: <b>{wrong}</b>"
            )

        # Offer to restart learning or start exam, and main menu
        buttons = [
            [InlineKeyboardButton("🔁 Restart Learning", callback_data="mode_learning"),
             InlineKeyboardButton("📝 Start Exam", callback_data="mode_exam")],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")],
        ]

    msg = await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(buttons),
    )
    # Remember summary id so we can delete/disable it on next actions
    context.chat_data["summary_message_id"] = msg.message_id

    # Clear state after summary is shown so next action starts fresh
    chat_data.clear()

async def quiz_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Reset state and behave exactly like /start
    context.chat_data.clear()
    await _purge_ui_soft(context, update.effective_chat.id)
    await start(update, context)

@antispam
async def answer_handler(update: Update, context: CallbackContext) -> None:
    # Support both button (callback_query) and text answers (update.message)
    import telegram.error
    chat_data = context.chat_data
    # If this is a callback query (button answer)
    if update.callback_query:
        query = update.callback_query
        # --- Early drop of stale callbacks ---
        # Ignore callbacks that aren't from the last message with active keyboard
        if _is_stale_callback(context.chat_data, query.message.message_id):
            try:
                await query.answer()
            except Exception:
                pass
            return
        try:
            await query.answer()
            try:
                await query.edit_message_reply_markup(reply_markup=None)
            except Exception:
                pass
        except telegram.error.BadRequest as e:
            if "Query is too old" in str(e):
                logger.warning("Callback query too old; skipping answer.")
            else:
                raise
        # --- Per-message consume guard: process each question only once even if user taps many times ---
        consumed_id = context.chat_data.get("_consumed_msg_id")
        if consumed_id == query.message.message_id:
            # already handled this message; politely ack and stop
            try:
                await query.answer("⏳ Please wait…")
            except Exception:
                pass
            return
        context.chat_data["_consumed_msg_id"] = query.message.message_id
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
            # Track mistakes (exam and learning)
            if not is_correct:
                if mode in ("exam", "learning"):
                    chat_data["wrong_count"] = chat_data.get("wrong_count", 0) + 1
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
            wrong_count = chat_data.get("wrong_count", 0)
            # --- Insert fail fast logic for exam mode ---
            if mode == "exam" and wrong_count >= 6:
                text = (
                    f"<b>❌ You made {wrong_count} mistakes. Test failed.</b>\n\n"
                    f"<b>🇺🇦 Ви зробили {wrong_count} помилок. Тест не складено.</b>\n\n"
                )
                keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔁 Try Again / Спробувати ще раз", callback_data="mode_exam")],
                    [InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")]
                ])
                await query.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
                chat_data.clear()
                return
            # --- End fail fast logic ---
            if mode == "exam":
                position = len(chat_data.get("used_questions", []))
                result_title = f"<i><b>Question {position} of {total_questions} ({wrong_count} Fails ❌)</b></i>"
            else:
                correct_count = chat_data.get("score", 0)
                result_title = (
                    f"<i><b>Question {current_index + 1} of {total_questions} "
                    f"({wrong_count} Fails, {correct_count} Correct)</b></i>"
                )
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
            full_text.append("🚗═════════════════════════🚦")
            full_text += options_text
            # Do not show explanation in exam mode
            # Show explanation only in learning mode, and only if correct
            if mode == "exam" or not is_correct:
                pass
            else:
                if mode == "learning" and "explanation" in question:
                    full_text.append("🚗═════════════════════════🚦")
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
                    context.chat_data["last_has_kb"] = False
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
                    context.chat_data["last_has_kb"] = False
            else:
                if query.message and query.message.text:
                    msg = await query.edit_message_text(
                        text=formatted_question,
                        reply_markup=None,
                        parse_mode=ParseMode.HTML
                    )
                    context.chat_data["last_message_id"] = msg.message_id
                    context.chat_data["last_has_kb"] = False
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

        # Numeric jump is ONLY for Learning Mode
        if user_msg.isdigit():
            if mode != "learning":
                await update.message.reply_text("ℹ️ Jump by question number is available only in Learning Mode.")
                return
            # Jump to a specific question number in Learning
            n = int(user_msg)
            total = len(QUESTIONS)
            if 1 <= n <= total:
                # When jumping, remove the previous question message if it still has an inline keyboard
                last_id = chat_data.get("last_message_id")
                last_has_kb = chat_data.get("last_has_kb")
                if last_id and last_has_kb:
                    try:
                        await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=last_id)
                    except Exception as _:
                        # If deletion fails (already gone/edited), ignore
                        pass
                    chat_data["last_message_id"] = None
                    chat_data["last_has_kb"] = False
                chat_data["current_index"] = n - 1
                await send_question(update.effective_chat.id, context)
            else:
                await update.message.reply_text(f"⚠️ Please enter a number from 1 to {total}.")
            return
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
        elif mode == "learning":
            chat_data["wrong_count"] = chat_data.get("wrong_count", 0) + 1
        # Prepare feedback message
        feedback_lines = []
        if is_correct:
            feedback_lines.append("✅ Correct!")
        else:
            feedback_lines.append("❌ Incorrect.")
        # In learning mode, show explanation if correct
        if mode == "learning" and "explanation" in question:
            feedback_lines.append("🚗═════════════════════════🚦")
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

@antispam
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
    application.add_handler(CommandHandler("stop", stop_command))
    application.add_handler(CallbackQueryHandler(next_handler, pattern="^(NEXT|CONTINUE|RESTART)$"))
    application.add_handler(CallbackQueryHandler(answer_handler, pattern="^[ABCDSTOP]{1,4}$"))
    application.add_handler(CallbackQueryHandler(handle_language, pattern="^lang_.*$"))
    application.add_handler(CallbackQueryHandler(handle_mode, pattern="^mode_.*$"))
    application.add_handler(CallbackQueryHandler(handle_pause, pattern="^mode_pause$"))
    application.add_handler(CallbackQueryHandler(handle_resume_pause, pattern="^RESUME_PAUSE$"))
    application.add_handler(CallbackQueryHandler(handle_main_menu, pattern="^MAIN_MENU$"))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), answer_handler))

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
@antispam
async def handle_pause(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    try:
        await query.answer()
        await _purge_ui_soft(context, query.message.chat.id)
    except telegram.error.BadRequest as e:
        if "Query is too old" in str(e):
            logger.warning("Callback query too old; skipping answer.")
            return
        else:
            raise
    context.chat_data["paused"] = True
    context.chat_data["resume_question"] = context.chat_data.get("current_index", 0)
    await query.edit_message_text("⏸ Test paused. You can continue anytime by selecting Continue from the main menu.")

@antispam
async def handle_resume_pause(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    try:
        await query.answer()
        await _purge_ui_soft(context, query.message.chat.id)
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