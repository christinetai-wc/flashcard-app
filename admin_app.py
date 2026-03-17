import streamlit as st
import pandas as pd
import json
import hashlib
import os
import time
from datetime import datetime, timedelta, timezone
from google.cloud import firestore

TW_TZ = timezone(timedelta(hours=8))
from google.oauth2 import service_account


# --- 快取函式（module level，_db 參數不參與 hash） ---

@st.cache_data(ttl=600)
def _get_sentence_books(_db, catalog_path):
    """取得所有句型書目錄"""
    docs = _db.collection(catalog_path).stream()
    result = {}
    for d in docs:
        data = d.to_dict()
        result[d.id] = {
            "name": data.get("name", d.id),
            "is_premium": data.get("is_premium", False),
        }
    return result


@st.cache_data(ttl=600)
def _get_sentences_content(_db, data_base_path, book_id):
    """取得特定書本的所有句型內容"""
    path = f"{data_base_path}/{book_id}"
    docs = _db.collection(path).stream()
    data = []
    for d in docs:
        item = d.to_dict()
        item['doc_id'] = d.id
        if isinstance(item.get('Options'), list):
            item['Options_Str'] = "|".join(item['Options'])
        else:
            item['Options_Str'] = ""
        data.append(item)
    return sorted(data, key=lambda x: x.get('Order', 9999))


def _fix_practice_time(db, app_id, user_name, student_id, user_info):
    """從 drill logs 計算練習時間，補正 Firebase 中的 practice_time"""
    log_path = f"artifacts/{app_id}/users/{student_id}/drill_logs"
    logs = list(db.collection(log_path).stream())
    if not logs:
        return

    log_secs = {}
    for log in logs:
        data = log.to_dict()
        started = data.get('started_at', '')
        events = data.get('events', [])
        if not events or not started:
            continue
        last_t = events[-1].get('t', '')
        if not last_t:
            continue
        try:
            t0 = datetime.fromisoformat(started.replace('Z', '+00:00'))
            t1 = datetime.fromisoformat(last_t.replace('Z', '+00:00'))
            dur = (t1 - t0).total_seconds()
            tw_date = t0.astimezone(TW_TZ).strftime('%Y-%m-%d')
            log_secs[tw_date] = log_secs.get(tw_date, 0) + int(dur)
        except:
            pass

    if not log_secs:
        return

    existing = user_info.get('practice_time', {})
    updates = {}
    for d, secs in log_secs.items():
        if secs > existing.get(d, 0):
            updates[d] = secs

    if updates:
        users_path = f"artifacts/{app_id}/public/data/users"
        db.collection(users_path).document(user_name).set({'practice_time': updates}, merge=True)
        # 更新本地 user_info 讓畫面即時反映
        if 'practice_time' not in user_info:
            user_info['practice_time'] = {}
        user_info['practice_time'].update(updates)


