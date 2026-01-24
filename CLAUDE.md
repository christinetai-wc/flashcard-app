# CLAUDE.md - Flashcard Pro 雲端版

## 核心功能

### 1. 使用者管理
- **登入/登出系統**：基於 Firestore 的使用者認證，支援密碼加密 (SHA-256)
- **密碼修改**：使用者可在側邊欄修改自己的密碼
- **多使用者隔離**：每個使用者有獨立的資料路徑 (`users/{uid}/`)

### 2. 單字學習
- **單字庫管理**：
  - 批次輸入：透過 Gemini AI 自動解析並補全單字資訊（詞性、中文釋義、例句）
  - 手動修改：直接編輯現有單字
  - CSV 匯入：支援從 CSV 檔案批次匯入
  - 單字刪除：支援全選批次刪除
- **快閃練習**：卡片式翻面練習，支援鍵盤快捷鍵（方向鍵切換、空白鍵翻面）
- **實力測驗**：隨機抽題測驗中文，記錄答對/答錯次數

### 3. 句型口說練習
- **題庫系統**：支援多本句型書，每本有多個分類
- **語音辨識**：
  - 主要：Gemini 多模態 API（直接處理音訊）
  - 備援：Google Speech Recognition (本地)
- **進度追蹤**：記錄每個句型的選項完成狀態
- **智慧跳轉**：切換題庫時自動跳到第一個未完成的題目

### 4. 學習儀表板
- **學習戰績表**：堆疊進度條顯示各課程/分類的完成狀態（綠=已完成、黃=進行中、灰=未開始）
- **單字統計**：覆蓋率、正確率等指標
- **句型統計**：完成率、進度表格
- **排行榜**：未登入時顯示全班句型練習排行榜（按完成率排序）

### 5. 文字轉語音 (TTS)
- 使用瀏覽器原生 Web Speech API
- 支援自動播放與手動按鈕播放（相容 iOS）

---

## 數據模型

### Firestore 資料結構

```
artifacts/
└── {APP_ID}/
    ├── public/
    │   └── data/
    │       ├── users/              # 使用者列表
    │       │   └── {user_name}/    # 文件 ID = 使用者名稱
    │       ├── sentences/          # 句型題庫目錄
    │       │   └── {dataset_id}/   # 題庫 metadata
    │       └── {dataset_id}/       # 句型題目內容
    │           └── {doc_id}/       # 單一句型題目
    └── users/
        └── {user_id}/
            ├── vocabulary/         # 使用者的單字庫
            │   └── {doc_id}/       # 單一單字
            └── sentence_progress/  # 句型練習進度
                └── {template_hash}/ # 以句型模板 MD5 為 ID
```

### 單字 (Vocabulary) 資料結構

| 欄位 | 類型 | 說明 |
|------|------|------|
| English | string | 英文單字 |
| POS | string | 詞性（中文，如：名詞、動詞） |
| Chinese_1 | string | 主要中文釋義 |
| Chinese_2 | string | 次要中文釋義 |
| Example | string | 英文例句 |
| Course | string | 所屬課程名稱 |
| Date | string | 加入日期 (YYYY-MM-DD) |
| Correct | int | 答對次數 |
| Total | int | 總練習次數 |

### 句型 (Sentence) 資料結構

| 欄位 | 類型 | 說明 |
|------|------|------|
| Category | string | 分類名稱 |
| Template | string | 句型模板（含 `___` 填空） |
| Options | array[string] | 可填入的選項列表 |
| Order | int | 排序編號 |
| Timestamp | timestamp | 建立時間 |

### 使用者 (User) 資料結構

| 欄位 | 類型 | 說明 |
|------|------|------|
| name | string | 顯示名稱 |
| id | string | 學號 |
| password | string | SHA-256 雜湊後的密碼 |
| color | string | 使用者代表色 |
| sentence_stats | map | 句型練習統計（按題庫 ID 分組） |

### 句型進度 (Sentence Progress) 資料結構

| 欄位 | 類型 | 說明 |
|------|------|------|
| template_text | string | 原始句型模板文字 |
| completed_options | array[string] | 已完成的選項列表 |
| dataset_id | string | 所屬題庫 ID |
| last_updated | timestamp | 最後更新時間 |

---

## 架構設計

### 整體結構

