import { describe, expect, it } from "vitest";

import { localizeAuditSummary, MACHINE_AUDIT_KEYS } from "@/lib/api/insights";
import { messages as en } from "@/lib/i18n/messages/en/settings";
import { messages as enLogs } from "@/lib/i18n/messages/en/logs";
import { messages as nl } from "@/lib/i18n/messages/nl/settings";
import { messages as nlLogs } from "@/lib/i18n/messages/nl/logs";

describe("machine-sleutelteksten", () => {
  it("bewaart alle Nederlandse en Engelse ontwerpteksten met gelijke variabelen", () => {
    const keys = Object.keys(nl).filter((key) => key.startsWith("machineKeys."));
    expect(keys.length).toBeGreaterThan(50);
    expect(Object.keys(en).sort()).toEqual(keys.sort());
    for (const key of keys) {
      const a = nl[key as keyof typeof nl];
      const b = en[key as keyof typeof en];
      expect(b).toBeTruthy();
      expect(b.match(/\{\w+\}/g) ?? []).toEqual(a.match(/\{\w+\}/g) ?? []);
    }
  });
  it("vertaalt elke auditactie afzonderlijk, ook zonder bruikbare serverzin", () => {
    expect(new Set(Object.values(MACHINE_AUDIT_KEYS)).size).toBe(5);
    for (const [action, key] of Object.entries(MACHINE_AUDIT_KEYS)) {
      expect(nlLogs[key]).toBeTruthy();
      expect(enLogs[key]).toBeTruthy();
      expect(enLogs[key]).not.toBe(nlLogs[key]);
      expect(localizeAuditSummary("ruwe serverzin", action)).toBe(nlLogs[key]);
    }
  });
});