def render_admin(db, app_id):
    """Render admin UI. 可從 streamlit_app.py 嵌入呼叫，或獨立執行。"""

    # --- Path 常數 ---
    USER_LIST_PATH = f"artifacts/{app_id}/public/data/users"
    SENTENCE_CATALOG_PATH = f"artifacts/{app_id}/public/data/sentences"
    SENTENCE_DATA_BASE_PATH = f"artifacts/{app_id}/public/data"
    SHARED_VOCAB_CATALOG_PATH = f"artifacts/{app_id}/public/data/shared_vocab"
    SHARED_VOCAB_DATA_PATH = f"artifacts/{app_id}/public/data/shared_vocab_data"

    # --- 工具函式 ---
    def hash_password(password):
        return hashlib.sha256(password.encode()).hexdigest()

    def get_users():
        docs = db.collection(USER_LIST_PATH).stream()
        return [d.to_dict() for d in docs]

    def get_sentence_books():
        return _get_sentence_books(db, SENTENCE_CATALOG_PATH)

    def get_sentences_content(book_id):
        return _get_sentences_content(db, SENTENCE_DATA_BASE_PATH, book_id)

    # --- 確認對話框 ---
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
            _get_sentences_content.clear()
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

    # === 主要 UI ===
    st.header("⚙️ 後台管理")

    menu = st.radio("管理功能", ["👥 學生管理", "📊 AI 用量統計", "📝 句型書管理", "📚 管理公用單字集", "🔍 學生詳情"], key="admin_menu", horizontal=True)

    # ==========================================
    # 功能 1: 學生管理（帳號 + 訂閱）
    # ==========================================
    if menu == "👥 學生管理":
        # 全班訂閱狀態一覽（tab 上方）
        users = get_users()
        if users:
            overview_rows = []
            for u in users:
                plan = u.get("plan", "free")
                expiry = u.get("plan_expiry")
                note = u.get("plan_note", "")

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

                overview_rows.append({
                    "姓名": u.get("name", ""),
                    "學號": u.get("id", ""),
                    "訂閱狀態": status_display,
                    "備註": note
                })

            st.dataframe(pd.DataFrame(overview_rows), use_container_width=True, hide_index=True)
            st.divider()

        tab_create, tab_manage, tab_subscription = st.tabs(["➕ 新增學生", "✏️ 編輯/刪除學生", "💎 訂閱管理"])

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

        with tab_subscription:
            users = get_users()
            if not users:
                st.info("目前無使用者資料。")
            else:
                st.subheader("🔧 開通或調整訂閱")

                user_names = [u['name'] for u in users]
                selected_name = st.selectbox("選擇學生", user_names, key="sub_user_select")
                target_user = next((u for u in users if u['name'] == selected_name), None)

                if target_user:
                    current_plan = target_user.get("plan", "free")
                    current_expiry = target_user.get("plan_expiry")
                    current_note = target_user.get("plan_note", "")

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

                    with col_activate:
                        with st.form("activate_premium_form"):
                            st.markdown("**開通 Premium**")
                            duration_days = st.selectbox("訂閱天數", [30, 60, 90, 180, 365], index=0)
                            note = st.text_input("備註（如收款紀錄）", placeholder="例如：3/1 Line Pay 收到 $149")

                            if st.form_submit_button("💎 開通 Premium", type="primary"):
                                new_expiry = datetime.now() + timedelta(days=duration_days)

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

                    with col_revoke:
                        st.markdown("**取消 Premium**")
                        st.caption("將立即降級為免費方案。")
                        if st.button(f"🔄 取消 {selected_name} 的 Premium"):
                            st.session_state._confirm_revoke_name = selected_name
                            st.session_state._confirm_revoke_note = current_note
                            confirm_revoke_premium()

    # ==========================================
    # 功能 2: AI 用量統計
    # ==========================================
    elif menu == "📊 AI 用量統計":
        if not db:
            st.error("資料庫連線失敗。")
        else:
            all_users_docs = db.collection(USER_LIST_PATH).stream()
            all_users_data = {d.id: d.to_dict() for d in all_users_docs}

            if not all_users_data:
                st.info("目前沒有使用者資料。")
            else:
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

                    st.subheader("📋 總覽")
                    col1, col2, col3 = st.columns(3)
                    total_tokens = df["Token 數"].sum()
                    total_speech = df[df["類型"] == "語音辨識"]["Token 數"].sum()
                    total_vocab = df[df["類型"] == "單字補全"]["Token 數"].sum()
                    col1.metric("總 Token 數", f"{total_tokens:,}")
                    col2.metric("語音辨識", f"{total_speech:,}")
                    col3.metric("單字補全", f"{total_vocab:,}")

                    est_cost_usd = total_tokens * 0.3 / 1_000_000
                    est_cost_twd = est_cost_usd * 32
                    st.caption(f"💰 預估費用：≈ US${est_cost_usd:.4f}（≈ NT${est_cost_twd:.2f}）— 以 Gemini 2.5 Flash 均價估算")

                    st.divider()

                    st.subheader("📈 每日用量")
                    daily_pivot = df.pivot_table(index="日期", columns="類型", values="Token 數", aggfunc="sum", fill_value=0)
                    daily_pivot = daily_pivot.sort_index(ascending=False)
                    st.dataframe(daily_pivot, use_container_width=True)

                    st.divider()

                    st.subheader("👤 各使用者用量")
                    user_pivot = df.pivot_table(index="使用者", columns="類型", values="Token 數", aggfunc="sum", fill_value=0)
                    user_pivot["合計"] = user_pivot.sum(axis=1)
                    user_pivot = user_pivot.sort_values("合計", ascending=False)
                    st.dataframe(user_pivot, use_container_width=True)

                    st.divider()

                    with st.expander("📄 完整明細"):
                        st.dataframe(df, use_container_width=True, hide_index=True)

    # ==========================================
    # 功能 3: 句型書管理（匯入 + 編輯）
    # ==========================================
    elif menu == "📝 句型書管理":
        tab_import, tab_edit = st.tabs(["📥 匯入 CSV", "✏️ 編輯現有句型書"])

        with tab_import:
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
                                    "Order": idx,
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

                            _get_sentence_books.clear()
                            _get_sentences_content.clear()

                            progress_bar.progress(1.0)
                            st.success(f"✅ 成功匯入 {total} 筆資料至「{dataset_name}」！")

                    else:
                        st.error("CSV 缺少必要欄位。")
                except Exception as e:
                    st.error(f"處理失敗: {e}")

        with tab_edit:
            if st.button("🔄 重新整理資料"):
                _get_sentence_books.clear()
                _get_sentences_content.clear()
                st.rerun()

            books = get_sentence_books()
            if not books:
                st.warning("目前沒有任何句型書。請先至「匯入」頁面新增。")
            else:
                combined_options = []
                book_id_map = {}
                book_premium_map = {}

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
                        _get_sentence_books.clear()
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
                        num_rows="dynamic",
                        key="data_editor_main"
                    )

                    col_save, col_del = st.columns([1, 1])
                    target_path = f"{SENTENCE_DATA_BASE_PATH}/{selected_bid}"

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

                    if col_save.button("💾 儲存變更 (含新增)"):
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
                                    ref = db.collection(target_path).document(doc_id)
                                    batch.set(ref, data, merge=True)
                                else:
                                    ref = db.collection(target_path).document()
                                    batch.set(ref, data)

                                updated_count += 1
                                count += 1
                                if count >= 400:
                                    batch.commit(); batch = db.batch(); count = 0

                            if count > 0: batch.commit()

                        st.success(f"已更新 {updated_count} 筆資料！")
                        _get_sentences_content.clear()
                        del st.session_state.editor_df
                        time.sleep(1)
                        st.rerun()

    # ==========================================
    # 功能 4: 管理公用單字集
    # ==========================================
    elif menu == "📚 管理公用單字集":
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
                            db.collection(SHARED_VOCAB_CATALOG_PATH).document(set_id).set({
                                "id": set_id,
                                "name": set_name,
                                "word_count": len(words_list),
                                "courses": unique_courses,
                                "last_updated": firestore.SERVER_TIMESTAMP
                            }, merge=True)

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
    # 功能 6: 學生詳情
    # ==========================================
    elif menu == "🔍 學生詳情":
        user_docs = db.collection(USER_LIST_PATH).stream()
        all_users = {}
        for d in user_docs:
            all_users[d.id] = d.to_dict()
        user_names = sorted(all_users.keys())

        if not user_names:
            st.info("目前沒有學生帳號。")
        else:
            selected_user = st.selectbox("選擇學生", user_names, key="log_user_select")
            user_info = all_users.get(selected_user, {})
            student_id = user_info.get("id", selected_user)

            # 自動從 drill logs 補正 practice_time
            _fix_practice_time(db, app_id, selected_user, student_id, user_info)

            st.subheader(f"📋 {selected_user} 概覽")
            plan = user_info.get("plan", "free")
            plan_expiry = user_info.get("plan_expiry", "")
            expiry_str = ""
            if plan_expiry:
                if hasattr(plan_expiry, "strftime"):
                    expiry_str = plan_expiry.strftime("%Y-%m-%d")
                else:
                    expiry_str = str(plan_expiry)[:10]

            col_a, col_b, col_c = st.columns(3)
            col_a.metric("方案", "Premium" if plan == "premium" else "免費", expiry_str if plan == "premium" else "")
            col_b.metric("學生 ID", student_id)
            col_c.metric("顏色", user_info.get("color", "—"))

            practice_time = user_info.get("practice_time", {})
            if practice_time:
                st.subheader("⏱️ 練習時間")
                sorted_dates = sorted(practice_time.keys(), reverse=True)
                time_rows = []
                total_seconds = 0
                for date in sorted_dates:
                    secs = practice_time[date]
                    total_seconds += secs
                    mins = secs // 60
                    time_rows.append({"日期": date, "時長": f"{mins} 分 {secs % 60} 秒"})
                st.metric("累計練習時間", f"{total_seconds // 60} 分鐘")
                st.dataframe(time_rows, use_container_width=True, hide_index=True)

            ai_usage = user_info.get("ai_usage", {})
            if ai_usage:
                st.subheader("🤖 AI 用量")
                drill_count = ai_usage.get("drill_count", {})
                speech_tokens = ai_usage.get("speech", {})
                all_dates = sorted(set(list(drill_count.keys()) + list(speech_tokens.keys())), reverse=True)
                usage_rows = []
                for date in all_dates:
                    usage_rows.append({
                        "日期": date,
                        "判讀次數": drill_count.get(date, 0),
                        "語音 Token": speech_tokens.get(date, 0),
                    })
                st.dataframe(usage_rows, use_container_width=True, hide_index=True)

            sentence_stats = user_info.get("sentence_stats", {})
            if sentence_stats:
                st.subheader("📖 句型進度")
                stats_rows = []
                for ds_id, stats in sentence_stats.items():
                    name = stats.get("name", ds_id)
                    completed = stats.get("completed", 0)
                    total = stats.get("total", 0)
                    last_active = stats.get("last_active", "")[:10]
                    pct = round(completed / total * 100) if total > 0 else 0
                    stats_rows.append({
                        "句型書": name,
                        "進度": f"{completed}/{total}（{pct}%）",
                        "最後練習": last_active,
                    })
                st.dataframe(stats_rows, use_container_width=True, hide_index=True)

            st.subheader("🪵 練習 Log")
            log_path = f"artifacts/{app_id}/users/{student_id}/drill_logs"
            log_docs = db.collection(log_path).order_by("started_at", direction=firestore.Query.DESCENDING).limit(20).stream()
            logs = []
            for d in log_docs:
                data = d.to_dict()
                data["_id"] = d.id
                logs.append(data)

            if not logs:
                st.info("目前沒有練習 log。")
            else:
                st.write(f"最近 {len(logs)} 筆")
                for log in logs:
                    started = log.get("started_at", "?")
                    template = log.get("template", "?")
                    events = log.get("events", [])
                    device = log.get("device", {})

                    error_types = [e.get("type", "") for e in events if e.get("type", "") in ("mic_error", "gemini_error", "save_error", "audio_empty")]
                    label = f"{'🔴' if error_types else '🟢'} {started[:19]} — {template[:40]}"

                    with st.expander(label):
                        if device:
                            st.markdown(f"**裝置：** `{device.get('ua', '?')[:100]}`")
                            st.markdown(f"**螢幕：** {device.get('screen', '?')} / **SR：** {'✅' if device.get('sr') else '❌'} / **平台：** {device.get('platform', '?')}")

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

            # === 📊 AI 分析報告 ===
            st.subheader("📊 AI 分析報告")

            # 顯示已存的報告
            report_path = f"artifacts/{app_id}/users/{student_id}/reports"
            try:
                report_docs = list(db.collection(report_path).order_by("created_at", direction=firestore.Query.DESCENDING).limit(1).stream())
            except Exception:
                report_docs = []

            if report_docs:
                report_data = report_docs[0].to_dict()
                report_date = report_docs[0].id
                st.caption(f"最新報告：{report_date}")
                st.markdown(report_data.get("content", "（無內容）"))
            else:
                st.info("尚未產生報告。")

            # 產生新報告按鈕
            if st.button("🤖 產生 AI 分析報告", use_container_width=True):
                with st.spinner("正在用 Gemini 產生報告（約 30 秒）..."):
                    try:
                        from student_report import collect_student_data, generate_ai_report_text
                        data, secrets = collect_student_data(selected_user)
                        if data:
                            report_text = generate_ai_report_text(data, secrets)
                            if report_text:
                                # 存到 Firestore
                                today_str = datetime.now().strftime("%Y-%m-%d")
                                db.collection(report_path).document(today_str).set({
                                    "content": report_text,
                                    "created_at": firestore.SERVER_TIMESTAMP,
                                })
                                st.success("報告已產生！")
                                st.markdown(report_text)
                            else:
                                st.error("報告產生失敗。")
                        else:
                            st.error(f"找不到學生「{selected_user}」的資料。")
                    except Exception as e:
                        st.error(f"產生報告時發生錯誤：{e}")


