import { once } from "node:events";
import { createServer, request, Server } from "node:http";
import type { AddressInfo } from "node:net";

import { afterAll, beforeAll, expect, it, vi } from "vitest";

import config from "../../next.config";

vi.mock("next", () => ({
  default: () => ({
    prepare: async () => {},
    getRequestHandler: () => (_req: unknown, res: import("node:http").ServerResponse) => res.end("testpagina"),
    getUpgradeHandler: () => () => {},
  }),
}));

let gedrag: "gezond" | "databasefout" | "stil" | "onvolledig" = "gezond";
const core = createServer((req, res) => {
  if (gedrag === "stil") return;
  res.setHeader("content-type", "application/json");
  if (req.url === "/healthz" && req.headers["x-correlation-id"]) {
    res.setHeader("x-correlation-id", req.headers["x-correlation-id"]);
  }
  if (gedrag === "databasefout") {
    res.writeHead(503);
    res.end(JSON.stringify({ detail: "database onbereikbaar", correlation_id: req.headers["x-correlation-id"] }));
    return;
  }
  if (gedrag === "onvolledig") res.write("{");
  else if (req.url === "/readyz") {
    let bytes = 0;
    req.on("data", (chunk: Buffer) => { bytes += chunk.length; });
    req.on("end", () => res.end(JSON.stringify({ bytes })));
  } else res.end(JSON.stringify({ status: "ok", bron: req.url }));
});
let web: Server;
let basis: string;

function adres(server: Server) {
  return `http://localhost:${(server.address() as AddressInfo).port}`;
}

async function sluit(server: Server) {
  if (!server?.listening) return;
  const klaar = once(server, "close");
  server.close();
  server.closeAllConnections();
  await klaar;
}

beforeAll(async () => {
  core.listen(0, "localhost");
  await once(core, "listening");
  vi.stubEnv("MSO_CORE_URL", adres(core));
  vi.stubEnv("MSO_WEB_HOST", "localhost");
  vi.stubEnv("PORT", "0");
  const listen = vi.spyOn(Server.prototype, "listen");
  try {
    const modulePad = "../../server.mjs";
    await import(modulePad);
    web = listen.mock.instances[0] as Server;
    if (!web.listening) await once(web, "listening");
    basis = adres(web);
  } finally {
    listen.mockRestore();
  }
});

afterAll(async () => {
  await sluit(web);
  await sluit(core);
  vi.unstubAllEnvs();
});

it("geeft de JSON van de core door op de publieke livenessroute", async () => {
  expect(await config.rewrites?.()).toContainEqual({ source: "/healthz", destination: expect.stringMatching(/\/healthz$/) });
  const antwoord = await fetch(basis + "/healthz");
  expect(antwoord.status).toBe(200);
  expect(antwoord.headers.get("content-type")).toContain("application/json");
  expect(await antwoord.json()).toEqual({ status: "ok", bron: "/healthz" });
});

it("geeft elke publieke livenessaanroep vóór doorvoer een eigen correlatie-ID", async () => {
  const headers = { "X-Correlation-Id": "meegegeven-kenmerk" };
  const ids = [];
  for (let i = 0; i < 2; i++) {
    const antwoord = await fetch(basis + "/healthz", { headers });
    expect(antwoord.status).toBe(200);
    expect(await antwoord.text()).not.toContain(headers["X-Correlation-Id"]);
    const id = antwoord.headers.get("x-correlation-id");
    expect(id).toMatch(/^[0-9a-f-]{36}$/);
    ids.push(id);
  }
  expect(ids[0]).not.toBe(ids[1]);
});

it("schermt het verzoekkenmerk af in de livenessfout van de core", async () => {
  gedrag = "databasefout";
  try {
    const antwoord = await fetch(basis + "/healthz", { headers: { "X-Correlation-Id": "meegegeven-kenmerk" } });
    expect(antwoord.status).toBe(503);
    const id = antwoord.headers.get("x-correlation-id");
    expect(id).toMatch(/^[0-9a-f-]{36}$/);
    expect(await antwoord.json()).toEqual({ detail: "database onbereikbaar", correlation_id: id });
  } finally {
    gedrag = "gezond";
  }
});

it.each(["vaste lengte", "stream"])("bewaakt 1 MiB op de webpoort bij %s", async (vorm) => {
  const grens = 1024 * 1024;
  async function verstuur(bytes: number) {
    const headers = vorm === "stream" ? { "transfer-encoding": "chunked" } : { "content-length": bytes };
    const req = request(basis + "/readyz", { method: "GET", headers, agent: false });
    const ontvangen = once(req, "response");
    req.write(Buffer.alloc(bytes - 1));
    req.end(Buffer.alloc(1));
    const [res] = await ontvangen;
    const delen = [];
    for await (const deel of res) delen.push(deel);
    return { status: res.statusCode, body: JSON.parse(Buffer.concat(delen).toString()) };
  }
  expect(await verstuur(grens)).toEqual({ status: 200, body: { bytes: grens } });
  const geweigerd = await verstuur(grens + 1);
  expect(geweigerd.status).toBe(413);
  expect(geweigerd.body).toEqual({ code: "payload_too_large", message: expect.any(String),
    details: { max_bytes: grens }, trace_id: expect.stringMatching(/^[0-9a-f-]{36}$/) });
});

it.each(["stil", "onvolledig"] as const)("geeft bij een %s antwoord tijdig 503 en Retry-After", async (mode) => {
  gedrag = mode;
  const routes = ["/readyz", "/connector/v1/discover"];
  await Promise.all(routes.map(async (route) => {
    const antwoord = await fetch(basis + route, {
      headers: { "X-Correlation-Id": "meegegeven-kenmerk" }, signal: AbortSignal.timeout(8000),
    });
    expect(antwoord.status).toBe(503);
    expect(Number(antwoord.headers.get("retry-after"))).toBeGreaterThan(0);
    const body = await antwoord.json();
    if (route === "/readyz") {
      expect(body).toEqual({ status: "not_ready", checks: { core: false } });
      expect(antwoord.headers.get("x-correlation-id")).toMatch(/^[0-9a-f-]{36}$/);
    } else {
      expect(Object.keys(body).sort()).toEqual(["code", "details", "message", "trace_id"]);
      expect(body.code).toBe("dependency_unavailable");
    }
  }));
}, 10000);

it("geeft bij een gestopte core ook op liveness een JSON-storing", async () => {
  await sluit(core);
  const antwoord = await fetch(basis + "/healthz", { headers: { "X-Correlation-Id": "meegegeven-kenmerk" } });
  expect(antwoord.status).toBe(503);
  expect(antwoord.headers.get("x-correlation-id")).toMatch(/^[0-9a-f-]{36}$/);
  expect(antwoord.headers.get("content-type")).toContain("application/json");
  expect(await antwoord.json()).toMatchObject({ status: "down" });
});
