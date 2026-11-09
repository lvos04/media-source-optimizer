import { describe, expect, it } from "vitest";

import { CHANGE_STATUS, healthLabel, healthTone, isHealthProblem, jobLabel, jobTone, peilStatusText } from "@/lib/status";

// Peil §4.1: green (`healthy`) only for MEASURED health; "connected" is a link state; unknown is never "neutral/pause".
describe("health → Peil", () => {
  it("only measured health is healthy", () => {
    for (const s of ["healthy", "ok", "online"]) expect(healthTone(s)).toBe("healthy");
    expect(healthTone("connected")).toBe("linked");
    expect(healthTone("degraded")).toBe("warning");
    expect(healthTone("auth_failed")).toBe("danger");
    expect(healthTone("not_configured")).toBe("unlinked");
    expect(healthTone("disabled")).toBe("neutral");
    for (const s of ["", null, undefined, "unknown", "iets-anders"]) expect(healthTone(s)).toBe("unknown");
  });
  it("uses the fixed Peil words", () => {
    expect(healthLabel("degraded")).toBe("status.degraded");
    expect(healthLabel("offline")).toBe("status.offline");
    expect(healthLabel("connected")).toBe("status.linked");
    expect(healthLabel(undefined)).toBe("status.unknown");
  });
  it("counts everything that is not measured healthy as a problem, except link state and 'disabled'", () => {
    expect(isHealthProblem("healthy")).toBe(false);
    expect(isHealthProblem("connected")).toBe(false);
    expect(isHealthProblem("disabled")).toBe(false);
    expect(isHealthProblem("degraded")).toBe(true);
    expect(isHealthProblem("unknown")).toBe(true);
  });
});

describe("jobs and changes are never green", () => {
  it("a finished job is a neutral 'Afgerond'", () => {
    expect(jobTone("done")).toBe("success");
    expect(jobLabel("done")).toBe("status.succeeded");
    expect(jobTone("failed")).toBe("danger");
    expect(jobTone("retry_scheduled")).toBe("neutral");
  });
  it("change statuses map to Peil axes", () => {
    expect(CHANGE_STATUS.applied.tone).toBe("success");
    expect(CHANGE_STATUS.approved.tone).toBe("approval");
    expect(CHANGE_STATUS.applying.tone).toBe("accent");
    for (const v of Object.values(CHANGE_STATUS)) expect(v.tone).not.toBe("healthy");
  });
});

describe("peilStatusText — oude statuswoorden uit backendteksten", () => {
  const t = (k: string) => ({ "status.degraded": "Beperkt", "status.offline": "Storing" })[k] ?? k;
  it("vervangt alleen een los statuswoord achter de naam", () => {
    expect(peilStatusText("Prowlarr: Verstoord", t)).toBe("Prowlarr: Beperkt");
    expect(peilStatusText("SABnzbd: Offline", t)).toBe("SABnzbd: Storing");
  });
  it("laat zinnen en andere teksten ongemoeid", () => {
    expect(peilStatusText("Prowlarr onbereikbaar sinds 10:12", t)).toBe("Prowlarr onbereikbaar sinds 10:12");
    expect(peilStatusText("2 mislukte taak/taken (24 u)", t)).toBe("2 mislukte taak/taken (24 u)");
    expect(peilStatusText("Verstoord verkeer: Prowlarr", t)).toBe("Verstoord verkeer: Prowlarr");
  });
});
