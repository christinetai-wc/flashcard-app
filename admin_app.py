import streamlit as st
import pandas as pd
import json
import hashlib
import os
import time
from google.cloud import firestore
from google.oauth2 import service_account

# --- 設定區 ---
st.set_page_config(page_title="Flashcard 後台管理", page_icon="⚙️", layout="wide")

# 環境選項
ENV_OPTIONS = {
    "正式環境": "flashcard-pro-v1",
    "測試環境": "flashcard-local-test"
}

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
    selected_env = st.selectbox(
        "選擇資料庫環境",
        list(ENV_OPTIONS.keys()),
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

# --- 工具函式 ---
def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def get_users():
    docs = db.collection(USER_LIST_PATH).stream()
    return [d.to_dict() for d in docs]

@st.cache_data(ttl=600)
def get_sentence_books():
    """取得所有句型書目錄"""
    if not db: return {}
    docs = db.collection(SENTENCE_CATALOG_PATH).stream()
    return {d.id: d.to_dict().get('name', d.id) for d in docs}

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

menu = st.sidebar.radio("管理功能", ["👥 學生帳號管理", "📥 匯入句型書 (CSV)", "📝 編輯現有句型書"])

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
                    if st.button(f"🗑️ 刪除使用者 {selected_user_name}", type="primary"):
                        db.collection(USER_LIST_PATH).document(selected_user_name).delete()
                        st.success(f"已刪除 {selected_user_name}")
                        time.sleep(1)
                        st.rerun()
    
    st.divider()
    st.caption("目前所有使用者一覽：")
    if users:
        st.dataframe(pd.DataFrame(users)[['name', 'id', 'color']], use_container_width=True)

# ==========================================
# 功能 2: 匯入句型書 (CSV)
# ==========================================
elif menu == "📥 匯入句型書 (CSV)":
    st.header("匯入句型 CSV")

    st.info("CSV 格式要求：必須包含 `Category`, `Template`, `Options` 三個欄位。Options 請用 `|` 分隔。")

    c1, c2 = st.columns(2)
    dataset_id = c1.text_input("資料庫 ID (Collection ID)", placeholder="例如: junior_100")
    dataset_name = c2.text_input("顯示名稱 (Display Name)", placeholder="例如: 國中核心100句")

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
# 功能 3: 編輯現有句型書 (智慧選單 + 勾選刪除 + 新增)
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
        book_id_map = {} 

        for bid, bname in books.items():
            book_id_map[bname] = bid
            combined_options.append(f"{bname} (全部)")
            
            s_content = get_sentences_content(bid)
            if s_content:
                df_s = pd.DataFrame(s_content)
                if 'Category' in df_s.columns:
                    cats = sorted(df_s['Category'].unique())
                    for c in cats:
                        combined_options.append(f"{bname} | {c}")
        
        selected_option = st.selectbox("選擇要編輯的範圍：", combined_options)
        
        if " (全部)" in selected_option:
            selected_book_name = selected_option.replace(" (全部)", "")
            selected_category = None
        else:
            parts = selected_option.split(" | ")
            selected_book_name = parts[0]
            selected_category = parts[1]
            
        selected_bid = book_id_map.get(selected_book_name)
        
        if selected_bid:
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
            if col_del.button("🗑️ 刪除選取項目", type="primary"):
                # 處理原本有 Select 欄位的 (既有資料)
                if "Select" in edited_df.columns:
                    # 填補 NaN (針對新加入的行預設可能是 NaN)
                    edited_df["Select"] = edited_df["Select"].fillna(False)
                    to_delete_df = edited_df[edited_df["Select"] == True]
                    delete_count = len(to_delete_df)
                    
                    if delete_count > 0:
                        batch = db.batch()
                        count = 0
                        for _, row in to_delete_df.iterrows():
                            doc_id = row.get("doc_id")
                            if doc_id and pd.notna(doc_id):
                                ref = db.collection(target_path).document(doc_id)
                                batch.delete(ref)
                                count += 1
                                if count >= 400:
                                    batch.commit(); batch = db.batch(); count = 0
                        if count > 0: batch.commit()
                        
                        st.success(f"已刪除 {delete_count} 筆資料。")
                        get_sentences_content.clear()
                        del st.session_state.editor_df
                        time.sleep(1)
                        st.rerun()
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
