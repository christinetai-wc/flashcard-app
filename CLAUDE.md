# CLAUDE.md

## 專案概述
Flashcard Pro 雲端版 — 台灣國中小英語學習平台（Streamlit + Firestore + Cloud Function + Gemini AI）

## 開發指令
```bash
# 學生端
streamlit run streamlit_app.py
# 管理後台
streamlit run admin_app.py
# 學生報告（CLI）
python student_report.py <學生名稱> [--ai]
# Cloud Function 部署
firebase deploy --only functions --project flashcard-app-9dd69 --force
```

## Secrets（.streamlit/secrets.toml）
`GEMINI_API_KEY`, `GEMINI_PROXY_URL`, `PROXY_SECRET`, `APP_ID`（預設 "flashcard-pro-v1"）, `LINE_CHANNEL_ACCESS_TOKEN`, `LINE_TEACHER_USER_ID`, `[firebase_credentials]`

## Firestore 路徑常數
```
artifacts/{APP_ID}/public/data/users/{user_name}           # 使用者帳號
artifacts/{APP_ID}/public/data/sentences/{dataset_id}       # 句型書目錄
artifacts/{APP_ID}/public/data/{dataset_id}/{doc_id}        # 句型題目
artifacts/{APP_ID}/public/data/shared_vocab/{set_id}        # 公用單字集目錄
artifacts/{APP_ID}/public/data/shared_vocab_data/{set_id}   # 公用單字集資料（單一文件 words 陣列）
artifacts/{APP_ID}/users/{student_id}/vocabulary/{doc_id}   # 個人單字庫
artifacts/{APP_ID}/users/{student_id}/sentence_progress/{md5}  # 句型進度
artifacts/{APP_ID}/users/{student_id}/sentence_progress/{md5}/rounds/round_{n}  # 每輪詳細結果
artifacts/{APP_ID}/users/{student_id}/reports/{date}        # 練習報告（content=家長版, student_content=學生版）
artifacts/{APP_ID}/users/{student_id}/drill_logs/{session_id}  # 口說練習 log
artifacts/{APP_ID}/error_logs/{auto_id}                     # 錯誤紀錄
```

## 檔案結構
| 檔案 | 用途 |
|------|------|
| streamlit_app.py | 主程式（學生端） |
| admin_app.py | 管理後台 |
| drill_component.py | 句型口說 JS 元件 |
| student_report.py | 學生報告工具（CLI） |
| functions/index.js | Cloud Function — Gemini API proxy |
| firebase.json | Firebase 部署設定 |
| .firebaserc | Firebase 專案綁定（flashcard-app-9dd69） |

## Cloud Function Proxy 架構
- **用途**：JS 端呼叫 Gemini API 的安全代理，API key 不暴露於前端
- **URL**：`GEMINI_PROXY_URL`（存在 Streamlit secrets）
- **驗證**：HMAC token（Python 產生，`PROXY_SECRET` 共享密鑰，有效期 1 小時）
- **防護**：CORS 限制 + HMAC 驗證 + Gemini API 30 RPM 配額
- **Thinking**：已關閉（`thinkingBudget: 0`），大幅降低 token 費用
- **降級**：gemini-2.5-flash → gemini-2.0-flash → 瀏覽器語音辨識

## streamlit_app.py 模組結構
| 區段 | 行號（約略） | 內容 |
|------|-------------|------|
| 設定與常數 | 1-80 | import、API 常數、Firestore、Cookie、免費方案限制 |
| 工具函式 | 80-180 | hash、is_premium、check_vocab_ai_usage、log_error、register_new_user |
| 資料庫 CRUD | 180-650 | 單字/句型/進度/公用單字集的讀寫函式 |
| AI 函式 | 650-1000 | call_gemini_to_complete、call_gemini_ocr、check_audio_batch、TTS、鍵盤橋接 |
| SRS 核心 | 1000-1120 | compute_srs_update、get_due_words、sample_for_review、練習時間追蹤 |
| 鼓勵語 | 1200-1250 | _generate_encouragement（根據練習數據，不呼叫 AI） |
| 登入與 UI | 1280+ | 側邊欄、首頁（報告）、儀表板、單字管理、單字練習、句型口說、後台管理 |

## 功能選單結構
首頁（學生版報告）→ 學習儀表板 → 單字管理 → 單字練習 → 句型口說 → ⚙️ 後台管理（admin only）

