"""
資料載入 / 儲存工具
---------------------------------
所有「章節案例」「前後測題目」「場所建議」「闖關劇本」「解鎖控制狀態」
都獨立存放於 data/ 目錄下的 JSON 檔案，程式碼中完全不寫死內容。
講師後台可以透過 st.data_editor 編輯或直接上傳新的 JSON 檔案來即時替換。
"""
import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"

PREPOST_FILE = DATA_DIR / "prepost_test.json"
CASES_FILE = DATA_DIR / "cases_data.json"
VENUE_FILE = DATA_DIR / "venue_advice.json"
SIMULATION_FILE = DATA_DIR / "simulation_data.json"
CONTROL_FILE = DATA_DIR / "control_state.json"


def _load(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"找不到資料檔案：{path}")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save(path: Path, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ---------- 前後測題目 ----------
def get_prepost_questions() -> list:
    return _load(PREPOST_FILE).get("questions", [])


def save_prepost_questions(questions: list) -> None:
    _save(PREPOST_FILE, {"questions": questions})


# ---------- 章節案例 ----------
def get_chapters() -> list:
    return _load(CASES_FILE).get("chapters", [])


def save_chapters(chapters: list) -> None:
    _save(CASES_FILE, {"chapters": chapters})


def get_chapter_by_id(chapter_id: int) -> dict | None:
    for ch in get_chapters():
        if ch["chapter_id"] == chapter_id:
            return ch
    return None


# ---------- 場所建議 ----------
def get_venue_advice() -> dict:
    return _load(VENUE_FILE)


def save_venue_advice(data: dict) -> None:
    _save(VENUE_FILE, data)


def get_venue_categories() -> list:
    """下拉選單使用的場所類別清單（依 venue_advice.json 的 key 順序）。"""
    return list(get_venue_advice().keys())


# ---------- 闖關模擬劇本 ----------
def get_simulation() -> dict:
    return _load(SIMULATION_FILE)


def save_simulation(data: dict) -> None:
    _save(SIMULATION_FILE, data)


# ---------- 全班共用的控制狀態（章節解鎖等） ----------
def get_control_state() -> dict:
    if not CONTROL_FILE.exists():
        default = {"unlocked_chapter": 1, "course_title": "防火管理訓練互動教學", "notice": "", "current_class": ""}
        _save(CONTROL_FILE, default)
        return default
    return _load(CONTROL_FILE)


def save_control_state(data: dict) -> None:
    _save(CONTROL_FILE, data)


def raw_json_text(which: str) -> str:
    """回傳原始 JSON 檔案文字內容，供後台「下載目前題庫」使用。"""
    mapping = {
        "prepost": PREPOST_FILE,
        "cases": CASES_FILE,
        "venue": VENUE_FILE,
        "simulation": SIMULATION_FILE,
    }
    path = mapping[which]
    return path.read_text(encoding="utf-8")


def overwrite_from_upload(which: str, uploaded_bytes: bytes) -> None:
    """講師上傳自訂 JSON 檔案，直接整份覆蓋置換（會先驗證是合法 JSON）。"""
    mapping = {
        "prepost": PREPOST_FILE,
        "cases": CASES_FILE,
        "venue": VENUE_FILE,
        "simulation": SIMULATION_FILE,
    }
    path = mapping[which]
    data = json.loads(uploaded_bytes.decode("utf-8"))  # 驗證格式
    _save(path, data)
