// Derived from MediaGuard Ultimate (lvos04/media-guard-ultimate@e92c2acc) apps/web/server.mjs — internally versioned copy, see PROVENANCE.md
//
// MSO web server: serves the Next.js UI and proxies API traffic so the browser only ever talks to
// one origin (cookies stay HttpOnly/SameSite=strict, no secrets in the browser).
//
//   /api/v1/*    → MSO_CORE_URL (default http://localhost:8200) — MSO core (FastAPI)
//   /newznab/*   → MSO_CORE_URL/newznab/*                        — Newznab-compatible feed for *arr apps
//   /torznab/*   → MSO_CORE_URL/torznab/*                        — Torznab-compatible feed for *arr apps
//
// Streaming responses (SSE) are piped without buffering. WebSocket upgrades on these paths are
// forwarded as well.
import { randomUUID } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";
import { request as httpRequest } from "node:http";
import { request as httpsRequest } from "node:https";
import { createServer } from "node:http";
import { connect as netConnect } from "node:net";
import { PassThrough } from "node:stream";
import { connect as tlsConnect } from "node:tls";

import next from "next";

const host = process.env.MSO_WEB_HOST ?? process.env.LAN_LISTEN_HOST ?? "0.0.0.0";
const port = Number.parseInt(process.env.PORT ?? "3000", 10);
const coreUrl = new URL(process.env.MSO_CORE_URL ?? "http://localhost:8200");
const development = process.env.NODE_ENV !== "production";
const trustProxy = process.env.MSO_TRUST_PROXY === "1";

const HOP_BY_HOP = new Set(["connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te",
  "trailer", "transfer-encoding", "upgrade", "host"]);

const CONNECTOR_PREFIX = "/connector/v1";
const CORE_PREFIXES = ["/api/v1", "/newznab", "/torznab", CONNECTOR_PREFIX];
const CORE_JSON_TIMEOUT_MS = 5000;
const MAX_REQUEST_BYTES = 1024 * 1024;
// Ook een pagina van 200 event-enveloppen met elk 16 KiB payload past binnen deze grens.
const MAX_CORE_JSON_BYTES = 4 * 1024 * 1024;

function connectorPath(pathname) {
  return pathname === CONNECTOR_PREFIX || pathname.startsWith(`${CONNECTOR_PREFIX}/`);
}

function target(pathname) {
  if (pathname === "/readyz" || pathname === "/healthz") return { base: coreUrl, path: pathname };
  for (const prefix of CORE_PREFIXES) {
    if (pathname === prefix || pathname.startsWith(`${prefix}/`)) return { base: coreUrl, path: pathname };
  }
  return null;
}

function forwardHeaders(req, dest) {
  const headers = {};
  for (const [key, value] of Object.entries(req.headers)) {
    if (!HOP_BY_HOP.has(key.toLowerCase()) && value !== undefined) headers[key] = value;
  }
  headers.host = dest.base.host;
  if (dest.path === "/healthz") headers["x-correlation-id"] = randomUUID();
  headers["x-forwarded-host"] = req.headers.host ?? "";
  headers["x-forwarded-proto"] = req.socket.encrypted ? "https" : "http";
  // Overwrite (never append): a LAN client must not be able to spoof its address for core's per-IP limits.
  // Behind a trusted TLS reverse proxy set MSO_TRUST_PROXY=1 to keep that proxy's X-Forwarded-For/-Host/-Proto.
  if (trustProxy && req.headers["x-forwarded-for"]) {
    headers["x-forwarded-for"] = String(req.headers["x-forwarded-for"]).split(",")[0].trim();
    headers["x-forwarded-host"] = req.headers["x-forwarded-host"] ?? req.headers.host ?? "";
    headers["x-forwarded-proto"] = req.headers["x-forwarded-proto"] ?? headers["x-forwarded-proto"];
  } else {
    headers["x-forwarded-for"] = req.socket.remoteAddress ?? "";
  }
  return headers;
}

async function readChunkedBody(req, signal) {
  const chunks = [];
  let size = 0;
  // Het afbreken van de tussenstroom laat de clientsocket vrij voor de JSON-fout.
  for await (const chunk of req.pipe(new PassThrough({ signal }))) {
    size += chunk.length;
    if (size > MAX_REQUEST_BYTES) return null;
    chunks.push(chunk);
  }
  return Buffer.concat(chunks, size);
}

