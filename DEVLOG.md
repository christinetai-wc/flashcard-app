# Development Log

## 2026-03-26

### 句型口說麥克風錯誤提示改善
- 麥克風權限被拒時，顯示黃色提示框列出 iPhone/Android/電腦三種裝置的操作步驟
- 原本只顯示一行錯誤訊息，學生不知道怎麼修

## 2026-03-25

### Cloud Function 加 LINE 即時通知
- Gemini API 異常（401/403/429 或例外）時自動 LINE 推播通知老師
- 10 分鐘冷卻機制，避免同一問題連續洗版
- LINE secrets（`LINE_CHANNEL_ACCESS_TOKEN`、`LINE_TEACHER_USER_ID`）加入 Firebase Secret Manager

### Gemini API Key 更新
- 舊 key 過期（API key expired），第二把 key 超過 spending cap
- 更新第三把 key 到 secrets.toml 和 Firebase Functions secrets
- 重新部署 Cloud Function

### 新增 check_activity.py — 學生練習狀況檢查工具
- 顯示每位學生今日/最近口說紀錄、句型進度
- 偵測近 7 天異狀：API 額度用完、API 錯誤、麥克風錯誤、空白錄音、降級語音辨識
- 發現 Leticia 麥克風完全無法使用（46 次錯誤）、Vanessasu88 全程語音辨識模式（API 額度用完 14 次）

### 學生版報告
- 產生語晰、Vanessasu88 的 3/25 學生版報告並存入 Firestore

### OpenAI 遷移計畫（暫緩）
- 評估從 Gemini 遷移到 OpenAI API（可設月度消費硬上限）
- OpenAI gpt-4o-mini-audio-preview 支援音檔直接輸入（含 webm），不需轉格式
- 計畫存於 `.claude/plans/compiled-honking-music.md`，等低峰期執行

## 2026-03-19 ~ 2026-03-20

### 例句連連看拖拉配對
- 新增 `match_component.py` — 自訂 JS 拖拉配對元件（取代 selectbox 下拉選單）
- 支援桌面拖拉 + 手機觸控（拖曳影子 + drop zone 偵測）
- 點已放的字可移除重放
- 提交後 JS 直接用 Firestore REST API 更新 SRS（srs_due、interval、ease、streak）+ Correct/Total
- 換題時排除上一輪出過的單字，改用隨機抽（不再優先低正確率）
- 嘗試 streamlit-sortables 和 antd-components，都不適合配對場景，最終自寫 JS

### 學生報告系統
- 後台學生詳情改為只顯示報告（家長版 + 學生版 tab），移除產生按鈕
- 報告由 Claude 手動撰寫存入 Firestore，不依賴 Gemini API
- 新增所有有練習紀錄學生的學生版報告（Neo、語晰、Beef noodle master、翎晞）
- 修正報告中「今天/明天」改為日期或「下次」

### 文件更新
- CLAUDE.md 全面更新（Cloud Function、報告系統、Cookie 安全、新功能）
- DEVLOG.md 補齊 3/16~3/17 所有改動紀錄

## 2026-03-16 ~ 2026-03-17

### 資安修復：API Key 暴露事件
- 舊 Gemini API key（...ViMmmw）被 Google 偵測為公開暴露並停用
- 被盜用產生約 $35 USD 異常費用（合法用量僅 $0.19）
- 新增 Firebase Cloud Function（`functions/index.js`）作為 Gemini API proxy
- JS 端改呼叫 proxy URL，API key 只存在 Cloud Function 環境變數
- HMAC token 驗證（PROXY_SECRET + timestamp，有效期 1 小時）
- 關閉 Gemini 2.5 Flash thinking（`thinkingBudget: 0`），token 費用降低約 63%
- Gemini API 設定 30 RPM 配額限制

### 資安修復：Cookie 安全
- Cookie 從存明文密碼改為存隨機 session token（64 hex）
- 登入時產生 token 存入 Firestore + Cookie
- 登出時清除 Firestore session_token + Cookie
- 自動登入改用 session token 比對