```
┌─────────────────────────────────────────────────────────┐
│                    Streamlit UI Layer                    │
│  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────────────┐│
│  │ 儀表板  │ │單字管理 │ │單字練習 │ │   句型口說     ││
│  └────┬────┘ └────┬────┘ └────┬────┘ └────────┬────────┘│
└───────┼──────────┼──────────┼─────────────────┼─────────┘
        │          │          │                 │
┌───────┴──────────┴──────────┴─────────────────┴─────────┐
│                   Business Logic Layer                   │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────────┐ │
│  │ 資料篩選過濾 │ │ 統計計算     │ │ AI 語音辨識      │ │
│  └──────────────┘ └──────────────┘ └──────────────────┘ │
└─────────────────────────────────────────────────────────┘
        │                                    │
┌───────┴────────────────────────────────────┴────────────┐
│                    Data Access Layer                     │
│  ┌──────────────────────┐  ┌────────────────────────┐   │
│  │ Firestore CRUD       │  │ Session State 管理     │   │
│  │ (vocabulary, users,  │  │ (暫存、UI 狀態)        │   │
│  │  sentence_progress)  │  │                        │   │
│  └──────────────────────┘  └────────────────────────┘   │
└─────────────────────────────────────────────────────────┘
        │                            │
┌───────┴────────────┐    ┌─────────┴─────────────────────┐
│ Google Firestore   │    │ External APIs                 │
│ (資料持久化)        │    │ - Gemini API (AI 處理)        │
│                    │    │ - Web Speech API (TTS)        │
│                    │    │ - SpeechRecognition (STT)     │
└────────────────────┘    └───────────────────────────────┘
```

### 主要模組職責

| 模組區塊 | 行號範圍 | 職責 |
|----------|----------|------|
| 設定與常數 | 1-60 | 初始化、API 設定、Firestore 連線 |
| 工具函式 | 61-121 | 雜湊、使用者列表取得、初始化 |
| 資料庫操作 | 123-343 | CRUD 函式（單字、句型、進度） |
| AI 與 JS 工具 | 344-718 | Gemini 呼叫、TTS、鍵盤橋接 |
| 登入回調 | 719-736 | 處理登入邏輯 |
| UI 介面 | 738-1489 | Sidebar + 主要頁面渲染 |

---

## 核心演算法 / 邏輯

### 1. 語音辨識雙重機制 (`check_audio_batch`)

```
輸入: 音訊檔案, 句型模板, 選項列表
輸出: { correct_options, heard, feedback }

流程:
1. 嘗試 Gemini 多模態 API
   - 將音訊 Base64 編碼
   - 發送 prompt + 音訊給 Gemini
   - 解析 JSON 回應

2. 如果 Gemini 成功且有結果 → 直接回傳

3. 否則 Fallback 到本地 SpeechRecognition
   - 使用 Google STT 轉錄
   - 字串比對：將每個選項填入模板，檢查是否出現在轉錄文字中

4. 回傳最終結果
```

### 2. 學習進度計算

**單字**：
- 熟練：`Correct > 0`
- 練習中：`Total > 0 && Correct == 0`
- 未開始：`Total == 0`

**句型**：
- 完成：所有 Options 都在 completed_options 中
- 進行中：部分 Options 完成
- 未開始：無任何 completed_options

### 3. 智慧跳轉邏輯

```python
# 當切換題庫時 (filter signature 改變)
if last_filter_sig != current_filter_sig:
    for i, sentence in enumerate(sentences):
        user_done = get_user_progress(sentence.hash)
        if not all_options_done(sentence.options, user_done):
            jump_to(i)  # 跳到第一個未完成的
            break
```

### 4. 排行榜排序

```python
# 按完成率降序，同率則按完成數量降序
sorted(students, key=lambda x: (-x['rate'], -x['completed']))
```

---

## 設計決策與理由

### 1. 選擇 Streamlit

**理由**：
- 快速原型開發，適合教育類應用
- 內建元件豐富（data_editor, audio_input, charts）
- Session State 簡化狀態管理

**替代方案考慮**：
- Flask/Django：需自行處理前端，開發速度慢
- Gradio：功能較受限

### 2. Firestore 作為後端

**理由**：
- 無伺服器架構，免維運
- 即時同步能力
- 與 Google Cloud 生態整合良好

**替代方案考慮**：
- Supabase：需要 PostgreSQL 知識
- MongoDB Atlas：文件結構類似，但整合性較差

### 3. 使用 MD5 作為句型 Hash

**理由**：
- 句型模板作為唯一識別，Hash 後作為 Document ID
- 避免特殊字符問題
- 快速比對

**注意**：僅用於識別，非安全性用途

### 4. 雙重語音辨識機制

**理由**：
- Gemini 多模態準確度高，但偶爾會漏判
- SpeechRecognition 作為備援提高容錯性
- 降低 API 失敗對用戶體驗的影響

### 5. Session State 管理導航

**理由**：
- Streamlit 每次互動都會重新執行，需要狀態持久化
- 使用 `key` 綁定 widget 與 session state
- 透過 callback 函式處理跨頁面導航

---

## 邊界情況處理

### 已處理

