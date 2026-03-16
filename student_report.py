"""
學生口說練習報告工具
用法：python student_report.py 語晰
      python student_report.py 語晰 --ai    # 加上 Gemini AI 分析報告
"""
import sys
import json
import requests
from datetime import datetime, timezone, timedelta
import firebase_admin
from firebase_admin import credentials, firestore
import toml

TW = timezone(timedelta(hours=8))

def utc_to_tw(iso_str):
    """將 UTC ISO 字串轉為台灣時間字串"""
    if not iso_str:
        return ''
    try:
        # 處理 Z 結尾或無時區的 ISO 字串
        s = iso_str.replace('Z', '+00:00')
        if '+' not in s and s.endswith('00:00') is False:
            s += '+00:00'
        dt = datetime.fromisoformat(s).astimezone(TW)
        return dt.strftime('%Y-%m-%d %H:%M:%S')
    except:
        return iso_str

def load_secrets():
    with open('.streamlit/secrets.toml') as f:
        return toml.loads(f.read())

def init_db(secrets):
    cred = credentials.Certificate(dict(secrets['firebase_credentials']))
    app = firebase_admin.initialize_app(cred)
    db = firestore.client()
    app_id = secrets.get('APP_ID', 'flashcard-pro-v1')
    return db, app_id, app

def collect_student_data(student_name):
    """撈取學生所有資料，回傳結構化 dict"""
    secrets = load_secrets()
    db, app_id, app = init_db(secrets)
    users_path = f'artifacts/{app_id}/public/data/users'

    user_doc = db.collection(users_path).document(student_name).get()
    if not user_doc.exists:
        firebase_admin.delete_app(app)
        return None, secrets

    user_data = user_doc.to_dict()
    user_id = user_data.get('id', '')

    # 句型進度
    progress_path = f'artifacts/{app_id}/users/{user_id}/sentence_progress'
    progress_docs = list(db.collection(progress_path).stream())
    progress = []
    for d in sorted(progress_docs, key=lambda x: x.to_dict().get('dataset_id', '')):
        data = d.to_dict()
        progress.append({
            'dataset_id': data.get('dataset_id', ''),
            'template': data.get('template_text', ''),
            'completion_count': data.get('completion_count', 0),
        })

    # Drill logs
    logs_path = f'artifacts/{app_id}/users/{user_id}/drill_logs'
    logs = list(db.collection(logs_path).stream())
    logs_sorted = sorted(logs, key=lambda x: x.to_dict().get('started_at', ''))

    drill_sessions = []
    for log in logs_sorted:
        data = log.to_dict()
        events = data.get('events', [])
        attempts = [e for e in events if e.get('type') == 'attempt']
        if not attempts:
            continue
        drill_sessions.append({
            'started_at': data.get('started_at', ''),
            'dataset_id': data.get('dataset_id', ''),
            'template': data.get('template', ''),
            'device': data.get('device', {}),
            'events': events,
        })

    result = {
        'name': student_name,
        'user_id': user_id,
        'plan': user_data.get('plan', 'free'),
        'tts_rate': user_data.get('tts_rate', 0.85),
        'ai_usage': user_data.get('ai_usage', {}),
        'practice_time': user_data.get('practice_time', {}),
        'progress': progress,
        'drill_sessions': drill_sessions,
    }

    firebase_admin.delete_app(app)
    return result, secrets


def print_raw_report(data):
    """印出原始資料報告"""
    print(f'=== 學生：{data["name"]}（{data["user_id"]}）===')
    print(f'方案：{data["plan"]}')
    print(f'語速：{data["tts_rate"]}')
    print()

    print(f'--- 句型進度（{len(data["progress"])} 句）---')
    for p in data['progress']:
        print(f'  [{p["dataset_id"]}] {p["template"][:50]}  完成 {p["completion_count"]} 輪')
    print()

    print(f'--- Drill Logs（{len(data["drill_sessions"])} 筆）---')
    for session in data['drill_sessions']:
        started = utc_to_tw(session['started_at'])
        dataset = session['dataset_id']
        template = session['template']
        ua_short = session['device'].get('ua', '')[:60]

        print(f'\n  [{started}] [{dataset}] {template}')
        print(f'  裝置：{ua_short}')

        for e in session['events']:
            t = e.get('type', '')
            detail = e.get('detail', '')
            ts = utc_to_tw(e.get('t', ''))[11:19]

            if t == 'attempt':
                try:
                    d = json.loads(detail)
                    ok = '✅' if d.get('ok') else '❌'
                    word = d.get('word', '')
                    tries = d.get('try', '')
                    transcript = d.get('transcript', '')[:60]
                    feedback = d.get('feedback', '')[:80]
                    print(f'    {ts} {ok} {word}（第{tries}次）：{transcript}')
                    if feedback:
                        print(f'           💡 {feedback}')
                except:
                    print(f'    {ts} attempt: {detail[:100]}')
            elif t in ('vad_init', 'drill_complete', 'gemini_error',
                       'gemini_quota', 'sr_disabled', 'audio_empty'):
                print(f'    {ts} [{t}] {detail[:80]}')

    ai_usage = data.get('ai_usage', {})
    if ai_usage:
        print('\n--- AI 使用量 ---')
        drill_count = ai_usage.get('drill_count', {})
        speech_tokens = ai_usage.get('speech', {})
        for date_key in sorted(set(list(drill_count.keys()) + list(speech_tokens.keys())), reverse=True):
            dc = drill_count.get(date_key, 0)
            st_ = speech_tokens.get(date_key, 0)
            print(f'  {date_key}：判讀 {dc} 次，tokens {st_}')

    practice_time = data.get('practice_time', {})
    if practice_time:
        print('\n--- 練習時間 ---')
        for date_key in sorted(practice_time.keys(), reverse=True):
            secs = practice_time[date_key]
            mins = secs // 60
            print(f'  {date_key}：{mins} 分 {secs % 60} 秒')


