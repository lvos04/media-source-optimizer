import { readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { FILES, PEIL_COMMIT, PEIL_REPO, PEIL_VERSION, checkCopy, parseHeaded, sha256 } from "../../scripts/check-peil.mjs";

// Pin- en driftregel van de Peil-kopieën (ONTWERPTAAL §14, E08): versie + bron-commit + sha256 in de herkomstkop.
const web = path.resolve(import.meta.dirname, "../..");
const pin = JSON.parse(readFileSync(path.resolve(web, "../../design/ontwerptaal/HERKOMST.json"), "utf8")) as {
  versie: string; bron_commit: string; bestanden: Array<{ kopie: string; sha256: string }>;
};
const expectFor = (f: (typeof FILES)[number]) => ({
  version: PEIL_VERSION, commit: PEIL_COMMIT, repo: PEIL_REPO, source: f.source,
  sha: pin.bestanden.find((b) => b.kopie === `apps/web/${f.copy}`)?.sha256,
});

describe("Peil-kopieën in apps/web (1.1.1)", () => {
  it("het script en de pin noemen dezelfde versie en bron-commit", () => {
    expect(PEIL_VERSION).toBe("1.1.1");
    expect(pin.versie).toBe(PEIL_VERSION);
    expect(pin.bron_commit).toBe(PEIL_COMMIT);
  });

  it.each(FILES)("$copy heeft een geldige kop en de gepinde inhoud", (f) => {
    const text = readFileSync(path.join(web, f.copy), "utf8");
    expect(checkCopy(f.copy, text, expectFor(f))).toEqual([]);
    const c = parseHeaded(text);
    expect(sha256(c.body)).toBe(c.sha);
    expect(c.body).toContain(f.copy.endsWith("ontwerptaal.css") ? "Peil 1.1.1 — ontwerptaal" : "Peil 1.1.1 — aliaslaag");
  });

  describe("negatieve gevallen (moeten falen)", () => {
    const f = FILES[0];
    const good = readFileSync(path.join(web, f.copy), "utf8");
    const lines = good.split("\n");

    it("met de hand gewijzigde inhoud onder de kop", () => {
      const bad = good.replace("--domain-heat-soft: #FFC3A4;", "--domain-heat-soft: #F4A57E;");
      expect(bad).not.toBe(good);
      expect(checkCopy(f.copy, bad, expectFor(f)).join("\n")).toMatch(/inhoud wijkt af van de sha256/);
    });
    it("kop met een andere versie", () => {
      const bad = [lines[0].replace("Peil 1.1.1", "Peil 1.1.0"), ...lines.slice(1)].join("\n");
      expect(checkCopy(f.copy, bad, expectFor(f)).join("\n")).toMatch(/noemt Peil 1\.1\.0, verwacht 1\.1\.1/);
    });
    it("kop met een andere bron-commit", () => {
      const bad = [lines[0].replace(PEIL_COMMIT, "f".repeat(40)), ...lines.slice(1)].join("\n");
      expect(checkCopy(f.copy, bad, expectFor(f)).join("\n")).toMatch(/bron-commit f{40}/);
    });
    it("sha256 in de kop past bij de inhoud maar niet bij de pin", () => {
      const body = lines.slice(2).join("\n") + "\n/* extra */";
      const bad = [lines[0].replace(/[0-9a-f]{64}$/, sha256(body)), lines[1], ...body.split("\n")].join("\n");
      const errors = checkCopy(f.copy, bad, expectFor(f)).join("\n");
      expect(errors).not.toMatch(/inhoud wijkt af/);
      expect(errors).toMatch(/≠ pin/);
    });
    it("geen herkomstkop", () => {
      const errors = checkCopy(f.copy, lines.slice(2).join("\n"), expectFor(f)).join("\n");
      expect(errors).toMatch(/geen geldige herkomstkop/);
      expect(errors).toMatch(/geen sha256/);
    });
  });
});
