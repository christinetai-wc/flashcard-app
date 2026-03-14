# Development Log

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
