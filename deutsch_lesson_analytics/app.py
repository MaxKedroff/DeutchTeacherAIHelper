import os
import re
import json
import uuid
import sqlite3
import logging
import traceback
import sys
from pathlib import Path
from datetime import datetime

import streamlit as st
import pandas as pd

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
    # В консоль (stdout)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.DEBUG)
    console_fmt = logging.Formatter(
        "[%(asctime)s] %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    console_handler.setFormatter(console_fmt)
    logger.addHandler(console_handler)

    # В файл app.log рядом с app.py
    file_handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_fmt = logging.Formatter(
        "[%(asctime)s] %(levelname)s %(name)s: %(message)s"
    )
    file_handler.setFormatter(file_fmt)
    logger.addHandler(file_handler)

logger.info("=== Приложение запущено. Лог: %s ===", LOG_PATH)
# ----------------------------------

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
    .small-muted {opacity: .68; font-size: .88rem;}
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
</style>
""", unsafe_allow_html=True)


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
        """)
        ensure_student(con, TEACHER_NAME)


def normalize_name(name):
    return re.sub(r"\s+", " ", name.strip()).casefold()


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
        hours, minutes, seconds = parts
    elif len(parts) == 2:
        hours, minutes, seconds = "0", parts[0], parts[1]
    else:
        raise ValueError(f"Не удалось разобрать временную метку: {value}")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def parse_vtt(content):
    """Parse WebVTT cues and infer speaker from 'Name: utterance' at cue start."""
    content = content.lstrip("\ufeff")
    blocks = re.split(r"\n\s*\n", content.strip())
    segments = []
    for block in blocks:
        lines = [line.strip("\r") for line in block.splitlines() if line.strip()]
        if not lines or lines[0].strip() == "WEBVTT":
            continue
        time_line_idx = next((i for i, line in enumerate(lines) if "-->" in line), None)
        if time_line_idx is None:
            continue
        time_line = lines[time_line_idx]
        match = re.match(r"\s*(\S+)\s+-->\s+(\S+)", time_line)
        if not match:
            continue
        start = parse_timestamp(match.group(1))
        end = parse_timestamp(match.group(2))
        text = " ".join(lines[time_line_idx + 1:]).strip()
        if not text:
            continue
        speaker = "Не определён"
        utterance = text
        speaker_match = re.match(r"^([^:：]{1,100})[:：]\s*(.*)$", text)
        if speaker_match:
            speaker = speaker_match.group(1).strip()
            utterance = speaker_match.group(2).strip()
        segments.append({
            "start_seconds": start,
            "end_seconds": end,
            "speaker_name": speaker,
            "text": utterance or text,
        })
    return segments


def fmt_time(seconds):
    if seconds is None:
        return "—"
    seconds = max(0, float(seconds))
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    if h:
        return f"{h:02d}:{m:02d}:{s:06.3f}"
    return f"{m:02d}:{s:06.3f}"


def get_counts():
    with db() as con:
        students = con.execute(
            "SELECT COUNT(*) AS n FROM students WHERE normalized_name != ?",
            (normalize_name(TEACHER_NAME),)
        ).fetchone()["n"]
        lessons = con.execute("SELECT COUNT(*) AS n FROM lessons").fetchone()["n"]
        segments = con.execute("SELECT COUNT(*) AS n FROM segments").fetchone()["n"]
        observations = con.execute("SELECT COUNT(*) AS n FROM observations").fetchone()["n"]
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


