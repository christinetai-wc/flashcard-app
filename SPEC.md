# Flashcard Pro 雲端版 — Software Specification

> **版本日期：** 2026-03-14
> **主程式版本：** streamlit_app.py (~2,370 行) + admin_app.py (~800 行)
> **技術棧：** Streamlit + Google Firestore + Gemini AI + LINE Messaging API

---

## 1. 產品概述

**Flashcard Pro 雲端版** 是一個面向台灣國中小學生的英語學習 Web 應用，提供單字管理、卡片練習、測驗、句型口說等功能。採用 Freemium 訂閱模式，透過 Gemini AI 驅動核心的語音辨識、單字補全和圖片 OCR 功能。

---

## 2. 系統架構

### 2.1 檔案結構

| 檔案 | 行數 | 角色 |
|------|------|------|
| `streamlit_app.py` | 2,304 | 主應用程式（學生端） |
| `admin_app.py` | 753 | 後台管理系統（教師端） |
| `system_prompt.md` | 12 | Gemini 單字解析 Prompt 模板 |
| `pronunciation_feedback_prompt.md` | 42 | Gemini 語音辨識 Prompt 模板（舊版，新版 prompt 內嵌於 drill_component.py） |
| `drill_component.py` | ~850 | 句型口說 JS 元件產生器（TTS + 錄音 + VAD + Gemini + Firestore） |
| `fix_sentence_stats.py` | ~123 | 排行榜統計修復腳本（從 sentence_progress 重建 sentence_stats） |
| `drill_build/index.html` | ~300 | 句型口說 Streamlit custom component 版（備用） |
| `requirements.txt` | 7 | Python 依賴 |
| `CLAUDE.md` | ~62 | Claude 操作指引 |
| `SPEC.md` | — | 本文件（軟體規格書） |
| `README.md` | ~76 | 專案說明 |
| `DEVLOG.md` | ~130 | 開發變更紀錄 |
| `flashcard.jpg` | — | 應用圖示 |

### 2.2 技術依賴

```
streamlit                    # Web 框架
streamlit-cookies-controller # Cookie 管理（記住登入）
pandas                       # 資料處理
requests                     # HTTP 請求（Gemini API / LINE API）
google-cloud-firestore       # Firestore SDK
google-auth                  # Google 認證
SpeechRecognition            # 本地語音辨識（備援）
```

### 2.3 外部服務

| 服務 | 用途 | 模型/版本 |
|------|------|-----------|
| Google Firestore | 資料持久化 | — |
| Gemini API | 單字補全、語音辨識、OCR 圖片辨識 | `gemini-2.5-flash`（口說元件自動降級：2.5→2.0→語音辨識） |
| Web Speech API (瀏覽器) | TTS 文字轉語音 + SpeechRecognition 語音辨識 fallback | — |
| LINE Messaging API | 學生付款通知老師 | Push Message |

### 2.4 Secrets 結構

```toml
GEMINI_API_KEY = "..."
APP_ID = "flashcard-pro-v1"
LINE_CHANNEL_ACCESS_TOKEN = "..."   # LINE Bot（共用 expense_tracker 的 Bot）
LINE_TEACHER_USER_ID = "..."        # 老師的 LINE User ID

[firebase_credentials]
type = "service_account"
project_id = "..."
private_key = "..."
client_email = "..."
```

---

## 3. 功能規格

### 3.1 使用者管理

#### 3.1.1 登入系統
- **認證方式：** 直接輸入名稱（text_input）+ SHA-256 密碼雜湊比對
- **預設帳號：** Esme/S001、Neo/S002、Verno/S003（預設密碼 `1234`）
- **Cookie 記憶：** 使用 `streamlit-cookies-controller` 儲存帳號密碼（有效期 30 天），下次開啟自動預填
- **登出：** 清除 Cookie + 重設 session state

#### 3.1.2 自助註冊
- 位於登入區下方的 Expander「📝 新用戶註冊（7天免費試用）」
- 填寫名稱（≤20 字元）+ 密碼（≥4 字元）+ 確認密碼
- 名稱唯一性檢查（查詢 Firestore）
- **學號自動產生：** `S` + 3 位數字，從現有最大編號遞增，排除 S999 測試帳號
- **顏色隨機指定：** 從 10 種預設色中隨機選取
- **7 天免費試用：** 自動設定 `plan="premium"`, `plan_expiry=now+7天`, `plan_note="7-day free trial"`

#### 3.1.3 密碼修改
- 位於側邊欄 Expander 內
- 需驗證當前密碼、確認新密碼一致
- 更新 Firestore + session state + 清除 users 快取

