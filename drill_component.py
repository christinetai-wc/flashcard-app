"""
句型口說練習 JS 元件
全程由 JS 控制：TTS → 錄音 → VAD 靜音偵測 → Gemini API → 回饋 → Firestore 寫入
"""
import json
import streamlit as st
import google.auth.transport.requests


def _get_firestore_token():
    """從 service account credentials 取得短期 access token"""
    creds_info = st.secrets["firebase_credentials"]
    from google.oauth2 import service_account
    scoped = service_account.Credentials.from_service_account_info(
        creds_info,
        scopes=["https://www.googleapis.com/auth/datastore"],
    )
    scoped.refresh(google.auth.transport.requests.Request())
    return scoped.token, creds_info.get("project_id", "")


def generate_drill_html(template, options, completion_count, api_key, api_url,
                        template_hash, dataset_id, firestore_doc_path,
                        completed_options=None, user_doc_path=None,
                        drill_remaining=-1):
    """產生句型口說練習的完整 HTML/JS/CSS 元件

    firestore_doc_path: e.g. "artifacts/flashcard-pro-v1/users/xxx/sentence_progress/abc123"
    completed_options: 已完成的選項列表，用於續練（中途離開後回來跳過已完成的）
    user_doc_path: e.g. "artifacts/flashcard-pro-v1/public/data/users/xxx" 用於記錄 AI token 使用量
    drill_remaining: 免費用戶今日剩餘 AI 判讀次數，-1 表示無限（Premium）
    """
    token, project_id = _get_firestore_token()

    config = json.dumps({
        "template": template,
        "options": options,
        "completionCount": completion_count,
        "completedOptions": list(completed_options) if completed_options else [],
        "apiKey": api_key,
        "apiUrl": api_url,
        "templateHash": template_hash,
        "datasetId": dataset_id,
        "silenceThreshold": 12,
        "silenceDuration": 1800,
        "ttsRate": 0.85,
        "firestoreToken": token,
        "firestoreProject": project_id,
        "firestoreDocPath": firestore_doc_path,
        "firestoreUserDocPath": user_doc_path or "",
        "drillRemaining": drill_remaining,
    }, ensure_ascii=False)

    return f"""
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background:transparent; }}
body.dark {{ color:#e0e0e0; }}
body.light {{ color:#1a1a1a; }}

#drill-app {{ max-width:100%; padding:8px 4px; }}

.drill-header {{ margin-bottom:12px; }}
.drill-template {{ font-size:1.3rem; font-weight:600; margin:8px 0; }}
.drill-round {{ font-size:0.85rem; opacity:0.6; margin-bottom:8px; }}

.drill-options {{ display:flex; flex-wrap:wrap; gap:8px; margin:10px 0; }}
.opt {{ padding:8px 14px; border-radius:8px; font-size:0.9rem; transition:all 0.3s; }}

/* Light mode */
body.light .opt-pending {{ background:rgba(28,131,225,0.1); color:#0e4da4; }}
body.light .opt-active {{ background:rgba(255,165,0,0.2); color:#cc7000; border:2px solid #ffa500; font-weight:600; }}
body.light .opt-done {{ background:rgba(33,195,84,0.15); color:#0d6832; }}
body.light .drill-sentence {{ color:#333; }}
body.light .drill-status {{ color:#666; }}
body.light .feedback-transcript {{ color:#555; }}
body.light .feedback-text {{ color:#333; }}
body.light .history-item {{ background:rgba(0,0,0,0.03); }}
body.light .summary-row {{ border-bottom-color:#eee; }}

/* Dark mode */
body.dark .opt-pending {{ background:rgba(80,160,255,0.15); color:#7db8ff; }}
body.dark .opt-active {{ background:rgba(255,180,50,0.2); color:#ffb84d; border:2px solid #ffa500; font-weight:600; }}
body.dark .opt-done {{ background:rgba(80,220,120,0.15); color:#6ddb8a; }}
body.dark .drill-sentence {{ color:#ddd; }}
body.dark .drill-status {{ color:#aaa; }}
body.dark .feedback-transcript {{ color:#bbb; }}
body.dark .feedback-text {{ color:#ddd; }}
body.dark .history-item {{ background:rgba(255,255,255,0.05); }}
body.dark .summary-row {{ border-bottom-color:#444; }}

.drill-main {{ text-align:center; padding:16px 0; min-height:120px; }}
.drill-sentence {{ font-size:1.2rem; margin:8px 0; font-weight:500; }}
.drill-status {{ font-size:1rem; margin:8px 0; min-height:28px; }}

.vol-bar-container {{ display:flex; justify-content:center; align-items:center; gap:2px; height:30px; margin:8px 0; }}
.vol-bar {{ width:4px; background:#555; border-radius:2px; transition:height 0.05s; }}
.vol-bar.active {{ background:#4CAF50; }}

.drill-feedback {{ margin:10px 0; padding:12px; border-radius:10px; font-size:0.9rem; line-height:1.5; display:none; }}
.feedback-correct {{ background:rgba(33,195,84,0.1); border:1px solid rgba(33,195,84,0.3); }}
.feedback-wrong {{ background:rgba(255,100,100,0.1); border:1px solid rgba(255,100,100,0.3); }}

.drill-history {{ margin:10px 0; max-height:200px; overflow-y:auto; }}
.history-item {{ padding:8px 12px; margin:4px 0; border-radius:8px; font-size:0.85rem; border-left:3px solid #ccc; }}
.history-item.correct {{ border-left-color:#4CAF50; }}
.history-item.wrong {{ border-left-color:#f44336; }}

.drill-start-btn {{
    display:inline-block; padding:14px 36px; font-size:1.1rem; font-weight:600;
    background:linear-gradient(135deg,#667eea,#764ba2); color:#fff;
    border:none; border-radius:12px; cursor:pointer; transition:all 0.2s;
    box-shadow:0 4px 15px rgba(102,126,234,0.4);
}}
.drill-start-btn:hover {{ transform:translateY(-2px); box-shadow:0 6px 20px rgba(102,126,234,0.5); }}
.drill-start-btn:disabled {{ opacity:0.5; cursor:not-allowed; transform:none; }}

.drill-complete {{ text-align:center; padding:20px; }}
.drill-complete h2 {{ margin:8px 0; }}
.drill-complete .stars {{ font-size:2rem; margin:8px 0; }}
.drill-summary {{ text-align:left; margin:12px auto; max-width:400px; }}
.summary-row {{ display:flex; justify-content:space-between; padding:6px 0; font-size:0.9rem; }}
</style>

<div id="drill-app">
    <div class="drill-header">
        <div class="drill-round" id="drill-round"></div>
        <div class="drill-template" id="drill-template"></div>
    </div>
    <div class="drill-options" id="drill-options"></div>
    <div class="drill-main">
        <div class="drill-sentence" id="drill-sentence"></div>
        <div class="drill-status" id="drill-status">按下開始，AI 會帶你逐句練習</div>
        <div class="vol-bar-container" id="vol-bars" style="display:none;"></div>
    </div>
    <div class="drill-feedback" id="drill-feedback"></div>
    <div class="drill-history" id="drill-history"></div>
    <div style="text-align:center; margin:12px 0;">
        <button class="drill-start-btn" id="start-btn">🎯 開始練習</button>
    </div>
    <div id="drill-complete" class="drill-complete" style="display:none;"></div>
</div>

<script>
(function() {{
    // === 偵測深色模式 ===
    try {{
        const bg = getComputedStyle(window.parent.document.body).backgroundColor;
        const m = bg.match(/\\d+/g);
        if (m) {{
            const avg = (parseInt(m[0]) + parseInt(m[1]) + parseInt(m[2])) / 3;
            document.body.className = avg < 128 ? 'dark' : 'light';
        }} else {{
            document.body.className = 'dark';
        }}
    }} catch(e) {{
        document.body.className = 'dark';
    }}

    const CFG = {config};
    const STARS = (n) => n >= 5 ? '⭐⭐⭐' : n >= 3 ? '⭐⭐' : n >= 1 ? '⭐' : '';
    const $ = id => document.getElementById(id);

    let S = {{
        optIdx: 0, phase: 'idle', tries: {{}}, results: {{}},
        stream: null, analyser: null, audioCtx: null, history: [],
        drillUsed: 0,  // 本次 session 已用的 AI 判讀次數
    }};

    // === UI ===
    function renderOptions() {{
        $('drill-options').innerHTML = CFG.options.map((opt, i) => {{
            let cls = 'opt ';
            if (S.results[opt]) cls += 'opt-done';
            else if (i === S.optIdx && S.phase !== 'idle' && S.phase !== 'done') cls += 'opt-active';
            else cls += 'opt-pending';
            const tries = S.tries[opt] || 0;
            const icon = S.results[opt] ? '✅' : (i === S.optIdx && S.phase !== 'idle' ? '🎯' : '○');
            const triesText = tries > 0 ? ` (${{tries}})` : '';
            return `<span class="${{cls}}">${{icon}} ${{opt}}${{triesText}}</span>`;
        }}).join('');
    }}

    function setStatus(text) {{ $('drill-status').textContent = text; }}

    function showSentence(word) {{
        if (word) {{
            const s = CFG.template.replace('___', word);
            $('drill-sentence').innerHTML = s.replace(word, `<b style="color:#ff9800">${{word}}</b>`);
        }} else {{ $('drill-sentence').textContent = ''; }}
    }}

    function showFeedback(word, result) {{
        const el = $('drill-feedback');
        el.style.display = 'block';
        el.className = 'drill-feedback ' + (result.is_correct ? 'feedback-correct' : 'feedback-wrong');
        el.innerHTML = `
            <div class="feedback-transcript">${{result.is_correct ? '✅' : '❌'}} 聽到：${{result.transcript || '(無法辨識)'}}</div>
            <div class="feedback-text">💡 ${{result.feedback || ''}}</div>
        `;
        S.history.push({{ word, ...result }});
    }}

    function hideFeedback() {{ $('drill-feedback').style.display = 'none'; }}

    function renderHistory() {{
        $('drill-history').innerHTML = S.history.map(h => {{
            const cls = h.is_correct ? 'correct' : 'wrong';
            return `<div class="history-item ${{cls}}">
                ${{h.is_correct ? '✅' : '❌'}} <b>${{h.word}}</b>：${{h.transcript || ''}}
                <br><span style="opacity:0.7">💡 ${{h.feedback || ''}}</span>
            </div>`;
        }}).reverse().join('');
    }}

    function initVolBars() {{
        $('vol-bars').innerHTML = Array(20).fill('<div class="vol-bar" style="height:4px;"></div>').join('');
        $('vol-bars').style.display = 'flex';
    }}

    function updateVolBars(volume) {{
        const bars = $('vol-bars').children;
        for (let i = 0; i < bars.length; i++) {{
            const threshold = (i / bars.length) * 50;
            if (volume > threshold) {{
                bars[i].style.height = Math.min(30, 4 + volume * 0.5) + 'px';
                bars[i].classList.add('active');
            }} else {{
                bars[i].style.height = '4px';
                bars[i].classList.remove('active');
            }}
        }}
    }}

    // === AUDIO ===
    async function initAudio() {{
        S.stream = await navigator.mediaDevices.getUserMedia({{ audio: true }});
        S.audioCtx = new (window.AudioContext || window.webkitAudioContext)();
        S.analyser = S.audioCtx.createAnalyser();
        S.analyser.fftSize = 256;
        S.audioCtx.createMediaStreamSource(S.stream).connect(S.analyser);
    }}

    function getVolume() {{
        const data = new Uint8Array(S.analyser.frequencyBinCount);
        S.analyser.getByteFrequencyData(data);
        return data.reduce((a, b) => a + b, 0) / data.length;
    }}

    // 偵測環境底噪（取 0.5 秒平均值）
    function measureNoiseFloor() {{
        return new Promise(resolve => {{
            const samples = [];
            const measure = () => {{
                samples.push(getVolume());
                if (samples.length < 10) {{ setTimeout(measure, 50); return; }}  // 10 次 × 50ms = 0.5 秒
                const avg = samples.reduce((a, b) => a + b, 0) / samples.length;
                resolve(avg);
            }};
            measure();
        }});
    }}

    function recordUntilSilence() {{
        return new Promise(async (resolve) => {{
            // 動態門檻：底噪 × 1.5，至少 CFG.silenceThreshold
            const noiseFloor = await measureNoiseFloor();
            const threshold = Math.max(CFG.silenceThreshold, noiseFloor * 1.5);
            console.log('[VAD] noise floor:', noiseFloor.toFixed(1), 'threshold:', threshold.toFixed(1));

            const chunks = [];
            let mimeType = 'audio/webm';
            if (!MediaRecorder.isTypeSupported(mimeType)) {{
                mimeType = 'audio/mp4';
                if (!MediaRecorder.isTypeSupported(mimeType)) mimeType = '';
            }}
            const rec = new MediaRecorder(S.stream, mimeType ? {{ mimeType }} : {{}});
            rec.ondataavailable = e => {{ if (e.data.size > 0) chunks.push(e.data); }};
            rec.onstop = () => resolve(new Blob(chunks, {{ type: mimeType || 'audio/webm' }}));
            rec.start(100);

            const MAX_RECORD_MS = 10000;  // 最長錄音 10 秒
            let silenceStart = null, speechDetected = false, elapsed = 0;
            const check = () => {{
                if (rec.state !== 'recording') return;
                elapsed += 50;
                const vol = getVolume();
                updateVolBars(vol);
                if (vol > threshold) {{ speechDetected = true; silenceStart = null; }}
                else if (speechDetected) {{
                    if (!silenceStart) silenceStart = Date.now();
                    else if (Date.now() - silenceStart > CFG.silenceDuration) {{ rec.stop(); return; }}
                }}
                // 超時保底：未說話 15 秒 或 錄音達 10 秒
                if (!speechDetected && elapsed > 15000) {{ rec.stop(); return; }}
                if (elapsed > MAX_RECORD_MS) {{ rec.stop(); return; }}
                setTimeout(check, 50);
            }};
            setTimeout(check, 500);
        }});
    }}

    // === TTS ===
    function playTTS(text) {{
        return new Promise(resolve => {{
            const syn = window.parent.speechSynthesis || window.speechSynthesis;
            if (!syn) {{ resolve(); return; }}
            syn.cancel();
            const u = new SpeechSynthesisUtterance(text);
            u.lang = 'en-US'; u.rate = CFG.ttsRate;
            u.onend = () => setTimeout(resolve, 300);
            u.onerror = () => resolve();
            syn.speak(u);
        }});
    }}

    // === AI 判讀（多模型降級 + 語音辨識 fallback） ===
    const API_BASE = 'https://generativelanguage.googleapis.com/v1beta/models/';
    const MODELS = ['gemini-2.5-flash', 'gemini-2.0-flash'];
    let modelIdx = 0; // 目前使用的模型索引，429 時往下降級

    function blobToBase64(blob) {{
        return new Promise(resolve => {{
            const reader = new FileReader();
            reader.onloadend = () => resolve(reader.result.split(',')[1]);
            reader.readAsDataURL(blob);
        }});
    }}

    function buildPrompt(sentence, targetWord) {{
        return `Context: English pronunciation practice for non-native speakers.
The student is practicing: "${{sentence}}"
Target word in the blank: "${{targetWord}}"

Listen to the audio and evaluate.
These are young non-native learners, so be encouraging but fair.
Rules:
- The student MUST attempt the FULL sentence, not just the target word alone
- Accept ANY article substitution (a/an/the/omitted)
- Accept tense variations (is/was/are)
- Accept imperfect pronunciation as long as words are identifiable
- Mark INCORRECT if: only said the target word without sentence structure, or skipped major parts of the sentence
- Mark CORRECT if: the target word is recognizable AND the student attempted most of the sentence structure

Pronunciation Feedback (Traditional Chinese, 1-2 lines):
- Point out any mispronounced words with correct pronunciation
- Note missing/substituted words briefly
- If good, give brief praise AND one tip for sounding more natural

Return JSON:
{{"is_correct": true, "transcript": "what you heard", "feedback": "feedback in Traditional Chinese"}}`;
    }}

    async function callGeminiWithModel(model, base64, mimeType, prompt) {{
        const url = API_BASE + model + ':generateContent?key=' + CFG.apiKey;
        const res = await fetch(url, {{
            method: 'POST',
            headers: {{ 'Content-Type': 'application/json' }},
            body: JSON.stringify({{
                contents: [{{ parts: [
                    {{ text: prompt }},
                    {{ inline_data: {{ mime_type: mimeType, data: base64 }} }}
                ] }}],
                generationConfig: {{ responseMimeType: 'application/json' }}
            }})
        }});
        if (res.status === 429 || res.status === 404) return 'quota';
        if (!res.ok) return null;
        const data = await res.json();
        const tokenCount = data.usageMetadata?.totalTokenCount || 0;
        let text = data.candidates[0].content.parts[0].text;
        if (text.includes('```json')) text = text.split('```json')[1].split('```')[0];
        else if (text.includes('```')) text = text.split('```')[1].split('```')[0];
        const parsed = JSON.parse(text.trim());
        parsed._tokenCount = tokenCount;
        return parsed;
    }}

    // 用同一段錄音依序嘗試：2.5 → 2.0 → 1.5 → 語音辨識
    async function evaluate(audioBlob, targetWord, srTranscripts) {{
        const sentence = CFG.template.replace('___', targetWord);

        // 免費用戶每日額度檢查（drillRemaining == -1 表示 Premium 無限）
        const remaining = CFG.drillRemaining < 0 ? Infinity : CFG.drillRemaining - S.drillUsed;
        if (remaining <= 0) {{
            setStatus('🎙️ 今日 AI 額度已用完，語音比對中...');
            return textMatch(srTranscripts, targetWord, sentence);
        }}

        const base64 = await blobToBase64(audioBlob);
        const mimeType = audioBlob.type || 'audio/webm';
        const prompt = buildPrompt(sentence, targetWord);

        // 從目前的 modelIdx 開始嘗試
        for (let i = modelIdx; i < MODELS.length; i++) {{
            const model = MODELS[i];
            setStatus(`🤖 ${{model}} 分析中...`);
            try {{
                const result = await callGeminiWithModel(model, base64, mimeType, prompt);
                if (result === 'quota') {{
                    console.warn('[Drill] ' + model + ' quota exceeded, trying next...');
                    modelIdx = i + 1; // 之後直接跳過這個模型
                    continue;
                }}
                if (result) {{
                    // 記錄 token 使用量 + 判讀次數（合併一次寫入）
                    const tc = result._tokenCount || 0;
                    delete result._tokenCount;
                    S.drillUsed++;
                    recordUsageToFirestore(tc);
                    return result;
                }}
            }} catch(e) {{
                console.warn('[Drill] ' + model + ' error:', e.message);
                return {{ is_correct: false, transcript: '', feedback: '分析失敗：' + e.message }};
            }}
        }}

        // 所有 Gemini 模型都不可用 → 語音辨識 fallback
        setStatus('🎙️ 語音比對中...');
        return textMatch(srTranscripts, targetWord, sentence);
    }}

    // === 瀏覽器語音辨識（錄音時同步執行） ===
    function speechRecognize() {{
        return new Promise(resolve => {{
            const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
            if (!SR) {{ resolve(null); return; }}
            const rec = new SR();
            rec.lang = 'en-US';
            rec.interimResults = false;
            rec.maxAlternatives = 3;
            let resolved = false;
            rec.onresult = (e) => {{
                if (resolved) return;
                resolved = true;
                const results = [];
                for (let i = 0; i < e.results[0].length; i++) {{
                    results.push(e.results[0][i].transcript.toLowerCase().trim());
                }}
                resolve(results);
            }};
            rec.onerror = () => {{ if (!resolved) {{ resolved = true; resolve(null); }} }};
            rec.onend = () => {{ if (!resolved) {{ resolved = true; resolve(null); }} }};
            setTimeout(() => {{ if (!resolved) {{ resolved = true; try {{ rec.stop(); }} catch(e) {{}} resolve(null); }} }}, 12000);
            rec.start();
        }});
    }}

    function textMatch(transcripts, targetWord, sentence) {{
        if (!transcripts || transcripts.length === 0) {{
            return {{ is_correct: false, transcript: '', feedback: '無法辨識語音，請再試一次。' }};
        }}
        const target = targetWord.toLowerCase();
        const stopWords = new Set(['a','an','the','is','are','was','were','very','this','that','it','to','of','in','on','for']);
        const keyWords = sentence.toLowerCase().split(/\\s+/).filter(w => w.length > 2 && !stopWords.has(w));

        for (const transcript of transcripts) {{
            const words = transcript.toLowerCase().split(/\\s+/);
            const hasTarget = words.some(w => w.includes(target) || target.includes(w));
            if (!hasTarget) continue;
            const matched = keyWords.filter(kw => words.some(w => w.includes(kw) || kw.includes(w)));
            if (matched.length >= Math.ceil(keyWords.length * 0.4)) {{
                return {{ is_correct: true, transcript, feedback: '（語音辨識模式）發音不錯，繼續加油！' }};
            }}
        }}
        return {{ is_correct: false, transcript: transcripts[0], feedback: '（語音辨識模式）請試著唸完整句子：' + sentence }};
    }}

    // === FIRESTORE REST API ===
    function fsUrl() {{
        return `https://firestore.googleapis.com/v1/projects/${{CFG.firestoreProject}}/databases/(default)/documents/${{CFG.firestoreDocPath}}`;
    }}

    async function fsRead() {{
        try {{
            const res = await fetch(fsUrl(), {{
                headers: {{ 'Authorization': 'Bearer ' + CFG.firestoreToken }}
            }});
            if (res.status === 404) return null;
            const doc = await res.json();
            return doc.fields || null;
        }} catch(e) {{ console.error('Firestore read error:', e); return null; }}
    }}

    function toFsValue(val) {{
        if (typeof val === 'string') return {{ stringValue: val }};
        if (typeof val === 'number') return Number.isInteger(val) ? {{ integerValue: String(val) }} : {{ doubleValue: val }};
        if (typeof val === 'boolean') return {{ booleanValue: val }};
        if (Array.isArray(val)) return {{ arrayValue: {{ values: val.map(toFsValue) }} }};
        if (val && typeof val === 'object') {{
            const fields = {{}};
            for (const [k, v] of Object.entries(val)) fields[k] = toFsValue(v);
            return {{ mapValue: {{ fields }} }};
        }}
        return {{ nullValue: null }};
    }}

    async function fsWrite(data) {{
        const fields = {{}};
        for (const [k, v] of Object.entries(data)) fields[k] = toFsValue(v);
        try {{
            await fetch(fsUrl() + '?updateMask.fieldPaths=' + Object.keys(data).join('&updateMask.fieldPaths='), {{
                method: 'PATCH',
                headers: {{
                    'Authorization': 'Bearer ' + CFG.firestoreToken,
                    'Content-Type': 'application/json',
                }},
                body: JSON.stringify({{ fields }})
            }});
        }} catch(e) {{ console.error('Firestore write error:', e); }}
    }}

    // 記錄 AI token 使用量 + 判讀次數（合併一次讀寫，避免互相覆蓋）
    async function recordUsageToFirestore(tokenCount) {{
        if (!CFG.firestoreUserDocPath) return;
        const today = new Date().toISOString().slice(0, 10);
        const userUrl = `https://firestore.googleapis.com/v1/projects/${{CFG.firestoreProject}}/databases/(default)/documents/${{CFG.firestoreUserDocPath}}`;
        try {{
            // 讀取現有 ai_usage
            const res = await fetch(userUrl, {{
                headers: {{ 'Authorization': 'Bearer ' + CFG.firestoreToken }}
            }});
            let speechVal = 0, drillVal = 0;
            if (res.ok) {{
                const doc = await res.json();
                const usageFields = doc.fields?.ai_usage?.mapValue?.fields;
                const speechMap = usageFields?.speech?.mapValue?.fields;
                const drillMap = usageFields?.drill_count?.mapValue?.fields;
                if (speechMap?.[today]) speechVal = parseInt(speechMap[today].integerValue || '0');
                if (drillMap?.[today]) drillVal = parseInt(drillMap[today].integerValue || '0');
            }}
            // 組裝更新：speech token + drill_count
            const usageFields = {{}};
            if (tokenCount > 0) {{
                usageFields.speech = {{ mapValue: {{ fields: {{
                    [today]: {{ integerValue: String(speechVal + tokenCount) }}
                }} }} }};
            }}
            usageFields.drill_count = {{ mapValue: {{ fields: {{
                [today]: {{ integerValue: String(drillVal + 1) }}
            }} }} }};
            const fields = {{
                ai_usage: {{ mapValue: {{ fields: usageFields }} }}
            }};
            await fetch(userUrl + '?updateMask.fieldPaths=ai_usage', {{
                method: 'PATCH',
                headers: {{
                    'Authorization': 'Bearer ' + CFG.firestoreToken,
                    'Content-Type': 'application/json',
                }},
                body: JSON.stringify({{ fields }})
            }});
        }} catch(e) {{ console.warn('Usage record error:', e); }}
    }}

    // 每完成一個 option 就即時寫入，中途離開不丟進度
    async function saveOptionToFirestore(word) {{
        const doneList = CFG.options.filter(o => S.results[o]);
        await fsWrite({{
            template_text: CFG.template,
            completed_options: doneList,
            dataset_id: CFG.datasetId,
        }});
    }}

    // 全部完成後：completion_count +1，completed_options 重置，寫入 round 紀錄
    async function saveRoundToFirestore() {{
        const existing = await fsRead();
        const currentCount = existing?.completion_count?.integerValue
            ? parseInt(existing.completion_count.integerValue) : (CFG.completionCount || 0);
        const newCount = currentCount + 1;

        const roundData = {{
            round: newCount,
            timestamp: new Date().toISOString(),
            results: S.results,
        }};

        const existingRounds = [];
        if (existing?.rounds?.arrayValue?.values) {{
            for (const v of existing.rounds.arrayValue.values) {{
                existingRounds.push(parseFsValue(v));
            }}
        }}
        existingRounds.push(roundData);

        await fsWrite({{
            template_text: CFG.template,
            completed_options: [],  // 重置，準備下一輪
            completion_count: newCount,
            dataset_id: CFG.datasetId,
            rounds: existingRounds,
        }});

        return newCount;
    }}

    function parseFsValue(v) {{
        if (v.stringValue !== undefined) return v.stringValue;
        if (v.integerValue !== undefined) return parseInt(v.integerValue);
        if (v.doubleValue !== undefined) return v.doubleValue;
        if (v.booleanValue !== undefined) return v.booleanValue;
        if (v.nullValue !== undefined) return null;
        if (v.arrayValue) return (v.arrayValue.values || []).map(parseFsValue);
        if (v.mapValue) {{
            const obj = {{}};
            for (const [k, fv] of Object.entries(v.mapValue.fields || {{}})) obj[k] = parseFsValue(fv);
            return obj;
        }}
        return null;
    }}

    // === DRILL FLOW ===
    async function drillOneOption(word) {{
        const sentence = CFG.template.replace('___', word);
        S.tries[word] = 0;

        while (!S.results[word]) {{
            S.tries[word]++;
            renderOptions();
            hideFeedback();

            setStatus('🔊 聽示範...');
            showSentence(word);
            await playTTS(sentence);

            setStatus('🎤 請跟著唸...');

            // 同時啟動語音辨識（作為 fallback 備用）
            const srPromise = speechRecognize();
            const audioBlob = await recordUntilSilence();

            if (audioBlob.size < 1000) {{
                setStatus('😮 沒有偵測到聲音，再試一次...');
                await sleep(1500);
                continue;
            }}

            updateVolBars(0);
            const srTranscripts = await srPromise;

            // 預篩：SpeechRecognition 完全沒辨識到 → 不送 Gemini，省 token
            if (!srTranscripts || srTranscripts.length === 0 || srTranscripts.every(t => !t.trim())) {{
                showFeedback(word, {{ is_correct: false, transcript: '', feedback: '沒有偵測到語音內容，請對著麥克風清楚地唸出句子。' }});
                renderHistory();
                setStatus('再唸一次...');
                renderOptions();
                await sleep(2000);
                continue;
            }}

            const result = await evaluate(audioBlob, word, srTranscripts);
            showFeedback(word, result);
            renderHistory();

            if (result.is_correct) {{
                S.results[word] = {{
                    tries: S.tries[word],
                    transcript: result.transcript || '',
                    feedback: result.feedback || ''
                }};
                setStatus(`✅ ${{word}} — 通過！`);
                // 即時存入 Firestore，中途離開不丟進度
                try {{ await saveOptionToFirestore(word); }} catch(e) {{ console.warn('Save option error:', e); }}
            }} else {{
                setStatus(`再唸一次 ${{word}}...`);
            }}
            renderOptions();
            await sleep(2000);
        }}
    }}

    async function startDrill() {{
        $('start-btn').disabled = true;
        $('start-btn').style.display = 'none';
        S.phase = 'running';
        S.results = {{}};  S.tries = {{}};  S.history = [];
        $('drill-history').innerHTML = '';

        // 標記已完成的 option（續練時跳過）
        const done = new Set(CFG.completedOptions || []);
        for (const opt of CFG.options) {{
            if (done.has(opt)) {{
                S.results[opt] = {{ tries: 0, transcript: '(已完成)', feedback: '' }};
            }}
        }}

        // 找出還沒練的 option
        const remaining = CFG.options.filter(o => !done.has(o));
        if (remaining.length === 0) {{
            // 全部已完成（新一輪），重新練全部
            S.results = {{}};
            for (const opt of CFG.options) delete S.results[opt];
        }}

        renderOptions();

        try {{
            await initAudio();
            initVolBars();
        }} catch (e) {{
            setStatus('❌ 無法存取麥克風，請允許麥克風權限後重試');
            $('start-btn').disabled = false;
            $('start-btn').style.display = 'inline-block';
            return;
        }}

        const toDrill = remaining.length > 0 ? remaining : CFG.options;
        for (let i = 0; i < CFG.options.length; i++) {{
            if (S.results[CFG.options[i]]) continue;  // 跳過已完成
            S.optIdx = i;
            await drillOneOption(CFG.options[i]);
        }}

        S.phase = 'done';
        await completeDrill();
    }}

    async function completeDrill() {{
        $('vol-bars').style.display = 'none';
        showSentence(null);
        hideFeedback();

        setStatus('📝 儲存成績中...');
        let newCount;
        try {{
            newCount = await saveRoundToFirestore();
        }} catch(e) {{
            console.error('Save error:', e);
            newCount = CFG.completionCount + 1;
            setStatus('⚠️ 儲存失敗，但練習已完成');
        }}

        const stars = STARS(newCount);
        const nextStar = newCount < 1 ? 1 : newCount < 3 ? 3 : newCount < 5 ? 5 : null;
        const nextMsg = nextStar ? `（再 ${{nextStar - newCount}} 輪升級 ${{STARS(nextStar)}}）` : '🏆 已達最高等級！';

        let summaryHtml = '';
        for (const opt of CFG.options) {{
            const r = S.results[opt];
            summaryHtml += `<div class="summary-row">
                <span>✅ ${{opt}}</span>
                <span>${{r.tries === 1 ? '一次過關 🎉' : r.tries + ' 次通過'}}</span>
            </div>`;
        }}

        $('drill-complete').style.display = 'block';
        $('drill-complete').innerHTML = `
            <h2>🎉 本輪完成！</h2>
            <div class="stars">${{stars || '⭐'}}</div>
            <p>累計 ${{newCount}} 輪 ${{nextMsg}}</p>
            <div class="drill-summary">${{summaryHtml}}</div>
        `;
        setStatus('✅ 成績已儲存');

        if (S.stream) S.stream.getTracks().forEach(t => t.stop());
    }}

    function sleep(ms) {{ return new Promise(r => setTimeout(r, ms)); }}

    // === INIT ===
    const doneCount = (CFG.completedOptions || []).length;
    const totalCount = CFG.options.length;
    if (CFG.completionCount > 0 || doneCount > 0) {{
        let info = CFG.completionCount > 0 ? `${{STARS(CFG.completionCount)}} 已完成 ${{CFG.completionCount}} 輪` : '';
        if (doneCount > 0 && doneCount < totalCount) {{
            info += (info ? '　' : '') + `（本輪進度 ${{doneCount}}/${{totalCount}}）`;
        }}
        $('drill-round').textContent = info || '尚未練習';
    }} else {{
        $('drill-round').textContent = '尚未練習';
    }}

    // 初始畫面標記已完成的 option
    const initDone = new Set(CFG.completedOptions || []);
    for (const opt of CFG.options) {{
        if (initDone.has(opt)) S.results[opt] = {{ tries: 0, transcript: '(已完成)', feedback: '' }};
    }}

    $('drill-template').textContent = CFG.template;
    renderOptions();
    $('start-btn').textContent = doneCount > 0 && doneCount < totalCount ? '🎯 繼續練習' : '🎯 開始練習';
    $('start-btn').onclick = startDrill;

}})();
</script>
</body>
</html>
"""
