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
import re
from datetime import date, datetime, timedelta, timezone
from google.cloud import firestore
from google.oauth2 import service_account
from streamlit.components.v1 import html
from streamlit_cookies_controller import CookieController
from drill_component import generate_drill_html

# --- 新增：嘗試匯入 SpeechRecognition (保留供其他用途，但主功能改用 Gemini Audio) ---
try:
    import speech_recognition as sr
except ImportError:
    sr = None

# --- 0. 設定與常數 ---
st.set_page_config(page_title="Flashcard Pro 雲端版", page_icon="✨", layout="wide")

# --- 全域 CSS（手機適配） ---
st.markdown("""<style>
@media (max-width: 640px) {
    .block-container { padding-left: 1rem !important; padding-right: 1rem !important; }
    .stButton > button { min-height: 44px; }
    .stDataFrame, .stTable { overflow-x: auto !important; }
}
</style>""", unsafe_allow_html=True)

# 讀取 Secrets
GEMINI_API_KEY = st.secrets.get("GEMINI_API_KEY", "")
GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_API_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
GEMINI_HEADERS = {"Referer": "https://flashcard-techeasy.streamlit.app/"}

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
cookie_controller = CookieController()
APP_ID = st.secrets.get("APP_ID", "flashcard-pro-v1")
USER_LIST_PATH = f"artifacts/{APP_ID}/public/data/users"
SENTENCE_CATALOG_PATH = f"artifacts/{APP_ID}/public/data/sentences"
SENTENCE_DATA_BASE_PATH = f"artifacts/{APP_ID}/public/data"
SHARED_VOCAB_CATALOG_PATH = f"artifacts/{APP_ID}/public/data/shared_vocab"
SHARED_VOCAB_DATA_PATH = f"artifacts/{APP_ID}/public/data/shared_vocab_data"

# --- 免費方案限制 ---
FREE_DAILY_VOCAB_AI_LIMIT = 3   # 單字補全每日上限
VOCAB_AI_MAX_LINES = 100        # 單字補全每次最多行數
FREE_DAILY_DRILL_LIMIT = 30     # 句型口說 AI 判讀每日上限（免費用戶）

# --- LINE Bot (Messaging API) ---
LINE_CHANNEL_ACCESS_TOKEN = st.secrets.get("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_TEACHER_USER_ID = st.secrets.get("LINE_TEACHER_USER_ID", "")

# --- 確認對話框（危險操作） ---

@st.dialog("⚠️ 確認清除")
def confirm_clear_sentence_progress():
    target_id = st.session_state.get("_confirm_clear_target")
    st.warning("此操作無法復原！將清除所有句型練習紀錄。")
    c1, c2 = st.columns(2)
    if c1.button("取消", use_container_width=True):
        st.session_state.pop("_confirm_clear_target", None)
        st.rerun()
    if c2.button("確認清除", type="primary", use_container_width=True):
        clear_user_sentence_history(target_id)
        st.session_state.pop("_confirm_clear_target", None)
        st.rerun()

@st.dialog("⚠️ 確認刪除單字")
def confirm_delete_vocab():
    ids = st.session_state.get("_confirm_delete_ids", [])
    st.warning(f"即將刪除 {len(ids)} 個單字，此操作無法復原。")
    c1, c2 = st.columns(2)
    if c1.button("取消", use_container_width=True):
        st.session_state.pop("_confirm_delete_ids", None)
        st.rerun()
    if c2.button("確認刪除", type="primary", use_container_width=True):
        delete_words_from_db(ids)
        sync_vocab_from_db()
        st.session_state.pop("_confirm_delete_ids", None)
        st.rerun()

# --- 2. 工具函式 ---

def send_line_notify(message):
    """透過 LINE Messaging API push message 發送通知給老師"""
    if not LINE_CHANNEL_ACCESS_TOKEN or not LINE_TEACHER_USER_ID:
        return False, "LINE Bot 尚未設定。"
    try:
        resp = requests.post(
            "https://api.line.me/v2/bot/message/push",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
            },
            json={
                "to": LINE_TEACHER_USER_ID,
                "messages": [{"type": "text", "text": message}],
            },
            timeout=10,
        )
        if resp.status_code == 200:
            return True, "通知已發送。"
        return False, f"發送失敗 (status={resp.status_code})"
    except Exception as e:
        return False, f"發送失敗：{e}"

def hash_string(text):
    return hashlib.md5(text.encode('utf-8')).hexdigest()

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def is_premium(user_info):
    """檢查使用者是否為有效的 Premium 用戶"""
    if not user_info:
        return False
    if user_info.get("plan") != "premium":
        return False
    expiry = user_info.get("plan_expiry")
    if not expiry:
        return False
    if hasattr(expiry, 'date'):
        return expiry.date() >= date.today()
    if isinstance(expiry, str):
        try:
            return datetime.fromisoformat(expiry).date() >= date.today()
        except Exception:
            return False
    return False

# --- 單字補全額度（免費用戶每日 3 次）---

def check_vocab_ai_usage():
    """檢查免費用戶的單字補全每日額度，回傳 (可使用, 剩餘次數)"""
    if is_premium(st.session_state.get("user_info")):
        return True, -1
    today_str = str(date.today())
    if st.session_state.get("vocab_ai_date") != today_str:
        st.session_state.vocab_ai_date = today_str
        st.session_state.vocab_ai_count = 0
    used = st.session_state.get("vocab_ai_count", 0)
    remaining = FREE_DAILY_VOCAB_AI_LIMIT - used
    return remaining > 0, remaining

def consume_vocab_ai_usage():
    """消耗一次單字補全額度（免費用戶）"""
    if is_premium(st.session_state.get("user_info")):
        return
    st.session_state.vocab_ai_count = st.session_state.get("vocab_ai_count", 0) + 1

def get_drill_remaining():
    """取得免費用戶今日句型口說 AI 判讀剩餘次數。Premium 回傳 -1（無限）"""
    if is_premium(st.session_state.get("user_info")):
        return -1
    today_str = str(date.today())
    try:
        user_name = st.session_state.get("current_user_name")
        if not user_name or not db:
            return FREE_DAILY_DRILL_LIMIT
        doc = db.collection(USER_LIST_PATH).document(user_name).get()
        if doc.exists:
            usage = doc.to_dict().get("ai_usage", {})
            used = usage.get("drill_count", {}).get(today_str, 0)
            return max(0, FREE_DAILY_DRILL_LIMIT - int(used))
    except Exception:
        pass
    return FREE_DAILY_DRILL_LIMIT

# --- 語音辨識次數紀錄（不限制，但寫入 Firestore 供統計）---

def record_ai_usage(usage_type, token_count):
    """
    紀錄 AI 使用量，寫入 Firestore。
    usage_type: "speech" 或 "vocab"
    token_count: 本次消耗的 token 數（從 Gemini API usageMetadata 取得）
    Firestore 結構: ai_usage.{type}.{date} = 累計 token 數
    """
    if not db or not st.session_state.get("logged_in"):
        return
    user_name = st.session_state.get("current_user_name")
    if not user_name or token_count <= 0:
        return
    today_str = str(date.today())
    try:
        user_ref = db.collection(USER_LIST_PATH).document(user_name)
        user_ref.set({
            "ai_usage": {
                usage_type: {
                    today_str: firestore.Increment(token_count)
                }
            }
        }, merge=True)
    except Exception:
        pass  # 紀錄失敗不影響使用

# --- 自助註冊 ---

RANDOM_COLORS = ["#FF69B4", "#1E90FF", "#32CD32", "#FF6347", "#9370DB",
                 "#FF8C00", "#20B2AA", "#DA70D6", "#4682B4", "#F4A460"]

