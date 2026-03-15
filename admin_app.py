import streamlit as st
import pandas as pd
import json
import hashlib
import os
import time
from datetime import datetime, timedelta
from google.cloud import firestore
from google.oauth2 import service_account

# --- 設定區 ---
st.set_page_config(page_title="Flashcard 後台管理", page_icon="⚙️", layout="wide")

# --- 手機適配 CSS ---
st.markdown("""<style>
@media (max-width: 640px) {
    .block-container { padding-left: 1rem !important; padding-right: 1rem !important; }
    .stButton > button { min-height: 44px; }
    .stDataFrame, .stTable { overflow-x: auto !important; }
}
</style>""", unsafe_allow_html=True)

# 環境選項 — 從 secrets.toml 讀取預設值，與 streamlit_app.py 一致
_DEFAULT_APP_ID = st.secrets.get("APP_ID", "flashcard-pro-v1")
ENV_OPTIONS = {
    "正式環境": "flashcard-pro-v1",
    "測試環境": "flashcard-local-test"
}
# 反查預設環境名稱
_DEFAULT_ENV = next((k for k, v in ENV_OPTIONS.items() if v == _DEFAULT_APP_ID), "正式環境")

# 嘗試讀取 Secrets 或本地檔案
if "firebase_credentials" in st.secrets:
    creds_info = st.secrets["firebase_credentials"]
else:
    # 本地測試用
    KEY_FILE_PATH = 'firebase-key.json'
    creds_info = None
    if os.path.exists(KEY_FILE_PATH):
        with open(KEY_FILE_PATH) as f:
            creds_info = json.load(f)

# --- Sidebar: 環境選擇 ---
with st.sidebar:
    st.subheader("🔧 環境設定")
    env_keys = list(ENV_OPTIONS.keys())
    default_idx = env_keys.index(_DEFAULT_ENV) if _DEFAULT_ENV in env_keys else 0
    selected_env = st.selectbox(
        "選擇資料庫環境",
        env_keys,
        index=default_idx,
        key="env_selector"
    )
    APP_ID = ENV_OPTIONS[selected_env]

    # 顯示當前環境
    if selected_env == "正式環境":
        st.success(f"📍 {selected_env}")
    else:
        st.warning(f"🧪 {selected_env}")

    st.caption(f"APP_ID: `{APP_ID}`")
    st.divider()

# --- Firestore 初始化 ---
@st.cache_resource
def get_db():
    if not creds_info:
        st.error("找不到 Firebase 憑證設定。")
        return None
    try:
        creds = service_account.Credentials.from_service_account_info(creds_info)
        return firestore.Client(credentials=creds)
    except Exception as e:
        st.error(f"資料庫連線失敗: {e}")
        return None

db = get_db()
USER_LIST_PATH = f"artifacts/{APP_ID}/public/data/users"
SENTENCE_CATALOG_PATH = f"artifacts/{APP_ID}/public/data/sentences"
SENTENCE_DATA_BASE_PATH = f"artifacts/{APP_ID}/public/data"
SHARED_VOCAB_CATALOG_PATH = f"artifacts/{APP_ID}/public/data/shared_vocab"
SHARED_VOCAB_DATA_PATH = f"artifacts/{APP_ID}/public/data/shared_vocab_data"

# --- 確認對話框（危險操作） ---

@st.dialog("⚠️ 確認刪除使用者")
def confirm_delete_user():
    name = st.session_state.get("_confirm_delete_user")
    st.warning(f"即將刪除使用者「{name}」，刪除後將無法登入，此操作無法復原。")
    c1, c2 = st.columns(2)
    if c1.button("取消", use_container_width=True):
        st.session_state.pop("_confirm_delete_user", None)
        st.rerun()
    if c2.button("確認刪除", type="primary", use_container_width=True):
        db.collection(USER_LIST_PATH).document(name).delete()
        st.session_state.pop("_confirm_delete_user", None)
        st.rerun()

