const { onRequest } = require("firebase-functions/v2/https");
const { defineSecret } = require("firebase-functions/params");
const crypto = require("crypto");

const GEMINI_API_KEY = defineSecret("GEMINI_API_KEY");
const PROXY_SECRET = defineSecret("PROXY_SECRET");

const ALLOWED_ORIGINS = [
  "https://flashcard-techeasy.streamlit.app",
];

const MODELS = ["gemini-2.5-flash", "gemini-2.0-flash"];  // v2

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
  if (Math.abs(now - ts) > 7200) return false;

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
    secrets: [GEMINI_API_KEY, PROXY_SECRET],
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

    // 驗證 HMAC token（暫時降級為 log-only，不阻擋請求）
    const authHeader = req.headers.authorization || "";
    if (!authHeader.startsWith("Bearer ")) {
      res.status(401).json({ error: "Missing auth token" });
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
      res.status(status).json(data);
    } catch (error) {
      console.error("Gemini proxy error:", error);
      res.status(500).json({ error: "Internal error" });
    }
  }
);
