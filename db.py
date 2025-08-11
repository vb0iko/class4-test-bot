import os
import time
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

_engine = create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)

DDL = """
CREATE TABLE IF NOT EXISTS users (
  id BIGSERIAL PRIMARY KEY,
  chat_id BIGINT UNIQUE,
  lang_mode TEXT,
  created_at TIMESTAMPTZ DEFAULT now()
);
CREATE TABLE IF NOT EXISTS sessions (
  id BIGSERIAL PRIMARY KEY,
  user_id BIGINT REFERENCES users(id),
  mode TEXT,
  started_at TIMESTAMPTZ DEFAULT now(),
  finished_at TIMESTAMPTZ,
  score INT DEFAULT 0,
  wrong INT DEFAULT 0,
  total INT,
  status TEXT
);
CREATE TABLE IF NOT EXISTS answers (
  id BIGSERIAL PRIMARY KEY,
  session_id BIGINT REFERENCES sessions(id),
  question_number INT,
  selected INT,
  correct INT,
  is_correct BOOLEAN,
  answered_at TIMESTAMPTZ DEFAULT now(),
  latency_ms INT
);
"""

def init_db():
    # кілька ретраїв, якщо БД прокидається
    for _ in range(10):
        try:
            with _engine.begin() as conn:
                conn.exec_driver_sql(DDL)
            return
        except Exception:
            time.sleep(1)

def get_session():
    return Session(_engine)

# ——— хелпери для main.py ———

def ensure_user(chat_id: int, lang_mode: str):
    with get_session() as s:
        row = s.execute(text("SELECT id FROM users WHERE chat_id=:c"), {"c": chat_id}).first()
        if not row:
            s.execute(text("INSERT INTO users (chat_id, lang_mode) VALUES (:c,:l)"), {"c": chat_id, "l": lang_mode})
        else:
            s.execute(text("UPDATE users SET lang_mode=:l WHERE chat_id=:c"), {"l": lang_mode, "c": chat_id})
        s.commit()

def start_session(chat_id: int, mode: str, total: int) -> int:
    with get_session() as s:
        uid = s.execute(text("SELECT id FROM users WHERE chat_id=:c"), {"c": chat_id}).scalar_one()
        sid = s.execute(
            text("INSERT INTO sessions (user_id, mode, total, status) VALUES (:u,:m,:t,'in_progress') RETURNING id"),
            {"u": uid, "m": mode, "t": total},
        ).scalar_one()
        s.commit()
        return sid

def log_answer(session_id: int, qnum: int, selected: int, correct: int, ok: bool, latency_ms: int | None):
    with get_session() as s:
        s.execute(
            text(
                """
                INSERT INTO answers (session_id, question_number, selected, correct, is_correct, latency_ms)
                VALUES (:sid, :q, :sel, :cor, :ok, :lat)
                """
            ),
            {"sid": session_id, "q": qnum, "sel": selected, "cor": correct, "ok": ok, "lat": latency_ms},
        )
        if ok:
            s.execute(text("UPDATE sessions SET score = score + 1 WHERE id=:sid"), {"sid": session_id})
        else:
            s.execute(text("UPDATE sessions SET wrong = wrong + 1 WHERE id=:sid"), {"sid": session_id})
        s.commit()

def finish_session(session_id: int, status: str):
    with get_session() as s:
        s.execute(text("UPDATE sessions SET finished_at=now(), status=:st WHERE id=:sid"),
                  {"sid": session_id, "st": status})
        s.commit()