@st.dialog("⚠️ 確認取消 Premium")
def confirm_revoke_premium():
    name = st.session_state.get("_confirm_revoke_name")
    note = st.session_state.get("_confirm_revoke_note", "")
    st.warning(f"即將取消「{name}」的 Premium，將立即降級為免費方案。")
    c1, c2 = st.columns(2)
    if c1.button("取消", use_container_width=True):
        st.session_state.pop("_confirm_revoke_name", None)
        st.rerun()
    if c2.button("確認取消", type="primary", use_container_width=True):
        db.collection(USER_LIST_PATH).document(name).update({
            "plan": "free", "plan_expiry": None,
            "plan_note": f"[已取消] {note}"
        })
        st.session_state.pop("_confirm_revoke_name", None)
        st.rerun()

@st.dialog("⚠️ 確認刪除句型")
def confirm_delete_sentences():
    count = st.session_state.get("_confirm_del_sentence_count", 0)
    st.warning(f"即將刪除 {count} 筆句型資料，此操作無法復原。")
    c1, c2 = st.columns(2)
    if c1.button("取消", use_container_width=True):
        st.session_state.pop("_confirm_del_sentences", None)
        st.rerun()
    if c2.button("確認刪除", type="primary", use_container_width=True):
        items = st.session_state.get("_confirm_del_sentences", [])
        path = st.session_state.get("_confirm_del_sentence_path", "")
        batch = db.batch()
        bc = 0
        for doc_id in items:
            batch.delete(db.collection(path).document(doc_id))
            bc += 1
            if bc >= 400:
                batch.commit(); batch = db.batch(); bc = 0
        if bc > 0: batch.commit()
        st.session_state.pop("_confirm_del_sentences", None)
        get_sentences_content.clear()
        if "editor_df" in st.session_state:
            del st.session_state.editor_df
        st.rerun()

@st.dialog("⚠️ 確認刪除公用單字集")
def confirm_delete_shared_vocab():
    doc_id = st.session_state.get("_confirm_del_sv_id")
    name = st.session_state.get("_confirm_del_sv_name", doc_id)
    st.warning(f"即將刪除公用單字集「{name}」，此操作無法復原。")
    c1, c2 = st.columns(2)
    if c1.button("取消", use_container_width=True):
        st.session_state.pop("_confirm_del_sv_id", None)
        st.rerun()
    if c2.button("確認刪除", type="primary", use_container_width=True):
        db.collection(SHARED_VOCAB_CATALOG_PATH).document(doc_id).delete()
        db.collection(SHARED_VOCAB_DATA_PATH).document(doc_id).delete()
        st.session_state.pop("_confirm_del_sv_id", None)
        st.rerun()

# --- 工具函式 ---
def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def get_users():
    docs = db.collection(USER_LIST_PATH).stream()
    return [d.to_dict() for d in docs]

@st.cache_data(ttl=600)
def get_sentence_books():
    """取得所有句型書目錄，回傳 {id: {name, is_premium}}"""
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
def get_sentences_content(book_id):
    """取得特定書本的所有句型內容"""
    if not db: return []
    path = f"{SENTENCE_DATA_BASE_PATH}/{book_id}"
    docs = db.collection(path).stream()
    data = []
    for d in docs:
        item = d.to_dict()
        item['doc_id'] = d.id # 保存文件ID以便更新
        # 將 Options list 轉為字串方便編輯
        if isinstance(item.get('Options'), list):
            item['Options_Str'] = "|".join(item['Options'])
        else:
            item['Options_Str'] = ""
        data.append(item)
    
    # 依照 Order 排序 (如果有)
    return sorted(data, key=lambda x: x.get('Order', 9999))

# --- UI 介面 ---
st.title("⚙️ Flashcard 後台管理系統")

menu = st.sidebar.radio("管理功能", ["👥 學生帳號管理", "💎 訂閱管理", "📊 AI 用量統計", "📥 匯入句型書 (CSV)", "📝 編輯現有句型書", "📚 管理公用單字集", "🪵 口說練習 Log"])

