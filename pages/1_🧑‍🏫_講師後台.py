"""
講師後台與題庫管理頁面
=================================================
- 密碼保護
- 即時儀表板：簽到名冊、場所圓餅圖、前後測進步幅度、CSV 匯出
- 題庫動態管理：st.data_editor 線上編輯，或直接上傳 JSON 檔案整份置換
- 章節解鎖控制：控制全班學員手機端可看到的章節進度
"""
import json
from datetime import datetime

import altair as alt
import pandas as pd
import streamlit as st

from utils import data_loader as dl
from utils import db
from utils.helpers import check_admin_password
from utils.reporting import build_class_package, expand_survey_df, survey_summary

st.set_page_config(page_title="講師後台 - 防火管理訓練系統", page_icon="🧑‍🏫", layout="wide")
db.init_db()

st.title("🧑‍🏫 講師後台管理系統")

# ---------------- 登入驗證 ----------------
if "admin_authed" not in st.session_state:
    st.session_state.admin_authed = False

if not st.session_state.admin_authed:
    st.info("請輸入講台後台密碼以繼續。密碼建議設定於 `.streamlit/secrets.toml` 的 `admin_password`。")
    with st.form("admin_login"):
        pwd = st.text_input("後台密碼", type="password")
        ok = st.form_submit_button("登入")
    if ok:
        if check_admin_password(pwd):
            st.session_state.admin_authed = True
            st.rerun()
        else:
            st.error("密碼錯誤，請再試一次。")
    st.stop()

with st.sidebar:
    st.success("✅ 已登入講師後台")
    if st.button("登出"):
        st.session_state.admin_authed = False
        st.rerun()


# ==================== 資料轉換工具（扁平表格 ⇄ 巢狀 JSON） ====================
def prepost_to_df(questions: list) -> pd.DataFrame:
    rows = []
    for q in questions:
        opts = (q["options"] + [""] * 4)[:4]
        rows.append({
            "id": q["id"], "question": q["question"],
            "option_A": opts[0], "option_B": opts[1], "option_C": opts[2], "option_D": opts[3],
            "correct_index": q["correct_index"], "explanation": q["explanation"],
        })
    return pd.DataFrame(rows)


def df_to_prepost(df: pd.DataFrame) -> list:
    out = []
    for i, row in df.iterrows():
        qid = str(row.get("id") or f"q{i + 1}").strip() or f"q{i + 1}"
        out.append({
            "id": qid,
            "question": str(row["question"]),
            "options": [str(row["option_A"]), str(row["option_B"]), str(row["option_C"]), str(row["option_D"])],
            "correct_index": int(row["correct_index"]),
            "explanation": str(row["explanation"]),
        })
    return out


def cases_to_df(chapters: list) -> pd.DataFrame:
    rows = []
    for ch in chapters:
        for case in ch["cases"]:
            opt_map = {o["key"]: o["text"] for o in case["options"]}
            out_map = case.get("outcome_predictions", {})
            rows.append({
                "chapter_id": ch["chapter_id"], "chapter_title": ch["chapter_title"],
                "unlock_order": ch["unlock_order"], "case_id": case["case_id"], "scenario": case["scenario"],
                "option_A": opt_map.get("A", ""), "option_B": opt_map.get("B", ""),
                "option_C": opt_map.get("C", ""), "option_D": opt_map.get("D", ""),
                "correct_option": case["correct_option"],
                "outcome_A": out_map.get("A", ""), "outcome_B": out_map.get("B", ""),
                "outcome_C": out_map.get("C", ""), "outcome_D": out_map.get("D", ""),
                "correct_advice": case["correct_advice"],
            })
    return pd.DataFrame(rows)


def quiz_to_df(chapters: list) -> pd.DataFrame:
    rows = []
    for ch in chapters:
        for q in ch.get("quiz", []):
            opts = (q["options"] + [""] * 4)[:4]
            rows.append({
                "chapter_id": ch["chapter_id"], "chapter_title": ch["chapter_title"],
                "unlock_order": ch["unlock_order"], "quiz_id": q["id"], "question": q["question"],
                "option_A": opts[0], "option_B": opts[1], "option_C": opts[2], "option_D": opts[3],
                "correct_index": q["correct_index"], "explanation": q["explanation"],
            })
    return pd.DataFrame(rows)


