import streamlit as st
import pandas as pd
import random
import json
import requests
import time
import hashlib
import os
import base64
import string
from datetime import date
from google.cloud import firestore
from google.oauth2 import service_account
from streamlit.components.v1 import html

# --- 新增：嘗試匯入 SpeechRecognition (保留供其他用途，但主功能改用 Gemini Audio) ---
try:
    import speech_recognition as sr
except ImportError:
    sr = None

# --- 0. 設定與常數 ---
st.set_page_config(page_title="Flashcard Pro 雲端版", page_icon="app-icon.png", layout="wide")

# 讀取 Secrets
GEMINI_API_KEY = st.secrets.get("GEMINI_API_KEY", "")
GEMINI_MODEL = "gemini-2.5-flash-preview-09-2025"
GEMINI_API_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"

# 預設單字內容 (Fallback)
INITIAL_VOCAB = [
    {"English": "plus", "POS": "介系詞", "Chinese_1": "加", "Chinese_2": "加上", "Example": "Two plus two is four.", "Course": "Sophie數學課", "Date": "2025-11-15", "Correct": 0, "Total": 0},
    {"English": "minus", "POS": "介系詞", "Chinese_1": "減", "Chinese_2": "減去", "Example": "Five minus two is three.", "Course": "Sophie數學課", "Date": "2025-11-15", "Correct": 0, "Total": 0},
    {"English": "multiply", "POS": "動詞", "Chinese_1": "乘", "Chinese_2": "繁殖", "Example": "Multiply 3 by 4.", "Course": "Sophie數學課", "Date": "2025-12-31", "Correct": 0, "Total": 0},
    {"English": "divide", "POS": "動詞", "Chinese_1": "除", "Chinese_2": "分開", "Example": "Divide 10 by 2.", "Course": "Sophie數學課", "Date": "2026-01-10", "Correct": 0, "Total": 0},
    {"English": "think", "POS": "動詞", "Chinese_1": "思考", "Chinese_2": "想", "Example": "I need to think about it.", "Course": "Cherie思考課", "Date": "2025-11-16", "Correct": 0, "Total": 0},
]

# 預設句型內容 (Fallback)
INITIAL_SENTENCES = [
    {"Category": "1.基礎描述句", "Template": "This ___ is very important.", "Options": ["test", "rule", "decision", "habit", "lesson"]},
    {"Category": "1.基礎描述句", "Template": "This ___ is very expensive.", "Options": ["course", "phone", "trip", "book", "gift"]},
]

# --- 1. Firestore 初始化 ---
@st.cache_resource
def get_db():
    try:
        creds_info = st.secrets["firebase_credentials"]
        creds = service_account.Credentials.from_service_account_info(creds_info)
        return firestore.Client(credentials=creds)
    except Exception as e:
        return None

db = get_db()
APP_ID = st.secrets.get("APP_ID", "flashcard-pro-v1")
USER_LIST_PATH = f"artifacts/{APP_ID}/public/data/users"
SENTENCE_CATALOG_PATH = f"artifacts/{APP_ID}/public/data/sentences"
SENTENCE_DATA_BASE_PATH = f"artifacts/{APP_ID}/public/data"

# --- 2. 工具函式 ---

def hash_string(text):
    return hashlib.md5(text.encode('utf-8')).hexdigest()

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

@st.cache_data(ttl=600)
def fetch_users_list():
    if not db: return {}
    docs = db.collection(USER_LIST_PATH).stream()
    return {d.id: d.to_dict() for d in docs}

def init_users_in_db():
    if not db: return
    if st.session_state.get("users_initialized"): return
    docs = db.collection(USER_LIST_PATH).limit(1).get()
    if not docs:
        default_pwd = hash_password("1234")
        users = [
            {"name": "Esme", "id": "S001", "password": default_pwd, "color": "#FF69B4"},
            {"name": "Neo", "id": "S002", "password": default_pwd, "color": "#1E90FF"},
            {"name": "Verno", "id": "S003", "password": default_pwd, "color": "#32CD32"}
        ]
        for u in users:
            db.collection(USER_LIST_PATH).document(u["name"]).set(u)
    st.session_state.users_initialized = True

# --- 3. Session State 初始化 ---
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
if "user_info" not in st.session_state:
    st.session_state.user_info = None
if "u_vocab" not in st.session_state:
    st.session_state.u_vocab = []
if "practice_idx" not in st.session_state:
    st.session_state.practice_idx = 0
if "practice_reveal" not in st.session_state:
    st.session_state.practice_reveal = False
if "quiz_history" not in st.session_state:
    st.session_state.quiz_history = []
if "audio_to_play" not in st.session_state:
    st.session_state.audio_to_play = None
# 導航狀態管理
if "nav_selection" not in st.session_state:
    st.session_state.nav_selection = "學習儀表板"

# 句型練習專用 State
if "sentence_idx" not in st.session_state:
    st.session_state.sentence_idx = 0
if "completed_options" not in st.session_state:
    st.session_state.completed_options = set() 
if "current_sentences" not in st.session_state:
    st.session_state.current_sentences = []
if "last_sentence_filter_sig" not in st.session_state:
    st.session_state.last_sentence_filter_sig = ""
if "current_dataset_id" not in st.session_state:
    st.session_state.current_dataset_id = None # 記錄當前正在練習哪個題庫

init_users_in_db()

# --- 4. 資料庫操作函式 (單字 & 句型) ---

def get_vocab_path():
    if st.session_state.logged_in and st.session_state.user_info:
        uid = st.session_state.user_info["id"]
        return f"artifacts/{APP_ID}/users/{uid}/vocabulary"
    return None

def get_sentence_progress_path():
    if st.session_state.logged_in and st.session_state.user_info:
        uid = st.session_state.user_info["id"]
        return f"artifacts/{APP_ID}/users/{uid}/sentence_progress"
    return None

def sync_vocab_from_db(init_if_empty=False):
    path = get_vocab_path()
    if not db or not path: return
    docs = db.collection(path).stream()
    data = []
    for d in docs:
        item = d.to_dict()
        item['id'] = d.id
        data.append(item)
    
    if not data and init_if_empty:
        for item in INITIAL_VOCAB:
            db.collection(path).add(item)
        time.sleep(1)
        return sync_vocab_from_db(init_if_empty=False)
        
    st.session_state.u_vocab = data

def update_word_data(doc_id, update_dict):
    path = get_vocab_path()
    if db and path and doc_id:
        db.collection(path).document(doc_id).update(update_dict)
        for item in st.session_state.u_vocab:
            if item.get('id') == doc_id:
                item.update(update_dict)
                break

def save_new_words_to_db(items):
    path = get_vocab_path()
    if db and path:
        batch = db.batch()
        count = 0
        for it in items:
            doc_ref = db.collection(path).document()
            batch.set(doc_ref, it)
            count += 1
            if count >= 400:
                batch.commit()
                batch = db.batch()
                count = 0
        if count > 0:
            batch.commit()

def delete_words_from_db(doc_ids):
    path = get_vocab_path()
    if db and path:
        for doc_id in doc_ids:
            db.collection(path).document(doc_id).delete()

# --- 句型資料庫操作 ---

