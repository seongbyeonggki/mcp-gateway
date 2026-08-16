import express from "express";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import { z } from "zod";

const PORT = process.env.PORT || 3001;

function createMcpServer() {
  const server = new McpServer({
    name: "mcp-server",
    version: "1.0.0",
  });

  server.registerTool(
    "calculate",
    {
      description: "Perform a basic arithmetic operation on two numbers",
      inputSchema: {
        a: z.number(),
        b: z.number(),
        op: z.enum(["add", "subtract", "multiply", "divide"]),
      },
    },
    async ({ a, b, op }) => {
      let result;
      switch (op) {
        case "add":
          result = a + b;
          break;
        case "subtract":
          result = a - b;
          break;
        case "multiply":
          result = a * b;
          break;
        case "divide":
          if (b === 0) {
            throw new Error("Division by zero is not allowed");
          }
          result = a / b;
          break;
      }
      return {
        content: [{ type: "text", text: String(result) }],
      };
    }
  );

  server.registerTool(
    "get_current_time",
    {
      description: "Get the current time in Korea (Asia/Seoul)",
      inputSchema: {},
    },
    async () => {
      const now = new Date().toLocaleString("ko-KR", {
        timeZone: "Asia/Seoul",
      });
      return {
        content: [{ type: "text", text: now }],
      };
    }
  );

  return server;
}

const app = express();
app.use(express.json());

app.get("/health", (req, res) => {
  res.json({ status: "ok", service: "mcp-server" });
});

app.post("/mcp", async (req, res) => {
  try {
    const server = createMcpServer();
    const transport = new StreamableHTTPServerTransport({
      sessionIdGenerator: undefined,
    });

    res.on("close", () => {
      transport.close();
      server.close();
    });

    await server.connect(transport);
    await transport.handleRequest(req, res, req.body);
  } catch (err) {
    console.error("Error handling MCP request:", err);
    if (!res.headersSent) {
      res.status(500).json({
        jsonrpc: "2.0",
        error: { code: -32603, message: "Internal server error" },
        id: null,
      });
    }
  }
});

app.listen(PORT, () => {
  console.log(`mcp-server listening on port ${PORT}`);
});