# === 獨立執行模式 ===
if __name__ == "__main__":
    st.set_page_config(page_title="Flashcard 後台管理", page_icon="⚙️", layout="wide")

    st.markdown("""<style>
    @media (max-width: 640px) {
        .block-container { padding-left: 1rem !important; padding-right: 1rem !important; }
        .stButton > button { min-height: 44px; }
        .stDataFrame, .stTable { overflow-x: auto !important; }
    }
    </style>""", unsafe_allow_html=True)

    _DEFAULT_APP_ID = st.secrets.get("APP_ID", "flashcard-pro-v1")
    ENV_OPTIONS = {
        "正式環境": "flashcard-pro-v1",
        "測試環境": "flashcard-local-test"
    }
    _DEFAULT_ENV = next((k for k, v in ENV_OPTIONS.items() if v == _DEFAULT_APP_ID), "正式環境")

    if "firebase_credentials" in st.secrets:
        creds_info = st.secrets["firebase_credentials"]
    else:
        KEY_FILE_PATH = 'firebase-key.json'
        creds_info = None
        if os.path.exists(KEY_FILE_PATH):
            with open(KEY_FILE_PATH) as f:
                creds_info = json.load(f)

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

        if selected_env == "正式環境":
            st.success(f"📍 {selected_env}")
        else:
            st.warning(f"🧪 {selected_env}")

        st.caption(f"APP_ID: `{APP_ID}`")
        st.divider()

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
    if db:
        render_admin(db, APP_ID)
