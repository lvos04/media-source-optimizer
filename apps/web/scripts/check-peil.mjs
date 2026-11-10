#!/usr/bin/env node
// Driftcontrole van de versie-gepinde Peil-kopieën in de Docker-context apps/web (O-14, ONTWERPTAAL §14, regel E08).
//
// Elke kopie in styles/peil/ heeft een herkomstkop van 2 regels:
//   /* HERKOMST — Peil <versie> · bron: lvos04/ai-server-manager@<commit> <pad> · sha256 van het bronbestand (zonder deze kop): <sha>
//      <toelichting> */
// Deze controle faalt als
//   1. de kop niet Peil PEIL_VERSION en bron-commit PEIL_COMMIT noemt, of de inhoud onder de kop een andere sha256 heeft
//      (iemand heeft de kopie met de hand gewijzigd);
//   2. de pin design/ontwerptaal/HERKOMST.json (indien aanwezig: niet in de Docker-context) een andere versie, commit
//      of sha256 noemt, of de repo-kopie in design/ontwerptaal/ een andere inhoud heeft.
// De vergelijking met de bron zelf (ai-server-manager) doet scripts/bronkopie.py vergelijk (CI, job python).
// Gebruik: node scripts/check-peil.mjs   (npm run check:peil)
import { createHash } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const web = path.resolve(here, "..");
const design = path.resolve(web, "../../design/ontwerptaal");

export const PEIL_VERSION = "1.1.1";
export const PEIL_COMMIT = "0a7143bc0a94ed5317a9655fa785e73a1fb07ba1";
export const PEIL_REPO = "lvos04/ai-server-manager";
export const FILES = [
  { copy: "styles/peil/ontwerptaal.css", design: "ontwerptaal.css", source: "design/ontwerptaal/ontwerptaal.css" },
  { copy: "styles/peil/mediaguard-mso.css", design: "aliaslagen/mediaguard-mso.css", source: "design/ontwerptaal/aliaslagen/mediaguard-mso.css" },
];

const HEAD = /HERKOMST — Peil (\d+\.\d+\.\d+) · bron: ([\w.-]+\/[\w.-]+)@([0-9a-f]{40}) (\S+) · sha256 van het bronbestand \(zonder deze kop\): ([0-9a-f]{64})/;

/** Splits a provenance-headed file into its header fields and body; the header is always the first two lines. */
export function parseHeaded(text) {
  const lines = text.split("\n");
  const m = HEAD.exec(lines[0] ?? "");
  return {
    version: m?.[1] ?? null, repo: m?.[2] ?? null, commit: m?.[3] ?? null, source: m?.[4] ?? null, sha: m?.[5] ?? null,
    wellFormed: Boolean(m) && (lines[0] ?? "").startsWith("/* ") && !(lines[0] ?? "").includes("*/") && (lines[1] ?? "").endsWith(" */"),
    body: lines.slice(2).join("\n"),
  };
}

export const sha256 = (s) => createHash("sha256").update(s).digest("hex");

/** All problems of one copy (empty list = in orde). `expect` = {version, commit, repo, source, sha?}. */
export function checkCopy(name, text, expect) {
  const errors = [];
  const c = parseHeaded(text);
  if (!c.wellFormed) errors.push(`${name}: geen geldige herkomstkop van 2 regels`);
  if (c.version !== expect.version) errors.push(`${name}: herkomstkop noemt Peil ${c.version ?? "?"}, verwacht ${expect.version}`);
  if (c.commit !== expect.commit) errors.push(`${name}: herkomstkop noemt bron-commit ${c.commit ?? "?"}, verwacht ${expect.commit}`);
  if (c.repo !== expect.repo) errors.push(`${name}: herkomstkop noemt bron ${c.repo ?? "?"}, verwacht ${expect.repo}`);
  if (c.source !== expect.source) errors.push(`${name}: herkomstkop noemt bronpad ${c.source ?? "?"}, verwacht ${expect.source}`);
  if (!c.sha) errors.push(`${name}: geen sha256 in de herkomstkop`);
  else if (sha256(c.body) !== c.sha) errors.push(`${name}: inhoud wijkt af van de sha256 in de herkomstkop (met de hand gewijzigd?)`);
  if (expect.sha && c.sha !== expect.sha) errors.push(`${name}: sha256 ${c.sha ?? "?"} ≠ pin ${expect.sha}`);
  return errors;
}

function main() {
  const errors = [];
  const pinFile = path.join(design, "HERKOMST.json");
  const pin = existsSync(pinFile) ? JSON.parse(readFileSync(pinFile, "utf8")) : null;
  if (pin && (pin.versie !== PEIL_VERSION || pin.bron_commit !== PEIL_COMMIT)) {
    errors.push(`design/ontwerptaal/HERKOMST.json pint Peil ${pin.versie} @ ${pin.bron_commit}, dit script ${PEIL_VERSION} @ ${PEIL_COMMIT}`);
  }
  for (const f of FILES) {
    const text = readFileSync(path.join(web, f.copy), "utf8");
    const pinned = pin?.bestanden?.find((b) => b.kopie === `apps/web/${f.copy}`);
    if (pin && !pinned) errors.push(`${f.copy}: staat niet in design/ontwerptaal/HERKOMST.json`);
    const own = checkCopy(f.copy, text, { version: PEIL_VERSION, commit: PEIL_COMMIT, repo: PEIL_REPO, source: f.source, sha: pinned?.sha256 });
    errors.push(...own);
    const src = path.join(design, f.design);
    if (existsSync(src)) {
      const ref = parseHeaded(readFileSync(src, "utf8"));
      const copy = parseHeaded(text);
      if (ref.body !== copy.body) errors.push(`${f.copy}: verschilt van design/ontwerptaal/${f.design}`);
      if (ref.version !== copy.version || ref.commit !== copy.commit) errors.push(`${f.copy}: versie/commit ≠ design/ontwerptaal/${f.design}`);
    }
    if (!own.length) console.log(`ok  ${f.copy}  Peil ${PEIL_VERSION} @ ${PEIL_COMMIT.slice(0, 12)}  sha256 ${parseHeaded(text).sha}`);
  }
  if (errors.length) {
    for (const e of errors) console.error(`FOUT ${e}`);
    process.exit(1);
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) main();
