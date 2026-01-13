import streamlit as st
import pandas as pd
import random
import json
import requests
import time
import hashlib
from datetime import date
from google.cloud import firestore
from google.oauth2 import service_account
from streamlit.components.v1 import html

# --- 0. 設定與常數 ---
st.set_page_config(page_title="Flashcard Pro 雲端版", page_icon="🧠", layout="wide")

# 讀取 Secrets
GEMINI_API_KEY = st.secrets.get("GEMINI_API_KEY", "")
GEMINI_MODEL = "gemini-2.5-flash-preview-09-2025"
GEMINI_API_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"

# 預設單字內容
INITIAL_VOCAB = [
    {"English": "plus", "Group": "介系詞", "Chinese_1": "加", "Chinese_2": "加上", "Example": "Two plus two is four.", "Course": "Sophie數學課", "Date": "2025-11-15", "Correct": 0, "Total": 0},
    {"English": "minus", "Group": "介系詞", "Chinese_1": "減", "Chinese_2": "減去", "Example": "Five minus two is three.", "Course": "Sophie數學課", "Date": "2025-11-15", "Correct": 0, "Total": 0},
    {"English": "multiply", "Group": "動詞", "Chinese_1": "乘", "Chinese_2": "繁殖", "Example": "Multiply 3 by 4.", "Course": "Sophie數學課", "Date": "2025-12-31", "Correct": 0, "Total": 0},
    {"English": "divide", "Group": "動詞", "Chinese_1": "除", "Chinese_2": "分開", "Example": "Divide 10 by 2.", "Course": "Sophie數學課", "Date": "2026-01-10", "Correct": 0, "Total": 0},
    {"English": "think", "Group": "動詞", "Chinese_1": "思考", "Chinese_2": "想", "Example": "I need to think about it.", "Course": "Cherie思考課", "Date": "2025-11-16", "Correct": 0, "Total": 0},
]

# --- 1. Firestore 初始化 ---
def init_firestore():
    try:
        creds_info = st.secrets["firebase_credentials"]
        creds = service_account.Credentials.from_service_account_info(creds_info)
        db = firestore.Client(credentials=creds)
        return db
    except Exception as e:
        return None

db = init_firestore()
APP_ID = st.secrets.get("APP_ID", "flashcard-pro-v1")

# 路徑規範
USER_LIST_PATH = f"artifacts/{APP_ID}/public/data/users"

# --- 2. 工具函式 (Security & Hash) ---

def hash_password(password):
    """將密碼轉換為 SHA-256 雜湊值"""
    return hashlib.sha256(password.encode()).hexdigest()

def init_users_in_db():
    """初始化學生名單到 Firestore (若不存在)"""
    if not db: return
    docs = db.collection(USER_LIST_PATH).stream()
    if not any(docs):
        # 預設三個學生，初始密碼皆為 1234
        default_pwd = hash_password("1234")
        users = [
            {"name": "Esme", "id": "S001", "password": default_pwd, "color": "#FF69B4"},
            {"name": "Neo", "id": "S002", "password": default_pwd, "color": "#1E90FF"},
            {"name": "Verno", "id": "S003", "password": default_pwd, "color": "#32CD32"}
        ]
        for u in users:
            db.collection(USER_LIST_PATH).document(u["name"]).set(u)

def fetch_users_list():
    """從 Firestore 獲取所有學生資訊"""
    if not db: return {}
    docs = db.collection(USER_LIST_PATH).stream()
    return {d.id: d.to_dict() for d in docs}

# --- 3. Session State 初始化 ---
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
if "user_info" not in st.session_state:
    st.session_state.user_info = None
if "practice_idx" not in st.session_state:
    st.session_state.practice_idx = 0
if "practice_reveal" not in st.session_state:
    st.session_state.practice_reveal = False
if "pending_items" not in st.session_state:
    st.session_state.pending_items = None
if "initialized_users" not in st.session_state:
    st.session_state.initialized_users = set()

# 啟動時初始化使用者名單
init_users_in_db()

# --- 4. 資料庫操作函式 (Vocabulary) ---

def get_vocab_path():
    if st.session_state.logged_in and st.session_state.user_info:
        uid = st.session_state.user_info["id"]
        return f"artifacts/{APP_ID}/users/{uid}/vocabulary"
    return None

def load_vocab_from_db():
    path = get_vocab_path()
    if not db or not path: return []
    docs = db.collection(path).stream()
    data = []
    for d in docs:
        item = d.to_dict()
        item['id'] = d.id
        data.append(item)
    
    uid = st.session_state.user_info["id"]
    if not data and uid not in st.session_state.initialized_users:
        for item in INITIAL_VOCAB:
            db.collection(path).add(item)
        st.session_state.initialized_users.add(uid)
        return load_vocab_from_db()
    return data

