"""
防火管理訓練互動教學與成效評估系統 — 學員端
=================================================
流程：簽到 → 前測 → 動態章節案例應變 → 自衛消防編組模擬闖關 → 後測 → 學習卡

⚠️ 所有題目／案例／建議內容皆由 data/*.json 動態載入，程式碼中不寫死任何題目內容。
    講師如需調整題庫、案例、闖關劇本，請至「講師後台」頁面編輯或上傳 JSON 檔案。
"""
import streamlit as st
from utils import data_loader as dl
from utils import db
from utils.helpers import score_from_answers

st.set_page_config(page_title="防火管理訓練互動教學系統", page_icon="🔥", layout="centered")
db.init_db()

# ---------------- session_state 初始化 ----------------
DEFAULTS = {
    "stage": "onboarding",
    "student_id": None,
    "student_name": "",
    "student_venue": "",
    "student_position": "",
    "pretest_answers": {},
    "pretest_score": None,
    "posttest_answers": {},
    "posttest_score": None,
    "case_ptr": 0,
    "sim_node": None,
    "sim_task_idx": 0,
}
for k, v in DEFAULTS.items():
    if k not in st.session_state:
        st.session_state[k] = v

# 若尚未簽到卻不在簽到頁（例如重新整理），一律導回簽到
if st.session_state.stage != "onboarding" and st.session_state.student_id is None:
    st.session_state.stage = "onboarding"

STAGE_LABELS = {
    "onboarding": "① 學員簽到",
    "pretest": "② 迷思破除前測",
    "cases": "③ 章節案例應變",
    "simulation": "④ 自衛消防編組闖關",
    "posttest": "⑤ 觀念後測",
    "report": "⑥ 學習卡與總結",
}


def sidebar_progress():
    control = dl.get_control_state()
    with st.sidebar:
        st.title(f"🔥 {control.get('course_title', '防火管理訓練互動教學')}")
        if control.get("notice"):
            st.info(control["notice"])
        st.divider()
        for key, label in STAGE_LABELS.items():
            st.markdown(f"**➡️ {label}**" if key == st.session_state.stage else f"　{label}")
        st.divider()
        if st.session_state.student_name:
            st.caption(f"👤 {st.session_state.student_name}｜{st.session_state.student_venue}")
        with st.expander("重新開始（測試 / 展示用）"):
            if st.button("清空我的作答，重新開始"):
                # 清空所有 session_state（含各案例/闖關任務的作答暫存），
                # 下一次 rerun 時會重新套用 DEFAULTS，等同完全重來一次。
                st.session_state.clear()
                st.rerun()


# ==================== ① 學員簽到 ====================
def render_onboarding():
    st.header("👋 學員簽到")
    st.write("請填寫以下資料，開始今天的防火管理訓練課程。")
    venues = dl.get_venue_categories()
    with st.form("onboarding_form"):
        name = st.text_input("姓名")
        venue = st.selectbox("場所類別", venues)
        position = st.text_input("職稱")
        submitted = st.form_submit_button("開始簽到 ➜", width="stretch")
    if submitted:
        if not name.strip():
            st.error("請輸入姓名。")
            return
        student_id = db.add_student(name.strip(), venue, position.strip())
        st.session_state.update(
            student_id=student_id,
            student_name=name.strip(),
            student_venue=venue,
            student_position=position.strip(),
            stage="pretest",
        )
        st.rerun()


# ==================== ② / ⑤ 前測與後測（共用邏輯） ====================
def render_test(phase: str):
    questions = dl.get_prepost_questions()
    answers_key = "pretest_answers" if phase == "pre" else "posttest_answers"

    st.header("🧠 迷思破除前測" if phase == "pre" else "🧠 觀念後測")
    if phase == "pre":
        st.caption("提交後僅會顯示您的「分數」，正確解答將於課程最後的學習卡中一併公布，請放心作答！")
    else:
        st.caption("這是與前測相同的題目，看看你的觀念進步了多少！")

    with st.form(f"{phase}_test_form"):
        selections = {}
        for i, q in enumerate(questions):
            st.subheader(f"Q{i + 1}. {q['question']}")
            choice = st.radio(
                "選項", q["options"], index=None,
                key=f"{phase}_{q['id']}", label_visibility="collapsed",
            )
            selections[q["id"]] = choice
        submitted = st.form_submit_button("提交答案", width="stretch")

    if submitted:
        if any(v is None for v in selections.values()):
            st.error("請完成所有題目後再提交。")
            return
        correctness = []
        for q in questions:
            selected_index = q["options"].index(selections[q["id"]])
            is_correct = selected_index == q["correct_index"]
            correctness.append(is_correct)
            db.save_test_answer(st.session_state.student_id, phase, q["id"], selected_index, is_correct)

        score = score_from_answers(correctness)
        st.session_state[answers_key] = selections

        if phase == "pre":
            st.session_state.pretest_score = score
            st.success(f"✅ 提交完成！您的前測得分：**{score} 分**（滿分 100 分）")
            if st.button("進入下一階段：章節案例應變 ➜", width="stretch"):
                st.session_state.stage = "cases"
                st.rerun()
        else:
            st.session_state.posttest_score = score
            st.session_state.stage = "report"
            st.rerun()


