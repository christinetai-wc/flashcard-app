# Flashcard Pro 雲端版

英語單字與句型學習平台，專為台灣國中小學生設計。

## 功能總覽

### 單字學習
- **AI 輸入** — 文字輸入 / 拍照辨識 (OCR) / 上傳圖片，Gemini AI 自動補全詞性、中文釋義、例句
- **快閃練習** — 卡片翻面練習，支援鍵盤快捷鍵
- **實力測驗** — 抽題測驗中文釋義，SRS 間隔重複排程
- **例句連連看** — 例句挖空配對遊戲
- **公用單字集** — 一鍵匯入教育部國小 1200 字等公用詞庫
- **CSV 匯入/匯出** — 匯入時自動檢查重複，匯出帶走個人學習紀錄

### 句型口說
- **多本句型書** — 支援多題庫、多分類
- **語音辨識** — Gemini 多模態 API（主要）+ Google STT（備援）
- **進度追蹤** — 記錄每個句型的完成狀態，智慧跳轉到未完成題目

### 學習數據
- **個人儀表板** — 堆疊進度條、單字/句型統計
- **全班排行榜** — 按句型書分組、完成率排序
- **練習時長追蹤** — 記錄每次練習的持續時間

### 使用者系統
- **自助註冊** — 新用戶 7 天免費 Premium 試用
- **Freemium 訂閱** — 免費方案每日 3 次 AI 補全；Premium 無限制
- **記住登入** — Cookie 記住帳密，30 天免重新輸入

## 技術架構

| 層級 | 技術 |
|------|------|
| 前端 | Streamlit |
| 資料庫 | Google Firestore |
| AI | Gemini 2.5 Flash（文字補全、語音辨識、OCR） |
| TTS | 瀏覽器 Web Speech API |
| 部署 | Streamlit Cloud |
| 通知 | LINE Messaging API |

## 檔案結構

```
streamlit_app.py      # 主應用程式（學生端）
admin_app.py          # 管理後台（老師端）
system_prompt.md      # Gemini 單字補全 prompt
pronunciation_feedback_prompt.md  # 語音回饋 prompt
requirements.txt      # Python 依賴
.streamlit/
  config.toml         # Streamlit 設定
  secrets.toml        # API 金鑰（不入版控）
```

## 本地開發

```bash
pip install -r requirements.txt

# 學生端
streamlit run streamlit_app.py

# 管理後台
streamlit run admin_app.py
```

需要在 `.streamlit/secrets.toml` 設定：
- `GEMINI_API_KEY`
- `APP_ID`
- `LINE_CHANNEL_ACCESS_TOKEN`
- `LINE_TEACHER_USER_ID`
- `[firebase_credentials]`

## 授權

Private — TechEasy Lab
