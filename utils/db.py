"""
SQLite 資料儲存層 (v3 - 含班別、滿意度調查)
---------------------------------
所有學員的簽到、前後測作答、章節內容（案例/觀念題）作答、闖關紀錄都寫進同一個
SQLite 檔案，讓「講台上的講師後台」與「學員手機端」讀寫同一份資料，避免多支手機
同時使用 CSV 檔案時互相覆蓋的問題。

`progress` 表額外保存每位學員「目前進度到哪裡」的完整快照，讓學員中途斷線、
關閉分頁、或切換裝置時，可以憑「簽到代碼」（其實就是 student_id）接續作答，
不必從頭重來一次。

`students.class_id` 用來區分「班別」（每次開課一個班別，預設用日期命名）。
每位學員簽到當下，會被標記為目前講師在後台設定的「目前班別」，讓講師可以
針對單一班別查看儀表板、打包匯出、或課後清空重來。

連線注意事項（Streamlit Community Cloud 冷啟動）
------------------------------------------------
`training.db` 不進 git。容器從休眠醒來時檔案通常還不存在，喚醒後的第一波
請求會同時跑 `init_db()`。資料庫還在預設的 rollback 模式時，
`PRAGMA journal_mode=WAL` 需要獨佔鎖，而且**不會**遵守 `sqlite3.connect` 的
timeout / `busy_timeout`，會立刻拋出 ``database is locked``，整頁就當在這一行。

因此 `get_conn()` 會：

* 已經是 WAL 就不再下切換指令（切換才是會撞鎖的那一步）。
* 還不是 WAL 時，遇到 locked 就重試，等另一個連線把模式切完。
* 檔案系統不支援 WAL（``disk I/O error`` 等）就改用 DELETE。只會清掉可重建的
  ``-shm`` 索引，不會刪 ``training.db`` 或 ``-wal``（未 checkpoint 的資料在 -wal 裡）。
* 路徑或檔案無法寫入時，用 SQLite backup 把既有內容複製到可寫目錄再打開，
  原始檔留在原地。
"""
import json
import logging
import os
import shutil
import sqlite3
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows 沒有 fcntl
    fcntl = None

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "training.db"
# 專案目錄不能寫時的退路。可在測試裡改掉。
FALLBACK_DIR = Path.home() / ".fire_safety_training"

_LOCK_RETRY_SECONDS = 5.0
_thread_lock = threading.Lock()
_process_lock_fd = None
# 目前這個行程實際打開的檔案。None 代表沿用 DB_PATH。
_active_db_path: Path | None = None
# "wal" 或 "delete"。避免每次連線都對一個不支援 WAL 的檔案再下一次獨佔切換。
_journal_choice: str | None = None
_fallback_logged = False


def _reset_runtime_state() -> None:
    """測試用：忘掉上一個資料庫路徑的執行期狀態。"""
    global _active_db_path, _journal_choice, _fallback_logged
    _active_db_path = None
    _journal_choice = None
    _fallback_logged = False


def _lock_file_path() -> Path:
    override = os.environ.get("FIRE_SAFETY_DB_LOCK")
    if override:
        return Path(override)
    return Path(tempfile.gettempdir()) / "fire_safety_training_db.lock"


def _acquire_process_lock() -> None:
    """跨行程互斥。必須在 _thread_lock 裡面呼叫，同一個 fd 才不會自己把自己鎖死。"""
    global _process_lock_fd
    if fcntl is None:
        return
    if _process_lock_fd is None:
        path = _lock_file_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        _process_lock_fd = open(path, "a+")
    fcntl.flock(_process_lock_fd.fileno(), fcntl.LOCK_EX)


def _release_process_lock() -> None:
    if fcntl is None or _process_lock_fd is None:
        return
    fcntl.flock(_process_lock_fd.fileno(), fcntl.LOCK_UN)


def _is_locked(exc: BaseException) -> bool:
    message = str(exc).lower()
    # 「locking protocol」是 shm 壞掉，不是暫時的寫入鎖，不能靠重試耗掉 5 秒。
    return "database is locked" in message or "database table is locked" in message


def _is_io_error(exc: BaseException) -> bool:
    message = str(exc).lower()
    return "disk i/o" in message or "locking protocol" in message


def _is_readonly(exc: BaseException) -> bool:
    message = str(exc).lower()
    return (
        "readonly" in message
        or "read-only" in message
        or "unable to open database file" in message
    )


def _location_writable(path: Path) -> bool:
    parent = path.parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
        probe = parent / f".write_probe_{os.getpid()}"
        with open(probe, "wb") as handle:
            handle.write(b"1")
        probe.unlink()
    except OSError:
        return False
    if path.exists() and not os.access(path, os.W_OK):
        return False
    return True


