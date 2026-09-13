"""
SQLite 資料儲存層
---------------------------------
所有學員的簽到、前後測作答、案例作答、闖關紀錄都寫進同一個 SQLite 檔案，
讓「講台上的講師後台」與「學員手機端」讀寫同一份資料，
避免多支手機同時使用 CSV 檔案時互相覆蓋的問題。
"""
import sqlite3
from pathlib import Path
from datetime import datetime
import pandas as pd

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "training.db"


def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL;")  # 提升多連線同時寫入的穩定度
    return conn


def init_db():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS students (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            venue TEXT NOT NULL,
            position TEXT,
            created_at TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS test_answers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            phase TEXT NOT NULL,          -- 'pre' or 'post'
            question_id TEXT NOT NULL,
            selected_index INTEGER,
            is_correct INTEGER,
            created_at TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS case_answers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            chapter_id INTEGER,
            case_id TEXT,
            selected_option TEXT,
            is_correct INTEGER,
            created_at TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS sim_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            node_id TEXT,
            task_id TEXT,
            selected_key TEXT,
            is_correct INTEGER,
            created_at TEXT
        )
    """)
    conn.commit()
    conn.close()


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------- 寫入 ----------
def add_student(name: str, venue: str, position: str) -> int:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO students (name, venue, position, created_at) VALUES (?, ?, ?, ?)",
        (name, venue, position, _now()),
    )
    conn.commit()
    student_id = cur.lastrowid
    conn.close()
    return student_id


def save_test_answer(student_id: int, phase: str, question_id: str, selected_index: int, is_correct: bool):
    conn = get_conn()
    conn.execute(
        "INSERT INTO test_answers (student_id, phase, question_id, selected_index, is_correct, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (student_id, phase, question_id, selected_index, int(is_correct), _now()),
    )
    conn.commit()
    conn.close()


def save_case_answer(student_id: int, chapter_id: int, case_id: str, selected_option: str, is_correct: bool):
    conn = get_conn()
    conn.execute(
        "INSERT INTO case_answers (student_id, chapter_id, case_id, selected_option, is_correct, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (student_id, chapter_id, case_id, selected_option, int(is_correct), _now()),
    )
    conn.commit()
    conn.close()


def save_sim_log(student_id: int, node_id: str, task_id: str, selected_key: str, is_correct: bool):
    conn = get_conn()
    conn.execute(
        "INSERT INTO sim_log (student_id, node_id, task_id, selected_key, is_correct, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (student_id, node_id, task_id, selected_key, int(is_correct), _now()),
    )
    conn.commit()
    conn.close()


# ---------- 查詢（給講師後台儀表板使用） ----------
def get_students_df() -> pd.DataFrame:
    conn = get_conn()
    df = pd.read_sql_query("SELECT * FROM students ORDER BY created_at DESC", conn)
    conn.close()
    return df


def get_test_scores_df() -> pd.DataFrame:
    """每位學員的前測/後測得分（答對題數 * 20 分，滿分100）。"""
    conn = get_conn()
    df = pd.read_sql_query("""
        SELECT s.id AS student_id, s.name, s.venue, s.position,
               ta.phase, SUM(ta.is_correct) AS correct_count,
               COUNT(ta.id) AS total_count
        FROM students s
        JOIN test_answers ta ON ta.student_id = s.id
        GROUP BY s.id, ta.phase
    """, conn)
    conn.close()
    if df.empty:
        return df
    df["score"] = (df["correct_count"] / df["total_count"] * 100).round(1)
    return df


def get_case_answers_df() -> pd.DataFrame:
    conn = get_conn()
    df = pd.read_sql_query("""
        SELECT ca.*, s.name, s.venue
        FROM case_answers ca
        JOIN students s ON s.id = ca.student_id
        ORDER BY ca.created_at DESC
    """, conn)
    conn.close()
    return df


def get_sim_log_df() -> pd.DataFrame:
    conn = get_conn()
    df = pd.read_sql_query("""
        SELECT sl.*, s.name, s.venue
        FROM sim_log sl
        JOIN students s ON s.id = sl.student_id
        ORDER BY sl.created_at DESC
    """, conn)
    conn.close()
    return df


def reset_all_data():
    """危險操作：清空所有作答紀錄與簽到名冊（僅供講師課後或測試時使用）。"""
    conn = get_conn()
    for table in ["students", "test_answers", "case_answers", "sim_log"]:
        conn.execute(f"DELETE FROM {table}")
    conn.commit()
    conn.close()