@st.cache_data(ttl=600)
def fetch_sentence_catalogs():
    """讀取公用題庫列表"""
    if not db: return {}
    docs = db.collection(SENTENCE_CATALOG_PATH).stream()
    return {d.id: d.to_dict().get('name', d.id) for d in docs}

@st.cache_data(ttl=600)
def fetch_sentences_by_id(dataset_id):
    """讀取特定題庫的句型，並依照 Order 排序"""
    if not db: return []
    path = f"{SENTENCE_DATA_BASE_PATH}/{dataset_id}"
    docs = db.collection(path).stream()
    data = [d.to_dict() for d in docs]
    sorted_data = sorted(data, key=lambda x: x.get('Order', 9999))
    return sorted_data

def load_user_sentence_progress(template_hash):
    path = get_sentence_progress_path()
    if not db or not path: return []
    doc = db.collection(path).document(template_hash).get()
    if doc.exists:
        return set(doc.to_dict().get("completed_options", []))
    return set()

def fetch_all_user_sentence_progress():
    path = get_sentence_progress_path()
    if not db or not path: return {}
    docs = db.collection(path).stream()
    return {d.id: d.to_dict().get("completed_options", []) for d in docs}

# --- 新增：更新使用者統計摘要 ---
def update_user_stats_summary(dataset_id):
    """計算並更新使用者的該題庫統計資訊"""
    if not db or not dataset_id: return
    user_name = st.session_state.get("current_user_name")
    if not user_name: return

    # 1. 取得題庫資訊 (利用快取)
    sentences = fetch_sentences_by_id(dataset_id)
    catalogs = fetch_sentence_catalogs()
    dataset_name = catalogs.get(dataset_id, dataset_id)
    
    total_count = len(sentences)
    if total_count == 0: return

    # 2. 取得使用者在該題庫的所有進度
    # 這裡直接查詢 Firestore，因為需要最新數據
    progress_path = get_sentence_progress_path()
    docs = db.collection(progress_path).where("dataset_id", "==", dataset_id).stream()
    
    progress_map = {}
    for d in docs:
        data = d.to_dict()
        progress_map[d.id] = set(data.get("completed_options", []))
        
    completed_count = 0
    in_progress_count = 0
    
    for s in sentences:
        tid = hash_string(s['Template'])
        user_done = progress_map.get(tid, set())
        all_opts = set(s.get('Options', []))
        
        if not all_opts: continue
        
        if user_done:
            if all_opts.issubset(user_done):
                completed_count += 1
            else:
                in_progress_count += 1
    
    # 3. 更新使用者文件
    # 結構: sentence_stats: { dataset_id: { ... } }
    user_ref = db.collection(USER_LIST_PATH).document(user_name)
    stats_data = {
        f"sentence_stats.{dataset_id}": {
            "name": dataset_name,
            "total": total_count,
            "completed": completed_count,
            "in_progress": in_progress_count,
            "last_active": firestore.SERVER_TIMESTAMP
        }
    }
    user_ref.update(stats_data)
    # 清除快取，確保排行榜更新
    fetch_users_list.clear()

def save_user_sentence_progress(template_str, completed_list, dataset_id=None):
    """儲存使用者對某句型的練習進度，並標記來源題庫 ID"""
    path = get_sentence_progress_path()
    if not db or not path: return
    template_hash = hash_string(template_str)
    data = {
        "template_text": template_str,
        "completed_options": list(completed_list),
        "last_updated": firestore.SERVER_TIMESTAMP
    }
    # 新增：記錄這是哪本題庫的進度，方便日後管理
    if dataset_id:
        data["dataset_id"] = dataset_id
        
    db.collection(path).document(template_hash).set(data, merge=True)
    
    # 同步更新統計摘要
    if dataset_id:
        update_user_stats_summary(dataset_id)

def clear_user_sentence_history(target_dataset_id=None):
    """
    清除該使用者所有的句型練習紀錄。
    如果指定了 target_dataset_id，只清除該題庫的紀錄。
    """
    path = get_sentence_progress_path()
    if not db or not path: return 0

    # 批次刪除 sentence_progress
    docs = db.collection(path).stream()
    batch = db.batch()
    count = 0
    deleted_count = 0

    for d in docs:
        doc_data = d.to_dict()
        # 如果指定了題庫ID，且該記錄不屬於此題庫，則跳過
        if target_dataset_id and doc_data.get("dataset_id") != target_dataset_id:
            continue

        batch.delete(d.reference)
        count += 1
        deleted_count += 1
        if count >= 400:
            batch.commit()
            batch = db.batch()
            count = 0
    if count > 0:
        batch.commit()

    # 清除 users 文件中的 sentence_stats
    user_name = st.session_state.get("current_user_name")
    if user_name:
        user_ref = db.collection(USER_LIST_PATH).document(user_name)
        if target_dataset_id:
            # 只刪除特定題庫的統計
            user_ref.update({
                f"sentence_stats.{target_dataset_id}": firestore.DELETE_FIELD
            })
        else:
            # 刪除所有 sentence_stats
            user_ref.update({
                "sentence_stats": firestore.DELETE_FIELD
            })
        fetch_users_list.clear()  # 清除快取

    return deleted_count

# --- 5. AI 與 JS 工具 ---

def normalize_text(text):
    if not text: return ""
    text = text.translate(str.maketrans('', '', string.punctuation))
    return " ".join(text.split()).lower()