# ==========================================
# 功能 1: 學生帳號管理 (新增 / 編輯 / 刪除)
# ==========================================
if menu == "👥 學生帳號管理":
    st.header("學生帳號管理")
    
    tab_create, tab_manage = st.tabs(["➕ 新增學生", "✏️ 編輯/刪除學生"])

    # --- 分頁 1: 新增 ---
    with tab_create:
        with st.form("add_user_form"):
            st.subheader("建立新帳號")
            c1, c2 = st.columns(2)
            name = c1.text_input("姓名 (作為登入帳號)", placeholder="例如: Neo")
            sid = c2.text_input("學號 (Student ID)", placeholder="例如: S002")
            
            c3, c4 = st.columns(2)
            pwd = c3.text_input("密碼 (將自動加密)", type="password")
            color = c4.color_picker("代表色", "#1E90FF")
            
            submitted = st.form_submit_button("儲存使用者")
            
            if submitted:
                if name and sid and pwd:
                    user_data = {
                        "name": name,
                        "id": sid,
                        "password": hash_password(pwd),
                        "color": color
                    }
                    db.collection(USER_LIST_PATH).document(name).set(user_data, merge=True)
                    st.success(f"使用者 {name} 已儲存！")
                    time.sleep(1)
                    st.rerun()
                else:
                    st.error("姓名、學號、密碼皆為必填。")

    # --- 分頁 2: 編輯與刪除 ---
    with tab_manage:
        users = get_users()
        if not users:
            st.info("目前無使用者資料。")
        else:
            user_names = [u['name'] for u in users]
            selected_user_name = st.selectbox("請選擇要管理的使用者：", user_names)
            
            target_user = next((u for u in users if u['name'] == selected_user_name), None)
            
            if target_user:
                st.divider()
                col_edit, col_del = st.columns([2, 1])
                
                with col_edit:
                    with st.form("edit_user_form"):
                        st.subheader(f"編輯資料: {selected_user_name}")
                        
                        new_sid = st.text_input("學號", value=target_user.get('id', ''))
                        new_color = st.color_picker("代表色", value=target_user.get('color', '#000000'))
                        new_pwd = st.text_input("重設密碼 (若不修改請留空)", type="password")
                        
                        if st.form_submit_button("💾 更新資料"):
                            update_data = {
                                "id": new_sid,
                                "color": new_color
                            }
                            if new_pwd:
                                update_data["password"] = hash_password(new_pwd)
                            
                            db.collection(USER_LIST_PATH).document(selected_user_name).update(update_data)
                            st.success(f"使用者 {selected_user_name} 更新成功！")
                            time.sleep(1)
                            st.rerun()
                
                with col_del:
                    st.subheader("危險區域")
                    st.write("刪除後該使用者將無法登入。")
                    if st.button(f"🗑️ 刪除使用者 {selected_user_name}"):
                        st.session_state._confirm_delete_user = selected_user_name
                        confirm_delete_user()
    
    st.divider()
    st.caption("目前所有使用者一覽：")
    if users:
        df_users = pd.DataFrame(users)
        display_cols = ['name', 'id', 'color']
        if 'plan' in df_users.columns:
            display_cols.append('plan')
        st.dataframe(df_users[display_cols], use_container_width=True)

