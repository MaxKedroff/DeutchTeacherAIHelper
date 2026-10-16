import os
import re
import json
import uuid
import sqlite3
import logging
import traceback
import sys
from pathlib import Path
from datetime import datetime, date, time as dtime, timedelta

import streamlit as st
import pandas as pd


# ---------- Санитайзер JSON для ответов LLM ----------
def _sanitize_json(text: str) -> str:
    text = re.sub(r",(\s*[}\]])", r"\1", text)
    text = re.sub(r"//[^\n]*", "", text)
    return text


APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "data"
AUDIO_DIR = DATA_DIR / "audio"
DB_PATH = DATA_DIR / "lessons.sqlite3"
TEACHER_NAME = "Эвелина Акбашева"
MODEL_NAME = os.getenv("GIGACHAT_MODEL", "GigaChat")

DATA_DIR.mkdir(exist_ok=True)
AUDIO_DIR.mkdir(exist_ok=True)

st.set_page_config(
    page_title="Deutsch Lernanalyse",
    page_icon="📚",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------- ЛОГГИРОВАНИЕ ----------
LOG_PATH = APP_DIR / "app.log"
logger = logging.getLogger("deutsch_app")
logger.setLevel(logging.DEBUG)
logger.propagate = False
if not logger.handlers:
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.DEBUG)
    ch.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S"))
    logger.addHandler(ch)
    fh = logging.FileHandler(LOG_PATH, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s %(name)s: %(message)s"))
    logger.addHandler(fh)
logger.info("=== Приложение запущено. Лог: %s ===", LOG_PATH)
# ----------------------------------

# ---------- Стили ----------
st.markdown("""
<style>
    .block-container {padding-top: 1.6rem; padding-bottom: 3rem; max-width: 1400px;}
    [data-testid="stSidebar"] {border-right: 1px solid rgba(120,120,120,.18);}
    .hero {
        padding: 1.5rem 1.7rem; border-radius: 20px;
        background: linear-gradient(120deg, rgba(72,113,184,.18), rgba(99,170,151,.13));
        border: 1px solid rgba(120,140,170,.20); margin-bottom: 1.2rem;
    }
    .hero h1 {margin: 0 0 .35rem 0; font-size: 2rem;}
    .hero p {margin: 0; color: var(--text-color); opacity: .78;}
    .segment {
        padding: .8rem 1rem; border: 1px solid rgba(120,120,120,.2);
        border-radius: 12px; margin: .45rem 0;
    }
    .pill {
        display: inline-block; padding: .18rem .55rem; border-radius: 999px;
        background: rgba(90,130,180,.15); font-size: .78rem;
    }
    div[data-testid="stMetric"] {
        background: rgba(120,130,140,.07); padding: 1rem; border-radius: 14px;
        border: 1px solid rgba(120,130,140,.13);
    }
    /* Карточки календаря */
    .cal-card {
        border: 1px solid rgba(120,120,120,.25);
        border-left: 5px solid #4871b8;
        border-radius: 14px;
        padding: 1rem 1.1rem;
        margin-bottom: .9rem;
        background: rgba(120,130,140,.05);
    }
    .cal-card.overdue {border-left-color: #d9534f; background: rgba(217,83,79,.06);}
    .cal-card.done {border-left-color: #4caf50;}
    .cal-card h4 {margin: 0 0 .3rem 0;}
    .cal-meta {opacity: .75; font-size: .86rem; margin-bottom: .5rem;}
    .hw-badge {
        display: inline-block; padding: .12rem .5rem; border-radius: 999px;
        font-size: .76rem; margin-left: .35rem;
    }
        .hw-badge.ok {background: rgba(76,175,80,.18); color: #2e7d32;}
    .hw-badge.bad {background: rgba(217,83,79,.18); color: #b71c1c;}
    .hw-badge.pending {background: rgba(255,193,7,.20); color: #8d6e00;}

    /* Ежедневник */
    .week-grid {
        display: grid;
        grid-template-columns: repeat(7, minmax(0, 1fr));
        gap: .55rem;
    }
    .day-col {
        border: 1px solid rgba(120,120,120,.18);
        border-radius: 12px;
        padding: .5rem .55rem;
        background: rgba(120,130,140,.04);
        min-height: 220px;
    }
    .day-col.today {
        border-color: #4871b8;
        box-shadow: 0 0 0 2px rgba(72,113,184,.15) inset;
    }
    .day-head {
        font-weight: 600;
        font-size: .9rem;
        margin-bottom: .35rem;
        display: flex;
        justify-content: space-between;
        align-items: baseline;
    }
    .day-head .wd {opacity: .65; font-weight: 400; font-size: .8rem;}
    .evt {
        border-left: 4px solid #4871b8;
        background: rgba(72,113,184,.10);
        border-radius: 8px;
        padding: .35rem .5rem;
        margin-bottom: .35rem;
        font-size: .82rem;
        line-height: 1.25;
    }
    .evt .t {opacity: .7; font-size: .75rem;}
    .evt.done {border-left-color: #4caf50; background: rgba(76,175,80,.10);}
    .evt.overdue {border-left-color: #d9534f; background: rgba(217,83,79,.10);}
    .evt .grp {font-weight: 600; display: block;}
    .evt .ttl {display: block; margin-top: .1rem;}
    .day-empty {
        opacity: .5; font-size: .8rem; text-align: center; padding: 1rem 0;
    }
</style>
""", unsafe_allow_html=True)


# ---------- БД ----------
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    with db() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS students (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            normalized_name TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS lessons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            lesson_date TEXT NOT NULL,
            topic TEXT DEFAULT '',
            group_name TEXT DEFAULT '',
            audio_path TEXT DEFAULT '',
            transcript_filename TEXT DEFAULT '',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS segments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lesson_id INTEGER NOT NULL REFERENCES lessons(id) ON DELETE CASCADE,
            segment_index INTEGER NOT NULL,
            start_seconds REAL NOT NULL,
            end_seconds REAL NOT NULL,
            speaker_name TEXT NOT NULL,
            speaker_role TEXT NOT NULL,
            student_id INTEGER REFERENCES students(id),
            text TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lesson_id INTEGER NOT NULL REFERENCES lessons(id) ON DELETE CASCADE,
            student_id INTEGER REFERENCES students(id),
            category TEXT NOT NULL,
            topic TEXT DEFAULT '',
            description TEXT NOT NULL,
            evidence TEXT DEFAULT '',
            start_seconds REAL,
            end_seconds REAL,
            confidence REAL,
            recommended_action TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'Требует проверки',
            created_at TEXT NOT NULL
        );

        -- NEW: группы, состав групп, расписание
        CREATE TABLE IF NOT EXISTS groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            level TEXT DEFAULT '',
            note TEXT DEFAULT '',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS group_students (
            group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
            student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
            PRIMARY KEY (group_id, student_id)
        );
        CREATE TABLE IF NOT EXISTS group_schedule (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
            weekday INTEGER NOT NULL,           -- 0=Пн ... 6=Вс
            start_time TEXT NOT NULL,           -- "HH:MM"
            duration_min INTEGER DEFAULT 60
        );

        -- NEW: экземпляры занятий (карточки календаря)
        CREATE TABLE IF NOT EXISTS planned_lessons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
            scheduled_at TEXT NOT NULL,         -- ISO datetime
            title TEXT DEFAULT '',
            topic TEXT DEFAULT '',
            is_prepared INTEGER DEFAULT 0,
            is_conducted INTEGER DEFAULT 0,
            lesson_id INTEGER REFERENCES lessons(id) ON DELETE SET NULL,
            homework_due_date TEXT DEFAULT '',
            created_at TEXT NOT NULL
        );

        -- NEW: статус домашки по каждому студенту
        CREATE TABLE IF NOT EXISTS homework_status (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            planned_lesson_id INTEGER NOT NULL REFERENCES planned_lessons(id) ON DELETE CASCADE,
            student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
            is_done INTEGER DEFAULT 0,
            done_at TEXT DEFAULT '',
            UNIQUE (planned_lesson_id, student_id)
        );
        """)
        ensure_student(con, TEACHER_NAME)


# ---------- Утилиты ----------
def normalize_name(name):
    return re.sub(r"\s+", " ", (name or "").strip()).casefold()


def ensure_student(con, name):
    normalized = normalize_name(name)
    if not normalized:
        return None
    row = con.execute("SELECT id FROM students WHERE normalized_name=?", (normalized,)).fetchone()
    if row:
        return row["id"]
    cur = con.execute(
        "INSERT INTO students(name, normalized_name, created_at) VALUES(?,?,?)",
        (name.strip(), normalized, datetime.now().isoformat(timespec="seconds"))
    )
    return cur.lastrowid


def parse_timestamp(value):
    value = value.strip().replace(",", ".")
    parts = value.split(":")
    if len(parts) == 3:
        h, m, s = parts
    elif len(parts) == 2:
        h, m, s = "0", parts[0], parts[1]
    else:
        raise ValueError(f"Не удалось разобрать временную метку: {value}")
    return int(h) * 3600 + int(m) * 60 + float(s)


def parse_vtt(content):
    content = content.lstrip("\ufeff")
    blocks = re.split(r"\n\s*\n", content.strip())
    segments = []
    for block in blocks:
        lines = [line.strip("\r") for line in block.splitlines() if line.strip()]
        if not lines or lines[0].strip() == "WEBVTT":
            continue
        idx = next((i for i, line in enumerate(lines) if "-->" in line), None)
        if idx is None:
            continue
        m = re.match(r"\s*(\S+)\s+-->\s+(\S+)", lines[idx])
        if not m:
            continue
        start = parse_timestamp(m.group(1))
        end = parse_timestamp(m.group(2))
        text = " ".join(lines[idx + 1:]).strip()
        if not text:
            continue
        speaker, utterance = "Не определён", text
        sm = re.match(r"^([^:：]{1,100})[:：]\s*(.*)$", text)
        if sm:
            speaker = sm.group(1).strip()
            utterance = sm.group(2).strip()
        segments.append({
            "start_seconds": start, "end_seconds": end,
            "speaker_name": speaker, "text": utterance or text,
        })
    return segments


def fmt_time(seconds):
    if seconds is None:
        return "—"
    seconds = max(0, float(seconds))
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}" if h else f"{m:02d}:{s:06.3f}"


WEEKDAYS_RU = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]


def get_counts():
    with db() as con:
        students = con.execute("SELECT COUNT(*) n FROM students WHERE normalized_name != ?",
                               (normalize_name(TEACHER_NAME),)).fetchone()["n"]
        lessons = con.execute("SELECT COUNT(*) n FROM lessons").fetchone()["n"]
        segments = con.execute("SELECT COUNT(*) n FROM segments").fetchone()["n"]
        observations = con.execute("SELECT COUNT(*) n FROM observations").fetchone()["n"]
    return students, lessons, segments, observations


def get_lesson_list():
    with db() as con:
        return pd.read_sql_query("""
            SELECT l.id, l.title, l.lesson_date, l.topic, l.group_name,
                   COUNT(DISTINCT s.id) AS segments_count,
                   COUNT(DISTINCT o.id) AS observations_count
            FROM lessons l
            LEFT JOIN segments s ON s.lesson_id=l.id
            LEFT JOIN observations o ON o.lesson_id=l.id
            GROUP BY l.id
            ORDER BY l.lesson_date DESC, l.id DESC
        """, con)


# ---------- НОВОЕ: группы ----------
def list_groups():
    with db() as con:
        return con.execute("SELECT * FROM groups ORDER BY name COLLATE NOCASE").fetchall()


def create_group(name, level, note):
    with db() as con:
        cur = con.execute(
            "INSERT INTO groups(name, level, note, created_at) VALUES(?,?,?,?)",
            (name.strip(), level.strip(), note.strip(),
             datetime.now().isoformat(timespec="seconds"))
        )
        return cur.lastrowid


def update_group(group_id, name, level, note):
    with db() as con:
        con.execute("UPDATE groups SET name=?, level=?, note=? WHERE id=?",
                    (name.strip(), level.strip(), note.strip(), group_id))


def delete_group(group_id):
    with db() as con:
        con.execute("DELETE FROM groups WHERE id=?", (group_id,))


def set_group_schedule(group_id, schedule_rows):
    """schedule_rows: list of (weekday:int, 'HH:MM', duration_min:int)"""
    with db() as con:
        con.execute("DELETE FROM group_schedule WHERE group_id=?", (group_id,))
        for wd, tm, dur in schedule_rows:
            con.execute(
                "INSERT INTO group_schedule(group_id, weekday, start_time, duration_min) VALUES(?,?,?,?)",
                (group_id, wd, tm, dur)
            )


def get_group_schedule(group_id):
    with db() as con:
        return con.execute(
            "SELECT * FROM group_schedule WHERE group_id=? ORDER BY weekday, start_time",
            (group_id,)
        ).fetchall()


def get_group_members(group_id):
    with db() as con:
        return con.execute("""
            SELECT s.id, s.name FROM students s
            JOIN group_students gs ON gs.student_id = s.id
            WHERE gs.group_id=?
            ORDER BY s.name COLLATE NOCASE
        """, (group_id,)).fetchall()


def set_group_members(group_id, student_ids):
    with db() as con:
        con.execute("DELETE FROM group_students WHERE group_id=?", (group_id,))
        for sid in student_ids:
            con.execute("INSERT OR IGNORE INTO group_students(group_id, student_id) VALUES(?,?)",
                        (group_id, sid))


def get_all_students():
    with db() as con:
        return con.execute(
            "SELECT id, name FROM students WHERE normalized_name != ? ORDER BY name COLLATE NOCASE",
            (normalize_name(TEACHER_NAME),)
        ).fetchall()


# ---------- НОВОЕ: календарь занятий ----------
def upsert_planned_lessons_for_window(group_id, days_ahead=60):
    """
    Материализует карточки занятий по расписанию группы на N дней вперёд
    (если их ещё нет).
    """
    sched = get_group_schedule(group_id)
    if not sched:
        return
    today = date.today()
    with db() as con:
        for offset in range(0, days_ahead):
            d = today + timedelta(days=offset)
            for s in sched:
                if d.weekday() != s["weekday"]:
                    continue
                hh, mm = s["start_time"].split(":")
                dt = datetime.combine(d, dtime(int(hh), int(mm)))
                iso = dt.isoformat(timespec="minutes")
                row = con.execute(
                    "SELECT id FROM planned_lessons WHERE group_id=? AND scheduled_at=?",
                    (group_id, iso)
                ).fetchone()
                if not row:
                    con.execute(
                        """INSERT INTO planned_lessons
                           (group_id, scheduled_at, title, topic, created_at)
                           VALUES(?,?,?,?,?)""",
                        (group_id, iso, "", "", datetime.now().isoformat(timespec="seconds"))
                    )


def get_upcoming_planned(days_ahead=14, group_id=None):
    with db() as con:
        today = date.today()
        end = today + timedelta(days=days_ahead)
        params = [today.isoformat(), (end + timedelta(days=1)).isoformat()]
        sql = """
            SELECT pl.*, g.name AS group_name,
                   l.title AS conducted_lesson_title
            FROM planned_lessons pl
            JOIN groups g ON g.id = pl.group_id
            LEFT JOIN lessons l ON l.id = pl.lesson_id
            WHERE date(pl.scheduled_at) >= ? AND date(pl.scheduled_at) < ?
        """
        if group_id:
            sql += " AND pl.group_id = ?"
            params.append(group_id)
        sql += " ORDER BY pl.scheduled_at"
        return con.execute(sql, params).fetchall()


def get_planned_in_range(start_date: date, end_date: date, group_id=None):
    """Возвращает карточки занятий в диапазоне [start_date; end_date] включительно."""
    with db() as con:
        params = [start_date.isoformat(), (end_date + timedelta(days=1)).isoformat()]
        sql = """
            SELECT pl.*, g.name AS group_name
            FROM planned_lessons pl
            JOIN groups g ON g.id = pl.group_id
            WHERE date(pl.scheduled_at) >= ? AND date(pl.scheduled_at) < ?
        """
        if group_id:
            sql += " AND pl.group_id = ?"
            params.append(group_id)
        sql += " ORDER BY pl.scheduled_at"
        return con.execute(sql, params).fetchall()


def update_planned(planned_id, **fields):
    if not fields:
        return
    sets = ", ".join(f"{k}=?" for k in fields)
    params = list(fields.values()) + [planned_id]
    with db() as con:
        con.execute(f"UPDATE planned_lessons SET {sets} WHERE id=?", params)


def get_planned(planned_id):
    with db() as con:
        return con.execute("SELECT * FROM planned_lessons WHERE id=?", (planned_id,)).fetchone()


def get_homework_rows(planned_id):
    with db() as con:
        return con.execute("""
            SELECT hs.*, s.name AS student_name
            FROM homework_status hs
            JOIN students s ON s.id = hs.student_id
            WHERE hs.planned_lesson_id=?
            ORDER BY s.name COLLATE NOCASE
        """, (planned_id,)).fetchall()


def ensure_homework_rows(planned_id, group_id):
    members = get_group_members(group_id)
    with db() as con:
        for m in members:
            con.execute(
                "INSERT OR IGNORE INTO homework_status(planned_lesson_id, student_id) VALUES(?,?)",
                (planned_id, m["id"])
            )


def set_homework_done(planned_id, student_id, done):
    with db() as con:
        con.execute(
            "UPDATE homework_status SET is_done=?, done_at=? WHERE planned_lesson_id=? AND student_id=?",
            (1 if done else 0, datetime.now().isoformat(timespec="seconds") if done else "",
             planned_id, student_id)
        )


def homework_state(planned_row):
    """
    Возвращает 'none' | 'ok' | 'pending' | 'overdue'
    """
    if not planned_row["homework_due_date"]:
        return "none"
    rows = get_homework_rows(planned_row["id"])
    if not rows:
        return "none"
    all_done = all(r["is_done"] for r in rows)
    if all_done:
        return "ok"
    today_iso = date.today().isoformat()
    if planned_row["homework_due_date"] < today_iso:
        return "overdue"
    return "pending"


# ---------- Анализ GigaChat (без изменений) ----------
def run_ai_analysis(lesson_id):
    logger.info("---- run_ai_analysis START lesson_id=%s ----", lesson_id)

    credentials = os.getenv("GIGACHAT_CREDENTIALS", "").strip()
    if not credentials:
        return False, "Не задан GIGACHAT_CREDENTIALS."
    try:
        from gigachat import GigaChat
    except ImportError:
        return False, "Не установлен пакет gigachat."

    with db() as con:
        lesson = con.execute("SELECT * FROM lessons WHERE id=?", (lesson_id,)).fetchone()
        rows = con.execute("""
            SELECT start_seconds, end_seconds, speaker_name, speaker_role, text
            FROM segments WHERE lesson_id=? ORDER BY segment_index
        """, (lesson_id,)).fetchall()
    if not lesson:
        return False, "Урок не найден."
    transcript = "\n".join(
        f"[{fmt_time(r['start_seconds'])}-{fmt_time(r['end_seconds'])}] "
        f"{r['speaker_name']} ({r['speaker_role']}): {r['text']}" for r in rows
    )
    if not transcript.strip():
        return False, "В уроке нет реплик."

    prompt = f"""
