# 🔥 防火管理訓練互動教學與成效評估系統

以 Streamlit 打造的防火管理課程互動教學系統，支援學員手機端即時作答、自衛消防編組闖關模擬，
並將所有「案例」「題庫」「場所建議」「闖關劇本」獨立於 JSON 檔案，講師可於課前 / 課間隨時透過
後台線上編輯或上傳新檔案來替換內容，**不需要修改任何程式碼**。

## 專案檔案結構

```
fire_safety_training/
├── app.py                          # 學員端主程式（簽到→前測→案例→闖關→後測→學習卡）
├── pages/
│   └── 1_🧑‍🏫_講師後台.py           # 講師後台（密碼保護、儀表板、題庫管理、章節解鎖控制）
├── utils/
│   ├── data_loader.py              # 讀寫 data/*.json 的工具函式（動態載入，不寫死內容）
│   ├── db.py                       # SQLite 讀寫層（簽到、作答紀錄，供多支手機同時使用）
│   └── helpers.py                  # 密碼驗證、分數計算等小工具
├── data/
│   ├── prepost_test.json           # 前後測 5 題迷思題
│   ├── cases_data.json             # 章節案例（可依講課進度解鎖）
│   ├── venue_advice.json           # 各場所類別的專屬課後提醒卡內容
│   ├── simulation_data.json        # 自衛消防編組闖關的分支劇本
│   ├── control_state.json          # 全班共用的即時控制狀態（目前解鎖到第幾節、公告等）
│   └── training.db                 # 執行後自動產生的 SQLite 資料庫（簽到與作答紀錄）
├── .streamlit/
│   └── secrets.toml.example        # 後台密碼設定範例（複製為 secrets.toml 使用）
├── requirements.txt
└── README.md
```

## 核心設計說明

1. **內容與程式碼完全解耦**：`utils/data_loader.py` 是唯一讀寫 JSON 的入口，`app.py` 與後台頁面
   都是「讀資料 → 渲染畫面」，換題庫、換案例、換闖關劇本完全不需要碰程式碼。
2. **章節解鎖是全班共用狀態**：寫在 `data/control_state.json`，講師在後台按下「解鎖至第N節」，
   全班學員的手機在下一次互動（提交、點擊按鈕，或點擊「檢查最新進度」）時就會讀到最新的解鎖狀態。
3. **多手機同時作答用 SQLite（WAL 模式）**，而非單純覆寫 CSV，避免多人同時寫入互相覆蓋。
4. **前測不揭曉解答，後測才公布**：符合你的需求——前測只給分數，後測完成後在學習卡一併呈現
   「前後測得分對比 + 正確解答 + 個人化場所提醒卡」。
5. **闖關劇本是「節點式狀態機」JSON**（`start_node` / `nodes` / `endings`），比表格更適合表達
   「有些選項直接跳結局、有些要走完 4 個子任務才能進下一階段」的分支邏輯，因此後台對它提供
   JSON 直接編輯 + 上傳置換，而非表格編輯器。

## 本地端安裝與執行

```bash
cd fire_safety_training
python3 -m venv .venv
source .venv/bin/activate   # Windows 請用 .venv\Scripts\activate
pip install -r requirements.txt
```

設定後台密碼（第一次使用請務必修改）：

```bash
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
# 編輯 .streamlit/secrets.toml，把 admin_password 改成你自己的密碼
```

啟動系統：

```bash
streamlit run app.py
```

- 學員端網址：`http://<你的電腦IP>:8501`（同一個 Wi-Fi 下，學員手機瀏覽器輸入這個網址即可）
- 講師後台：在瀏覽器網址列打「同一網址 + `/講師後台`」，或直接在左側 `>` 展開找到頁面連結
  （學員端已隱藏此導覽列，只有你自己在網址列輸入或用書籤才進得去）

> 若不知道自己電腦的區網 IP，Mac/Linux 可用 `ipconfig getifaddr en0` 或 `hostname -I` 查詢，
> 並確認防火牆允許 8501 連接埠，且手機與電腦在同一個 Wi-Fi。

## 雲端部署（Streamlit Community Cloud，免費）

1. 把整個 `fire_safety_training/` 資料夾推上一個 GitHub repo（**不要**把 `secrets.toml` 推上去，
   `.gitignore` 已經排除它）。
2. 到 https://share.streamlit.io 用 GitHub 帳號登入，選擇「New app」。
3. 選擇你的 repo、branch，Main file path 填 `app.py`。
4. 部署前先到 App 的 **Settings → Secrets**，貼上：
   ```toml
   admin_password = "你的後台密碼"
   ```
5. 部署完成後會得到一個公開網址（例如 `https://xxx.streamlit.app`），把這個網址做成 QR code
   給學員手機掃描即可（可用任意線上 QR code 產生器，把網址貼進去產生）。
6. 之後要換題庫，直接到後台上傳新的 JSON 檔案即可，**不需要重新部署**；但如果是改 `app.py`
   本身的程式邏輯，推新 commit 到 GitHub 後 Streamlit Cloud 會自動重新部署。

