#!/usr/bin/env node
// Driftcontrole van de versie-gepinde Peil-kopieën (O-14, ONTWERPTAAL §14).
//
// Elke kopie in styles/peil/ heeft een herkomstkop van 2 regels met "Peil <versie>" en de sha256 van het bronbestand
// (zonder kop). Deze controle faalt als
//   1. de inhoud onder de kop niet (meer) die sha256 heeft (iemand heeft de kopie met de hand gewijzigd), of
//   2. de repo-kopie in design/ontwerptaal/ (indien aanwezig: niet in de Docker-context) een andere inhoud of versie heeft.
// Gebruik: node scripts/check-peil.mjs   (npm run check:peil)
import { createHash } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const web = path.resolve(here, "..");
const design = path.resolve(web, "../../design/ontwerptaal");

export const PEIL_VERSION = "1.1.0";
const FILES = [
  { copy: "styles/peil/ontwerptaal.css", source: "ontwerptaal.css" },
  { copy: "styles/peil/mediaguard-mso.css", source: "aliaslagen/mediaguard-mso.css" },
];

/** Splits a provenance-headed file into {version, sha, body}; the header is always the first two lines. */
export function parseHeaded(text) {
  const lines = text.split("\n");
  const head = lines.slice(0, 2).join("\n");
  const version = /Peil (\d+\.\d+\.\d+)/.exec(head)?.[1] ?? null;
  const sha = /sha256[^:]*:\s*([0-9a-f]{64})/.exec(head)?.[1] ?? null;
  return { version, sha, body: lines.slice(2).join("\n") };
}

const sha256 = (s) => createHash("sha256").update(s).digest("hex");

function main() {
  const errors = [];
  for (const f of FILES) {
    const file = path.join(web, f.copy);
    const copy = parseHeaded(readFileSync(file, "utf8"));
    if (copy.version !== PEIL_VERSION) errors.push(`${f.copy}: herkomstkop noemt Peil ${copy.version ?? "?"}, verwacht ${PEIL_VERSION}`);
    if (!copy.sha) errors.push(`${f.copy}: geen sha256 in de herkomstkop`);
    else if (sha256(copy.body) !== copy.sha) errors.push(`${f.copy}: inhoud wijkt af van de sha256 in de herkomstkop (met de hand gewijzigd?)`);
    const src = path.join(design, f.source);
    if (existsSync(src)) {
      const ref = parseHeaded(readFileSync(src, "utf8"));
      if (ref.body !== copy.body) errors.push(`${f.copy}: verschilt van design/ontwerptaal/${f.source}`);
      if (ref.version !== copy.version) errors.push(`${f.copy}: versie ${copy.version} ≠ design/ontwerptaal ${ref.version}`);
    }
    if (!errors.length) console.log(`ok  ${f.copy}  Peil ${copy.version}  sha256 ${copy.sha}`);
  }
  if (errors.length) {
    for (const e of errors) console.error(`FOUT ${e}`);
    process.exit(1);
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) main();