def register_new_user(name, password):
    """
    註冊新用戶：自動產生學號、隨機顏色、7天 Premium 試用。
    回傳 (success: bool, message: str)
    """
    if not db:
        return False, "資料庫連線失敗，請稍後再試。"
    name = name.strip()
    if not name:
        return False, "名稱不能為空。"
    if len(name) > 20:
        return False, "名稱不能超過 20 個字元。"
    if not password or len(password) < 4:
        return False, "密碼至少需要 4 個字元。"

    # 檢查名稱是否已存在
    existing = db.collection(USER_LIST_PATH).document(name).get()
    if existing.exists:
        return False, f"名稱「{name}」已被使用，請換一個。"

    # 自動產生學號：S + 3位數字，從現有最大編號遞增
    existing_users = fetch_users_list()
    max_num = 0
    for _, u in existing_users.items():
        uid = u.get("id", "")
        if uid.startswith("S") and uid[1:].isdigit() and uid != "S999":
            max_num = max(max_num, int(uid[1:]))
    auto_id = f"S{max_num + 1:03d}"

    # 隨機顏色
    color = random.choice(RANDOM_COLORS)

    # 7天 Premium 試用
    trial_expiry = datetime.now() + timedelta(days=7)

    user_data = {
        "name": name,
        "id": auto_id,
        "password": hash_password(password),
        "color": color,
        "plan": "premium",
        "plan_expiry": trial_expiry,
        "plan_note": "7-day free trial",
    }
    db.collection(USER_LIST_PATH).document(name).set(user_data)

    # 清除使用者列表快取
    fetch_users_list.clear()

    return True, f"註冊成功！歡迎 {name}，享有 7 天免費 Premium 試用。"

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
# 單字補全額度追蹤（免費方案）
if "vocab_ai_count" not in st.session_state:
    st.session_state.vocab_ai_count = 0
if "vocab_ai_date" not in st.session_state:
    st.session_state.vocab_ai_date = str(date.today())
if "pending_ocr_items" not in st.session_state:
    st.session_state.pending_ocr_items = None
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
if "drill_completion_count" not in st.session_state:
    st.session_state.drill_completion_count = 0
# 練習時長追蹤
if "practice_last_active" not in st.session_state:
    st.session_state.practice_last_active = None
if "practice_seconds_today" not in st.session_state:
    st.session_state.practice_seconds_today = 0
if "practice_seconds_last_saved" not in st.session_state:
    st.session_state.practice_seconds_last_saved = 0

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
    """讀取公用題庫列表，回傳 {id: {name, is_premium}}"""
    if not db: return {}
    docs = db.collection(SENTENCE_CATALOG_PATH).stream()
    result = {}
    for d in docs:
        data = d.to_dict()
        result[d.id] = {
            "name": data.get("name", d.id),
            "is_premium": data.get("is_premium", False),
        }
    return result

@st.cache_data(ttl=600)
def fetch_sentences_by_id(dataset_id):
    """讀取特定題庫的句型，並依照 Order 排序"""
    if not db: return []
    path = f"{SENTENCE_DATA_BASE_PATH}/{dataset_id}"
    docs = db.collection(path).stream()
    data = [d.to_dict() for d in docs]
    sorted_data = sorted(data, key=lambda x: x.get('Order', 9999))
    return sorted_data

@st.cache_data(ttl=600)
def fetch_shared_vocab_catalogs():
    """讀取公用單字集目錄，回傳 {set_id: {name, word_count, courses}}"""
    if not db: return {}
    docs = db.collection(SHARED_VOCAB_CATALOG_PATH).stream()
    result = {}
    for d in docs:
        data = d.to_dict()
        result[d.id] = {
            "name": data.get("name", d.id),
            "word_count": data.get("word_count", 0),
            "courses": data.get("courses", []),
        }
    return result

@st.cache_data(ttl=600)
def fetch_shared_vocab_words(set_id):
    """讀取公用單字集的所有單字（單一文件讀取）"""
    if not db: return []
    doc = db.collection(SHARED_VOCAB_DATA_PATH).document(set_id).get()
    if doc.exists:
        return doc.to_dict().get("words", [])
    return []

def get_star_display(count):
    """根據完成輪數回傳星級顯示"""
    if count >= 5: return "⭐⭐⭐"
    if count >= 3: return "⭐⭐"
    if count >= 1: return "⭐"
    return ""

def load_user_sentence_progress(template_hash):
    path = get_sentence_progress_path()
    if not db or not path: return set(), 0
    doc = db.collection(path).document(template_hash).get()
    if doc.exists:
        data = doc.to_dict()
        return set(data.get("completed_options", [])), int(data.get("completion_count", 0))
    return set(), 0

def fetch_all_user_sentence_progress():
    path = get_sentence_progress_path()
    if not db or not path: return {}
    docs = db.collection(path).stream()
    result = {}
    for d in docs:
        data = d.to_dict()
        result[d.id] = {
            "completed_options": data.get("completed_options", []),
            "completion_count": int(data.get("completion_count", 0)),
        }
    return result

# --- 新增：更新使用者統計摘要 ---
def update_user_stats_summary(dataset_id):
    """計算並更新使用者的該題庫統計資訊"""
    if not db or not dataset_id: return
    user_name = st.session_state.get("current_user_name")
    if not user_name: return

    # 1. 取得題庫資訊 (利用快取)
    sentences = fetch_sentences_by_id(dataset_id)
    catalogs = fetch_sentence_catalogs()
    cat_info = catalogs.get(dataset_id)
    dataset_name = cat_info["name"] if cat_info else dataset_id

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

def save_user_sentence_progress(template_str, completed_list, dataset_id=None, increment_count=False, round_data=None):
    """儲存使用者對某句型的練習進度，並標記來源題庫 ID"""
    path = get_sentence_progress_path()
    if not db or not path: return
    template_hash = hash_string(template_str)
    data = {
        "template_text": template_str,
        "completed_options": list(completed_list),
        "last_updated": firestore.SERVER_TIMESTAMP
    }
    if dataset_id:
        data["dataset_id"] = dataset_id
    if increment_count:
        data["completion_count"] = firestore.Increment(1)

    # 追加 round 資料到 rounds 陣列
    if round_data:
        data["rounds"] = firestore.ArrayUnion([round_data])

    db.collection(path).document(template_hash).set(data, merge=True)

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
        print(f"[Gemini Speech] Prompt loaded from file, length={len(base_prompt)}, contains 'intended': {'intended' in base_prompt}")
    else:
        print(f"[Gemini Speech] WARNING: prompt file not found at '{prompt_file}', cwd={os.getcwd()}")

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

    # 讀取音訊 Bytes 並自動偵測格式
    audio_file.seek(0)
    audio_bytes = audio_file.read()
    encoded_audio = base64.b64encode(audio_bytes).decode('utf-8')

    # 根據檔頭判斷 MIME type
    if audio_bytes[:4] == b'RIFF':
        audio_mime = "audio/wav"
    elif audio_bytes[:4] == b'OggS':
        audio_mime = "audio/ogg"
    else:
        audio_mime = "audio/webm"  # 瀏覽器 MediaRecorder 預設格式

    # --- 嘗試 1：Gemini 多模態 (音訊直接輸入) ---
    ai_corrects = []
    ai_transcript = ""
    ai_feedback = ""
    gemini_success = False

    gemini_payload = {
        "contents": [{
            "parts": [
                {"text": prompt},
                {"inline_data": {"mime_type": audio_mime, "data": encoded_audio}}
            ]
        }],
        "generationConfig": {"responseMimeType": "application/json"}
    }
    
    token_count = 0
    try:
        print(f"[Gemini Speech] Calling API... model={GEMINI_MODEL}")
        res = requests.post(f"{GEMINI_API_URL}?key={GEMINI_API_KEY}", json=gemini_payload, headers=GEMINI_HEADERS, timeout=30)
        print(f"[Gemini Speech] API status={res.status_code}")
        if res.status_code != 200:
            print(f"[Gemini Speech] API error body: {res.text[:500]}")
        if res.status_code == 200:
            res_json = res.json()
            content_text = res_json['candidates'][0]['content']['parts'][0]['text']

            # 提取 token 使用量
            usage = res_json.get("usageMetadata", {})
            token_count = usage.get("totalTokenCount", 0)

            # 清理 JSON 字串
            if "```json" in content_text:
                content_text = content_text.split("```json")[1].split("```")[0]
            elif "```" in content_text:
                content_text = content_text.split("```")[1].split("```")[0]

            ai_result = json.loads(content_text.strip())
            print(f"[Gemini Speech] raw response: {ai_result}")  # debug log

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
        print(f"[Gemini Speech] Error: {e}")
        import traceback; traceback.print_exc()

    # 不管成功與否，只要有 token 就記錄
    if token_count > 0:
        record_ai_usage("speech", token_count)

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
        res = requests.post(f"{GEMINI_API_URL}?key={GEMINI_API_KEY}", json=payload, headers=GEMINI_HEADERS, timeout=30)
        if res.status_code == 200:
            res_json = res.json()
            text = res_json['candidates'][0]['content']['parts'][0]['text']

            # 記錄 token 使用量
            usage = res_json.get("usageMetadata", {})
            token_count = usage.get("totalTokenCount", 0)
            if token_count > 0:
                record_ai_usage("vocab", token_count)

            raw_items = []
            for line in text.strip().split('\n'):
                if '|' in line:
                    p = [i.strip() for i in line.split('|')]
                    if len(p) >= 5:
                        raw_items.append({
                            "English": p[0], "POS": p[1], "Chinese_1": p[2], "Chinese_2": p[3],
                            "Example": p[4], "Course": course_name, "Date": str(course_date),
                            "Correct": 0, "Total": 0,
                            "srs_interval": 0, "srs_ease": 2.5, "srs_due": "", "srs_streak": 0, "srs_last_review": ""
                        })
            return raw_items
    except: pass
    return []