#### 3.1.4 訂閱方案 (Freemium)

| 功能 | 免費方案 (free) | 付費方案 (premium) |
|------|----------------|-------------------|
| 單字 AI 補全（含 OCR） | 每日 3 次、每次 ≤100 行 | 無限制 |
| 語音辨識 | 不限次數（記錄用量） | 不限次數 |
| 付費句型書 | 🔒 鎖定 | 全部開放 |

- **Premium 判定 `is_premium()`：** `plan == "premium"` 且 `plan_expiry >= today`
- **到期自動降級：** 不修改 Firestore，僅在 `is_premium()` 判定時即時比對日期
- **舊用戶相容：** `user_info.get("plan")` 預設 `"free"`，無 `plan` 欄位視為免費
- **側邊欄顯示：**
  - Premium：顯示「💎 Premium 會員」或「💎 免費試用中（到期：{date}）」
  - 免費：顯示「🆓 免費方案（單字補全剩餘 N/3 次/天）」

#### 3.1.5 付款通知與訂閱付費流程
- **費用：** NT$300/月
- **付款方式：** LINE Pay 或銀行轉帳
- **匯款資訊取得方式：** 頁面不顯示帳號，引導學生透過 LINE 私訊老師取得匯款資訊（避免帳號公開曝露）
- **付款通知流程：**
  1. 學生完成轉帳後，點擊側邊欄 Expander「💰 我已完成轉帳」
  2. 輸入轉帳帳號末 5 碼（5 位數字驗證）
  3. 透過 LINE Messaging API Push Message 即時通知老師
  4. 通知內容：學生名稱、學號、末 5 碼、時間
  5. 老師確認收款後，透過 admin_app.py 手動開通 Premium

#### 3.1.6 AI 用量追蹤
- `record_ai_usage(usage_type, token_count)` — 寫入 Firestore
- 結構：`ai_usage.{type}.{date}` = 累計 token 數
- 使用 `firestore.Increment()` 避免併發覆蓋
- 寫入失敗靜默處理（`try-except pass`）

### 3.2 單字管理（`單字管理` 頁面）

共 5 個子分頁：

#### 3.2.1 ✨ AI 輸入（合併文字輸入 / 拍照 / 上傳圖片）
- **共用：** 課程名稱選擇（selectbox 或新增）、日期選擇
- **三種輸入模式**（`st.radio` 切換，horizontal）：
  - **✏️ 文字輸入**：`st.text_area` → `call_gemini_to_complete()` → 預覽儲存
  - **📸 拍照**：`st.camera_input()` → `call_gemini_ocr()` → 預覽儲存
  - **📁 上傳圖片**：`st.file_uploader(accept_multiple_files=True)` → `call_gemini_ocr()` → 預覽儲存
- **AI 補全前檢查（共用）：**
  - 免費用戶額度檢查 `check_vocab_ai_usage()`，用完顯示升級提示
  - 文字模式：行數 ≤ `VOCAB_AI_MAX_LINES` (100)
  - 圖片模式：每張上限 10MB
- **Prompt：** 從 `system_prompt.md` 讀取，OCR 前置「從圖片中辨識英文單字」指令
- **結果：** `data_editor` 預覽 → 確認儲存
- **Session State：** `pending_items`（文字）和 `pending_ocr_items`（圖片）分開存，避免互相覆蓋

#### 3.2.2 手動修改
- 課程篩選器 → `st.data_editor` 直接編輯 → 逐筆 update

#### 3.2.3 單字刪除
- 全選 Checkbox + 個別勾選 → 批次刪除

#### 3.2.4 📂 CSV 匯入/匯出
兩個子 tab：

**📥 匯入：**
- CSV 必須含 `English`、`Chinese_1` 欄位
- 可指定預設課程名稱與日期
- **重複檢查：** 比對 English 欄位（case-insensitive），跳過已存在的單字，顯示「N 筆為新單字，M 筆已存在（將跳過）」
- Firestore batch（每 400 筆 commit）

**📤 匯出：**
- 匯出所有個人單字為 CSV
- 欄位：English, POS, Chinese_1, Chinese_2, Example, Course, Date, Correct, Total
- `st.download_button`，UTF-8 BOM 編碼（`utf-8-sig`，Excel 相容）
- 檔名：`{user_name}_vocabulary.csv`

#### 3.2.5 📥 公用單字集
- 從 Firestore `shared_vocab` 讀取可用單字集目錄
- 瀏覽各單字集內容（表格預覽）
- **一鍵匯入：** 選擇課程名稱 → 批次寫入個人單字庫
- **重複檢查：** 同 CSV 匯入，跳過已存在的 English 單字