### 架構修復
- JS ai_usage 改用 fieldTransforms.increment（原子操作），解決與 Python 端雙寫衝突
- saveRoundToFirestore 改原子操作：completion_count 用 increment，rounds 改為 subcollection
- updateSentenceStats completed 改用 increment，只在新句型（count=1）時 +1
- 排行榜 completed 計數修正（從 sentence_progress 實際重算）
- 排行榜刷新改用精確清除 `fetch_users_list.clear()`（不再 `st.cache_data.clear()`）
- 單字 AI 額度改存 Firestore（ai_usage.vocab_count），重新整理不再重置
- Drill 頁面合併重複 Firestore 讀取（get_drill_remaining + tts_rate 共用一次）
- fetch_all_user_sentence_progress 同一次 rerun 只讀一次
- 自動登入延遲 sync_vocab（避免浪費的 Firestore 讀取）
- user_info 為 None 時自動登出（防止 NameError）
- get_drill_remaining 從 Firestore 再確認 premium 狀態 + 安全網

### 錯誤處理
- 替換所有 `except: pass` 為 `log_error()`
- 重要錯誤（Gemini API 失敗）寫入 Firestore error_logs
- 一般錯誤 print 到 Streamlit Logs

### 新功能
- 句型口說新增語速調整按鈕（慢 0.5 / 中 0.85 / 快 1.0），存 Firestore 跨 session
- 登入改用 selectbox 可選可搜尋，admin 隱藏（?admin=1 顯示）
- 註冊成功自動登入 + toast 提示
- 登入側邊欄新增鼓勵語（連續天數、練習時長、句型進度）
- 排行榜 UTC 轉台灣時間、只顯示前 5 名、移除 👈 標記
- 句型口說練完一輪顯示「🔄 再來一次」按鈕
- 新增「首頁」顯示學生版每日報告
- 後台學生詳情自動補正 practice_time（從 drill logs 計算）
- 後台學生詳情顯示練習報告（家長版 + 學生版 tab）
- 新增 student_report.py 學生報告工具（CLI + Gemini AI 分析）
- VAD 防止 TTS 示範音誤判（清空 buffer + 連續幀判定 + 無語音不送 AI）

### GAS LINE Bot 重構（expense_tracker）
- Code.gs 精簡為路由，依「群組清單」備註欄分派（FlashCard/分帳/自學）
- 新增 LineApi.gs / ExpenseTracker.gs / MedicineReminder.gs / FlashCard.gs / Save2GoogleDrive.gs
- FlashCard.gs：家長群自動記錄 LINE User ID 到「FlashCard名單」sheet
- 自動記錄所有群組 ID + 使用者 ID 到 sheet
- 移除舊 Flask 版 Python 代碼
- Save2GoogleDrive.gs 加入 YouTube 上傳功能（不公開，Brand Account 不支援 GAS，保留 Drive fallback）

## 2026-03-15

### 口說練習 UI 調整
- 移除 AI 回饋閃現框，只保留下方歷史紀錄
- 錄音中新增「✋ 我說完了」手動停止按鈕（VAD + 手動雙軌）

### 練習時間追蹤修復
- Python `save_practice_time()` 改用 `firestore.Increment(delta)`，避免覆蓋
- JS drill 元件新增計時：`startDrill` 記錄開始時間，每個 option 通過後定期存入，完成一輪存剩餘
- JS 使用 Firestore `commit` API 的 `fieldTransforms.increment`（原子操作）

### Gemini API key 網域限制
- GCP Console 設定 HTTP Referrer 限制（僅允許 `flashcard-techeasy.streamlit.app` 和 `localhost:8501`）
- Python server 端所有 Gemini 請求加上 `Referer` header
- `expense_tracker` 和 `meeting-notes` 改用另一把無限制的 key

## 2026-03-14

### 句型口說全面重構 — JS 自包含元件
- 移除舊版 Streamlit 錄音 + check_audio_batch 架構，改為 `drill_component.py` 產生的 JS 元件
- 流程：按開始 → TTS 示範 → 錄音 + VAD 自動偵測說完 → AI 判讀 → 回饋，全程不離開 iframe
- JS 直接呼叫 Gemini API（不經 Streamlit rerun），完成後直接用 Firestore REST API 寫入成績
- Python 產生短期 service account access token 傳給 JS

### AI 多模型降級策略
- 僅 429（額度不足）或 404（模型不存在）時觸發降級
- 順序：gemini-2.5-flash → gemini-2.0-flash → 瀏覽器 SpeechRecognition 文字比對
- 同一段錄音不需要重唸，錄音時同步啟動 SpeechRecognition 備用
- 其他錯誤不降級，直接回報失敗

