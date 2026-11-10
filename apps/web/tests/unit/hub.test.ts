import { readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { hubUrl } from "@/lib/hub";

// Shared with apps/core/tests/test_hub_url_setting.py (contract 1.3 §2.3 regel 5 + §3.2, C1.3-MSO-02): the UI shows
// the link only when the core would accept the value, and normalises it the same way.
const vectors = JSON.parse(readFileSync(path.resolve(import.meta.dirname, "../../../../tests/vectoren/hub_url.json"), "utf8")) as {
  geldig: Array<[string, string]>;
  ongeldig: string[];
};

describe("hubUrl (Terug naar de Hub, Peil O-11)", () => {
  it("the shared vector file is not empty", () => {
    expect(vectors.geldig.length).toBeGreaterThan(10);
    expect(vectors.ongeldig.length).toBeGreaterThan(40);
  });

  it.each(vectors.geldig)("valid %s → %s", (raw, out) => {
    expect(hubUrl(raw)).toBe(out);
  });

  it.each([...vectors.ongeldig, "", "   "])("no link for %s", (raw) => {
    expect(hubUrl(raw)).toBeNull();
  });

  it.each([null, undefined, 42, {}, ["http://nas.lan"]])("no link for a non-string %s", (raw) => {
    expect(hubUrl(raw)).toBeNull();
  });

  // a value stored under the 1.2 rules that 1.3 refuses still produces no link (the core keeps it, ADR-0009)
  it.each(["http://2130706433:30080", "http://010.0.0.1", "http://0x0a000001:30080", "http://10.0.0.010"])(
    "no link for an older, now invalid value %s", (raw) => {
      expect(hubUrl(raw)).toBeNull();
    });
});