def reminder_to_df(chapters: list) -> pd.DataFrame:
    rows = []
    for ch in chapters:
        rem = ch.get("reminder")
        if rem:
            rows.append({
                "chapter_id": ch["chapter_id"], "chapter_title": ch["chapter_title"],
                "unlock_order": ch["unlock_order"], "reminder_title": rem.get("title", "本節重點提醒"),
                "points": "\n".join(rem.get("points", [])),
            })
    return pd.DataFrame(rows)


def df_to_chapters(cases_df: pd.DataFrame, quiz_df: pd.DataFrame, reminder_df: pd.DataFrame) -> list:
    """把『案例』『章節觀念題』『章節重點提醒』三張表格合併回完整的 chapters 巢狀結構。
    各表格可以獨立新增/刪除列，即使某章節暫時只有其中幾種內容也不會遺失其他內容。"""
    chapters = {}

    def ensure_chapter(cid, title, order):
        if cid not in chapters:
            chapters[cid] = {
                "chapter_id": cid, "chapter_title": title, "unlock_order": order,
                "quiz": [], "cases": [], "reminder": None,
            }

    for _, row in cases_df.iterrows():
        cid = int(row["chapter_id"])
        ensure_chapter(cid, str(row["chapter_title"]), int(row["unlock_order"]))
        options = [{"key": k, "text": str(row[f"option_{k}"])} for k in ["A", "B", "C", "D"]]
        outcomes = {k: str(row[f"outcome_{k}"]) for k in ["A", "B", "C", "D"]}
        chapters[cid]["cases"].append({
            "case_id": str(row["case_id"]), "scenario": str(row["scenario"]),
            "options": options, "correct_option": str(row["correct_option"]),
            "outcome_predictions": outcomes, "correct_advice": str(row["correct_advice"]),
        })

    for _, row in quiz_df.iterrows():
        cid = int(row["chapter_id"])
        ensure_chapter(cid, str(row["chapter_title"]), int(row["unlock_order"]))
        chapters[cid]["quiz"].append({
            "id": str(row["quiz_id"]), "question": str(row["question"]),
            "options": [str(row["option_A"]), str(row["option_B"]), str(row["option_C"]), str(row["option_D"])],
            "correct_index": int(row["correct_index"]), "explanation": str(row["explanation"]),
        })

    for _, row in reminder_df.iterrows():
        cid = int(row["chapter_id"])
        ensure_chapter(cid, str(row["chapter_title"]), int(row["unlock_order"]))
        points = [p.strip() for p in str(row["points"]).split("\n") if p.strip()]
        if points:
            chapters[cid]["reminder"] = {"title": str(row["reminder_title"]) or "本節重點提醒", "points": points}

    return sorted(chapters.values(), key=lambda c: c["unlock_order"])


# ==================== 分頁 ====================
tab_dash, tab_class, tab_unlock, tab_prepost, tab_cases, tab_sim, tab_sys = st.tabs(
    ["📊 即時儀表板", "🗂️ 班別管理", "🔓 章節解鎖控制", "📝 前後測題庫", "📖 案例題庫", "🧯 闖關劇本", "⚙️ 系統設定"]
)