def call_gemini_ocr(image_files, course_name, course_date):
    """從課本圖片中辨識英文單字，回傳結構化單字列表"""
    if not image_files: return []

    # 讀取基礎 prompt（與 call_gemini_to_complete 相同）
    prompt_file = "system_prompt.md"
    if st.secrets.get("system_prompt"):
        base_prompt = st.secrets["system_prompt"]
    elif os.path.exists(prompt_file):
        with open(prompt_file, "r", encoding="utf-8") as f:
            base_prompt = f.read()
    else:
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

    ocr_instruction = """Look at the textbook page image(s) provided.
Extract ALL English vocabulary words visible on the page.
For each word, provide the information in the format below.
If the image shows Chinese translations, POS, or example sentences, include them.
If any information is missing from the image, provide it yourself.
Ignore page numbers, headers, footers, and non-vocabulary content.
If no English vocabulary words are found in the image, return an empty response.

"""
    prompt = ocr_instruction + base_prompt

    # 組裝 multimodal payload
    parts = [{"text": prompt}]
    for img_file in image_files:
        img_file.seek(0)
        img_bytes = img_file.read()
        encoded_image = base64.b64encode(img_bytes).decode('utf-8')
        fname = img_file.name.lower() if hasattr(img_file, 'name') else ""
        if fname.endswith('.png'):
            mime = "image/png"
        elif fname.endswith('.webp'):
            mime = "image/webp"
        else:
            mime = "image/jpeg"
        parts.append({"inline_data": {"mime_type": mime, "data": encoded_image}})

    payload = {"contents": [{"parts": parts}]}
    try:
        res = requests.post(f"{GEMINI_API_URL}?key={GEMINI_API_KEY}", json=payload, headers=GEMINI_HEADERS, timeout=60)
        if res.status_code == 200:
            res_json = res.json()
            text = res_json['candidates'][0]['content']['parts'][0]['text']
            usage = res_json.get("usageMetadata", {})
            token_count = usage.get("totalTokenCount", 0)
            if token_count > 0:
                record_ai_usage("vocab", token_count)
            raw_items = []
            for line in text.strip().split('\n'):
                if '|' in line:
                    p = [i.strip() for i in line.split('|')]
                    if len(p) >= 5:
                        raw_items.append({
                            "English": p[0], "POS": p[1], "Chinese_1": p[2], "Chinese_2": p[3],
                            "Example": p[4], "Course": course_name, "Date": str(course_date),
                            "Correct": 0, "Total": 0,
                            "srs_interval": 0, "srs_ease": 2.5, "srs_due": "", "srs_streak": 0, "srs_last_review": ""
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
        for cid, info in catalogs.items():
            name = info["name"]
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

def sample_by_accuracy(vocab_list, count):
    """按正確率由低到高排序後抽取指定數量的單字（正確率低的優先）"""
    def get_accuracy(w):
        total = int(w.get('Total', 0))
        correct = int(w.get('Correct', 0))
        if total == 0:
            return -1  # 未練習過的優先（排最前）
        return correct / total

    sorted_list = sorted(vocab_list, key=get_accuracy)
    return sorted_list[:count]

# ── SRS (Spaced Repetition System) 核心函式 ──────────────────────
def compute_srs_update(word, is_correct):
    """根據答題結果計算新的 SRS 欄位（簡化 SM-2）"""
    today_str = str(date.today())
    interval = int(word.get('srs_interval', 0))
    ease = float(word.get('srs_ease', 2.5))
    streak = int(word.get('srs_streak', 0))

    if is_correct:
        streak += 1
        if streak == 1:
            new_interval = 1        # 首次答對：明天複習
        elif streak == 2:
            new_interval = 3        # 連續兩次：3 天後
        else:
            new_interval = round(interval * ease)
        ease = min(3.0, ease + 0.1)  # 答對 ease 緩升，上限 3.0
    else:
        streak = 0
        new_interval = 1            # 答錯：明天重來
        ease = max(1.3, ease - 0.2)  # 答錯 ease 下降，下限 1.3

    due_date = date.today() + timedelta(days=new_interval)
    return {
        'srs_interval': new_interval,
        'srs_ease': round(ease, 2),
        'srs_due': str(due_date),
        'srs_streak': streak,
        'srs_last_review': today_str
    }

def get_due_words(vocab_list):
    """取得需要複習的單字：今日到期（含逾期）＋ 從未練過的新字"""
    today_str = str(date.today())
    return [w for w in vocab_list if not w.get('srs_due') or (isinstance(w.get('srs_due'), str) and w['srs_due'] <= today_str)]

def sample_for_review(vocab_list, count):
    """SRS 智慧抽題：到期優先 → 新字 → 正確率低"""
    due = get_due_words(vocab_list)
    # 到期的排前面（按日期），新字（空字串）排後面
    due.sort(key=lambda w: w.get('srs_due') or '9999-99-99')
    result = due[:count]

    if len(result) < count:
        used_ids = {w.get('id') for w in result}
        remaining = [w for w in vocab_list if w.get('id') not in used_ids]
        result.extend(sample_by_accuracy(remaining, count - len(result)))

    return result[:count]
# ── SRS 核心函式結束 ─────────────────────────────────────────────

# ── 練習時長追蹤 ─────────────────────────────────────────────────
def track_practice_time():
    """每次 rerun 時呼叫，累加練習秒數（僅在練習頁面呼叫）"""
    now = datetime.now()
    last = st.session_state.get('practice_last_active')
    st.session_state.practice_last_active = now
    if last:
        delta = (now - last).total_seconds()
        if delta < 300:  # 5 分鐘內算有效練習
            st.session_state.practice_seconds_today += delta

def save_practice_time():
    """將新增的練習秒數以 Increment 寫入 Firestore（避免覆蓋）"""
    total = int(st.session_state.get('practice_seconds_today', 0))
    last_saved = int(st.session_state.get('practice_seconds_last_saved', 0))
    delta = total - last_saved
    if delta <= 0 or not db: return
    today_str = str(date.today())
    try:
        user_ref = db.collection(USER_LIST_PATH).document(st.session_state.current_user_name)
        user_ref.set({"practice_time": {today_str: firestore.Increment(delta)}}, merge=True)
        st.session_state.practice_seconds_last_saved = total
    except: pass
# ── 練習時長追蹤結束 ─────────────────────────────────────────────

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

def _generate_encouragement(user_info):
    """根據練習數據產生鼓勵語（不呼叫 AI，零延遲）"""
    import random
    msgs = []
    today_str = str(date.today())
    yesterday_str = str(date.today() - timedelta(days=1))

    # 1. 連續練習天數
    practice_time = user_info.get('practice_time', {})
    if practice_time:
        streak = 0
        d = date.today() - timedelta(days=1)  # 從昨天開始算（今天還在進行中）
        while str(d) in practice_time and practice_time[str(d)] > 0:
            streak += 1
            d -= timedelta(days=1)
        # 今天有練也算
        if practice_time.get(today_str, 0) > 0:
            streak += 1
        if streak >= 7:
            msgs.append(f"🔥 連續第 {streak} 天練習，超強！")
        elif streak >= 3:
            msgs.append(f"🔥 連續第 {streak} 天練習，保持下去！")

    # 2. 昨天練習時間
    yesterday_secs = practice_time.get(yesterday_str, 0)
    if yesterday_secs >= 600:  # 10 分鐘以上才提
        msgs.append(f"💪 昨天練了 {yesterday_secs // 60} 分鐘，今天繼續挑戰！")

    # 3. 句型口說進度鼓勵
    sentence_stats = user_info.get('sentence_stats', {})
    total_completed = sum(s.get('completed', 0) for s in sentence_stats.values() if isinstance(s, dict))
    if total_completed > 0:
        greetings = [
            f"📖 已完成 {total_completed} 個句型，繼續累積！",
            f"🎯 {total_completed} 個句型已征服，挑戰下一個吧！",
        ]
        msgs.append(random.choice(greetings))

    # 4. 新用戶歡迎
    if not practice_time and total_completed == 0:
        msgs.append("👋 歡迎！從第一句開始練習吧")

    return msgs[0] if msgs else None

def attempt_login():
    """處理登入的 Callback 函式"""
    input_name = (st.session_state.login_user_name or "").strip()
    input_password = st.session_state.login_password
    users_db = st.session_state.users_db_cache

    if input_name and input_password:
        if input_name in users_db:
            user_record = users_db[input_name]
            if hash_password(input_password) == user_record["password"]:
                st.session_state.logged_in = True
                st.session_state.current_user_name = input_name
                st.session_state.user_info = user_record
                st.session_state.login_error = None
                sync_vocab_from_db(init_if_empty=False)
                # 載入今日已累計練習秒數
                existing_time = user_record.get('practice_time', {}).get(str(date.today()), 0)
                st.session_state.practice_seconds_today = existing_time
                st.session_state.practice_seconds_last_saved = existing_time
                st.session_state.practice_last_active = None
                # 記住登入資訊到 Cookie (30 天有效)
                cookie_controller.set("remembered_user", input_name, max_age=30*24*60*60)
                cookie_controller.set("remembered_pwd", input_password, max_age=30*24*60*60)
            else:
                st.session_state.login_error = "密碼錯誤。"
        else:
            st.session_state.login_error = f"找不到使用者「{input_name}」。"
    else:
        st.session_state.login_error = "請輸入名稱和密碼。"

# --- 7. UI 介面 ---

with st.sidebar:
    st.title("✨ Flashcard Pro")
    users_db = fetch_users_list()
    # 暫存使用者名單以供 callback 使用
    st.session_state.users_db_cache = users_db
    
    if not st.session_state.logged_in:
        st.subheader("🔑 學生登入")

        # 讀取 Cookie 預填登入資訊
        remembered_user = cookie_controller.get("remembered_user")
        remembered_pwd = cookie_controller.get("remembered_pwd")

        # 名稱選單：學生可選可搜尋，admin 不顯示但 Cookie 記住時仍可登入
        user_names = sorted(k for k, v in users_db.items() if v.get('role') != 'admin')
        default_idx = 0
        if remembered_user and remembered_user in user_names:
            default_idx = user_names.index(remembered_user) + 1
        # admin Cookie 自動登入
        if remembered_user and remembered_user in users_db and remembered_user not in user_names and remembered_pwd and isinstance(remembered_pwd, str):
            if hash_password(remembered_pwd) == users_db[remembered_user].get("password"):
                st.session_state.logged_in = True
                st.session_state.current_user_name = remembered_user
                st.session_state.user_info = users_db[remembered_user]
                sync_vocab_from_db(init_if_empty=False)
                existing_time = users_db[remembered_user].get('practice_time', {}).get(str(date.today()), 0)
                st.session_state.practice_seconds_today = existing_time
                st.session_state.practice_seconds_last_saved = existing_time
                st.session_state.practice_last_active = None
                st.rerun()
        st.selectbox(
            "選擇或搜尋名稱",
            options=[""] + user_names,
            index=default_idx,
            key="login_user_name",
            placeholder="輸入名稱搜尋...",
        )

        st.text_input(
            "輸入密碼",
            type="password",
            value=remembered_pwd or "",
            key="login_password",
            on_change=attempt_login
        )

        st.button("登入", on_click=attempt_login, use_container_width=True)

        if st.session_state.get("login_error"):
            st.error(st.session_state.login_error)

        # --- 自助註冊 ---
        st.divider()
        with st.expander("📝 新用戶註冊（7天免費試用）"):
            reg_name = st.text_input("取一個名稱", key="reg_name", max_chars=20)
            reg_pwd = st.text_input("設定密碼（至少4碼）", type="password", key="reg_pwd")
            reg_pwd2 = st.text_input("確認密碼", type="password", key="reg_pwd2")
            if st.button("🚀 立即註冊", use_container_width=True):
                if reg_pwd != reg_pwd2:
                    st.error("兩次密碼不一致，請重新輸入。")
                else:
                    ok, msg = register_new_user(reg_name, reg_pwd)
                    if ok:
                        # 註冊成功，直接自動登入
                        fresh_users = fetch_users_list()
                        if reg_name in fresh_users:
                            st.session_state.logged_in = True
                            st.session_state.current_user_name = reg_name
                            st.session_state.user_info = fresh_users[reg_name]
                            sync_vocab_from_db(init_if_empty=False)
                            st.session_state.practice_seconds_today = 0
                            st.session_state.practice_last_active = None
                            cookie_controller.set("remembered_user", reg_name, max_age=30*24*60*60)
                            cookie_controller.set("remembered_pwd", reg_pwd, max_age=30*24*60*60)
                            st.toast(f"✅ 歡迎 {reg_name}，享有 7 天免費 Premium 試用！")
                        st.rerun()
                    else:
                        st.error(msg)

    else:
        user = st.session_state.user_info
        st.markdown(f"### 👤 {user['name']}")
        st.caption(f"學號: {user['id']}")
        # 顯示訂閱狀態與 AI 額度
        if is_premium(user):
            plan_note = user.get("plan_note", "")
            expiry = user.get("plan_expiry")
            if plan_note == "7-day free trial" and expiry:
                exp_date = expiry.date() if hasattr(expiry, 'date') else expiry
                st.success(f"💎 免費試用中（到期：{exp_date}）")
            else:
                st.success("💎 Premium 會員")
        else:
            _, remaining = check_vocab_ai_usage()
            st.caption(f"🆓 免費方案（單字補全剩餘 {remaining}/{FREE_DAILY_VOCAB_AI_LIMIT} 次/天）")
        # SRS 今日複習提示
        if st.session_state.get('u_vocab'):
            due_today = get_due_words(st.session_state.u_vocab)
            _enc = _generate_encouragement(user)
            if due_today:
                tip = f"📅 今日待複習：{len(due_today)} 個單字"
                if _enc:
                    tip += f"  \n{_enc}"
                st.warning(tip)
            elif _enc:
                st.warning(_enc)
        st.divider()
        # 綁定選單狀態至 nav_selection
        menu_options = ["學習儀表板", "單字管理", "單字練習", "句型口說"]
        if user.get("role") == "admin":
            menu_options.append("⚙️ 後台管理")
        menu =st.radio("功能選單", menu_options, key="nav_selection")
        if st.button("登出", use_container_width=True):
            save_practice_time()
            # 清除記住的登入資訊 Cookie
            cookie_controller.remove("remembered_user")
            cookie_controller.remove("remembered_pwd")
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

        # --- 訂閱付費資訊與付款通知 ---
        with st.expander("💰 升級 Premium（NT$300/月）"):
            st.markdown(
                "**付款方式：** LINE Pay 或 銀行轉帳\n\n"
                "👉 掃描 QR code 加入 LINE 群組，私訊小編取得匯款資訊"
            )
            import base64 as _b64
            _qr_b64 = "iVBORw0KGgoAAAANSUhEUgAAAXIAAAFyAQAAAADAX2ykAAAC70lEQVR4nO2bYWrrMAzHJcWwjwnsAD2Ke4N3pNEj7QbNUXqAQfxx4KCHZCdb8yBxYSvOi/4f3Cb6UQRCtiW7yPCIenoIBzB+XbRhX8r4ddGGfSnj10Ub9qWMXxdt2Jcy/gg8ZjmAvhsRILgJCJPt/ER/ykUPsAfmPYsGAHwbGkY8xRzaMzRq4rr9LxYdlA85Q/mCiMyDJHGbo5oS+7n+lIqKySTjQfK3kwFdFf6sitbNcHTeLZ7RDx1ODyM+3R8y/lfi28pUHAAYIDrw7wjg37tk44r9J+NL+F63yB0AnsOLxLOZhzFtn+/5YpHxFeQvz8/cn6I8fqJ+y4n9PH/I+B/lIRU/fmjSwKyDvuOYCif9ll5ea/OfjC+LL2iuMl+1KmoltEPDfBVE32mJbPHd6fwc5CO4iP7mImv/qu8aifmHQ3/DWv0n4wviizJwKogkdftzExHaQQy63YqO6/SfjF8XJ30tvYO8k+Ga+lcySet0bfPzfvleTxXaCHhumfNZwjdppVSx/8WiQ/L4JnvlPjUkR+TLSVI3OEnieNfJqtP/ctFB+1fhVXbI2uloGP0wOtb1VzbWCO1cItfmPxlfuv7CXCSlqhdS6tr6+z+tv6xbq4s8+ttLqn8BUs/yif4Ui8rRI9dHfpCzhNDl0sjfvs6VRgTPtv7ulIfcd5xnYC2SrtOGmVk2XimxbX7eef8Z4KsJrXd2YmZS4C2+u+VZ8jfn6iS+yJSddlrT7Z1a/S8XHbc/CQBN5P70qVev5Hxh1MIpQn+KrlL/yfiH7k+CFkR6v67vmm9F0j1fLDK+Cj7kGXgaJKBBDpHk6oYfRsyVUq3+l4oOfr+OIWDqVaF2Kvs/Hy51t57jDxn/qzzK1diUup6jXHeX7sdJDh5+5Pe3RJvEvYx/9P5keJWGsxw3dAOj9DzkYNjOf3dd/2bphiqXw3nBnQsn6z/vkUf7f/eqaN38j4xfF23YlzJ+XbRhX8r4ddGGfSnj10Ubdjg4/xdp05P5bGhauQAAAABJRU5ErkJggg=="
            st.markdown(
                f'<img src="data:image/png;base64,{_qr_b64}" width="160">',
                unsafe_allow_html=True
            )
            st.divider()
            st.markdown("**已完成轉帳？** 請在下方通知小編：")
            with st.form("payment_notify_form"):
                last5 = st.text_input("轉帳帳號末5碼", max_chars=5, placeholder="例：12345")
                if st.form_submit_button("通知小編"):
                    if not last5 or len(last5) < 5 or not last5.isdigit():
                        st.error("請輸入正確的 5 位數字。")
                    else:
                        uname = st.session_state.current_user_name
                        uid = user.get("id", "?")
                        msg = f"\n💰 付款通知\n學生：{uname}（{uid}）\n轉帳末5碼：{last5}\n時間：{datetime.now().strftime('%Y-%m-%d %H:%M')}"
                        ok, result = send_line_notify(msg)
                        if ok:
                            st.success("已通知小編，確認後將為你開通 Premium！")
                        else:
                            st.error(f"通知失敗：{result}")

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
    st.info("請在左側登入，或展開「新用戶註冊」免費試用 7 天。")

else:
    u_vocab = st.session_state.u_vocab

    if menu == "學習儀表板":
        st.title("📊 學習儀表板")
        
        # 調整 Tab 順序：個人戰績表、排行榜、單字學習、句型練習
        tab_total, tab_rank, tab_v, tab_s = st.tabs(["個人戰績表", "🏆 全班排行榜", "單字學習", "句型練習"])

        # --- 個人戰績表 Tab ---
        with tab_total:
            st.subheader("📈 個人戰績表")
            
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
                user_progress = fetch_all_user_sentence_progress()
                user_info = st.session_state.get("user_info")

                for cid, info in catalogs.items():
                    name = info["name"]
                    book_is_premium = info.get("is_premium", False)
                    b_sentences = fetch_sentences_by_id(cid)
                    if not b_sentences: continue

                    label = f"📙 {name}"
                    if book_is_premium and not is_premium(user_info):
                        label += " 🔒"
                    with st.expander(label, expanded=True):
                        df_s = pd.DataFrame(b_sentences)
                        if 'Category' not in df_s.columns: df_s['Category'] = '未分類'
                        cats = sorted(df_s['Category'].unique())
                        
                        for cat in cats:
                            cat_sents = [s for s in b_sentences if s.get('Category') == cat]
                            tot = len(cat_sents)

                            cnt_mastered = 0  # 3輪以上（⭐⭐）
                            cnt_practiced = 0  # 1~2輪

                            for s in cat_sents:
                                h = hash_string(s['Template'])
                                p_data = user_progress.get(h, {})
                                rounds = p_data.get("completion_count", 0) if isinstance(p_data, dict) else 0
                                if rounds >= 3:
                                    cnt_mastered += 1
                                elif rounds >= 1:
                                    cnt_practiced += 1

                            p_done = cnt_mastered / tot if tot > 0 else 0
                            p_prog = cnt_practiced / tot if tot > 0 else 0
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

            st.divider()

            # 3. 練習時長
            st.markdown("#### ⏱️ 最近練習時長")
            practice_time = st.session_state.get('user_info', {}).get('practice_time', {})
            if practice_time:
                # 最近 7 天
                recent_days = []
                for i in range(6, -1, -1):
                    d = date.today() - timedelta(days=i)
                    d_str = str(d)
                    secs = practice_time.get(d_str, 0)
                    # 今天的用 session state 的即時值
                    if d_str == str(date.today()):
                        secs = max(secs, int(st.session_state.get('practice_seconds_today', 0)))
                    recent_days.append({"日期": d.strftime("%m/%d"), "分鐘": round(secs / 60, 1)})
                df_time = pd.DataFrame(recent_days)
                st.bar_chart(df_time, x="日期", y="分鐘", height=200)
                total_week = sum(r["分鐘"] for r in recent_days)
                st.caption(f"本週合計：{total_week:.0f} 分鐘")
            else:
                today_mins = int(st.session_state.get('practice_seconds_today', 0)) // 60
                if today_mins > 0:
                    st.info(f"今日已練習 {today_mins} 分鐘")
                else:
                    st.info("尚無練習記錄，快去練習吧！")

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
                
                total_vocab_count = len(filtered_vocab)
                practiced_count = len([v for v in filtered_vocab if v.get('Total', 0) > 0])
                coverage_rate = (practiced_count / total_vocab_count * 100) if total_vocab_count > 0 else 0
                total_correct = sum(v.get('Correct', 0) for v in filtered_vocab)
                total_attempts = sum(v.get('Total', 0) for v in filtered_vocab)
                accuracy_rate = (total_correct / total_attempts * 100) if total_attempts > 0 else 0
                due_count_dash = len(get_due_words(filtered_vocab))

                mc1, mc2 = st.columns(2)
                mc1.metric("單字數", total_vocab_count)
                mc2.metric("覆蓋率", f"{coverage_rate:.1f}%")
                mc3, mc4 = st.columns(2)
                mc3.metric("正確率", f"{accuracy_rate:.1f}%")
                mc4.metric("📅 待複習", due_count_dash)

                st.divider()
                df_vocab_display = pd.DataFrame(filtered_vocab)
                df_vocab_display['下次複習'] = df_vocab_display.apply(
                    lambda r: r.get('srs_due') if r.get('srs_due') else '尚未排程', axis=1
                )
                st.dataframe(df_vocab_display[['English', 'Chinese_1', 'POS', 'Course', 'Date', 'Correct', 'Total', '下次複習']], use_container_width=True, hide_index=True)

        # --- 句型 Tab ---
        with tab_s:
            catalogs = fetch_sentence_catalogs()
            if not catalogs:
                st.info("尚無句型資料庫。")
            else:
                # 準備選單
                combined_s_options = []
                book_map = {}  # name -> cid

                for cid, info in catalogs.items():
                    name = info["name"]
                    book_map[name] = cid
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
                    practiced_count = 0
                    total_rounds = 0

                    progress_table = []

                    for s in target_sentences:
                        h = hash_string(s['Template'])
                        p_data = user_progress.get(h, {})
                        rounds = p_data.get("completion_count", 0) if isinstance(p_data, dict) else 0
                        if rounds > 0:
                            practiced_count += 1
                        total_rounds += rounds

                        stars = get_star_display(rounds)
                        progress_table.append({
                            "分類": s.get('Category', ''),
                            "句型": s['Template'],
                            "輪數": rounds,
                            "熟練度": stars if stars else "—",
                        })

                    sc1, sc2, sc3 = st.columns(3)
                    sc1.metric("總句數", total_s_count)
                    sc2.metric("已練習", f"{practiced_count}/{total_s_count}")
                    sc3.metric("累計輪數", total_rounds)

                    st.divider()
                    st.dataframe(pd.DataFrame(progress_table), use_container_width=True, hide_index=True)
                    
                    # --- 清除紀錄（需確認） ---
                    if st.button("🗑️ 清除所有句型練習紀錄"):
                        st.session_state._confirm_clear_target = target_id
                        confirm_clear_sentence_progress()

        # --- 🏆 全班排行榜 Tab ---
        with tab_rank:
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
                if u_data.get("role") == "admin": continue
                s_stats = u_data.get("sentence_stats", {})
                if not s_stats: continue

                for book_id, stat in s_stats.items():
                    if not isinstance(stat, dict): continue
                    total = stat.get('total', 0)
                    if total == 0: continue

                    completed = stat.get('completed', 0)
                    book_name = stat.get('name', book_id)

                    # 將 Timestamp 轉換為台灣時間
                    last_active = stat.get('last_active')
                    last_active_str = ""
                    if hasattr(last_active, 'astimezone'):
                        last_active_str = last_active.astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")
                    elif hasattr(last_active, 'strftime'):
                        last_active_str = last_active.strftime("%Y-%m-%d %H:%M")
                    elif isinstance(last_active, str) and len(last_active) >= 19:
                        try:
                            utc_dt = datetime.fromisoformat(last_active.replace('Z', '+00:00'))
                            tw_dt = utc_dt.astimezone(timezone(timedelta(hours=8)))
                            last_active_str = tw_dt.strftime("%Y-%m-%d %H:%M")
                        except:
                            last_active_str = last_active[:10] + " " + last_active[11:16]

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

                    current_user = st.session_state.get("current_user_name", "")
                    for rank, s in enumerate(students_sorted[:5], 1):
                        pct = int(s['rate'] * 100)
                        if rank == 1: rank_display = "🥇"
                        elif rank == 2: rank_display = "🥈"
                        elif rank == 3: rank_display = "🥉"
                        else: rank_display = f"{rank}."

                        is_me = s['student'] == current_user
                        row_style = "background: rgba(255,193,7,0.15); border-radius: 4px;" if is_me else ""
                        name_style = "font-weight: 700;" if is_me else ""

                        bar_html = f"""
                        <div style="display: flex; align-items: center; margin-bottom: 6px; font-size: 0.9rem; {row_style}">
                            <div style="min-width: 100px; white-space: nowrap; {name_style}">{rank_display} {s['student']}</div>
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

    elif menu == "單字管理":
        st.title("⚙️ 單字管理")
        tab1, tab2, tab3, tab4, tab5 = st.tabs(["✨ AI 輸入", "手動修改", "單字刪除", "📂 CSV 匯入/匯出", "📥 公用單字集"])

        with tab1:
            # 共用：課程名稱選擇
            existing_courses = []
            if u_vocab:
                df_courses = pd.DataFrame(u_vocab)
                if 'Course' in df_courses.columns:
                    existing_courses = sorted(df_courses['Course'].dropna().unique().tolist())
            if existing_courses:
                course_options = existing_courses + ["➕ 新增課程..."]
                selected_course = st.selectbox("課程名稱:", course_options, key="ai_course_select")
                if selected_course == "➕ 新增課程...":
                    c_name = st.text_input("輸入新課程名稱:", key="ai_new_course_name")
                else:
                    c_name = selected_course
            else:
                c_name = st.text_input("課程名稱:", key="ai_new_course_name")
            c_date = st.date_input("日期:", value=date.today(), key="ai_date")

            # 輸入模式切換
            input_mode = st.radio("輸入方式：", ["✏️ 文字輸入", "📸 拍照", "📁 上傳圖片"], horizontal=True, key="ai_input_mode")

            has_input = False
            btn_label = ""
            ocr_images = []

            if input_mode == "✏️ 文字輸入":
                text_area = st.text_area("輸入內容:", height=150, key="ai_text_area")
                has_input = bool(text_area and text_area.strip())
                btn_label = "啟動 AI 處理"
            elif input_mode == "📸 拍照":
                camera_image = st.camera_input("拍攝課本頁面", key="ai_camera")
                if camera_image:
                    ocr_images = [camera_image]
                    has_input = True
                btn_label = "🔍 啟動 AI 辨識"
            else:
                uploaded_images = st.file_uploader(
                    "上傳課本圖片（支援多張）", type=["jpg", "jpeg", "png", "webp"],
                    accept_multiple_files=True, key="ai_upload"
                )
                if uploaded_images:
                    ocr_images = uploaded_images
                    has_input = True
                btn_label = "🔍 啟動 AI 辨識"

            # 圖片預覽
            if ocr_images:
                cols = st.columns(min(len(ocr_images), 3))
                for i, img in enumerate(ocr_images):
                    with cols[i % 3]:
                        st.image(img, use_container_width=True, caption=f"圖片 {i+1}")

            # AI 處理按鈕
            if has_input and st.button(btn_label, key="ai_process_btn"):
                # 圖片大小檢查
                oversized = False
                if ocr_images:
                    for img in ocr_images:
                        img.seek(0, 2)
                        if img.tell() > 10 * 1024 * 1024:
                            st.warning(f"圖片 {img.name} 超過 10MB，請縮小後再試。")
                            oversized = True
                        img.seek(0)
                # 文字模式行數檢查
                if input_mode == "✏️ 文字輸入":
                    line_count = len([l for l in text_area.strip().split('\n') if l.strip()])
                    if line_count > VOCAB_AI_MAX_LINES:
                        st.warning(f"⚠️ 每次最多 {VOCAB_AI_MAX_LINES} 行，目前 {line_count} 行，請分批輸入。")
                        oversized = True
                if not oversized:
                    can_use, remaining = check_vocab_ai_usage()
                    if not can_use:
                        st.warning(f"🔒 今日單字補全額度已用完（每日 {FREE_DAILY_VOCAB_AI_LIMIT} 次）。升級 Premium 可無限使用！")
                    else:
                        spinner_msg = "解析中..." if input_mode == "✏️ 文字輸入" else "AI 辨識中，請稍候..."
                        with st.spinner(spinner_msg):
                            if input_mode == "✏️ 文字輸入":
                                st.session_state.pending_items = call_gemini_to_complete(text_area, c_name, c_date)
                            else:
                                st.session_state.pending_ocr_items = call_gemini_ocr(ocr_images, c_name, c_date)
                            consume_vocab_ai_usage()
                        # OCR 無結果提示
                        if input_mode != "✏️ 文字輸入" and not st.session_state.get("pending_ocr_items"):
                            st.warning("⚠️ 未能從圖片中辨識出單字，請確認：\n1. 圖片清晰且包含英文單字\n2. 文字方向正確\n3. 光線充足，無嚴重反光")

            # 預覽與儲存（文字模式）
            if st.session_state.get("pending_items"):
                edited = st.data_editor(pd.DataFrame(st.session_state.pending_items), use_container_width=True, hide_index=True)
                if st.button("💾 確認儲存", type="primary", key="ai_save_text"):
                    path = get_vocab_path()
                    for it in edited.to_dict('records'): db.collection(path).add(it)
                    st.session_state.pending_items = None
                    sync_vocab_from_db(); st.success("儲存成功！"); st.rerun()
            # 預覽與儲存（OCR 模式）
            if st.session_state.get("pending_ocr_items"):
                st.success(f"辨識到 {len(st.session_state.pending_ocr_items)} 個單字，請檢查後儲存：")
                edited_ocr = st.data_editor(pd.DataFrame(st.session_state.pending_ocr_items), use_container_width=True, hide_index=True)
                if st.button("💾 確認儲存", type="primary", key="ai_save_ocr"):
                    path = get_vocab_path()
                    for it in edited_ocr.to_dict('records'): db.collection(path).add(it)
                    st.session_state.pending_ocr_items = None
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
                    if to_delete and st.button(f"🗑️ 刪除 ({len(to_delete)} 個)"):
                        st.session_state._confirm_delete_ids = to_delete
                        confirm_delete_vocab()
                else: st.warning("無資料。")
            else: st.info("無資料。")

        with tab4:
            csv_tab_import, csv_tab_export = st.tabs(["📥 匯入", "📤 匯出"])

            with csv_tab_import:
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
                            df_csv = df_csv.fillna("")
                            items_to_add = []
                            for _, row in df_csv.iterrows():
                                pos_val = str(row.get("POS", str(row.get("Group", "")))).strip()
                                if not pos_val: pos_val = "未分類"
                                course_val = str(row.get("Course", "")).strip()
                                if not course_val: course_val = default_course
                                date_val = str(row.get("Date", "")).strip()
                                if not date_val: date_val = str(default_date)
                                items_to_add.append({
                                    "English": str(row.get("English", "")),
                                    "Chinese_1": str(row.get("Chinese_1", "")),
                                    "Chinese_2": str(row.get("Chinese_2", "")),
                                    "POS": pos_val,
                                    "Example": str(row.get("Example", "")),
                                    "Course": course_val,
                                    "Date": date_val,
                                    "Correct": int(row.get("Correct", 0)) if str(row.get("Correct", "0")).isdigit() else 0,
                                    "Total": int(row.get("Total", 0)) if str(row.get("Total", "0")).isdigit() else 0,
                                    "srs_interval": 0, "srs_ease": 2.5, "srs_due": "", "srs_streak": 0, "srs_last_review": ""
                                })

                            # 重複檢查
                            existing_english = {w.get('English', '').lower() for w in u_vocab} if u_vocab else set()
                            new_items = [it for it in items_to_add if it['English'].lower() not in existing_english]
                            dup_count = len(items_to_add) - len(new_items)

                            if dup_count > 0:
                                st.info(f"📋 共 {len(items_to_add)} 筆：{len(new_items)} 筆為新單字，{dup_count} 筆已存在（將跳過）")

                            if new_items:
                                if st.button(f"🚀 匯入 {len(new_items)} 個新單字", type="primary"):
                                    with st.spinner("正在匯入..."):
                                        save_new_words_to_db(new_items)
                                        sync_vocab_from_db()
                                        st.success(f"成功匯入 {len(new_items)} 筆單字！")
                                        time.sleep(1)
                                        st.rerun()
                            elif items_to_add:
                                st.success("所有單字都已存在於你的單字庫中！")
                        else:
                            st.error("CSV 格式錯誤：必須包含 'English' 與 'Chinese_1' 欄位。")
                    except Exception as e:
                        st.error(f"讀取檔案失敗: {e}")

            with csv_tab_export:
                st.subheader("📤 匯出我的單字集")
                if u_vocab:
                    export_cols = ["English", "POS", "Chinese_1", "Chinese_2", "Example", "Course", "Date", "Correct", "Total"]
                    df_export = pd.DataFrame(u_vocab)
                    # 只匯出存在的欄位
                    export_cols = [c for c in export_cols if c in df_export.columns]
                    df_export = df_export[export_cols]
                    st.write(f"共 {len(df_export)} 個單字")
                    st.dataframe(df_export, use_container_width=True, hide_index=True)
                    csv_data = df_export.to_csv(index=False).encode('utf-8-sig')
                    user_name = st.session_state.get("user", "vocab")
                    st.download_button(
                        label="⬇️ 下載 CSV",
                        data=csv_data,
                        file_name=f"{user_name}_vocabulary.csv",
                        mime="text/csv",
                        type="primary"
                    )
                else:
                    st.info("你還沒有任何單字，新增後即可匯出。")

        with tab5:
            st.subheader("📥 公用單字集")
            st.caption("匯入公用單字到你的個人單字庫，可用於練習和測驗。")

            shared_catalogs = fetch_shared_vocab_catalogs()

            if not shared_catalogs:
                st.info("目前沒有公用單字集。")
            else:
                set_options = {sid: info["name"] for sid, info in shared_catalogs.items()}
                selected_set_id = st.selectbox(
                    "選擇單字集：", list(set_options.keys()),
                    format_func=lambda x: set_options[x],
                    key="shared_vocab_select"
                )

                shared_words = fetch_shared_vocab_words(selected_set_id)
                catalog_info = shared_catalogs[selected_set_id]
                all_courses = sorted(catalog_info.get("courses", []))
                st.write(f"共 {len(shared_words)} 字" + (f"，{len(all_courses)} 個分類" if len(all_courses) > 1 else ""))

                if len(all_courses) > 1:
                    theme_options = ["全部"] + all_courses
                    selected_themes = st.multiselect(
                        "選擇要匯入的分類：", theme_options, default=["全部"],
                        key=f"shared_themes_{selected_set_id}"
                    )
                    if "全部" in selected_themes:
                        words_to_import = shared_words
                    else:
                        words_to_import = [w for w in shared_words if w.get("Course") in selected_themes]
                else:
                    words_to_import = shared_words

                existing_english = {w.get('English', '').lower() for w in u_vocab} if u_vocab else set()
                new_words = [w for w in words_to_import if w.get('English', '').lower() not in existing_english]
                dup_count = len(words_to_import) - len(new_words)

                if dup_count > 0:
                    st.info(f"{len(new_words)} 字為新單字（{dup_count} 字已存在，將跳過）")

                if new_words and st.button(f"📥 匯入 {len(new_words)} 個新單字", type="primary", key=f"import_{selected_set_id}"):
                    with st.spinner(f"正在匯入 {len(new_words)} 個單字..."):
                        today_str = str(date.today())
                        items_to_save = [{
                            "English": w.get("English", ""), "POS": w.get("POS", ""),
                            "Chinese_1": w.get("Chinese_1", ""), "Chinese_2": w.get("Chinese_2", ""),
                            "Example": w.get("Example", ""), "Course": w.get("Course", ""),
                            "Date": today_str, "Correct": 0, "Total": 0,
                            "srs_interval": 0, "srs_ease": 2.5, "srs_due": "", "srs_streak": 0, "srs_last_review": ""
                        } for w in new_words]
                        save_new_words_to_db(items_to_save)
                        sync_vocab_from_db()
                        st.success(f"成功匯入 {len(items_to_save)} 筆單字！")
                        time.sleep(1)
                        st.rerun()
                elif not new_words and words_to_import:
                    st.success("所有單字都已存在於你的單字庫中！")

    elif menu == "單字練習":
        track_practice_time()
        st.title("✏️ 單字練習")
        options = get_course_options(u_vocab)
        # 直接使用 key="practice_filter" 從 session state 取值，不使用 index
        selection = st.selectbox("🎯 選擇練習範圍：", options, key="practice_filter")
        
        current_set = filter_vocab_data(u_vocab, selection)
        
        tab_p, tab_t, tab_m = st.tabs(["快閃練習", "實力測驗", "例句連連看"])
        
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
                # SRS 複習提示
                due_words = get_due_words(current_set)
                due_count = len(due_words)
                if due_count > 0:
                    st.info(f"📅 此範圍有 **{due_count}** 個單字到期需要複習")

                col_q1, col_q2 = st.columns(2)
                init_pool = False
                with col_q1:
                    if st.button("🔄 換一批題目", use_container_width=True):
                        st.session_state.test_pool = sample_by_accuracy(current_set, min(10, len(current_set)))
                        st.session_state.t_idx = 0; st.session_state.t_score = 0; st.session_state.quiz_history = []
                        st.rerun()
                with col_q2:
                    if due_count > 0:
                        if st.button(f"📅 複習到期單字 ({due_count})", type="primary", use_container_width=True):
                            st.session_state.test_pool = sample_for_review(current_set, min(10, len(current_set)))
                            st.session_state.t_idx = 0; st.session_state.t_score = 0; st.session_state.quiz_history = []
                            st.rerun()

                if "test_pool" not in st.session_state:
                    # 首次載入：用 SRS 智慧抽題
                    st.session_state.test_pool = sample_for_review(current_set, min(10, len(current_set)))
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
                            srs = compute_srs_update(curr, ok)
                            update_word_data(curr.get('id'), {"Correct": int(curr.get('Correct', 0)) + (1 if ok else 0), "Total": int(curr.get('Total', 0)) + 1, **srs})
                            save_practice_time()
                            st.session_state.t_idx += 1; st.rerun()
                    auto_focus_input()
                else:
                    score = st.session_state.t_score
                    total = len(st.session_state.test_pool)
                    if score == total:
                        st.balloons()
                        st.success(f"🎉 滿分！{score} / {total}　太厲害了！")
                    elif score >= total * 0.8:
                        st.success(f"👏 測驗得分：{score} / {total}　表現很棒！")
                    else:
                        st.info(f"測驗得分：{score} / {total}　再多練習幾次吧！")
                    df_h = pd.DataFrame(st.session_state.quiz_history)
                    wrongs = df_h[df_h["is_correct"] == False]
                    if not wrongs.empty:
                        st.subheader("❌ 錯誤回顧")
                        st.table(wrongs[["英文", "你的輸入", "正確答案"]])

        with tab_m:
            st.subheader("🔗 例句連連看")

            # 篩選有例句且例句包含該單字的項目（避免挖空失敗）
            words_with_example = [
                w for w in current_set
                if w.get('Example') and w.get('English')
                and re.search(re.escape(w['English']), w['Example'], re.IGNORECASE)
            ]

            if len(words_with_example) < 6:
                st.warning("需要至少 6 個有例句（且例句包含該單字）的單字才能進行此測驗")
            else:
                # 初始化或換題
                if "match_pool" not in st.session_state or st.button("🔄 換一批題目", key="match_refresh"):
                    # 按正確率由低到高抽題（正確率低的優先）
                    selected = sample_by_accuracy(words_with_example, 5)

                    # 產生干擾選項（從其他單字中隨機選一個）
                    other_words = [w for w in words_with_example if w not in selected]
                    decoy = random.choice(other_words)['English'] if other_words else "unknown"

                    # 打亂選項順序
                    options = [w['English'] for w in selected] + [decoy]
                    random.shuffle(options)

                    # 建立題目（例句挖空）
                    questions = []
                    for w in selected:
                        example = w['Example']
                        english = w['English']
                        # 將單字替換為 ___（不區分大小寫）
                        blanked = re.sub(re.escape(english), "______", example, flags=re.IGNORECASE)
                        questions.append({
                            "blanked": blanked,
                            "answer": english,
                            "original": example,
                            "id": w.get('id')
                        })

                    st.session_state.match_pool = questions
                    st.session_state.match_options = options
                    st.session_state.match_submitted = False
                    st.rerun()

                # 顯示選項
                st.info(f"**選項：** {' ・ '.join(st.session_state.match_options)}")

                # 顯示題目（使用 form 確保同時提交）
                with st.form("match_form"):
                    user_answers = []
                    for i, q in enumerate(st.session_state.match_pool):
                        col1, col2 = st.columns([3, 1])
                        col1.markdown(f"**{i+1}.** {q['blanked']}")
                        ans = col2.selectbox(
                            f"選擇答案 {i+1}",
                            ["請選擇..."] + st.session_state.match_options,
                            key=f"match_ans_{i}",
                            label_visibility="collapsed"
                        )
                        user_answers.append(ans)

                    if st.form_submit_button("✅ 提交答案", use_container_width=True):
                        st.session_state.match_submitted = True
                        st.session_state.match_user_answers = user_answers

                        # 計算結果並更新資料庫
                        results = []
                        for i, q in enumerate(st.session_state.match_pool):
                            user_ans = user_answers[i]
                            is_correct = user_ans.lower() == q['answer'].lower()
                            results.append(is_correct)
                            # 更新單字的 Correct/Total
                            if q.get('id'):
                                word_data = next((w for w in current_set if w.get('id') == q['id']), None)
                                if word_data:
                                    srs = compute_srs_update(word_data, is_correct)
                                    update_word_data(q['id'], {
                                        "Correct": int(word_data.get('Correct', 0)) + (1 if is_correct else 0),
                                        "Total": int(word_data.get('Total', 0)) + 1,
                                        **srs
                                    })
                        st.session_state.match_results = results
                        save_practice_time()
                        st.rerun()

                # 顯示結果
                if st.session_state.get("match_submitted"):
                    correct_count = 0
                    st.divider()
                    for i, q in enumerate(st.session_state.match_pool):
                        user_ans = st.session_state.match_user_answers[i]
                        is_correct = st.session_state.match_results[i]
                        if is_correct:
                            correct_count += 1
                            st.success(f"✅ {q['original']}")
                        else:
                            st.error(f"❌ {q['blanked'].replace('______', f'**{user_ans}**')} → 正確：**{q['answer']}**")

                    if correct_count == 5:
                        st.balloons()
                        st.metric("得分", f"🎉 {correct_count} / 5 滿分！")
                    elif correct_count >= 4:
                        st.metric("得分", f"👏 {correct_count} / 5")
                    else:
                        st.metric("得分", f"{correct_count} / 5")

    elif menu == "句型口說":
        track_practice_time()
        st.title("🗣️ 句型口說挑戰")
        catalogs = fetch_sentence_catalogs()
        if not catalogs:
            st.info("目前雲端沒有句型資料庫。")
            if INITIAL_SENTENCES:
                st.warning("⚠️ 使用預設題庫模式 (未連結雲端)"); current_sentences = INITIAL_SENTENCES
            else: st.stop()
        else:
            combined_options = []
            book_map = {}   # name -> cid
            premium_books = set()  # 需要付費的書名

            user_info = st.session_state.get("user_info")
            user_is_premium = is_premium(user_info)

            for cid, info in catalogs.items():
                name = info["name"]
                book_map[name] = cid
                book_is_premium = info.get("is_premium", False)
                if book_is_premium:
                    premium_books.add(name)

                display_name = f"{name} 🔒" if (book_is_premium and not user_is_premium) else name
                combined_options.append(f"{display_name} (全部)")
                book_sentences = fetch_sentences_by_id(cid)
                if book_sentences:
                    df_b = pd.DataFrame(book_sentences)
                    if 'Category' in df_b.columns:
                        cats = sorted(df_b['Category'].unique())
                        for c in cats:
                            combined_options.append(f"{display_name} | {c}")

            selection = st.selectbox("選擇練習範圍：", combined_options, key="sentence_filter")

            clean_selection = selection.replace(" 🔒", "")

            if " (全部)" in clean_selection:
                book_name = clean_selection.replace(" (全部)", "")
                target_id = book_map.get(book_name)
                current_sentences = fetch_sentences_by_id(target_id)
                st.session_state.current_dataset_id = target_id
            else:
                book_name, category = clean_selection.split(" | ")
                target_id = book_map.get(book_name)
                st.session_state.current_dataset_id = target_id
                all_book_sentences = fetch_sentences_by_id(target_id)
                current_sentences = [s for s in all_book_sentences if s.get('Category') == category]

            # 付費句型書存取控制
            if book_name in premium_books and not user_is_premium:
                st.warning("🔒 此句型書為 Premium 專屬內容。升級 Premium 即可解鎖所有句型書！")
                st.info("💡 新註冊用戶享有 7 天免費試用，試用期間可使用所有 Premium 內容。")
                st.stop()

        if not current_sentences: st.info("此範圍內無題目。")
        else:
            # 智慧跳轉：切換題庫時跳到第一個未完成的題目
            current_filter_sig = selection
            if st.session_state.last_sentence_filter_sig != current_filter_sig:
                user_progress = fetch_all_user_sentence_progress()
                found_idx = 0
                for i, s in enumerate(current_sentences):
                    h = hash_string(s['Template'])
                    p_data = user_progress.get(h, {})
                    rounds = p_data.get("completion_count", 0) if isinstance(p_data, dict) else 0
                    if rounds == 0:
                        found_idx = i
                        break
                st.session_state.sentence_idx = found_idx
                st.session_state.completed_options = set()
                st.session_state.last_sentence_filter_sig = current_filter_sig
                if "loaded_hash" in st.session_state: del st.session_state.loaded_hash

            if st.session_state.sentence_idx >= len(current_sentences):
                st.session_state.sentence_idx = 0

            curr_sent = current_sentences[st.session_state.sentence_idx]
            template = curr_sent['Template']
            options = curr_sent['Options']

            template_hash = hash_string(template)
            if "loaded_hash" not in st.session_state or st.session_state.loaded_hash != template_hash:
                loaded_opts, loaded_count = load_user_sentence_progress(template_hash)
                st.session_state.completed_options = loaded_opts
                st.session_state.drill_completion_count = loaded_count
                st.session_state.loaded_hash = template_hash
            if "drill_completion_count" not in st.session_state:
                st.session_state.drill_completion_count = 0

            # 題目資訊
            st.caption(f"題目 {st.session_state.sentence_idx + 1}/{len(current_sentences)}　({curr_sent.get('Category', '一般')})")

            # 上一題 / 下一題 導覽
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

            # === JS 口說練習元件（直接寫 Firestore） ===
            user_id = st.session_state.user_info["id"]
            user_name = st.session_state.current_user_name
            fs_doc_path = f"artifacts/{APP_ID}/users/{user_id}/sentence_progress/{template_hash}"
            user_doc_path = f"{USER_LIST_PATH}/{user_name}"
            drill_remaining = get_drill_remaining()
            # 取得題庫全部句數（排行榜統計用）
            all_sentences_for_stats = fetch_sentences_by_id(st.session_state.current_dataset_id)
            # 直接從 Firestore 讀取語速設定（JS 端會即時寫入，不能靠快取的 user_info）
            _user_doc = db.collection(USER_LIST_PATH).document(user_name).get()
            saved_tts_rate = _user_doc.to_dict().get("tts_rate", 0.85) if _user_doc.exists else 0.85
            drill_html = generate_drill_html(
                template=template,
                options=options,
                completion_count=st.session_state.drill_completion_count,
                api_key=GEMINI_API_KEY,
                api_url=GEMINI_API_URL,
                template_hash=template_hash,
                dataset_id=st.session_state.current_dataset_id,
                firestore_doc_path=fs_doc_path,
                completed_options=st.session_state.completed_options,
                user_doc_path=user_doc_path,
                drill_remaining=drill_remaining,
                dataset_name=book_name,
                total_sentences=len(all_sentences_for_stats),
                tts_rate=saved_tts_rate,
            )
            html(drill_html, height=550, scrolling=True)

            keyboard_bridge()

    elif menu == "⚙️ 後台管理":
        from admin_app import render_admin
        render_admin(db, APP_ID)

st.divider()
st.caption("Flashcard Pro - 資料已加密並同步至 Firestore")