# ==================== ③ 動態章節案例應變 ====================
def _get_unlocked_flat_cases():
    control = dl.get_control_state()
    unlocked_chapter = control.get("unlocked_chapter", 1)
    chapters = sorted(dl.get_chapters(), key=lambda c: c["unlock_order"])
    max_order = max((c["unlock_order"] for c in chapters), default=1)
    flat = []
    for ch in chapters:
        if ch["unlock_order"] <= unlocked_chapter:
            for case in ch["cases"]:
                flat.append({**case, "chapter_id": ch["chapter_id"], "chapter_title": ch["chapter_title"]})
    return flat, unlocked_chapter, max_order


def render_cases():
    st.header("📖 動態章節案例應變")
    flat_cases, unlocked_chapter, max_order = _get_unlocked_flat_cases()

    _, col2 = st.columns([3, 1])
    with col2:
        if st.button("🔄 檢查最新進度"):
            st.rerun()

    ptr = st.session_state.case_ptr
    if ptr >= len(flat_cases):
        if unlocked_chapter >= max_order:
            st.success("🎉 所有章節案例都已完成！")
            if st.button("前往下一階段：自衛消防編組闖關 ➜", width="stretch"):
                st.session_state.stage = "simulation"
                st.rerun()
        else:
            st.info("⏳ 已完成目前開放的案例，請等待講師開放下一節課程，或稍後點擊上方「檢查最新進度」。")
        return

    case = flat_cases[ptr]
    st.caption(case["chapter_title"])
    st.markdown("#### 🔥 情境描述")
    st.warning(case["scenario"])

    answered_key = f"case_answered_{case['case_id']}"
    if answered_key not in st.session_state:
        st.markdown("#### 你會怎麼應變？")
        for opt in case["options"]:
            if st.button(f"{opt['key']}. {opt['text']}", key=f"case_opt_{case['case_id']}_{opt['key']}",
                         width="stretch"):
                is_correct = opt["key"] == case["correct_option"]
                db.save_case_answer(st.session_state.student_id, case["chapter_id"], case["case_id"],
                                     opt["key"], is_correct)
                st.session_state[answered_key] = opt["key"]
                st.rerun()
    else:
        selected_key = st.session_state[answered_key]
        selected_opt = next(o for o in case["options"] if o["key"] == selected_key)
        is_correct = selected_key == case["correct_option"]

        st.markdown(f"**你的選擇：{selected_key}. {selected_opt['text']}**")
        prediction = case["outcome_predictions"].get(selected_key, "")
        (st.success if is_correct else st.error)(("✅ " if is_correct else "❌ ") + prediction)
        st.info(f"💡 正確建議：{case['correct_advice']}")

        if st.button("下一個案例 ➜", width="stretch"):
            st.session_state.case_ptr += 1
            st.rerun()


