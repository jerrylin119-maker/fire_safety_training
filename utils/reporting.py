"""
班別報表工具（不依賴 Streamlit，方便單獨測試）
---------------------------------
- expand_survey_df：把滿意度調查 JSON 回覆展開成「一題一欄」的表格
- survey_summary：每題平均分數與回覆人數
- build_class_package：把某班別的完整資料打包成 ZIP（摘要報告 + 各項 CSV）
"""
import io
import json
import zipfile
from datetime import datetime

import pandas as pd

from utils import data_loader as dl
from utils import db


def expand_survey_df(df: pd.DataFrame, questions: list) -> pd.DataFrame:
    """把 answers(JSON) 展開成每一題一欄，欄名用題目文字；建議事項放最後一欄。"""
    if df.empty:
        return df
    rows = []
    for _, r in df.iterrows():
        try:
            answers = json.loads(r["answers"]) if r["answers"] else {}
        except (TypeError, ValueError):
            answers = {}
        row = {
            "student_id": r["student_id"], "name": r["name"], "venue": r["venue"],
            "position": r["position"], "class_id": r["class_id"], "created_at": r["created_at"],
        }
        for q in questions:
            row[q["text"]] = answers.get(q["id"])
        row["建議事項"] = r["suggestion"] or ""
        rows.append(row)
    return pd.DataFrame(rows)


def survey_summary(df: pd.DataFrame, questions: list) -> pd.DataFrame:
    """每題平均分數（1~5）與有效回覆人數。"""
    expanded = expand_survey_df(df, questions)
    if expanded.empty:
        return pd.DataFrame(columns=["題目", "平均分數", "回覆人數"])
    out = []
    for q in questions:
        col = pd.to_numeric(expanded[q["text"]], errors="coerce")
        out.append({"題目": q["text"], "平均分數": round(col.mean(), 2) if col.notna().any() else None,
                    "回覆人數": int(col.notna().sum())})
    return pd.DataFrame(out)


def build_class_summary_md(class_id: str) -> str:
    students_df = db.get_students_df(class_id)
    scores_df = db.get_test_scores_df(class_id)
    case_df = db.get_case_answers_df(class_id)
    sim_df = db.get_sim_log_df(class_id)
    survey_df = db.get_survey_df(class_id)
    questions = dl.get_survey().get("questions", [])

    lines = [f"# 班別打包報告：{class_id}", "", f"匯出時間：{datetime.now():%Y-%m-%d %H:%M:%S}", ""]
    lines.append(f"## 簽到人數：{len(students_df)} 人")
    if not students_df.empty:
        lines.append("")
        lines.append("### 場所類別分布")
        for venue, cnt in students_df["venue"].value_counts().items():
            lines.append(f"- {venue}：{cnt} 人")

    lines.append("")
    lines.append("## 前後測成績")
    if scores_df.empty:
        lines.append("（尚無前後測作答紀錄）")
    else:
        pivot = scores_df.pivot_table(index="student_id", columns="phase", values="score")
        pre_avg = pivot["pre"].mean() if "pre" in pivot.columns else None
        post_avg = pivot["post"].mean() if "post" in pivot.columns else None
        lines.append(f"- 前測平均：{pre_avg:.1f} 分" if pre_avg is not None else "- 前測平均：無資料")
        lines.append(f"- 後測平均：{post_avg:.1f} 分" if post_avg is not None else "- 後測平均：無資料")
        if pre_avg is not None and post_avg is not None:
            lines.append(f"- 平均進步：{post_avg - pre_avg:+.1f} 分")

    lines.append("")
    lines.append("## 章節內容（觀念題＋案例）整體正確率")
    if case_df.empty:
        lines.append("（尚無作答紀錄）")
    else:
        lines.append(f"- 共 {len(case_df)} 題作答，整體正確率 {case_df['is_correct'].mean() * 100:.1f}%")

    lines.append("")
    lines.append("## 闖關模擬整體正確率")
    if sim_df.empty:
        lines.append("（尚無闖關紀錄）")
    else:
        lines.append(f"- 共 {len(sim_df)} 個決策點，整體正確率 {sim_df['is_correct'].mean() * 100:.1f}%")

    lines.append("")
    lines.append("## 課後滿意度調查（1~5 分，5 分最滿意）")
    if survey_df.empty:
        lines.append("（尚無學員填寫）")
    else:
        lines.append(f"- 共 {len(survey_df)} 人填寫（簽到 {len(students_df)} 人）")
        for _, r in survey_summary(survey_df, questions).iterrows():
            lines.append(f"- {r['題目']}：平均 {r['平均分數']} 分（{r['回覆人數']} 人）")
        suggestions = [s for s in survey_df["suggestion"].fillna("").tolist() if s.strip()]
        if suggestions:
            lines.append("")
            lines.append("### 學員建議事項")
            for s in suggestions:
                lines.append(f"- {s.strip()}")

    return "\n".join(lines)


def build_class_package(class_id: str) -> bytes:
    """把某個班別的完整資料打包成一個 ZIP（摘要報告 + 各項原始資料 CSV，Excel 可直接開啟）。"""
    questions = dl.get_survey().get("questions", [])
    survey_df = expand_survey_df(db.get_survey_df(class_id), questions)

    def csv(df: pd.DataFrame) -> bytes:
        return df.to_csv(index=False).encode("utf-8-sig")  # 加 BOM，Excel 開啟中文不亂碼

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("summary.md", build_class_summary_md(class_id))
        zf.writestr("roster.csv", csv(db.get_students_df(class_id)))
        zf.writestr("test_scores.csv", csv(db.get_test_scores_df(class_id)))
        zf.writestr("case_answers.csv", csv(db.get_case_answers_df(class_id)))
        zf.writestr("sim_log.csv", csv(db.get_sim_log_df(class_id)))
        zf.writestr("survey.csv", csv(survey_df))
    buf.seek(0)
    return buf.getvalue()