# -------------------------------------------------- 儀表板
with tab_dash:
    _control = dl.get_control_state()
    _class_options_df = db.get_class_list()
    _class_options = ["（全部班別）"] + list(_class_options_df["class_id"]) if not _class_options_df.empty else ["（全部班別）"]
    _default_label = _control.get("current_class") or "（全部班別）"
    _default_idx = _class_options.index(_default_label) if _default_label in _class_options else 0
    selected_class_label = st.selectbox("篩選班別", _class_options, index=_default_idx)
    selected_class = None if selected_class_label == "（全部班別）" else selected_class_label

    st.subheader("📋 簽到名冊")
    students_df = db.get_students_df(selected_class)
    if students_df.empty:
        st.info("目前尚無學員簽到。")
    else:
        st.dataframe(students_df, width="stretch", hide_index=True)

        c1, c2 = st.columns(2)
        with c1:
            st.subheader("🥧 場所類別分布")
            venue_counts = students_df["venue"].value_counts().reset_index()
            venue_counts.columns = ["venue", "count"]
            chart = (
                alt.Chart(venue_counts)
                .mark_arc(innerRadius=60)
                .encode(theta="count", color="venue", tooltip=["venue", "count"])
            )
            st.altair_chart(chart, width="stretch")

        with c2:
            st.subheader("📈 前後測分數比較")
            scores_df = db.get_test_scores_df(selected_class)
            if scores_df.empty:
                st.info("目前尚無前後測作答紀錄。")
            else:
                pivot = scores_df.pivot_table(index=["student_id", "name", "venue"], columns="phase",
                                               values="score").reset_index()
                for col in ["pre", "post"]:
                    if col not in pivot.columns:
                        pivot[col] = None
                pivot = pivot.rename(columns={"pre": "前測分數", "post": "後測分數"})
                pivot["進步幅度"] = pivot["後測分數"] - pivot["前測分數"]
                st.dataframe(pivot, width="stretch", hide_index=True)

                avg_df = pd.DataFrame({
                    "階段": ["前測平均", "後測平均"],
                    "分數": [
                        scores_df.loc[scores_df.phase == "pre", "score"].mean() if "pre" in scores_df.phase.values else 0,
                        scores_df.loc[scores_df.phase == "post", "score"].mean() if "post" in scores_df.phase.values else 0,
                    ],
                })
                bar = alt.Chart(avg_df).mark_bar().encode(x="階段", y="分數", tooltip=["階段", "分數"])
                st.altair_chart(bar, width="stretch")

        st.divider()
        st.subheader("😊 課後滿意度調查結果")
        survey_cfg = dl.get_survey()
        survey_raw_df = db.get_survey_df(selected_class)
        if survey_raw_df.empty:
            st.info("目前尚無學員填寫課後滿意度調查。")
        else:
            st.caption(f"已有 {len(survey_raw_df)} 人填寫（簽到 {len(students_df)} 人）。分數 1~5，5 分最滿意。")
            summary_df = survey_summary(survey_raw_df, survey_cfg.get("questions", []))
            sc1, sc2 = st.columns([3, 2])
            with sc1:
                chart = (
                    alt.Chart(summary_df).mark_bar()
                    .encode(x=alt.X("平均分數:Q", scale=alt.Scale(domain=[0, 5])),
                            y=alt.Y("題目:N", sort=None, axis=alt.Axis(labelLimit=400)),
                            tooltip=["題目", "平均分數", "回覆人數"])
                )
                st.altair_chart(chart, width="stretch")
            with sc2:
                st.dataframe(summary_df, width="stretch", hide_index=True)
            suggestions = survey_raw_df[survey_raw_df["suggestion"].fillna("").str.strip() != ""]
            st.markdown("**📝 學員建議事項**")
            if suggestions.empty:
                st.caption("（沒有學員留下建議）")
            else:
                st.dataframe(
                    suggestions[["name", "venue", "suggestion"]].rename(
                        columns={"name": "姓名", "venue": "場所類別", "suggestion": "建議事項"}),
                    width="stretch", hide_index=True)

        st.divider()
        st.subheader("⬇️ 資料匯出（依目前篩選的班別）")
        colA, colB, colC, colD = st.columns(4)
        with colA:
            st.download_button("匯出簽到名冊 CSV", students_df.to_csv(index=False).encode("utf-8-sig"),
                                file_name=f"roster_{datetime.now():%Y%m%d_%H%M}.csv", mime="text/csv",
                                width="stretch")
        with colB:
            case_df = db.get_case_answers_df(selected_class)
            st.download_button("匯出案例作答 CSV", case_df.to_csv(index=False).encode("utf-8-sig"),
                                file_name=f"case_answers_{datetime.now():%Y%m%d_%H%M}.csv", mime="text/csv",
                                width="stretch", disabled=case_df.empty)
        with colC:
            sim_df = db.get_sim_log_df(selected_class)
            st.download_button("匯出闖關紀錄 CSV", sim_df.to_csv(index=False).encode("utf-8-sig"),
                                file_name=f"sim_log_{datetime.now():%Y%m%d_%H%M}.csv", mime="text/csv",
                                width="stretch", disabled=sim_df.empty)
        with colD:
            survey_export_df = expand_survey_df(db.get_survey_df(selected_class), survey_cfg.get("questions", []))
            st.download_button("匯出滿意度調查 CSV", survey_export_df.to_csv(index=False).encode("utf-8-sig"),
                                file_name=f"survey_{datetime.now():%Y%m%d_%H%M}.csv", mime="text/csv",
                                width="stretch", disabled=survey_export_df.empty)

    if st.button("🔄 重新整理儀表板"):
        st.rerun()

