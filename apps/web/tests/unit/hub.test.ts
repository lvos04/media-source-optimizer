import { describe, expect, it } from "vitest";

import { hubUrl } from "@/lib/hub";

// Same cases as apps/core/tests/test_hub_url_setting.py: the UI shows the link only when the core would accept it.
describe("hubUrl (Terug naar de Hub, Peil O-11)", () => {
  it.each([
    ["http://nas.lan:30080", "http://nas.lan:30080"],
    ["HTTPS://NAS.LAN/", "https://nas.lan"],
    ["http://10.20.30.40:8080", "http://10.20.30.40:8080"],
    ["http://[fd00::12]:30080", "http://[fd00::12]:30080"],
    ["  https://hub.example  ", "https://hub.example"],
  ])("valid %s → %s", (raw, out) => {
    expect(hubUrl(raw)).toBe(out);
  });

  it.each([
    "", "   ", null, undefined, 42, "javascript:alert(1)", "ftp://nas.lan", "nas.lan:30080", "http://",
    "http://nas.lan:30080/pad", "http://nas.lan/?q=1", "http://nas.lan/?", "http://nas.lan/#x",
    "http://gebruiker:geheim@nas.lan", "http://localhost:30080", "http://hub.localhost", "http://127.0.0.1:30080",
    "http://127.1", "http://[::1]:30080", "http://169.254.10.1", "http://[fe80::1]", "http://0.0.0.0:30080",
    "http://[::]:30080", "http://nas.lan:99999", "http://nas lan", "http://nas.lan\\@evil",
  ])("no link for %s", (raw) => {
    expect(hubUrl(raw)).toBeNull();
  });
});
