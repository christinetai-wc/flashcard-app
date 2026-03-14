"""
修復腳本：從 sentence_progress 重新計算 sentence_stats
用途：JS 元件改版後，舊的練習紀錄沒有寫入 sentence_stats，排行榜看不到

執行方式：
  streamlit run fix_sentence_stats.py
"""
import streamlit as st
import hashlib
from google.cloud import firestore
from google.oauth2 import service_account

st.set_page_config(page_title="修復 sentence_stats", page_icon="🔧")
st.title("🔧 修復排行榜統計資料")

# --- Firestore ---
APP_ID = st.secrets.get("APP_ID", "flashcard-pro-v1")
creds_info = st.secrets["firebase_credentials"]
creds = service_account.Credentials.from_service_account_info(creds_info)
db = firestore.Client(credentials=creds)

USER_LIST_PATH = f"artifacts/{APP_ID}/public/data/users"
SENTENCE_CATALOG_PATH = f"artifacts/{APP_ID}/public/data/sentences"
SENTENCE_DATA_BASE_PATH = f"artifacts/{APP_ID}/public/data"


def hash_string(s):
    return hashlib.md5(s.encode()).hexdigest()


def run_fix():
    # 1. 讀取所有題庫
    st.write("📖 讀取句型書目錄...")
    catalogs = {}
    for d in db.collection(SENTENCE_CATALOG_PATH).stream():
        data = d.to_dict()
        catalogs[d.id] = {
            "name": data.get("name", d.id),
            "is_premium": data.get("is_premium", False),
        }
    st.write(f"  找到 {len(catalogs)} 個題庫")

    # 2. 讀取每個題庫的句型
    sentences_by_dataset = {}
    for dataset_id, cat_info in catalogs.items():
        docs = db.collection(f"{SENTENCE_DATA_BASE_PATH}/{dataset_id}").stream()
        sentences = []
        for d in docs:
            data = d.to_dict()
            if "Template" in data:
                sentences.append(data)
        sentences_by_dataset[dataset_id] = sentences
        st.write(f"  📘 {cat_info['name']}: {len(sentences)} 句")

    # 3. 讀取所有使用者
    st.write("👥 讀取使用者...")
    users = {}
    for d in db.collection(USER_LIST_PATH).stream():
        users[d.id] = d.to_dict()
    st.write(f"  找到 {len(users)} 個使用者")

    # 4. 逐個使用者修復
    st.write("🔄 開始修復...")
    progress_bar = st.progress(0)
    fixed_count = 0

    for idx, (user_name, user_data) in enumerate(users.items()):
        user_id = user_data.get("id", "")
        if not user_id:
            continue

        # 讀取該使用者的 sentence_progress
        progress_path = f"artifacts/{APP_ID}/users/{user_id}/sentence_progress"
        progress_docs = db.collection(progress_path).stream()
        progress_map = {}  # template_hash -> {completion_count, dataset_id}
        for d in progress_docs:
            data = d.to_dict()
            progress_map[d.id] = {
                "completion_count": int(data.get("completion_count", 0)),
                "dataset_id": data.get("dataset_id", ""),
            }

        if not progress_map:
            progress_bar.progress((idx + 1) / len(users))
            continue

        # 計算每個題庫的 completed 數
        stats = {}
        for dataset_id, sentences in sentences_by_dataset.items():
            completed = 0
            for s in sentences:
                t_hash = hash_string(s["Template"])
                p = progress_map.get(t_hash)
                if p and p.get("completion_count", 0) > 0:
                    completed += 1

            if completed > 0:
                cat_info = catalogs.get(dataset_id, {})
                stats[dataset_id] = {
                    "name": cat_info.get("name", dataset_id),
                    "total": len(sentences),
                    "completed": completed,
                    "last_active": firestore.SERVER_TIMESTAMP,
                }

        if stats:
            # 寫入 sentence_stats
            update_data = {}
            for dataset_id, stat in stats.items():
                update_data[f"sentence_stats.{dataset_id}"] = stat

            db.collection(USER_LIST_PATH).document(user_name).update(update_data)
            fixed_count += 1
            st.write(f"  ✅ {user_name}: {', '.join(f'{s['name']}({s['completed']}/{s['total']})' for s in stats.values())}")

        progress_bar.progress((idx + 1) / len(users))

    st.success(f"🎉 修復完成！更新了 {fixed_count} 個使用者的排行榜統計。")


st.warning("此腳本會從 sentence_progress 重新計算所有使用者的 sentence_stats（排行榜資料）。")
if st.button("🚀 開始修復", type="primary"):
    run_fix()