# -------------------------------------------------- 班別管理
with tab_class:
    st.subheader("🗂️ 班別管理")
    st.caption("每次開課前，先在這裡設定／確認班別名稱，之後所有新簽到的學員都會自動歸屬這個班別。"
               "下課後可以打包下載這個班別的完整資料，再清空資料庫、準備開下一班。")

    control = dl.get_control_state()
    current_class = control.get("current_class")
    st.metric("目前班別", current_class or "尚未設定")
    if not current_class:
        st.caption("尚未設定時，第一位學員簽到會自動用今天日期建立一個班別。")

    with st.form("class_form"):
        new_class = st.text_input(
            "設定／更改「目前班別」名稱（會顯示在學員歡迎畫面：「歡迎參加 ○○ 期防火管理人訓練班」，例如填 115-03、第12）",
            value=current_class or datetime.now().strftime("%Y-%m-%d"),
        )
        if st.form_submit_button("設定為目前班別", width="stretch"):
            new_class = new_class.strip()
            if not new_class:
                st.error("班別名稱不能是空白。")
            else:
                control["current_class"] = new_class
                dl.save_control_state(control)
                st.success(f"已將目前班別設定為「{new_class}」，之後新簽到的學員都會歸屬這個班別。")
                st.rerun()

    st.divider()
    st.subheader("📋 資料庫中的班別列表")
    class_list_df = db.get_class_list()
    if class_list_df.empty:
        st.info("目前資料庫裡還沒有任何學員資料。")
    else:
        st.dataframe(class_list_df, width="stretch", hide_index=True)

    st.divider()
    st.subheader("📦 打包並下載「目前班別」的完整資料")
    if not current_class:
        st.info("尚未設定目前班別，無法打包。")
    else:
        pkg_students_df = db.get_students_df(current_class)
        st.caption(f"將打包「{current_class}」，共 {len(pkg_students_df)} 位學員的資料"
                   "（簽到名冊、前後測成績、章節作答、闖關紀錄、滿意度調查、摘要報告）。")
        if st.button("📦 產生打包檔", width="stretch", disabled=pkg_students_df.empty):
            st.session_state["_class_pkg_bytes"] = build_class_package(current_class)
            st.session_state["_class_pkg_name"] = current_class
        if (st.session_state.get("_class_pkg_bytes") is not None
                and st.session_state.get("_class_pkg_name") == current_class):
            st.download_button(
                "⬇️ 下載打包檔（ZIP）",
                st.session_state["_class_pkg_bytes"],
                file_name=f"class_{current_class}_{datetime.now():%Y%m%d_%H%M}.zip",
                mime="application/zip",
                width="stretch",
            )

    st.divider()
    st.subheader("🗑️ 課程結束：清空所有資料，準備下一班")
    st.warning("此操作會清空**所有班別**的簽到名冊與作答紀錄，且無法復原！請先確認已經打包下載過需要保存的資料。")
    confirm = st.checkbox("我已經打包下載需要的資料，確定要清空所有資料、準備開始下一班")
    if st.button("清空所有資料，準備下一班", disabled=not confirm, type="primary", width="stretch"):
        db.reset_all_data()
        control["current_class"] = ""
        dl.save_control_state(control)
        st.session_state.pop("_class_pkg_bytes", None)
        st.session_state.pop("_class_pkg_name", None)
        st.success("已清空所有資料。下一位學員簽到時，會自動用當天日期建立新的班別。")
        st.rerun()