# ==========================================
# 功能 2: 訂閱管理
# ==========================================
elif menu == "💎 訂閱管理":
    st.header("訂閱管理")

    users = get_users()
    if not users:
        st.info("目前無使用者資料。")
    else:
        # --- 總覽表格 ---
        st.subheader("📋 全班訂閱狀態一覽")
        overview_rows = []
        for u in users:
            plan = u.get("plan", "free")
            expiry = u.get("plan_expiry")
            note = u.get("plan_note", "")

            # 判斷到期狀態
            if plan == "premium" and expiry:
                if hasattr(expiry, 'date'):
                    expiry_date = expiry.date()
                elif isinstance(expiry, str):
                    expiry_date = datetime.fromisoformat(expiry).date()
                else:
                    expiry_date = None

                if expiry_date and expiry_date >= datetime.now().date():
                    status_display = f"💎 Premium（到期：{expiry_date}）"
                else:
                    status_display = f"⚠️ 已過期（{expiry_date}）"
            else:
                status_display = "🆓 免費"
                expiry_date = None

            overview_rows.append({
                "姓名": u.get("name", ""),
                "學號": u.get("id", ""),
                "訂閱狀態": status_display,
                "備註": note
            })

        st.dataframe(pd.DataFrame(overview_rows), use_container_width=True, hide_index=True)

        st.divider()

        # --- 開通 / 管理 ---
        st.subheader("🔧 開通或調整訂閱")

        user_names = [u['name'] for u in users]
        selected_name = st.selectbox("選擇學生", user_names, key="sub_user_select")
        target_user = next((u for u in users if u['name'] == selected_name), None)

        if target_user:
            current_plan = target_user.get("plan", "free")
            current_expiry = target_user.get("plan_expiry")
            current_note = target_user.get("plan_note", "")

            # 顯示目前狀態
            if current_plan == "premium" and current_expiry:
                if hasattr(current_expiry, 'date'):
                    exp_str = str(current_expiry.date())
                elif isinstance(current_expiry, str):
                    exp_str = current_expiry[:10]
                else:
                    exp_str = str(current_expiry)
                st.info(f"目前方案：💎 Premium，到期日：{exp_str}")
            else:
                st.info("目前方案：🆓 免費")

            col_activate, col_revoke = st.columns(2)

            # 開通 Premium
            with col_activate:
                with st.form("activate_premium_form"):
                    st.markdown("**開通 Premium**")
                    duration_days = st.selectbox("訂閱天數", [30, 60, 90, 180, 365], index=0)
                    note = st.text_input("備註（如收款紀錄）", placeholder="例如：3/1 Line Pay 收到 $149")

                    if st.form_submit_button("💎 開通 Premium", type="primary"):
                        new_expiry = datetime.now() + timedelta(days=duration_days)

                        # 如果目前是 Premium 且未過期，從現有到期日延長
                        if current_plan == "premium" and current_expiry:
                            if hasattr(current_expiry, 'date'):
                                existing_date = datetime.combine(current_expiry.date(), datetime.min.time())
                            elif isinstance(current_expiry, str):
                                existing_date = datetime.fromisoformat(current_expiry)
                            else:
                                existing_date = datetime.now()

                            if existing_date > datetime.now():
                                new_expiry = existing_date + timedelta(days=duration_days)

                        update_data = {
                            "plan": "premium",
                            "plan_expiry": new_expiry,
                            "plan_note": note if note else current_note
                        }
                        db.collection(USER_LIST_PATH).document(selected_name).update(update_data)
                        st.success(f"已開通 {selected_name} 的 Premium，到期日：{new_expiry.strftime('%Y-%m-%d')}")
                        time.sleep(1)
                        st.rerun()

            # 取消 Premium
            with col_revoke:
                st.markdown("**取消 Premium**")
                st.caption("將立即降級為免費方案。")
                if st.button(f"🔄 取消 {selected_name} 的 Premium"):
                    st.session_state._confirm_revoke_name = selected_name
                    st.session_state._confirm_revoke_note = current_note
                    confirm_revoke_premium()