def _sqlite_uri(path: Path, query: str) -> str:
    return f"{path.resolve().as_uri()}?{query}"


def _quick_ok(path: Path) -> bool:
    if not path.exists() or path.stat().st_size == 0:
        return False
    try:
        conn = sqlite3.connect(_sqlite_uri(path, "mode=ro"), uri=True, timeout=30)
    except sqlite3.Error:
        return False
    try:
        row = conn.execute("PRAGMA quick_check").fetchone()
    except sqlite3.Error:
        return False
    finally:
        conn.close()
    return bool(row) and row[0] == "ok"


def _install_snapshot(tmp: Path, dest: Path) -> None:
    """把暫存複本換成正式檔。backup API 產生的單一檔要清掉目標上舊的 -wal/-shm，
    否則舊的 wal 會被套到新檔上。檔案複製路徑則會一併搬走 -wal/-shm。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    os.replace(tmp, dest)
    for suffix in ("-wal", "-shm"):
        src_side = Path(str(tmp) + suffix)
        dst_side = Path(str(dest) + suffix)
        if src_side.exists():
            os.replace(src_side, dst_side)
        elif dst_side.exists():
            dst_side.unlink()


def _snapshot_database(src: Path, dest: Path) -> None:
    """把 src 的已提交內容複製到 dest。不刪除、不修改 src。"""
    if not src.exists():
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".partial")
    if tmp.exists():
        tmp.unlink()
    for suffix in ("-wal", "-shm"):
        side = Path(str(tmp) + suffix)
        if side.exists():
            side.unlink()

    try:
        src_conn = sqlite3.connect(_sqlite_uri(src, "mode=ro"), uri=True, timeout=30)
    except sqlite3.OperationalError:
        shutil.copy2(src, tmp)
        for suffix in ("-wal", "-shm"):
            side = Path(str(src) + suffix)
            if side.exists():
                shutil.copy2(side, Path(str(tmp) + suffix))
    else:
        try:
            dst_conn = sqlite3.connect(tmp)
            try:
                src_conn.backup(dst_conn)
                dst_conn.commit()
            finally:
                dst_conn.close()
        finally:
            src_conn.close()

    check = sqlite3.connect(tmp)
    try:
        row = check.execute("PRAGMA quick_check").fetchone()
    finally:
        check.close()
    if not row or row[0] != "ok":
        tmp.unlink(missing_ok=True)
        raise sqlite3.DatabaseError(f"資料庫複本完整性檢查失敗：{row}")
    _install_snapshot(tmp, dest)


def _relocate(src: Path) -> Path:
    """改用可寫目錄。若退路複本比來源新且完整，沿用複本，避免行程重啟把後來的寫入蓋掉。"""
    global _active_db_path, _journal_choice, _fallback_logged
    _journal_choice = None
    dest = FALLBACK_DIR / "training.db"
    if src.exists() and dest.exists() and dest.resolve() == src.resolve():
        raise sqlite3.OperationalError(f"資料庫路徑無法寫入：{src}")
    FALLBACK_DIR.mkdir(parents=True, exist_ok=True)
    if src.exists():
        src_mtime = src.stat().st_mtime
        if dest.exists() and dest.stat().st_mtime >= src_mtime and _quick_ok(dest):
            if not _fallback_logged:
                logger.warning("資料庫路徑無法寫入，沿用較新的複本：%s", dest)
                _fallback_logged = True
        else:
            _snapshot_database(src, dest)
            if not _fallback_logged:
                logger.warning(
                    "資料庫路徑無法寫入，已將既有資料複製到 %s（原始檔保留在 %s）",
                    dest,
                    src,
                )
                _fallback_logged = True
    elif not _fallback_logged:
        logger.warning("資料庫路徑無法寫入，改把新資料庫建立在 %s", dest)
        _fallback_logged = True
    _active_db_path = dest
    return dest


def _db_file() -> Path:
    if _active_db_path is not None:
        return _active_db_path
    if _location_writable(DB_PATH):
        return DB_PATH
    return _relocate(DB_PATH)


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30, check_same_thread=False)
    conn.execute("PRAGMA busy_timeout=30000;")
    return conn


def _read_journal_mode(conn: sqlite3.Connection) -> str:
    row = conn.execute("PRAGMA journal_mode;").fetchone()
    return (row[0] if row else "").lower()


def _set_journal_mode(conn: sqlite3.Connection, mode: str) -> str:
    """切換 journal mode。locked 時 SQLite 不會自己等，所以這裡重試。"""
    delay = 0.02
    deadline = time.monotonic() + _LOCK_RETRY_SECONDS
    while True:
        try:
            row = conn.execute(f"PRAGMA journal_mode={mode};").fetchone()
            return (row[0] if row else mode).lower()
        except sqlite3.OperationalError as exc:
            if not _is_locked(exc) or time.monotonic() >= deadline:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 0.25)


def _drop_shm_index(path: Path) -> None:
    """-shm 只是 WAL 索引，遺失時 SQLite 會用 -wal 重建。不能刪 -wal，否則未 checkpoint 的提交會不見。"""
    shm = Path(str(path) + "-shm")
    if not shm.exists():
        return
    try:
        shm.unlink()
    except OSError:
        logger.warning("無法移除 %s", shm, exc_info=True)


def _log_wal_fallback(reason: BaseException, actual: str, path: Path) -> None:
    global _fallback_logged
    if _fallback_logged:
        return
    logger.warning(
        "此檔案系統無法使用 SQLite WAL（%s），已改用 %s journal。資料檔仍是 %s",
        reason,
        actual,
        path,
    )
    _fallback_logged = True


def _configure_existing(path: Path) -> sqlite3.Connection:
    """打開 path 並設好 journal mode。失敗時會關掉自己開的連線再拋出。"""
    global _fallback_logged, _journal_choice
    conn = _connect(path)
    try:
        current = _read_journal_mode(conn)
        if _journal_choice == "wal" and current == "wal":
            return conn
        if _journal_choice == "delete" and current != "wal":
            return conn
        if current == "wal":
            _journal_choice = "wal"
            return conn
        try:
            actual = _set_journal_mode(conn, "WAL")
        except sqlite3.OperationalError as exc:
            if not _is_io_error(exc):
                raise
            conn.close()
            conn = None
            _drop_shm_index(path)
            conn = _connect(path)
            try:
                actual = _set_journal_mode(conn, "WAL")
            except sqlite3.OperationalError as retry_exc:
                if not _is_io_error(retry_exc):
                    raise
                conn.close()
                conn = None
                conn = _connect(path)
                actual = _set_journal_mode(conn, "DELETE")
                _journal_choice = "delete"
                _log_wal_fallback(retry_exc, actual, path)
        if actual == "wal":
            _journal_choice = "wal"
        else:
            _journal_choice = "delete"
            if not _fallback_logged:
                logger.warning("要求 journal_mode=WAL，實際為 %s（資料檔 %s）", actual, path)
                _fallback_logged = True
        return conn
    except Exception:
        if conn is not None:
            conn.close()
        raise


def _open_configured(path: Path) -> sqlite3.Connection:
    try:
        return _configure_existing(path)
    except sqlite3.OperationalError as exc:
        if not _is_readonly(exc):
            raise
        logger.warning("資料庫無法寫入（%s），改開啟複本", exc)
        return _configure_existing(_relocate(path))


def get_conn():
    """打開訓練資料庫。多個 Streamlit session 同時冷啟動時也不會被 WAL 切換的瞬間鎖打倒。"""
    with _thread_lock:
        _acquire_process_lock()
        try:
            return _open_configured(_db_file())
        finally:
            _release_process_lock()


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
            created_at TEXT,
            class_id TEXT
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
    cur.execute("""
        CREATE TABLE IF NOT EXISTS survey_responses (
            student_id INTEGER PRIMARY KEY,
            answers TEXT,          -- JSON：{題目id: 1~5 分}
            suggestion TEXT,       -- 建議事項（自由填寫）
            created_at TEXT
        )
    """)
    # 相容舊資料庫：補上後來才新增的欄位。
    _ensure_column(conn, "case_answers", "item_type", "TEXT DEFAULT 'case'")
    _ensure_column(conn, "students", "class_id", "TEXT")
    conn.commit()
    conn.close()


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------- 寫入 ----------
def add_student(name: str, venue: str, position: str, class_id: str = "") -> int:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO students (name, venue, position, created_at, class_id) VALUES (?, ?, ?, ?, ?)",
        (name, venue, position, _now(), class_id),
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
        "SELECT id, name, venue, position, class_id FROM students WHERE id = ?", (student_id,)
    ).fetchone()
    conn.close()
    if row is None:
        return None
    return {"id": row[0], "name": row[1], "venue": row[2], "position": row[3], "class_id": row[4] or ""}


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


def save_survey_response(student_id: int, answers: dict, suggestion: str):
    """儲存（或覆蓋）該學員的課後滿意度調查回覆。"""
    conn = get_conn()
    conn.execute(
        "INSERT INTO survey_responses (student_id, answers, suggestion, created_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(student_id) DO UPDATE SET answers=excluded.answers, suggestion=excluded.suggestion, "
        "created_at=excluded.created_at",
        (student_id, json.dumps(answers, ensure_ascii=False), suggestion, _now()),
    )
    conn.commit()
    conn.close()


def has_survey_response(student_id: int) -> bool:
    conn = get_conn()
    row = conn.execute("SELECT 1 FROM survey_responses WHERE student_id = ?", (student_id,)).fetchone()
    conn.close()
    return row is not None


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


# ---------- 查詢（給講師後台儀表板使用；class_id=None 代表不篩選、查全部） ----------
def get_class_list() -> pd.DataFrame:
    """列出資料庫裡目前有哪些班別、各自的人數與簽到時間範圍（新到舊排序）。"""
    conn = get_conn()
    df = pd.read_sql_query("""
        SELECT COALESCE(NULLIF(class_id, ''), '（未分班）') AS class_id,
               COUNT(*) AS student_count,
               MIN(created_at) AS first_signup,
               MAX(created_at) AS last_signup
        FROM students
        GROUP BY class_id
        ORDER BY last_signup DESC
    """, conn)
    conn.close()
    return df


def get_students_df(class_id: str | None = None) -> pd.DataFrame:
    conn = get_conn()
    if class_id:
        df = pd.read_sql_query(
            "SELECT * FROM students WHERE class_id = ? ORDER BY created_at DESC", conn, params=(class_id,)
        )
    else:
        df = pd.read_sql_query("SELECT * FROM students ORDER BY created_at DESC", conn)
    conn.close()
    return df


def get_test_scores_df(class_id: str | None = None) -> pd.DataFrame:
    """每位學員的前測/後測得分（答對題數 / 總題數 * 100）。"""
    conn = get_conn()
    sql = """
        SELECT s.id AS student_id, s.name, s.venue, s.position,
               ta.phase, SUM(ta.is_correct) AS correct_count,
               COUNT(ta.id) AS total_count
        FROM students s
        JOIN test_answers ta ON ta.student_id = s.id
        {where}
        GROUP BY s.id, ta.phase
    """
    if class_id:
        df = pd.read_sql_query(sql.format(where="WHERE s.class_id = ?"), conn, params=(class_id,))
    else:
        df = pd.read_sql_query(sql.format(where=""), conn)
    conn.close()
    if df.empty:
        return df
    df["score"] = (df["correct_count"] / df["total_count"] * 100).round(1)
    return df


def get_case_answers_df(class_id: str | None = None) -> pd.DataFrame:
    conn = get_conn()
    sql = """
        SELECT ca.*, s.name, s.venue
        FROM case_answers ca
        JOIN students s ON s.id = ca.student_id
        {where}
        ORDER BY ca.created_at DESC
    """
    if class_id:
        df = pd.read_sql_query(sql.format(where="WHERE s.class_id = ?"), conn, params=(class_id,))
    else:
        df = pd.read_sql_query(sql.format(where=""), conn)
    conn.close()
    return df


def get_sim_log_df(class_id: str | None = None) -> pd.DataFrame:
    conn = get_conn()
    sql = """
        SELECT sl.*, s.name, s.venue
        FROM sim_log sl
        JOIN students s ON s.id = sl.student_id
        {where}
        ORDER BY sl.created_at DESC
    """
    if class_id:
        df = pd.read_sql_query(sql.format(where="WHERE s.class_id = ?"), conn, params=(class_id,))
    else:
        df = pd.read_sql_query(sql.format(where=""), conn)
    conn.close()
    return df


def get_survey_df(class_id: str | None = None) -> pd.DataFrame:
    """滿意度調查原始回覆（answers 為 JSON 字串），可依班別篩選。"""
    conn = get_conn()
    sql = """
        SELECT sr.student_id, s.name, s.venue, s.position, s.class_id,
               sr.created_at, sr.answers, sr.suggestion
        FROM survey_responses sr
        JOIN students s ON s.id = sr.student_id
        {where}
        ORDER BY sr.created_at DESC
    """
    if class_id:
        df = pd.read_sql_query(sql.format(where="WHERE s.class_id = ?"), conn, params=(class_id,))
    else:
        df = pd.read_sql_query(sql.format(where=""), conn)
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


def reset_all_data():
    """危險操作：清空所有作答紀錄與簽到名冊（課後打包完畢、準備開下一班時使用）。"""
    conn = get_conn()
    for table in ["students", "test_answers", "case_answers", "sim_log", "progress", "survey_responses"]:
        conn.execute(f"DELETE FROM {table}")
    conn.commit()
    conn.close()
