"""
防火管理訓練互動教學系統 — 入口（Streamlit Cloud 的 Main file path 維持 app.py）
=================================================
這個檔案只負責「側欄導覽」：讓側欄第一項顯示成「互動教學系統」，而不是檔名 app。
學員端主程式在 student_app.py，講師後台與火警受信總機模擬器在 pages/。
"""
import streamlit as st

pg = st.navigation(
    [
        st.Page("student_app.py", title="互動教學系統", icon="🔥", default=True),
        st.Page("pages/1_🧑‍🏫_講師後台.py", title="講師後台", icon="🧑‍🏫", url_path="講師後台"),
        st.Page("pages/2_🚨_火警受信總機模擬器.py", title="火警受信總機模擬器", icon="🚨",
                url_path="火警受信總機模擬器"),
    ]
)
pg.run()