# ==========================================
# 功能 3: AI 用量統計
# ==========================================
elif menu == "📊 AI 用量統計":
    st.header("AI Token 用量統計")

    if not db:
        st.error("資料庫連線失敗。")
    else:
        all_users_docs = db.collection(USER_LIST_PATH).stream()
        all_users_data = {d.id: d.to_dict() for d in all_users_docs}

        if not all_users_data:
            st.info("目前沒有使用者資料。")
        else:
            # 彙整所有用戶的 ai_usage
            rows = []
            for uname, udata in all_users_data.items():
                ai_usage = udata.get("ai_usage", {})
                if not ai_usage:
                    continue

                for usage_type in ["speech", "vocab"]:
                    daily_data = ai_usage.get(usage_type, {})
                    if not daily_data or not isinstance(daily_data, dict):
                        continue
                    for date_str, tokens in daily_data.items():
                        rows.append({
                            "使用者": uname,
                            "類型": "語音辨識" if usage_type == "speech" else "單字補全",
                            "日期": date_str,
                            "Token 數": tokens,
                        })

            if not rows:
                st.info("尚無 AI 使用紀錄。")
            else:
                df = pd.DataFrame(rows)
                df = df.sort_values(["日期", "使用者"], ascending=[False, True])

                # 總覽
                st.subheader("📋 總覽")
                col1, col2, col3 = st.columns(3)
                total_tokens = df["Token 數"].sum()
                total_speech = df[df["類型"] == "語音辨識"]["Token 數"].sum()
                total_vocab = df[df["類型"] == "單字補全"]["Token 數"].sum()
                col1.metric("總 Token 數", f"{total_tokens:,}")
                col2.metric("語音辨識", f"{total_speech:,}")
                col3.metric("單字補全", f"{total_vocab:,}")

                # 預估費用（Gemini 2.5 Flash 約 $0.15/M input + $0.6/M output，簡化取平均 $0.3/M）
                est_cost_usd = total_tokens * 0.3 / 1_000_000
                est_cost_twd = est_cost_usd * 32
                st.caption(f"💰 預估費用：≈ US${est_cost_usd:.4f}（≈ NT${est_cost_twd:.2f}）— 以 Gemini 2.5 Flash 均價估算")

                st.divider()

                # 每日趨勢
                st.subheader("📈 每日用量")
                daily_pivot = df.pivot_table(index="日期", columns="類型", values="Token 數", aggfunc="sum", fill_value=0)
                daily_pivot = daily_pivot.sort_index(ascending=False)
                st.dataframe(daily_pivot, use_container_width=True)

                st.divider()

                # 依使用者
                st.subheader("👤 各使用者用量")
                user_pivot = df.pivot_table(index="使用者", columns="類型", values="Token 數", aggfunc="sum", fill_value=0)
                user_pivot["合計"] = user_pivot.sum(axis=1)
                user_pivot = user_pivot.sort_values("合計", ascending=False)
                st.dataframe(user_pivot, use_container_width=True)

                st.divider()

                # 完整明細
                with st.expander("📄 完整明細"):
                    st.dataframe(df, use_container_width=True, hide_index=True)

# ==========================================
# 功能 4: 匯入句型書 (CSV)
# ==========================================
elif menu == "📥 匯入句型書 (CSV)":
    st.header("匯入句型 CSV")

    st.info("CSV 格式要求：必須包含 `Category`, `Template`, `Options` 三個欄位。Options 請用 `|` 分隔。")

    c1, c2 = st.columns(2)
    dataset_id = c1.text_input("資料庫 ID (Collection ID)", placeholder="例如: junior_100")
    dataset_name = c2.text_input("顯示名稱 (Display Name)", placeholder="例如: 國中核心100句")
    is_premium_book = st.checkbox("🔒 此為付費 Premium 專屬句型書", value=False)

    uploaded_file = st.file_uploader("上傳 CSV", type=["csv"])

    if uploaded_file and dataset_id and dataset_name:
        try:
            df = pd.read_csv(uploaded_file)
            st.write("預覽資料：")
            st.dataframe(df.head())

            if "Category" in df.columns and "Template" in df.columns and "Options" in df.columns:
                if st.button("🚀 開始匯入", type="primary"):
                    progress_bar = st.progress(0)
                    status_text = st.empty()

                    db.collection(SENTENCE_CATALOG_PATH).document(dataset_id).set({
                        "id": dataset_id,
                        "name": dataset_name,
                        "is_premium": is_premium_book,
                        "last_updated": firestore.SERVER_TIMESTAMP
                    }, merge=True)

                    target_path = f"{SENTENCE_DATA_BASE_PATH}/{dataset_id}"
                    batch = db.batch()
                    count = 0
                    total = len(df)

                    for idx, row in df.iterrows():
                        doc_ref = db.collection(target_path).document()

                        raw_opts = str(row.get('Options', ''))
                        opt_list = [o.strip() for o in raw_opts.split('|') if o.strip()]

                        data = {
                            "Category": str(row.get('Category', '未分類')),
                            "Template": str(row.get('Template', '')),
                            "Options": opt_list,
                            "Order": idx, # 自動加入順序
                            "Timestamp": firestore.SERVER_TIMESTAMP
                        }
                        batch.set(doc_ref, data)
                        count += 1

                        if count >= 400:
                            batch.commit()
                            batch = db.batch()
                            count = 0
                            status_text.text(f"已上傳 {idx+1}/{total}...")
                            progress_bar.progress((idx+1)/total)

                    if count > 0:
                        batch.commit()

                    get_sentence_books.clear()
                    get_sentences_content.clear()

                    progress_bar.progress(1.0)
                    st.success(f"✅ 成功匯入 {total} 筆資料至「{dataset_name}」！")

            else:
                st.error("CSV 缺少必要欄位。")
        except Exception as e:
            st.error(f"處理失敗: {e}")

