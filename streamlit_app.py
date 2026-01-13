import streamlit as st
import pandas as pd
import random
import json
import requests
import time
import hashlib
import os
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

# 預設單字內容 (修改 Group -> POS)
INITIAL_VOCAB = [
    {"English": "plus", "POS": "介系詞", "Chinese_1": "加", "Chinese_2": "加上", "Example": "Two plus two is four.", "Course": "Sophie數學課", "Date": "2025-11-15", "Correct": 0, "Total": 0},
    {"English": "minus", "POS": "介系詞", "Chinese_1": "減", "Chinese_2": "減去", "Example": "Five minus two is three.", "Course": "Sophie數學課", "Date": "2025-11-15", "Correct": 0, "Total": 0},
    {"English": "multiply", "POS": "動詞", "Chinese_1": "乘", "Chinese_2": "繁殖", "Example": "Multiply 3 by 4.", "Course": "Sophie數學課", "Date": "2025-12-31", "Correct": 0, "Total": 0},
    {"English": "divide", "POS": "動詞", "Chinese_1": "除", "Chinese_2": "分開", "Example": "Divide 10 by 2.", "Course": "Sophie數學課", "Date": "2026-01-10", "Correct": 0, "Total": 0},
    {"English": "think", "POS": "動詞", "Chinese_1": "思考", "Chinese_2": "想", "Example": "I need to think about it.", "Course": "Cherie思考課", "Date": "2025-11-16", "Correct": 0, "Total": 0},
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

# --- 2. 工具函式 ---

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

init_users_in_db()

# --- 4. 資料庫操作函式 ---

def get_vocab_path():
    if st.session_state.logged_in and st.session_state.user_info:
        uid = st.session_state.user_info["id"]
        return f"artifacts/{APP_ID}/users/{uid}/vocabulary"
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

# --- 5. AI 與 JS 工具 ---

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

def attempt_login():
    """處理登入的 Callback 函式"""
    selected_name = st.session_state.login_user_name
    input_password = st.session_state.login_password
    users_db = st.session_state.users_db_cache
    
    if selected_name != "請選擇..." and input_password:
        user_record = users_db[selected_name]
        if hash_password(input_password) == user_record["password"]:
            st.session_state.logged_in = True
            st.session_state.user_info = user_record
            st.session_state.login_error = None
            sync_vocab_from_db(init_if_empty=True)
        else:
            st.session_state.login_error = "密碼錯誤。"
    else:
        st.session_state.login_error = "請選擇使用者並輸入密碼。"

# --- 7. UI 介面 ---

with st.sidebar:
    st.title("🧠 Flashcard Pro")
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
        menu = st.radio("功能選單", ["學習儀表板", "單字管理", "單字練習"])
        if st.button("登出", use_container_width=True):
            st.session_state.logged_in = False
            st.session_state.user_info = None
            st.session_state.u_vocab = []
            st.rerun()

if not st.session_state.logged_in:
    st.title("🚀 歡迎使用 Flashcard Pro")
    st.info("請登入以開始練習。預設密碼 1234。")
else:
    u_vocab = st.session_state.u_vocab

    if menu == "學習儀表板":
        st.title("📊 學習儀表板")
        if not u_vocab:
            st.info("目前尚無資料。")
            if st.button("🔄 同步雲端"): sync_vocab_from_db(); st.rerun()
        else:
            options = get_course_options(u_vocab)
            selection = st.selectbox("篩選檢視範圍：", options, key="dash_filter")
            filtered_vocab = filter_vocab_data(u_vocab, selection)
            
            col1, col2, col3 = st.columns(3)
            col1.metric("單字數", len(filtered_vocab))
            col2.metric("測驗次數", sum(v.get('Total', 0) for v in filtered_vocab))
            
            t_q = sum(v.get('Total', 0) for v in filtered_vocab)
            acc = (sum(v.get('Correct', 0) for v in filtered_vocab) / t_q * 100) if t_q > 0 else 0
            col3.metric("正確率", f"{acc:.1f}%")
            
            st.divider()
            df = pd.DataFrame(filtered_vocab)
            st.dataframe(df[['English', 'Chinese_1', 'POS', 'Course', 'Date', 'Correct', 'Total']], use_container_width=True, hide_index=True)

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

st.divider()
st.caption("Flashcard Pro - 資料已加密並同步至 Firestore")