def generate_ai_report(data, secrets):
    """用 Gemini 產出分析報告"""
    api_key = secrets.get('GEMINI_API_KEY', '')
    if not api_key:
        print('錯誤：secrets 中沒有 GEMINI_API_KEY')
        return

    # 整理摘要資料給 Gemini（避免 token 爆量）
    summary_lines = []
    summary_lines.append(f'學生：{data["name"]}（{data["user_id"]}），方案：{data["plan"]}，語速：{data["tts_rate"]}')

    total_attempts = 0
    first_try_pass = 0
    word_tries = {}  # word -> max tries

    for session in data['drill_sessions']:
        template = session['template']
        started = utc_to_tw(session['started_at'])
        session_lines = [f'\n[{started}] {template}']

        for e in session['events']:
            if e.get('type') != 'attempt':
                continue
            try:
                d = json.loads(e['detail'])
                word = d.get('word', '')
                ok = d.get('ok', False)
                tries = d.get('try', 0)
                transcript = d.get('transcript', '')[:80]
                feedback = d.get('feedback', '')[:120]

                total_attempts += 1
                if ok and tries == 1:
                    first_try_pass += 1
                if ok:
                    word_tries[word] = max(word_tries.get(word, 0), tries)

                mark = '✅' if ok else '❌'
                session_lines.append(f'  {mark} {word}（第{tries}次）：{transcript}')
                if feedback:
                    session_lines.append(f'    💡 {feedback}')
            except:
                pass

        summary_lines.extend(session_lines)

    practice_time = data.get('practice_time', {})
    if practice_time:
        summary_lines.append('\n練習時間：')
        for date_key in sorted(practice_time.keys(), reverse=True):
            secs = practice_time[date_key]
            summary_lines.append(f'  {date_key}：{secs // 60} 分 {secs % 60} 秒')

    raw_data = '\n'.join(summary_lines)

    prompt = f"""你是一位英語教學專家，正在分析一位台灣國中小學生的英語口說練習紀錄。
請根據以下原始資料，產出一份給老師看的繁體中文分析報告。

報告格式要求：
1. **整體統計**：總嘗試次數、一次過關率、練習時長
2. **苦戰單字排行**：列出花最多次才通過的單字（≥3次），說明卡關原因
3. **發音弱點分析**：從 AI 回饋中歸納出 2-4 個系統性發音問題，附具體例子
4. **明確強項**：哪些類型的句子/單字表現好
5. **第 2 輪 vs 第 1 輪進步觀察**：有重複練習的句型，比較兩輪表現差異
6. **建議重點練習**：用表格列出優先級、目標音、代表字、練習建議
7. **總評**：2-3 句總結這位學生的學習態度和下一步方向

注意：
- 這是給老師的報告，語氣專業但親切
- 用 Markdown 格式
- 從 transcript 和 feedback 中找出真實的發音模式，不要泛泛而談

以下是學生的練習原始資料：

{raw_data}"""

    url = f'https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}'
    headers = {
        'Content-Type': 'application/json',
        'Referer': 'https://flashcard-techeasy.streamlit.app/',
    }
    payload = {
        'contents': [{'parts': [{'text': prompt}]}],
    }

    print('正在用 Gemini 產生分析報告...\n')
    res = requests.post(url, json=payload, headers=headers, timeout=300)
    if res.status_code != 200:
        print(f'Gemini API 錯誤：{res.status_code} {res.text[:200]}')
        return

    result = res.json()
    text = result['candidates'][0]['content']['parts'][0]['text']
    tokens = result.get('usageMetadata', {}).get('totalTokenCount', '?')
    print(text)
    print(f'\n---\n（Gemini tokens: {tokens}）')


def get_student_report(student_name, use_ai=False):
    data, secrets = collect_student_data(student_name)
    if not data:
        print(f'找不到學生「{student_name}」')
        return

    print_raw_report(data)

    if use_ai:
        print('\n' + '=' * 60)
        print('📊 AI 分析報告')
        print('=' * 60 + '\n')
        generate_ai_report(data, secrets)

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('用法：python student_report.py <學生名稱> [--ai]')
        sys.exit(1)
    use_ai = '--ai' in sys.argv
    name = [a for a in sys.argv[1:] if a != '--ai'][0]
    get_student_report(name, use_ai=use_ai)