# -------------------------------------------------- 章節解鎖控制
with tab_unlock:
    st.subheader("🔓 章節解鎖控制（全班學員即時同步）")
    st.caption("學員的手機畫面會依此設定即時開放對應章節案例，講到哪一節就在這裡解鎖到第幾節。")
    control = dl.get_control_state()
    chapters = sorted(dl.get_chapters(), key=lambda c: c["unlock_order"])

    st.metric("目前已解鎖至", f"第 {control.get('unlocked_chapter', 1)} 節")
    cols = st.columns(len(chapters) if chapters else 1)
    for i, ch in enumerate(chapters):
        with cols[i]:
            if st.button(f"解鎖至：{ch['chapter_title']}", key=f"unlock_btn_{ch['chapter_id']}",
                         width="stretch"):
                control["unlocked_chapter"] = ch["unlock_order"]
                dl.save_control_state(control)
                st.success(f"已解鎖至第 {ch['unlock_order']} 節！")
                st.rerun()

    st.divider()
    st.subheader("📊 各章節作答統計")
    st.caption("進入下一章節前，可以先看這裡的統計結果，決定要不要多花時間講解某一題。")
    if st.button("🔄 重新整理統計"):
        st.rerun()
    answers_df = db.get_case_answers_df()

    def _option_counts(item_id: str, item_type: str) -> tuple[dict, int]:
        """回傳 {selected_option: 人數} 與總作答人數；answers_df 為空或缺欄位時安全回傳空結果。"""
        if answers_df.empty or "case_id" not in answers_df.columns:
            return {}, 0
        sub = answers_df[(answers_df["case_id"] == item_id) & (answers_df["item_type"] == item_type)]
        return sub["selected_option"].value_counts().to_dict(), len(sub)

    def _render_option_bars(options: list, counts: dict, total: int, correct_key):
        for key, label in options:
            cnt = int(counts.get(key, 0))
            pct = (cnt / total) if total else 0
            mark = " ✅" if key == correct_key else ""
            st.caption(f"{label}{mark} — {cnt} 人（{pct * 100:.0f}%）")
            st.progress(pct)

    current_unlock = control.get("unlocked_chapter", 1)
    for ch in chapters:
        with st.expander(f"{ch['chapter_title']}", expanded=(ch["unlock_order"] == current_unlock)):
            has_content = False
            for q in ch.get("quiz", []):
                has_content = True
                counts, total = _option_counts(q["id"], "quiz")
                st.markdown(f"💡 **{q['question']}**　`{total} 人作答`")
                options = [(str(i), text) for i, text in enumerate(q["options"])]
                _render_option_bars(options, counts, total, str(q["correct_index"]))
                st.write("")
            for case in ch.get("cases", []):
                has_content = True
                counts, total = _option_counts(case["case_id"], "case")
                st.markdown(f"🔥 **{case['scenario']}**　`{total} 人作答`")
                options = [(opt["key"], f"{opt['key']}. {opt['text']}") for opt in case["options"]]
                _render_option_bars(options, counts, total, case["correct_option"])
                st.write("")
            if not has_content:
                st.caption("這個章節目前沒有觀念題或案例。")

    st.divider()
    with st.form("course_settings_form"):
        title = st.text_input("課程標題（顯示於學員端側邊欄）", value=control.get("course_title", ""))
        topic = st.text_input("今日課程主題（顯示於學員歡迎畫面：「今天的課程是 ○○」）",
                              value=control.get("course_topic", "消防設備維護及管理"))
        notice = st.text_area("即時公告（顯示於學員端側邊欄，留空則不顯示）", value=control.get("notice", ""))
        if st.form_submit_button("儲存課程設定"):
            control["course_title"] = title
            control["course_topic"] = topic.strip()
            control["notice"] = notice
            dl.save_control_state(control)
            st.success("已儲存！")

