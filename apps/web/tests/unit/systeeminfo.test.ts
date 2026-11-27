import { afterEach, expect, it, vi } from "vitest";

import { coreRequest } from "@/lib/api/client";

function onbereikbaar(headers?: HeadersInit) {
  return Response.json({ code: "upstream_unreachable" }, { status: 502, headers });
}

afterEach(() => vi.unstubAllGlobals());

it("haalt de echte buildgegevens op na één tijdelijke proxyfout", async () => {
  const info = { version: "proef", schema_version: 613 };
  const haal = vi.fn().mockResolvedValueOnce(onbereikbaar()).mockResolvedValueOnce(Response.json(info));
  vi.stubGlobal("fetch", haal);
  expect(await coreRequest("/system/info")).toEqual(info);
  expect(haal).toHaveBeenCalledTimes(2);
});

it("meldt de blijvende proxyfout na twee systeemlezingen", async () => {
  const haal = vi.fn(async () => onbereikbaar());
  vi.stubGlobal("fetch", haal);
  await expect(coreRequest("/system/info")).rejects.toMatchObject({ status: 502, code: "upstream_unreachable" });
  expect(haal).toHaveBeenCalledTimes(2);
});

it("eerbiedigt Retry-After en herhaalt geen schrijfactie", async () => {
  const haal = vi.fn()
    .mockResolvedValueOnce(onbereikbaar({ "Retry-After": "8" }))
    .mockResolvedValueOnce(onbereikbaar());
  vi.stubGlobal("fetch", haal);
  await expect(coreRequest("/system/info")).rejects.toMatchObject({ retryAfterSeconds: 8 });
  await expect(coreRequest("/system/pause", { method: "POST" })).rejects.toMatchObject({ status: 502 });
  expect(haal).toHaveBeenCalledTimes(2);
});