def update_db_word(doc_id, update_dict):
    path = get_vocab_path()
    if db and path and doc_id:
        db.collection(path).document(doc_id).update(update_dict)

def save_new_words_to_db(items):
    path = get_vocab_path()
    if db and path:
        for it in items:
            data = {k: v for k, v in it.items() if k != 'id'}
            db.collection(path).add(data)

def delete_words_from_db(doc_ids):
    path = get_vocab_path()
    if db and path:
        for doc_id in doc_ids:
            db.collection(path).document(doc_id).delete()
        uid = st.session_state.user_info["id"]
        st.session_state.initialized_users.add(uid)

# --- 5. AI 與 JS 工具 ---

def call_gemini_to_complete(words_text, course_name, course_date):
    if not words_text.strip(): return []
    prompt = f"""
You are a vocabulary organizing assistant.
I will give you a list of words or messy notes. Your goal is to extract the vocabulary and fill in missing information.
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

Input words:
{words_text}
    """
    payload = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"responseMimeType": "text/plain"}}
    try:
        res = requests.post(f"{GEMINI_API_URL}?key={GEMINI_API_KEY}", json=payload, timeout=30)
        if res.status_code == 200:
            text = res.json()['candidates'][0]['content']['parts'][0]['text']
            raw_items = []
            for line in text.strip().split('\n'):
                if '|' in line:
                    p = [i.strip() for i in line.split('|')]
                    if len(p) >= 5:
                        raw_items.append({"English": p[0], "Group": p[1], "Chinese_1": p[2], "Chinese_2": p[3], "Example": p[4], "Course": course_name, "Date": str(course_date), "Correct": 0, "Total": 0})
            return raw_items
    except: pass
    return []

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

# --- 6. UI 介面 ---

with st.sidebar:
    st.title("🧠 Flashcard Pro")
    
    # 獲取動態學生名單
    users_db = fetch_users_list()
    
    if not st.session_state.logged_in:
        st.subheader("🔑 學生登入")
        selected_name = st.selectbox("請選擇使用者", ["請選擇..."] + list(users_db.keys()))
        input_password = st.text_input("輸入密碼", type="password")
        
        if st.button("登入", use_container_width=True):
            if selected_name != "請選擇..." and input_password:
                user_record = users_db[selected_name]
                if hash_password(input_password) == user_record["password"]:
                    st.session_state.logged_in = True
                    st.session_state.user_info = user_record
                    st.success(f"登入成功，歡迎 {selected_name}！")
                    st.rerun()
                else:
                    st.error("密碼不正確，請再試一次。")
            else:
                st.warning("請選擇使用者並輸入密碼。")
        
        st.info("💡 預設密碼均為 1234")
    else:
        user = st.session_state.user_info
        st.markdown(f"### 👤 {user['name']}")
        st.caption(f"學號: {user['id']}")
        st.divider()
        menu = st.radio("功能選單", ["學習儀表板", "單字管理", "單字練習"])
        if st.button("登出", use_container_width=True):
            st.session_state.logged_in = False
            st.session_state.user_info = None
            st.rerun()

if not st.session_state.logged_in:
    st.title("🚀 歡迎使用 Flashcard Pro")
    st.info("本系統已連線至 Firestore 資料庫。請在側邊欄登入以同步您的個人進度與字庫。")