### 3.3 單字練習（`單字練習` 頁面）

- **練習時長追蹤：** `track_practice_time()` 記錄進入/離開時間，存入 Firestore `practice_duration`

#### 3.3.1 快閃練習
- 卡片式 UI：英文 → 翻面顯示中文 + 例句
- **鍵盤快捷鍵：** ← 上一個 / → 下一個 / Space 翻面（JavaScript bridge）
- **TTS：** 未翻面播英文；翻面播例句（Web Speech API）

#### 3.3.2 實力測驗
- **抽題策略（SRS 優先）：** `sample_for_review()` 優先抽 SRS 到期的單字，不足時用 `sample_by_accuracy()` 補充
- 抽取最多 10 題
- 判定邏輯：`ans in Chinese_1 or Chinese_1 in ans`
- 答對/答錯即時更新 Firestore + SRS 參數（`compute_srs_update()`）
- 測驗結束顯示得分 + 錯題回顧
- **慶祝**：滿分 `st.balloons()` + 鼓勵訊息

#### 3.3.3 例句連連看
- 從有例句的單字中抽 5 個（正確率低的優先）
- 例句目標單字替換為 `______`（正則不區分大小寫）
- 6 個選項（5 正確 + 1 干擾項）
- 使用 `st.form` 一次提交
- 答對/答錯更新 `Correct` / `Total`
- **慶祝**：全對 `st.balloons()`

### 3.4 句型口說練習（`句型口說` 頁面）

- **練習時長追蹤：** 同單字練習，`track_practice_time()` 記錄持續時間
- **教學法：** Substitution Drill（替換練習）— 同一句型反覆替換不同單字，建立口語肌肉記憶

#### 3.4.1 題庫選擇與 Premium 門控
- 從 Firestore `sentences` 目錄讀取所有題庫（含 `is_premium` 欄位）
- 合併選單：`{書名} (全部)` / `{書名} | {分類}`
- **付費句型書：** 選單顯示 🔒 圖示；免費用戶選擇後顯示升級提示並 `st.stop()`
- **智慧跳轉：** 切換題庫時自動跳到第一個 `completion_count == 0` 的句型

#### 3.4.2 練習流程（JS 自包含元件 `drill_component.py`）
- 全程由 JS 控制，不依賴 Streamlit 的 request-response 循環
- 嵌入方式：`st.components.v1.html()` iframe
- **流程：** 按「開始練習」→ 逐個選項：TTS 示範 → 錄音 + VAD 偵測說完 → 預篩 → AI 判讀 → 回饋 → 下一個
- **動態 VAD：** 錄音前偵測 0.5 秒環境底噪，門檻 = `max(12, 底噪×1.5)`；最長錄音 10 秒保底
- **深色模式：** 偵測父頁面 `document.body` 背景色亮度，動態加 `body.dark` / `body.light` class
- **iOS Safari 相容：** iframe 動態加 `allow="microphone; autoplay"`；TTS user gesture 解鎖（靜音播放 + 8s timeout）；SR 不可用時 `_srAvailable=false` 跳過預篩
- **排行榜更新：** `updateSentenceStats()` 完成一輪後更新 user doc 的 `sentence_stats`；首次完成才遞增 `completed`
- **慶祝動畫：** 完成一輪後 emoji confetti（JS CSS animation）

#### 3.4.3 AI 判讀（額度控管 + 預篩 + 多模型降級 + 語音辨識 fallback）

**免費用戶每日額度：**
- `FREE_DAILY_DRILL_LIMIT = 30` 次 AI 判讀/天
- 用完後自動切換語音辨識模式（仍可練習，不花 token）
- `ai_usage.drill_count.{date}` 記錄每日判讀次數
- Premium 用戶不限次數

**預篩（零 token 成本）：**
- 錄音時同步啟動瀏覽器 `SpeechRecognition`
- **SpeechRecognition 完全沒辨識到文字** → 不送 Gemini，直接提示重唸（防止小孩亂按浪費額度）

**降級策略（僅 429 額度不足 / 404 模型不存在觸發）：**
1. `gemini-2.5-flash` — 音訊 + Prompt → JSON `{ is_correct, transcript, feedback }`
2. `gemini-2.0-flash` — 同上
3. **瀏覽器 `SpeechRecognition` 文字比對** — 用預篩階段已取得的辨識結果

- 同一段錄音不需要重唸，降級時直接用已錄好的音訊/辨識結果
- `modelIdx` 記住當前可用模型，後續選項直接跳過已知不可用的
- 其他錯誤（網路、解析等）不觸發降級，直接回報失敗讓學生重試