def check_audio_batch(audio_file, template, options_list):
    """
    批次語音檢查：
    1. 優先使用 Gemini (多模態) 處理音訊 + 轉錄 + 判斷。
    2. 如果 Gemini 沒抓到任何選項 (correct_options 為空) 或失敗，才使用 SpeechRecognition (SR) 做 Fallback。
    """
    # --- 準備：讀取 Prompt 檔案 ---
    prompt_file = "pronunciation_feedback_prompt.md"
    base_prompt = ""
    if os.path.exists(prompt_file):
        with open(prompt_file, "r", encoding="utf-8") as f:
            base_prompt = f.read()
    else:
        # Fallback prompt if file is missing
        base_prompt = """
        Context: English pronunciation practice for non-native speakers.
        Template Sentence: "{template}"
        Target Vocabulary to fill in the blank: {options_list}
        
        Task:
        1. Listen to the audio provided.
        2. Transcribe it exactly as heard.
        3. Identify which of the 'Target Vocabulary' appear in the speech within the sentence structure.
        4. Be flexible with minor pronunciation errors, but key words must be recognizable.
        5. Provide specific, constructive feedback in Traditional Chinese.

        Return JSON: 
        {{ 
            "transcript": "Transcription of the audio",
            "correct_options": ["opt1", "opt2"], 
            "feedback": "Specific feedback here" 
        }}
        """

    # 填入 Prompt 變數
    prompt = base_prompt.format(
        template=template,
        options_list=options_list
    )

    # 讀取音訊 Bytes
    audio_file.seek(0)
    audio_bytes = audio_file.read()
    encoded_audio = base64.b64encode(audio_bytes).decode('utf-8')
    
    # --- 嘗試 1：Gemini 多模態 (音訊直接輸入) ---
    ai_corrects = []
    ai_transcript = ""
    ai_feedback = ""
    gemini_success = False
    
    gemini_payload = {
        "contents": [{
            "parts": [
                {"text": prompt},
                {"inline_data": {"mime_type": "audio/wav", "data": encoded_audio}}
            ]
        }],
        "generationConfig": {"responseMimeType": "application/json"}
    }
    
    try:
        res = requests.post(f"{GEMINI_API_URL}?key={GEMINI_API_KEY}", json=gemini_payload, timeout=30)
        if res.status_code == 200:
            content_text = res.json()['candidates'][0]['content']['parts'][0]['text']
            
            # 清理 JSON 字串
            if "```json" in content_text:
                content_text = content_text.split("```json")[1].split("```")[0]
            elif "```" in content_text:
                content_text = content_text.split("```")[1].split("```")[0]
            
            ai_result = json.loads(content_text.strip())
            
            ai_transcript = ai_result.get("transcript", "")
            ai_feedback = ai_result.get("feedback", "加油！")
            
            # 處理大小寫
            raw_ai_found = ai_result.get("correct_options", [])
            options_lower_map = {opt.lower(): opt for opt in options_list}
            for raw_opt in raw_ai_found:
                if raw_opt in options_list:
                    ai_corrects.append(raw_opt)
                elif raw_opt.lower() in options_lower_map:
                    ai_corrects.append(options_lower_map[raw_opt.lower()])
            
            gemini_success = True
            
    except Exception as e:
        print(f"Gemini Audio Error: {e}")

    # 如果 Gemini 成功且有抓到東西，直接回傳
    if gemini_success and ai_corrects:
        return {
            "correct_options": ai_corrects,
            "heard": ai_transcript,
            "feedback": ai_feedback
        }

    # --- 嘗試 2：Fallback (本地 SR + 字串比對) ---
    # 當 Gemini 沒抓到 (ai_corrects 為空) 或 連線失敗 時執行
    
    # 確保有安裝 SR
    if sr:
        audio_file.seek(0) # 重置指針
        recognizer = sr.Recognizer()
        local_transcript = ""
        try:
            with sr.AudioFile(audio_file) as source:
                audio_data = recognizer.record(source)
            local_transcript = recognizer.recognize_google(audio_data, language="en-US")
        except:
            pass # SR 失敗就維持空字串

        if local_transcript:
            local_found = []
            norm_transcript = normalize_text(local_transcript)
            for opt in options_list:
                target_sent = template.replace("___", opt)
                norm_target = normalize_text(target_sent)
                if norm_target in norm_transcript:
                    local_found.append(opt)
            
            # 如果本地比對有抓到，就使用本地結果
            if local_found:
                return {
                    "correct_options": local_found,
                    "heard": local_transcript,
                    "feedback": "AI 未偵測到，但本地規則比對成功！(Fallback)"
                }
            
            # 如果本地也沒抓到，但 Gemini 有回傳 transcript，優先顯示 Gemini 的聽寫結果
            if gemini_success:
                 return {
                    "correct_options": [],
                    "heard": ai_transcript,
                    "feedback": ai_feedback
                }
            
            # 只有 SR 成功，Gemini 失敗的情況
            return {
                "correct_options": [],
                "heard": local_transcript,
                "feedback": "未能辨識出正確句子，請再試一次。"
            }
    
    # 全部失敗
    return {
        "correct_options": [],
        "heard": ai_transcript if ai_transcript else "(無法辨識)",
        "feedback": ai_feedback if ai_feedback else "系統忙碌或無法辨識。"
    }

def call_gemini_to_complete(words_text, course_name, course_date):
    if not words_text.strip(): return []
    
    # --- 修改點：讀取外部 MD 檔案 ---
    prompt_file = "system_prompt.md"
    if st.secrets.get("system_prompt"):
        base_prompt = st.secrets["system_prompt"]
    elif os.path.exists(prompt_file):
        with open(prompt_file, "r", encoding="utf-8") as f:
            base_prompt = f.read()
    else:
        # 備用 Prompt，防止檔案遺失導致程式崩潰
        base_prompt = """
You are a vocabulary organizing assistant.
Requirements:
1. Identify the main English word each line.
2. If a line includes definitions or example sentences, CORRECT them if there are errors.
3. If definitions (Chinese_1, Chinese_2), POS, or example sentences are MISSING, provide them.
4. Ensure the Part of Speech (POS) in Traditional Chinese (e.g., 名詞, 動詞, 形容詞).
5. Ensure the (Chinese_1, Chinese_2) in Traditional Chinese.
6. Ensure the (Word, Example) in English.
7. Output format MUST be strictly separated by a pipe symbol (|) for each line.
8. Format: Word | POS | Chinese_1 | Chinese_2 | Example
9. Do not output any header or markdown symbols, just the raw data lines.
        """
    
    prompt = f"{base_prompt}\n\nInput words:\n{words_text}"

    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    try:
        res = requests.post(f"{GEMINI_API_URL}?key={GEMINI_API_KEY}", json=payload, timeout=30)
        if res.status_code == 200:
            text = res.json()['candidates'][0]['content']['parts'][0]['text']
            raw_items = []
            for line in text.strip().split('\n'):
                if '|' in line:
                    p = [i.strip() for i in line.split('|')]
                    if len(p) >= 5:
                        raw_items.append({
                            "English": p[0], "POS": p[1], "Chinese_1": p[2], "Chinese_2": p[3], 
                            "Example": p[4], "Course": course_name, "Date": str(course_date), 
                            "Correct": 0, "Total": 0
                        })
            return raw_items
    except: pass
    return []

def get_combined_dashboard_options(vocab, catalogs):
    options = ["單字 (全部)"]
    if vocab:
        df = pd.DataFrame(vocab)
        if 'Course' not in df.columns: df['Course'] = '未分類'
        if 'Date' not in df.columns: df['Date'] = 'N/A'
        unique_courses = sorted(df['Course'].unique())
        for c in unique_courses:
            dates = df[df['Course'] == c]['Date'].unique()
            for d in sorted(dates, reverse=True):
                options.append(f"單字 | {c} | {d}")
    if catalogs:
        catalog_names = list(catalogs.values())
        catalog_ids = list(catalogs.keys())
        for name, cid in zip(catalog_names, catalog_ids):
            options.append(f"句型 | {name} (全部)")
            book_sentences = fetch_sentences_by_id(cid)
            if book_sentences:
                df_b = pd.DataFrame(book_sentences)
                if 'Category' in df_b.columns:
                    cats = sorted(df_b['Category'].unique())
                    for cat in cats:
                        options.append(f"句型 | {name} | {cat}")
    return options

def get_course_options(vocab):
    if not vocab: return ["全部單字"]
    df = pd.DataFrame(vocab)
    if 'Course' not in df.columns: df['Course'] = '未分類'
    if 'Date' not in df.columns: df['Date'] = 'N/A'
    
    unique_courses = sorted(df['Course'].unique())
    unique_instances = df[['Course', 'Date']].drop_duplicates().sort_values(['Course', 'Date'], ascending=[True, False])
    
    options = ["全部單字"]
    for c in unique_courses:
        options.append(f"📚 {c} (全部)")
        dates = unique_instances[unique_instances['Course'] == c]['Date'].tolist()
        for d in dates:
            options.append(f"   📅 {c} | {d}")
    return options