# -------------------------------------------------- 前後測題庫
with tab_prepost:
    st.subheader("📝 前後測題庫管理")
    st.caption("correct_index：0=A, 1=B, 2=C, 3=D。可直接新增/刪除列來增減題目。")
    questions = dl.get_prepost_questions()
    df = prepost_to_df(questions)
    edited = st.data_editor(
        df, num_rows="dynamic", width="stretch", key="prepost_editor",
        column_config={
            "correct_index": st.column_config.NumberColumn("correct_index (0-3)", min_value=0, max_value=3, step=1),
            "question": st.column_config.TextColumn("question", width="large"),
            "explanation": st.column_config.TextColumn("explanation", width="large"),
        },
    )
    c1, c2 = st.columns(2)
    with c1:
        if st.button("💾 儲存前後測題庫變更", width="stretch"):
            try:
                dl.save_prepost_questions(df_to_prepost(edited))
                st.success("已儲存！學員下次讀取時將套用新題庫。")
            except Exception as e:
                st.error(f"儲存失敗，請檢查表格內容：{e}")
    with c2:
        st.download_button("⬇️ 下載目前 prepost_test.json", dl.raw_json_text("prepost"),
                            file_name="prepost_test.json", mime="application/json", width="stretch")

    st.divider()
    st.markdown("**或直接上傳自訂 JSON 檔案整份置換：**")
    up = st.file_uploader("上傳 prepost_test.json", type="json", key="upload_prepost")
    if up is not None and st.button("套用上傳的前後測題庫"):
        try:
            dl.overwrite_from_upload("prepost", up.read())
            st.success("已套用上傳的題庫！")
            st.rerun()
        except Exception as e:
            st.error(f"檔案格式錯誤：{e}")

# -------------------------------------------------- 案例題庫
with tab_cases:
    st.subheader("📖 章節內容管理")
    st.caption("chapter_id / chapter_title / unlock_order 要在下面三張表格中保持一致，"
               "才會被合併成同一個章節。unlock_order 決定章節解鎖順序。")
    chapters = dl.get_chapters()

    st.markdown("#### 📌 章節重點提醒（學員答完該章節所有題目後顯示的總結卡）")
    st.caption("points 欄位可以打好幾行，一行一個重點，畫面上會自動加上 ◆ 符號條列顯示。")
    reminder_df = reminder_to_df(chapters)
    edited_reminder = st.data_editor(
        reminder_df, num_rows="dynamic", width="stretch", key="reminder_editor",
        column_config={
            "points": st.column_config.TextColumn("points（一行一點）", width="large"),
        },
    )

    st.markdown("#### 💡 章節觀念題（單純選擇題，不需要情境描述）")
    quiz_df = quiz_to_df(chapters)
    edited_quiz = st.data_editor(
        quiz_df, num_rows="dynamic", width="stretch", key="quiz_editor",
        column_config={
            "question": st.column_config.TextColumn("question", width="large"),
            "explanation": st.column_config.TextColumn("explanation", width="large"),
            "correct_index": st.column_config.NumberColumn("correct_index (0-3)", min_value=0, max_value=3, step=1),
        },
    )

    st.markdown("#### 🔥 情境案例（4 個選項 A/B/C/D）")
    cases_df = cases_to_df(chapters)
    edited_cases = st.data_editor(
        cases_df, num_rows="dynamic", width="stretch", key="cases_editor",
        column_config={
            "scenario": st.column_config.TextColumn("scenario", width="large"),
            "correct_advice": st.column_config.TextColumn("correct_advice", width="large"),
            "correct_option": st.column_config.SelectboxColumn("correct_option", options=["A", "B", "C", "D"]),
        },
    )
    c1, c2 = st.columns(2)
    with c1:
        if st.button("💾 儲存章節內容變更（重點提醒＋觀念題＋案例）", width="stretch"):
            try:
                dl.save_chapters(df_to_chapters(edited_cases, edited_quiz, edited_reminder))
                st.success("已儲存！")
            except Exception as e:
                st.error(f"儲存失敗，請檢查表格內容：{e}")
    with c2:
        st.download_button("⬇️ 下載目前 cases_data.json", dl.raw_json_text("cases"),
                            file_name="cases_data.json", mime="application/json", width="stretch")

    st.divider()
    st.markdown("**或直接上傳自訂 JSON 檔案整份置換：**")
    up = st.file_uploader("上傳 cases_data.json", type="json", key="upload_cases")
    if up is not None and st.button("套用上傳的案例題庫"):
        try:
            dl.overwrite_from_upload("cases", up.read())
            st.success("已套用上傳的案例題庫！")
            st.rerun()
        except Exception as e:
            st.error(f"檔案格式錯誤：{e}")

