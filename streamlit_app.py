import streamlit as st
import pandas as pd
import random
import json
import requests
import time
from datetime import date
from streamlit.components.v1 import html

# --- 0. 設定與常數 ---
st.set_page_config(page_title="Flashcard 專業版", page_icon="🧠", layout="wide")

APP_ID = "flashcard"
GEMINI_MODEL = "gemini-2.5-flash-preview-09-2025"
GEMINI_API_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"

# 預設單字格式 (新增 Total 欄位)
DEFAULT_VOCAB = [
    {"English": "plus", "Group": "介系詞", "Chinese_1": "加", "Chinese_2": "加上", "Example": "Two plus two is four.", "Course": "Sophie數學課", "Date": "2025-11-15", "Correct": 2, "Total": 5},
    {"English": "minus", "Group": "介系詞", "Chinese_1": "減", "Chinese_2": "減去", "Example": "Five minus two is three.", "Course": "Sophie數學課", "Date": "2025-11-15", "Correct": 1, "Total": 3},
    {"English": "multiply", "Group": "動詞", "Chinese_1": "乘", "Chinese_2": "繁殖", "Example": "Multiply 3 by 4.", "Course": "Sophie數學課", "Date": "2025-12-31", "Correct": 0, "Total": 2},
    {"English": "divide", "Group": "動詞", "Chinese_1": "除", "Chinese_2": "分開", "Example": "Divide 10 by 2.", "Course": "Sophie數學課", "Date": "2026-01-10", "Correct": 0, "Total": 1},
    {"English": "think", "Group": "動詞", "Chinese_1": "思考", "Chinese_2": "想", "Example": "I need to think about it.", "Course": "Cherie思考課", "Date": "2025-11-16", "Correct": 3, "Total": 4},
    {"English": "reason", "Group": "名詞", "Chinese_1": "原因", "Chinese_2": "理性", "Example": "Give me a reason.", "Course": "Cherie思考課", "Date": "2025-12-30", "Correct": 0, "Total": 0},
    {"English": "logic", "Group": "名詞", "Chinese_1": "邏輯", "Chinese_2": "理路", "Example": "The logic is sound.", "Course": "Cherie思考課", "Date": "2026-01-09", "Correct": 0, "Total": 0},
]

# --- 1. Session State 初始化 ---
if "user_storage" not in st.session_state:
    st.session_state.user_storage = {
        "Esme": {"vocab": list(DEFAULT_VOCAB), "id": "S001", "color": "#FF69B4"},
        "Neo": {"vocab": list(DEFAULT_VOCAB), "id": "S002", "color": "#1E90FF"},
        "Verno": {"vocab": list(DEFAULT_VOCAB), "id": "S003", "color": "#32CD32"}
    }

if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
if "current_user_name" not in st.session_state:
    st.session_state.current_user_name = None

if "practice_idx" not in st.session_state:
    st.session_state.practice_idx = 0
if "practice_reveal" not in st.session_state:
    st.session_state.practice_reveal = False

# --- 2. 工具函式 ---

def call_gemini_to_complete(words_text, course_name, course_date):
    """調用 Gemini 補齊單字資訊"""
    words = [w.strip() for w in words_text.split('\n') if w.strip()]
    if not words: return []
    
    apiKey = "" 
    prompt = f"""
    請為以下英文單字提供詳細資訊，以 JSON 格式回傳一個物件列表。
    格式：[{"{"}"English": "word", "Group": "詞性", "Chinese_1": "主要中文", "Chinese_2": "次要中文", "Example": "英文例句"{"}"}, ...]
    單字列表：{", ".join(words)}
    請務必只回傳純 JSON 代碼，不要有任何文字說明。
    """
    
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json"}
    }
    
    for attempt in range(5):
        try:
            response = requests.post(f"{GEMINI_API_URL}?key={apiKey}", json=payload, timeout=30)
            if response.status_code == 200:
                result = response.json()
                if 'candidates' in result:
                    text_content = result['candidates'][0].get('content', {}).get('parts', [{}])[0].get('text', '')
                    raw_items = json.loads(text_content.strip())
                    for item in raw_items:
                        item["Course"] = course_name
                        item["Date"] = str(course_date)
                        item["Correct"] = 0
                        item["Total"] = 0 # 初始化 Total 欄位
                    return raw_items
            time.sleep(2 ** attempt)
        except Exception:
            time.sleep(2 ** attempt)
    return []