**判讀規則：**
- 學生必須嘗試完整句子（只唸目標字不算通過）
- 寬容：冠詞替換、時態變化、發音不完美皆可接受
- 回饋：繁體中文，指出發音問題並給建議

**語音辨識 fallback 文字比對：**
- 目標字必須出現 + 至少 40% 關鍵字匹配
- 去除虛詞（a/an/the/is/are 等）後比對

**Token 使用量記錄：**
- Gemini 回應的 `usageMetadata.totalTokenCount` 由 JS 寫入 Firestore `ai_usage.speech.{date}`
- 與 Python 端 `record_ai_usage()` 同結構，後台統計可正確彙整

#### 3.4.4 Firestore 寫入
- JS 直接用 Firestore REST API 寫入（Python 產生短期 service account access token）
- **逐題存入：** 每個 option 通過後即時寫入 `completed_options`（中途離開不丟進度）
- **整輪完成：** `completion_count` +1 → 追加 `rounds` 陣列 → `completed_options` 重置為空
- 續練時：讀取 `completed_options` 跳過已完成的選項，按鈕顯示「繼續練習」

#### 3.4.5 進度與星級
- Document ID = 句型模板 MD5 hash
- `completion_count`：累計完成輪數
- `completed_options`：本輪已完成的選項（未完成一整輪時有值，完成後重置為空）
- 星級：1 輪 ⭐ / 3 輪 ⭐⭐ / 5 輪 ⭐⭐⭐
- 儀表板以 `completion_count` 為準（不看 `completed_options`）

### 3.5 學習儀表板（`學習儀表板` 頁面）

4 個 Tab：

#### 3.5.1 個人戰績表
- **單字課程進度：** Course → Date 分群，堆疊進度條（🟢熟練/🟡練習中/⚪未開始）
- **句型書進度：** 題庫 → 分類分群，堆疊進度條（付費書 🔒 標記）
- 每個項目可點擊跳轉至對應練習頁面

#### 3.5.2 全班排行榜（登入後）
- 從所有使用者的 `sentence_stats` 彙整
- 按句型書分組，完成率降序 → 完成數降序
- 前三名 🥇🥈🥉
- **當前使用者高亮**：黃色底色 + 👈 標記
- 含刷新按鈕
- **資料來源**：`sentence_stats` 為快取，由 JS `updateSentenceStats()` 在完成一輪時更新；`fix_sentence_stats.py` 可從 `sentence_progress` 重建

#### 3.5.3 單字學習 Tab
- 三個 Metric：單字數、覆蓋率、正確率
- 單字明細表

#### 3.5.4 句型練習 Tab
- 三個 Metric：總句數、已練習（N/M）、累計輪數（2×2 排列）
- 進度明細表：分類、句型、輪數、熟練度（⭐/⭐⭐/⭐⭐⭐）
- 清除紀錄按鈕（需 `@st.dialog` 確認）

#### 3.5.1 個人戰績表 — 句型書進度
- 堆疊進度條：🟢 熟練（3 輪以上）/ 🟡 練習中（1~2 輪）/ ⚪ 未練習

### 3.6 管理後台 (`admin_app.py`)

6 大功能（側邊欄切換）：

#### 3.6.1 學生帳號管理
- 新增（姓名、學號、密碼、代表色）
- 編輯（學號、代表色、重設密碼）
- 刪除（`@st.dialog` 確認）
- 使用者總覽表（含 plan 欄位）

#### 3.6.2 訂閱管理
- **費用：** NT$300/月
- **全班總覽表：** 姓名、學號、訂閱狀態（💎Premium/⚠️已過期/🆓免費）、備註
- **開通 Premium：** 選擇天數（30/60/90/180/365）+ 備註（如收款紀錄）；未過期時從現有到期日延長
- **取消 Premium：** 立即降級為 free，備註加上 `[已取消]` 前綴（`@st.dialog` 確認）
- **未來規劃：** 20+ 付費用戶後考慮串接藍新定期定額自動扣款

#### 3.6.3 AI 用量統計
- 讀取所有使用者的 `ai_usage` 欄位
- **總覽 Metric：** 總 Token 數、語音辨識 Token、單字補全 Token
- **預估費用：** Gemini 2.5 Flash 均價 $0.3/M Token → US$ → NT$
- **每日趨勢：** pivot table（日期 × 類型）
- **各使用者用量：** pivot table（使用者 × 類型 + 合計）
- **完整明細：** 展開式

#### 3.6.4 匯入句型書 (CSV)
- CSV 格式：`Category | Template | Options`
- 新增 `is_premium` checkbox（可標記為付費句型書）
- Firestore batch 寫入