async function proxy(req, res, dest) {
  const url = new URL(req.url ?? "/", "http://placeholder");
  const send = dest.base.protocol === "https:" ? httpsRequest : httpRequest;
  const limitRequest = connectorPath(dest.path) || dest.path === "/readyz";
  const bounded = limitRequest || dest.path === "/healthz";
  const signal = bounded ? AbortSignal.timeout(CORE_JSON_TIMEOUT_MS) : undefined;
  const requestHeaders = forwardHeaders(req, dest);
  function unavailable() {
    if (res.writableEnded || res.destroyed) return;
    if (res.headersSent) return res.destroy();
    const headers = { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" };
    if (bounded) {
      if (dest.path === "/readyz") headers["x-correlation-id"] = randomUUID();
      if (dest.path === "/healthz") headers["x-correlation-id"] = requestHeaders["x-correlation-id"];
      if (!req.complete) headers.connection = "close";
      res.writeHead(503, { ...headers, "retry-after": "1" });
      if (dest.path === "/readyz") res.end(JSON.stringify({ status: "not_ready", checks: { core: false } }));
      else if (dest.path === "/healthz") res.end(JSON.stringify({ status: "down" }));
      else res.end(JSON.stringify({
        code: "dependency_unavailable", message: "MSO is tijdelijk onbereikbaar.", details: {}, trace_id: randomUUID(),
      }));
    } else {
      res.writeHead(502, headers);
      res.end(JSON.stringify({ detail: "Upstream service unreachable", code: "upstream_unreachable" }));
    }
  }
  let body;
  if (limitRequest) {
    if (req.headers["transfer-encoding"]) {
      try {
        body = await readChunkedBody(req, signal);
      } catch {
        unavailable();
        return;
      }
      // Een GET krijgt ook bij streaming een expliciete afbakening op de verbinding naar de core.
      if (body !== null) requestHeaders["content-length"] = body.length;
    }
    if (body === null || Number(req.headers["content-length"]) > MAX_REQUEST_BYTES) {
      req.resume();
      res.writeHead(413, { "content-type": "application/json; charset=utf-8", "cache-control": "no-store",
        connection: "close" });
      res.end(JSON.stringify({ code: "payload_too_large", message: "Het verzoek is groter dan 1 MiB.",
        details: { max_bytes: MAX_REQUEST_BYTES }, trace_id: randomUUID() }));
      return;
    }
  }
  if (res.destroyed) return;
  const upstream = send({
    protocol: dest.base.protocol,
    hostname: dest.base.hostname,
    port: dest.base.port || (dest.base.protocol === "https:" ? 443 : 80),
    method: req.method,
    path: dest.path + url.search,
    headers: requestHeaders,
    signal,
  }, (upstreamRes) => {
    upstreamRes.on("error", unavailable);
    const headers = { ...upstreamRes.headers };
    if (bounded) {
      // Houd de status beschikbaar tot de volledige JSON binnen is, ook bij een onderbroken antwoord.
      const chunks = [];
      let bytes = 0;
      upstreamRes.on("data", (chunk) => {
        bytes += chunk.length;
        if (bytes > MAX_CORE_JSON_BYTES) {
          unavailable();
          upstream.destroy();
        } else chunks.push(chunk);
      });
      upstreamRes.on("end", () => {
        if (res.writableEnded || res.destroyed) return;
        res.writeHead(upstreamRes.statusCode ?? 502, headers);
        res.end(Buffer.concat(chunks));
      });
      return;
    }
    if (String(headers["content-type"] ?? "").startsWith("text/event-stream")) {
      headers["cache-control"] = "no-cache, no-transform";
      headers["x-accel-buffering"] = "no";
    }
    res.writeHead(upstreamRes.statusCode ?? 502, headers);
    res.flushHeaders?.();
    upstreamRes.pipe(res);
  });
  if (!bounded) upstream.setTimeout(0);
  upstream.on("error", unavailable);
  req.on("aborted", () => upstream.destroy());
  res.on("close", () => upstream.destroy());
  if (body !== undefined) upstream.end(body);
  else req.pipe(upstream);
}

function proxyUpgrade(req, socket, head, dest) {
  const portNo = Number(dest.base.port || (dest.base.protocol === "https:" ? 443 : 80));
  const open = dest.base.protocol === "https:" ? tlsConnect : netConnect;
  const upstream = open({ host: dest.base.hostname, port: portNo, servername: dest.base.hostname }, () => {
    const url = new URL(req.url ?? "/", "http://placeholder");
    const headers = forwardHeaders(req, dest);
    headers.connection = "Upgrade";
    headers.upgrade = req.headers.upgrade ?? "websocket";
    const lines = [`GET ${dest.path}${url.search} HTTP/1.1`, ...Object.entries(headers).map(([k, v]) => `${k}: ${Array.isArray(v) ? v.join(", ") : v}`)];
    upstream.write(lines.join("\r\n") + "\r\n\r\n");
    if (head?.length) upstream.write(head);
    upstream.pipe(socket);
    socket.pipe(upstream);
  });
  const close = () => { socket.destroy(); upstream.destroy(); };
  upstream.on("error", close);
  socket.on("error", close);
}

// Standalone build (Docker): no next.config.ts / TypeScript at runtime — use the config that
// `next build` serialised, exactly like Next's own standalone server.js does.
const requiredFiles = new URL("./.next/required-server-files.json", import.meta.url);
if (!development && !process.env.__NEXT_PRIVATE_STANDALONE_CONFIG && existsSync(requiredFiles)) {
  process.env.__NEXT_PRIVATE_STANDALONE_CONFIG = JSON.stringify(JSON.parse(readFileSync(requiredFiles, "utf8")).config);
}

const application = next({ dev: development, hostname: host, port, dir: new URL(".", import.meta.url).pathname });
await application.prepare();
const handle = application.getRequestHandler();
const handleUpgrade = application.getUpgradeHandler();

const server = createServer((req, res) => {
  const pathname = new URL(req.url ?? "/", "http://placeholder").pathname;
  const dest = target(pathname);
  if (dest) {
    proxy(req, res, dest);
    return;
  }
  handle(req, res);
});

server.on("upgrade", (req, socket, head) => {
  const pathname = new URL(req.url ?? "/", "http://placeholder").pathname;
  const dest = target(pathname);
  if (dest) {
    proxyUpgrade(req, socket, head, dest);
    return;
  }
  handleUpgrade(req, socket, head);
});

server.listen(port, host, () => {
  console.log(`MSO web luistert op ${host}:${port} · core ${coreUrl.origin}`);
});
