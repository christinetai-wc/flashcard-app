# CLAUDE.md

## 專案概述
Flashcard Pro 雲端版 — 台灣國中小英語學習平台（Streamlit + Firestore + Gemini AI）

## 開發指令
```bash
# 學生端
streamlit run streamlit_app.py
# 管理後台
streamlit run admin_app.py
```

## Secrets（.streamlit/secrets.toml）
`GEMINI_API_KEY`, `APP_ID`（預設 "flashcard-pro-v1"）, `LINE_CHANNEL_ACCESS_TOKEN`, `LINE_TEACHER_USER_ID`, `[firebase_credentials]`

## Firestore 路徑常數
```
artifacts/{APP_ID}/public/data/users/{user_name}           # 使用者帳號
artifacts/{APP_ID}/public/data/sentences/{dataset_id}       # 句型書目錄
artifacts/{APP_ID}/public/data/{dataset_id}/{doc_id}        # 句型題目
artifacts/{APP_ID}/public/data/shared_vocab/{set_id}        # 公用單字集目錄
artifacts/{APP_ID}/public/data/shared_vocab_data/{set_id}   # 公用單字集資料（單一文件 words 陣列）
artifacts/{APP_ID}/users/{student_id}/vocabulary/{doc_id}   # 個人單字庫
artifacts/{APP_ID}/users/{student_id}/sentence_progress/{md5}  # 句型進度
```

## streamlit_app.py 模組結構
| 區段 | 行號（約略） | 內容 |
|------|-------------|------|
| 設定與常數 | 1-70 | import、API 常數、Firestore、Cookie、免費方案限制 |
| 工具函式 | 70-170 | hash、is_premium、check_vocab_ai_usage、record_ai_usage、register_new_user |
| 資料庫 CRUD | 170-400 | 單字/句型/進度/公用單字集的讀寫函式 |
| AI 函式 | 400-850 | call_gemini_to_complete、call_gemini_ocr、check_audio_batch、TTS、鍵盤橋接 |
| 登入與 UI | 850+ | 側邊欄、儀表板、單字管理（5 tab）、單字練習（3 tab）、句型口說 |

## 單字管理 Tab 結構
✨ AI 輸入 → 手動修改 → 單字刪除 → 📂 CSV 匯入/匯出 → 📥 公用單字集

## 關鍵慣例
- **Gemini API**：`POST {GEMINI_API_URL}?key={API_KEY}`，multimodal 用 `inline_data`（audio/image）
- **Gemini 回應格式**：pipe-delimited `Word | POS | Chinese_1 | Chinese_2 | Example`，prompt 在 `system_prompt.md`
- **Firestore 批次寫入**：上限 400 筆/commit（`save_new_words_to_db` 已處理）
- **快取 TTL**：`@st.cache_data(ttl=600)` 用於少變動的集合（users、sentences、shared_vocab）
- **Session State**：Streamlit 每次互動重跑，用 `st.session_state` 持久化；widget 用 `key=` 綁定
- **SRS 欄位**：單字含 `srs_interval`, `srs_ease`, `srs_due`, `srs_streak`, `srs_last_review`
- **Premium 判定**：`is_premium(user_info)` 比對 `plan` + `plan_expiry`，舊用戶無欄位預設 free
- **免費方案限制**：`FREE_DAILY_VOCAB_AI_LIMIT=3`（AI 輸入 + OCR 共用），`VOCAB_AI_MAX_LINES=100`
- **密碼**：SHA-256 雜湊存 Firestore
- **繁體中文**：所有 UI 文字、POS 詞性、釋義均使用繁體中文

## 句型口說元件（drill_component.py）
- **架構**：自包含 JS 元件，透過 `st.components.v1.html()` 嵌入 iframe
- **流程**：按開始 → TTS 示範 → 錄音 + VAD → 預篩 → AI 判讀 → 回饋 → 下一個選項
- **預篩**：SpeechRecognition 沒辨識到文字 → 不送 Gemini（省 token，防小孩亂按）
- **AI 降級**：429/404 時降級：gemini-2.5-flash → gemini-2.0-flash → 瀏覽器語音辨識（同一段錄音不重唸）
- **Firestore 寫入**：JS 直接用 REST API 寫入（Python 產生短期 access token 傳給 JS）
- **逐題存入**：每個 option 通過後即時寫入 `completed_options`，中途離開不丟進度；續練時跳過已完成
- **Token 記錄**：Gemini `usageMetadata.totalTokenCount` 寫入 `ai_usage.speech.{date}`（與 Python 同結構）
- **深色模式**：偵測父頁面 `document.body` 背景色亮度，動態切換 CSS class
- **進度格式**：`completion_count`（輪數）+ `rounds` 陣列（每輪詳細結果）

## Gotchas
- 新用戶 `sync_vocab_from_db(init_if_empty=False)` — 不自動建立預設單字
- `pending_items`（文字 AI）和 `pending_ocr_items`（圖片 OCR）分開存，避免互相覆蓋
- Timestamp 欄位需檢查 `hasattr(x, 'date')` 處理不同格式
- 句型進度 Document ID = Template 的 MD5 hash
- 舊資料向後相容：`.get("plan")` 預設 `"free"`、`.get("is_premium", False)` 預設免費
- 公用單字集資料存為單一文件（words 陣列 ~200KB），不是每個單字一個文件
- 句型口說完成一輪後 `completed_options` 重置為空，儀表板以 `completion_count` 為準

## 詳細規格
完整資料模型、設計決策、邊界處理、安全分析等詳見 `SPEC.md`