def filter_vocab_data(vocab, selection):
    if selection == "全部單字" or not vocab: return vocab
    df = pd.DataFrame(vocab)
    if 'Course' not in df.columns: df['Course'] = '未分類'
    if 'Date' not in df.columns: df['Date'] = 'N/A'

    if "(全部)" in selection:
        course_name = selection.replace("📚 ", "").replace(" (全部)", "").strip()
        return df[df['Course'] == course_name].to_dict('records')
    elif "|" in selection:
        parts = selection.replace("   📅 ", "").split("|")
        if len(parts) >= 2:
            course_name = parts[0].strip()
            course_date = parts[1].strip()
            return df[(df['Course'] == course_name) & (df['Date'] == course_date)].to_dict('records')
    return vocab

def get_sentence_category_options(sentences, catalog_name):
    if not sentences: return [f"📚 {catalog_name} (全部)"]
    df = pd.DataFrame(sentences)
    if 'Category' not in df.columns: df['Category'] = '未分類'
    unique_categories = sorted(df['Category'].unique())
    options = [f"📚 {catalog_name} (全部)"]
    for cat in unique_categories:
        options.append(f"   🏷️ {cat}")
    return options

def filter_sentence_data(sentences, selection):
    if " (全部)" in selection: return sentences
    category = selection.replace("   🏷️ ", "").strip()
    return [s for s in sentences if s.get('Category') == category]

def keyboard_bridge():
    js = """<script>
    var doc = window.parent.document;
    window.parent.myKeyHandler = function(e) {
        const getBtn = (txt) => Array.from(doc.querySelectorAll('button')).find(b => b.innerText.includes(txt));
        if (e.key === 'ArrowRight') getBtn("下一個")?.click();
        else if (e.key === 'ArrowLeft') getBtn("上一個")?.click();
        else if (e.key === ' ') { e.preventDefault(); getBtn("翻面")?.click(); }
    };
    doc.removeEventListener('keydown', window.parent.myKeyHandler);
    doc.addEventListener('keydown', window.parent.myKeyHandler);
    </script>"""
    html(js, height=0)

def auto_focus_input():
    js = """<script>
    setTimeout(() => {
        const doc = window.parent.document;
        const input = Array.from(doc.querySelectorAll('input')).find(i => i.getAttribute('aria-label')?.includes("輸入中文"));
        input?.focus();
    }, 250);
    </script>"""
    html(js, height=0)

def text_to_speech(text):
    """
    產生語音播放的 HTML 元件。
    包含一個自動觸發的 Script (針對 PC/Android)
    和一個實體按鈕 (針對 iOS)
    """
    if not text: return
    safe_text = text.replace('"', '\\"').replace('\n', ' ')
    
    js_code = f"""
    <script>
        function playSound() {{
            var synthesis = window.parent.speechSynthesis || window.speechSynthesis;
            if (synthesis) {{
                synthesis.cancel();
                var msg = new SpeechSynthesisUtterance("{safe_text}");
                msg.lang = 'en-US';
                msg.rate = 0.9;
                synthesis.speak(msg);
            }}
        }}
        setTimeout(playSound, 300);
    </script>
    <style>
        .audio-btn {{
            background-color: transparent; border: 1px solid #ddd; border-radius: 5px;
            padding: 4px 8px; font-size: 12px; cursor: pointer; color: #666;
            display: flex; align-items: center; gap: 4px; margin: 5px auto;
        }}
        .audio-btn:hover {{ background-color: #f0f0f0; color: #333; }}
    </style>
    <div style="display: flex; justify-content: center; width: 100%;">
        <button class="audio-btn" onclick="playSound()">🔊 播放發音</button>
    </div>
    """
    html(js_code, height=40)

# --- 客製化堆疊進度條函式 (水平排列版，無文字) ---
def render_custom_progress_bar(label_left, green_pct, yellow_pct, empty_pct):
    """
    繪製一個 HTML/CSS 堆疊進度條，標籤與進度條在同一行，移除右側文字，取消深色字體限制
    """
    bar_html = f"""
    <div style="display: flex; align-items: center; margin-bottom: 8px;">
        <div style="width: 40px; min-width: 40px; font-size: 0.9rem; margin-right: 10px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;" title="{label_left}">
            {label_left}
        </div>
        <div style="flex-grow: 1; background-color: #e0e0e0; border-radius: 6px; height: 16px; display: flex; overflow: hidden;">
            <div style="width: {green_pct*100}%; background-color: #28a745; height: 100%;" title="已熟練/已完成"></div>
            <div style="width: {yellow_pct*100}%; background-color: #ffc107; height: 100%;" title="練習中"></div>
            <div style="width: {empty_pct*100}%; background-color: #e0e0e0; height: 100%;" title="未開始"></div>
        </div>
    </div>
    """
    st.markdown(bar_html, unsafe_allow_html=True)

# --- 導航用回調函式 ---
def navigate_to_practice(preset):
    st.session_state.nav_selection = "單字練習"
    # 強制更新練習頁面的選單狀態
    st.session_state["practice_filter"] = preset

# --- 導航用回調函式 (句型) ---
def navigate_to_sentence(book, cat):
    preset = f"{book} | {cat}"
    st.session_state.sentence_filter_preset = preset
    st.session_state.nav_selection = "句型口說"
    # 強制更新句型頁面的選單狀態
    st.session_state["sentence_filter"] = preset

def attempt_login():
    """處理登入的 Callback 函式"""
    selected_name = st.session_state.login_user_name
    input_password = st.session_state.login_password
    users_db = st.session_state.users_db_cache
    
    if selected_name != "請選擇..." and input_password:
        user_record = users_db[selected_name]
        if hash_password(input_password) == user_record["password"]:
            st.session_state.logged_in = True
            st.session_state.current_user_name = selected_name
            st.session_state.user_info = user_record
            st.session_state.login_error = None
            sync_vocab_from_db(init_if_empty=True)
        else:
            st.session_state.login_error = "密碼錯誤。"
    else:
        st.session_state.login_error = "請選擇使用者並輸入密碼。"

# --- 7. UI 介面 ---

with st.sidebar:
    col_icon, col_title = st.columns([1, 4])
    col_icon.image("app-icon.png", width=40)
    col_title.markdown("### Flashcard Pro")
    users_db = fetch_users_list()
    # 暫存使用者名單以供 callback 使用
    st.session_state.users_db_cache = users_db
    
    if not st.session_state.logged_in:
        st.subheader("🔑 學生登入")
        
        st.selectbox(
            "請選擇使用者", 
            ["請選擇..."] + list(users_db.keys()),
            key="login_user_name"
        )
        
        st.text_input(
            "輸入密碼", 
            type="password",
            key="login_password",
            on_change=attempt_login
        )
        
        st.button("登入", on_click=attempt_login, use_container_width=True)
        
        if st.session_state.get("login_error"):
            st.error(st.session_state.login_error)
            
    else:
        user = st.session_state.user_info
        st.markdown(f"### 👤 {user['name']}")
        st.caption(f"學號: {user['id']}")
        st.divider()
        # 綁定選單狀態至 nav_selection
        menu =st.radio("功能選單", ["學習儀表板", "單字管理", "單字練習", "句型口說"], key="nav_selection")
        if st.button("登出", use_container_width=True):
            st.session_state.logged_in = False
            st.session_state.user_info = None
            st.session_state.u_vocab = []
            st.rerun()
        
        # --- 新增：修改密碼 Expander ---
        with st.expander("🔐 修改密碼"):
            with st.form("change_pwd_form"):
                curr_pwd = st.text_input("目前密碼", type="password")
                new_pwd = st.text_input("新密碼", type="password")
                conf_pwd = st.text_input("確認新密碼", type="password")
                
                if st.form_submit_button("確認修改"):
                    if hash_password(curr_pwd) != st.session_state.user_info['password']:
                        st.error("目前密碼錯誤。")
                    elif new_pwd != conf_pwd:
                        st.error("兩次新密碼輸入不一致。")
                    elif not new_pwd:
                        st.error("新密碼不能為空。")
                    else:
                        # Update Firestore
                        new_hash = hash_password(new_pwd)
                        user_ref = db.collection(USER_LIST_PATH).document(st.session_state.current_user_name)
                        user_ref.update({"password": new_hash})
                        
                        # Update Session State
                        st.session_state.user_info['password'] = new_hash
                        # 清除使用者列表快取，確保下次登入能讀取到新密碼
                        fetch_users_list.clear()
                        
                        st.success("密碼修改成功！")
                        time.sleep(1)