#### 3.6.5 編輯現有句型書
- 合併選單（含 🔒 付費標記）
- **Premium 切換：** checkbox 即時更新 `is_premium`
- `data_editor`：inline 編輯、動態新增行、勾選刪除
- 全選/取消全選

#### 3.6.6 管理公用單字集
- **上傳單字集：** CSV 上傳 → 預覽 → 指定名稱 → 寫入 Firestore（`shared_vocab` 目錄 + `shared_vocab_data` 資料）
- **管理現有單字集：** 列出所有已上傳的單字集，可查看內容或刪除

---

## 4. 資料模型

### 4.1 Firestore 路徑結構

```
artifacts/{APP_ID}/
├── public/data/
│   ├── users/{user_name}              # 使用者帳號
│   ├── sentences/{dataset_id}         # 句型書目錄（metadata）
│   ├── {dataset_id}/{doc_id}          # 句型題目內容
│   ├── shared_vocab/{set_id}          # 公用單字集目錄（metadata）
│   └── shared_vocab_data/{set_id}     # 公用單字集資料（單一文件，words 陣列）
└── users/{student_id}/
    ├── vocabulary/{doc_id}            # 單字庫
    └── sentence_progress/{md5}        # 句型進度
```

### 4.2 Schema 定義

#### User

| 欄位 | 類型 | 說明 |
|------|------|------|
| name | string | 顯示名稱（= Document ID） |
| id | string | 學號（S + 3 位數字） |
| password | string | SHA-256 雜湊密碼 |
| color | string | 代表色（hex） |
| sentence_stats | map | 句型統計，結構：`{ [dataset_id]: { name, total, completed, in_progress, last_active } }` |
| plan | string | 訂閱方案：`"free"` 或 `"premium"`（預設 `"free"`） |
| plan_expiry | timestamp | Premium 到期日 |
| plan_note | string | 管理員備註 |
| ai_usage | map | AI 用量，結構：`{ speech: { "YYYY-MM-DD": token_count }, vocab: { ... } }` |
| practice_duration | map | 練習時長，結構：`{ "YYYY-MM-DD": seconds }` |

#### Vocabulary

| 欄位 | 類型 | 說明 |
|------|------|------|
| English | string | 英文單字 |
| POS | string | 詞性（繁體中文） |
| Chinese_1 | string | 主要中文釋義 |
| Chinese_2 | string | 次要中文釋義 |
| Example | string | 英文例句 |
| Course | string | 課程名稱 |
| Date | string | 加入日期 (YYYY-MM-DD) |
| Correct | int | 答對次數 |
| Total | int | 總練習次數 |
| srs_interval | int | SRS 間隔天數（預設 0） |
| srs_ease | float | SRS 難易度因子（預設 2.5） |
| srs_due | string | SRS 下次複習日期（YYYY-MM-DD，空字串=未排程） |
| srs_streak | int | SRS 連續正確次數（預設 0） |
| srs_last_review | string | SRS 最後複習日期（YYYY-MM-DD） |

#### Sentence Catalog

| 欄位 | 類型 | 說明 |
|------|------|------|
| id | string | 題庫 ID |
| name | string | 顯示名稱 |
| is_premium | bool | 是否為付費 Premium 專屬（預設 `false`） |
| last_updated | timestamp | 最後更新時間 |

#### Sentence

| 欄位 | 類型 | 說明 |
|------|------|------|
| Category | string | 分類名稱 |
| Template | string | 句型模板（含 `___`） |
| Options | array[string] | 可填入的選項 |
| Order | int | 排序編號 |
| Timestamp | timestamp | 建立時間 |

#### Sentence Progress

| 欄位 | 類型 | 說明 |
|------|------|------|
| template_text | string | 原始句型模板 |
| completed_options | array[string] | 已完成選項（每輪結束後重置為空） |
| completion_count | int | 累計完成輪數 |
| dataset_id | string | 所屬題庫 ID |
| rounds | array[map] | 每輪詳細結果（見下方） |
| last_updated | timestamp | 最後更新時間 |

**rounds 陣列結構：**
```json
{
  "round": 1,
  "timestamp": "2026-03-14T10:30:00Z",
  "results": {
    "test": { "tries": 1, "transcript": "This test is very important.", "feedback": "..." },
    "rule": { "tries": 2, "transcript": "...", "feedback": "..." }
  }
}
```

#### Shared Vocab Catalog (`shared_vocab/{set_id}`)

| 欄位 | 類型 | 說明 |
|------|------|------|
| name | string | 單字集名稱 |
| count | int | 單字數量 |
| created_at | timestamp | 建立時間 |