def get_course_options(vocab):
    """生成課程選擇清單"""
    if not vocab: return ["全部單字"]
    df = pd.DataFrame(vocab)
    unique_courses = sorted(df['Course'].unique())
    unique_instances = df[['Course', 'Date']].drop_duplicates().sort_values(['Course', 'Date'], ascending=[True, False])
    options = ["全部單字"]
    for c in unique_courses:
        options.append(f"📚 {c} (全部)")
        dates = unique_instances[unique_instances['Course'] == c]['Date'].tolist()
        for d in dates:
            options.append(f"   📅 {c} | {d}")
    return options

def filter_vocab(vocab, selection):
    """過濾單字清單"""
    if selection == "全部單字": return vocab
    df = pd.DataFrame(vocab)
    if "(全部)" in selection:
        course_name = selection.replace("📚 ", "").replace(" (全部)", "").strip()
        return df[df['Course'] == course_name].to_dict('records')
    elif "|" in selection:
        parts = selection.replace("   📅 ", "").split("|")
        course_name = parts[0].strip()
        course_date = parts[1].strip()
        return df[(df['Course'] == course_name) & (df['Date'] == course_date)].to_dict('records')
    return vocab

# --- 3. JavaScript 輔助函式 ---

def keyboard_bridge():
    """全域鍵盤監聽（用於 Flashcard）"""
    js_code = """
    <script>
    var doc = window.parent.document;
    if (window.parent.myFlashcardKeyHandler) {
        doc.removeEventListener('keydown', window.parent.myFlashcardKeyHandler);
    }
    window.parent.myFlashcardKeyHandler = function(e) {
        const getBtnByText = (text) => {
            const buttons = Array.from(doc.querySelectorAll('button'));
            return buttons.find(b => b.innerText.includes(text));
        };
        if (e.key === 'ArrowRight') {
            const btn = getBtnByText("下一個 (→)");
            if (btn) { btn.click(); e.preventDefault(); }
        } else if (e.key === 'ArrowLeft') {
            const btn = getBtnByText("上一個 (←)");
            if (btn) { btn.click(); e.preventDefault(); }
        } else if (e.key === ' ' || e.code === 'Space') {
            const btn = getBtnByText("翻面 (Space)");
            if (btn) { btn.click(); e.preventDefault(); }
        }
    };
    doc.addEventListener('keydown', window.parent.myFlashcardKeyHandler);
    </script>
    """
    html(js_code, height=0)

def auto_focus_input():
    """自動聚焦到測驗輸入框 (增強版)"""
    js_code = """
    <script>
    setTimeout(function() {
        try {
            const doc = window.parent.document;
            let targetInput = null;

            // 策略 1: 嘗試透過 aria-label 尋找
            targetInput = doc.querySelector('input[aria-label="請輸入中文意思："]');

            // 策略 2: 如果找不到，尋找包含特定標籤文字的 stTextInput 容器
            if (!targetInput) {
                const widgets = doc.querySelectorAll('div[data-testid="stTextInput"]');
                for (let i = 0; i < widgets.length; i++) {
                    if (widgets[i].innerText.includes("請輸入中文意思：")) {
                        targetInput = widgets[i].querySelector('input');
                        break;
                    }
                }
            }
            
            // 策略 3: 如果還是找不到，嘗試找頁面上第一個文字輸入框 (Fallback)
            if (!targetInput) {
                 const allInputs = doc.querySelectorAll('input[type="text"]');
                 if (allInputs.length > 0) targetInput = allInputs[0];
            }

            if (targetInput) {
                targetInput.focus();
            }
        } catch (e) {
            console.log("Auto-focus error:", e);
        }
    }, 250); // 延遲 250ms 確保 DOM 渲染完成
    </script>
    """
    html(js_code, height=0)