# ==================== ④ 自衛消防編組模擬闖關 ====================
def render_simulation():
    sim = dl.get_simulation()
    if st.session_state.sim_node is None:
        st.session_state.sim_node = sim["start_node"]

    st.header(f"🧯 {sim.get('title', '自衛消防編組模擬闖關')}")
    node_id = st.session_state.sim_node

    # ---- 結局畫面 ----
    if node_id in sim.get("endings", {}):
        ending = sim["endings"][node_id]
        box = st.success if node_id == "end_good" else st.warning
        st.markdown(f"## {ending['title']}")
        box(ending["message"])
        if st.button("前往下一階段：觀念後測 ➜", width="stretch"):
            st.session_state.stage = "posttest"
            st.rerun()
        return

    node = sim["nodes"][node_id]
    st.markdown(f"### {node['title']}")
    if node.get("image_emoji"):
        st.markdown(
            f"<div style='font-size:52px;text-align:center;line-height:1.4'>{node['image_emoji']}</div>",
            unsafe_allow_html=True,
        )
    st.warning(node["scenario"])

    if node["type"] == "single_choice":
        answered_key = f"sim_single_{node_id}"
        if answered_key not in st.session_state:
            for opt in node["options"]:
                if st.button(f"{opt['key']}. {opt['text']}", key=f"sim_opt_{node_id}_{opt['key']}",
                             width="stretch"):
                    db.save_sim_log(st.session_state.student_id, node_id, None, opt["key"], opt["correct"])
                    st.session_state[answered_key] = opt
                    st.rerun()
        else:
            opt = st.session_state[answered_key]
            (st.success if opt["correct"] else st.error)(opt["feedback"])
            if st.button("繼續 ➜", width="stretch"):
                st.session_state.sim_node = opt["next"]
                del st.session_state[answered_key]
                st.rerun()

    elif node["type"] == "checklist":
        tasks = node["tasks"]
        idx = st.session_state.sim_task_idx

        if idx >= len(tasks):
            st.success("✅ 這個階段的任務已全部完成！")
            if st.button("前往下一階段 ➜", width="stretch"):
                st.session_state.sim_node = node["next"]
                st.session_state.sim_task_idx = 0
                st.rerun()
            return

        st.progress(idx / len(tasks), text=f"任務進度：{idx}/{len(tasks)}")
        task = tasks[idx]
        st.markdown(f"**{task['prompt']}**")

        answered_key = f"sim_task_{node_id}_{task['id']}"
        if answered_key not in st.session_state:
            for opt in task["options"]:
                if st.button(f"{opt['key']}. {opt['text']}",
                             key=f"sim_task_opt_{node_id}_{task['id']}_{opt['key']}",
                             width="stretch"):
                    db.save_sim_log(st.session_state.student_id, node_id, task["id"], opt["key"], opt["correct"])
                    st.session_state[answered_key] = opt
                    st.rerun()
        else:
            opt = st.session_state[answered_key]
            (st.success if opt["correct"] else st.error)(("✅ " if opt["correct"] else "❌ ") + opt["feedback"])
            if st.button("下一步 ➜", width="stretch"):
                st.session_state.sim_task_idx += 1
                del st.session_state[answered_key]
                st.rerun()


# ==================== ⑥ 學習卡與總結 ====================
def render_report():
    st.header("🎓 課後學習卡")

    pre = st.session_state.pretest_score or 0
    post = st.session_state.posttest_score or 0
    col1, col2, col3 = st.columns(3)
    col1.metric("前測分數", f"{pre} 分")
    col2.metric("後測分數", f"{post} 分", delta=f"{round(post - pre, 1)} 分")
    improve = "—" if pre == 0 else f"{round((post - pre) / pre * 100, 1)}%"
    col3.metric("進步幅度", improve)

    st.divider()
    st.subheader("📚 迷思題解答回顧")
    questions = dl.get_prepost_questions()
    post_answers = st.session_state.posttest_answers
    for i, q in enumerate(questions):
        selected = post_answers.get(q["id"])
        correct = q["options"][q["correct_index"]]
        st.markdown(f"**Q{i + 1}. {q['question']}**")
        if selected == correct:
            st.success(f"你的答案：{selected} ✅")
        else:
            st.error(f"你的答案：{selected}　|　正確答案：{correct}")
        st.caption(q["explanation"])

    st.divider()
    st.subheader(f"📋 {st.session_state.student_venue} 專屬課後提醒卡")
    advice = dl.get_venue_advice().get(st.session_state.student_venue, {})
    st.markdown(f"### {advice.get('emoji', '🏢')} {st.session_state.student_venue}")
    st.info(advice.get("reminder", ""))
    for item in advice.get("checklist", []):
        st.markdown(f"- {item}")

    st.divider()
    st.success(f"🎉 恭喜 {st.session_state.student_name}，完成本次防火管理訓練課程！")


# ==================== 主流程路由 ====================
sidebar_progress()

ROUTES = {
    "onboarding": render_onboarding,
    "pretest": lambda: render_test("pre"),
    "cases": render_cases,
    "simulation": render_simulation,
    "posttest": lambda: render_test("post"),
    "report": render_report,
}
ROUTES[st.session_state.stage]()
