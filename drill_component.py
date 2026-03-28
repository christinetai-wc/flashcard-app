"""
句型口說練習 JS 元件
全程由 JS 控制：TTS → 錄音 → VAD 靜音偵測 → Cloud Function Proxy → 回饋 → Firestore 寫入
"""
import json
import hmac
import hashlib
import time
import streamlit as st
import google.auth.transport.requests


def _generate_proxy_token():
    """產生 HMAC token 供 Cloud Function 驗證（有效期 1 小時，與 Firestore token 同步）"""
    secret = st.secrets.get("PROXY_SECRET", "")
    timestamp = str(int(time.time()))
    signature = hmac.new(secret.encode(), timestamp.encode(), hashlib.sha256).hexdigest()
    return f"{timestamp}.{signature}"


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


def generate_drill_html(template, options, completion_count, proxy_url,
                        template_hash, dataset_id, firestore_doc_path,
                        completed_options=None, user_doc_path=None,
                        drill_remaining=-1,
                        dataset_name="", total_sentences=0,
                        tts_rate=0.85):
    """產生句型口說練習的完整 HTML/JS/CSS 元件

    firestore_doc_path: e.g. "artifacts/flashcard-pro-v1/users/xxx/sentence_progress/abc123"
    completed_options: 已完成的選項列表，用於續練（中途離開後回來跳過已完成的）
    user_doc_path: e.g. "artifacts/flashcard-pro-v1/public/data/users/xxx" 用於記錄 AI token 使用量
    drill_remaining: 免費用戶今日剩餘 AI 判讀次數，-1 表示無限（Premium）
    """
    token, project_id = _get_firestore_token()
    proxy_token = _generate_proxy_token()

    config = json.dumps({
        "template": template,
        "options": options,
        "completionCount": completion_count,
        "completedOptions": list(completed_options) if completed_options else [],
        "proxyUrl": proxy_url,
        "proxyToken": proxy_token,
        "templateHash": template_hash,
        "datasetId": dataset_id,
        "silenceThreshold": 12,
        "silenceDuration": 1800,
        "ttsRate": tts_rate,
        "firestoreToken": token,
        "firestoreProject": project_id,
        "firestoreDocPath": firestore_doc_path,
        "firestoreUserDocPath": user_doc_path or "",
        "drillRemaining": drill_remaining,
        "datasetName": dataset_name,
        "totalSentences": total_sentences,
    }, ensure_ascii=False)

    return f"""
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
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
.done-btn {{ margin:8px auto; padding:6px 20px; border:1px solid #999; border-radius:18px; background:transparent; color:#666; font-size:0.85rem; cursor:pointer; }}
.done-btn:hover {{ background:rgba(0,0,0,0.05); }}
body.dark .done-btn {{ color:#aaa; border-color:#666; }}
body.dark .done-btn:hover {{ background:rgba(255,255,255,0.1); }}

.speed-control {{ display:inline-flex; align-items:center; gap:6px; margin:8px 0; }}
.speed-btn {{ padding:4px 10px; border:1px solid #999; border-radius:14px; background:transparent; color:#666; font-size:0.8rem; cursor:pointer; min-width:28px; text-align:center; }}
.speed-btn:hover {{ background:rgba(0,0,0,0.05); }}
.speed-btn.active {{ background:rgba(28,131,225,0.15); color:#0e4da4; border-color:#0e4da4; font-weight:600; }}
body.dark .speed-btn {{ color:#aaa; border-color:#666; }}
body.dark .speed-btn:hover {{ background:rgba(255,255,255,0.1); }}
body.dark .speed-btn.active {{ background:rgba(80,160,255,0.2); color:#7db8ff; border-color:#7db8ff; }}

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

/* 完成慶祝動畫 */
@keyframes celebrate {{ 0% {{ transform:scale(0.5); opacity:0; }} 50% {{ transform:scale(1.1); }} 100% {{ transform:scale(1); opacity:1; }} }}
.drill-complete.show {{ animation: celebrate 0.5s ease-out; }}
.confetti {{ position:fixed; top:-10px; font-size:1.5rem; animation: fall linear forwards; pointer-events:none; z-index:999; }}
@keyframes fall {{ to {{ top:100vh; opacity:0; }} }}

/* 手機適配 */
@media (max-width: 480px) {{
    .drill-template {{ font-size: 1.1rem; }}
    .drill-sentence {{ font-size: 1rem; }}
    .opt {{ padding: 6px 10px; font-size: 0.85rem; }}
    .drill-start-btn {{ padding: 12px 24px; font-size: 1rem; }}
    .drill-history {{ max-height: 150px; }}
    .drill-feedback {{ font-size: 0.85rem; padding: 10px; }}
    .history-item {{ font-size: 0.8rem; padding: 6px 10px; }}
}}
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
        <button class="done-btn" id="done-btn" style="display:none;">✋ 我說完了</button>
    </div>
    <div class="drill-history" id="drill-history"></div>
    <div style="text-align:center; margin:12px 0;">
        <div class="speed-control" id="speed-control">
            <span style="font-size:0.8rem; opacity:0.6;">🔊 語速</span>
            <button class="speed-btn" data-rate="0.5">慢</button>
            <button class="speed-btn" data-rate="0.85">中</button>
            <button class="speed-btn" data-rate="1.0">快</button>
        </div>
        <button class="drill-start-btn" id="start-btn">🎯 開始練習</button>
    </div>
    <div id="drill-complete" class="drill-complete" style="display:none;"></div>
</div>

<script>
(function() {{
    // === iOS iframe 權限修復 ===
    // Streamlit html() 的 iframe 沒有 allow="microphone" 屬性，手動補上
    try {{
        const frames = window.parent.document.querySelectorAll('iframe');
        frames.forEach(f => {{
            if (f.contentWindow === window) {{
                if (!f.getAttribute('allow') || !f.getAttribute('allow').includes('microphone')) {{
                    f.setAttribute('allow', 'microphone; autoplay');
                }}
            }}
        }});
    }} catch(e) {{ console.warn('Cannot set iframe allow attribute:', e); }}

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
        vadThreshold: CFG.silenceThreshold,  // VAD 門檻，startDrill 時偵測一次
    }};

    // === Error Log ===
    const _logSessionId = Date.now().toString();
    const _logEvents = [];
    const _logDevice = {{
        ua: navigator.userAgent,
        platform: navigator.platform || '',
        screen: `${{screen.width}}x${{screen.height}}`,
        sr: !!(window.SpeechRecognition || window.webkitSpeechRecognition),
    }};

    function logEvent(type, detail) {{
        _logEvents.push({{
            t: new Date().toISOString(),
            type,
            detail: typeof detail === 'object' ? JSON.stringify(detail) : String(detail || ''),
        }});
    }}

    async function flushLog() {{
        if (_logEvents.length === 0) return;
        // 從 firestoreDocPath 取得 user base path（前 4 段）
        const parts = CFG.firestoreDocPath.split('/');
        const basePath = parts.slice(0, 4).join('/');
        const logUrl = `https://firestore.googleapis.com/v1/projects/${{CFG.firestoreProject}}/databases/(default)/documents/${{basePath}}/drill_logs/${{_logSessionId}}`;
        const data = {{
            started_at: new Date(parseInt(_logSessionId)).toISOString(),
            device: _logDevice,
            template: CFG.template,
            dataset_id: CFG.datasetId,
            events: _logEvents,
        }};
        const fields = {{}};
        for (const [k, v] of Object.entries(data)) fields[k] = toFsValue(v);
        try {{
            await fetch(logUrl, {{
                method: 'PATCH',
                headers: {{
                    'Authorization': 'Bearer ' + CFG.firestoreToken,
                    'Content-Type': 'application/json',
                }},
                body: JSON.stringify({{ fields }})
            }});
        }} catch(e) {{ console.error('Log flush error:', e); }}
    }}

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
        S.history.push({{ word, ...result }});
    }}

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
        return new Promise((resolve) => {{
            const threshold = S.vadThreshold;
            const doneBtn = $('done-btn');

            const chunks = [];
            let mimeType = 'audio/webm';
            if (!MediaRecorder.isTypeSupported(mimeType)) {{
                mimeType = 'audio/mp4';
                if (!MediaRecorder.isTypeSupported(mimeType)) mimeType = '';
            }}
            const rec = new MediaRecorder(S.stream, mimeType ? {{ mimeType }} : {{}});
            rec.ondataavailable = e => {{ if (e.data.size > 0) chunks.push(e.data); }};
            let _speechDetected = false;
            rec.onstop = () => {{
                doneBtn.style.display = 'none';
                doneBtn.onclick = null;
                resolve({{ blob: new Blob(chunks, {{ type: mimeType || 'audio/webm' }}), speechDetected: _speechDetected }});
            }};
            rec.start(100);

            // 手動停止按鈕
            doneBtn.style.display = 'inline-block';
            doneBtn.onclick = () => {{ if (rec.state === 'recording') rec.stop(); }};

            const MAX_RECORD_MS = 10000;  // 最長錄音 10 秒
            let silenceStart = null, speechDetected = false, elapsed = 0, speechFrames = 0;
            const check = () => {{
                if (rec.state !== 'recording') return;
                elapsed += 50;
                const vol = getVolume();
                updateVolBars(vol);
                if (vol > threshold) {{
                    speechFrames++;
                    // 需要連續 3 幀（150ms）以上才算真正說話，避免 TTS 殘餘音誤觸發
                    if (speechFrames >= 3) {{ speechDetected = true; _speechDetected = true; }}
                    silenceStart = null;
                }} else {{
                    speechFrames = 0;
                    if (speechDetected) {{
                        if (!silenceStart) silenceStart = Date.now();
                        else if (Date.now() - silenceStart > CFG.silenceDuration) {{ rec.stop(); return; }}
                    }}
                }}
                // 超時保底：未說話 15 秒 或 錄音達 10 秒
                if (!speechDetected && elapsed > 15000) {{ rec.stop(); return; }}
                if (elapsed > MAX_RECORD_MS) {{ rec.stop(); return; }}
                setTimeout(check, 50);
            }};
            setTimeout(check, 500);
        }});
    }}

    // === TTS（iOS 相容） ===
    const _syn = window.parent.speechSynthesis || window.speechSynthesis;
    let _ttsUnlocked = false;

    // iOS Safari 要求 speechSynthesis 在使用者手勢中同步呼叫一次才能解鎖
    function unlockTTS() {{
        if (_ttsUnlocked || !_syn) return;
        const u = new SpeechSynthesisUtterance('');
        u.volume = 0;
        _syn.speak(u);
        _ttsUnlocked = true;
    }}

    function playTTS(text) {{
        return new Promise(resolve => {{
            if (!_syn) {{ resolve(); return; }}
            _syn.cancel();
            const u = new SpeechSynthesisUtterance(text);
            u.lang = 'en-US'; u.rate = CFG.ttsRate;
            u.onend = () => setTimeout(resolve, 300);
            u.onerror = () => resolve();
            // iOS 有時 onend 不觸發，加超時保底
            const timeout = setTimeout(() => resolve(), 8000);
            const origEnd = u.onend;
            u.onend = () => {{ clearTimeout(timeout); origEnd(); }};
            _syn.speak(u);
        }});
    }}

    // === AI 判讀（透過 Cloud Function proxy，多模型降級 + 語音辨識 fallback） ===
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
        const res = await fetch(CFG.proxyUrl, {{
            method: 'POST',
            headers: {{
                'Content-Type': 'application/json',
                'Authorization': 'Bearer ' + CFG.proxyToken,
            }},
            body: JSON.stringify({{
                model: model,
                contents: [{{ parts: [
                    {{ text: prompt }},
                    {{ inline_data: {{ mime_type: mimeType, data: base64 }} }}
                ] }}],
                generationConfig: {{ responseMimeType: 'application/json' }}
            }})
        }});
        if (res.status === 429 || res.status === 404 || res.status === 403) return 'quota';
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
                    logEvent('gemini_quota', model);
                    modelIdx = i + 1; // 之後直接跳過這個模型
                    continue;
                }}
                if (result) {{
                    // 記錄 token 使用量 + 判讀次數（合併一次寫入）
                    const tc = result._tokenCount || 0;
                    delete result._tokenCount;
                    S.drillUsed++;
                    await recordUsageToFirestore(tc);
                    return result;
                }}
            }} catch(e) {{
                console.warn('[Drill] ' + model + ' error:', e.message);
                logEvent('gemini_error', `${{model}}: ${{e.message}}`);
                return {{ is_correct: false, transcript: '', feedback: '分析失敗：' + e.message }};
            }}
        }}

        // 所有 Gemini 模型都不可用 → 語音辨識 fallback
        setStatus('🎙️ 語音比對中...');
        return textMatch(srTranscripts, targetWord, sentence);
    }}

    // === 瀏覽器語音辨識（錄音時同步執行） ===
    let _srAvailable = !!(window.SpeechRecognition || window.webkitSpeechRecognition);

    function speechRecognize() {{
        return new Promise(resolve => {{
            const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
            if (!SR) {{ _srAvailable = false; resolve(null); return; }}
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
            rec.onerror = (e) => {{ if (!resolved) {{ resolved = true; if (e.error === 'not-allowed') _srAvailable = false; resolve(null); }} }};
            rec.onend = () => {{ if (!resolved) {{ resolved = true; resolve(null); }} }};
            setTimeout(() => {{ if (!resolved) {{ resolved = true; try {{ rec.stop(); }} catch(e) {{}} resolve(null); }} }}, 12000);
            try {{ rec.start(); }} catch(e) {{ _srAvailable = false; resolved = true; resolve(null); }}
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

    async function fsReadCompletionCount() {{
        try {{
            const fields = await fsRead();
            if (fields?.completion_count?.integerValue) return parseInt(fields.completion_count.integerValue);
            return 0;
        }} catch(e) {{ return 0; }}
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

    // 記錄 AI token 使用量 + 判讀次數（使用 fieldTransforms.increment，原子操作）
    async function recordUsageToFirestore(tokenCount) {{
        if (!CFG.firestoreUserDocPath) return;
        const today = new Date().toISOString().slice(0, 10);
        const projectId = CFG.firestoreProject;
        const docPath = CFG.firestoreUserDocPath;
        const commitUrl = `https://firestore.googleapis.com/v1/projects/${{projectId}}/databases/(default)/documents:commit`;
        const transforms = [
            {{
                fieldPath: `ai_usage.drill_count.${{today}}`,
                increment: {{ integerValue: '1' }}
            }}
        ];
        if (tokenCount > 0) {{
            transforms.push({{
                fieldPath: `ai_usage.speech.${{today}}`,
                increment: {{ integerValue: String(tokenCount) }}
            }});
        }}
        try {{
            const res = await fetch(commitUrl, {{
                method: 'POST',
                headers: {{
                    'Authorization': 'Bearer ' + CFG.firestoreToken,
                    'Content-Type': 'application/json',
                }},
                body: JSON.stringify({{
                    writes: [{{
                        transform: {{
                            document: `projects/${{projectId}}/databases/(default)/documents/${{docPath}}`,
                            fieldTransforms: transforms
                        }}
                    }}]
                }})
            }});
            if (!res.ok) {{
                const errText = await res.text().catch(() => '');
                console.error('[ai_usage] write failed:', res.status, errText);
                logEvent('ai_usage_error', `${{res.status}} ${{errText.slice(0, 100)}}`);
            }}
        }} catch(e) {{
            console.warn('Usage record error:', e);
            logEvent('ai_usage_error', e.message || 'fetch_error');
        }}
    }}

    // 練習時間寫入 Firestore（使用 fieldTransforms.increment，原子操作）
    async function savePracticeTime(seconds) {{
        if (!CFG.firestoreUserDocPath || seconds <= 0) return;
        const today = new Date().toISOString().slice(0, 10);
        const projectId = CFG.firestoreProject;
        const docPath = CFG.firestoreUserDocPath;
        const commitUrl = `https://firestore.googleapis.com/v1/projects/${{projectId}}/databases/(default)/documents:commit`;
        try {{
            await fetch(commitUrl, {{
                method: 'POST',
                headers: {{
                    'Authorization': 'Bearer ' + CFG.firestoreToken,
                    'Content-Type': 'application/json',
                }},
                body: JSON.stringify({{
                    writes: [{{
                        transform: {{
                            document: `projects/${{projectId}}/databases/(default)/documents/${{docPath}}`,
                            fieldTransforms: [{{
                                fieldPath: `practice_time.${{today}}`,
                                increment: {{ integerValue: String(Math.round(seconds)) }}
                            }}]
                        }}
                    }}]
                }})
            }});
        }} catch(e) {{ console.warn('Practice time save error:', e); }}
    }}

    // 更新排行榜統計（sentence_stats）
    async function updateSentenceStats() {{
        if (!CFG.firestoreUserDocPath || !CFG.datasetId || !CFG.totalSentences) return;
        const projectId = CFG.firestoreProject;
        const docPath = CFG.firestoreUserDocPath;
        const userUrl = `https://firestore.googleapis.com/v1/projects/${{projectId}}/databases/(default)/documents/${{docPath}}`;
        const commitUrl = `https://firestore.googleapis.com/v1/projects/${{projectId}}/databases/(default)/documents:commit`;
        const now = new Date().toISOString();

        try {{
            // 先更新 name, total, last_active（這些可以覆蓋）
            const fields = {{
                sentence_stats: {{ mapValue: {{ fields: {{
                    [CFG.datasetId]: {{ mapValue: {{ fields: {{
                        name: {{ stringValue: CFG.datasetName || CFG.datasetId }},
                        total: {{ integerValue: String(CFG.totalSentences) }},
                        last_active: {{ stringValue: now }}
                    }} }} }}
                }} }} }}
            }};
            await fetch(userUrl + '?updateMask.fieldPaths=sentence_stats.' + CFG.datasetId + '.name&updateMask.fieldPaths=sentence_stats.' + CFG.datasetId + '.total&updateMask.fieldPaths=sentence_stats.' + CFG.datasetId + '.last_active', {{
                method: 'PATCH',
                headers: {{
                    'Authorization': 'Bearer ' + CFG.firestoreToken,
                    'Content-Type': 'application/json',
                }},
                body: JSON.stringify({{ fields }})
            }});

            // completed 用原子遞增（只有新句型 completion_count === 1 時才 +1）
            const newCount = await fsReadCompletionCount();
            if (newCount === 1) {{
                await fetch(commitUrl, {{
                    method: 'POST',
                    headers: {{
                        'Authorization': 'Bearer ' + CFG.firestoreToken,
                        'Content-Type': 'application/json',
                    }},
                    body: JSON.stringify({{
                        writes: [{{
                            transform: {{
                                document: `projects/${{projectId}}/databases/(default)/documents/${{docPath}}`,
                                fieldTransforms: [{{
                                    fieldPath: `sentence_stats.${{CFG.datasetId}}.completed`,
                                    increment: {{ integerValue: '1' }}
                                }}]
                            }}
                        }}]
                    }})
                }});
            }}
        }} catch(e) {{ console.warn('Stats update error:', e); }}
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

    // 全部完成後：completion_count +1（原子操作），completed_options 重置，round 寫入子文件
    async function saveRoundToFirestore() {{
        const projectId = CFG.firestoreProject;
        const docPath = CFG.firestoreDocPath;
        const commitUrl = `https://firestore.googleapis.com/v1/projects/${{projectId}}/databases/(default)/documents:commit`;

        // 1. 原子遞增 completion_count + 重置 completed_options + 更新 template/dataset
        try {{
            await fetch(commitUrl, {{
                method: 'POST',
                headers: {{
                    'Authorization': 'Bearer ' + CFG.firestoreToken,
                    'Content-Type': 'application/json',
                }},
                body: JSON.stringify({{
                    writes: [
                        // 寫入固定欄位
                        {{
                            update: {{
                                name: `projects/${{projectId}}/databases/(default)/documents/${{docPath}}`,
                                fields: {{
                                    template_text: {{ stringValue: CFG.template }},
                                    dataset_id: {{ stringValue: CFG.datasetId }},
                                    completed_options: {{ arrayValue: {{ values: [] }} }},
                                }}
                            }},
                            updateMask: {{ fieldPaths: ['template_text', 'dataset_id', 'completed_options'] }}
                        }},
                        // 原子遞增 completion_count
                        {{
                            transform: {{
                                document: `projects/${{projectId}}/databases/(default)/documents/${{docPath}}`,
                                fieldTransforms: [{{
                                    fieldPath: 'completion_count',
                                    increment: {{ integerValue: '1' }}
                                }}]
                            }}
                        }}
                    ]
                }})
            }});
        }} catch(e) {{ console.error('Save round error:', e); }}

        // 2. 讀取遞增後的 completion_count
        const newCount = await fsReadCompletionCount();

        // 3. Round 詳細資料寫入獨立子文件（不會覆蓋）
        const roundDocPath = `${{docPath}}/rounds/round_${{newCount}}`;
        const roundUrl = `https://firestore.googleapis.com/v1/projects/${{projectId}}/databases/(default)/documents/${{roundDocPath}}`;
        const roundData = {{
            round: {{ integerValue: String(newCount) }},
            timestamp: {{ stringValue: new Date().toISOString() }},
            results: toFsValue(S.results),
        }};
        try {{
            await fetch(roundUrl, {{
                method: 'PATCH',
                headers: {{
                    'Authorization': 'Bearer ' + CFG.firestoreToken,
                    'Content-Type': 'application/json',
                }},
                body: JSON.stringify({{ fields: roundData }})
            }});
        }} catch(e) {{ console.error('Save round detail error:', e); }}

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

            setStatus('🔊 聽示範...');
            showSentence(word);
            await playTTS(sentence);

            // 清空麥克風 buffer，丟棄 TTS 喇叭殘餘音訊
            if (S.analyser) {{
                const trash = new Uint8Array(S.analyser.frequencyBinCount);
                for (let i = 0; i < 5; i++) {{ S.analyser.getByteFrequencyData(trash); await sleep(50); }}
            }}

            setStatus('🎤 請跟著唸...');

            // 同時啟動語音辨識（作為 fallback 備用）
            const srPromise = speechRecognize();
            const recResult = await recordUntilSilence();
            const audioBlob = recResult.blob;

            if (!recResult.speechDetected || audioBlob.size < 1000) {{
                logEvent('audio_empty', `size=${{audioBlob.size}},speech=${{recResult.speechDetected}}`);
                setStatus('😮 沒有偵測到聲音，再試一次...');
                await sleep(1500);
                continue;
            }}

            updateVolBars(0);
            const srTranscripts = await srPromise;

            // SR 預篩：首次失敗即停用，之後直接送 Gemini（後台記錄但不顯示給使用者）
            const srEmpty = !srTranscripts || srTranscripts.length === 0 || srTranscripts.every(t => !t.trim());
            if (srEmpty && _srAvailable) {{
                logEvent('sr_disabled', `word=${{word}}`);
                _srAvailable = false;
            }}

            const result = await evaluate(audioBlob, word, srTranscripts);
            logEvent('attempt', JSON.stringify({{
                word,
                try: S.tries[word],
                ok: !!result.is_correct,
                transcript: (result.transcript || '').slice(0, 100),
                feedback: (result.feedback || '').slice(0, 200),
            }}));
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
                // 定期儲存練習時間（每 10 秒以上才寫一次）
                const elapsed = (Date.now() - S.drillStartTime) / 1000;
                const delta = elapsed - S.practiceTimeSaved;
                if (delta > 10) {{ savePracticeTime(delta); S.practiceTimeSaved = elapsed; }}
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
        unlockTTS();  // iOS: 在使用者手勢中同步解鎖 TTS
        S.results = {{}};  S.tries = {{}};  S.history = [];
        S.drillStartTime = Date.now();
        S.practiceTimeSaved = 0;
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
            // 偵測一次環境底噪，設定 VAD 門檻
            setStatus('🔇 偵測環境音量...');
            const noiseFloor = await measureNoiseFloor();
            S.vadThreshold = Math.max(CFG.silenceThreshold, noiseFloor * 1.5);
            console.log('[VAD] noise floor:', noiseFloor.toFixed(1), 'threshold:', S.vadThreshold.toFixed(1));
            logEvent('vad_init', `noise=${{noiseFloor.toFixed(1)}} threshold=${{S.vadThreshold.toFixed(1)}}`);
        }} catch (e) {{
            logEvent('mic_error', e.message || e.name || 'unknown');
            flushLog();
            setStatus('❌ 無法存取麥克風');
            const helpDiv = document.createElement('div');
            helpDiv.style.cssText = 'background:#fff3cd;border:1px solid #ffc107;border-radius:8px;padding:12px 16px;margin:12px auto;max-width:360px;text-align:left;font-size:14px;line-height:1.8;color:#333';
            helpDiv.innerHTML = `
                <b>📋 請依照以下步驟開啟麥克風：</b><br>
                <b>iPhone / iPad：</b><br>
                1. 點網址列左邊的「大小」按鈕<br>
                2. 找到「麥克風」→ 選「允許」<br>
                3. 重新整理頁面<br><br>
                <b>Android Chrome：</b><br>
                1. 點網址列左邊的 🔒 圖示<br>
                2.「麥克風」→ 開啟<br>
                3. 重新整理頁面<br><br>
                <b>電腦 Chrome：</b><br>
                1. 點網址列左邊的 🔒 圖示<br>
                2.「麥克風」→ 允許<br>
                3. 重新整理頁面
            `;
            $('drill-status').parentNode.insertBefore(helpDiv, $('drill-status').nextSibling);
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

        setStatus('📝 儲存成績中...');
        let newCount;
        try {{
            newCount = await saveRoundToFirestore();
            // 更新排行榜統計
            updateSentenceStats();
            // 儲存剩餘練習時間
            const finalElapsed = (Date.now() - S.drillStartTime) / 1000;
            const finalDelta = finalElapsed - S.practiceTimeSaved;
            if (finalDelta > 0) await savePracticeTime(finalDelta);
        }} catch(e) {{
            console.error('Save error:', e);
            logEvent('save_error', e.message || 'unknown');
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
        $('drill-complete').className = 'drill-complete show';
        // 全部一次過關的特殊訊息
        const allFirstTry = CFG.options.every(o => S.results[o]?.tries === 1);
        const completeTitle = allFirstTry ? '🏆 完美通關！全部一次過關！' : '🎉 本輪完成！';
        $('drill-complete').innerHTML = `
            <h2>${{completeTitle}}</h2>
            <div class="stars">${{stars || '⭐'}}</div>
            <p>累計 ${{newCount}} 輪 ${{nextMsg}}</p>
            <div class="drill-summary">${{summaryHtml}}</div>
        `;
        setStatus('✅ 成績已儲存');
        // 慶祝灑花
        launchConfetti();

        // 寫入 drill log
        logEvent('drill_complete', `round=${{newCount}}`);
        flushLog();

        if (S.stream) S.stream.getTracks().forEach(t => t.stop());

        // 顯示「再來一次」按鈕
        $('start-btn').textContent = '🔄 再來一次';
        $('start-btn').style.display = 'inline-block';
        $('start-btn').disabled = false;
    }}

    function launchConfetti() {{
        const emojis = ['🎉','🎊','⭐','✨','🌟','💫','🏆'];
        for (let i = 0; i < 20; i++) {{
            const el = document.createElement('div');
            el.className = 'confetti';
            el.textContent = emojis[Math.floor(Math.random() * emojis.length)];
            el.style.left = Math.random() * 100 + 'vw';
            el.style.animationDuration = (2 + Math.random() * 2) + 's';
            el.style.animationDelay = Math.random() * 1 + 's';
            document.body.appendChild(el);
            setTimeout(() => el.remove(), 5000);
        }}
    }}

    function sleep(ms) {{ return new Promise(r => setTimeout(r, ms)); }}

    // === SPEED CONTROL ===
    function initSpeedControl() {{
        const btns = document.querySelectorAll('.speed-btn');
        // 標記目前速度
        btns.forEach(btn => {{
            if (parseFloat(btn.dataset.rate) === CFG.ttsRate) btn.classList.add('active');
            btn.onclick = async () => {{
                const rate = parseFloat(btn.dataset.rate);
                CFG.ttsRate = rate;
                btns.forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                // 儲存到 Firestore 使用者文件
                if (CFG.firestoreUserDocPath) {{
                    const userUrl = `https://firestore.googleapis.com/v1/projects/${{CFG.firestoreProject}}/databases/(default)/documents/${{CFG.firestoreUserDocPath}}`;
                    try {{
                        await fetch(userUrl + '?updateMask.fieldPaths=tts_rate', {{
                            method: 'PATCH',
                            headers: {{
                                'Authorization': 'Bearer ' + CFG.firestoreToken,
                                'Content-Type': 'application/json',
                            }},
                            body: JSON.stringify({{ fields: {{ tts_rate: {{ doubleValue: rate }} }} }})
                        }});
                    }} catch(e) {{ console.warn('Save tts_rate error:', e); }}
                }}
            }};
        }});
    }}
    initSpeedControl();

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
