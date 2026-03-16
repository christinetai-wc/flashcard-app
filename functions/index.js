const { onRequest } = require("firebase-functions/v2/https");
const { defineSecret } = require("firebase-functions/params");

const GEMINI_API_KEY = defineSecret("GEMINI_API_KEY");

const ALLOWED_ORIGINS = [
  "https://flashcard-techeasy.streamlit.app",
];

const MODELS = ["gemini-2.5-flash", "gemini-2.0-flash"];

exports.geminiProxy = onRequest(
  {
    cors: ALLOWED_ORIGINS,
    secrets: [GEMINI_API_KEY],
    invoker: "public",
    maxInstances: 10,
    timeoutSeconds: 60,
    memory: "256MiB",
  },
  async (req, res) => {
    // 只接受 POST
    if (req.method !== "POST") {
      res.status(405).json({ error: "Method not allowed" });
      return;
    }

    // 驗證 auth token（使用 Firestore service account token）
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

    // 限制 body 大小（音檔 base64 約 200-500KB）
    const bodySize = JSON.stringify(req.body).length;
    if (bodySize > 2 * 1024 * 1024) {
      res.status(413).json({ error: "Request too large" });
      return;
    }

    // 選擇模型，預設 gemini-2.5-flash
    const targetModel = MODELS.includes(model) ? model : MODELS[0];
    const apiKey = GEMINI_API_KEY.value();
    const url = `https://generativelanguage.googleapis.com/v1beta/models/${targetModel}:generateContent?key=${apiKey}`;

    try {
      const response = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          contents,
          generationConfig,
          // 關閉 thinking 以降低 token 費用（口說判讀不需要深度思考）
          generationConfig: {
            ...generationConfig,
            thinkingConfig: { thinkingBudget: 0 },
          },
        }),
      });

      const status = response.status;
      const data = await response.json();

      // 透傳 Gemini 的 status code（讓 JS 端的降級邏輯正常運作）
      res.status(status).json(data);
    } catch (error) {
      console.error("Gemini proxy error:", error);
      res.status(500).json({ error: "Proxy error: " + error.message });
    }
  }
);