def run_ai_analysis(lesson_id):
    logger.info("---- run_ai_analysis START lesson_id=%s ----", lesson_id)

    # 1. Проверка ключа
    credentials = os.getenv("GIGACHAT_CREDENTIALS", "").strip()
    if not credentials:
        logger.error("GIGACHAT_CREDENTIALS не задан в переменных окружения.")
        return False, "Не задан GIGACHAT_CREDENTIALS. Добавьте ключ авторизации в переменные окружения и перезапустите приложение."
    logger.info("Ключ GIGACHAT_CREDENTIALS найден (длина=%d, начинается с %s...)",
                len(credentials), credentials[:8])

    # 2. Проверка SDK
    try:
        from gigachat import GigaChat
        logger.info("Пакет gigachat импортирован успешно. Версия: %s",
                    getattr(__import__("gigachat"), "__version__", "неизвестна"))
    except ImportError:
        logger.exception("Не удалось импортировать gigachat.")
        return False, "Не установлен пакет gigachat. Выполните: pip install gigachat"

    # 3. Чтение данных из БД
    try:
        with db() as con:
            lesson = con.execute("SELECT * FROM lessons WHERE id=?", (lesson_id,)).fetchone()
            rows = con.execute("""
                SELECT start_seconds, end_seconds, speaker_name, speaker_role, text
                FROM segments WHERE lesson_id=? ORDER BY segment_index
            """, (lesson_id,)).fetchall()
        logger.info("Загружено из БД: lesson=%s, сегментов=%d",
                    lesson["title"] if lesson else None, len(rows))
    except Exception:
        logger.exception("Ошибка чтения из БД для lesson_id=%s", lesson_id)
        return False, "Не удалось прочитать урок из базы. См. app.log."

    if not lesson:
        logger.error("Урок с id=%s не найден.", lesson_id)
        return False, "Урок не найден в базе."

    transcript = "\n".join(
        f"[{fmt_time(r['start_seconds'])}-{fmt_time(r['end_seconds'])}] "
        f"{r['speaker_name']} ({r['speaker_role']}): {r['text']}" for r in rows
    )
    if not transcript.strip():
        logger.warning("Пустой транскрипт для lesson_id=%s", lesson_id)
        return False, "В уроке нет распознанных реплик."

    logger.debug("Длина транскрипта: %d символов", len(transcript))
    logger.debug("Первые 300 символов транскрипта: %s", transcript[:300])

    prompt = f"""
Ты — аналитик уроков немецкого языка. Проанализируй только подтверждаемые наблюдения.
Не выдумывай ошибки; отличай ошибки немецкого языка от вероятных ошибок транскрибации.
Не делай выводов о личности, мотивации или способностях. Если ученик или факт неясен,
укажи это и снизь confidence. Реплики преподавателя — контекст, а не ошибки ученика.
Для ошибок немецкого укажи category (например: грамматика, лексика, произношение,
понимание на слух, беглость, вопрос, трудность, замечание, прогресс, другое).
Пиши описание и рекомендации на русском. Цитаты evidence должны точно совпадать
с транскриптом. Если говорящий не является преподавателем, используй его имя как student_name.
Возвращай только JSON по схеме:
{{
 "lesson_summary": "краткая сводка на русском",
 "observations": [
  {{
   "student_name": "имя или null",
   "category": "категория",
   "topic": "тема/навык",
   "description": "что произошло",
   "evidence": "точная цитата из транскрипта",
   "timestamp_start": 0.0,
   "timestamp_end": 1.0,
   "confidence": 0.0,
   "recommended_action": "конкретный следующий шаг"
  }}
 ]
}}
Урок: {lesson['title']}
Тема: {lesson['topic'] or 'не указана'}
Транскрипт:
{transcript}
"""
    logger.debug("Длина промпта: %d символов", len(prompt))

    # 4. Создание клиента
    try:
        logger.info("Создаю GigaChat client (model=%s, base_url=%s, verify_ssl_certs=False)",
                    MODEL_NAME, "https://api.giga.chat/v1")
        giga = GigaChat(
            credentials=credentials,
            base_url="https://api.giga.chat/v1",
            model=MODEL_NAME,
            verify_ssl_certs=False,
            max_tokens=8192
        )
        logger.info("Клиент GigaChat создан успешно.")
    except Exception:
        logger.exception("Ошибка при создании клиента GigaChat.")
        return False, "Не удалось создать клиент GigaChat. См. app.log."

    # 5. Запрос
    try:
        logger.info("Отправляю запрос в GigaChat...")
        response = giga.chat(prompt)
        logger.info("Ответ получен. Тип: %s", type(response))
    except Exception:
        logger.exception("Ошибка при вызове giga.chat()")
        return False, "Ошибка при обращении к GigaChat. См. app.log."

    # 6. Разбор ответа
    try:
        logger.debug("Raw response repr: %r", response)
        content = response.choices[0].message.content
        logger.info("Длина ответа модели: %d символов", len(content))
        logger.debug("Первые 500 символов ответа модели: %s", content[:500])
    except Exception:
        logger.exception("Не удалось извлечь content из ответа. Структура объекта иная.")
        return False, "Неожиданный формат ответа GigaChat. См. app.log."

    # 7. Парсинг JSON
    try:
        json_match = re.search(r"\{.*\}", content, re.DOTALL)
        if not json_match:
            logger.error("В ответе модели не найден JSON. Полный ответ:\n%s", content)
            return False, "Не удалось извлечь JSON из ответа модели."
        payload = json.loads(json_match.group(0))
        observations = payload.get("observations", [])
        logger.info("JSON успешно распарсен. Наблюдений: %d", len(observations))
    except json.JSONDecodeError:
        logger.exception("JSONDecodeError. Сырой фрагмент:\n%s",
                         json_match.group(0) if json_match else "(нет)")
        return False, "Ответ модели не является корректным JSON. См. app.log."
    except Exception:
        logger.exception("Ошибка при парсинге ответа.")
        return False, "Ошибка при разборе ответа модели. См. app.log."

    # 8. Запись в БД
    try:
        with db() as con:
            con.execute("DELETE FROM observations WHERE lesson_id=?", (lesson_id,))
            for item in observations:
                name = item.get("student_name")
                student_id = None
                if name and normalize_name(name) != normalize_name(TEACHER_NAME):
                    student_id = ensure_student(con, name)
                start = item.get("timestamp_start")
                end = item.get("timestamp_end")
                con.execute("""
                    INSERT INTO observations
                    (lesson_id, student_id, category, topic, description, evidence,
                     start_seconds, end_seconds, confidence, recommended_action, status, created_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    lesson_id, student_id, item.get("category", "Другое"),
                    item.get("topic", ""), item.get("description", ""),
                    item.get("evidence", ""), start, end, item.get("confidence"),
                    item.get("recommended_action", ""), "Требует проверки",
                    datetime.now().isoformat(timespec="seconds")
                ))
            con.execute("UPDATE lessons SET topic=COALESCE(NULLIF(topic,''), ?) WHERE id=?",
                        (payload.get("lesson_summary", ""), lesson_id))
        logger.info("Наблюдения записаны в БД: %d шт.", len(observations))
    except Exception:
        logger.exception("Ошибка записи наблюдений в БД.")
        return False, "Ошибка при сохранении наблюдений. См. app.log."

    logger.info("---- run_ai_analysis END lesson_id=%s OK ----", lesson_id)
    return True, f"Анализ завершён. Добавлено наблюдений: {len(observations)}."


init_db()

with st.sidebar:
    st.markdown("## 📚 Deutsch Lernanalyse")
    st.caption("Аналитика уроков немецкого языка")
    page = st.radio(
        "Навигация",
        ["Обзор", "Добавить урок", "Уроки и транскрипты", "Ученики", "Аналитика"],
        label_visibility="collapsed"
    )
    st.divider()
    st.markdown("**Рабочая база**")
    st.caption("SQLite · локальное хранение")
    st.caption(f"Преподаватель: {TEACHER_NAME}")

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
        st.info("Пока нет занятий. Перейдите в «Добавить урок», чтобы загрузить аудио и VTT-транскрибацию.")
    else:
        st.dataframe(lessons_df, use_container_width=True, hide_index=True)
    st.markdown("### Быстрый старт")
    a, b, c = st.columns(3)
    with a:
        st.markdown("**01 · Импорт**")
        st.caption("Загрузите аудио `.m4a` и транскрибацию `.vtt`.")
    with b:
        st.markdown("**02 · Проверка**")
        st.caption("Проверьте говорящих, временные метки и текст.")
    with c:
        st.markdown("**03 · Анализ**")
        st.caption("Запустите ИИ-анализ и подтвердите наблюдения.")

elif page == "Добавить урок":
    st.markdown("## Добавить занятие")
    st.caption("Загрузите аудио и WebVTT-транскрибацию. Реплики и новые имена учеников будут сохранены в SQLite.")
    with st.form("lesson_upload", clear_on_submit=False):
        col1, col2 = st.columns(2)
        with col1:
            title = st.text_input("Название занятия", placeholder="Например: Lektion 04 — Einkaufen")
            lesson_date = st.date_input("Дата занятия", value=datetime.now().date())
            topic = st.text_input("Тема урока", placeholder="Например: Fragen im Alltag")
        with col2:
            group_name = st.text_input("Группа (необязательно)", placeholder="Например: A2 — Gruppe 1")
            audio_file = st.file_uploader("Аудиозапись (.m4a)", type=["m4a", "mp3", "wav", "mp4", "aac", "ogg"])
            vtt_file = st.file_uploader("Транскрибация (.vtt)", type=["vtt", "txt"])
        submitted = st.form_submit_button("Импортировать занятие", type="primary", use_container_width=True)
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
                    st.error("Не удалось найти реплики в файле. Проверьте, что это WebVTT с временными метками.")
                else:
                    file_id = uuid.uuid4().hex[:12]
                    safe_audio_name = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(audio_file.name).name)
                    audio_path = AUDIO_DIR / f"{file_id}_{safe_audio_name}"
                    audio_path.write_bytes(audio_file.getvalue())
                    with db() as con:
                        cur = con.execute("""
                            INSERT INTO lessons(title, lesson_date, topic, group_name, audio_path,
                                                transcript_filename, created_at)
                            VALUES(?,?,?,?,?,?,?)
                        """, (
                            title.strip(), lesson_date.isoformat(), topic.strip(), group_name.strip(),
                            str(audio_path.relative_to(APP_DIR)), vtt_file.name,
                            datetime.now().isoformat(timespec="seconds")
                        ))
                        lesson_id = cur.lastrowid
                        for i, seg in enumerate(parsed):
                            speaker = seg["speaker_name"]
                            if normalize_name(speaker) == normalize_name(TEACHER_NAME):
                                role = "Преподаватель"
                                student_id = ensure_student(con, TEACHER_NAME)
                            elif speaker == "Не определён":
                                role = "Не определён"
                                student_id = None
                            else:
                                role = "Ученик"
                                student_id = ensure_student(con, speaker)
                            con.execute("""
                                INSERT INTO segments(lesson_id, segment_index, start_seconds, end_seconds,
                                                     speaker_name, speaker_role, student_id, text)
                                VALUES(?,?,?,?,?,?,?,?)
                            """, (lesson_id, i, seg["start_seconds"], seg["end_seconds"],
                                  speaker, role, student_id, seg["text"]))
                    st.success(f"Занятие импортировано: {len(parsed)} реплик.")
                    st.session_state["last_imported_lesson"] = lesson_id
            except Exception as exc:
                st.error(f"Ошибка импорта: {exc}")

    if st.session_state.get("last_imported_lesson"):
        lid = st.session_state["last_imported_lesson"]
        st.divider()
        st.markdown("### Следующий шаг")
        st.success("Файлы сохранены, говорящие выделены, ученики добавлены в базу при необходимости.")
        if st.button("Открыть импортированное занятие", type="primary"):
            st.session_state["selected_lesson_id"] = lid
            st.session_state["page_hint"] = "lesson"
            st.rerun()

elif page == "Уроки и транскрипты":
    st.markdown("## Уроки и транскрипты")
    with db() as con:
        lessons = con.execute("SELECT * FROM lessons ORDER BY lesson_date DESC, id DESC").fetchall()
    if not lessons:
        st.info("Здесь появятся импортированные занятия.")
    else:
        choices = {f"#{l['id']} · {l['lesson_date']} · {l['title']}": l["id"] for l in lessons}
        default_idx = 0
        selected_default = st.session_state.get("selected_lesson_id")
        if selected_default in choices.values():
            default_idx = list(choices.values()).index(selected_default)
        chosen_label = st.selectbox("Выберите занятие", list(choices.keys()), index=default_idx)
        lesson_id = choices[chosen_label]
        with db() as con:
            lesson = con.execute("SELECT * FROM lessons WHERE id=?", (lesson_id,)).fetchone()
            segs = con.execute("SELECT * FROM segments WHERE lesson_id=? ORDER BY segment_index", (lesson_id,)).fetchall()
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
                path = APP_DIR / lesson["audio_path"]
                if path.exists():
                    st.audio(path.read_bytes())
        with c3:
            if st.button("Запустить ИИ-анализ", type="primary", use_container_width=True):
                with st.spinner("Анализируем транскрипт..."):
                    try:
                        ok, msg = run_ai_analysis(lesson_id)
                    except Exception as e:
                        logger.exception("Необработанное исключение в run_ai_analysis")
                        ok, msg = False, f"Необработанная ошибка: {e}"
                (st.success if ok else st.error)(msg)
                if not ok:
                    st.code(traceback.format_exc(), language="text")
                st.rerun()
        st.divider()
        tab1, tab2 = st.tabs(["Транскрипт", f"Наблюдения ({len(obs)})"])
        with tab1:
            filter_speaker = st.selectbox("Фильтр по говорящему", ["Все"] + sorted(set(s["speaker_name"] for s in segs)))
            shown = [s for s in segs if filter_speaker == "Все" or s["speaker_name"] == filter_speaker]
            for s in shown:
                st.markdown(
                    f"""<div class="segment"><span class="pill">{fmt_time(s['start_seconds'])} – {fmt_time(s['end_seconds'])}</span>
                    <b style="margin-left:.45rem">{s['speaker_name']}</b>
                    <div style="margin-top:.4rem">{s['text']}</div></div>""",
                    unsafe_allow_html=True
                )
        with tab2:
            if not obs:
                st.info("Наблюдений пока нет. Запустите ИИ-анализ после настройки API-ключа.")
            for o in obs:
                with st.expander(f"{o['category']} · {o['student_name'] or 'Ученик не определён'} · {o['status']}"):
                    st.write(o["description"])
                    if o["evidence"]:
                        st.markdown(f"> {o['evidence']}")
                    st.caption(f"Время: {fmt_time(o['start_seconds'])} – {fmt_time(o['end_seconds'])} · Уверенность: {o['confidence'] if o['confidence'] is not None else '—'}")
                    if o["recommended_action"]:
                        st.markdown(f"**Рекомендация:** {o['recommended_action']}")
                    new_status = st.selectbox(
                        "Статус проверки", ["Требует проверки", "Подтверждено", "Отклонено"],
                        index=["Требует проверки", "Подтверждено", "Отклонено"].index(o["status"]),
                        key=f"status_{o['id']}"
                    )
                    if st.button("Сохранить статус", key=f"save_{o['id']}"):
                        with db() as con:
                            con.execute("UPDATE observations SET status=? WHERE id=?", (new_status, o["id"]))
                        st.success("Статус сохранён.")
                        st.rerun()

elif page == "Ученики":
    st.markdown("## Ученики")
    st.caption("Имена извлекаются из VTT и автоматически добавляются в базу. Новые имена можно проверить здесь.")
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
        st.info("Ученики появятся здесь после импорта первого занятия.")
    else:
        st.dataframe(students_df, use_container_width=True, hide_index=True)
        selected_name = st.selectbox("Карточка ученика", students_df["name"].tolist())
        row = students_df[students_df["name"] == selected_name].iloc[0]
        c1, c2, c3 = st.columns(3)
        c1.metric("Занятий", int(row["lessons_count"]))
        c2.metric("Реплик", int(row["utterances_count"]))
        c3.metric("Наблюдений", int(row["observations_count"]))
        with db() as con:
            history = pd.read_sql_query("""
                SELECT l.lesson_date, l.title, o.category, o.topic, o.description, o.status
                FROM observations o JOIN lessons l ON l.id=o.lesson_id
                JOIN students s ON s.id=o.student_id
                WHERE s.name=? ORDER BY l.lesson_date DESC
            """, con, params=(selected_name,))
        st.markdown("### История наблюдений")
        if history.empty:
            st.info("Для этого ученика пока нет сохранённых наблюдений.")
        else:
            st.dataframe(history, use_container_width=True, hide_index=True)

elif page == "Аналитика":
    st.markdown("## Аналитика")
    st.caption("Статистика рассчитывается по сохранённым наблюдениям. До запуска ИИ-анализа графики могут быть пустыми.")
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
    a, b = st.columns(2)
    with a:
        st.markdown("### По категориям")
        if by_category.empty:
            st.info("Нет данных для графика.")
        else:
            st.bar_chart(by_category.set_index("category"))
    with b:
        st.markdown("### По ученикам")
        if by_student.empty:
            st.info("Нет данных для графика.")
        else:
            st.bar_chart(by_student.set_index("student"))
    st.markdown("### Наблюдения по занятиям")
    if by_lesson.empty:
        st.info("Сначала импортируйте занятие.")
    else:
        st.dataframe(by_lesson, use_container_width=True, hide_index=True)
    st.warning("Частота наблюдений сама по себе не является оценкой уровня языка. Для корректной динамики позже стоит учитывать число заданий/попыток и подтверждённые преподавателем ошибки.")