"""P型火警受信總機模擬器。

靜態檔在 ``static/fire_panel/``（``index.html`` + ``alarm.mp3``）。
手機請直接開靜態頁，不要先進入 Streamlit：Community Cloud 會把 App
掛在 ``/~/+/``，根目錄的 ``/app/static/`` 只會得到外層載入頁，iframe
裡會再跑一次 Streamlit，手機就會一直轉圈。

未來自衛編組闖關可以直接嵌入，不必再做一個頁面：

    from utils.fire_panel import render_fire_panel
    render_fire_panel(height=960)

獨立頁面（學員公開、不需講師密碼）是
``pages/2_🚨_火警受信總機模擬器.py``。檔名決定側欄標題與網址路徑
``/火警受信總機模擬器``，改檔名時要一併改 ``PAGE_SCRIPT``。
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs, urlparse

import streamlit as st

# 與 pages/ 檔名對應。Streamlit 會去掉排序數字與開頭 emoji，當作網址路徑。
PAGE_URL_PATH = "火警受信總機模擬器"
PAGE_SCRIPT = "pages/2_🚨_火警受信總機模擬器.py"
# 給 QR code 用的 ASCII 參數：/?page=fire-panel 會轉到上面那個頁面。
QUERY_VALUE = "fire-panel"
# Community Cloud 的瀏覽器路徑前綴。App 實際掛在這裡，不是網域根目錄。
_CLOUD_MOUNT = "/~/+"
_STATIC_PATH = "/app/static/fire_panel/index.html"

_STATIC_DIR = Path(__file__).resolve().parent.parent / "static" / "fire_panel"

# Streamlit 用 flex: 0 0 <height> 鎖死 iframe 外框，只改 height 不會縮。
# 這裡把外框改成吃滿視窗，模擬器自己的內容（含手機分頁）在 iframe 裡捲動。
_VIEWPORT_CSS = """
<style>
  [data-testid="stMain"] {
    overflow: hidden !important;
  }
  [data-testid="stMainBlockContainer"] {
    height: 100% !important;
    max-height: 100dvh !important;
    overflow: hidden !important;
    padding-top: 3.4rem !important;
    padding-bottom: 0.35rem !important;
    padding-left: 0.45rem !important;
    padding-right: 0.45rem !important;
    max-width: 100% !important;
  }
  [data-testid="stMainBlockContainer"] > [data-testid="stVerticalBlock"] {
    height: 100% !important;
    min-height: 0 !important;
    gap: 0 !important;
  }
  [data-testid="stElementContainer"]:has(iframe[data-testid="stIFrame"]) {
    flex: 1 1 auto !important;
    height: auto !important;
    min-height: 0 !important;
  }
  iframe[data-testid="stIFrame"] {
    height: 100% !important;
    min-height: 0 !important;
  }