# -------------------------------------------------- 闖關劇本
with tab_sim:
    st.subheader("🧯 自衛消防編組闖關劇本管理")
    st.caption("闖關為多階段分支劇本結構，較適合直接編輯 / 上傳 JSON 原始檔（結構請參考下載範例）。")
    raw = dl.raw_json_text("simulation")
    edited_text = st.text_area("編輯闖關劇本 JSON", value=raw, height=400)
    c1, c2 = st.columns(2)
    with c1:
        if st.button("💾 驗證並儲存闖關劇本", width="stretch"):
            try:
                data = json.loads(edited_text)
                missing = [k for k in ("start_node", "nodes", "endings") if k not in data]
                if missing:
                    raise ValueError(f"缺少必要欄位：{', '.join(missing)}")
                dl.save_simulation(data)
                st.success("已儲存！")
            except Exception as e:
                st.error(f"JSON 格式錯誤：{e}")
    with c2:
        st.download_button("⬇️ 下載目前 simulation_data.json", raw,
                            file_name="simulation_data.json", mime="application/json", width="stretch")

    st.divider()
    st.markdown("**或直接上傳自訂 JSON 檔案整份置換：**")
    up = st.file_uploader("上傳 simulation_data.json", type="json", key="upload_sim")
    if up is not None and st.button("套用上傳的闖關劇本"):
        try:
            dl.overwrite_from_upload("simulation", up.read())
            st.success("已套用上傳的闖關劇本！")
            st.rerun()
        except Exception as e:
            st.error(f"檔案格式錯誤：{e}")

# -------------------------------------------------- 系統設定
with tab_sys:
    st.subheader("⚙️ 場所建議卡管理")
    up = st.file_uploader("上傳 venue_advice.json（整份置換）", type="json", key="upload_venue")
    if up is not None and st.button("套用上傳的場所建議卡"):
        try:
            dl.overwrite_from_upload("venue", up.read())
            st.success("已套用！")
            st.rerun()
        except Exception as e:
            st.error(f"檔案格式錯誤：{e}")
    st.download_button("⬇️ 下載目前 venue_advice.json", dl.raw_json_text("venue"),
                        file_name="venue_advice.json", mime="application/json")

    st.divider()
    st.subheader("😊 課後滿意度調查題目")
    st.caption("直接編輯 JSON：questions 每題的 id 不能重複；scale_labels 是 1~5 分對應的文字；"
               "suggestion_prompt 是「建議事項」欄位的提示文字。修改後學員下次進入結業頁面就會套用。")
    survey_text = st.text_area("編輯 survey.json", value=dl.raw_json_text("survey"), height=320, key="survey_json_text")
    if st.button("💾 驗證並儲存滿意度調查題目"):
        try:
            data = json.loads(survey_text)
            qs = data.get("questions")
            if not isinstance(qs, list) or not qs:
                raise ValueError("questions 必須是至少一題的清單")
            ids = [q["id"] for q in qs]
            if len(set(ids)) != len(ids):
                raise ValueError("題目 id 不能重複")
            for q in qs:
                if not q.get("text"):
                    raise ValueError(f"題目 {q['id']} 缺少 text")
            dl.save_survey(data)
            st.success("已儲存！")
        except Exception as e:
            st.error(f"格式錯誤：{e}")

    st.divider()
    st.subheader("🔑 後台密碼")
    st.caption("密碼請設定在 `.streamlit/secrets.toml` 的 `admin_password`（本機）或雲端平台的 Secrets 設定，"
               "本頁面不提供線上修改密碼功能，以避免密碼外洩風險。")

    st.divider()
    st.subheader("🗑️ 清空所有資料")
    st.caption("這個功能已經搬到「🗂️ 班別管理」分頁，跟班別打包下載放在一起，"
               "課程結束後可以在那裡先打包匯出、再清空資料，準備下一班。")