| 情況 | 處理方式 |
|------|----------|
| Firestore 連線失敗 | `get_db()` 返回 None，後續檢查 `if not db` |
| 無使用者資料 | 自動初始化預設使用者 (`init_users_in_db`) |
| 無單字資料 | `sync_vocab_from_db(init_if_empty=True)` 初始化預設單字 |
| 索引越界 | `practice_idx % len(current_set)` 循環處理 |
| API 超時 | `timeout=30` 秒限制，exception 處理 |
| 空輸入 | 各輸入處檢查 `if not text.strip()` |
| Firestore 批次限制 | 每 400 筆 commit 一次 |
| 密碼錯誤 | 顯示錯誤訊息，不允許登入 |
| Timestamp 格式 | 檢查 `hasattr(last_active, 'date')` 處理不同格式 |

### 潛在未處理

| 情況 | 風險 |
|------|------|
| 並發寫入 | 同一使用者多裝置同時操作可能覆蓋 |
| API 配額超限 | Gemini API 有每分鐘請求限制 |
| 大量資料載入 | 無分頁，大資料集可能緩慢 |
| 網路中斷 | 無離線模式支援 |

---

## 性能考慮

### 快取策略

| 函式 | TTL | 用途 |
|------|-----|------|
| `get_db()` | 永久 (cache_resource) | 避免重複建立連線 |
| `fetch_users_list()` | 600s | 使用者列表少變動 |
| `fetch_sentence_catalogs()` | 600s | 題庫目錄少變動 |
| `fetch_sentences_by_id()` | 600s | 題目內容少變動 |

### 優化空間

1. **分頁載入**：大量單字時應實作分頁
2. **增量同步**：目前每次都全量同步，可改用 `last_updated` 增量
3. **前端快取**：可用 localStorage 快取已完成的題目
4. **批次更新統計**：`update_user_stats_summary` 每次練習都計算全部，可改為增量

### 瓶頸分析

- **語音辨識**：Gemini API 回應時間約 2-5 秒
- **首次載入**：需讀取多個 Collection，約 1-2 秒
- **Session State**：大量資料存放會增加記憶體

---

## 依賴與外部集成

### Python 套件

```
streamlit           # Web 框架
pandas              # 資料處理
google-cloud-firestore  # Firestore SDK
google-oauth2-tool  # 認證
requests            # HTTP 請求
SpeechRecognition   # 本地語音辨識 (optional)
```

### 外部服務

| 服務 | 用途 | 認證方式 |
|------|------|----------|
| Google Firestore | 資料庫 | Service Account (st.secrets) |
| Gemini API | AI 文字處理、語音辨識 | API Key (st.secrets) |
| Google Speech API | 語音轉文字 (備援) | 無需認證 (免費額度) |

### API 呼叫方式

**Gemini API**：
```python
POST https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={API_KEY}
Content-Type: application/json

{
  "contents": [{"parts": [{"text": "prompt"}, {"inline_data": {...}}]}],
  "generationConfig": {"responseMimeType": "application/json"}
}
```

### Secrets 結構

```toml
# .streamlit/secrets.toml
GEMINI_API_KEY = "..."
APP_ID = "flashcard-pro-v1"

[firebase_credentials]
type = "service_account"
project_id = "..."
private_key = "..."
client_email = "..."
# ... 其他 service account 欄位
```

---

## 已知限制與改進空間

### 目前限制

1. **單一租戶**：APP_ID 固定，無法支援多租戶
2. **無離線模式**：完全依賴網路連線
3. **無資料匯出**：使用者無法匯出自己的學習紀錄
4. **無管理後台**：題庫管理需另開 admin_app.py
5. **iOS 主畫面圖示**：Streamlit Cloud 限制，無法自訂 apple-touch-icon

### 未實現功能

- [ ] 錯題本：收集常錯單字重點練習
- [ ] 學習提醒：推播通知
- [ ] 社交功能：好友 PK
- [ ] 成就系統：學習獎章
- [ ] 自訂題庫：使用者自建句型

### 可優化項目

| 項目 | 優先級 | 說明 |
|------|--------|------|
| 增量同步 | 高 | 減少 Firestore 讀取量 |
| 分頁載入 | 中 | 大資料集性能 |
| 離線快取 | 中 | PWA 支援 |
| 批次統計更新 | 低 | 減少計算開銷 |

### 技術債

1. **硬編碼路徑**：Firestore 路徑散落各處，應集中管理
2. **重複程式碼**：篩選邏輯在多處重複
3. **缺乏單元測試**：核心函式無測試覆蓋
4. **錯誤處理不一致**：部分用 try-except，部分用 if 檢查
5. **Magic Numbers**：如 `400` (批次大小)、`600` (TTL) 應定義為常數