### Firestore 資料結構更新
- `sentence_progress` 新增 `completion_count`（輪數）和 `rounds` 陣列（每輪結果含 tries/transcript/feedback）
- `completed_options` 每輪完成後重置為空
- 執行 `cleanup_sentence_progress.py` 清除舊格式資料

### 儀表板更新
- 句型練習 tab：移除「選項數」「已完成」「狀態」欄，改為「輪數」「熟練度」
- 指標改為：總句數 / 已練習 / 累計輪數
- 個人戰績表 stacked bar：改用 completion_count（3輪以上=熟練，1~2輪=練習中）
- 智慧跳轉改用 `completion_count == 0` 判斷未練習

### 深色模式適配
- JS 元件偵測父頁面 body 背景色亮度，動態切換 dark/light CSS class
- 所有文字、選項、回饋區顏色依主題自動調整

### 判讀規則調整
- 必須嘗試完整句子（只唸目標字不算通過）
- 仍寬容冠詞替換、時態變化、發音不完美
- 回饋語言：繁體中文

### 語音預篩（省 token）
- SpeechRecognition 沒辨識到文字時不送 Gemini，直接提示重唸
- 防止小孩亂按或無效錄音浪費 API 額度

### 逐題存入進度
- 每個 option 通過後即時寫入 `completed_options` 到 Firestore
- 中途離開再回來可續練（跳過已完成的 option，按鈕顯示「繼續練習」）
- `completion_count` 仍等全部練完才 +1

### AI Token 使用量記錄
- JS 從 Gemini 回應 `usageMetadata.totalTokenCount` 取得 token 數
- 寫入 Firestore `ai_usage.speech.{date}`，與 Python 端 `record_ai_usage` 同結構

### 免費用戶每日 AI 判讀限制
- `FREE_DAILY_DRILL_LIMIT=30`，用完自動切語音辨識模式（仍可練習不花 token）
- `ai_usage.drill_count.{date}` 記錄每日判讀次數
- token 與 drill_count 合併一次讀寫 Firestore，避免 updateMask 互相覆蓋

### 動態 VAD 門檻 + 最長錄音
- 錄音前偵測 0.5 秒環境底噪，門檻 = max(12, 底噪×1.5)，解決噪音環境斷不了句
- 最長錄音 10 秒保底

### iOS Safari 相容性修復
- 移除未使用的 `streamlit_mic_recorder` import（造成 Streamlit Cloud 崩潰）
- iframe 加 `allow="microphone; autoplay"` 屬性（JS 動態設定）
- TTS 需 user gesture 解鎖：`startDrill()` 同步播放靜音語音 + 8 秒 timeout fallback
- SpeechRecognition 在 Safari iframe 不可用，加 `_srAvailable` 旗標，預篩只在 SR 可用時啟用

### UI/UX 改善
- 所有危險操作加 `@st.dialog` 確認對話框（6 處：刪除使用者、取消 Premium、刪除句型、刪除單字集、清除句型進度、刪除單字）
- 全域手機適配 CSS（縮減 padding、按鈕最小高度 44px、表格水平捲動）
- 刪除按鈕不使用 `type="primary"`，與儲存/確認按鈕視覺區分
- 排行榜當前使用者黃色底色高亮 + 👈 標記
- 測驗滿分 `st.balloons()`、連連看全對 `st.balloons()`
- 口說完成一輪 emoji confetti 動畫
- 儀表板指標從 4 欄改為 2×2 排列

### 排行榜統計修復
- JS `updateSentenceStats()` — 完成一輪後更新 user doc 的 `sentence_stats.{datasetId}`
- 首次完成（`completionCount === 0`）才遞增 `completed` 計數
- 新增 `fix_sentence_stats.py` 修復腳本 — 從 `sentence_progress` 重建所有使用者的排行榜統計

## 2026-03-08

### CSV 匯入/匯出 + 重複檢查
- CSV 匯入新增重複檢查（比對 English 欄位，跳過已存在的單字）
- 新增 CSV 匯出功能（`st.download_button`，UTF-8 BOM 編碼）
- Tab 改名「📂 CSV 匯入/匯出」，內部分匯入/匯出子 tab

### 合併 AI 輸入 Tab
- 「批次輸入」+「拍照辨識」合併為「✨ AI 輸入」
- `st.radio` 切換三種模式：文字輸入 / 拍照 / 上傳圖片
- 課程選擇、額度檢查、預覽儲存流程共用，消除重複程式碼