#### Shared Vocab Data (`shared_vocab_data/{set_id}`)

| 欄位 | 類型 | 說明 |
|------|------|------|
| words | array[map] | 單字陣列，每個元素含 English, POS, Chinese_1, Chinese_2, Example |

> **設計決策：** 單一文件存整個 words 陣列（~200KB），而非每個單字一個文件，減少 Firestore 讀取次數。

---

## 5. 快取策略

| 函式 | 裝飾器 | TTL | 說明 |
|------|--------|-----|------|
| `get_db()` | `@st.cache_resource` | 永久 | Firestore 連線 |
| `fetch_users_list()` | `@st.cache_data` | 600s | 使用者列表 |
| `fetch_sentence_catalogs()` | `@st.cache_data` | 600s | 句型書目錄 |
| `fetch_sentences_by_id()` | `@st.cache_data` | 600s | 特定題庫句型 |
| `fetch_shared_vocab_catalogs()` | `@st.cache_data` | 600s | 公用單字集目錄 |

快取清除時機：密碼修改、統計更新、註冊新用戶 → `fetch_users_list.clear()`

---

## 6. Session State

| Key | Type | 用途 |
|-----|------|------|
| `logged_in` | bool | 登入狀態 |
| `user_info` | dict | 當前使用者資訊 |
| `current_user_name` | str | 使用者名稱（Firestore doc ID） |
| `u_vocab` | list[dict] | 使用者單字庫快取 |
| `nav_selection` | str | 當前頁面 |
| `practice_idx` | int | 快閃練習索引 |
| `practice_reveal` | bool | 卡片翻面狀態 |
| `quiz_history` | list | 測驗歷史 |
| `test_pool` / `t_idx` / `t_score` | — | 測驗進行狀態 |
| `match_pool` / `match_options` | — | 連連看狀態 |
| `sentence_idx` | int | 句型練習索引 |
| `completed_options` | set | 當前句型已完成選項 |
| `current_dataset_id` | str | 當前題庫 ID |
| `last_sentence_filter_sig` | str | 篩選簽名（偵測切換） |
| `loaded_hash` | str | 已載入進度的句型 hash |
| `vocab_ai_count` | int | 今日單字 AI 使用次數（免費方案） |
| `vocab_ai_date` | str | 計數日期 |
| `users_db_cache` | dict | callback 用使用者名單暫存 |
| `pending_items` | list\|None | 文字 AI 補全暫存結果 |
| `pending_ocr_items` | list\|None | 圖片 OCR 暫存結果 |
| `practice_start_time` | float\|None | 練習開始時間（`time.time()`） |

---

## 7. 程式碼結構對照表

### streamlit_app.py (2,304 行)

| 區段 | 行號 | 內容 |
|------|------|------|
| Import & 設定 | 1–65 | 套件、API 常數、Firestore、Cookie、免費方案限制、LINE Bot |
| 工具函式 | 74–167 | `send_line_notify`, `hash_*`, `is_premium`, `check_vocab_ai_usage`, `consume_vocab_ai_usage`, `record_ai_usage` |
| 自助註冊 | 170–222 | `register_new_user` |
| 快取函式 | 224–245 | `fetch_users_list`, `init_users_in_db` |
| Session State 初始化 | 247–290 | 所有 session state 預設值（含 `pending_ocr_items`） |
| 單字 CRUD | 292–353 | path, sync, update, save, delete |
| 句型 CRUD | 355–415 | catalogs, sentences, shared_vocab, progress |
| 統計更新 | 420–545 | `update_user_stats_summary`, `save_user_sentence_progress`, `clear_user_sentence_history` |
| AI 處理 | 545–855 | `normalize_text`, `check_audio_batch`, `call_gemini_to_complete`, `call_gemini_ocr` |
| 篩選/抽題/SRS | 857–975 | 課程選項、篩選、`sample_by_accuracy`、`compute_srs_update`、`get_due_words`、`sample_for_review` |
| 練習時長追蹤 | 979–998 | `track_practice_time`, `save_practice_time` |
| 句型篩選 | 1000–1015 | `get_sentence_category_options`, `filter_sentence_data` |
| UI 工具 | 1015–1095 | keyboard, focus, TTS, progress bar |
| 導航回調 | 1095–1108 | navigate callbacks |
| 登入邏輯 | 1109–1136 | `attempt_login` |
| 側邊欄 UI | 1138–1285 | 登入表單、自助註冊、Premium 狀態、選單、密碼修改、付款通知 |
| CSS 注入 | 1286–1325 | Expander 按鈕偽裝樣式 |
| 儀表板 | 1329–1650 | 4 Tab：戰績表、排行榜、單字統計、句型統計 |
| 單字管理 | 1654–1940 | 5 Tab：✨ AI 輸入、修改、刪除、📂 CSV 匯入/匯出、📥 公用單字集 |
| 單字練習 | 1945–2150 | 3 Tab：快閃、測驗（SRS）、連連看 + 練習時長追蹤 |
| 句型口說 | 2153–2300 | 題庫選擇（Premium 門控）、JS drill 元件嵌入（drill_component.py）、練習時長追蹤 |
| 頁尾 | 2302–2304 | 分隔線 + 版權 |