# --- 注入 CSS 以偽裝 Button 為純文字 (加強版) ---
st.markdown("""
<style>
/* 將 Expander 內的按鈕偽裝成純文字 */
div[data-testid="stExpander"] [data-testid="stButton"] button {
    border: none !important;
    background: transparent !important;
    color: inherit !important;
    text-decoration: none !important;
    padding: 0px !important;
    margin: 0px !important;
    height: auto !important;
    min-height: 0px !important;
    line-height: normal !important;
    font-size: 0.9rem !important;
    cursor: pointer !important;
    text-align: left !important;
    display: inline-block !important;
}

div[data-testid="stExpander"] button:hover {
    text-decoration: underline !important; /* 滑鼠移過時加底線作為提示 */
    color: #555 !important;
}

div[data-testid="stExpander"] button:focus {
    box-shadow: none !important;
    outline: none !important;
}
</style>
""", unsafe_allow_html=True)

if not st.session_state.logged_in:
    st.title("🚀 歡迎使用 Flashcard Pro")
    st.info("請登入以開始練習。預設密碼 1234。")
    
    st.divider()

    c_title, c_refresh = st.columns([8, 2])
    c_title.subheader("🏆 全班句型練習排行榜")
    if c_refresh.button("🔄 刷新數據"):
        st.cache_data.clear()
        st.rerun()

    # 讀取排行榜數據，按句型書分組
    all_users = fetch_users_list()

    # 結構: { book_name: [ {學生, completed, total, rate, last_active}, ... ] }
    books_data = {}

    for uid, u_data in all_users.items():
        s_stats = u_data.get("sentence_stats", {})
        if not s_stats: continue

        for book_id, stat in s_stats.items():
            if not isinstance(stat, dict): continue
            total = stat.get('total', 0)
            if total == 0: continue

            completed = stat.get('completed', 0)
            book_name = stat.get('name', book_id)

            # 將 Timestamp 轉換為字串
            last_active = stat.get('last_active')
            if hasattr(last_active, 'date'):
                last_active_str = last_active.strftime("%m-%d %H:%M")
            else:
                last_active_str = str(last_active) if last_active else ""

            if book_name not in books_data:
                books_data[book_name] = []

            books_data[book_name].append({
                "student": u_data.get('name', uid),
                "completed": completed,
                "total": total,
                "rate": completed / total if total > 0 else 0,
                "last_active": last_active_str
            })

    if books_data:
        for book_name, students in books_data.items():
            # 按完成率排序（高到低）
            students_sorted = sorted(students, key=lambda x: (-x['rate'], -x['completed']))

            st.markdown(f"#### 📘 {book_name}")

            for rank, s in enumerate(students_sorted, 1):
                pct = int(s['rate'] * 100)
                # 前三名使用獎牌 emoji
                if rank == 1:
                    rank_display = "🥇"
                elif rank == 2:
                    rank_display = "🥈"
                elif rank == 3:
                    rank_display = "🥉"
                else:
                    rank_display = f"{rank}."

                bar_html = f"""
                <div style="display: flex; align-items: center; margin-bottom: 6px; font-size: 0.9rem;">
                    <div style="width: 80px; min-width: 80px;">{rank_display} {s['student']}</div>
                    <div style="flex-grow: 1; background-color: #e0e0e0; border-radius: 6px; height: 14px; margin: 0 10px; overflow: hidden;">
                        <div style="width: {pct}%; background-color: #4CAF50; height: 100%;"></div>
                    </div>
                    <div style="width: 60px; min-width: 60px; text-align: right;">{s['completed']}/{s['total']}</div>
                    <div style="width: 90px; min-width: 90px; text-align: right; color: #888; font-size: 0.8rem;">{s['last_active']}</div>
                </div>
                """
                st.markdown(bar_html, unsafe_allow_html=True)

            st.write("")  # 間隔
    else:
        st.info("目前還沒有人開始練習句型，快登入成為第一名！")

