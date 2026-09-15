"""
SQLite 資料儲存層
---------------------------------
所有學員的簽到、前後測作答、章節內容（案例/觀念題）作答、闖關紀錄都寫進同一個
SQLite 檔案，讓「講台上的講師後台」與「學員手機端」讀寫同一份資料，避免多支手機
同時使用 CSV 檔案時互相覆蓋的問題。

`progress` 表額外保存每位學員「目前進度到哪裡」的完整快照，讓學員中途斷線、
關閉分頁、或切換裝置時，可以憑「簽到代碼」（其實就是 student_id）接續作答，
不必從頭重來一次。
"""
import json
import sqlite3
from pathlib import Path
from datetime import datetime
import pandas as pd

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "training.db"


def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL;")  # 提升多連線同時寫入的穩定度
    return conn


def _ensure_column(conn, table: str, column: str, coltype: str):
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")


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
            created_at TEXT,
            item_type TEXT DEFAULT 'case'   -- 'case'（情境案例）或 'quiz'（章節觀念題）
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
    cur.execute("""
        CREATE TABLE IF NOT EXISTS progress (
            student_id INTEGER PRIMARY KEY,
            stage TEXT,
            case_ptr INTEGER DEFAULT 0,
            sim_node TEXT,
            sim_task_idx INTEGER DEFAULT 0,
            sim_ending TEXT,
            pretest_score REAL,
            posttest_score REAL,
            pretest_answers TEXT,
            posttest_answers TEXT,
            updated_at TEXT
        )
    """)
    # 相容舊資料庫：如果是舊版本建立的 case_answers 表（沒有 item_type 欄位），補上。
    _ensure_column(conn, "case_answers", "item_type", "TEXT DEFAULT 'case'")
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


def get_student(student_id) -> dict | None:
    """依「簽到代碼」（student_id）查詢學員基本資料，找不到回傳 None。"""
    try:
        student_id = int(student_id)
    except (TypeError, ValueError):
        return None
    conn = get_conn()
    row = conn.execute(
        "SELECT id, name, venue, position FROM students WHERE id = ?", (student_id,)
    ).fetchone()
    conn.close()
    if row is None:
        return None
    return {"id": row[0], "name": row[1], "venue": row[2], "position": row[3]}


def save_test_answer(student_id: int, phase: str, question_id: str, selected_index: int, is_correct: bool):
    conn = get_conn()
    conn.execute(
        "INSERT INTO test_answers (student_id, phase, question_id, selected_index, is_correct, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (student_id, phase, question_id, selected_index, int(is_correct), _now()),
    )
    conn.commit()
    conn.close()


def save_case_answer(student_id: int, chapter_id: int, case_id: str, selected_option: str, is_correct: bool,
                      item_type: str = "case"):
    conn = get_conn()
    conn.execute(
        "INSERT INTO case_answers (student_id, chapter_id, case_id, selected_option, is_correct, created_at, item_type) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (student_id, chapter_id, case_id, selected_option, int(is_correct), _now(), item_type),
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


def save_progress(student_id: int, data: dict):
    """把學員目前的完整進度快照寫入（覆蓋式），供之後接續使用。"""
    conn = get_conn()
    conn.execute("""
        INSERT INTO progress (student_id, stage, case_ptr, sim_node, sim_task_idx, sim_ending,
                               pretest_score, posttest_score, pretest_answers, posttest_answers, updated_at)
        VALUES (:student_id, :stage, :case_ptr, :sim_node, :sim_task_idx, :sim_ending,
                :pretest_score, :posttest_score, :pretest_answers, :posttest_answers, :updated_at)
        ON CONFLICT(student_id) DO UPDATE SET
            stage=excluded.stage, case_ptr=excluded.case_ptr, sim_node=excluded.sim_node,
            sim_task_idx=excluded.sim_task_idx, sim_ending=excluded.sim_ending,
            pretest_score=excluded.pretest_score, posttest_score=excluded.posttest_score,
            pretest_answers=excluded.pretest_answers, posttest_answers=excluded.posttest_answers,
            updated_at=excluded.updated_at
    """, {
        "student_id": student_id,
        "stage": data.get("stage"),
        "case_ptr": data.get("case_ptr", 0),
        "sim_node": data.get("sim_node"),
        "sim_task_idx": data.get("sim_task_idx", 0),
        "sim_ending": data.get("sim_ending"),
        "pretest_score": data.get("pretest_score"),
        "posttest_score": data.get("posttest_score"),
        "pretest_answers": json.dumps(data.get("pretest_answers") or {}, ensure_ascii=False),
        "posttest_answers": json.dumps(data.get("posttest_answers") or {}, ensure_ascii=False),
        "updated_at": _now(),
    })
    conn.commit()
    conn.close()


def load_progress(student_id) -> dict | None:
    conn = get_conn()
    row = conn.execute(
        "SELECT stage, case_ptr, sim_node, sim_task_idx, sim_ending, pretest_score, posttest_score, "
        "pretest_answers, posttest_answers FROM progress WHERE student_id = ?",
        (student_id,),
    ).fetchone()
    conn.close()
    if row is None:
        return None
    return {
        "stage": row[0],
        "case_ptr": row[1] or 0,
        "sim_node": row[2],
        "sim_task_idx": row[3] or 0,
        "sim_ending": row[4],
        "pretest_score": row[5],
        "posttest_score": row[6],
        "pretest_answers": json.loads(row[7]) if row[7] else {},
        "posttest_answers": json.loads(row[8]) if row[8] else {},
    }


# ---------- 查詢（給講師後台儀表板使用） ----------
def get_students_df() -> pd.DataFrame:
    conn = get_conn()
    df = pd.read_sql_query("SELECT * FROM students ORDER BY created_at DESC", conn)
    conn.close()
    return df


def get_test_scores_df() -> pd.DataFrame:
    """每位學員的前測/後測得分（答對題數 / 總題數 * 100）。"""
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


def get_content_accuracy(student_id: int) -> dict:
    """該學員在章節內容（案例＋觀念題）的正確率。"""
    conn = get_conn()
    row = conn.execute(
        "SELECT SUM(is_correct), COUNT(*) FROM case_answers WHERE student_id = ?",
        (student_id,),
    ).fetchone()
    conn.close()
    correct, total = (row[0] or 0), (row[1] or 0)
    return {"correct": correct, "total": total, "pct": round(correct / total * 100, 1) if total else None}


def get_sim_accuracy(student_id: int) -> dict:
    """該學員在闖關模擬的正確率。"""
    conn = get_conn()
    row = conn.execute(
        "SELECT SUM(is_correct), COUNT(*) FROM sim_log WHERE student_id = ?",
        (student_id,),
    ).fetchone()
    conn.close()
    correct, total = (row[0] or 0), (row[1] or 0)
    return {"correct": correct, "total": total, "pct": round(correct / total * 100, 1) if total else None}


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
    for table in ["students", "test_answers", "case_answers", "sim_log", "progress"]:
        conn.execute(f"DELETE FROM {table}")
    conn.commit()
    conn.close()