</style>
"""


def _public_origin_and_host() -> tuple[str, str]:
    """學員瀏覽器上的來源與主機名。

    ``st.context.url`` 在 Cloud 上是最外層網址（不含 ``/~/+/``），
    用來判斷是不是 ``*.streamlit.app``，以及組出完整連結。
    """
    url = ""
    try:
        url = st.context.url or ""
    except Exception:
        url = ""
    if url:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}", host
    host = ""
    try:
        raw = st.context.headers.get("Host") or ""
        host = raw.split(":")[0].lower()
    except Exception:
        host = ""
    return "", host


def static_mount_prefix() -> str:
    """靜態檔在瀏覽器裡的路徑前綴。本機是空字串，Cloud 是 ``/~/+``。"""
    _origin, host = _public_origin_and_host()
    if host.endswith(".streamlit.app"):
        return _CLOUD_MOUNT
    try:
        from streamlit import config as st_config

        base = (st_config.get_option("server.baseUrlPath") or "").strip("/")
    except Exception:
        base = ""
    return f"/{base}" if base else ""


def static_page_path() -> str:
    """給 iframe / 連結用的路徑，已含 Cloud 掛載前綴。"""
    return f"{static_mount_prefix()}{_STATIC_PATH}"


def static_page_url(app_base: str | None = None) -> str:
    """模擬器靜態頁的完整網址。QR code 請用這個，不要用 Streamlit 頁。"""
    path = static_page_path()
    if app_base:
        return f"{app_base.rstrip('/')}{path}"
    origin, _host = _public_origin_and_host()
    return f"{origin}{path}" if origin else path


def fire_panel_url(app_base: str) -> str:
    """學員應使用的直接網址（靜態頁，可當 QR code）。"""
    return static_page_url(app_base)


def query_requests_fire_panel() -> bool:
    """主頁網址帶 ``?page=fire-panel`` 時為 True。

    讀執行環境裡的 query string，不呼叫會畫出元件的 Streamlit API，
    這樣主頁仍可以先執行 ``st.set_page_config``。
    """
    try:
        from streamlit.runtime.scriptrunner_utils.script_run_context import (
            get_script_run_ctx,
        )
    except Exception:
        return False
    ctx = get_script_run_ctx()
    if ctx is None:
        return False
    values = parse_qs(getattr(ctx, "query_string", "") or "").get("page", [])
    return QUERY_VALUE in values


def render_fire_panel(
    *,
    height: int = 960,
    fill_viewport: bool = False,
    show_open_link: bool = True,
) -> None:
    """在目前頁面嵌入可操作的受信總機模擬器。

    Parameters
    ----------
    height:
        iframe 高度（CSS 像素）。``fill_viewport`` 為 False 時使用。
        闖關流程嵌入時傳這個值，避免改到外層頁面的留白。
    fill_viewport:
        True 時讓 iframe 盡量佔滿視窗，內容超出時在 iframe 裡捲動。
        給獨立的模擬器頁用。嵌在其他流程請維持 False。
    show_open_link:
        顯示「全螢幕開啟」。連結用 ``target=_top``，手機會離開
        Streamlit 外框，直接打開靜態模擬器。
    """
    _ensure_ready()
    page = static_page_path()
    if show_open_link:
        _render_open_link(static_page_url(), compact=not fill_viewport)
    if fill_viewport:
        st.markdown(_VIEWPORT_CSS, unsafe_allow_html=True)
        _embed(page, height=max(height, 720))
        return
    _embed(page, height=height)


def _render_open_link(url: str, *, compact: bool) -> None:
    pad = "10px 12px" if compact else "14px 12px"
    size = "1rem" if compact else "1.15rem"
    st.markdown(
        f'<a href="{url}" target="_top" rel="noopener" '
        f'style="display:block;text-align:center;background:#c53030;color:#fff;'
        f'font-weight:800;font-size:{size};line-height:1.35;padding:{pad};'
        f'border-radius:10px;text-decoration:none;margin:0 0 8px">'
        f"📱 全螢幕開啟（手機請點這裡）</a>",
        unsafe_allow_html=True,
    )


def _ensure_ready() -> None:
    html_path = _STATIC_DIR / "index.html"
    mp3_path = _STATIC_DIR / "alarm.mp3"
    if not html_path.is_file() or not mp3_path.is_file():
        st.error("模擬器檔案缺失，請確認 static/fire_panel/index.html 與 alarm.mp3 已部署。")
        st.stop()
    try:
        from streamlit import config as st_config

        enabled = bool(st_config.get_option("server.enableStaticServing"))
    except Exception:
        enabled = False
    if not enabled:
        st.error(
            "靜態檔服務未開啟，模擬器音效無法載入。"
            "請在 .streamlit/config.toml 設定 server.enableStaticServing = true。"
        )
        st.stop()


def _embed(src: str, height: int) -> None:
    # st.iframe 與舊的 components.iframe 都是同一個 sandbox：
    # allow-scripts / allow-modals / allow-downloads / autoplay。
    # 警報、語音、alert、下載報告都要這些權限。
    iframe = getattr(st, "iframe", None)
    if iframe is not None:
        iframe(src, width="stretch", height=height)
        return
    import streamlit.components.v1 as components

    components.iframe(src, height=height, scrolling=True)
