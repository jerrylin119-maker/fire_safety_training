"""冷啟動時資料庫連線必須活下來，而且不能弄丟既有資料。"""
import os
import shutil
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from utils import db


class DbStartupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db_path = self.root / "data" / "training.db"
        self.fallback = self.root / "fallback"
        self._orig_db = db.DB_PATH
        self._orig_fallback = db.FALLBACK_DIR
        db.DB_PATH = self.db_path
        db.FALLBACK_DIR = self.fallback
        db._reset_runtime_state()

    def tearDown(self):
        db.DB_PATH = self._orig_db
        db.FALLBACK_DIR = self._orig_fallback
        db._reset_runtime_state()
        self.tmp.cleanup()

    def _create_students(self, path: Path, names: list[str]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE students (name TEXT)")
        conn.executemany("INSERT INTO students (name) VALUES (?)", [(n,) for n in names])
        conn.commit()
        conn.close()

    def test_wal_pragma_ignores_busy_timeout_but_get_conn_waits(self):
        """資料庫還在 rollback 模式時，journal_mode=WAL 被寫入鎖擋住會立刻失敗。

        sqlite3 的 timeout 等不到這一行。冷啟動的另一個連線只要還握著寫入交易，
        舊的 get_conn() 就會整頁當掉。重試必須等到鎖放開，而且兩筆資料都在。
        """
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        holder = sqlite3.connect(self.db_path, check_same_thread=False, timeout=30)
        holder.execute("CREATE TABLE students (name TEXT)")
        holder.execute("INSERT INTO students (name) VALUES ('原有')")
        holder.commit()
        holder.execute("BEGIN IMMEDIATE")
        holder.execute("INSERT INTO students (name) VALUES ('鎖定中')")

        rushed = sqlite3.connect(self.db_path, timeout=2)
        rushed.execute("PRAGMA busy_timeout=2000;")
        with self.assertRaises(sqlite3.OperationalError) as caught:
            rushed.execute("PRAGMA journal_mode=WAL;")
        self.assertIn("locked", str(caught.exception).lower())
        rushed.close()

        def release():
            holder.commit()
            holder.close()

        timer = threading.Timer(0.4, release)
        timer.start()
        try:
            conn = db.get_conn()
            try:
                names = [row[0] for row in conn.execute("SELECT name FROM students ORDER BY rowid")]
            finally:
                conn.close()
        finally:
            timer.join(2)
        self.assertEqual(names, ["原有", "鎖定中"])
        self.assertTrue(self.db_path.exists())

    def test_cold_start_stampede_keeps_every_signup(self):
        errors = []
        saved = []

        def worker(index):
            try:
                db.init_db()
                student_id = db.add_student(f"學員{index}", "旅宿業", "管理員", "2026-10-30")
                saved.append(student_id)
            except Exception as exc:  # pragma: no cover - 失敗時才會進來
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(12)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        self.assertEqual(errors, [])
        self.assertEqual(len(saved), 12)
        conn = db.get_conn()
        try:
            count = conn.execute("SELECT COUNT(*) FROM students").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(count, 12)
        db.init_db()
        self.assertEqual(count, 12)
        self.assertIsNotNone(db.get_student(saved[0]))

    def test_disk_io_falls_back_to_delete_without_deleting_wal(self):
        self._create_students(self.db_path, ["林小明"])
        shm = Path(str(self.db_path) + "-shm")
        wal = Path(str(self.db_path) + "-wal")
        shm.write_bytes(b"stale-index")
        wal.write_bytes(b"must-not-be-deleted-by-startup")
        # 假的 -wal 不能留到 sqlite 打開，否則會把資料庫判壞。先確認清理函式只刪 -shm。
        db._drop_shm_index(self.db_path)
        self.assertFalse(shm.exists())
        self.assertEqual(wal.read_bytes(), b"must-not-be-deleted-by-startup")
        wal.unlink()

        real_set = db._set_journal_mode
        wal_attempts = []

        def fail_wal(conn, mode):
            if mode == "WAL":
                wal_attempts.append(mode)
                raise sqlite3.OperationalError("disk I/O error")
            return real_set(conn, mode)

        with mock.patch("utils.db._set_journal_mode", fail_wal):
            conn = db.get_conn()
            try:
                names = [row[0] for row in conn.execute("SELECT name FROM students")]
                mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
            finally:
                conn.close()
            again = db.get_conn()
            again.close()

        self.assertEqual(names, ["林小明"])
        self.assertEqual(mode, "delete")
        self.assertTrue(self.db_path.exists())
        self.assertFalse(wal.exists())
        self.assertGreaterEqual(len(wal_attempts), 1)
        # 同一個行程記住檔案系統不支援 WAL，下一條連線不再下會撞獨佔鎖的切換指令。
        self.assertEqual(len(wal_attempts), 2)
        self.assertEqual(db._journal_choice, "delete")

    def test_silent_wal_refusal_is_remembered(self):
        """有些檔案系統不丟錯，只是把 journal_mode=WAL 答成 delete。

        若每次連線都再試一次，寫入當中還是會撞上那個不遵守 timeout 的獨佔鎖。
        """
        self._create_students(self.db_path, ["林小明"])
        real_set = db._set_journal_mode
        attempts = []

        def pretend_unsupported(conn, mode):
            attempts.append(mode)
            if mode == "WAL":
                return "delete"
            return real_set(conn, mode)

        with mock.patch("utils.db._set_journal_mode", pretend_unsupported):
            first = db.get_conn()
            try:
                self.assertEqual(
                    [row[0] for row in first.execute("SELECT name FROM students")],
                    ["林小明"],
                )
            finally:
                first.close()
            second = db.get_conn()
            second.close()
        self.assertEqual(attempts, ["WAL"])
        self.assertEqual(db._journal_choice, "delete")

    def test_uncheckpointed_wal_rows_survive_open(self):
        src_dir = self.root / "src"
        src_dir.mkdir()
        src = src_dir / "training.db"
        writer = sqlite3.connect(src, check_same_thread=False)
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("CREATE TABLE students (name TEXT)")
        writer.execute("INSERT INTO students (name) VALUES ('主檔')")
        writer.commit()
        writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        writer.execute("INSERT INTO students (name) VALUES ('只在WAL')")
        writer.commit()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        for entry in src_dir.iterdir():
            shutil.copy2(entry, self.db_path.parent / entry.name)
        writer.close()
        shm = Path(str(self.db_path) + "-shm")
        if shm.exists():
            shm.unlink()

        conn = db.get_conn()
        try:
            names = [row[0] for row in conn.execute("SELECT name FROM students ORDER BY rowid")]
        finally:
            conn.close()
        self.assertEqual(names, ["主檔", "只在WAL"])
        untouched = sqlite3.connect(src)
        try:
            original = [row[0] for row in untouched.execute("SELECT name FROM students ORDER BY rowid")]
        finally:
            untouched.close()
        self.assertEqual(original, ["主檔", "只在WAL"])

    def test_readonly_directory_copies_existing_rows_and_keeps_original(self):
        self._create_students(self.db_path, ["林小明"])
        original_bytes = self.db_path.read_bytes()
        os.chmod(self.db_path, 0o444)
        os.chmod(self.db_path.parent, 0o555)
        try:
            conn = db.get_conn()
            try:
                names = [row[0] for row in conn.execute("SELECT name FROM students")]
                conn.execute("INSERT INTO students (name) VALUES ('新簽到')")
                conn.commit()
            finally:
                conn.close()
            self.assertEqual(names, ["林小明"])
            self.assertEqual(self.db_path.read_bytes(), original_bytes)
            self.assertEqual(db._active_db_path, self.fallback / "training.db")
            again = db.get_conn()
            try:
                names = [row[0] for row in again.execute("SELECT name FROM students ORDER BY rowid")]
            finally:
                again.close()
            self.assertEqual(names, ["林小明", "新簽到"])
        finally:
            os.chmod(self.db_path.parent, 0o755)
            os.chmod(self.db_path, 0o644)

    def test_snapshot_file_copy_keeps_wal_frames_when_backup_api_cannot_open(self):
        src_dir = self.root / "live"
        src_dir.mkdir()
        src = src_dir / "training.db"
        writer = sqlite3.connect(src, check_same_thread=False)
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("CREATE TABLE students (name TEXT)")
        writer.execute("INSERT INTO students (name) VALUES ('主檔')")
        writer.commit()
        writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        writer.execute("INSERT INTO students (name) VALUES ('只在WAL')")
        writer.commit()
        dest = self.root / "copied" / "training.db"
        real_connect = sqlite3.connect

        def fail_ro(database, *args, **kwargs):
            if kwargs.get("uri") and isinstance(database, str) and "mode=ro" in database:
                raise sqlite3.OperationalError("unable to open database file")
            return real_connect(database, *args, **kwargs)

        with mock.patch("utils.db.sqlite3.connect", fail_ro):
            db._snapshot_database(src, dest)
        writer.close()

        conn = sqlite3.connect(dest)
        try:
            names = [row[0] for row in conn.execute("SELECT name FROM students ORDER BY rowid")]
        finally:
            conn.close()
        self.assertEqual(names, ["主檔", "只在WAL"])
        self.assertTrue(src.exists())


if __name__ == "__main__":
    unittest.main()
