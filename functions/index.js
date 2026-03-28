const { onRequest } = require("firebase-functions/v2/https");
const { defineSecret } = require("firebase-functions/params");
const crypto = require("crypto");

const GEMINI_API_KEY = defineSecret("GEMINI_API_KEY");
const PROXY_SECRET = defineSecret("PROXY_SECRET");
const LINE_CHANNEL_ACCESS_TOKEN = defineSecret("LINE_CHANNEL_ACCESS_TOKEN");
const LINE_TEACHER_USER_ID = defineSecret("LINE_TEACHER_USER_ID");

// LINE 通知冷卻：同一個錯誤類型 10 分鐘內只發一次
let _lastNotifyTime = 0;
const NOTIFY_COOLDOWN_MS = 10 * 60 * 1000;

async function notifyLineIfNeeded(model, statusCode, detail) {
  const now = Date.now();
  if (now - _lastNotifyTime < NOTIFY_COOLDOWN_MS) return;

  const token = LINE_CHANNEL_ACCESS_TOKEN.value();
  const userId = LINE_TEACHER_USER_ID.value();
  if (!token || !userId) return;

  _lastNotifyTime = now;
  const time = new Date(now + 8 * 3600 * 1000).toISOString().slice(11, 16);
  const msg = `⚠️ Gemini API 異常\n模型：${model}\n狀態：${statusCode}\n${detail}\n時間：${time}`;

  try {
    await fetch("https://api.line.me/v2/bot/message/push", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Authorization": `Bearer ${token}`,
      },
      body: JSON.stringify({
        to: userId,
        messages: [{ type: "text", text: msg }],
      }),
    });
  } catch (e) {
    console.error("LINE notify failed:", e.message);
  }
}

const ALLOWED_ORIGINS = [
  "https://flashcard-techeasy.streamlit.app",
];

const MODELS = ["gemini-2.5-flash"];

/**
 * 驗證 HMAC token
 * 格式：Bearer {timestamp}.{hmac}
 * HMAC = SHA256(secret, timestamp)
 * 有效期 2 小時
 */
function validateToken(authHeader, secret) {
  if (!authHeader.startsWith("Bearer ")) return false;
  const token = authHeader.slice(7);
  const parts = token.split(".");
  if (parts.length !== 2) return false;

  const [timestamp, hmac] = parts;
  const ts = parseInt(timestamp);
  if (isNaN(ts)) return false;

  // 檢查有效期（2 小時）
  const now = Math.floor(Date.now() / 1000);
  if (Math.abs(now - ts) > 3600) return false;

  // 驗證 HMAC
  const expected = crypto.createHmac("sha256", secret).update(timestamp).digest("hex");
  if (hmac.length !== expected.length) return false;
  try {
    return crypto.timingSafeEqual(Buffer.from(hmac), Buffer.from(expected));
  } catch (e) {
    return false;
  }
}

exports.geminiProxy = onRequest(
  {
    cors: ALLOWED_ORIGINS,
    secrets: [GEMINI_API_KEY, PROXY_SECRET, LINE_CHANNEL_ACCESS_TOKEN, LINE_TEACHER_USER_ID],
    invoker: "public",
    maxInstances: 10,
    timeoutSeconds: 60,
    memory: "256MiB",
  },
  async (req, res) => {
    if (req.method !== "POST") {
      res.status(405).json({ error: "Method not allowed" });
      return;
    }

    // 驗證 HMAC token
    const authHeader = req.headers.authorization || "";
    if (!validateToken(authHeader, PROXY_SECRET.value())) {
      res.status(401).json({ error: "Invalid or expired token" });
      return;
    }

    const { contents, generationConfig, model } = req.body;
    if (!contents) {
      res.status(400).json({ error: "Missing contents" });
      return;
    }

    const bodySize = JSON.stringify(req.body).length;
    if (bodySize > 2 * 1024 * 1024) {
      res.status(413).json({ error: "Request too large" });
      return;
    }

    const targetModel = MODELS.includes(model) ? model : MODELS[0];
    const apiKey = GEMINI_API_KEY.value();
    const url = `https://generativelanguage.googleapis.com/v1beta/models/${targetModel}:generateContent?key=${apiKey}`;

    try {
      const response = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          contents,
          generationConfig: {
            ...generationConfig,
            thinkingConfig: { thinkingBudget: 0 },
          },
        }),
      });

      const status = response.status;
      const data = await response.json();

      // API key 失效、額度用完、權限錯誤 → LINE 通知
      if (status === 401 || status === 403 || status === 429) {
        const errMsg = data?.error?.message || `HTTP ${status}`;
        notifyLineIfNeeded(targetModel, status, errMsg);
      }

      res.status(status).json(data);
    } catch (error) {
      console.error("Gemini proxy error:", error);
      notifyLineIfNeeded(targetModel, 500, error.message);
      res.status(500).json({ error: "Internal error" });
    }
  }
);