# ==========================================
# 功能 4: 編輯現有句型書 (智慧選單 + 勾選刪除 + 新增)
# ==========================================
elif menu == "📝 編輯現有句型書":
    st.header("編輯句型書內容")
    
    if st.button("🔄 重新整理資料"):
        get_sentence_books.clear()
        get_sentences_content.clear()
        st.rerun()

    books = get_sentence_books()
    if not books:
        st.warning("目前沒有任何句型書。請先至「匯入」頁面新增。")
    else:
        combined_options = []
        book_id_map = {}   # name -> bid
        book_premium_map = {}  # name -> is_premium

        for bid, info in books.items():
            bname = info["name"]
            book_id_map[bname] = bid
            book_premium_map[bname] = info.get("is_premium", False)
            premium_tag = " 🔒" if info.get("is_premium", False) else ""
            combined_options.append(f"{bname}{premium_tag} (全部)")

            s_content = get_sentences_content(bid)
            if s_content:
                df_s = pd.DataFrame(s_content)
                if 'Category' in df_s.columns:
                    cats = sorted(df_s['Category'].unique())
                    for c in cats:
                        combined_options.append(f"{bname}{premium_tag} | {c}")

        selected_option = st.selectbox("選擇要編輯的範圍：", combined_options)
        
        # 清除 🔒 標記後解析
        clean_option = selected_option.replace(" 🔒", "")
        if " (全部)" in clean_option:
            selected_book_name = clean_option.replace(" (全部)", "")
            selected_category = None
        else:
            parts = clean_option.split(" | ")
            selected_book_name = parts[0]
            selected_category = parts[1]
            
        selected_bid = book_id_map.get(selected_book_name)

        if selected_bid:
            # 付費設定切換
            current_premium = book_premium_map.get(selected_book_name, False)
            new_premium = st.checkbox(
                "🔒 此為付費 Premium 專屬句型書",
                value=current_premium,
                key=f"premium_toggle_{selected_bid}"
            )
            if new_premium != current_premium:
                db.collection(SENTENCE_CATALOG_PATH).document(selected_bid).set(
                    {"is_premium": new_premium}, merge=True
                )
                get_sentence_books.clear()
                st.success(f"已{'啟用' if new_premium else '關閉'} Premium 標記")
                st.rerun()
            if "editor_df" not in st.session_state or st.session_state.get("current_book_scope") != selected_option:
                full_data = get_sentences_content(selected_bid)
                df_full = pd.DataFrame(full_data)
                
                if selected_category:
                    if not df_full.empty and 'Category' in df_full.columns:
                        df_filtered = df_full[df_full['Category'] == selected_category].copy()
                    else:
                        df_filtered = pd.DataFrame()
                else:
                    df_filtered = df_full.copy()
                
                if not df_filtered.empty:
                    df_filtered.insert(0, "Select", False)
                else:
                    # 初始化空 DataFrame 結構，方便直接新增
                    df_filtered = pd.DataFrame(columns=["Select", "Category", "Template", "Options_Str", "Order", "doc_id"])
                
                st.session_state.editor_df = df_filtered
                st.session_state.current_book_scope = selected_option
            
            st.info(f"正在編輯：{selected_option} (共 {len(st.session_state.editor_df)} 筆)")
            st.caption("提示：在表格最後一行可以直接輸入資料來**新增**題目。勾選第一欄並點擊刪除按鈕可**刪除**。")

            col_actions = st.columns([1, 1, 6])
            if not st.session_state.editor_df.empty:
                if col_actions[0].button("✅ 全選"):
                    st.session_state.editor_df["Select"] = True
                    st.rerun()
                if col_actions[1].button("⬜ 取消"):
                    st.session_state.editor_df["Select"] = False
                    st.rerun()
            
            edited_df = st.data_editor(
                st.session_state.editor_df,
                column_order=["Select", "Category", "Template", "Options_Str", "Order"],
                column_config={
                    "Select": st.column_config.CheckboxColumn("勾選刪除", width="small"),
                    "Options_Str": st.column_config.TextColumn("Options (用 | 分隔)"),
                    "Order": st.column_config.NumberColumn("順序")
                },
                use_container_width=True,
                hide_index=True,
                num_rows="dynamic", # 關鍵：允許動態新增行
                key="data_editor_main"
            )
            
            col_save, col_del = st.columns([1, 1])
            target_path = f"{SENTENCE_DATA_BASE_PATH}/{selected_bid}"
            
            # 刪除功能
            if col_del.button("🗑️ 刪除選取項目"):
                if "Select" in edited_df.columns:
                    edited_df["Select"] = edited_df["Select"].fillna(False)
                    to_delete_df = edited_df[edited_df["Select"] == True]
                    delete_ids = [row.get("doc_id") for _, row in to_delete_df.iterrows()
                                  if row.get("doc_id") and pd.notna(row.get("doc_id"))]
                    if delete_ids:
                        st.session_state._confirm_del_sentences = delete_ids
                        st.session_state._confirm_del_sentence_count = len(delete_ids)
                        st.session_state._confirm_del_sentence_path = target_path
                        confirm_delete_sentences()
                    else:
                        st.warning("請先勾選要刪除的項目。")

            # 儲存/新增功能
            if col_save.button("💾 儲存變更 (含新增)"):
                # 只儲存沒被勾選刪除的
                if "Select" in edited_df.columns:
                    edited_df["Select"] = edited_df["Select"].fillna(False)
                    to_save_df = edited_df[edited_df["Select"] == False]
                else:
                    to_save_df = edited_df
                
                batch = db.batch()
                count = 0
                updated_count = 0
                
                with st.spinner("正在同步資料庫..."):
                    for _, row in to_save_df.iterrows():
                        if not row.get("Template"): continue
                        
                        raw_opts = str(row.get('Options_Str', ''))
                        opt_list = [o.strip() for o in raw_opts.split('|') if o.strip()]
                        
                        data = {
                            "Category": str(row.get('Category', '')),
                            "Template": str(row.get('Template', '')),
                            "Options": opt_list,
                            "Order": int(row.get("Order", 9999))
                        }
                        
                        doc_id = row.get("doc_id")
                        
                        if doc_id and pd.notna(doc_id):
                            # 更新舊有資料
                            ref = db.collection(target_path).document(doc_id)
                            batch.set(ref, data, merge=True)
                        else:
                            # 這是新增的資料 (沒有 doc_id)
                            ref = db.collection(target_path).document()
                            batch.set(ref, data)
                        
                        updated_count += 1
                        count += 1
                        if count >= 400:
                            batch.commit(); batch = db.batch(); count = 0
                            
                    if count > 0: batch.commit()
                
                st.success(f"已更新 {updated_count} 筆資料！")
                get_sentences_content.clear()
                del st.session_state.editor_df
                time.sleep(1)
                st.rerun()

