"""
例句連連看 拖拉配對 JS 元件
左邊例句（挖空），右邊單字卡片，拖拉到對應的空格
提交後直接用 Firestore REST API 更新 SRS
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


def generate_match_html(questions, options, vocab_path=""):
    """產生拖拉配對的 HTML/JS/CSS 元件

    questions: [{"blanked": "...", "answer": "test", "original": "...", "id": "xxx"}, ...]
    options: ["test", "rule", ...] (含干擾項，已打亂)
    vocab_path: Firestore 路徑，如 "artifacts/flashcard-pro-v1/users/S002/vocabulary"

    回傳 HTML 字串
    """
    token, project_id = _get_firestore_token()
    config = json.dumps({
        "questions": questions,
        "options": options,
        "firestoreToken": token,
        "firestoreProject": project_id,
        "vocabPath": vocab_path,
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
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: transparent; padding: 8px; }}
body.dark {{ color: #e0e0e0; }}
body.light {{ color: #1a1a1a; }}

.match-container {{ display: flex; gap: 16px; }}
@media (max-width: 600px) {{ .match-container {{ flex-direction: column; }} }}

.match-left {{ flex: 3; }}
.match-right {{ flex: 1; min-width: 120px; }}

.sentence-row {{
    display: flex; align-items: center; gap: 8px;
    margin-bottom: 10px; padding: 8px; border-radius: 8px;
    min-height: 48px; font-size: 0.95rem;
}}
body.light .sentence-row {{ background: rgba(0,0,0,0.03); }}
body.dark .sentence-row {{ background: rgba(255,255,255,0.05); }}

.sentence-num {{ font-weight: 700; min-width: 24px; }}
.sentence-text {{ flex: 1; }}

.drop-zone {{
    min-width: 80px; min-height: 36px; padding: 6px 12px;
    border: 2px dashed #888; border-radius: 8px;
    display: flex; align-items: center; justify-content: center;
    font-weight: 600; font-size: 0.9rem; transition: all 0.2s;
}}
.drop-zone.has-word {{
    border-style: solid; border-color: #4CAF50;
}}
body.light .drop-zone.has-word {{ background: rgba(76,175,80,0.1); color: #2e7d32; }}
body.dark .drop-zone.has-word {{ background: rgba(76,175,80,0.15); color: #6ddb8a; }}
.drop-zone.drag-over {{ border-color: #ff9800; background: rgba(255,152,0,0.1); }}

.word-pool-title {{ font-size: 0.85rem; opacity: 0.6; margin-bottom: 8px; }}
.word-card {{
    padding: 8px 14px; margin-bottom: 6px; border-radius: 8px;
    font-size: 0.9rem; font-weight: 600; cursor: grab;
    text-align: center; transition: all 0.2s; user-select: none;
    -webkit-user-select: none;
}}
.word-card:active {{ cursor: grabbing; opacity: 0.8; transform: scale(0.95); }}
body.light .word-card {{ background: rgba(28,131,225,0.1); color: #0e4da4; }}
body.dark .word-card {{ background: rgba(80,160,255,0.15); color: #7db8ff; }}
.word-card.placed {{ opacity: 0.3; pointer-events: none; }}

.submit-btn {{
    display: block; width: 100%; margin-top: 16px; padding: 12px;
    font-size: 1rem; font-weight: 600; border: none; border-radius: 10px;
    cursor: pointer; transition: all 0.2s;
    background: linear-gradient(135deg, #667eea, #764ba2); color: #fff;
    box-shadow: 0 4px 12px rgba(102,126,234,0.3);
}}
.submit-btn:hover {{ transform: translateY(-1px); box-shadow: 0 6px 16px rgba(102,126,234,0.4); }}
.submit-btn:disabled {{ opacity: 0.4; cursor: not-allowed; transform: none; }}

.result-row {{ padding: 8px 12px; margin-bottom: 6px; border-radius: 8px; font-size: 0.9rem; }}
.result-correct {{ background: rgba(76,175,80,0.15); border-left: 3px solid #4CAF50; }}
.result-wrong {{ background: rgba(244,67,54,0.1); border-left: 3px solid #f44336; }}
.result-score {{ text-align: center; font-size: 1.3rem; font-weight: 700; margin: 12px 0; }}
</style>

<div id="match-app"></div>

<script>
(function() {{
    // 偵測深色模式
    try {{
        const bg = getComputedStyle(window.parent.document.body).backgroundColor;
        const m = bg.match(/\\d+/g);
        if (m) {{
            const avg = (parseInt(m[0]) + parseInt(m[1]) + parseInt(m[2])) / 3;
            document.body.className = avg < 128 ? 'dark' : 'light';
        }} else {{ document.body.className = 'dark'; }}
    }} catch(e) {{ document.body.className = 'dark'; }}

    const CFG = {config};
    const app = document.getElementById('match-app');
    let answers = {{}};  // {{ 0: "word", 1: "word", ... }}
    let submitted = false;
    let dragWord = null;
    let touchOffsetX = 0, touchOffsetY = 0;

    function render() {{
        if (submitted) return;

        let html = '<div class="match-container">';

        // 左邊：例句
        html += '<div class="match-left">';
        CFG.questions.forEach((q, i) => {{
            const word = answers[i] || '';
            const hasWord = word ? 'has-word' : '';
            html += `<div class="sentence-row">
                <span class="sentence-num">${{i+1}}.</span>
                <span class="sentence-text">${{q.blanked}}</span>
                <div class="drop-zone ${{hasWord}}" data-idx="${{i}}">${{word || ''}}</div>
            </div>`;
        }});
        html += '</div>';

        // 右邊：單字池
        const placedWords = new Set(Object.values(answers));
        html += '<div class="match-right">';
        html += '<div class="word-pool-title">📦 拖到左邊空格</div>';
        CFG.options.forEach(w => {{
            const placed = placedWords.has(w) ? 'placed' : '';
            html += `<div class="word-card ${{placed}}" draggable="true" data-word="${{w}}">${{w}}</div>`;
        }});
        html += '</div>';

        html += '</div>';
        html += '<button class="submit-btn" id="submit-btn">✅ 提交答案</button>';

        app.innerHTML = html;
        bindEvents();
    }}

    function bindEvents() {{
        // Drag events for word cards
        document.querySelectorAll('.word-card:not(.placed)').forEach(card => {{
            card.addEventListener('dragstart', e => {{
                dragWord = e.target.dataset.word;
                e.target.style.opacity = '0.5';
            }});
            card.addEventListener('dragend', e => {{
                e.target.style.opacity = '';
                dragWord = null;
            }});

            // Touch events for mobile
            card.addEventListener('touchstart', e => {{
                dragWord = card.dataset.word;
                const touch = e.touches[0];
                const rect = card.getBoundingClientRect();
                touchOffsetX = touch.clientX - rect.left;
                touchOffsetY = touch.clientY - rect.top;

                // 建立拖曳影子
                const ghost = card.cloneNode(true);
                ghost.id = 'drag-ghost';
                ghost.style.position = 'fixed';
                ghost.style.zIndex = '9999';
                ghost.style.pointerEvents = 'none';
                ghost.style.opacity = '0.8';
                ghost.style.width = rect.width + 'px';
                ghost.style.left = (touch.clientX - touchOffsetX) + 'px';
                ghost.style.top = (touch.clientY - touchOffsetY) + 'px';
                document.body.appendChild(ghost);
                card.style.opacity = '0.3';
            }}, {{ passive: true }});

            card.addEventListener('touchmove', e => {{
                e.preventDefault();
                const ghost = document.getElementById('drag-ghost');
                if (!ghost) return;
                const touch = e.touches[0];
                ghost.style.left = (touch.clientX - touchOffsetX) + 'px';
                ghost.style.top = (touch.clientY - touchOffsetY) + 'px';

                // 偵測 drop zone
                document.querySelectorAll('.drop-zone').forEach(dz => dz.classList.remove('drag-over'));
                const el = document.elementFromPoint(touch.clientX, touch.clientY);
                if (el && el.classList.contains('drop-zone')) {{
                    el.classList.add('drag-over');
                }}
            }});

            card.addEventListener('touchend', e => {{
                const ghost = document.getElementById('drag-ghost');
                if (ghost) ghost.remove();
                card.style.opacity = '';

                if (!dragWord) return;
                const touch = e.changedTouches[0];
                const el = document.elementFromPoint(touch.clientX, touch.clientY);
                if (el && el.classList.contains('drop-zone')) {{
                    const idx = parseInt(el.dataset.idx);
                    // 如果這格已有字，先移除
                    if (answers[idx]) delete answers[idx];
                    // 如果這個字已被放在別格，先移除
                    for (const k in answers) {{ if (answers[k] === dragWord) delete answers[k]; }}
                    answers[idx] = dragWord;
                    render();
                }}
                dragWord = null;
            }});
        }});

        // Drop zones
        document.querySelectorAll('.drop-zone').forEach(zone => {{
            zone.addEventListener('dragover', e => {{
                e.preventDefault();
                zone.classList.add('drag-over');
            }});
            zone.addEventListener('dragleave', () => {{
                zone.classList.remove('drag-over');
            }});
            zone.addEventListener('drop', e => {{
                e.preventDefault();
                zone.classList.remove('drag-over');
                if (!dragWord) return;
                const idx = parseInt(zone.dataset.idx);
                if (answers[idx]) delete answers[idx];
                for (const k in answers) {{ if (answers[k] === dragWord) delete answers[k]; }}
                answers[idx] = dragWord;
                dragWord = null;
                render();
            }});

            // 點擊已放的字可以移除
            zone.addEventListener('click', () => {{
                const idx = parseInt(zone.dataset.idx);
                if (answers[idx]) {{
                    delete answers[idx];
                    render();
                }}
            }});
        }});

        // Submit
        document.getElementById('submit-btn').addEventListener('click', () => {{
            submitted = true;
            showResults();
        }});
    }}

    // SRS 簡化版（跟 Python compute_srs_update 同邏輯）
    function computeSrs(wordDoc, isCorrect) {{
        const interval = parseInt(wordDoc.srs_interval || 0);
        let ease = parseFloat(wordDoc.srs_ease || 2.5);
        let streak = parseInt(wordDoc.srs_streak || 0);
        let newInterval;

        if (isCorrect) {{
            streak += 1;
            if (streak === 1) newInterval = 1;
            else if (streak === 2) newInterval = 3;
            else newInterval = Math.round(interval * ease);
            ease = Math.min(3.0, ease + 0.1);
        }} else {{
            streak = 0;
            newInterval = 1;
            ease = Math.max(1.3, ease - 0.2);
        }}

        const today = new Date();
        const due = new Date(today);
        due.setDate(due.getDate() + newInterval);
        const dueStr = due.toISOString().slice(0, 10);
        const todayStr = today.toISOString().slice(0, 10);

        return {{
            srs_interval: newInterval,
            srs_ease: Math.round(ease * 100) / 100,
            srs_due: dueStr,
            srs_streak: streak,
            srs_last_review: todayStr,
        }};
    }}

    // 更新單字的 SRS + Correct/Total 到 Firestore
    async function updateWordInFirestore(docId, isCorrect) {{
        if (!CFG.vocabPath || !docId) return;
        const url = `https://firestore.googleapis.com/v1/projects/${{CFG.firestoreProject}}/databases/(default)/documents/${{CFG.vocabPath}}/${{docId}}`;
        try {{
            // 讀取現有資料
            const res = await fetch(url, {{
                headers: {{ 'Authorization': 'Bearer ' + CFG.firestoreToken }}
            }});
            if (!res.ok) return;
            const doc = await res.json();
            const f = doc.fields || {{}};

            const correct = parseInt(f.Correct?.integerValue || '0') + (isCorrect ? 1 : 0);
            const total = parseInt(f.Total?.integerValue || '0') + 1;

            // 計算 SRS
            const wordData = {{
                srs_interval: parseInt(f.srs_interval?.integerValue || '0'),
                srs_ease: parseFloat(f.srs_ease?.doubleValue || f.srs_ease?.integerValue || '2.5'),
                srs_streak: parseInt(f.srs_streak?.integerValue || '0'),
            }};
            const srs = computeSrs(wordData, isCorrect);

            // 寫回
            const fields = {{
                Correct: {{ integerValue: String(correct) }},
                Total: {{ integerValue: String(total) }},
                srs_interval: {{ integerValue: String(srs.srs_interval) }},
                srs_ease: {{ doubleValue: srs.srs_ease }},
                srs_due: {{ stringValue: srs.srs_due }},
                srs_streak: {{ integerValue: String(srs.srs_streak) }},
                srs_last_review: {{ stringValue: srs.srs_last_review }},
            }};
            await fetch(url + '?updateMask.fieldPaths=Correct&updateMask.fieldPaths=Total&updateMask.fieldPaths=srs_interval&updateMask.fieldPaths=srs_ease&updateMask.fieldPaths=srs_due&updateMask.fieldPaths=srs_streak&updateMask.fieldPaths=srs_last_review', {{
                method: 'PATCH',
                headers: {{
                    'Authorization': 'Bearer ' + CFG.firestoreToken,
                    'Content-Type': 'application/json',
                }},
                body: JSON.stringify({{ fields }})
            }});
        }} catch(e) {{ console.warn('SRS update error:', e); }}
    }}

    async function showResults() {{
        let html = '';
        let correct = 0;

        CFG.questions.forEach((q, i) => {{
            const userAns = answers[i] || '';
            const isCorrect = userAns.toLowerCase() === q.answer.toLowerCase();
            if (isCorrect) correct++;

            if (isCorrect) {{
                html += `<div class="result-row result-correct">✅ ${{q.original}}</div>`;
            }} else if (userAns) {{
                const wrong = q.blanked.replace('______', '<b>' + userAns + '</b>');
                html += `<div class="result-row result-wrong">❌ ${{wrong}} → 正確：<b>${{q.answer}}</b></div>`;
            }} else {{
                html += `<div class="result-row result-wrong">❌ （未作答）→ 正確：<b>${{q.answer}}</b>  ${{q.original}}</div>`;
            }}

            // 更新 Firestore SRS
            updateWordInFirestore(q.id, isCorrect);
        }});

        const total = CFG.questions.length;
        let scoreText = correct + ' / ' + total;
        if (correct === total) scoreText = '🎉 ' + scoreText + ' 滿分！';
        else if (correct >= total - 1) scoreText = '👏 ' + scoreText;

        html += `<div class="result-score">${{scoreText}}</div>`;
        app.innerHTML = html;
    }}

    render();
}})();
</script>
</body>
</html>
"""