else:
    u_vocab = load_vocab_from_db()

    if menu == "學習儀表板":
        st.title("📊 學習儀表板")
        if not u_vocab:
            st.info("目前尚無單字資料。")
        else:
            col1, col2, col3 = st.columns(3)
            col1.metric("總單字數", len(u_vocab))
            col2.metric("測驗次數", sum(v.get('Total', 0) for v in u_vocab))
            t_c = sum(v.get('Correct', 0) for v in u_vocab)
            t_q = sum(v.get('Total', 0) for v in u_vocab)
            acc = (t_c / t_q * 100) if t_q > 0 else 0
            col3.metric("正確率", f"{acc:.1f}%")
            st.divider()
            df = pd.DataFrame(u_vocab)
            st.dataframe(df[['English', 'Chinese_1', 'Course', 'Date', 'Correct', 'Total']], use_container_width=True, hide_index=True)

    elif menu == "單字管理":
        st.title("⚙️ 單字管理")
        tab1, tab2, tab3 = st.tabs(["批次輸入", "手動修改", "單字刪除"])
        with tab1:
            c_name = st.text_input("課程名稱:", value="新課程")
            c_date = st.date_input("日期:", value=date.today())
            text_area = st.text_area("輸入內容 (AI 自動解析修正):", height=150)
            if st.button("啟動 AI 處理"):
                if text_area:
                    with st.spinner("解析中..."):
                        st.session_state.pending_items = call_gemini_to_complete(text_area, c_name, c_date)
            if st.session_state.pending_items:
                st.divider()
                st.subheader("📝 預覽解析結果")
                edited = st.data_editor(pd.DataFrame(st.session_state.pending_items), column_order=["English", "Group", "Chinese_1", "Chinese_2", "Example"], use_container_width=True, hide_index=True)
                if st.button("💾 確認儲存至雲端", type="primary"):
                    save_new_words_to_db(edited.to_dict('records'))
                    st.session_state.pending_items = None
                    st.success("儲存成功！"); st.rerun()

        with tab2:
            st.subheader("📝 修改現有單字")
            if u_vocab:
                edited_df = st.data_editor(pd.DataFrame(u_vocab), column_order=["English", "Group", "Chinese_1", "Chinese_2", "Example"], use_container_width=True, hide_index=True)
                if st.button("儲存修改"):
                    for _, row in edited_df.iterrows():
                        update_db_word(row.get('id'), {k: v for k, v in row.to_dict().items() if k != 'id'})
                    st.success("雲端已同步！"); st.rerun()

        with tab3:
            st.subheader("🗑️ 刪除單字")
            if u_vocab:
                df_del = pd.DataFrame(u_vocab); df_del.insert(0, "選取", False)
                res = st.data_editor(df_del[['選取', 'id', 'English', 'Chinese_1', 'Course']], column_config={"id": None}, use_container_width=True, hide_index=True)
                to_delete = res[res["選取"] == True]["id"].tolist()
                if st.button(f"確認刪除 ({len(to_delete)} 個)", type="primary"):
                    if to_delete:
                        delete_words_from_db(to_delete)
                        st.success("已移除！"); st.rerun()

    elif menu == "單字練習":
        st.title("✏️ 單字練習")
        tab_p, tab_t = st.tabs(["快閃練習", "實力測驗"])
        with tab_p:
            if not u_vocab: st.info("請新增單字。")
            else:
                if st.session_state.practice_idx >= len(u_vocab): st.session_state.practice_idx = 0
                target = u_vocab[st.session_state.practice_idx]
                with st.container(border=True):
                    st.caption(f"{target.get('Course')} | {st.session_state.practice_idx + 1}/{len(u_vocab)}")
                    st.header(target['English'])
                    if st.session_state.practice_reveal:
                        st.divider()
                        st.markdown(f"**中文：** {target['Chinese_1']}")
                    c1, c2, c3 = st.columns(3)
                    if c1.button("上一個", use_container_width=True):
                        st.session_state.practice_idx = (st.session_state.practice_idx-1)%len(u_vocab)
                        st.session_state.practice_reveal=False; st.rerun()
                    if c2.button("翻面", use_container_width=True):
                        st.session_state.practice_reveal = not st.session_state.practice_reveal; st.rerun()
                    if c3.button("下一個", use_container_width=True):
                        st.session_state.practice_idx = (st.session_state.practice_idx+1)%len(u_vocab)
                        st.session_state.practice_reveal=False; st.rerun()
                keyboard_bridge()
        with tab_t:
            if st.session_state.get("show_test_toast"):
                st.toast("✅ 正確！"); st.session_state.show_test_toast = False
            if not u_vocab: st.info("請新增單字。")
            else:
                if "test_pool" not in st.session_state or st.button("換一批"):
                    st.session_state.test_pool = random.sample(u_vocab, min(10, len(u_vocab)))
                    st.session_state.t_idx = 0; st.session_state.t_score = 0; st.rerun()
                if st.session_state.t_idx < len(st.session_state.test_pool):
                    curr = st.session_state.test_pool[st.session_state.t_idx]
                    with st.form(key=f"q_{st.session_state.t_idx}", border=True):
                        st.header(curr['English'])
                        ans = st.text_input("輸入中文：")
                        if st.form_submit_button("提交", use_container_width=True):
                            ok = ans and (ans in str(curr['Chinese_1']) or str(curr['Chinese_1']) in ans)
                            if ok: st.session_state.t_score += 1; st.session_state.show_test_toast = True
                            update_db_word(curr.get('id'), {"Correct": int(curr.get('Correct', 0)) + (1 if ok else 0), "Total": int(curr.get('Total', 0)) + 1})
                            st.session_state.t_idx += 1; st.rerun()
                    auto_focus_input()
                else:
                    st.success(f"測驗結束！得分：{st.session_state.t_score} / {len(st.session_state.test_pool)}")

st.divider()
st.caption("Flashcard Pro - 資料已加密並同步至 Firestore")