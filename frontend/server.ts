import { Hono } from "hono";
import { serveStatic } from "hono/bun";

const API_URL = process.env.API_URL || "http://localhost:8000";
const PORT = Number(process.env.PORT) || 3001;

const app = new Hono();

app.all("/api/*", async (c) => {
  const url = new URL(c.req.url);
  const target = `${API_URL}${url.pathname}${url.search}`;
  return fetch(target, {
    method: c.req.method,
    headers: c.req.raw.headers,
    body: ["GET", "HEAD"].includes(c.req.method) ? undefined : c.req.raw.body,
  });
});

app.use("/assets/*", serveStatic({ root: "./public" }));
app.use("/main.js", serveStatic({ path: "./public/main.js" }));
app.use("/styles.css", serveStatic({ path: "./public/styles.css" }));

app.get("*", (c) =>
  c.html(`<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>VCP Scanner</title>
<link rel="stylesheet" href="/styles.css" />
</head>
<body>
<div id="root"></div>
<script type="module" src="/main.js"></script>
</body>
</html>`),
);

console.log(`frontend ready on http://localhost:${PORT} (api → ${API_URL})`);
export default { port: PORT, fetch: app.fetch };