### admin_app.py (753 行)

| 區段 | 行號 | 內容 |
|------|------|------|
| 設定 & Firestore | 1–74 | 環境選擇、DB 連線、shared_vocab 路徑常數 |
| 工具函式 | 76–114 | hash, users, books, sentences |
| UI 介面 | 118–121 | 側邊欄選單（6 項功能） |
| 學生帳號管理 | 129–220 | 新增、編輯、刪除、使用者一覽（含 plan） |
| 訂閱管理 | 224–330 | 總覽表、開通 Premium（天數選擇 + 到期延長）、取消 Premium |
| AI 用量統計 | 333–406 | 總覽 Metric、預估費用、每日趨勢、各使用者用量、完整明細 |
| 匯入句型書 | 411–481 | CSV 匯入（含 is_premium checkbox） |
| 編輯句型書 | 486–675 | Premium 切換、data_editor、刪除/儲存 |
| 管理公用單字集 | 680–753 | 上傳 CSV、管理/刪除已有 set |

---

## 8. 核心演算法

### 8.1 語音辨識流程 (`check_audio_batch`)

```
輸入: audio_file, template, options_list
輸出: { correct_options, heard, feedback }

1. 讀取 pronunciation_feedback_prompt.md 作為 Prompt
2. 音訊 Base64 編碼
3. 嘗試 Gemini 多模態 API
   - 成功且 correct_options 非空 → 回傳
4. Fallback: SpeechRecognition
   - Google STT 轉錄
   - normalize_text 後字串比對
   - 有結果 → 回傳
5. 全部失敗 → 回傳空結果
```

### 8.2 抽題策略 (`sample_by_accuracy`)

```python
accuracy = -1 if Total == 0 else Correct / Total
# 排序後取前 N 個（正確率最低的優先）
```

### 8.3 SRS 間隔重複演算法 (`compute_srs_update`)

SM-2 變體實作：

```python
# 答對：
streak += 1
if streak == 1: new_interval = 1
elif streak == 2: new_interval = 6
else: new_interval = round(interval * ease)
ease = max(1.3, ease + 0.1)

# 答錯：
streak = 0
new_interval = 1
ease = max(1.3, ease - 0.2)

due_date = today + timedelta(days=new_interval)
```

**抽題優先順序（`sample_for_review`）：**
1. SRS 到期的單字（`srs_due <= today` 或 `srs_due` 為空）
2. 按 `srs_due` 排序（最早到期優先）
3. 不足 N 題時用 `sample_by_accuracy()` 從非到期單字中補充

### 8.4 OCR 圖片辨識 (`call_gemini_ocr`)

```
輸入: image_files[], course_name, course_date
輸出: list[dict]  # 結構化單字列表

1. 讀取 system_prompt.md 作為基礎 Prompt
2. 前置「辨識圖片中的英文單字」指令
3. 每張圖片 Base64 編碼 → inline_data (mime_type 自動偵測)
4. 送 Gemini 多模態 API（timeout=60，較文字模式長）
5. 解析 pipe-delimited 回應 → 結構化 dict（含 SRS 預設值）
6. record_ai_usage("vocab", token_count)
```

### 8.5 智慧跳轉

```python
# 偵測篩選簽名變更 → 遍歷句型 → 跳到第一個未全部完成的
```

### 8.6 排行榜排序

```python
sorted(students, key=lambda x: (-x['rate'], -x['completed']))
```

### 8.7 Premium 到期判定

```python
def is_premium(user_info):
    return plan == "premium" and plan_expiry.date() >= today
```

### 8.8 練習時長追蹤 (`track_practice_time`)

```python
# 進入練習頁面時記錄 practice_start_time = time.time()
# 離開或頁面重載時計算 duration = now - start_time
# 累加至 Firestore user 文件的 practice_duration.{today} 欄位
# 使用 firestore.Increment(seconds) 避免併發覆蓋
```

---

## 9. 邊界情況處理

### 已處理

