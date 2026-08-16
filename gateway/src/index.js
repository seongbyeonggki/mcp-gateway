import express from "express";
import jwt from "jsonwebtoken";
import morgan from "morgan";
import { createProxyMiddleware } from "http-proxy-middleware";
import { authMiddleware } from "./middleware/auth.js";
import { mcpLimiter } from "./middleware/rateLimit.js";

const PORT = process.env.PORT || 3000;
const MCP_SERVER_URL = process.env.MCP_SERVER_URL || "http://localhost:3001";
const JWT_SECRET = process.env.JWT_SECRET;

if (!JWT_SECRET) {
  console.error("JWT_SECRET environment variable is required");
  process.exit(1);
}

const app = express();
app.use(morgan("combined"));

app.get("/health", (req, res) => {
  res.json({ status: "ok", service: "gateway", mcpServerUrl: MCP_SERVER_URL });
});

app.post("/auth/token", express.json(), (req, res) => {
  const { userId } = req.body || {};

  if (!userId) {
    return res.status(400).json({ error: "userId is required" });
  }

  const token = jwt.sign({ userId }, JWT_SECRET, { expiresIn: "24h" });
  res.json({ token });
});

const mcpProxy = createProxyMiddleware({
  target: MCP_SERVER_URL,
  changeOrigin: true,
  on: {
    error: (err, req, res) => {
      console.error("Proxy error:", err.message);
      if (!res.headersSent) {
        res.status(502).json({ error: "Bad Gateway: failed to reach MCP server" });
      }
    },
  },
});

app.post("/mcp", authMiddleware, mcpLimiter, mcpProxy);

app.listen(PORT, () => {
  console.log(`gateway listening on port ${PORT}`);
});