> 注意：Streamlit Community Cloud 的檔案系統在應用程式重啟或重新部署時**不會保留**（ephemeral），
> 也就是說 `training.db` 裡的簽到與作答紀錄、以及後台上傳覆蓋的 JSON 題庫，在應用重新部署後會
> 回到 repo 裡的初始版本。建議：
> - 每次上課前先在後台把最新題庫上傳好，**上課結束後記得用「匯出 CSV」把資料下載保存**。
> - 若需要長期保存歷史班級資料，可將 `utils/db.py` 改接雲端資料庫（如 Supabase/PostgreSQL），
>   或部署在自己的伺服器 / Docker 上（檔案系統才會持久化）。

## JSON 檔案格式參考

### `data/prepost_test.json`
```json
{
  "questions": [
    {
      "id": "q1",
      "question": "題目文字",
      "options": ["選項A", "選項B", "選項C", "選項D"],
      "correct_index": 1,
      "explanation": "後測公布時顯示的解答說明"
    }
  ]
}
```

### `data/cases_data.json`
```json
{
  "chapters": [
    {
      "chapter_id": 1,
      "chapter_title": "第1節：初期滅火與通報",
      "unlock_order": 1,
      "cases": [
        {
          "case_id": "c1-1",
          "scenario": "情境描述文字",
          "options": [
            { "key": "A", "text": "選項文字" },
            { "key": "B", "text": "選項文字" },
            { "key": "C", "text": "選項文字" },
            { "key": "D", "text": "選項文字" }
          ],
          "correct_option": "B",
          "outcome_predictions": { "A": "選A的後果", "B": "選B的後果", "C": "...", "D": "..." },
          "correct_advice": "正確應變建議"
        }
      ]
    }
  ]
}
```

### `data/venue_advice.json`
```json
{
  "旅宿業": {
    "emoji": "🏨",
    "reminder": "一句話重點提醒",
    "checklist": ["提醒事項1", "提醒事項2"]
  }
}
```
> 下拉選單的「場所類別」選項就是直接取這個檔案的 key，新增場所類別只要在這裡加一組 key 即可。

### `data/simulation_data.json`（闖關劇本狀態機）
```json
{
  "title": "闖關標題",
  "start_node": "s0",
  "nodes": {
    "s0": {
      "type": "single_choice",
      "title": "節點標題", "image_emoji": "🚨🏨", "scenario": "情境敘述",
      "options": [
        { "key": "A", "text": "選項文字", "next": "end_bad_abandon", "correct": false, "feedback": "回饋文字" },
        { "key": "B", "text": "選項文字", "next": "s1", "correct": true, "feedback": "回饋文字" }
      ]
    },
    "s1": {
      "type": "checklist",
      "title": "節點標題", "scenario": "情境敘述",
      "tasks": [
        { "id": "t1", "prompt": "子任務題目",
          "options": [
            { "key": "A", "text": "選項", "correct": false, "feedback": "..." },
            { "key": "B", "text": "選項", "correct": true, "feedback": "..." }
          ] }
      ],
      "next": "s2"
    }
  },
  "endings": {
    "end_bad_abandon": { "title": "結局標題", "message": "結局說明" },
    "end_good": { "title": "結局標題", "message": "結局說明" }
  }
}
```
- `single_choice` 節點：學員選一個選項，依 `next` 直接跳到下一個節點或結局。
- `checklist` 節點：學員需依序完成 `tasks` 裡的每一個子任務，全部完成後才跳到 `next`。
- `endings` 裡任何一個 key 只要被某個選項的 `next` 指到，就會顯示對應的結局畫面。

## 後台功能一覽

| 分頁 | 功能 |
|---|---|
| 📊 即時儀表板 | 簽到名冊、場所類別圓餅圖、前後測分數比較表與長條圖、CSV 匯出 |
| 🔓 章節解鎖控制 | 一鍵解鎖到第N節（全班即時同步）、課程標題與公告設定 |
| 📝 前後測題庫 | `st.data_editor` 線上編輯 5 題（可增刪題目）、下載/上傳 JSON |
| 📖 案例題庫 | `st.data_editor` 線上編輯章節案例（可增刪章節/案例）、下載/上傳 JSON |
| 🧯 闖關劇本 | JSON 原始碼編輯 + 驗證儲存、下載/上傳 JSON |
| ⚙️ 系統設定 | 場所建議卡上傳、密碼設定說明、清空資料（危險操作） |

## 待你依實際課程調整的地方

- `data/*.json` 目前的內容是**範例題目**（依你提到的旅宿業/安養中心等場景撰寫），正式上課前
  請在後台檢視、修改成符合你自己教材與時事案例的內容。
- 若學員人數較多（例如 50 人以上同時作答），建議改用效能更好的資料庫（PostgreSQL/Supabase）
  取代內建 SQLite，`utils/db.py` 是唯一需要調整的檔案。
- 目前「章節解鎖後學員需要點擊按鈕才能看到最新進度」，若想要「講師一解鎖、學員畫面自動跳轉」，
  可以另外安裝 `streamlit-autorefresh` 套件，在 `app.py` 加入每隔幾秒自動 rerun 的機制。
