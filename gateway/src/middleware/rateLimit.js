import rateLimit from "express-rate-limit";

export const mcpLimiter = rateLimit({
  windowMs: 60 * 1000,
  limit: 60,
  standardHeaders: true,
  legacyHeaders: false,
  message: { error: "Too many requests, please try again later." },
});