# --- 4. 側邊欄 ---
with st.sidebar:
    st.title("🧠 Flashcard Pro")
    if not st.session_state.logged_in:
        st.subheader("🔑 學生登入")
        name = st.selectbox("請選擇使用者", ["Esme", "Neo", "Verno"])
        if st.button("登入", use_container_width=True):
            st.session_state.logged_in = True
            st.session_state.current_user_name = name
            st.rerun()
    else:
        u_name = st.session_state.current_user_name
        u_data = st.session_state.user_storage[u_name]
        st.markdown(f"### 👤 {u_name}")
        st.caption(f"ID: {u_data['id']} | 題庫: {len(u_data['vocab'])} 字")
        st.divider()
        menu = st.radio("功能導覽", ["📊 學習儀表板", "⚙️ 單字管理", "✏️ 單字練習"])
        if st.button("登出", use_container_width=True):
            st.session_state.logged_in = False
            st.session_state.current_user_name = None
            st.rerun()

# --- 5. 主要內容區 ---
if not st.session_state.logged_in:
    st.title("🚀 歡迎使用 Flashcard 專業版")
    st.info("請從側邊欄登入以存取您的個人單字庫。")
else:
    u_name = st.session_state.current_user_name
    u_vocab = st.session_state.user_storage[u_name]["vocab"]

    if menu == "📊 學習儀表板":
        st.title(f"📊 {u_name} 的學習數據")
        options = get_course_options(u_vocab)
        selection = st.selectbox("選擇檢視範圍：", options, key="dash_filter")
        df = pd.DataFrame(filter_vocab(u_vocab, selection))
        # 顯示欄位包含 Total
        st.dataframe(df[['English', 'Group', 'Chinese_1', 'Course', 'Date', 'Correct', 'Total']], use_container_width=True, hide_index=True)

    elif menu == "⚙️ 單字管理":
        st.title("⚙️ 單字管理系統")
        tab_in, tab_ed, tab_de = st.tabs(["➕ 批次輸入", "📝 手動修改", "🗑️ 單字刪除"])
        with tab_in:
            c_name = st.text_input("課程科目:", value="Sophie數學課")
            c_date = st.date_input("課程日期:", value=date.today())
            input_text = st.text_area("英文清單 (一行一個):", height=150)
            if st.button("🤖 AI 補齊", use_container_width=True):
                new_items = call_gemini_to_complete(input_text, c_name, c_date)
                if new_items: st.session_state.pending_items = new_items; st.rerun()
            if "pending_items" in st.session_state:
                st.table(pd.DataFrame(st.session_state.pending_items)[['English', 'Chinese_1']])
                if st.button("💾 確認儲存", use_container_width=True):
                    st.session_state.user_storage[u_name]["vocab"].extend(st.session_state.pending_items)
                    del st.session_state.pending_items; st.rerun()
        with tab_ed:
            opts = get_course_options(u_vocab)
            sel = st.selectbox("修改範圍：", opts, key="ed_filter")
            filtered = filter_vocab(u_vocab, sel)
            if filtered:
                new_df = st.data_editor(pd.DataFrame(filtered), column_order=["English", "Group", "Chinese_1", "Chinese_2", "Example"], use_container_width=True, hide_index=True)
                if st.button("💾 儲存修改", use_container_width=True):
                    for _, row in new_df.iterrows():
                        for item in u_vocab:
                            if item['English'] == row['English']: item.update(row.to_dict()); break
                    st.success("更新成功！"); st.rerun()
        with tab_de:
            opts = get_course_options(u_vocab)
            sel = st.selectbox("刪除範圍：", opts, key="de_filter")
            filtered = filter_vocab(u_vocab, sel)
            if filtered:
                all_sel = st.checkbox("全部勾選", value=False)
                df_de = pd.DataFrame(filtered); df_de.insert(0, "選取", all_sel)
                res = st.data_editor(df_de[['選取', 'English', 'Chinese_1']], use_container_width=True, hide_index=True)
                targets = res[res["選取"] == True]["English"].tolist()
                if st.button(f"🗑️ 確認刪除 ({len(targets)})", type="primary", use_container_width=True):
                    st.session_state.user_storage[u_name]["vocab"] = [v for v in u_vocab if v['English'] not in targets]; st.rerun()

    elif menu == "✏️ 單字練習":
        st.title("✏️ 練習與測驗")
        options = get_course_options(u_vocab)
        selection = st.selectbox("🎯 選擇練習範圍：", options, key="practice_filter")
        current_set = filter_vocab(u_vocab, selection)
        
        tab_p, tab_t = st.tabs(["📖 Flashcard 練習", "📝 實力測驗"])
        
        with tab_p:
            if not current_set:
                st.info("範圍內無單字。")
            else:
                if st.session_state.get('practice_idx', 0) >= len(current_set): st.session_state.practice_idx = 0
                target = current_set[st.session_state.practice_idx]
                
                with st.container(border=True):
                    st.caption(f"{target.get('Course')} | {target.get('Date')} | 進度: {st.session_state.practice_idx + 1}/{len(current_set)}")
                    st.markdown(f"## {target['English']}")
                    
                    if st.session_state.practice_reveal:
                        st.divider()
                        st.markdown(f"**💡 中文：** {target['Chinese_1']} {f'({target.get('Chinese_2')})' if target.get('Chinese_2') else ''}")
                        st.markdown(f"**📝 例句：** {target['Example']}")
                    
                    st.write("") 
                    
                    c1, c2, c3 = st.columns(3)
                    if c1.button("上一個 (←)", key="prev_btn", use_container_width=True):
                        st.session_state.practice_idx = (st.session_state.practice_idx - 1) % len(current_set)
                        st.session_state.practice_reveal = False; st.rerun()
                    if c2.button("翻面 (Space)", key="reveal_btn", use_container_width=True):
                        st.session_state.practice_reveal = not st.session_state.practice_reveal; st.rerun()
                    if c3.button("下一個 (→)", key="next_btn", use_container_width=True):
                        st.session_state.practice_idx = (st.session_state.practice_idx + 1) % len(current_set)
                        st.session_state.practice_reveal = False; st.rerun()
                
                keyboard_bridge()

        with tab_t:
            if st.session_state.get("show_correct_toast"):
                st.toast("✅ 答對了！")
                st.session_state.show_correct_toast = False 

            if len(current_set) < 1:
                st.info("範圍內無單字。")
            else:
                if "test_pool" not in st.session_state or st.button("重新產生測驗"):
                    st.session_state.test_pool = random.sample(current_set, min(10, len(current_set)))
                    st.session_state.test_idx = 0; st.session_state.test_score = 0; st.session_state.test_finished = False; st.rerun()
                
                if not st.session_state.test_finished:
                    curr_t = st.session_state.test_pool[st.session_state.test_idx]
                    st.write(f"進度: {st.session_state.test_idx + 1} / {len(st.session_state.test_pool)}")
                    
                    with st.form(key=f"test_form_{st.session_state.test_idx}", border=True):
                        st.markdown(f"## {curr_t['English']}")
                        
                        # 輸入框
                        t_ans = st.text_input("請輸入中文意思：", key=f"t_in_{st.session_state.test_idx}")
                        
                        submitted = st.form_submit_button("確認提交", use_container_width=True)
                        
                        if submitted:
                            # 答對判斷邏輯
                            is_correct = False
                            if t_ans and (t_ans in curr_t['Chinese_1'] or curr_t['Chinese_1'] in t_ans):
                                is_correct = True
                                st.session_state.test_score += 1
                                st.session_state.show_correct_toast = True
                            
                            # 更新資料庫中的 Correct 與 Total
                            for v in u_vocab:
                                if v['English'] == curr_t['English']:
                                    v['Total'] = v.get('Total', 0) + 1  # 無論對錯都 +1
                                    if is_correct:
                                        v['Correct'] = v.get('Correct', 0) + 1
                                    break

                            if st.session_state.test_idx + 1 < len(st.session_state.test_pool):
                                st.session_state.test_idx += 1
                            else: st.session_state.test_finished = True
                            st.rerun()
                    
                    # 呼叫增強版自動聚焦
                    auto_focus_input()

                else:
                    st.balloons(); st.success(f"測驗結束！得分：{st.session_state.test_score} / {len(st.session_state.test_pool)}")

st.divider()
st.caption(f"App ID: {APP_ID} | 多學生同步模式：啟用")