### OCR 拍照辨識
- 新增 `call_gemini_ocr()` — Gemini 2.5 Flash 視覺辨識課本圖片中的英文單字
- 支援 `st.camera_input`（手機拍照）和 `st.file_uploader`（多張上傳）
- 與文字補全共用免費方案每日 3 次額度
- 圖片大小檢查（上限 10MB）

## 2026-03-07

### 公用單字集（Firestore 架構）
- 新增 Firestore 雙集合：`shared_vocab`（目錄 metadata）+ `shared_vocab_data`（單字資料，單一文件存 words 陣列）
- admin_app.py：新增「📚 管理公用單字集」— 上傳 CSV、管理/刪除已有 set
- streamlit_app.py：新增「📥 公用單字集」tab — 瀏覽、篩選、一鍵匯入（含重複檢查）
- 國小 1200 單字 PDF 解析（pdftotext -layout + regex），1968 個單字
- 刪除本地 `shared_vocab/` 目錄，資料改由 admin 上傳到 Firestore

### 練習時長追蹤
- 新增 `track_practice_time()` — 記錄單字練習和句型練習的持續時間
- 資料存入 Firestore user 文件的 `practice_duration` 欄位

### SRS 間隔重複演算法
- 實作 SM-2 變體：`srs_interval`, `srs_ease`, `srs_due`, `srs_streak`, `srs_last_review`
- 實力測驗優先抽到期的 SRS 單字
- 答對/答錯更新 SRS 參數

### Bug 修復
- 修正例句連連看選項判斷邏輯
- 修正 SRS 欄位型態錯誤（string vs int）
- 付款說明文字更新

## 2026-03-06

### 付款資訊
- 新增 LINE 群組 QR code 圖片到付款說明頁面

## 2026-03-02

### LINE 付款通知
- 免費用戶側邊欄「💰 我已完成轉帳」按鈕
- 輸入轉帳末 5 碼後透過 LINE Messaging API 通知老師

## 2026-03-01

### Freemium 訂閱系統
- 免費方案：單字 AI 補全每日 3 次、付費句型書鎖定
- Premium 方案：所有功能無限制
- `is_premium()` 自動比對 `plan_expiry` 到期日
- AI 用量追蹤：`record_ai_usage()` 記錄 token 使用量到 Firestore

### 自助註冊
- 新用戶填寫名稱+密碼即可註冊
- 學號自動產生（S+3 位數字，排除 S999 測試帳號）
- 7 天免費 Premium 試用

### UI 改進
- 付費句型書選單顯示 🔒 圖示
- 側邊欄顯示方案狀態與剩餘額度

## 2026-01-29

### 例句連連看
- 抽 5 個單字例句挖空，6 個選項（含 1 干擾項）做配對

### Cookie 記住登入
- 使用 `streamlit-cookies-controller` 存使用者名稱和密碼（有效期 30 天）

## 2026-01-25

### 專案文件
- 新增 CLAUDE.md 專案說明文件

## 2026-01-22 ~ 2026-01-24

### 排行榜與儀表板
- 全班句型練習排行榜（按完成率排序）
- 修正排行榜顯示問題
- 修正刪除句型書紀錄的 bug

### 課程設定
- 調整課程預設值規則
- 放大輸入區域

## 2026-01-17 ~ 2026-01-18

### 語音辨識升級
- 改用 Gemini 多模態 API 作為主要語音辨識引擎
- Google STT 改為備援方案
- 要求說完整句子才算通過

### 學習儀表板
- 新增全班進度圖表
- 修正進度百分比計算 bug

### 其他
- 新增密碼修改功能
- 新增從練習頁面直接跳轉功能
- 修正 system_prompt、清除進度資料、快取等 bug

## 2026-01-13 ~ 2026-01-16

### 句型口說練習
- 新增句型練習模組（多題庫、多分類）
- 新增學習統計表

### 單字管理基礎
- Firestore 資料持久化
- 新增 TTS 語音朗讀（Web Speech API，修正 Safari 相容性）
- CSV 檔案匯入
- 顯示單字總數

## 2026-01-12

### 初始版本
- 單字卡片翻面練習
- Gemini AI 自動補全單字資訊
- 基本使用者系統
