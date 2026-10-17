import io
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
import zipfile

import streamlit as st
import pandas as pd
import streamlit.components.v1 as components
from pypdf import PdfReader

# ---------- Санитайзер JSON для ответов LLM ----------
def _sanitize_json(text: str) -> str:
    text = re.sub(r",(\s*[}\]])", r"\1", text)
    text = re.sub(r"//[^\n]*", "", text)
    return text


APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "data"
AUDIO_DIR = DATA_DIR / "audio"
BOOKS_DIR = DATA_DIR / "books"
BOOK_AUDIO_DIR = DATA_DIR / "audio_books"
DB_PATH = DATA_DIR / "lessons.sqlite3"
TEACHER_NAME = "Эвелина Акбашева"
MODEL_NAME = os.getenv("GIGACHAT_MODEL", "GigaChat")

DATA_DIR.mkdir(exist_ok=True)
AUDIO_DIR.mkdir(exist_ok=True)
BOOKS_DIR.mkdir(exist_ok=True)
BOOK_AUDIO_DIR.mkdir(exist_ok=True)

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
    .hw-badge.book {background: rgba(120,90,180,.18); color: #4a2c8a;}

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

    /* Библиотека */
    .book-card {
        border: 1px solid rgba(120,120,120,.22);
        border-radius: 14px;
        overflow: hidden;
        background: rgba(120,130,140,.04);
        margin-bottom: 1rem;
        transition: transform .12s ease;
    }
    .book-card:hover {transform: translateY(-2px);}
    .book-cover {
        width: 100%;
        aspect-ratio: 3 / 4;
        background: linear-gradient(135deg, #5e7ec4, #7a9fd8);
        color: white;
        display: flex;
        align-items: center;
        justify-content: center;
        font-weight: 600;
        font-size: 1.1rem;
        text-align: center;
        padding: 1rem;
        box-sizing: border-box;
        object-fit: cover;
    }
    .book-title {
        padding: .55rem .7rem .25rem .7rem;
        font-weight: 600;
        font-size: .95rem;
        line-height: 1.25;
    }
    .book-meta {
        padding: 0 .7rem .7rem .7rem;
        opacity: .7;
        font-size: .8rem;
    }
    .reader-frame {
        border: 1px solid rgba(120,120,120,.2);
        border-radius: 12px;
        overflow: hidden;
        background: #fafafa;
    }
        .audio-item {
        padding: .35rem .5rem;
        border-radius: 8px;
        margin-bottom: .25rem;
        border: 1px solid transparent;
        background: rgba(120,130,140,.03);
        min-height: 44px;
        display: flex;
        flex-direction: column;
        justify-content: center;
    }
    .audio-item:hover {background: rgba(72,113,184,.08);}
    .audio-item.active {background: rgba(72,113,184,.14); border-color: #4871b8;}
    .audio-track {font-weight: 600; font-size: .88rem; line-height: 1.2;}
    .audio-sub {opacity: .65; font-size: .76rem; line-height: 1.2;}
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
            weekday INTEGER NOT NULL,
            start_time TEXT NOT NULL,
            duration_min INTEGER DEFAULT 60
        );

        CREATE TABLE IF NOT EXISTS planned_lessons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
            scheduled_at TEXT NOT NULL,
            title TEXT DEFAULT '',
            topic TEXT DEFAULT '',
            is_prepared INTEGER DEFAULT 0,
            is_conducted INTEGER DEFAULT 0,
            lesson_id INTEGER REFERENCES lessons(id) ON DELETE SET NULL,
            homework_due_date TEXT DEFAULT '',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS homework_status (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            planned_lesson_id INTEGER NOT NULL REFERENCES planned_lessons(id) ON DELETE CASCADE,
            student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
            is_done INTEGER DEFAULT 0,
            done_at TEXT DEFAULT '',
            UNIQUE (planned_lesson_id, student_id)
        );

        -- NEW: библиотека
        CREATE TABLE IF NOT EXISTS library_books (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            author TEXT DEFAULT '',
            level TEXT DEFAULT '',
            note TEXT DEFAULT '',
            cover_path TEXT DEFAULT '',
            pdf_path TEXT DEFAULT '',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS library_audio (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            book_id INTEGER NOT NULL REFERENCES library_books(id) ON DELETE CASCADE,
            position INTEGER NOT NULL DEFAULT 0,
            title TEXT NOT NULL,
            audio_path TEXT NOT NULL,
            duration_note TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS group_books (
            group_id INTEGER PRIMARY KEY REFERENCES groups(id) ON DELETE CASCADE,
            book_id INTEGER NOT NULL REFERENCES library_books(id) ON DELETE CASCADE,
            assigned_at TEXT NOT NULL
        );
        
        CREATE TABLE IF NOT EXISTS book_chapters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            book_id INTEGER NOT NULL REFERENCES library_books(id) ON DELETE CASCADE,
            position INTEGER NOT NULL DEFAULT 0,
            title TEXT NOT NULL,
            page_from INTEGER,
            page_to INTEGER,
            summary TEXT DEFAULT '',
            created_at TEXT NOT NULL
        );

        -- NEW: глоссарии по уроку
        CREATE TABLE IF NOT EXISTS book_glossaries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chapter_id INTEGER NOT NULL REFERENCES book_chapters(id) ON DELETE CASCADE,
            word TEXT NOT NULL,
            article TEXT DEFAULT '',
            translation TEXT DEFAULT '',
            example TEXT DEFAULT '',
            pos TEXT DEFAULT '',
            created_at TEXT NOT NULL
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


def get_today_attention_items(horizon_hours: int = 48) -> list[dict]:
    """
    Собирает список «что требует внимания» для вкладки «Обзор».
    Каждый элемент: {kind, emoji, title, detail, action_label, action}.
    action — dict с полями: nav (страница) и, при необходимости, set_state (dict).
    """
    items: list[dict] = []
    now = datetime.now()
    horizon_iso = (now + timedelta(hours=horizon_hours)).isoformat(timespec="minutes")
    now_iso = now.isoformat(timespec="minutes")
    today_iso = date.today().isoformat()

    with db() as con:
        # 1) Ближайшие занятия без подготовки
        upcoming = con.execute("""
            SELECT pl.id, pl.scheduled_at, pl.title, g.name AS group_name
            FROM planned_lessons pl
            JOIN groups g ON g.id = pl.group_id
            WHERE pl.scheduled_at >= ? AND pl.scheduled_at <= ?
              AND pl.is_prepared = 0
              AND pl.is_conducted = 0
            ORDER BY pl.scheduled_at
        """, (now_iso, horizon_iso)).fetchall()
        for r in upcoming:
            dt = datetime.fromisoformat(r["scheduled_at"])
            delta_h = max(0, int((dt - now).total_seconds() // 3600))
            when = "через час" if delta_h < 1 else f"через {delta_h} ч"
            items.append({
                "kind": "unprepared_lesson",
                "emoji": "📅",
                "title": f"«{r['title'] or 'Без темы'}» · {r['group_name']} — {when}",
                "detail": "Занятие не подготовлено. Откройте карточку и отметьте подготовку.",
                "action_label": "Открыть календарь",
                "action": {"nav": "Календарь"},
            })

        # 2) Прошедшие занятия, отмеченные «проведено», но урок не загружен
        missed = con.execute("""
            SELECT pl.id, pl.scheduled_at, pl.title, g.name AS group_name
            FROM planned_lessons pl
            JOIN groups g ON g.id = pl.group_id
            WHERE pl.is_conducted = 1
              AND pl.lesson_id IS NULL
              AND date(pl.scheduled_at) <= date(?)
            ORDER BY pl.scheduled_at DESC
            LIMIT 5
        """, (today_iso,)).fetchall()
        for r in missed:
            d = datetime.fromisoformat(r["scheduled_at"]).strftime("%d.%m")
            items.append({
                "kind": "conducted_not_uploaded",
                "emoji": "📥",
                "title": f"Урок от {d} проведён, но не загружен · {r['group_name']}",
                "detail": f"«{r['title'] or 'Без темы'}». Загрузите аудио и транскрипт.",
                "action_label": "Открыть календарь",
                "action": {"nav": "Календарь"},
            })

        # 3) Просроченные домашки
        overdue = con.execute("""
            SELECT COUNT(*) AS n,
                   COUNT(DISTINCT hs.student_id) AS students,
                   GROUP_CONCAT(DISTINCT g.name) AS groups_list
            FROM homework_status hs
            JOIN planned_lessons pl ON pl.id = hs.planned_lesson_id
            JOIN groups g ON g.id = pl.group_id
            WHERE hs.is_done = 0
              AND pl.homework_due_date != ''
              AND pl.homework_due_date < ?
        """, (today_iso,)).fetchone()
        if overdue and overdue["n"]:
            items.append({
                "kind": "homework_overdue",
                "emoji": "🔴",
                "title": f"Просрочено домашек: {overdue['n']} "
                         f"у {overdue['students']} учеников",
                "detail": f"Группы: {overdue['groups_list'] or '—'}",
                "action_label": "Открыть аналитику",
                "action": {"nav": "Аналитика"},
            })

        # 4) Наблюдения ИИ, ждущие проверки
        pending_obs = con.execute("""
            SELECT COUNT(*) AS n FROM observations
            WHERE status = 'Требует проверки'
        """).fetchone()["n"]
        if pending_obs:
            items.append({
                "kind": "pending_observations",
                "emoji": "🟡",
                "title": f"Наблюдений ждут проверки: {pending_obs}",
                "detail": "Подтвердите или отклоните их в карточке урока.",
                "action_label": "Открыть уроки",
                "action": {"nav": "Уроки и транскрипты"},
            })

        # 5) Учебники, привязанные к группе, но без разобранных уроков
        unparsed = con.execute("""
            SELECT b.id, b.title, g.name AS group_name
            FROM group_books gb
            JOIN library_books b ON b.id = gb.book_id
            JOIN groups g ON g.id = gb.group_id
            WHERE NOT EXISTS (
                SELECT 1 FROM book_chapters c WHERE c.book_id = b.id
            )
            ORDER BY b.title
            LIMIT 5
        """).fetchall()
        for r in unparsed:
            items.append({
                "kind": "unparsed_book",
                "emoji": "📖",
                "title": f"«{r['title']}» привязан к {r['group_name']}, но уроки не разобраны",
                "detail": "Запустите анализ книги в режиме «Задания».",
                "action_label": "Открыть библиотеку",
                "action": {"nav": "Библиотека", "set_state": {"book_tasks_id": r["id"]}},
            })

        # 6) Дубликаты учеников (по транслиту)
        all_students = con.execute("SELECT id, name FROM students").fetchall()

    # Дубликаты ищем в Python — по транслиту
    seen: dict[str, list[dict]] = {}
    for s in all_students:
        key = translit_ru_to_lat(normalize_name(s["name"]))
        key = re.sub(r"[^a-z0-9]", "", key)
        if key:
            seen.setdefault(key, []).append({"id": s["id"], "name": s["name"]})
    dups = [v for v in seen.values() if len(v) > 1]
    if dups:
        names = "; ".join(" / ".join(x["name"] for x in d) for d in dups[:3])
        items.append({
            "kind": "student_duplicates",
            "emoji": "🟠",
            "title": f"Возможные дубликаты учеников: {len(dups)}",
            "detail": names + ("…" if len(dups) > 3 else ""),
            "action_label": "Открыть учеников",
            "action": {"nav": "Ученики"},
        })

    return items


# ---------- Группы ----------
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


# ---------- Календарь занятий ----------
def upsert_planned_lessons_for_window(group_id, days_ahead=60):
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


# ---------- Библиотека: доступ к данным ----------
def list_books():
    with db() as con:
        return con.execute("SELECT * FROM library_books ORDER BY title COLLATE NOCASE").fetchall()


def get_book(book_id):
    with db() as con:
        return con.execute("SELECT * FROM library_books WHERE id=?", (book_id,)).fetchone()


def create_book(title, author, level, note, cover_bytes, cover_name, pdf_bytes, pdf_name):
    with db() as con:
        cur = con.execute(
            "INSERT INTO library_books(title, author, level, note, cover_path, pdf_path, created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (title.strip(), author.strip(), level.strip(), note.strip(),
             "", "", datetime.now().isoformat(timespec="seconds"))
        )
        book_id = cur.lastrowid

    cover_rel = ""
    if cover_bytes:
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", cover_name or "cover")
        cover_file = BOOKS_DIR / f"book_{book_id}_cover_{safe}"
        cover_file.write_bytes(cover_bytes)
        cover_rel = str(cover_file.relative_to(APP_DIR))

    pdf_rel = ""
    if pdf_bytes:
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", pdf_name or "book.pdf")
        pdf_file = BOOKS_DIR / f"book_{book_id}_{safe}"
        pdf_file.write_bytes(pdf_bytes)
        pdf_rel = str(pdf_file.relative_to(APP_DIR))

    with db() as con:
        con.execute("UPDATE library_books SET cover_path=?, pdf_path=? WHERE id=?",
                    (cover_rel, pdf_rel, book_id))
    return book_id


def delete_book(book_id):
    with db() as con:
        audios = con.execute(
            "SELECT audio_path FROM library_audio WHERE book_id=?", (book_id,)
        ).fetchall()
        book = con.execute("SELECT cover_path, pdf_path FROM library_books WHERE id=?",
                           (book_id,)).fetchone()
        con.execute("DELETE FROM library_books WHERE id=?", (book_id,))

    for a in audios:
        try:
            (APP_DIR / a["audio_path"]).unlink(missing_ok=True)
        except Exception:
            logger.exception("Не удалось удалить аудио %s", a["audio_path"])
    if book:
        for key in ("cover_path", "pdf_path"):
            p = book[key]
            if p:
                try:
                    (APP_DIR / p).unlink(missing_ok=True)
                except Exception:
                    logger.exception("Не удалось удалить файл %s", p)


def add_book_audio(book_id, title, audio_bytes, audio_name, duration_note=""):
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", audio_name or "audio.mp3")
    target = BOOK_AUDIO_DIR / f"book_{book_id}_{uuid.uuid4().hex[:8]}_{safe}"
    target.write_bytes(audio_bytes)
    rel = str(target.relative_to(APP_DIR))
    with db() as con:
        pos = con.execute(
            "SELECT COALESCE(MAX(position),0)+1 AS p FROM library_audio WHERE book_id=?",
            (book_id,)
        ).fetchone()["p"]
        con.execute(
            "INSERT INTO library_audio(book_id, position, title, audio_path, duration_note) "
            "VALUES(?,?,?,?,?)",
            (book_id, pos, title.strip() or audio_name, rel, duration_note.strip())
        )


_AUDIO_EXTS = (".mp3", ".m4a", ".wav", ".ogg", ".aac", ".flac", ".opus")


def add_book_audio_bulk(book_id, files) -> dict:
    """
    Пакетная загрузка аудио.
    files — список dict {name, bytes}. Имя используется как title трека
    (без расширения, подчёркивания -> пробелы).
    Возвращает {'added': N, 'skipped': [имена], 'errors': [...]}
    """
    added = 0
    skipped: list[str] = []
    errors: list[str] = []

    def _make_title(filename: str) -> str:
        base = Path(filename).stem
        # "01_lek_1" -> "01 lek 1"; оставляем как есть, только убираем подчёркивания
        return re.sub(r"_+", " ", base).strip()

    for f in files:
        name = f.get("name") or "audio.mp3"
        data = f.get("bytes") or b""
        if not name.lower().endswith(_AUDIO_EXTS):
            skipped.append(name)
            continue
        if not data:
            skipped.append(name)
            continue
        try:
            add_book_audio(
                book_id,
                _make_title(name),
                data,
                name,
                duration_note="",
            )
            added += 1
        except Exception as exc:
            logger.exception("Не удалось добавить аудио %s", name)
            errors.append(f"{name}: {exc}")

    return {"added": added, "skipped": skipped, "errors": errors}


def extract_audio_files_from_zip(zip_bytes: bytes) -> list[dict]:
    """
    Возвращает список dict {name, bytes} из ZIP-архива — только аудиофайлы.
    Игнорирует вложенные папки (берёт basename).
    """
    result: list[dict] = []
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            base = Path(info.filename).name
            if not base or base.startswith("."):
                continue
            if not base.lower().endswith(_AUDIO_EXTS):
                continue
            try:
                with zf.open(info) as fh:
                    result.append({"name": base, "bytes": fh.read()})
            except Exception:
                logger.exception("Не удалось прочитать %s из архива", info.filename)
    return result

def delete_book_audio(audio_id):
    with db() as con:
        row = con.execute("SELECT audio_path FROM library_audio WHERE id=?", (audio_id,)).fetchone()
        con.execute("DELETE FROM library_audio WHERE id=?", (audio_id,))
    if row:
        try:
            (APP_DIR / row["audio_path"]).unlink(missing_ok=True)
        except Exception:
            logger.exception("Не удалось удалить аудио %s", row["audio_path"])


def get_book_audio(book_id):
    with db() as con:
        return con.execute(
            "SELECT * FROM library_audio WHERE book_id=? ORDER BY position, id",
            (book_id,)
        ).fetchall()


def set_group_book(group_id, book_id):
    with db() as con:
        if book_id:
            con.execute(
                "INSERT INTO group_books(group_id, book_id, assigned_at) VALUES(?,?,?) "
                "ON CONFLICT(group_id) DO UPDATE SET book_id=excluded.book_id, "
                "assigned_at=excluded.assigned_at",
                (group_id, book_id, datetime.now().isoformat(timespec="seconds"))
            )
        else:
            con.execute("DELETE FROM group_books WHERE group_id=?", (group_id,))


def get_group_book(group_id):
    with db() as con:
        return con.execute("""
            SELECT b.* FROM group_books gb
            JOIN library_books b ON b.id = gb.book_id
            WHERE gb.group_id = ?
        """, (group_id,)).fetchone()


def get_group_book_by_name(group_name):
    if not group_name:
        return None
    with db() as con:
        row = con.execute("SELECT id FROM groups WHERE name=?", (group_name,)).fetchone()
        if not row:
            return None
        return get_group_book(row["id"])


def get_group_book_by_name(group_name):
    if not group_name:
        return None
    with db() as con:
        row = con.execute("SELECT id FROM groups WHERE name=?", (group_name,)).fetchone()
        if not row:
            return None
        return get_group_book(row["id"])


def get_group_chapters_by_name(group_name) -> list[str]:
    """Возвращает список названий глав привязанного к группе учебника."""
    book = get_group_book_by_name(group_name)
    if not book:
        return []
    with db() as con:
        rows = con.execute(
            "SELECT title FROM book_chapters WHERE book_id=? ORDER BY position, id",
            (book["id"],),
        ).fetchall()
    return [r["title"] for r in rows]


# ---------- Задания по книге: доступ к данным ----------
def list_book_chapters(book_id):
    with db() as con:
        return con.execute(
            "SELECT * FROM book_chapters WHERE book_id=? ORDER BY position, id",
            (book_id,),
        ).fetchall()


def get_chapter(chapter_id):
    with db() as con:
        return con.execute("SELECT * FROM book_chapters WHERE id=?", (chapter_id,)).fetchone()


def add_book_chapter(book_id, title, page_from, page_to, summary, position):
    with db() as con:
        cur = con.execute(
            "INSERT INTO book_chapters(book_id, position, title, page_from, page_to, summary, created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (book_id, position, title, page_from, page_to, summary,
             datetime.now().isoformat(timespec="seconds")),
        )
        return cur.lastrowid


def clear_book_chapters(book_id):
    with db() as con:
        con.execute("DELETE FROM book_chapters WHERE book_id=?", (book_id,))


def get_chapter_glossary(chapter_id):
    with db() as con:
        return con.execute(
            "SELECT * FROM book_glossaries WHERE chapter_id=? ORDER BY word COLLATE NOCASE",
            (chapter_id,),
        ).fetchall()


def set_chapter_glossary(chapter_id, items):
    with db() as con:
        con.execute("DELETE FROM book_glossaries WHERE chapter_id=?", (chapter_id,))
        for it in items:
            con.execute(
                "INSERT INTO book_glossaries(chapter_id, word, article, translation, example, pos, created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (
                    chapter_id,
                    (it.get("word") or "").strip(),
                    (it.get("article") or "").strip(),
                    (it.get("translation") or "").strip(),
                    (it.get("example") or "").strip(),
                    (it.get("pos") or "").strip(),
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )

def clear_chapter_glossary(chapter_id) -> int:
    """Удаляет все записи глоссария для главы. Возвращает число удалённых."""
    with db() as con:
        cur = con.execute("DELETE FROM book_glossaries WHERE chapter_id=?", (chapter_id,))
        return cur.rowcount

# ---------- Задания по книге: PDF ----------
def read_pdf_text(book_pdf_path: str) -> tuple[str, int]:
    """
    Возвращает (полный_текст, число_страниц).
    Если PDF нет или pypdf не установлен — возвращает ("", 0).
    """
    if not book_pdf_path:
        return "", 0

    p = APP_DIR / book_pdf_path
    if not p.exists():
        return "", 0
    try:
        reader = PdfReader(str(p))
        pages = []
        for page in reader.pages:
            try:
                pages.append(page.extract_text() or "")
            except Exception:
                pages.append("")
        return "\n\n".join(pages), len(pages)
    except Exception:
        logger.exception("read_pdf_text failed: %s", book_pdf_path)
        return "", 0


def read_pdf_pages(book_pdf_path: str, page_from: int | None, page_to: int | None,
                   fallback_window: int = 15) -> tuple[str, str]:
    """
    Возвращает (текст, источник), где текст — только запрошенный диапазон страниц.

    Если page_from/page_to заданы:
        берём ровно эти страницы.
    Если нет — берём первые `fallback_window` страниц (лучше, чем весь учебник).

    Возвращает вторым элементом 'pages' или 'fallback'.
    """
    if not book_pdf_path:
        return "", "empty"

    p = APP_DIR / book_pdf_path
    if not p.exists():
        return "", "empty"
    try:
        reader = PdfReader(str(p))
    except Exception:
        logger.exception("read_pdf_pages: open failed")
        return "", "empty"

    total = len(reader.pages)

    if page_from and page_to:
        start = max(1, int(page_from))
        end = min(total, int(page_to))
        if start > end:
            start, end = end, start
        # ограничим до 30 страниц, чтобы не раздувать запрос
        end = min(end, start + 30 - 1)
        text_pages = []
        for i in range(start - 1, end):
            try:
                text_pages.append(reader.pages[i].extract_text() or "")
            except Exception:
                text_pages.append("")
        return "\n\n".join(text_pages), "pages"

    # fallback — первые N страниц
    n = min(total, max(1, fallback_window))
    text_pages = []
    for i in range(n):
        try:
            text_pages.append(reader.pages[i].extract_text() or "")
        except Exception:
            text_pages.append("")
    return "\n\n".join(text_pages), "fallback"


def chunk_text_for_llm(text: str, max_chars: int = 14000) -> list[str]:
    """Грубое разбиение длинного текста на куски для LLM."""
    if not text:
        return []
    text = text.strip()
    if len(text) <= max_chars:
        return [text]
    chunks = []
    cur = []
    cur_len = 0
    for para in re.split(r"\n\s*\n", text):
        if cur_len + len(para) > max_chars and cur:
            chunks.append("\n\n".join(cur))
            cur, cur_len = [], 0
        cur.append(para)
        cur_len += len(para) + 2
    if cur:
        chunks.append("\n\n".join(cur))
    return chunks


# ---------- Задания по книге: запросы к GigaChat ----------
def _giga_client():
    from gigachat import GigaChat
    credentials = os.getenv("GIGACHAT_CREDENTIALS", "").strip()
    if not credentials:
        raise RuntimeError("Не задан GIGACHAT_CREDENTIALS.")
    return GigaChat(
        credentials=credentials,
        base_url="https://api.giga.chat/v1",
        model="GigaChat-3-Ultra",
        verify_ssl_certs=False,
        max_tokens=20000,
    )


def _extract_json(text: str) -> dict | None:
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(_sanitize_json(m.group(0)))
    except Exception:
        logger.exception("extract_json failed")
        return None


def analyze_book_structure(book_row) -> dict:
    """
    Просит модель вернуть список уроков книги: [{title, page_from, page_to, summary}, ...].
    Работает с первой половиной текста PDF (обычно содержание книги + первые юниты).
    """
    text, n_pages = read_pdf_text(book_row["pdf_path"])
    if not text:
        return {"error": "Не удалось извлечь текст из PDF."}

    # Ограничим длину: первые ~30k символов хватает для оглавления и начала
    probe = text[:30000]

    prompt = f"""
Ты — методист по немецкому языку. Ниже — фрагмент учебника «{book_row['title']}»
(уровень {book_row['level'] or 'не указан'}).

Определи, из каких уроков (Lektionen/Units/Kapitel) состоит книга, и верни их
в порядке следования. Для каждого укажи:
- title — как он называется в книге;
- page_from, page_to — номера страниц (если можешь определить по разметке);
- summary — 1–2 предложения о содержании.

Верни ТОЛЬКО валидный JSON, без markdown-обёрток:
{{
  "chapters": [
    {{"title": "Lektion 1 — Hallo!", "page_from": 10, "page_to": 27, "summary": "..."}}
  ]
}}

Если разбиение по страницам неочевидно — поставь page_from/page_to = null.
Уроков обычно 8–12. Не придумывай несуществующие.

Текст:
\"\"\"
{probe}
\"\"\"
"""
    try:
        giga = _giga_client()
        resp = giga.chat(prompt)
        content = resp.choices[0].message.content or ""
    except Exception as exc:
        logger.exception("analyze_book_structure: GigaChat error")
        return {"error": f"Ошибка GigaChat: {exc}"}

    payload = _extract_json(content)
    if not payload or not isinstance(payload.get("chapters"), list):
        return {"error": "Модель вернула неожиданный JSON."}

    return {"chapters": payload["chapters"], "pages": n_pages}


def analyze_chapter_glossary(book_row, chapter_row) -> dict:
    """
    Составляет глоссарий по конкретной главе.
    Читает только страницы этой главы (или fallback, если страницы неизвестны),
    чтобы модель не вытаскивала лексику из первой лекции всей книги.
    """
    chapter_text, source = read_pdf_pages(
        book_row["pdf_path"],
        chapter_row["page_from"],
        chapter_row["page_to"],
        fallback_window=15,
    )

    if not chapter_text.strip():
        return {"error": "Не удалось извлечь текст для этого урока из PDF."}

    # Мягкий лимит: не гонять 100k символов
    sample = chapter_text[:24000]

    pages_info = ""
    if chapter_row["page_from"] and chapter_row["page_to"]:
        pages_info = (
            f"Страницы учебника: {chapter_row['page_from']}–{chapter_row['page_to']}."
        )
    elif source == "fallback":
        pages_info = (
            "ВНИМАНИЕ: точные страницы главы неизвестны. "
            "Ниже приведён фрагмент начала книги — определи по нему, "
            "какие слова относятся именно к уроку с указанным названием."
        )

    prompt = f"""
Ты — методист немецкого языка, составляешь глоссарий к уроку в учебнике.

Книга: «{book_row['title']}» (уровень {book_row['level'] or '—'}).
Урок: «{chapter_row['title']}».
{pages_info}

ЗАДАЧА: выписать ТОЛЬКО ту лексику, которая относится именно к теме этого урока
и встречается в приведённом тексте. Не брать общеупотребительные слова
(sein, haben, gehen, und, aber, sehr, gut, auch, nur, doch, immer и т. п.),
местоимения, артикли, союзы, частицы и базовые глаголы, которые есть в любой главе.

ПРАВИЛА:
1. Слова должны быть из темы урока — например, если урок про «Wohnen»,
   то Wohnung, Miete, Umzug, Nachbar, Möbel, etc., а НЕ «Haus» или «gehen»
   сами по себе.
2. Существительные — с артиклем (der/die/das). Указывай и в поле `article`,
   и в поле `word` в форме «die Wohnung».
3. Для каждого слова дай: перевод на русский, часть речи (noun/verb/adj/adv/phrase),
   пример из текста урока (короткий, если в тексте есть).
4. Учитывай уровень книги: для A1–A2 не бери сложную лексику B2+.
5. Не выдумывай слова, которых нет в тексте.
6. От 15 до 50 единиц — только релевантных теме.

Верни ТОЛЬКО валидный JSON:
{{
  "glossary": [
    {{
      "word": "die Wohnung",
      "article": "die",
      "translation": "квартира",
      "pos": "noun",
      "example": "Ich suche eine neue Wohnung."
    }}
  ]
}}

ТЕКСТ УРОКА:
\"\"\"
{sample}
\"\"\"
"""
    try:
        giga = _giga_client()
        resp = giga.chat(prompt)
        content = resp.choices[0].message.content or ""
    except Exception as exc:
        logger.exception("analyze_chapter_glossary: GigaChat error")
        return {"error": f"Ошибка GigaChat: {exc}"}

    payload = _extract_json(content)
    if not payload or not isinstance(payload.get("glossary"), list):
        return {"error": "Модель вернула неожиданный JSON."}

    # Постфильтр: убираем слова, которых нет в тексте главы (защита от галлюцинаций)
    raw_items = payload["glossary"]
    text_lower = chapter_text.lower()
    cleaned = []
    for it in raw_items:
        word = (it.get("word") or "").strip()
        if not word:
            continue
        # отбрасываем по одному слову из базовых стоп-слов, если модель их добавила
        core = re.sub(r"^(der|die|das)\s+", "", word, flags=re.IGNORECASE).strip().lower()
        if core in _GLOSSARY_STOPWORDS:
            continue
        # отбрасываем, если слова нет в тексте (учитываем корень)
        if core and core not in text_lower and not re.search(
            rf"\b{re.escape(core[:max(4, len(core) - 3)])}", text_lower
        ):
            # если совсем не нашли — всё равно оставим только если слово короткое (артикль и т.п.),
            # иначе выкидываем
            if len(core) > 4:
                continue
        cleaned.append(it)

    return {"glossary": cleaned, "source": source}


# ---------- Редактор карточки занятия ----------
def render_planned_card_editor(pl, *, key_prefix: str, compact: bool = False):
    pl_id = pl["id"]
    key_base = f"{key_prefix}_{pl_id}"
    refresh_needed = False

    # Учебник группы (бейдж)
    gbook = get_group_book_by_name(pl["group_name"]) if pl["group_name"] else None

    if gbook:
        st.markdown(
            f'<span class="hw-badge book">📖 {gbook["title"]}</span>',
            unsafe_allow_html=True
        )

    # Собираем варианты тем из учебника группы
    chapter_titles = get_group_chapters_by_name(pl["group_name"]) if pl["group_name"] else []
    current_title = pl["title"] or ""

    # Формируем список опций: если тема уже задана и её нет в списке — добавим её первой
    options = list(chapter_titles)
    if current_title and current_title not in options:
        options = [current_title] + options

    if chapter_titles:
        # Есть учебник с проанализированными уроками — даём выпадающий список
        # с возможностью ввести своё значение.
        try:
            topic_val = st.selectbox(
                "Тема (из учебника группы или своё название)",
                options=options,
                index=options.index(current_title) if current_title in options else 0,
                key=f"{key_base}_title",
                accept_new_options=True,
                placeholder="Например: Lektion 05 — Reisen",
                label_visibility="collapsed" if compact else "visible",
            )
        except TypeError:
            # Старая версия Streamlit без accept_new_options — fallback на selectbox без ввода
            topic_val = st.selectbox(
                "Тема (из учебника группы)",
                options=options,
                index=options.index(current_title) if current_title in options else 0,
                key=f"{key_base}_title",
                label_visibility="collapsed" if compact else "visible",
            )
    else:
        # Учебник не привязан или уроки ещё не выделены — обычное поле ввода
        topic_val = st.text_input(
            "Тема",
            value=current_title,
            key=f"{key_base}_title",
            placeholder="Например: Lektion 05 — Reisen",
            label_visibility="collapsed" if compact else "visible",
        )

    # Нормализуем: selectbox может вернуть None, если пользователь очистил поле
    topic_val = (topic_val or "").strip()

    if topic_val != current_title:
        update_planned(pl_id, title=topic_val)
        refresh_needed = True

    col1, col2 = st.columns(2)
    with col1:
        prep = st.checkbox("Подготовлено",
                           value=bool(pl["is_prepared"]),
                           key=f"{key_base}_prep")
        if prep != bool(pl["is_prepared"]):
            update_planned(pl_id, is_prepared=1 if prep else 0)
    with col2:
        cond = st.checkbox("Проведено",
                           value=bool(pl["is_conducted"]),
                           key=f"{key_base}_cond")
        if cond != bool(pl["is_conducted"]):
            update_planned(pl_id, is_conducted=1 if cond else 0)
            refresh_needed = True

    if pl["is_conducted"] and not pl["lesson_id"]:
        if st.button("📥 Загрузить", key=f"{key_base}_upload", use_container_width=True):
            st.session_state["prefill_planned_id"] = pl_id
            st.session_state["prefill_lesson"] = {
                "title": pl["title"] or "",
                "date": pl["scheduled_at"],
                "group_name": pl["group_name"],
            }
            st.session_state["pending_nav"] = "Добавить урок"
            st.rerun()

    if pl["lesson_id"]:
        st.caption(f"✅ Урок загружен (lesson #{pl['lesson_id']})")

    due_val = pl["homework_due_date"] or ""
    due_label = "Дедлайн домашки" if not compact else "Дедлайн ДЗ"
    new_due_date = st.date_input(
        due_label,
        value=date.fromisoformat(due_val) if due_val else None,
        key=f"{key_base}_due",
        format="DD.MM.YYYY",
    )
    new_due_iso = new_due_date.isoformat() if new_due_date else ""
    if new_due_iso != due_val:
        update_planned(pl_id, homework_due_date=new_due_iso)
        refresh_needed = True

    qc = st.columns([1, 1, 1])
    for i, days in enumerate([3, 5, 7]):
        if qc[i].button(f"+{days}д", key=f"{key_base}_q{i}", use_container_width=True):
            update_planned(
                pl_id,
                homework_due_date=(date.today() + timedelta(days=days)).isoformat(),
            )
            refresh_needed = True

    ensure_homework_rows(pl_id, pl["group_id"])
    hw_rows = get_homework_rows(pl_id)
    if not hw_rows:
        st.caption("В группе нет учеников.")
    else:
        done_cnt = sum(1 for h in hw_rows if h["is_done"])
        with st.expander(f"Отметки ({done_cnt}/{len(hw_rows)})", expanded=not compact):
            for hw in hw_rows:
                cA, cB = st.columns([4, 1])
                cA.write(hw["student_name"])
                checked = bool(hw["is_done"])
                new_checked = cB.checkbox(
                    "Сдал", value=checked,
                    key=f"{key_base}_hw_{hw['student_id']}",
                    label_visibility="collapsed",
                )
                if new_checked != checked:
                    set_homework_done(pl_id, hw["student_id"], new_checked)
                    refresh_needed = True

    state = homework_state(get_planned(pl_id))
    if state == "overdue":
        st.markdown('<span class="hw-badge bad">Домашка просрочена</span>',
                    unsafe_allow_html=True)
    elif state == "ok":
        st.markdown('<span class="hw-badge ok">Все сдали</span>',
                    unsafe_allow_html=True)
    elif state == "pending":
        st.markdown('<span class="hw-badge pending">Ожидается</span>',
                    unsafe_allow_html=True)

    if refresh_needed:
        st.rerun()


# ---------- Анализ GigaChat ----------
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


# =====================================================================
#                          AI-ПОМОЩНИК ПРЕПОДАВАТЕЛЯ
# =====================================================================

AI_SYSTEM_PROMPT = """Ты — ассистент преподавателя немецкого языка.
Твоя задача — помогать анализировать уроки, учеников, домашние задания и прогресс.

У тебя есть доступ к локальной базе данных SQLite (только ЧТЕНИЕ) через инструмент
`run_sql`. Ты можешь писать SQL-запросы SELECT, чтобы получать нужные данные.

Схема базы:

- students(id, name, normalized_name, created_at)
- lessons(id, title, lesson_date, topic, group_name, audio_path, transcript_filename, created_at)
- segments(id, lesson_id, segment_index, start_seconds, end_seconds, speaker_name,
           speaker_role, student_id, text)
- observations(id, lesson_id, student_id, category, topic, description, evidence,
               start_seconds, end_seconds, confidence, recommended_action, status, created_at)
- groups(id, name, level, note, created_at)
- group_students(group_id, student_id)
- group_schedule(id, group_id, weekday, start_time, duration_min)
- planned_lessons(id, group_id, scheduled_at, title, topic, is_prepared, is_conducted,
                 lesson_id, homework_due_date, created_at)
- homework_status(id, planned_lesson_id, student_id, is_done, done_at)
- library_books(id, title, author, level, note, cover_path, pdf_path, created_at)
- library_audio(id, book_id, position, title, audio_path, duration_note)
- group_books(group_id, book_id, assigned_at)

Дополнительные сведения:
- Преподавателя зовут {teacher}. с тобой общается только он, то есть ты знаешь с кем говоришь
- Даты хранятся в ISO (YYYY-MM-DD, YYYY-MM-DDTHH:MM).
- homework_due_date = '' означает, что домашка не задавалась.
- Статусы наблюдений: 'Требует проверки', 'Подтверждено', 'Отклонено'.

Правила:
1. Для получения данных используй run_sql (только SELECT).
2. Не выдумывай данные. Если SQL не дал ответа — скажи об этом.
3. Отвечай на русском, кратко и по делу. Если полезно — приводи таблицу или список.
4. Если вопрос неясен, задай уточняющий вопрос.
5. Никогда не выполняй INSERT/UPDATE/DELETE/DROP/ALTER — это запрещено.
6. Если нужно найти ученика по имени, а он может быть записан и кириллицей, и латиницей,
   сначала вызови инструмент `find_student` — он вернёт кандидатов.
   Не полагайся на точное `=` в SQL по имени, используй `LIKE '%...%'` или `find_student`.

ВАЖНО про имена: в базе имена могут быть записаны и на кириллице, и в латинице
(например, «Анна Лыкова» / «Anna Lykova»). Если точного совпадения нет — пробуй:
- `WHERE name LIKE '%<часть имени>%' COLLATE NOCASE`
- транслит: «Анна» -> «Anna», «Лыкова» -> «Lykova»
- инструмент `find_student`, который сам делает нечёткое сопоставление.

ИНСТРУМЕНТ УДАЛЕНИЯ ЗАНЯТИЙ: `delete_planned_lesson`.
Он удаляет карточку занятия из таблицы planned_lessons вместе со связанными
записями homework_status. По умолчанию НЕ удаляет карточку, к которой уже
привязан импортированный урок (lesson_id IS NOT NULL).

Аргументы:
- planned_lesson_id (integer) — точный id карточки (предпочтительно).
- group_name (string) — часть имени группы (LIKE).
- lesson_date (string) — дата в формате YYYY-MM-DD.
- title (string) — часть названия темы (LIKE).
- allow_with_lesson (boolean, default false) — разрешить удаление даже если
  карточка связана с импортированным уроком.

ПРАВИЛА работы с удалением:
1. НИКОГДА не удаляй карточку, пока не проверил её через run_sql
   (SELECT id, group_id, scheduled_at, title, lesson_id FROM planned_lessons ...).
   Убедись, что это именно та карточка, о которой говорит пользователь.
2. Предпочитай удаление по planned_lesson_id. Используй фильтры (group_name,
   lesson_date, title) только если пользователь не знает id.
3. Если найдено несколько кандидатов — не удаляй, а покажи их пользователю и
   попроси уточнить.
4. Если у карточки уже есть lesson_id — предупреди пользователя, что удаление
   отвяжет карточку от загруженного урока. Удаляй только после явного
   подтверждения пользователя и с allow_with_lesson=true.
5. После удаления всегда возвращай отчёт: какой id, группа, дата, тема.
""".replace("{teacher}", TEACHER_NAME)


AI_SQL_TOOL_SCHEMA = {
    "name": "run_sql",
    "description": (
        "Выполнить SQL SELECT-запрос к локальной базе данных приложения "
        "и вернуть результат в виде строк. Разрешены только SELECT-запросы."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "SQL SELECT-запрос (SQLite).",
            },
            "limit": {
                "type": "integer",
                "description": "Максимум строк в ответе (по умолчанию 200).",
            },
        },
        "required": ["query"],
    },
}


_AI_FORBIDDEN_KEYWORDS = (
    "insert", "update", "delete", "drop", "alter", "create", "replace",
    "attach", "detach", "pragma", "vacuum", "truncate", "grant", "revoke",
)

_GLOSSARY_STOPWORDS = {
    "sein", "haben", "werden", "gehen", "kommen", "machen", "sagen",
    "sehen", "wissen", "können", "müssen", "wollen", "sollen", "dürfen",
    "mögen", "und", "aber", "oder", "denn", "weil", "dass", "wenn",
    "ich", "du", "er", "sie", "es", "wir", "ihr", "man",
    "mein", "dein", "sein", "ihr", "unser", "euer",
    "der", "die", "das", "ein", "eine", "einen", "einem", "einer",
    "sehr", "gut", "auch", "nur", "schon", "noch", "immer", "oft",
    "hier", "da", "dort", "dann", "jetzt", "heute", "morgen", "gestern",
    "ja", "nein", "nicht", "kein", "keine",
    "in", "an", "auf", "mit", "von", "zu", "bei", "nach", "über", "unter",
    "vor", "hinter", "für", "gegen", "ohne", "um", "durch",
    "größe", "person", "leute", "sache", "ding", "stück",
}


# ---------- Транслитерация RU -> LAT для сопоставления имён ----------
_TRANSLIT_MAP = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def translit_ru_to_lat(text: str) -> str:
    if not text:
        return ""
    out = []
    for ch in text.lower():
        out.append(_TRANSLIT_MAP.get(ch, ch))
    return "".join(out)


def name_variants(name: str) -> list[str]:
    if not name:
        return []
    variants: set[str] = set()
    name = name.strip()
    parts = re.split(r"\s+", name)

    def add(s):
        s = (s or "").strip()
        if s:
            variants.add(s)
            variants.add(s.lower())

    add(name)
    add(translit_ru_to_lat(name))
    for p in parts:
        add(p)
        add(translit_ru_to_lat(p))
    return sorted(variants)


def ai_find_student(query: str) -> dict:
    if not query or not query.strip():
        return {"candidates": []}

    q = query.strip()
    variants = name_variants(q)
    with db() as con:
        found: dict[int, str] = {}
        for v in variants:
            pattern = f"%{v}%"
            rows = con.execute(
                "SELECT id, name FROM students WHERE lower(name) LIKE lower(?) LIMIT 20",
                (pattern,),
            ).fetchall()
            for r in rows:
                found[r["id"]] = r["name"]

        all_rows = con.execute("SELECT id, name FROM students").fetchall()
        q_lat = translit_ru_to_lat(q).lower()
        q_tokens = set(re.split(r"\s+", q_lat))
        for r in all_rows:
            r_lat = translit_ru_to_lat(r["name"]).lower()
            r_tokens = set(re.split(r"\s+", r_lat))
            if q_lat and (q_lat in r_lat or r_lat in q_lat):
                found[r["id"]] = r["name"]
            elif q_tokens & r_tokens and any(len(t) >= 3 for t in (q_tokens & r_tokens)):
                found[r["id"]] = r["name"]

    return {"candidates": [{"id": i, "name": n} for i, n in found.items()]}


def ai_delete_planned_lesson(
    planned_lesson_id: int | None = None,
    group_name: str | None = None,
    lesson_date: str | None = None,
    title: str | None = None,
    allow_with_lesson: bool = False,
) -> dict:
    def _row_to_dict(r):
        return {
            "id": r["id"],
            "group_id": r["group_id"],
            "group_name": r["group_name"],
            "scheduled_at": r["scheduled_at"],
            "title": r["title"],
            "lesson_id": r["lesson_id"],
        }

    with db() as con:
        if planned_lesson_id:
            row = con.execute("""
                SELECT pl.*, g.name AS group_name
                FROM planned_lessons pl
                JOIN groups g ON g.id = pl.group_id
                WHERE pl.id = ?
            """, (int(planned_lesson_id),)).fetchone()
            if not row:
                return {"error": f"Карточка с id={planned_lesson_id} не найдена."}
            if row["lesson_id"] and not allow_with_lesson:
                return {
                    "error": (
                        "У карточки уже есть импортированный урок "
                        f"(lesson_id={row['lesson_id']}). Чтобы удалить её "
                        "принудительно, передай allow_with_lesson=true."
                    ),
                    "candidate": _row_to_dict(row),
                }
            con.execute("DELETE FROM planned_lessons WHERE id=?", (row["id"],))
            return {"deleted": _row_to_dict(row)}

        if not any([group_name, lesson_date, title]):
            return {
                "error": (
                    "Укажи либо planned_lesson_id, либо хотя бы один из фильтров: "
                    "group_name, lesson_date (YYYY-MM-DD), title."
                )
            }

        sql = """
            SELECT pl.*, g.name AS group_name
            FROM planned_lessons pl
            JOIN groups g ON g.id = pl.group_id
            WHERE 1=1
        """
        params: list = []
        if group_name:
            sql += " AND g.name LIKE ?"
            params.append(f"%{group_name}%")
        if lesson_date:
            sql += " AND date(pl.scheduled_at) = date(?)"
            params.append(lesson_date)
        if title:
            sql += " AND pl.title LIKE ?"
            params.append(f"%{title}%")
        sql += " ORDER BY pl.scheduled_at"
        rows = con.execute(sql, params).fetchall()

        if not rows:
            return {"error": "По заданным фильтрам ничего не найдено."}

        if len(rows) > 1:
            return {
                "error": (
                    f"Найдено {len(rows)} карточек. Уточни запрос или передай "
                    "planned_lesson_id конкретной записи."
                ),
                "candidates": [_row_to_dict(r) for r in rows[:20]],
            }

        row = rows[0]
        if row["lesson_id"] and not allow_with_lesson:
            return {
                "error": (
                    "У найденной карточки уже есть импортированный урок "
                    f"(lesson_id={row['lesson_id']}). Если точно хочешь удалить — "
                    "передай allow_with_lesson=true."
                ),
                "candidate": _row_to_dict(row),
            }
        con.execute("DELETE FROM planned_lessons WHERE id=?", (row["id"],))
        return {"deleted": _row_to_dict(row)}


def ai_run_sql(query: str, limit: int = 200) -> dict:
    if not isinstance(query, str) or not query.strip():
        return {"error": "Пустой запрос."}

    lowered = query.lower()
    stripped = lowered.lstrip()
    if not (stripped.startswith("select") or stripped.startswith("with")):
        return {"error": "Разрешены только SELECT-запросы."}
    for kw in _AI_FORBIDDEN_KEYWORDS:
        if re.search(rf"\b{kw}\b", lowered):
            return {"error": f"Запрещённое ключевое слово: {kw}."}

    try:
        limit = max(1, min(int(limit or 200), 500))
    except Exception:
        limit = 200

    try:
        with db() as con:
            con.execute("PRAGMA query_only = ON")
            cur = con.execute(query)
            cols = [d[0] for d in (cur.description or [])]
            rows = cur.fetchmany(limit)
    except Exception as exc:
        logger.exception("ai_run_sql failed: %s", query)
        return {"error": f"Ошибка SQL: {exc}"}

    rows_serializable = [list(r) for r in rows]
    return {
        "columns": cols,
        "rows": rows_serializable,
        "row_count": len(rows_serializable),
    }


def _ai_try_extract_tool_calls(message) -> list[dict]:
    calls: list[dict] = []

    fc = getattr(message, "function_call", None)
    if fc is not None:
        name = getattr(fc, "name", None) or (fc.get("name") if isinstance(fc, dict) else None)
        args = getattr(fc, "arguments", None)
        if args is None and isinstance(fc, dict):
            args = fc.get("arguments")
        if name:
            calls.append({"name": name, "arguments": args, "raw": fc})

    fcs = getattr(message, "function_calls", None)
    if fcs:
        for one in fcs:
            name = getattr(one, "name", None) or (one.get("name") if isinstance(one, dict) else None)
            args = getattr(one, "arguments", None)
            if args is None and isinstance(one, dict):
                args = one.get("arguments")
            if name:
                calls.append({"name": name, "arguments": args, "raw": one})

    return calls


def _ai_parse_args(args) -> dict:
    if args is None:
        return {}
    if isinstance(args, dict):
        return args
    if isinstance(args, str):
        try:
            return json.loads(args)
        except Exception:
            return {}
    return {}


def ai_chat_step(messages: list[dict], max_tool_rounds: int = 4) -> str:
    credentials = os.getenv("GIGACHAT_CREDENTIALS", "").strip()
    if not credentials:
        raise RuntimeError("Не задан GIGACHAT_CREDENTIALS.")

    from gigachat import GigaChat
    from gigachat.models import Chat, Messages, MessagesRole, Function, FunctionParameters

    giga = GigaChat(
        credentials=credentials,
        base_url="https://api.giga.chat/v1",
        model="GigaChat-3-Ultra",
        verify_ssl_certs=False,
        max_tokens=8000,
    )

    def _to_sdk(msgs: list[dict]) -> list[Messages]:
        out: list[Messages] = []
        for m in msgs:
            role = m.get("role")
            content = m.get("content") or ""
            if role == "system":
                out.append(Messages(role=MessagesRole.SYSTEM, content=content))
            elif role == "user":
                out.append(Messages(role=MessagesRole.USER, content=content))
            elif role == "assistant":
                item: dict = {"role": "assistant", "content": content}
                fc = m.get("function_call")
                if fc:
                    args = fc.get("arguments")
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except Exception:
                            args = {}
                    item["function_call"] = {
                        "name": fc.get("name", "run_sql"),
                        "arguments": args if isinstance(args, dict) else {},
                    }
                out.append(Messages(**item))
            elif role == "function":
                out.append(Messages(
                    role=MessagesRole.FUNCTION,
                    content=content,
                    name=m.get("name") or "run_sql",
                ))
        return out

    functions = [
        Function(
            name=AI_SQL_TOOL_SCHEMA["name"],
            description=AI_SQL_TOOL_SCHEMA["description"],
            parameters=FunctionParameters(
                type="object",
                properties={
                    "query": {"type": "string", "description": "SQL SELECT-запрос."},
                    "limit": {"type": "integer", "description": "Максимум строк (<=500)."},
                },
                required=["query"],
            ),
        ),
        Function(
            name="find_student",
            description=(
                "Найти ученика по имени с учётом возможной транслитерации "
                "(кириллица <-> латиница). Возвращает список кандидатов с id и name."
            ),
            parameters=FunctionParameters(
                type="object",
                properties={
                    "query": {
                        "type": "string",
                        "description": "Имя (или его часть) на русском или латинице.",
                    },
                },
                required=["query"],
            ),
        ),
        Function(
            name="delete_planned_lesson",
            description=(
                "Удалить карточку занятия из planned_lessons вместе со связанными "
                "homework_status. Можно указать planned_lesson_id (точно) либо "
                "фильтры group_name/lesson_date/title. По умолчанию не удаляет "
                "карточку, к которой уже привязан импортированный урок "
                "(lesson_id IS NOT NULL) — для этого нужен allow_with_lesson=true."
            ),
            parameters=FunctionParameters(
                type="object",
                properties={
                    "planned_lesson_id": {
                        "type": "integer",
                        "description": "Точный id карточки занятия.",
                    },
                    "group_name": {
                        "type": "string",
                        "description": "Часть имени группы (LIKE).",
                    },
                    "lesson_date": {
                        "type": "string",
                        "description": "Дата занятия в формате YYYY-MM-DD.",
                    },
                    "title": {
                        "type": "string",
                        "description": "Часть названия темы (LIKE).",
                    },
                    "allow_with_lesson": {
                        "type": "boolean",
                        "description": (
                            "true — разрешить удаление карточки, к которой уже "
                            "привязан импортированный урок. По умолчанию false."
                        ),
                    },
                },
                required=[],
            ),
        ),
    ]

    working = list(messages)
    for _ in range(max_tool_rounds):
        chat = Chat(
            messages=_to_sdk(working),
            functions=functions,
            function_call="auto",
        )
        response = giga.chat(chat)
        msg = response.choices[0].message

        tool_calls = _ai_try_extract_tool_calls(msg)
        if not tool_calls:
            content = msg.content or ""
            working.append({"role": "assistant", "content": content})
            messages.clear()
            messages.extend(working)
            return content

        first = tool_calls[0]
        raw_args = first["arguments"]
        if isinstance(raw_args, str):
            try:
                raw_args = json.loads(raw_args)
            except Exception:
                raw_args = {}
        elif raw_args is None:
            raw_args = {}
        if not isinstance(raw_args, dict):
            raw_args = {}

        fc_dict = {
            "name": first["name"],
            "arguments": raw_args,
        }
        working.append({
            "role": "assistant",
            "content": msg.content or "",
            "function_call": fc_dict,
        })

        for call in tool_calls:
            name = call["name"]
            args = _ai_parse_args(call["arguments"])
            if name == "run_sql":
                result = ai_run_sql(args.get("query", ""), args.get("limit", 200))
            elif name == "find_student":
                result = ai_find_student(args.get("query", ""))
            elif name == "delete_planned_lesson":
                result = ai_delete_planned_lesson(
                    planned_lesson_id=args.get("planned_lesson_id"),
                    group_name=args.get("group_name"),
                    lesson_date=args.get("lesson_date"),
                    title=args.get("title"),
                    allow_with_lesson=bool(args.get("allow_with_lesson", False)),
                )
            else:
                result = {"error": f"Неизвестный инструмент: {name}"}
            working.append({
                "role": "function",
                "name": name,
                "content": json.dumps(result, ensure_ascii=False, default=str),
            })

    fallback = "Не удалось завершить ответ: превышено число шагов с инструментами."
    working.append({"role": "assistant", "content": fallback})
    messages.clear()
    messages.extend(working)
    return fallback


# ---------- Инициализация ----------
init_db()

# ---------- Боковая навигация ----------
PAGES = ["Обзор", "AI-помощник", "Добавить урок", "Календарь", "Ежедневник",
         "Библиотека", "Уроки и транскрипты", "Группы", "Ученики", "Аналитика"]

if "nav" not in st.session_state:
    st.session_state["nav"] = "Обзор"

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

    # ---- «Требует внимания» ----
    attention = get_today_attention_items()
    if attention:
        st.markdown("### 🎯 Требует внимания")
        for item in attention:
            col_icon, col_text, col_btn = st.columns([1, 8, 2])
            with col_icon:
                st.markdown(
                    f"<div style='font-size:1.6rem;text-align:center;"
                    f"padding-top:.35rem'>{item['emoji']}</div>",
                    unsafe_allow_html=True,
                )
            with col_text:
                st.markdown(f"**{item['title']}**")
                st.caption(item["detail"])
            with col_btn:
                if st.button(
                    item["action_label"],
                    key=f"attention_btn_{item['kind']}_{abs(hash(item['title'])) % 100000}",
                    use_container_width=True,
                ):
                    target = item["action"].get("nav")
                    if target:
                        st.session_state["pending_nav"] = target
                        for k, v in item["action"].get("set_state", {}).items():
                            st.session_state[k] = v
                        st.rerun()
        st.divider()

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
#                            AI-ПОМОЩНИК
# =====================================================================
elif page == "AI-помощник":
    st.markdown("## 🤖 AI-помощник преподавателя")
    st.caption("Задавайте вопросы о занятиях, учениках, домашке и прогрессе. "
               "Помощник умеет обращаться к базе данных через SQL.")

    if "ai_messages" not in st.session_state:
        st.session_state["ai_messages"] = [
            {"role": "system", "content": AI_SYSTEM_PROMPT},
        ]
    if "ai_display" not in st.session_state:
        st.session_state["ai_display"] = []

    top1, top2 = st.columns([3, 1])
    with top1:
        st.caption("Примеры: «Сколько уроков провёл каждый ученик?», "
                   "«Какие домашки просрочены?», "
                   "«О чём был последний урок группы A2?»")
    with top2:
        if st.button("🗑 Очистить диалог", use_container_width=True):
            st.session_state["ai_messages"] = [
                {"role": "system", "content": AI_SYSTEM_PROMPT},
            ]
            st.session_state["ai_display"] = []
            st.rerun()

    st.divider()

    chat_box = st.container()
    with chat_box:
        for m in st.session_state["ai_display"]:
            with st.chat_message(m["role"]):
                st.markdown(m["content"])

    user_input = st.chat_input("Ваш вопрос...")
    if user_input:
        st.session_state["ai_display"].append({"role": "user", "content": user_input})
        with chat_box:
            with st.chat_message("user"):
                st.markdown(user_input)

        st.session_state["ai_messages"].append({"role": "user", "content": user_input})

        with st.spinner("Думаю..."):
            try:
                answer = ai_chat_step(st.session_state["ai_messages"])
            except Exception as exc:
                logger.exception("Ошибка AI-помощника")
                answer = f"⚠️ Ошибка: {exc}"

        st.session_state["ai_display"].append({"role": "assistant", "content": answer})
        with chat_box:
            with st.chat_message("assistant"):
                st.markdown(answer)

    if not st.session_state["ai_display"]:
        st.markdown("### Быстрые запросы")
        q1, q2, q3 = st.columns(3)
        presets = [
            "Покажи 5 последних уроков",
            "Какие домашки просрочены?",
            "Сводка наблюдений по ученикам",
        ]
        for i, q in enumerate(presets):
            col = [q1, q2, q3][i]
            with col:
                if st.button(q, use_container_width=True, key=f"ai_preset_{i}"):
                    st.session_state["ai_display"].append(
                        {"role": "user", "content": q})
                    st.session_state["ai_messages"].append(
                        {"role": "user", "content": q})
                    with st.spinner("Думаю..."):
                        try:
                            answer = ai_chat_step(st.session_state["ai_messages"])
                        except Exception as exc:
                            logger.exception("Ошибка AI-помощника")
                            answer = f"⚠️ Ошибка: {exc}"
                    st.session_state["ai_display"].append(
                        {"role": "assistant", "content": answer})
                    st.rerun()


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

            topic_done = bool((pl["title"] or "").strip())
            checks = [
                f"{'✅' if topic_done else '⬜'} тема",
                f"{'✅' if pl['is_prepared'] else '⬜'} подготовлено",
                f"{'✅' if pl['is_conducted'] else '⬜'} проведено",
                f"{'✅' if pl['lesson_id'] else '⬜'} загружено",
            ]
            st.caption(" · ".join(checks))

            render_planned_card_editor(pl, key_prefix="cal")

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
        if "diary_ref" not in st.session_state:
            st.session_state["diary_ref"] = date.today()
        if "diary_date_widget" not in st.session_state:
            st.session_state["diary_date_widget"] = st.session_state["diary_ref"]

        def _diary_sync_from_widget():
            st.session_state["diary_ref"] = st.session_state["diary_date_widget"]

        def _diary_set(d):
            st.session_state["diary_ref"] = d
            st.session_state["diary_date_widget"] = d

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
            st.date_input(
                "Дата",
                value=st.session_state["diary_date_widget"],
                key="diary_date_widget",
                on_change=_diary_sync_from_widget,
            )

        ref_date = st.session_state["diary_ref"]

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

        if view_mode == "Неделя":
            monday = ref_date - timedelta(days=ref_date.weekday())
            sunday = monday + timedelta(days=6)
            range_start, range_end = monday, sunday
        else:
            range_start = range_end = ref_date

        for g in groups:
            if group_filter is None or g["id"] == group_filter:
                upsert_planned_lessons_for_window(g["id"], days_ahead=180)

        planned = get_planned_in_range(range_start, range_end, group_id=group_filter)

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

        def open_in_calendar(target_date: date):
            st.session_state["diary_ref_date"] = target_date
            st.session_state["pending_nav"] = "Календарь"
            st.rerun()

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
                            with st.expander("✏️ Редактировать", expanded=False):
                                render_planned_card_editor(
                                    pl,
                                    key_prefix=f"diary_w_{d.isoformat()}",
                                    compact=True,
                                )
                                if st.button(
                                        "Открыть в календаре",
                                        key=f"diary_w_open_{pl['id']}",
                                        use_container_width=True,
                                ):
                                    open_in_calendar(d)
                    st.markdown('</div>', unsafe_allow_html=True)
        else:
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

                st.markdown(
                    f"""<div class="cal-card" style="border-left-color:{border_color}">
                    <h4>{pl['title'] or '— без темы —'}</h4>
                    <div class="cal-meta">{pl['group_name']} · {dt.strftime('%H:%M')}
                    · {'проведено' if pl['is_conducted'] else 'запланировано'}
                    · {'подготовлено' if pl['is_prepared'] else 'не подготовлено'}</div>
                    </div>""",
                    unsafe_allow_html=True
                )
                render_planned_card_editor(
                    pl,
                    key_prefix=f"diary_d_{ref_date.isoformat()}",
                    compact=False,
                )
                if st.button(
                        "Открыть в календаре",
                        key=f"diary_d_open_{pl['id']}",
                        use_container_width=True,
                ):
                    open_in_calendar(ref_date)
                st.divider()

        st.caption("Все изменения сохраняются сразу. Полный обзор недели/дня — здесь; "
                   "детальные действия по уроку — во вкладке «Календарь».")


# =====================================================================
#                              БИБЛИОТЕКА
# =====================================================================
elif page == "Библиотека":
    st.markdown("## 📚 Библиотека")
    st.caption("Учебники, аудио и прикрепление книг к группам.")

    if "book_reader_id" not in st.session_state:
        st.session_state["book_reader_id"] = None
    if "book_audio_sel" not in st.session_state:
        st.session_state["book_audio_sel"] = None
    if "book_tasks_id" not in st.session_state:
        st.session_state["book_tasks_id"] = None
    if "book_tasks_chapter" not in st.session_state:
        st.session_state["book_tasks_chapter"] = None

    # ---- Если открыт режим заданий ----
    if st.session_state["book_tasks_id"]:
        tasks_book = get_book(st.session_state["book_tasks_id"])
        if not tasks_book:
            st.session_state["book_tasks_id"] = None
            st.rerun()

        top1, top2 = st.columns([3, 1])
        with top1:
            st.markdown(f"### 🎯 Задания по книге: {tasks_book['title']}")
            st.caption(f"{tasks_book['author'] or '—'} · {tasks_book['level'] or '—'}")
        with top2:
            if st.button("← К полке", use_container_width=True):
                st.session_state["book_tasks_id"] = None
                st.session_state["book_tasks_chapter"] = None
                st.rerun()

        st.divider()

        # ----- Открыт конкретный урок -----
        if st.session_state["book_tasks_chapter"]:
            ch_id = st.session_state["book_tasks_chapter"]
            chapter = get_chapter(ch_id)
            if not chapter:
                st.session_state["book_tasks_chapter"] = None
                st.rerun()

            bc1, bc2 = st.columns([3, 1])
            with bc1:
                st.markdown(f"#### 📖 {chapter['title']}")
                rng = ""
                if chapter["page_from"] and chapter["page_to"]:
                    rng = f" · стр. {chapter['page_from']}–{chapter['page_to']}"
                st.caption(rng.strip(" ·") or "")
            with bc2:
                if st.button("← К списку уроков", use_container_width=True):
                    st.session_state["book_tasks_chapter"] = None
                    st.rerun()

            if chapter["summary"]:
                st.info(chapter["summary"])

            if not (chapter["page_from"] and chapter["page_to"]):
                st.warning(
                    "Для этого урока не определены страницы. "
                    "Глоссарий будет строиться по первым 15 страницам книги — "
                    "лексика может захватывать соседние главы. "
                    "Переанализируйте книгу или задайте страницы вручную "
                    "(пока не поддерживается — доработаем позже)."
                )

            st.markdown("##### 📚 Глоссарий")
            glossary = get_chapter_glossary(ch_id)
            if glossary:
                df = pd.DataFrame([dict(g) for g in glossary])
                df = df[["word", "translation", "pos", "example"]]
                st.dataframe(
                    df,
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "word": "Слово",
                        "translation": "Перевод",
                        "pos": "Часть речи",
                        "example": "Пример",
                    },
                )
            else:
                st.caption("Глоссарий ещё не составлен.")

            gen_col, clr_col = st.columns([3, 1])
            with gen_col:
                if st.button("🧠 Составить глоссарий по уроку", type="primary",
                             use_container_width=True):
                    with st.spinner("Читаю PDF и строю глоссарий по теме урока..."):
                        result = analyze_chapter_glossary(tasks_book, chapter)
                    if "error" in result:
                        st.error(result["error"])
                    else:
                        set_chapter_glossary(ch_id, result["glossary"])
                        src = result.get("source")
                        suffix = "" if src == "pages" else " (страницы главы неизвестны — точность ниже)"
                        st.success(f"Готово. Слов: {len(result['glossary'])}.{suffix}")
                        st.rerun()

            with clr_col:
                # Показываем кнопку только если глоссарий есть — иначе нечего чистить
                if glossary:
                    confirm_key = f"confirm_clear_glossary_{ch_id}"
                    if st.session_state.get(confirm_key):
                        if st.button("✅ Да, удалить", key=f"yes_clear_{ch_id}",
                                     use_container_width=True):
                            n = clear_chapter_glossary(ch_id)
                            st.session_state.pop(confirm_key, None)
                            st.success(f"Удалено слов: {n}.")
                            st.rerun()
                    else:
                        if st.button("🧹 Обнулить", key=f"ask_clear_{ch_id}",
                                     use_container_width=True,
                                     help="Удалить весь глоссарий этой главы"):
                            st.session_state[confirm_key] = True
                            st.rerun()

            # Диалог подтверждения
            confirm_key = f"confirm_clear_glossary_{ch_id}"
            if st.session_state.get(confirm_key):
                st.warning(
                    "Удалить весь глоссарий этого урока? Действие необратимо. "
                    "Глоссарий можно будет составить заново."
                )
                c1, c2 = st.columns(2)
                with c1:
                    if st.button("Отмена", key=f"cancel_clear_{ch_id}",
                                 use_container_width=True):
                        st.session_state.pop(confirm_key, None)
                        st.rerun()
                with c2:
                    if st.button("Удалить", type="primary",
                                 key=f"confirm_yes_clear_{ch_id}",
                                 use_container_width=True):
                        n = clear_chapter_glossary(ch_id)
                        st.session_state.pop(confirm_key, None)
                        st.success(f"Удалено слов: {n}.")
                        st.rerun()

            st.divider()
            if st.button("← К списку уроков", key="bottom_back_chapters",
                         use_container_width=True):
                st.session_state["book_tasks_chapter"] = None
                st.rerun()

            st.divider()
            if st.button("← К списку уроков", key="bottom_back_chapters",
                         use_container_width=True):
                st.session_state["book_tasks_chapter"] = None
                st.rerun()
        else:
            # ----- Список уроков (как файловая система) -----
            chapters = list_book_chapters(tasks_book["id"])

            col_a, col_b = st.columns([3, 2])
            with col_a:
                st.markdown("##### 📂 Уроки книги")
                if not chapters:
                    st.caption("Уроки ещё не проанализированы.")
                else:
                    for i, ch in enumerate(chapters):
                        pages = ""
                        if ch["page_from"] and ch["page_to"]:
                            pages = f" · стр. {ch['page_from']}–{ch['page_to']}"
                        if st.button(
                            f"📁 {ch['title']}{pages}",
                            key=f"open_chapter_{ch['id']}",
                            use_container_width=True,
                        ):
                            st.session_state["book_tasks_chapter"] = ch["id"]
                            st.rerun()
            with col_b:
                st.markdown("##### 📊 Сводка")
                st.metric("Уроков", len(chapters))
                if chapters:
                    with db() as con:
                        total_g = con.execute("""
                            SELECT COUNT(*) n FROM book_glossaries g
                            JOIN book_chapters c ON c.id=g.chapter_id
                            WHERE c.book_id=?
                        """, (tasks_book["id"],)).fetchone()["n"]
                    st.metric("Слов в глоссариях", total_g)
                else:
                    st.metric("Слов в глоссариях", 0)

            st.divider()

            if not chapters:
                st.markdown(
                    "**Книга ещё не разобрана.** Нажмите кнопку ниже, чтобы "
                    "нейросеть выделила уроки по содержанию PDF."
                )
                if st.button("🧠 Проанализировать книгу", type="primary",
                             use_container_width=True):
                    with st.spinner("Анализирую структуру книги (может занять ~1 мин)..."):
                        result = analyze_book_structure(tasks_book)
                    if "error" in result:
                        st.error(result["error"])
                    else:
                        clear_book_chapters(tasks_book["id"])
                        for i, ch in enumerate(result["chapters"]):
                            add_book_chapter(
                                tasks_book["id"],
                                (ch.get("title") or f"Урок {i + 1}").strip(),
                                ch.get("page_from"),
                                ch.get("page_to"),
                                (ch.get("summary") or "").strip(),
                                i,
                            )
                        st.success(f"Найдено уроков: {len(result['chapters'])}.")
                        st.rerun()
            else:
                if st.button("🔁 Переанализировать книгу",
                             use_container_width=True):
                    with st.spinner("Анализирую..."):
                        result = analyze_book_structure(tasks_book)
                    if "error" in result:
                        st.error(result["error"])
                    else:
                        clear_book_chapters(tasks_book["id"])
                        for i, ch in enumerate(result["chapters"]):
                            add_book_chapter(
                                tasks_book["id"],
                                (ch.get("title") or f"Урок {i + 1}").strip(),
                                ch.get("page_from"),
                                ch.get("page_to"),
                                (ch.get("summary") or "").strip(),
                                i,
                            )
                        st.success("Структура обновлена.")
                        st.rerun()

    # ---- Если открыт ридер ----
    elif st.session_state["book_reader_id"]:
        book_id = st.session_state["book_reader_id"]
        book = get_book(book_id)
        if not book:
            st.session_state["book_reader_id"] = None
            st.rerun()

        top1, top2 = st.columns([3, 1])
        with top1:
            st.markdown(f"### {book['title']}")
            st.caption(f"{book['author'] or '—'} · {book['level'] or '—'}")
        with top2:
            if st.button("← Назад к полке", use_container_width=True):
                st.session_state["book_reader_id"] = None
                st.rerun()

        st.divider()

        left, right = st.columns([3, 2], gap="large")
        with left:
            st.markdown("**Чтение**")

            pdf_path_val = book["pdf_path"] or ""
            if pdf_path_val:
                pdf_file = APP_DIR / pdf_path_val
                if pdf_file.exists():
                    pdf_bytes = pdf_file.read_bytes()

                    # Пытаемся показать встроенным плеером Streamlit (>= 1.35)
                    pdf_rendered = False
                    if hasattr(st, "pdf"):
                        try:
                            st.pdf(pdf_bytes, height=800)
                            pdf_rendered = True
                        except Exception as e:
                            logger.exception("Ошибка st.pdf: %s", e)

                    # Фолбэк: HTML iframe с base64 (может не сработать в некоторых браузерах)
                    if not pdf_rendered:
                        import base64

                        b64 = base64.b64encode(pdf_bytes).decode("utf-8")
                        html = f'''
                            <div class="reader-frame">
                              <iframe src="data:application/pdf;base64,{b64}#toolbar=1"
                                      width="100%" height="800px"
                                      style="border:0"></iframe>
                            </div>
                        '''
                        components.html(html, height=820, scrolling=True)

                    st.download_button(
                        "⬇️ Скачать PDF",
                        data=pdf_bytes,
                        file_name=Path(pdf_path_val).name,
                        mime="application/pdf",
                        use_container_width=True,
                    )
                else:
                    st.warning(
                        f"Файл PDF не найден на диске: `{pdf_path_val}`. "
                        "Возможно, он был удалён вручную."
                    )
            else:
                st.info("У этого учебника нет загруженного PDF.")

        with right:
            st.markdown("**🎧 Аудио**")
            audios = get_book_audio(book_id)

            if not audios:
                st.caption("К учебнику пока не добавлено аудио.")
            else:
                # Высота списка подобрана так, чтобы вместе с плеером (≈80px)
                # и заголовком она совпадала с левым блоком чтения (≈800px).
                list_height = 700
                audio_list_container = st.container(height=list_height, border=False)

                with audio_list_container:
                    for a in audios:
                        active_cls = ""
                        if st.session_state["book_audio_sel"] == a["id"]:
                            active_cls = " active"

                        col_info, col_play, col_del = st.columns([5, 1, 1])
                        with col_info:
                            st.markdown(
                                f'<div class="audio-item{active_cls}">'
                                f'<div class="audio-track">{a["position"]:02d}. {a["title"]}</div>'
                                f'<div class="audio-sub">{a["duration_note"] or ""}</div>'
                                f'</div>',
                                unsafe_allow_html=True,
                            )
                        with col_play:
                            if st.button(
                                    "▶",
                                    key=f"play_audio_{a['id']}",
                                    use_container_width=True,
                                    help="Слушать",
                            ):
                                st.session_state["book_audio_sel"] = a["id"]
                                st.rerun()
                        with col_del:
                            if st.button(
                                    "🗑",
                                    key=f"del_audio_{a['id']}",
                                    use_container_width=True,
                                    help="Удалить",
                            ):
                                delete_book_audio(a["id"])
                                if st.session_state["book_audio_sel"] == a["id"]:
                                    st.session_state["book_audio_sel"] = None
                                st.rerun()

                # Плеер вынесен за пределы прокручиваемого списка — он всегда виден
                sel_id = st.session_state["book_audio_sel"]
                sel_row = next((x for x in audios if x["id"] == sel_id), None) if sel_id else None
                if sel_row:
                    audio_file = APP_DIR / sel_row["audio_path"]
                    if audio_file.exists():
                        st.markdown(f"**Сейчас играет:** {sel_row['title']}")
                        st.audio(audio_file.read_bytes())
                else:
                    st.caption("Выберите трек, чтобы включить воспроизведение.")

        st.divider()
        st.divider()
        st.markdown("### Управление аудио")

        tab_one, tab_many, tab_zip = st.tabs(
            ["Один файл", "Несколько файлов", "ZIP-архив"]
        )

        # --- Один файл ---
        with tab_one:
            with st.form(f"add_audio_form_{book_id}", clear_on_submit=True):
                a_title = st.text_input(
                    "Название трека",
                    placeholder="Например: Lektion 1 — Hören",
                )
                a_note = st.text_input(
                    "Пометка (необязательно)",
                    placeholder="стр. 12 / 2:34",
                )
                a_file = st.file_uploader(
                    "Аудиофайл",
                    type=["mp3", "m4a", "wav", "ogg", "aac", "flac", "opus"],
                    key=f"one_audio_{book_id}",
                )
                if st.form_submit_button("➕ Добавить аудио", type="primary"):
                    if not a_file:
                        st.error("Выберите аудиофайл.")
                    else:
                        add_book_audio(
                            book_id,
                            a_title or Path(a_file.name).stem,
                            a_file.getvalue(),
                            a_file.name,
                            a_note,
                        )
                        st.success("Аудио добавлено.")
                        st.rerun()

        # --- Несколько файлов ---
        with tab_many:
            a_files = st.file_uploader(
                "Аудиофайлы (можно выбрать несколько сразу)",
                type=["mp3", "m4a", "wav", "ogg", "aac", "flac", "opus"],
                accept_multiple_files=True,
                key=f"many_audio_{book_id}",
            )
            if st.button("➕ Добавить выбранные файлы", type="primary",
                         key=f"many_audio_btn_{book_id}"):
                if not a_files:
                    st.error("Выберите хотя бы один файл.")
                else:
                    result = add_book_audio_bulk(
                        book_id,
                        [{"name": f.name, "bytes": f.getvalue()} for f in a_files],
                    )
                    if result["added"]:
                        st.success(f"Добавлено файлов: {result['added']}.")
                    if result["skipped"]:
                        st.warning(
                            "Пропущены (не аудио или пустые): "
                            + ", ".join(result["skipped"][:10])
                            + ("…" if len(result["skipped"]) > 10 else "")
                        )
                    for err in result["errors"]:
                        st.error(err)
                    st.rerun()

        # --- ZIP-архив (рекомендуется для папок) ---
        with tab_zip:
            st.caption(
                "Загрузите ZIP-архив с аудиофайлами — названия треков будут взяты "
                "из имён файлов (без расширения, `_` заменятся на пробел)."
            )
            zip_file = st.file_uploader(
                "ZIP-архив с аудио",
                type=["zip"],
                key=f"zip_audio_{book_id}",
            )
            if st.button("📦 Распаковать и добавить всё аудио", type="primary",
                         key=f"zip_audio_btn_{book_id}"):
                if not zip_file:
                    st.error("Выберите ZIP-архив.")
                else:
                    try:
                        files = extract_audio_files_from_zip(zip_file.getvalue())
                    except Exception as exc:
                        logger.exception("Не удалось прочитать ZIP")
                        st.error(f"Ошибка чтения архива: {exc}")
                        files = []

                    if not files:
                        st.warning(
                            "В архиве не найдено аудиофайлов (mp3/m4a/wav/ogg/aac/flac/opus)."
                        )
                    else:
                        result = add_book_audio_bulk(book_id, files)
                        if result["added"]:
                            st.success(f"Добавлено треков: {result['added']}.")
                        if result["skipped"]:
                            st.warning(
                                f"Пропущено файлов: {len(result['skipped'])} "
                                f"(не аудио или пустые)."
                            )
                        for err in result["errors"]:
                            st.error(err)
                        st.rerun()
    else:
        # ---- Полка ----
        tab_shelf, tab_add = st.tabs(["Полка", "Добавить учебник"])

        with tab_add:
            with st.form("add_book_form", clear_on_submit=True):
                b_title = st.text_input("Название учебника", placeholder="Menschen A2.1")
                b_author = st.text_input("Автор(ы)", placeholder="Sandra Evans, Angela Pude")
                b_level = st.text_input("Уровень", placeholder="A2.1")
                b_note = st.text_area("Заметка", height=80)
                b_cover = st.file_uploader("Обложка (изображение)",
                                           type=["jpg", "jpeg", "png", "webp"])
                b_pdf = st.file_uploader("PDF учебника", type=["pdf"])
                if st.form_submit_button("📥 Добавить в библиотеку", type="primary"):
                    if not b_title.strip():
                        st.error("Укажите название.")
                    else:
                        create_book(
                            b_title,
                            b_author,
                            b_level,
                            b_note,
                            b_cover.getvalue() if b_cover else b"",
                            b_cover.name if b_cover else "",
                            b_pdf.getvalue() if b_pdf else b"",
                            b_pdf.name if b_pdf else "",
                        )
                        st.success("Учебник добавлен.")
                        st.rerun()

        with tab_shelf:
            books = list_books()
            if not books:
                st.info("Полка пуста. Загрузите первый учебник во вкладке «Добавить учебник».")
            else:
                cols = st.columns(4)
                for idx, b in enumerate(books):
                    col = cols[idx % 4]
                    with col:
                        cover_html = ""
                        if b["cover_path"]:
                            cover_file = APP_DIR / b["cover_path"]
                            if cover_file.exists():
                                import base64
                                b64 = base64.b64encode(cover_file.read_bytes()).decode("utf-8")
                                cover_html = (
                                    f'<img class="book-cover" '
                                    f'src="data:image/jpeg;base64,{b64}" '
                                    f'alt="{b["title"]}"/>'
                                )
                        if not cover_html:
                            cover_html = f'<div class="book-cover">{b["title"]}</div>'
                        st.markdown(
                            f'<div class="book-card">{cover_html}'
                            f'<div class="book-title">{b["title"]}</div>'
                            f'<div class="book-meta">{b["author"] or "—"} · {b["level"] or "—"}</div>'
                            f'</div>',
                            unsafe_allow_html=True,
                        )
                        btn1, btn2 = st.columns(2)
                        with btn1:
                            if st.button("📖 Открыть", key=f"open_book_{b['id']}",
                                         use_container_width=True):
                                st.session_state["book_reader_id"] = b["id"]
                                st.session_state["book_audio_sel"] = None
                                st.rerun()
                        with btn2:
                            if st.button("🎯 Задания", key=f"tasks_book_{b['id']}",
                                         use_container_width=True,
                                         help="Открыть режим заданий по книге"):
                                st.session_state["book_tasks_id"] = b["id"]
                                st.session_state["book_tasks_chapter"] = None
                                st.rerun()


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

            st.markdown("### Учебник группы")
            all_books = list_books()
            current_book = get_group_book(gid)
            if not all_books:
                st.caption("В библиотеке пока нет учебников. Добавьте их во вкладке «Библиотека».")
            else:
                book_options = {f"— нет —": None}
                for b in all_books:
                    book_options[f"{b['title']} ({b['level'] or '—'})"] = b["id"]
                default_label = "— нет —"
                if current_book:
                    for label, bid in book_options.items():
                        if bid == current_book["id"]:
                            default_label = label
                            break
                labels = list(book_options.keys())
                idx = labels.index(default_label) if default_label in labels else 0
                chosen_label = st.selectbox("Прикрепить учебник", labels, index=idx,
                                            key=f"gbook_{gid}")
                if st.button("💾 Сохранить учебник группы", key=f"gbook_save_{gid}"):
                    set_group_book(gid, book_options[chosen_label])
                    st.success("Учебник группы обновлён.")
                    st.rerun()
                if current_book:
                    st.caption(f"Текущий учебник: **{current_book['title']}**")

            st.divider()

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

            st.markdown("### Расписание")
            sched = get_group_schedule(gid)
            st.caption("Дни недели и время начала. Продолжительность — минуты.")

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