## 關鍵慣例
- **Gemini API（Python 端）**：`POST {GEMINI_API_URL}?key={API_KEY}`，帶 `GEMINI_HEADERS`（Referer）+ `thinkingConfig: {thinkingBudget: 0}`
- **Gemini API（JS 端）**：透過 Cloud Function proxy，帶 HMAC token，不直接帶 API key
- **Gemini 回應格式**：pipe-delimited `Word | POS | Chinese_1 | Chinese_2 | Example`，prompt 在 `system_prompt.md`
- **Firestore 批次寫入**：上限 400 筆/commit（`save_new_words_to_db` 已處理）
- **Firestore 原子操作**：ai_usage、completion_count、practice_time 都用 `fieldTransforms.increment`
- **快取 TTL**：`@st.cache_data(ttl=600)` 用於少變動的集合（users、sentences、shared_vocab）
- **Session State 單次快取**：`fetch_all_user_sentence_progress` 每次 rerun 只讀一次 Firestore
- **SRS 欄位**：單字含 `srs_interval`, `srs_ease`, `srs_due`, `srs_streak`, `srs_last_review`
- **Premium 判定**：`is_premium(user_info)` 比對 `plan` + `plan_expiry`，舊用戶無欄位預設 free
- **免費方案限制**：`FREE_DAILY_VOCAB_AI_LIMIT=3`（存 Firestore ai_usage.vocab_count），`FREE_DAILY_DRILL_LIMIT=30`
- **密碼**：SHA-256 雜湊存 Firestore
- **Cookie**：存 session_token（隨機 64 hex），不存明文密碼
- **登入**：selectbox 選單（隱藏 admin，`?admin=1` 可顯示），Cookie 自動登入用 session token 比對
- **繁體中文**：所有 UI 文字、POS 詞性、釋義均使用繁體中文
- **錯誤紀錄**：重要錯誤（Gemini API 失敗）寫入 Firestore error_logs，一般錯誤 print 到 Streamlit Logs

## 句型口說元件（drill_component.py）
- **架構**：自包含 JS 元件，透過 `st.components.v1.html()` 嵌入 iframe
- **流程**：按開始 → TTS 示範 → 錄音 + VAD → 預篩 → Cloud Function proxy → AI 判讀 → 回饋 → 下一個選項
- **語速控制**：慢(0.5) / 中(0.85) / 快(1.0)，存入 Firestore `tts_rate`
- **預篩**：需要連續 3 幀（150ms）音量超過門檻才算說話，避免 TTS 殘餘音誤觸發
- **免費額度**：每日 30 次 AI 判讀，用完自動切語音辨識模式
- **動態 VAD**：錄音前偵測 0.5 秒底噪，門檻 = max(12, 底噪×1.5)；最長錄音 10 秒
- **Firestore 寫入**：JS 直接用 REST API 寫入（Python 產生短期 access token 傳給 JS）
- **原子操作**：completion_count 用 increment，rounds 寫入獨立 subcollection，ai_usage 用 increment
- **逐題存入**：每個 option 通過後即時寫入 `completed_options`，中途離開不丟進度
- **排行榜更新**：sentence_stats.completed 用 increment，只在 completion_count 從 0→1 時 +1
- **完成後**：顯示「🔄 再來一次」按鈕
- **Token**：HMAC token（有效期 1 小時，與 Firestore token 同步）

## 練習報告系統
- **家長版**（每週）：7 個區塊（統計 → 苦戰單字 → 發音弱點 → 強項 → 進步觀察 → 建議 → 總評）
- **學生版**（每天）：三明治溝通法（🌟 讚美 → 💡 小提醒 → 🔥 鼓勵），150-250 字
- **存放**：`reports/{date}` 文件，`content`（家長版）+ `student_content`（學生版）
- **顯示**：首頁顯示學生版，後台顯示兩版（tab 切換）
- **產生**：由 Claude 手動撰寫存入 Firestore，觸發詞「幫 XX 產家長版/學生版報告」
- **風格**：不用「今天」「明天」，用日期或「下次」；學生版不用音標，用類比法

## UI/UX
- **確認對話框**：所有危險操作用 `@st.dialog` 二次確認
- **手機適配**：全域 CSS 注入（縮減 padding、按鈕最小高度 44px、表格水平捲動）
- **iOS Safari**：iframe 需 `allow="microphone; autoplay"`；TTS 需 user gesture 解鎖
- **排行榜**：當前使用者黃色底色高亮，前 5 名，時間顯示台灣時區
- **鼓勵語**：側邊欄登入後顯示（連續天數、練習時長、句型進度），與 SRS 複習提醒合併
- **註冊**：註冊成功自動登入 + toast 提示

## 後台（admin_app.py）
- **學生詳情**：自動補正 practice_time（從 drill logs 計算）
- **練習報告**：家長版 / 學生版 tab 顯示

## Gotchas
- 新用戶 `sync_vocab_from_db(init_if_empty=False)` — 不自動建立預設單字
- `pending_items`（文字 AI）和 `pending_ocr_items`（圖片 OCR）分開存，避免互相覆蓋
- Timestamp 欄位需檢查 `hasattr(x, 'date')` 處理不同格式
- 句型進度 Document ID = Template 的 MD5 hash
- 舊資料向後相容：`.get("plan")` 預設 `"free"`、`.get("is_premium", False)` 預設免費
- 公用單字集資料存為單一文件（words 陣列 ~200KB），不是每個單字一個文件
- 句型口說完成一輪後 `completed_options` 重置為空
- `sentence_stats` 的 `completed` 用原子 increment，只在新句型時 +1
- user_info 為 None 時自動登出（防止 NameError）
- 自動登入延遲 sync_vocab（不在 rerun 前做，避免浪費讀取）
- Drill 頁面合併 Firestore 讀取（get_drill_remaining + tts_rate 共用一次讀取）

## 詳細規格
完整資料模型、設計決策、邊界處理、安全分析等詳見 `SPEC.md`