# ==========================================
# 功能 6: 管理公用單字集
# ==========================================
elif menu == "📚 管理公用單字集":
    st.header("📚 管理公用單字集")

    tab_upload, tab_manage = st.tabs(["📤 上傳單字集", "🗂️ 管理現有單字集"])

    with tab_upload:
        st.subheader("上傳 CSV 到 Firestore")
        set_id = st.text_input("單字集 ID", placeholder="例如: elementary1200")
        set_name = st.text_input("顯示名稱", placeholder="例如: 國小1200單")
        uploaded_file = st.file_uploader("上傳 CSV 檔案", type=["csv"], key="shared_vocab_csv")

        if uploaded_file and set_id and set_name:
            df = pd.read_csv(uploaded_file, keep_default_na=False)
            st.write(f"預覽（共 {len(df)} 筆）：")
            st.dataframe(df.head(10))

            if "English" not in df.columns or "Chinese_1" not in df.columns:
                st.error("CSV 必須包含 'English' 和 'Chinese_1' 欄位。")
            else:
                # 只保留共用欄位
                shared_fields = ["English", "POS", "Chinese_1", "Chinese_2", "Example", "Course"]
                words_list = []
                for _, row in df.iterrows():
                    w = {}
                    for f in shared_fields:
                        w[f] = str(row.get(f, "")).strip()
                    if w["English"]:
                        words_list.append(w)

                unique_courses = sorted(set(w["Course"] for w in words_list if w["Course"]))
                st.info(f"有效單字：{len(words_list)} 筆，分類：{', '.join(unique_courses) if unique_courses else '無'}")

                if st.button("🚀 上傳到 Firestore", type="primary"):
                    with st.spinner("正在上傳..."):
                        # 寫入 catalog
                        db.collection(SHARED_VOCAB_CATALOG_PATH).document(set_id).set({
                            "id": set_id,
                            "name": set_name,
                            "word_count": len(words_list),
                            "courses": unique_courses,
                            "last_updated": firestore.SERVER_TIMESTAMP
                        }, merge=True)

                        # 寫入 data（單一文件）
                        db.collection(SHARED_VOCAB_DATA_PATH).document(set_id).set({
                            "words": words_list,
                            "last_updated": firestore.SERVER_TIMESTAMP
                        })

                        st.success(f"上傳成功！{set_name}（{len(words_list)} 字）")

    with tab_manage:
        st.subheader("現有公用單字集")
        docs = db.collection(SHARED_VOCAB_CATALOG_PATH).stream()
        catalogs = []
        for d in docs:
            data = d.to_dict()
            data["doc_id"] = d.id
            catalogs.append(data)

        if not catalogs:
            st.info("目前沒有公用單字集。")
        else:
            for cat in catalogs:
                col1, col2 = st.columns([4, 1])
                col1.write(f"**{cat.get('name', cat['doc_id'])}** — {cat.get('word_count', '?')} 字")
                if col2.button("🗑️ 刪除", key=f"del_sv_{cat['doc_id']}"):
                    st.session_state._confirm_del_sv_id = cat["doc_id"]
                    st.session_state._confirm_del_sv_name = cat.get("name", cat["doc_id"])
                    confirm_delete_shared_vocab()

