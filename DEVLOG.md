# Development Log

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
