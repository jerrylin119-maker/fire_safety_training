"""
防火管理訓練互動教學與成效評估系統 — 學員端
=================================================
流程：簽到 → 前測 → 動態章節內容（觀念題＋案例應變） → 自衛消防編組模擬闖關 → 後測 → 學習卡

⚠️ 所有題目／案例／建議內容皆由 data/*.json 動態載入，程式碼中不寫死任何題目內容。
    講師如需調整題庫、案例、闖關劇本，請至「講師後台」頁面編輯或上傳 JSON 檔案。

📌 進度接續：每位學員簽到後會拿到一組「簽到代碼」（其實就是資料庫的學員編號）。
    只要重新整理同一個網址，或回到簽到頁輸入代碼，就能接續先前的作答進度，
    不會因為斷線、關閉分頁而全部重來。
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
    "sim_ending": None,
    "resume_attempted": False,
}
for k, v in DEFAULTS.items():
    if k not in st.session_state:
        st.session_state[k] = v


def _restore_from_student_id(student_id) -> bool:
    """依簽到代碼把學員資料與進度快照讀回 session_state。成功回傳 True。"""
    student = db.get_student(student_id)
    if not student:
        return False
    progress = db.load_progress(student["id"]) or {}
    st.session_state.update(
        student_id=student["id"],
        student_name=student["name"],
        student_venue=student["venue"],
        student_position=student["position"],
        stage=progress.get("stage") or "pretest",
        case_ptr=progress.get("case_ptr", 0),
        sim_node=progress.get("sim_node"),
        sim_task_idx=progress.get("sim_task_idx", 0),
        sim_ending=progress.get("sim_ending"),
        pretest_score=progress.get("pretest_score"),
        posttest_score=progress.get("posttest_score"),
        pretest_answers=progress.get("pretest_answers") or {},
        posttest_answers=progress.get("posttest_answers") or {},
    )
    try:
        st.query_params["sid"] = str(student["id"])
    except Exception:
        pass
    return True


def persist_progress():
    """把目前 session_state 的關鍵進度存進資料庫，供之後接續使用。"""
    if not st.session_state.student_id:
        return
    db.save_progress(st.session_state.student_id, {
        "stage": st.session_state.stage,
        "case_ptr": st.session_state.case_ptr,
        "sim_node": st.session_state.sim_node,
        "sim_task_idx": st.session_state.sim_task_idx,
        "sim_ending": st.session_state.sim_ending,
        "pretest_score": st.session_state.pretest_score,
        "posttest_score": st.session_state.posttest_score,
        "pretest_answers": st.session_state.pretest_answers,
        "posttest_answers": st.session_state.posttest_answers,
    })


# 重新整理頁面時，如果網址帶著 ?sid=xxx，且目前這個瀏覽分頁還沒有學員資料，
# 就自動嘗試接續進度（不需要學員自己按任何按鈕）。
if not st.session_state.student_id and not st.session_state.resume_attempted:
    st.session_state.resume_attempted = True
    qp_sid = st.query_params.get("sid")
    if qp_sid:
        _restore_from_student_id(qp_sid)

# 若尚未簽到卻不在簽到頁，一律導回簽到
if st.session_state.stage != "onboarding" and st.session_state.student_id is None:
    st.session_state.stage = "onboarding"

STAGE_LABELS = {
    "onboarding": "① 學員簽到",
    "pretest": "② 迷思破除前測",
    "cases": "③ 章節內容（觀念題＋案例）",
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
            st.caption(f"🔑 簽到代碼：**{st.session_state.student_id}**（斷線時可用來接續）")
        with st.expander("重新開始（測試 / 展示用）"):
            if st.button("清空我的作答，重新開始"):
                st.session_state.clear()
                try:
                    del st.query_params["sid"]
                except Exception:
                    pass
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
        try:
            st.query_params["sid"] = str(student_id)
        except Exception:
            pass
        persist_progress()
        st.rerun()

    st.divider()
    with st.expander("🔁 我是中途離開的學員，要繼續之前的進度"):
        st.caption("簽到成功後，畫面跟側邊欄會顯示一組「簽到代碼」，輸入該代碼即可接續作答進度。")
        code = st.text_input("請輸入簽到代碼", key="resume_code_input")
        if st.button("繼續之前的進度 ➜"):
            if code.strip() and _restore_from_student_id(code.strip()):
                st.rerun()
            else:
                st.error("找不到這組代碼，請確認輸入是否正確。")


# ==================== ② / ⑤ 前測與後測（共用邏輯） ====================
def render_test(phase: str):
    questions = dl.get_prepost_questions()
    answers_key = "pretest_answers" if phase == "pre" else "posttest_answers"

    st.header("🧠 迷思破除前測" if phase == "pre" else "🧠 觀念後測")
    score_key = "pretest_score" if phase == "pre" else "posttest_score"
    already_done = st.session_state[score_key] is not None

    if not already_done:
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
            st.session_state[score_key] = score

            if phase == "post":
                st.session_state.stage = "report"
            persist_progress()
            st.rerun()
    else:
        score = st.session_state[score_key]
        st.success(f"✅ 提交完成！您的{'前' if phase == 'pre' else '後'}測得分：**{score} 分**（滿分 100 分）")
        if phase == "pre":
            if st.button("進入下一階段：章節內容 ➜", width="stretch"):
                st.session_state.stage = "cases"
                persist_progress()
                st.rerun()
        else:
            st.session_state.stage = "report"
            persist_progress()
            st.rerun()


# ==================== ③ 動態章節內容（觀念題＋案例應變） ====================
def _get_unlocked_flat_items():
    """把目前已解鎖章節的『觀念題』與『情境案例』依章節順序串成同一份清單。"""
    control = dl.get_control_state()
    unlocked_chapter = control.get("unlocked_chapter", 1)
    chapters = sorted(dl.get_chapters(), key=lambda c: c["unlock_order"])
    max_order = max((c["unlock_order"] for c in chapters), default=1)
    flat = []
    for ch in chapters:
        if ch["unlock_order"] <= unlocked_chapter:
            for q in ch.get("quiz", []):
                flat.append({**q, "_type": "quiz", "chapter_id": ch["chapter_id"], "chapter_title": ch["chapter_title"]})
            for case in ch["cases"]:
                flat.append({**case, "_type": "case", "chapter_id": ch["chapter_id"], "chapter_title": ch["chapter_title"]})
            if ch.get("reminder"):
                flat.append({**ch["reminder"], "_type": "reminder", "chapter_id": ch["chapter_id"],
                             "chapter_title": ch["chapter_title"]})
    return flat, unlocked_chapter, max_order


def _render_quiz_item(item):
    st.markdown("#### 💡 觀念小測驗")
    st.info(item["question"])
    answered_key = f"quiz_answered_{item['id']}"
    if answered_key not in st.session_state:
        for i, opt_text in enumerate(item["options"]):
            if st.button(opt_text, key=f"quiz_opt_{item['id']}_{i}", width="stretch"):
                is_correct = i == item["correct_index"]
                db.save_case_answer(st.session_state.student_id, item["chapter_id"], item["id"],
                                     str(i), is_correct, item_type="quiz")
                st.session_state[answered_key] = i
                st.rerun()
    else:
        selected_i = st.session_state[answered_key]
        is_correct = selected_i == item["correct_index"]
        st.markdown(f"**你的答案：{item['options'][selected_i]}**")
        (st.success if is_correct else st.error)(
            ("✅ 答對了！" if is_correct else f"❌ 答錯了，正確答案是：{item['options'][item['correct_index']]}")
        )
        st.caption(item["explanation"])
        if st.button("下一題 ➜", width="stretch"):
            st.session_state.case_ptr += 1
            persist_progress()
            st.rerun()


def _render_case_item(case):
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
                                     opt["key"], is_correct, item_type="case")
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

        if st.button("下一個 ➜", width="stretch"):
            st.session_state.case_ptr += 1
            persist_progress()
            st.rerun()


def _render_reminder_item(item):
    st.markdown(f"#### 📌 {item.get('title', '本節重點提醒')}")
    points_html = "".join(f"<div style='margin:6px 0;font-size:1.05em'>◆ {p}</div>" for p in item.get("points", []))
    st.markdown(
        f"<div style='background:#eef7ee;border-radius:10px;padding:18px 22px;'>{points_html}</div>",
        unsafe_allow_html=True,
    )
    st.write("")
    if st.button("我了解了，繼續 ➜", width="stretch", key=f"reminder_next_{item['chapter_id']}"):
        st.session_state.case_ptr += 1
        persist_progress()
        st.rerun()


def render_cases():
    st.header("📖 動態章節內容")
    flat_items, unlocked_chapter, max_order = _get_unlocked_flat_items()

    _, col2 = st.columns([3, 1])
    with col2:
        if st.button("🔄 檢查最新進度"):
            st.rerun()

    ptr = st.session_state.case_ptr
    if ptr >= len(flat_items):
        if unlocked_chapter >= max_order:
            st.success("🎉 所有章節內容都已完成！")
            if st.button("前往下一階段：自衛消防編組闖關 ➜", width="stretch"):
                st.session_state.stage = "simulation"
                persist_progress()
                st.rerun()
        else:
            st.info("⏳ 已完成目前開放的內容，請等待講師開放下一節課程，或稍後點擊上方「檢查最新進度」。")
        return

    item = flat_items[ptr]
    st.caption(item["chapter_title"])
    if item["_type"] == "quiz":
        _render_quiz_item(item)
    elif item["_type"] == "reminder":
        _render_reminder_item(item)
    else:
        _render_case_item(item)


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
            persist_progress()
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
                if opt["next"] in sim.get("endings", {}):
                    st.session_state.sim_ending = opt["next"]
                del st.session_state[answered_key]
                persist_progress()
                st.rerun()

    elif node["type"] == "checklist":
        tasks = node["tasks"]
        idx = st.session_state.sim_task_idx

        if idx >= len(tasks):
            st.success("✅ 這個階段的任務已全部完成！")
            if st.button("前往下一階段 ➜", width="stretch"):
                st.session_state.sim_node = node["next"]
                st.session_state.sim_task_idx = 0
                if node["next"] in sim.get("endings", {}):
                    st.session_state.sim_ending = node["next"]
                persist_progress()
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
                persist_progress()
                st.rerun()


# ==================== ⑥ 學習卡與總結 ====================
def render_report():
    st.header("🎓 課後學習卡")

    pre = st.session_state.pretest_score or 0
    post = st.session_state.posttest_score or 0
    content_acc = db.get_content_accuracy(st.session_state.student_id)
    sim_acc = db.get_sim_accuracy(st.session_state.student_id)

    st.subheader("📊 前後測分數對比")
    col1, col2, col3 = st.columns(3)
    col1.metric("前測分數", f"{pre} 分")
    col2.metric("後測分數", f"{post} 分", delta=f"{round(post - pre, 1)} 分")
    improve = "—" if pre == 0 else f"{round((post - pre) / pre * 100, 1)}%"
    col3.metric("進步幅度", improve)

    st.divider()
    st.subheader("🧩 課程參與表現")
    col4, col5, col6 = st.columns(3)
    col4.metric("章節內容正確率",
                f"{content_acc['pct']}%" if content_acc["pct"] is not None else "未作答",
                help=f"共作答 {content_acc['total']} 題（觀念題＋案例），答對 {content_acc['correct']} 題")
    col5.metric("闖關應變正確率",
                f"{sim_acc['pct']}%" if sim_acc["pct"] is not None else "未作答",
                help=f"共 {sim_acc['total']} 個決策點，正確 {sim_acc['correct']} 個")
    ending_label = {
        "end_good": "✅ 圓滿完成應變",
        "end_bad_abandon": "⚠️ 應變中斷",
    }.get(st.session_state.sim_ending, "—")
    col6.metric("闖關結局", ending_label)

    # 總分：後測分數、章節內容正確率、闖關正確率，三者平均（缺項則以已完成的項目平均）
    components = [v for v in [post, content_acc["pct"], sim_acc["pct"]] if v is not None]
    total_score = round(sum(components) / len(components), 1) if components else 0
    st.divider()
    st.metric("🏆 綜合總分", f"{total_score} 分", help="由後測分數、章節內容正確率、闖關應變正確率平均計算")

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
