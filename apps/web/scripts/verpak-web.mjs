import { cpSync, existsSync, readFileSync, readdirSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { isDeepStrictEqual } from "node:util";
import ts from "typescript";
import { jsPrefix, manifests, routeTemplate, runtimeDir } from "../next-keys.mjs";

function* files(dir, skip = new Set()) {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    if (skip.has(entry.name)) continue;
    const file = path.join(dir, entry.name);
    if (entry.isDirectory()) yield* files(file, skip);
    else if (entry.isFile()) yield file;
  }
}

export function checkWebSource(root) {
  const skip = new Set([".next", ".git", "node_modules", "tests", "scripts", "e2e", "public", "playwright-report", "test-results"]);
  const previewApis = new Set(["draftMode", "previewData", "setPreviewData", "clearPreviewData", "__prerender_bypass", "__next_preview_data"]);
  for (const file of files(root, skip)) {
    if (!/\.[cm]?[jt]sx?$/.test(file)) continue;
    const source = ts.createSourceFile(file, readFileSync(file, "utf8"), ts.ScriptTarget.Latest);
    function visit(node) {
      if ((ts.isIdentifier(node) || ts.isStringLiteralLike(node)) && previewApis.has(node.text)) {
        throw new Error(`Next.js-preview in ${path.relative(root, file)}. Beoordeel het sleutelgebruik vóór publicatie.`);
      }
      if (ts.isExpressionStatement(node) && node.expression.text === "use server") {
        throw new Error(`Server Action in ${path.relative(root, file)}. Beoordeel het sleutelgebruik vóór publicatie.`);
      }
      ts.forEachChild(node, visit);
    }
    visit(source);
  }
}

function parseManifest(text) {
  try {
    return JSON.parse(text);
  } catch {
    throw new Error("Ongeldige JSON in een Next.js-manifest.");
  }
}

export function removeBuildKeys(bundle, runtime = runtimeDir) {
  const nextDir = path.join(bundle, ".next");
  const paths = manifests.map((name) => path.join(nextDir, name));
  const [routesFile, previewFile, actionsFile, jsFile] = paths;
  const routes = parseManifest(readFileSync(routesFile, "utf8"));
  const actions = parseManifest(readFileSync(actionsFile, "utf8"));
  if (Object.keys(actions.node).length || Object.keys(actions.edge).length) {
    throw new Error("Het productiemanifest registreert een Server Action. Beoordeel het sleutelgebruik vóór publicatie.");
  }
  const js = readFileSync(jsFile, "utf8").trim().replace(/;$/, "");
  if (!js.startsWith(jsPrefix) || !isDeepStrictEqual(parseManifest(parseManifest(js.slice(jsPrefix.length))), actions)
      || !isDeepStrictEqual(Object.keys(actions).sort(), ["edge", "encryptionKey", "node"])
      || !isDeepStrictEqual(Object.keys(routes.preview).sort(), ["previewModeEncryptionKey", "previewModeId", "previewModeSigningKey"])
      || (existsSync(previewFile) && !isDeepStrictEqual(parseManifest(readFileSync(previewFile, "utf8")), routes.preview))) {
    throw new Error("Onverwachte indeling van de Next.js-manifesten.");
  }
  const buildValues = [...Object.values(routes.preview), actions.encryptionKey];
  if (buildValues.some((value) => typeof value !== "string" || value.length < 16)) {
    throw new Error("Onverwachte indeling van de Next.js-buildsleutels.");
  }
  delete routes.preview;
  writeFileSync(path.join(nextDir, routeTemplate), JSON.stringify(routes));
  for (const file of paths) {
    rmSync(file, { force: true }); // preview-props.json is optioneel in standalone-uitvoer.
    symlinkSync(path.join(runtime, path.basename(file)), file);
  }
  // Scan de volledige inhoud die de laatste Docker-stage kopieert, inclusief chunks, assets en caches.
  for (const file of files(bundle)) {
    const contents = readFileSync(file);
    if (buildValues.some((value) => contents.includes(value))) {
      throw new Error(`Next.js-buildsleutel achtergebleven in ${path.relative(bundle, file)}.`);
    }
    // De manifesten per route mogen uitsluitend lege actietabellen bevatten.
    if (path.basename(file) === "server-reference-manifest.json") {
      const routeActions = parseManifest(contents.toString());
      if (!isDeepStrictEqual(routeActions, { node: {}, edge: {} })) {
        throw new Error("Een routemanifest bevat een Server Action of een extra buildsleutel.");
      }
    }
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const bundle = ".next/standalone";
  checkWebSource(".");
  for (const file of ["server.mjs", "next-keys.mjs", "public", ".next/static"]) {
    cpSync(file, path.join(bundle, file), { recursive: true });
  }
  for (const dir of ["node_modules/@img", "node_modules/sharp", ".next/cache"]) {
    rmSync(path.join(bundle, dir), { recursive: true, force: true });
  }
  removeBuildKeys(bundle);
  console.log("Webpakket gecontroleerd: nul Server Actions en geen Next.js-buildsleutels.");
}
