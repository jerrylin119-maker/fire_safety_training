"""學員可公開使用的 P型火警受信總機模擬器。不需要講師後台密碼。

直接網址：/火警受信總機模擬器
ASCII 入口：/?page=fire-panel（由 app.py 轉進來）
"""
import streamlit as st

from utils.fire_panel import render_fire_panel

st.set_page_config(
    page_title="火警受信總機模擬器",
    page_icon="🚨",
    layout="wide",
    initial_sidebar_state="collapsed",
)
render_fire_panel(fill_viewport=True)