Ты — аналитик уроков немецкого языка. Анализируй только подтверждаемые наблюдения.
Не выдумывай ошибки; отличай ошибки немецкого от вероятных ошибок транскрибации.
Реплики преподавателя — контекст, а не ошибки ученика.
Пиши описание и рекомендации на русском. Цитаты evidence должны точно совпадать с транскриптом.
Выведи столько ошибок, сколько сможешь найти.
Верни ТОЛЬКО валидный JSON (RFC 8259). Запрещено: trailing commas, комментарии, одинарные кавычки.

Схема:
{{
 "lesson_summary": "...",
 "observations": [
  {{
   "student_name": "имя или null",
   "category": "категория",
   "topic": "тема/навык",
   "description": "что произошло",
   "evidence": "точная цитата",
   "timestamp_start": 0.0,
   "timestamp_end": 1.0,
   "confidence": 0.0,
   "recommended_action": "шаг"
  }}
 ]
}}
Урок: {lesson['title']}
Тема: {lesson['topic'] or 'не указана'}
Транскрипт:
{transcript}
"""
    try:
        giga = GigaChat(credentials=credentials, base_url="https://api.giga.chat/v1",
                        model="GigaChat-3-Ultra", verify_ssl_certs=False, max_tokens=20000)
        response = giga.chat(prompt)
        content = response.choices[0].message.content
    except Exception:
        logger.exception("Ошибка GigaChat")
        return False, "Ошибка обращения к GigaChat. См. app.log."

    try:
        m = re.search(r"\{.*\}", content, re.DOTALL)
        if not m:
            return False, "Не найден JSON в ответе модели."
        payload = json.loads(_sanitize_json(m.group(0)))
        observations = payload.get("observations", [])
    except Exception:
        logger.exception("Ошибка парсинга JSON")
        return False, "Невалидный JSON от модели. См. app.log."

    with db() as con:
        con.execute("DELETE FROM observations WHERE lesson_id=?", (lesson_id,))
        for item in observations:
            name = item.get("student_name")
            sid = None
            if name and normalize_name(name) != normalize_name(TEACHER_NAME):
                sid = ensure_student(con, name)
            con.execute("""
                INSERT INTO observations
                (lesson_id, student_id, category, topic, description, evidence,
                 start_seconds, end_seconds, confidence, recommended_action, status, created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """, (lesson_id, sid, item.get("category", "Другое"), item.get("topic", ""),
                  item.get("description", ""), item.get("evidence", ""),
                  item.get("timestamp_start"), item.get("timestamp_end"),
                  item.get("confidence"), item.get("recommended_action", ""),
                  "Требует проверки", datetime.now().isoformat(timespec="seconds")))
    return True, f"Анализ завершён. Наблюдений: {len(observations)}."


# ---------- Инициализация ----------
init_db()

# ---------- Боковая навигация ----------
# ---------- Боковая навигация ----------
PAGES = ["Обзор", "Добавить урок", "Календарь", "Ежедневник",
         "Уроки и транскрипты", "Группы", "Ученики", "Аналитика"]

if "nav" not in st.session_state:
    st.session_state["nav"] = "Обзор"

# применяем отложенный переход ДО отрисовки radio
if "pending_nav" in st.session_state:
    target = st.session_state.pop("pending_nav")
    if target in PAGES:
        st.session_state["nav"] = target

with st.sidebar:
    st.markdown("## 📚 Deutsch Lernanalyse")
    st.caption("Аналитика уроков немецкого языка")
    page = st.radio(
        "Навигация",
        PAGES,
        key="nav",
        label_visibility="collapsed",
    )
    st.divider()
    st.markdown("**Рабочая база**")
    st.caption("SQLite · локальное хранение")
    st.caption(f"Преподаватель: {TEACHER_NAME}")


# =====================================================================
#                                  ОБЗОР
# =====================================================================
if page == "Обзор":
    st.markdown(f"""
    <div class="hero">
      <h1>Guten Tag 👋</h1>
      <p>Рабочее пространство для анализа уроков, наблюдений и прогресса учеников.</p>
    </div>
    """, unsafe_allow_html=True)
    students, lessons, segments, observations = get_counts()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Ученики", students)
    c2.metric("Уроки", lessons)
    c3.metric("Реплики", segments)
    c4.metric("Наблюдения ИИ", observations)
    st.markdown("### Последние занятия")
    lessons_df = get_lesson_list()
    if lessons_df.empty:
        st.info("Пока нет занятий. Перейдите в «Добавить урок».")
    else:
        st.dataframe(lessons_df, use_container_width=True, hide_index=True)

    # NEW: напоминания о ближайших занятиях
    st.markdown("### Ближайшие занятия")
    upcoming = get_upcoming_planned(days_ahead=7)
    if not upcoming:
        st.caption("Нет запланированных занятий на 7 дней. Добавьте расписание в «Группах».")
    else:
        for pl in upcoming:
            dt = datetime.fromisoformat(pl["scheduled_at"])
            hw = homework_state(pl)
            badge = {"ok": ("Домашка сдана", "ok"),
                     "pending": ("Домашка ожидается", "pending"),
                     "overdue": ("Домашка просрочена", "bad"),
                     "none": ("", "")}[hw]
            badge_html = f'<span class="hw-badge {badge[1]}">{badge[0]}</span>' if badge[0] else ""
            st.markdown(
                f"""<div class="cal-card {'overdue' if hw=='overdue' else ''}">
                <h4>{pl['title'] or 'Без темы'} · {pl['group_name']}</h4>
                <div class="cal-meta">{dt.strftime('%d.%m.%Y %H:%M')} · {'проведено' if pl['is_conducted'] else 'запланировано'}{badge_html}</div>
                </div>""",
                unsafe_allow_html=True
            )


# =====================================================================
#                              ДОБАВИТЬ УРОК
# =====================================================================
elif page == "Добавить урок":
    st.markdown("## Добавить занятие")
    st.caption("Загрузите аудио и WebVTT-транскрибацию.")

    prefill = st.session_state.get("prefill_lesson") or {}
    planned_id = st.session_state.get("prefill_planned_id")
    groups = list_groups()
    group_names = ["— без группы —"] + [g["name"] for g in groups]

    default_group_idx = 0
    if prefill.get("group_name") in group_names:
        default_group_idx = group_names.index(prefill["group_name"])

    with st.form("lesson_upload", clear_on_submit=False):
        col1, col2 = st.columns(2)
        with col1:
            title = st.text_input("Название занятия",
                                  value=prefill.get("title", ""),
                                  placeholder="Например: Lektion 04 — Einkaufen")
            lesson_date = st.date_input(
                "Дата занятия",
                value=datetime.fromisoformat(prefill["date"]).date() if prefill.get("date") else datetime.now().date()
            )
            topic = st.text_input("Тема урока", value=prefill.get("topic", ""))
        with col2:
            group_name = st.selectbox("Группа", group_names, index=default_group_idx)
            audio_file = st.file_uploader("Аудиозапись",
                                          type=["m4a", "mp3", "wav", "mp4", "aac", "ogg"])
            vtt_file = st.file_uploader("Транскрибация (.vtt)", type=["vtt", "txt"])
        submitted = st.form_submit_button("Импортировать занятие", type="primary",
                                          use_container_width=True)

    if submitted:
        if not title.strip():
            st.error("Укажите название занятия.")
        elif not audio_file or not vtt_file:
            st.error("Загрузите и аудиофайл, и транскрибацию.")
        else:
            try:
                raw = vtt_file.getvalue().decode("utf-8-sig")
                parsed = parse_vtt(raw)
                if not parsed:
                    st.error("Не удалось найти реплики в файле.")
                else:
                    file_id = uuid.uuid4().hex[:12]
                    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(audio_file.name).name)
                    audio_path = AUDIO_DIR / f"{file_id}_{safe}"
                    audio_path.write_bytes(audio_file.getvalue())
                    final_group = "" if group_name == "— без группы —" else group_name
                    with db() as con:
                        cur = con.execute("""
                            INSERT INTO lessons(title, lesson_date, topic, group_name,
                                                audio_path, transcript_filename, created_at)
                            VALUES(?,?,?,?,?,?,?)
                        """, (title.strip(), lesson_date.isoformat(), topic.strip(), final_group,
                              str(audio_path.relative_to(APP_DIR)), vtt_file.name,
                              datetime.now().isoformat(timespec="seconds")))
                        lesson_id = cur.lastrowid
                        for i, seg in enumerate(parsed):
                            speaker = seg["speaker_name"]
                            if normalize_name(speaker) == normalize_name(TEACHER_NAME):
                                role, sid = "Преподаватель", ensure_student(con, TEACHER_NAME)
                            elif speaker == "Не определён":
                                role, sid = "Не определён", None
                            else:
                                role, sid = "Ученик", ensure_student(con, speaker)
                            con.execute("""
                                INSERT INTO segments(lesson_id, segment_index, start_seconds,
                                                     end_seconds, speaker_name, speaker_role,
                                                     student_id, text)
                                VALUES(?,?,?,?,?,?,?,?)
                            """, (lesson_id, i, seg["start_seconds"], seg["end_seconds"],
                                  speaker, role, sid, seg["text"]))

                        # NEW: привязка к planned_lessons, если пришли из календаря
                        if planned_id:
                            con.execute(
                                "UPDATE planned_lessons SET lesson_id=?, is_conducted=1 WHERE id=?",
                                (lesson_id, planned_id)
                            )
                    st.success(f"Занятие импортировано: {len(parsed)} реплик.")
                    st.session_state["last_imported_lesson"] = lesson_id
                    st.session_state.pop("prefill_planned_id", None)
                    st.session_state.pop("prefill_lesson", None)
            except Exception as exc:
                st.error(f"Ошибка импорта: {exc}")

    if st.session_state.get("last_imported_lesson"):
        lid = st.session_state["last_imported_lesson"]
        st.divider()
        st.markdown("### Следующий шаг")
        if st.button("Открыть импортированное занятие", type="primary"):
            st.session_state["selected_lesson_id"] = lid
            st.rerun()


# =====================================================================
#                              КАЛЕНДАРЬ
# =====================================================================
elif page == "Календарь":
    st.markdown("## Календарь занятий")
    st.caption("Карточки строятся по расписанию групп. Чек-лист ведёт занятие от темы до домашки.")

    groups = list_groups()
    if not groups:
        st.info("Сначала создайте группы и расписание во вкладке «Группы».")
    else:
        c1, c2 = st.columns([1, 2])
        with c1:
            group_options = {"Все группы": None} | {g["name"]: g["id"] for g in groups}
            group_label = st.selectbox("Группа", list(group_options.keys()))
            group_filter = group_options[group_label]
        with c2:
            days_ahead = st.number_input("Показать занятия в следующие N дней",
                                         min_value=1, max_value=120, value=14, step=1)

        # материализуем карточки для показанного окна и +30 дней вперёд
        for g in groups:
            if group_filter is None or g["id"] == group_filter:
                upsert_planned_lessons_for_window(g["id"], days_ahead=int(days_ahead) + 30)

        upcoming = get_upcoming_planned(days_ahead=int(days_ahead), group_id=group_filter)
        if not upcoming:
            st.warning("Нет занятий в выбранном окне. Проверьте расписание группы.")
        for pl in upcoming:
            dt = datetime.fromisoformat(pl["scheduled_at"])
            hw_state = homework_state(pl)
            card_class = "cal-card"
            if hw_state == "overdue":
                card_class += " overdue"
            elif pl["is_conducted"]:
                card_class += " done"

            wd_ru = WEEKDAYS_RU[dt.weekday()]
            st.markdown(
                f"""<div class="{card_class}">
                <h4>{pl['title'] or 'Без темы'} · {pl['group_name']}</h4>
                <div class="cal-meta">{wd_ru}, {dt.strftime('%d.%m.%Y %H:%M')}</div>
                </div>""", unsafe_allow_html=True
            )

            key_base = f"pl_{pl['id']}"

            # --- 1. Тема определена ---
            topic_val = st.text_input(
                "Тема (название занятия)",
                value=pl["title"] or "",
                key=f"{key_base}_title",
                placeholder="Например: Lektion 05 — Reisen"
            )
            if topic_val != (pl["title"] or ""):
                update_planned(pl["id"], title=topic_val)
                st.rerun()

            topic_done = bool((pl["title"] or "").strip())

            # --- 2. Занятие подготовлено ---
            prep = st.checkbox("Занятие подготовлено",
                               value=bool(pl["is_prepared"]),
                               key=f"{key_base}_prep")
            if prep != bool(pl["is_prepared"]):
                update_planned(pl["id"], is_prepared=1 if prep else 0)

            # --- 3. Занятие проведено ---
            cond = st.checkbox("Занятие проведено",
                               value=bool(pl["is_conducted"]),
                               key=f"{key_base}_cond")
            if cond != bool(pl["is_conducted"]):
                update_planned(pl["id"], is_conducted=1 if cond else 0)
                st.rerun()

            # чек-лист прогресса одним взглядом
            checks = [
                f"{'✅' if topic_done else '⬜'} тема",
                f"{'✅' if pl['is_prepared'] else '⬜'} подготовлено",
                f"{'✅' if pl['is_conducted'] else '⬜'} проведено",
                f"{'✅' if pl['lesson_id'] else '⬜'} загружено",
            ]
            st.caption(" · ".join(checks))

            # --- 3a. Кнопка «Загрузить занятие» ---
            if pl["is_conducted"] and not pl["lesson_id"]:
                if st.button("📥 Загрузить это занятие", key=f"{key_base}_upload"):
                    st.session_state["prefill_planned_id"] = pl["id"]
                    st.session_state["prefill_lesson"] = {
                        "title": pl["title"] or "",
                        "date": pl["scheduled_at"],
                        "group_name": pl["group_name"],
                    }
                    st.session_state["pending_nav"] = "Добавить урок"
                    st.rerun()
            if pl["lesson_id"]:
                st.success(f"Занятие в базе (lesson #{pl['lesson_id']}).")

            # --- 4. Домашка ---
            ensure_homework_rows(pl["id"], pl["group_id"])
            st.markdown("**📝 Домашнее задание**")

            due_val = pl["homework_due_date"] or ""
            new_due_date = st.date_input(
                "Дедлайн домашки",
                value=date.fromisoformat(due_val) if due_val else None,
                key=f"{key_base}_due",
                format="DD.MM.YYYY",
            )
            new_due_iso = new_due_date.isoformat() if new_due_date else ""
            if new_due_iso != due_val:
                update_planned(pl["id"], homework_due_date=new_due_iso)
                st.rerun()

            # быстрые кнопки «через N дней»
            qc = st.columns([1, 1, 1, 4])
            for i, days in enumerate([3, 5, 7]):
                if qc[i].button(f"+{days} дн.", key=f"{key_base}_q{i}"):
                    update_planned(
                        pl["id"],
                        homework_due_date=(date.today() + timedelta(days=days)).isoformat(),
                    )
                    st.rerun()

            # отметки по ученикам — всегда видны
            hw_rows = get_homework_rows(pl["id"])
            if not hw_rows:
                st.caption("В группе ещё нет учеников — отметьте состав во вкладке «Группы».")
            else:
                done_cnt = sum(1 for h in hw_rows if h["is_done"])
                with st.expander(f"Отметки по ученикам ({done_cnt}/{len(hw_rows)})",
                                 expanded=True):
                    for hw in hw_rows:
                        col_a, col_b = st.columns([4, 1])
                        col_a.write(hw["student_name"])
                        checked = bool(hw["is_done"])
                        new_checked = col_b.checkbox(
                            "Сдал", value=checked,
                            key=f"{key_base}_hw_{hw['student_id']}",
                            label_visibility="collapsed",
                        )
                        if new_checked != checked:
                            set_homework_done(pl["id"], hw["student_id"], new_checked)
                            st.rerun()

            # итоговый бейдж
            state = homework_state(get_planned(pl["id"]))
            if state == "overdue":
                st.markdown('<span class="hw-badge bad">Домашка просрочена</span>',
                            unsafe_allow_html=True)
            elif state == "ok":
                st.markdown('<span class="hw-badge ok">Все сдали</span>',
                            unsafe_allow_html=True)
            elif state == "pending":
                st.markdown('<span class="hw-badge pending">Ожидается</span>',
                            unsafe_allow_html=True)

            st.divider()

# =====================================================================
#                              ЕЖЕДНЕВНИК
# =====================================================================
elif page == "Ежедневник":
    st.markdown("## Ежедневник")
    st.caption("Недельный или дневной вид занятий по группам — как в календаре.")

    groups = list_groups()
    if not groups:
        st.info("Сначала создайте группы и расписание во вкладке «Группы».")
    else:
        # ---- Панель управления: группа, режим, дата ----
        # ---- Служебное состояние (не привязано к виджетам!) ----
        if "diary_ref" not in st.session_state:
            st.session_state["diary_ref"] = date.today()
        if "diary_date_widget" not in st.session_state:
            st.session_state["diary_date_widget"] = st.session_state["diary_ref"]


        def _diary_sync_from_widget():
            """Пользователь выбрал дату в date_input — обновляем эталон."""
            st.session_state["diary_ref"] = st.session_state["diary_date_widget"]


        def _diary_set(d):
            """Программно меняем эталон и виджет ДО их отрисовки."""
            st.session_state["diary_ref"] = d
            st.session_state["diary_date_widget"] = d


        # ---- Панель управления: группа, режим, дата ----
        top1, top2, top3 = st.columns([2, 2, 2])
        with top1:
            group_options = {"Все группы": None} | {g["name"]: g["id"] for g in groups}
            group_label = st.selectbox("Группа", list(group_options.keys()),
                                       key="diary_group")
            group_filter = group_options[group_label]
        with top2:
            view_mode = st.radio("Вид", ["Неделя", "День"],
                                 horizontal=True, key="diary_view")
        with top3:
            # date_input пишет в отдельный ключ, синхронизация — через on_change
            st.date_input(
                "Дата",
                value=st.session_state["diary_date_widget"],
                key="diary_date_widget",
                on_change=_diary_sync_from_widget,
            )

        ref_date = st.session_state["diary_ref"]

        # ---- Навигация стрелками ----
        nav1, nav2, nav3, nav4, nav5 = st.columns([1, 1, 2, 1, 1])
        delta = timedelta(days=7 if view_mode == "Неделя" else 1)
        if nav1.button("← Предыдущая", key="diary_prev", use_container_width=True):
            _diary_set(ref_date - delta)
            st.rerun()
        if nav2.button("Сегодня", key="diary_today", use_container_width=True):
            _diary_set(date.today())
            st.rerun()
        nav3.markdown(
            f"<div style='text-align:center; padding-top:.35rem; opacity:.75'>"
            f"{ref_date.strftime('%d.%m.%Y')} · {WEEKDAYS_RU[ref_date.weekday()]}</div>",
            unsafe_allow_html=True
        )
        if nav4.button("Следующая →", key="diary_next", use_container_width=True):
            _diary_set(ref_date + delta)
            st.rerun()
        if nav5.button("🔄 Обновить", key="diary_refresh", use_container_width=True):
            for g in groups:
                if group_filter is None or g["id"] == group_filter:
                    upsert_planned_lessons_for_window(g["id"], days_ahead=180)
            st.rerun()

        st.divider()

        # ---- Считаем диапазон ----
        if view_mode == "Неделя":
            monday = ref_date - timedelta(days=ref_date.weekday())
            sunday = monday + timedelta(days=6)
            range_start, range_end = monday, sunday
        else:
            range_start = range_end = ref_date

        # материализуем карточки, чтобы окно точно было заполнено
        for g in groups:
            if group_filter is None or g["id"] == group_filter:
                upsert_planned_lessons_for_window(g["id"], days_ahead=180)

        planned = get_planned_in_range(range_start, range_end, group_id=group_filter)

        # группируем по дате
        by_day = {}
        for pl in planned:
            d = datetime.fromisoformat(pl["scheduled_at"]).date()
            by_day.setdefault(d, []).append(pl)

        def _event_html(pl):
            dt = datetime.fromisoformat(pl["scheduled_at"])
            hw = homework_state(pl)
            cls = "evt"
            if pl["is_conducted"]:
                cls += " done"
            if hw == "overdue":
                cls += " overdue"
            title = pl["title"] or "— без темы —"
            hw_marks = ""
            if hw == "overdue":
                hw_marks = " 🔴"
            elif hw == "ok":
                hw_marks = " ✅"
            elif hw == "pending":
                hw_marks = " 🟡"
            return (
                f'<div class="{cls}">'
                f'<span class="t">{dt.strftime("%H:%M")}</span>'
                f'<span class="grp">{pl["group_name"]}</span>'
                f'<span class="ttl">{title}{hw_marks}</span>'
                f'</div>'
            )

        # ---- Отрисовка ----
        if view_mode == "Неделя":
            days = [range_start + timedelta(days=i) for i in range(7)]
            cols = st.columns(7)
            today = date.today()
            for i, d in enumerate(days):
                with cols[i]:
                    head_cls = "day-col today" if d == today else "day-col"
                    st.markdown(
                        f'<div class="{head_cls}">'
                        f'<div class="day-head">'
                        f'<span>{d.strftime("%d.%m")}</span>'
                        f'<span class="wd">{WEEKDAYS_RU[d.weekday()]}</span>'
                        f'</div>',
                        unsafe_allow_html=True
                    )
                    items = by_day.get(d, [])
                    if not items:
                        st.markdown('<div class="day-empty">—</div>',
                                    unsafe_allow_html=True)
                    else:
                        for pl in items:
                            st.markdown(_event_html(pl), unsafe_allow_html=True)
                    st.markdown('</div>', unsafe_allow_html=True)
        else:
            # День — карточки детальнее
            items = by_day.get(ref_date, [])
            if not items:
                st.info("На этот день занятий нет.")
            for pl in items:
                dt = datetime.fromisoformat(pl["scheduled_at"])
                hw = homework_state(pl)
                border_color = "#4871b8"
                if pl["is_conducted"]:
                    border_color = "#4caf50"
                if hw == "overdue":
                    border_color = "#d9534f"
                hw_label = {
                    "ok": '<span class="hw-badge ok">Домашка сдана</span>',
                    "overdue": '<span class="hw-badge bad">Домашка просрочена</span>',
                    "pending": '<span class="hw-badge pending">Ожидается</span>',
                    "none": "",
                }[hw]
                st.markdown(
                    f"""<div class="cal-card" style="border-left-color:{border_color}">
                    <h4>{pl['title'] or '— без темы —'}</h4>
                    <div class="cal-meta">{pl['group_name']} · {dt.strftime('%H:%M')}
                    · {'проведено' if pl['is_conducted'] else 'запланировано'}
                    · {'подготовлено' if pl['is_prepared'] else 'не подготовлено'}
                    {hw_label}</div>
                    </div>""",
                    unsafe_allow_html=True
                )

            # сводка по домашка
            if items:
                st.markdown("### Домашка на этот день")
                for pl in items:
                    if not pl["homework_due_date"]:
                        continue
                    hw_rows = get_homework_rows(pl["id"])
                    if not hw_rows:
                        continue
                    st.markdown(f"**{pl['title'] or '— без темы —'}** · "
                                f"дедлайн {pl['homework_due_date']}")
                    for hw in hw_rows:
                        mark = "✅" if hw["is_done"] else "⬜"
                        st.markdown(f"- {mark} {hw['student_name']}")

        st.divider()
        st.caption("Полный чек-лист занятия (тема, подготовка, домашка) — во вкладке «Календарь».")

# =====================================================================
#                          УРОКИ И ТРАНСКРИПТЫ
# =====================================================================
elif page == "Уроки и транскрипты":
    st.markdown("## Уроки и транскрипты")
    with db() as con:
        lessons = con.execute("SELECT * FROM lessons ORDER BY lesson_date DESC, id DESC").fetchall()
    if not lessons:
        st.info("Здесь появятся импортированные занятия.")
    else:
        choices = {f"#{l['id']} · {l['lesson_date']} · {l['title']}": l["id"] for l in lessons}
        default_idx = 0
        sel = st.session_state.get("selected_lesson_id")
        if sel in choices.values():
            default_idx = list(choices.values()).index(sel)
        chosen = st.selectbox("Выберите занятие", list(choices.keys()), index=default_idx)
        lesson_id = choices[chosen]
        with db() as con:
            lesson = con.execute("SELECT * FROM lessons WHERE id=?", (lesson_id,)).fetchone()
            segs = con.execute("SELECT * FROM segments WHERE lesson_id=? ORDER BY segment_index",
                               (lesson_id,)).fetchall()
            obs = con.execute("""
                SELECT o.*, s.name AS student_name FROM observations o
                LEFT JOIN students s ON s.id=o.student_id
                WHERE o.lesson_id=? ORDER BY o.id
            """, (lesson_id,)).fetchall()
        c1, c2, c3 = st.columns([2, 1, 1])
        with c1:
            st.markdown(f"### {lesson['title']}")
            st.caption(f"{lesson['lesson_date']} · {lesson['topic'] or 'Тема не указана'}")
        with c2:
            if lesson["audio_path"]:
                p = APP_DIR / lesson["audio_path"]
                if p.exists():
                    st.audio(p.read_bytes())
        with c3:
            if st.button("Запустить ИИ-анализ", type="primary", use_container_width=True):
                with st.spinner("Анализируем транскрипт..."):
                    try:
                        ok, msg = run_ai_analysis(lesson_id)
                    except Exception as e:
                        logger.exception("Ошибка в run_ai_analysis")
                        ok, msg = False, f"Ошибка: {e}"
                (st.success if ok else st.error)(msg)
                st.rerun()
        st.divider()
        tab1, tab2 = st.tabs(["Транскрипт", f"Наблюдения ({len(obs)})"])
        with tab1:
            filt = st.selectbox("Фильтр по говорящему",
                                ["Все"] + sorted({s["speaker_name"] for s in segs}))
            for s in segs:
                if filt != "Все" and s["speaker_name"] != filt:
                    continue
                st.markdown(
                    f"""<div class="segment"><span class="pill">{fmt_time(s['start_seconds'])} – {fmt_time(s['end_seconds'])}</span>
                    <b style="margin-left:.45rem">{s['speaker_name']}</b>
                    <div style="margin-top:.4rem">{s['text']}</div></div>""",
                    unsafe_allow_html=True
                )
        with tab2:
            if not obs:
                st.info("Наблюдений нет.")
            for o in obs:
                with st.expander(f"{o['category']} · {o['student_name'] or '—'} · {o['status']}"):
                    st.write(o["description"])
                    if o["evidence"]:
                        st.markdown(f"> {o['evidence']}")
                    st.caption(f"{fmt_time(o['start_seconds'])} – {fmt_time(o['end_seconds'])}"
                               f" · Уверенность: {o['confidence'] if o['confidence'] is not None else '—'}")
                    if o["recommended_action"]:
                        st.markdown(f"**Рекомендация:** {o['recommended_action']}")
                    new_status = st.selectbox(
                        "Статус", ["Требует проверки", "Подтверждено", "Отклонено"],
                        index=["Требует проверки", "Подтверждено", "Отклонено"].index(o["status"]),
                        key=f"status_{o['id']}"
                    )
                    if st.button("Сохранить статус", key=f"save_{o['id']}"):
                        with db() as con:
                            con.execute("UPDATE observations SET status=? WHERE id=?",
                                        (new_status, o["id"]))
                        st.rerun()


# =====================================================================
#                                 ГРУППЫ
# =====================================================================
elif page == "Группы":
    st.markdown("## Группы")
    st.caption("Создавайте группы, назначайте состав и расписание. "
               "По расписанию автоматически строятся карточки занятий в календаре.")

    tab_list, tab_new = st.tabs(["Существующие группы", "Новая группа"])

    with tab_new:
        with st.form("new_group", clear_on_submit=True):
            gname = st.text_input("Название группы", placeholder="A2 — Gruppe 1")
            glevel = st.text_input("Уровень (необязательно)", placeholder="A2 / B1")
            gnote = st.text_area("Заметка", height=80)
            if st.form_submit_button("Создать группу", type="primary"):
                if not gname.strip():
                    st.error("Укажите название.")
                else:
                    try:
                        create_group(gname, glevel, gnote)
                        st.success("Группа создана.")
                        st.rerun()
                    except sqlite3.IntegrityError:
                        st.error("Такая группа уже существует.")

    with tab_list:
        groups = list_groups()
        if not groups:
            st.info("Групп пока нет. Создайте первую во вкладке «Новая группа».")
        else:
            group_labels = {f"#{g['id']} · {g['name']} ({g['level'] or '—'})": g["id"] for g in groups}
            chosen = st.selectbox("Выберите группу", list(group_labels.keys()))
            gid = group_labels[chosen]
            with db() as con:
                g = con.execute("SELECT * FROM groups WHERE id=?", (gid,)).fetchone()

            # --- Основные настройки ---
            col1, col2 = st.columns(2)
            with col1:
                new_name = st.text_input("Название", value=g["name"], key=f"gname_{gid}")
                new_level = st.text_input("Уровень", value=g["level"] or "", key=f"glvl_{gid}")
            with col2:
                new_note = st.text_area("Заметка", value=g["note"] or "", key=f"gnote_{gid}", height=100)
            c1, c2 = st.columns([1, 1])
            if c1.button("💾 Сохранить", type="primary", key=f"gsave_{gid}"):
                try:
                    update_group(gid, new_name, new_level, new_note)
                    st.success("Сохранено.")
                    st.rerun()
                except sqlite3.IntegrityError:
                    st.error("Имя группы занято.")
            if c2.button("🗑 Удалить группу", key=f"gdel_{gid}"):
                delete_group(gid)
                st.warning("Группа удалена.")
                st.rerun()

            st.divider()

            # --- Состав группы ---
            st.markdown("### Состав группы")
            all_students = get_all_students()
            current_members = {m["id"] for m in get_group_members(gid)}
            if not all_students:
                st.caption("В базе пока нет учеников. Они появятся после импорта занятий.")
            else:
                student_options = {s["name"]: s["id"] for s in all_students}
                selected_names = st.multiselect(
                    "Ученики",
                    options=list(student_options.keys()),
                    default=[s["name"] for s in all_students if s["id"] in current_members],
                    key=f"gmembers_{gid}"
                )
                if st.button("💾 Сохранить состав", key=f"gsm_{gid}"):
                    set_group_members(gid, [student_options[n] for n in selected_names])
                    st.success("Состав обновлён.")
                    st.rerun()

            st.divider()

            # --- Расписание ---
            st.markdown("### Расписание")
            sched = get_group_schedule(gid)
            st.caption("Дни недели и время начала. Продолжительность — минуты.")

            # существующие строки
            updated_rows = []
            for i, s in enumerate(sched):
                cols = st.columns([2, 2, 2, 1])
                wd = cols[0].selectbox("День", WEEKDAYS_RU,
                                       index=s["weekday"], key=f"sched_wd_{gid}_{i}")
                hh, mm = s["start_time"].split(":")
                t = cols[1].time_input("Время", value=dtime(int(hh), int(mm)),
                                       key=f"sched_t_{gid}_{i}")
                dur = cols[2].number_input("Мин.", min_value=15, max_value=240,
                                           value=int(s["duration_min"] or 60), step=5,
                                           key=f"sched_d_{gid}_{i}")
                if cols[3].button("✕", key=f"sched_x_{gid}_{i}"):
                    with db() as con:
                        con.execute("DELETE FROM group_schedule WHERE id=?", (s["id"],))
                    st.rerun()
                updated_rows.append((WEEKDAYS_RU.index(wd), t.strftime("%H:%M"), int(dur)))

            # добавление новой строки
            with st.expander("➕ Добавить день"):
                cols = st.columns([2, 2, 2, 1])
                new_wd = cols[0].selectbox("День", WEEKDAYS_RU, key=f"new_wd_{gid}")
                new_t = cols[1].time_input("Время", value=dtime(18, 0), key=f"new_t_{gid}")
                new_dur = cols[2].number_input("Мин.", min_value=15, max_value=240,
                                               value=60, step=5, key=f"new_d_{gid}")
                if cols[3].button("Добавить", key=f"new_add_{gid}"):
                    with db() as con:
                        con.execute(
                            "INSERT INTO group_schedule(group_id, weekday, start_time, duration_min) VALUES(?,?,?,?)",
                            (gid, WEEKDAYS_RU.index(new_wd), new_t.strftime("%H:%M"), int(new_dur))
                        )
                    st.rerun()

            if st.button("💾 Сохранить расписание", key=f"sched_save_{gid}"):
                set_group_schedule(gid, updated_rows)
                st.success("Расписание сохранено.")
                st.rerun()

            # --- Сгенерировать карточки на 60 дней ---
            if st.button("🔁 Сгенерировать карточки занятий на 60 дней", key=f"gen_{gid}"):
                upsert_planned_lessons_for_window(gid, days_ahead=60)
                st.success("Карточки созданы. Смотрите вкладку «Календарь».")


# =====================================================================
#                                 УЧЕНИКИ
# =====================================================================
elif page == "Ученики":
    st.markdown("## Ученики")
    with db() as con:
        students_df = pd.read_sql_query("""
            SELECT s.id, s.name, s.created_at,
                   COUNT(DISTINCT seg.lesson_id) AS lessons_count,
                   COUNT(DISTINCT seg.id) AS utterances_count,
                   COUNT(DISTINCT obs.id) AS observations_count
            FROM students s
            LEFT JOIN segments seg ON seg.student_id=s.id
            LEFT JOIN observations obs ON obs.student_id=s.id
            WHERE s.normalized_name != ?
            GROUP BY s.id
            ORDER BY s.name COLLATE NOCASE
        """, con, params=(normalize_name(TEACHER_NAME),))

    if students_df.empty:
        st.info("Ученики появятся после импорта первого занятия.")
    else:
        st.dataframe(students_df, use_container_width=True, hide_index=True)
        selected_name = st.selectbox("Карточка ученика", students_df["name"].tolist())
        row = students_df[students_df["name"] == selected_name].iloc[0]
        c1, c2, c3 = st.columns(3)
        c1.metric("Занятий", int(row["lessons_count"]))
        c2.metric("Реплик", int(row["utterances_count"]))
        c3.metric("Наблюдений", int(row["observations_count"]))

        # NEW: статистика по домашке
        with db() as con:
            hw = con.execute("""
                SELECT
                    SUM(CASE WHEN hs.is_done=1 THEN 1 ELSE 0 END) AS done_cnt,
                    SUM(CASE WHEN hs.is_done=0
                              AND pl.homework_due_date != ''
                              AND pl.homework_due_date < ?
                             THEN 1 ELSE 0 END) AS overdue_cnt,
                    SUM(CASE WHEN hs.is_done=0
                              AND (pl.homework_due_date = ''
                                   OR pl.homework_due_date >= ?)
                             THEN 1 ELSE 0 END) AS pending_cnt
                FROM homework_status hs
                JOIN planned_lessons pl ON pl.id = hs.planned_lesson_id
                WHERE hs.student_id = ?
            """, (date.today().isoformat(), date.today().isoformat(), int(row["id"]))).fetchone()

            history = pd.read_sql_query("""
                SELECT l.lesson_date, l.title, o.category, o.topic, o.description, o.status
                FROM observations o JOIN lessons l ON l.id=o.lesson_id
                JOIN students s ON s.id=o.student_id
                WHERE s.name=? ORDER BY l.lesson_date DESC
            """, con, params=(selected_name,))

            hw_log = pd.read_sql_query("""
                SELECT pl.scheduled_at AS lesson_at, pl.title AS lesson_title,
                       pl.homework_due_date AS due_date,
                       hs.is_done, hs.done_at
                FROM homework_status hs
                JOIN planned_lessons pl ON pl.id = hs.planned_lesson_id
                WHERE hs.student_id = ?
                ORDER BY pl.scheduled_at DESC
            """, con, params=(int(row["id"]),))

        st.markdown("### Домашние задания")
        h1, h2, h3 = st.columns(3)
        h1.metric("Сдано", int(hw["done_cnt"] or 0))
        h2.metric("Ожидается", int(hw["pending_cnt"] or 0))
        h3.metric("Просрочено", int(hw["overdue_cnt"] or 0))

        if hw_log.empty:
            st.caption("Пока нет записей по домашке.")
        else:
            def _hw_label(r):
                if r["is_done"]:
                    return "✅ сдано"
                if r["due_date"] and r["due_date"] < date.today().isoformat():
                    return "🔴 просрочено"
                return "🟡 ожидается"
            hw_log["статус"] = hw_log.apply(_hw_label, axis=1)
            st.dataframe(
                hw_log[["lesson_at", "lesson_title", "due_date", "статус"]],
                use_container_width=True, hide_index=True
            )

        st.markdown("### История наблюдений")
        if history.empty:
            st.info("Для этого ученика пока нет наблюдений.")
        else:
            st.dataframe(history, use_container_width=True, hide_index=True)


# =====================================================================
#                                АНАЛИТИКА
# =====================================================================
elif page == "Аналитика":
    st.markdown("## Аналитика")
    with db() as con:
        by_category = pd.read_sql_query("""
            SELECT category, COUNT(*) AS count FROM observations
            WHERE status != 'Отклонено' GROUP BY category ORDER BY count DESC
        """, con)
        by_student = pd.read_sql_query("""
            SELECT COALESCE(s.name, 'Ученик не определён') AS student,
                   COUNT(o.id) AS count
            FROM observations o LEFT JOIN students s ON s.id=o.student_id
            WHERE o.status != 'Отклонено'
            GROUP BY student ORDER BY count DESC LIMIT 15
        """, con)
        by_lesson = pd.read_sql_query("""
            SELECT l.lesson_date, l.title, COUNT(o.id) AS observations
            FROM lessons l LEFT JOIN observations o ON o.lesson_id=l.id
            GROUP BY l.id ORDER BY l.lesson_date
        """, con)

        # NEW: сводка по домашке
        hw_summary = pd.read_sql_query("""
            SELECT s.name AS student,
                   SUM(CASE WHEN hs.is_done=1 THEN 1 ELSE 0 END) AS done_cnt,
                   SUM(CASE WHEN hs.is_done=0
                             AND pl.homework_due_date != ''
                             AND pl.homework_due_date < date('now')
                            THEN 1 ELSE 0 END) AS overdue_cnt,
                   SUM(CASE WHEN hs.is_done=0
                             AND (pl.homework_due_date = ''
                                  OR pl.homework_due_date >= date('now'))
                            THEN 1 ELSE 0 END) AS pending_cnt
            FROM homework_status hs
            JOIN students s ON s.id = hs.student_id
            JOIN planned_lessons pl ON pl.id = hs.planned_lesson_id
            GROUP BY s.id
            HAVING done_cnt + overdue_cnt + pending_cnt > 0
            ORDER BY overdue_cnt DESC, s.name COLLATE NOCASE
        """, con)

    a, b = st.columns(2)
    with a:
        st.markdown("### По категориям")
        if by_category.empty:
            st.info("Нет данных.")
        else:
            st.bar_chart(by_category.set_index("category"))
    with b:
        st.markdown("### По ученикам")
        if by_student.empty:
            st.info("Нет данных.")
        else:
            st.bar_chart(by_student.set_index("student"))

    st.markdown("### Наблюдения по занятиям")
    if by_lesson.empty:
        st.info("Сначала импортируйте занятие.")
    else:
        st.dataframe(by_lesson, use_container_width=True, hide_index=True)

    st.markdown("### Домашние задания")
    if hw_summary.empty:
        st.info("Домашних заданий пока нет. Задайте их в календаре.")
    else:
        st.dataframe(hw_summary, use_container_width=True, hide_index=True)

    st.warning("Частота наблюдений сама по себе не является оценкой уровня языка. "
               "Для корректной динамики стоит учитывать число заданий/попыток "
               "и подтверждённые преподавателем ошибки.")