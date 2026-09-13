"""共用小工具：密碼驗證、分數計算等。"""
import streamlit as st


def check_admin_password(input_password: str) -> bool:
    """
    密碼優先讀取 st.secrets["admin_password"]；
    若部署環境未設定 secrets（例如本機第一次測試），退回預設密碼 admin123，
    正式上課前請務必在 .streamlit/secrets.toml 或雲端後台設定自己的密碼。
    """
    try:
        correct = st.secrets["admin_password"]
    except Exception:
        correct = "admin123"
    return input_password == correct


def score_from_answers(answers: list[bool]) -> float:
    """依答對的布林清單計算 0~100 分（答對題數 / 總題數 * 100）。"""
    if not answers:
        return 0.0
    return round(sum(answers) / len(answers) * 100, 1)