| 情況 | 處理方式 |
|------|----------|
| Firestore 連線失敗 | `get_db()` 返回 None |
| 無使用者資料 | `init_users_in_db` 初始化預設帳號 |
| 無單字資料 | `sync_vocab_from_db(init_if_empty=True)` |
| 索引越界 | `% len(current_set)` 循環 |
| API 超時 | 文字 AI `timeout=30`、OCR `timeout=60` + exception |
| 空輸入 | `if not text.strip()` |
| Firestore 批次限制 | 每 400 筆 commit |
| 密碼錯誤 | 顯示錯誤訊息 |
| Timestamp 格式 | `hasattr(x, 'date')` |
| 舊用戶無 plan 欄位 | `.get("plan")` 預設 `"free"` |
| Premium 到期 | `is_premium()` 即時比對日期 |
| 單字補全行數過多 | 超 100 行警告阻擋 |
| 免費用戶 AI 額度用完 | 顯示升級提示阻擋 |
| AI 用量寫入失敗 | `try-except pass` 靜默 |
| 註冊名稱重複 | Firestore 查詢確認 |
| 註冊密碼過短 | ≥4 碼檢查 |
| 7 天試用到期 | `is_premium()` 自動降級 |
| 付費句型書免費用戶 | 🔒 圖示 + `st.stop()` |
| 舊句型書無 is_premium | `.get("is_premium", False)` |
| S999 測試帳號 | 學號產生時排除 |
| CSV 匯入重複單字 | 比對 English 欄位（case-insensitive），跳過重複 |
| OCR 圖片過大 | 每張上限 10MB，超過顯示警告 |
| SRS 欄位型態不一致 | `int()` / `float()` 強制轉換舊資料 |
| 公用單字集匯入重複 | 同 CSV 匯入，比對 English 欄位跳過 |
| 舊單字無 SRS 欄位 | `.get('srs_interval', 0)` 等預設值，向後相容 |
| iOS iframe 麥克風 | JS 動態加 `allow="microphone; autoplay"` |
| iOS TTS 無聲 | user gesture 同步播放靜音語音解鎖 + 8s timeout |
| iOS SR 不可用 | `_srAvailable` 旗標，預篩只在 SR 可用時啟用 |
| 危險操作誤觸 | 所有刪除/清除用 `@st.dialog` 二次確認 |
| 排行榜無資料 | `fix_sentence_stats.py` 修復腳本重建 |

### 潛在風險

| 情況 | 風險 |
|------|------|
| 並發寫入 | 同一使用者多裝置同時操作可能覆蓋 |
| Gemini API 配額超限 | 有每分鐘請求限制 |
| 大量資料載入 | 無分頁機制 |
| 網路中斷 | 無離線模式 |
| Cookie 明文密碼 | 瀏覽器 Cookie 儲存原始密碼 |
| 公用單字集資料過大 | 單一文件 words 陣列可能超過 Firestore 1MB 限制 |

---

## 10. 安全性分析

| 項目 | 現狀 | 風險 |
|------|------|------|
| 密碼儲存 | SHA-256 雜湊（無 salt） | 中 |
| Cookie 儲存 | **明文密碼**存入 Cookie | 高 |
| API Key | `st.secrets` 管理 | 低 |
| Firestore 規則 | 未在程式碼中體現 | 待確認 |
| 輸入驗證 | 基本空值+長度檢查 | 中 |
| LINE Token | `st.secrets` 管理 | 低 |

---

## 11. 已知限制

1. **單檔架構：** 主程式 2,304 行集中在單一檔案
2. **無單元測試：** 核心函式無測試覆蓋
3. **無離線支援：** 完全依賴網路
4. **無分頁：** 大量單字全量載入
5. **管理後台獨立運行：** admin_app.py 需另外啟動
6. **Cookie 明文密碼：** 安全風險
7. **Magic Numbers：** batch 400、TTL 600 散落各處
8. **Firestore 路徑硬編碼：** 路徑字串散布多處
9. **iOS 主畫面圖示：** Streamlit Cloud 限制
10. **手動開通 Premium：** 尚未串接金流

---

## 12. 未來規劃

- [ ] 20+ 付費用戶後串接藍新定期定額自動扣款
- [ ] 根據 `ai_usage` token 數據決定是否需要對語音辨識設限
- [ ] 錯題本：收集常錯單字重點練習
- [ ] 學習提醒：推播通知
- [ ] 社交功能：好友 PK
- [ ] 成就系統：學習獎章
- [ ] 自訂題庫：使用者自建句型
- [ ] 增量同步：減少 Firestore 讀取量
- [ ] 分頁載入：大資料集性能
- [ ] PWA 離線快取