else:
    u_vocab = st.session_state.u_vocab

    if menu == "學習儀表板":
        st.title("📊 學習儀表板")
        
        # 調整 Tab 順序：學習戰績表(原總表)在第一位
        tab_total, tab_v, tab_s = st.tabs(["學習戰績表", "單字學習", "句型練習"])
        
        # --- 學習戰績表 Tab (新設計) ---
        with tab_total:
            st.subheader("📈 學習戰績表")
            
            # 1. 單字概況 (Stacked Bar)
            st.markdown("#### 📚 單字課程進度")
            if u_vocab:
                df_v = pd.DataFrame(u_vocab)
                if 'Course' not in df_v.columns: df_v['Course'] = '未分類'
                if 'Date' not in df_v.columns: df_v['Date'] = 'N/A'
                
                courses = sorted(df_v['Course'].unique())
                for course in courses:
                    with st.expander(f"📘 {course}", expanded=True):
                        c_data = df_v[df_v['Course'] == course]
                        dates = sorted(c_data['Date'].unique(), reverse=True)
                        for d in dates:
                            d_data = c_data[c_data['Date'] == d]
                            total = len(d_data)
                            
                            mastered = len(d_data[d_data['Correct'] > 0])
                            learning = len(d_data[(d_data['Total'] > 0) & (d_data['Correct'] == 0)])
                            
                            p_mastered = mastered / total if total > 0 else 0
                            p_learning = learning / total if total > 0 else 0
                            p_empty = 1 - p_mastered - p_learning
                            
                            c1, c2 = st.columns([2, 8])
                            # 單字按鈕
                            c1.button(
                                f"📅 {d}", 
                                key=f"btn_vocab_{course}_{d}",
                                on_click=navigate_to_practice,
                                kwargs={"preset": f"   📅 {course} | {d}"}
                            )
                            with c2:
                                render_custom_progress_bar(f"({total}個)", p_mastered, p_learning, p_empty)
            else: st.info("尚無單字資料。")

            st.divider()

            # 2. 句型概況 (Stacked Bar)
            st.markdown("#### 🗣️ 句型書進度")
            catalogs = fetch_sentence_catalogs()
            if catalogs:
                catalog_names = list(catalogs.values())
                catalog_ids = list(catalogs.keys())
                user_progress = fetch_all_user_sentence_progress()
                
                for name, cid in zip(catalog_names, catalog_ids):
                    b_sentences = fetch_sentences_by_id(cid)
                    if not b_sentences: continue
                    
                    with st.expander(f"📙 {name}", expanded=True):
                        df_s = pd.DataFrame(b_sentences)
                        if 'Category' not in df_s.columns: df_s['Category'] = '未分類'
                        cats = sorted(df_s['Category'].unique())
                        
                        for cat in cats:
                            cat_sents = [s for s in b_sentences if s.get('Category') == cat]
                            tot = len(cat_sents)
                            
                            cnt_done = 0
                            cnt_progress = 0
                            
                            for s in cat_sents:
                                h = hash_string(s['Template'])
                                user_done = user_progress.get(h, [])
                                s_opts = s.get('Options', [])
                                
                                if not s_opts: continue
                                
                                intersection = len(set(s_opts).intersection(set(user_done)))
                                if intersection == len(s_opts):
                                    cnt_done += 1
                                elif intersection > 0:
                                    cnt_progress += 1
                            
                            p_done = cnt_done / tot if tot > 0 else 0
                            p_prog = cnt_progress / tot if tot > 0 else 0
                            p_empty = 1 - p_done - p_prog
                            
                            c1, c2 = st.columns([2, 8])
                            # 句型按鈕
                            c1.button(
                                f"🏷️ {cat}",
                                key=f"btn_sent_{name}_{cat}",
                                on_click=navigate_to_sentence,
                                kwargs={"book": name, "cat": cat}
                            )
                            with c2:
                                render_custom_progress_bar(f"({tot}句)", p_done, p_prog, p_empty)

        # --- 單字 Tab ---
        with tab_v:
            if not u_vocab:
                st.info("尚無單字資料。")
                if st.button("🔄 同步雲端"): sync_vocab_from_db(); st.rerun()
            else:
                options = get_course_options(u_vocab)
                # 直接使用 key="vocab_dash_filter" 從 session state 取值，不使用 index
                selection = st.selectbox("單字篩選範圍：", options, key="vocab_dash_filter")
                
                filtered_vocab = filter_vocab_data(u_vocab, selection)
                
                col1, col2, col3 = st.columns(3)
                
                # Metric 1: 總單字數
                total_vocab_count = len(filtered_vocab)
                col1.metric("範圍內單字數", total_vocab_count)
                
                # Metric 2: 練習覆蓋率 (有做過練習的單字數 / 總單字數)
                practiced_count = len([v for v in filtered_vocab if v.get('Total', 0) > 0])
                coverage_rate = (practiced_count / total_vocab_count * 100) if total_vocab_count > 0 else 0
                col2.metric("練習覆蓋率", f"{coverage_rate:.1f}%", help="有練習過的單字比例")
                
                # Metric 3: 答題正確率 (總答對 / 總答題) -> 品質指標
                total_correct = sum(v.get('Correct', 0) for v in filtered_vocab)
                total_attempts = sum(v.get('Total', 0) for v in filtered_vocab)
                accuracy_rate = (total_correct / total_attempts * 100) if total_attempts > 0 else 0
                col3.metric("答題正確率", f"{accuracy_rate:.1f}%", help="所有練習次數中的正確比例")
                
                st.divider()
                st.dataframe(pd.DataFrame(filtered_vocab)[['English', 'Chinese_1', 'POS', 'Course', 'Date', 'Correct', 'Total']], use_container_width=True, hide_index=True)

        # --- 句型 Tab ---
        with tab_s:
            catalogs = fetch_sentence_catalogs()
            if not catalogs:
                st.info("尚無句型資料庫。")
            else:
                # 準備選單
                catalog_names = list(catalogs.values())
                catalog_ids = list(catalogs.keys())
                
                combined_s_options = []
                # 書名 -> ID 對照
                book_map = {name: cid for cid, name in catalogs.items()}

                for name, cid in zip(catalog_names, catalog_ids):
                    combined_s_options.append(f"{name} (全部)")
                    book_sentences = fetch_sentences_by_id(cid)
                    if book_sentences:
                        df_b = pd.DataFrame(book_sentences)
                        if 'Category' in df_b.columns:
                            cats = sorted(df_b['Category'].unique())
                            for c in cats:
                                combined_s_options.append(f"{name} | {c}")
                
                # 直接使用 key="sentence_dash_filter" 從 session state 取值，不使用 index
                s_selection = st.selectbox("句型篩選範圍：", combined_s_options, key="sentence_dash_filter")
                
                if " (全部)" in s_selection:
                    book_name = s_selection.replace(" (全部)", "")
                    target_id = book_map.get(book_name)
                    target_sentences = fetch_sentences_by_id(target_id)
                else:
                    book_name, category = s_selection.split(" | ")
                    target_id = book_map.get(book_name)
                    all_sentences = fetch_sentences_by_id(target_id)
                    target_sentences = [s for s in all_sentences if s.get('Category') == category]
                
                if not target_sentences:
                    st.info("無句型資料。")
                else:
                    # 統計數據
                    user_progress = fetch_all_user_sentence_progress()
                    
                    total_s_count = len(target_sentences)
                    fully_completed_count = 0
                    
                    progress_table = []
                    
                    for s in target_sentences:
                        h = hash_string(s['Template'])
                        user_done = user_progress.get(h, [])
                        s_opts = s.get('Options', [])
                        
                        is_done = set(s_opts).issubset(set(user_done))
                        if is_done: fully_completed_count += 1
                        
                        progress_table.append({
                            "分類": s.get('Category', ''),
                            "句型": s['Template'],
                            "選項數": len(s_opts),
                            "已完成": len(set(s_opts).intersection(set(user_done))),
                            "狀態": "✅" if is_done else "💪"
                        })
                    
                    sc1, sc2, sc3 = st.columns(3)
                    sc1.metric("總句數", total_s_count)
                    sc2.metric("已完成句數", fully_completed_count)
                    s_rate = (fully_completed_count / total_s_count * 100) if total_s_count > 0 else 0
                    sc3.metric("完成率", f"{s_rate:.1f}%")

                    st.divider()
                    st.dataframe(pd.DataFrame(progress_table), use_container_width=True, hide_index=True)
                    
                    # --- 新增：清除紀錄按鈕 ---
                    if st.button("🗑️ 清除所有句型練習紀錄 (無法復原)", type="primary"):
                        clear_user_sentence_history(target_id)
                        st.success("已清除所有進度！")
                        time.sleep(1)
                        st.rerun()


    elif menu == "單字管理":
        st.title("⚙️ 單字管理")
        tab1, tab2, tab3, tab4 = st.tabs(["批次輸入", "手動修改", "單字刪除", "📂 CSV 匯入"])
        
        with tab1:
            c_name = st.text_input("課程名稱:", value="新課程")
            c_date = st.date_input("日期:", value=date.today())
            text_area = st.text_area("輸入內容:")
            if st.button("啟動 AI 處理"):
                with st.spinner("解析中..."):
                    st.session_state.pending_items = call_gemini_to_complete(text_area, c_name, c_date)
            if st.session_state.get("pending_items"):
                edited = st.data_editor(pd.DataFrame(st.session_state.pending_items), use_container_width=True, hide_index=True)
                if st.button("💾 確認儲存", type="primary"):
                    path = get_vocab_path()
                    for it in edited.to_dict('records'): db.collection(path).add(it)
                    st.session_state.pending_items = None
                    sync_vocab_from_db(); st.success("儲存成功！"); st.rerun()
        
        with tab2:
            if u_vocab:
                opts = get_course_options(u_vocab)
                sel = st.selectbox("請選擇修改範圍：", opts, key="edit_filter")
                filtered = filter_vocab_data(u_vocab, sel)
                if filtered:
                    edited_df = st.data_editor(pd.DataFrame(filtered), column_order=["English", "Group", "Chinese_1", "Chinese_2", "Example"], use_container_width=True, hide_index=True)
                    if st.button("💾 儲存修改"):
                        for _, row in edited_df.iterrows(): update_word_data(row.get('id'), {k: v for k, v in row.to_dict().items() if k != 'id'})
                        st.success("更新完成！"); st.rerun()
                else: st.warning("選取範圍內無單字。")
            else: st.info("無單字資料。")

        with tab3:
            if u_vocab:
                opts = get_course_options(u_vocab)
                sel = st.selectbox("請選擇刪除範圍：", opts, key="delete_filter")
                filtered = filter_vocab_data(u_vocab, sel)
                if filtered:
                    # 加入全選 Checkbox
                    col_check, _ = st.columns([1, 6])
                    with col_check:
                        select_all = st.checkbox("全選", value=False, key="del_select_all")
                    
                    df_del = pd.DataFrame(filtered)
                    # 根據 Checkbox 設定預設值
                    df_del.insert(0, "選取", select_all)
                    
                    res = st.data_editor(
                        df_del[['選取', 'id', 'English', 'Chinese_1', 'Course']], 
                        column_config={"id": None}, 
                        use_container_width=True, 
                        hide_index=True
                    )
                    
                    to_delete = res[res["選取"] == True]["id"].tolist()
                    if st.button(f"確認刪除 ({len(to_delete)} 個)", type="primary"):
                        delete_words_from_db(to_delete)
                        sync_vocab_from_db(); st.success("已刪除！"); st.rerun()
                else: st.warning("無資料。")
            else: st.info("無資料。")

        with tab4:
            st.subheader("📂 從 CSV 檔案匯入")
            uploaded_file = st.file_uploader("選擇 CSV 檔案", type=["csv"])
            col_a, col_b = st.columns(2)
            default_course = col_a.text_input("預設課程名稱", "匯入單字")
            default_date = col_b.date_input("預設日期", value=date.today())
            
            if uploaded_file is not None:
                try:
                    df_csv = pd.read_csv(uploaded_file)
                    st.write(f"預覽上傳內容 (共 {len(df_csv)} 筆)：")
                    st.dataframe(df_csv)
                    
                    if "English" in df_csv.columns and "Chinese_1" in df_csv.columns:
                        if st.button("🚀 開始匯入資料庫", type="primary"):
                            with st.spinner("正在匯入..."):
                                df_csv = df_csv.fillna("")
                                items_to_add = []
                                for _, row in df_csv.iterrows():
                                    # CSV 匯入也改為讀取 POS
                                    pos_val = str(row.get("POS", str(row.get("Group", "")))).strip()
                                    if not pos_val: pos_val = "未分類"
                                    
                                    course_val = str(row.get("Course", "")).strip()
                                    if not course_val: course_val = default_course
                                    
                                    date_val = str(row.get("Date", "")).strip()
                                    if not date_val: date_val = str(default_date)

                                    item = {
                                        "English": str(row.get("English", "")),
                                        "Chinese_1": str(row.get("Chinese_1", "")),
                                        "Chinese_2": str(row.get("Chinese_2", "")),
                                        "POS": pos_val,
                                        "Example": str(row.get("Example", "")),
                                        "Course": course_val,
                                        "Date": date_val,
                                        "Correct": int(row.get("Correct", 0)) if str(row.get("Correct", "0")).isdigit() else 0,
                                        "Total": int(row.get("Total", 0)) if str(row.get("Total", "0")).isdigit() else 0
                                    }
                                    items_to_add.append(item)
                                save_new_words_to_db(items_to_add)
                                sync_vocab_from_db()
                                st.success(f"成功匯入 {len(items_to_add)} 筆單字！")
                                time.sleep(1)
                                st.rerun()
                    else:
                        st.error("CSV 格式錯誤：必須包含 'English' 與 'Chinese_1' 欄位。")
                except Exception as e:
                    st.error(f"讀取檔案失敗: {e}")

    elif menu == "單字練習":
        st.title("✏️ 單字練習")
        options = get_course_options(u_vocab)
        # 直接使用 key="practice_filter" 從 session state 取值，不使用 index
        selection = st.selectbox("🎯 選擇練習範圍：", options, key="practice_filter")
        
        current_set = filter_vocab_data(u_vocab, selection)
        
        tab_p, tab_t = st.tabs(["快閃練習", "實力測驗"])
        
        with tab_p:
            if not current_set: st.info("範圍內無單字。")
            else:
                if st.session_state.practice_idx >= len(current_set): st.session_state.practice_idx = 0
                target = current_set[st.session_state.practice_idx]
                
                with st.container(border=True):
                    st.caption(f"{target.get('Course')} | {st.session_state.practice_idx + 1}/{len(current_set)}")
                    st.header(target['English'])
                    
                    if not st.session_state.practice_reveal:
                        text_to_speech(target['English'])
                    if st.session_state.practice_reveal:
                        text_to_speech(target.get('Example', ''))
                    
                    if st.session_state.practice_reveal:
                        st.divider()
                        st.markdown(f"**中文：** {target['Chinese_1']} ({target.get('POS')})")
                        st.info(f"例句：{target.get('Example', '')}")
                    st.write("")
                    c1, c2, c3 = st.columns(3)
                    
                    if c1.button("上一個", use_container_width=True):
                        st.session_state.practice_idx = (st.session_state.practice_idx-1)%len(current_set)
                        st.session_state.practice_reveal=False
                        st.session_state.audio_to_play = current_set[st.session_state.practice_idx]['English']
                        st.rerun()
                        
                    if c2.button("翻面", use_container_width=True):
                        st.session_state.practice_reveal = not st.session_state.practice_reveal
                        if st.session_state.practice_reveal:
                            st.session_state.audio_to_play = target.get('Example', '')
                        st.rerun()
                        
                    if c3.button("下一個", use_container_width=True):
                        st.session_state.practice_idx = (st.session_state.practice_idx+1)%len(current_set)
                        st.session_state.practice_reveal=False
                        st.session_state.audio_to_play = current_set[st.session_state.practice_idx]['English']
                        st.rerun()
                keyboard_bridge()

        with tab_t:
            if st.session_state.get("show_test_toast"):
                st.toast("✅ 正確！"); st.session_state.show_test_toast = False
            
            if not current_set: st.info("範圍內無單字。")
            else:
                if "test_pool" not in st.session_state or st.button("換一批題目"):
                    st.session_state.test_pool = random.sample(current_set, min(10, len(current_set)))
                    st.session_state.t_idx = 0; st.session_state.t_score = 0; st.session_state.quiz_history = []
                    st.rerun()
                
                if st.session_state.t_idx < len(st.session_state.test_pool):
                    curr = st.session_state.test_pool[st.session_state.t_idx]
                    with st.form(key=f"q_f_{st.session_state.t_idx}", border=True):
                        st.caption(f"進度：{st.session_state.t_idx + 1} / {len(st.session_state.test_pool)}")
                        st.header(curr['English'])
                        ans = st.text_input("輸入中文：")
                        if st.form_submit_button("提交", use_container_width=True):
                            ok = ans and (ans in str(curr['Chinese_1']) or str(curr['Chinese_1']) in ans)
                            st.session_state.quiz_history.append({"英文": curr['English'], "你的輸入": ans, "正確答案": curr['Chinese_1'], "is_correct": ok})
                            if ok: st.session_state.t_score += 1
                            update_word_data(curr.get('id'), {"Correct": int(curr.get('Correct', 0)) + (1 if ok else 0), "Total": int(curr.get('Total', 0)) + 1})
                            st.session_state.t_idx += 1; st.rerun()
                    auto_focus_input()
                else:
                    st.success(f"測驗得分：{st.session_state.t_score} / {len(st.session_state.test_pool)}")
                    df_h = pd.DataFrame(st.session_state.quiz_history)
                    wrongs = df_h[df_h["is_correct"] == False]
                    if not wrongs.empty:
                        st.subheader("❌ 錯誤回顧")
                        st.table(wrongs[["英文", "你的輸入", "正確答案"]])

    elif menu == "句型口說":
        st.title("🗣️ 句型口說挑戰")
        catalogs = fetch_sentence_catalogs()
        if not catalogs:
            st.info("目前雲端沒有句型資料庫。")
            if INITIAL_SENTENCES:
                st.warning("⚠️ 使用預設題庫模式 (未連結雲端)"); current_sentences = INITIAL_SENTENCES
            else: st.stop()
        else:
            catalog_names = list(catalogs.values())
            catalog_ids = list(catalogs.keys())
            
            combined_options = []
            book_map = {name: cid for cid, name in catalogs.items()}

            for name, cid in zip(catalog_names, catalog_ids):
                combined_options.append(f"{name} (全部)")
                book_sentences = fetch_sentences_by_id(cid)
                if book_sentences:
                    df_b = pd.DataFrame(book_sentences)
                    if 'Category' in df_b.columns:
                        cats = sorted(df_b['Category'].unique())
                        for c in cats:
                            combined_options.append(f"{name} | {c}")
            
            # 直接使用 key="sentence_filter" 從 session state 取值，不使用 index
            selection = st.selectbox("選擇練習範圍：", combined_options, key="sentence_filter")
            
            if " (全部)" in selection:
                book_name = selection.replace(" (全部)", "")
                target_id = book_map.get(book_name)
                current_sentences = fetch_sentences_by_id(target_id)
                # 記錄當前題庫 ID 供儲存時使用
                st.session_state.current_dataset_id = target_id
            else:
                book_name, category = selection.split(" | ")
                target_id = book_map.get(book_name)
                # 記錄當前題庫 ID 供儲存時使用
                st.session_state.current_dataset_id = target_id
                all_book_sentences = fetch_sentences_by_id(target_id)
                current_sentences = [s for s in all_book_sentences if s.get('Category') == category]
        
        if not current_sentences: st.info("此範圍內無題目。")
        else:
            # 智慧跳轉：如果是剛進入頁面（或切換題庫），嘗試跳到第一題未完成的
            # 我們用 session_state.last_sentence_filter_sig 來判斷是否切換了題庫
            current_filter_sig = selection
            if st.session_state.last_sentence_filter_sig != current_filter_sig:
                # 切換了題庫，尋找第一個未完成的
                user_progress = fetch_all_user_sentence_progress()
                found_idx = 0
                for i, s in enumerate(current_sentences):
                    h = hash_string(s['Template'])
                    done = user_progress.get(h, [])
                    opts = s.get('Options', [])
                    if not set(opts).issubset(set(done)):
                        found_idx = i
                        break
                st.session_state.sentence_idx = found_idx
                st.session_state.completed_options = set() # 重置當前題目的完成狀態
                st.session_state.last_sentence_filter_sig = current_filter_sig
                if "loaded_hash" in st.session_state: del st.session_state.loaded_hash
            
            # 確保索引不越界
            if st.session_state.sentence_idx >= len(current_sentences):
                st.session_state.sentence_idx = 0
            
            curr_sent = current_sentences[st.session_state.sentence_idx]
            template = curr_sent['Template']
            options = curr_sent['Options']
            
            template_hash = hash_string(template)
            if "loaded_hash" not in st.session_state or st.session_state.loaded_hash != template_hash:
                st.session_state.completed_options = load_user_sentence_progress(template_hash)
                st.session_state.loaded_hash = template_hash

            progress_placeholder = st.empty()
            def render_progress():
                c = len(st.session_state.completed_options); t = len(options)
                progress_placeholder.progress(c / t, text=f"完成進度: {c}/{t}")
            render_progress()
            
            st.subheader(f"題目 ({curr_sent.get('Category', '一般')})")
            st.markdown(f"### {template}", unsafe_allow_html=True)
            
            options_placeholder = st.empty()
            def render_options_status():
                with options_placeholder.container():
                    st.caption("請一口氣唸出包含下方所有單字的句子：")
                    cols = st.columns(len(options))
                    for i, opt in enumerate(options):
                        if opt in st.session_state.completed_options: cols[i].success(f"✅ {opt}")
                        else: cols[i].info(f"{opt}")
            render_options_status()
            
            st.divider()
            st.write("請按下錄音，並嘗試唸出所有句子 (例如: This test is very important. This rule is...)")
            audio_val = st.audio_input("🔴 點擊開始錄音", key=f"rec_{st.session_state.sentence_idx}")
            
            if audio_val:
                with st.spinner("AI 正在分析您的錄音..."):
                    remaining = [opt for opt in options if opt not in st.session_state.completed_options]
                    if not remaining: st.success("本題已全部完成！")
                    else:
                        result = check_audio_batch(audio_val, template, options)
                        new_corrects = result.get("correct_options", [])
                        if new_corrects:
                            for nc in new_corrects:
                                if nc in options: st.session_state.completed_options.add(nc)
                            save_user_sentence_progress(template, st.session_state.completed_options, dataset_id=st.session_state.current_dataset_id)
                            st.success(f"🎉 辨識出：{', '.join(new_corrects)}")
                            render_options_status(); render_progress() 
                            if len(st.session_state.completed_options) == len(options): st.balloons()                            
                        else:
                            st.warning("🤔 似乎沒有辨識到新的正確句子，請再試一次。")
                        with st.expander("查看完整聽寫內容", expanded=True):
                            st.write(result.get("heard"))
                            st.caption(f"AI 建議: {result.get('feedback')}")
            
            st.write("")
            c1, c2 = st.columns(2)
            if c1.button("← 上一題", use_container_width=True):
                st.session_state.sentence_idx = (st.session_state.sentence_idx - 1) % len(current_sentences)
                st.session_state.completed_options = set()
                del st.session_state.loaded_hash
                st.rerun()
            if c2.button("下一題 →", use_container_width=True):
                st.session_state.sentence_idx = (st.session_state.sentence_idx + 1) % len(current_sentences)
                st.session_state.completed_options = set()
                del st.session_state.loaded_hash
                st.rerun()
            
            keyboard_bridge()

st.divider()
st.caption("Flashcard Pro - 資料已加密並同步至 Firestore")