# ==========================================
# 功能 7: 口說練習 Log
# ==========================================
elif menu == "🪵 口說練習 Log":
    st.header("口說練習 Log")
    st.caption("查看學生口說練習的錯誤紀錄與裝置資訊")

    # 取得所有學生
    user_docs = db.collection(USER_LIST_PATH).stream()
    user_names = sorted([d.id for d in user_docs])

    if not user_names:
        st.info("目前沒有學生帳號。")
    else:
        selected_user = st.selectbox("選擇學生", user_names, key="log_user_select")
        log_path = f"artifacts/{APP_ID}/users/{selected_user}/drill_logs"
        log_docs = db.collection(log_path).order_by("started_at", direction=firestore.Query.DESCENDING).limit(20).stream()
        logs = []
        for d in log_docs:
            data = d.to_dict()
            data["_id"] = d.id
            logs.append(data)

        if not logs:
            st.info(f"「{selected_user}」目前沒有練習 log。")
        else:
            st.write(f"共 {len(logs)} 筆（最近 20 筆）")
            for log in logs:
                started = log.get("started_at", "?")
                template = log.get("template", "?")
                events = log.get("events", [])
                device = log.get("device", {})

                # 統計事件類型
                error_types = [e.get("type", "") for e in events if e.get("type", "").endswith("error") or e.get("type", "") in ("sr_empty", "sr_disabled", "audio_empty", "gemini_quota")]
                label = f"{'🔴' if error_types else '🟢'} {started[:19]} — {template[:40]}"

                with st.expander(label):
                    # 裝置資訊
                    if device:
                        st.markdown(f"**裝置：** `{device.get('ua', '?')[:80]}`")
                        st.markdown(f"**螢幕：** {device.get('screen', '?')} / **SR：** {'✅' if device.get('sr') else '❌'} / **平台：** {device.get('platform', '?')}")

                    # 事件列表
                    if events:
                        rows = []
                        for e in events:
                            rows.append({
                                "時間": e.get("t", "?")[11:19] if len(e.get("t", "")) > 19 else e.get("t", "?"),
                                "類型": e.get("type", ""),
                                "詳情": e.get("detail", ""),
                            })
                        st.dataframe(rows, use_container_width=True, hide_index=True)
                    else:
                        st.info("無事件紀錄")
