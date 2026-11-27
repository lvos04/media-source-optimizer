import { execFile } from "node:child_process";
import { randomBytes } from "node:crypto";
import {
  chmodSync, existsSync, lstatSync, mkdirSync, mkdtempSync, readFileSync, rmSync, statSync, writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { promisify } from "node:util";
import { afterEach, beforeEach, expect, test } from "vitest";
import { jsPrefix, manifests, prepareManifests, routeTemplate } from "../../next-keys.mjs";
import { checkWebSource, removeBuildKeys } from "../../scripts/verpak-web.mjs";

const read = (file) => readFileSync(file, "utf8");
const readJSON = (file) => JSON.parse(read(file));
let work, bundle, nextDir, dataDir, runtime, files, preview, actions;

beforeEach(() => {
  work = mkdtempSync(path.join(tmpdir(), "mso-next-test-"));
  bundle = path.join(work, "pakket");
  nextDir = path.join(bundle, ".next");
  dataDir = path.join(work, "appdata");
  runtime = path.join(work, "runtime");
  files = manifests.map((name) => path.join(nextDir, name));
  mkdirSync(path.join(nextDir, "server"), { recursive: true });
  mkdirSync(dataDir);
  preview = {
    previewModeId: randomBytes(16).toString("hex"),
    previewModeSigningKey: randomBytes(32).toString("hex"),
    previewModeEncryptionKey: randomBytes(32).toString("hex"),
  };
  actions = { node: {}, edge: {}, encryptionKey: randomBytes(32).toString("base64") };
  writeFileSync(files[0], JSON.stringify({ version: 4, routes: { "/login": {} }, preview }));
  writeFileSync(files[2], JSON.stringify(actions));
  writeFileSync(files[3], jsPrefix + JSON.stringify(JSON.stringify(actions)));
});
afterEach(() => rmSync(work, { recursive: true, force: true }));

test.each([true, false])("verpakt routes en start met eigen sleutels (preview-props aanwezig: %s)", (extra) => {
  if (extra) writeFileSync(files[1], JSON.stringify(preview));
  removeBuildKeys(bundle, runtime);
  expect(readJSON(path.join(nextDir, routeTemplate))).toEqual({ version: 4, routes: { "/login": {} } });
  expect(files.every((file) => lstatSync(file).isSymbolicLink() && !existsSync(file))).toBe(true);

  const start = (data) => {
    const key = prepareManifests(nextDir, data, runtime);
    const stored = path.join(data, "web/server-actions.key");
    expect(statSync(stored).mode & 0o777).toBe(0o600);
    expect(read(stored) === key && Buffer.from(key, "base64").length === 32).toBe(true);
    const routes = readJSON(files[0]);
    expect(routes.routes).toEqual({ "/login": {} });
    expect(JSON.stringify(routes.preview) === JSON.stringify(readJSON(files[1]))).toBe(true);
    expect(readJSON(files[2]).encryptionKey === key).toBe(true);
    expect(JSON.parse(read(files[3]).slice(jsPrefix.length)) === read(files[2])).toBe(true);
    expect(files.every((file) => (statSync(file).mode & 0o777) === 0o600)).toBe(true);
    return [...Object.values(routes.preview), key];
  };
  const old = [...Object.values(preview), actions.encryptionKey];
  const first = start(dataDir);
  expect(first.every((value) => !old.includes(value))).toBe(true);
  expect(new Set(first).size).toBe(4);
  rmSync(runtime, { recursive: true });
  expect(start(dataDir).every((value, i) => value === first[i])).toBe(true);
  expect(start(path.join(work, "tweede")).every((value) => !first.includes(value))).toBe(true);
});

test.each(["node", "edge"])("weigert een geregistreerde Server Action voor %s", (kind) => {
  actions[kind].voorbeeld = {};
  writeFileSync(files[2], JSON.stringify(actions));
  expect(() => removeBuildKeys(bundle, runtime)).toThrow(/Server Action/);
});

test.each(["import { draftMode as concept } from 'next/headers'", "res.setPreviewData({})", "'use server'"])(
  "weigert nieuw preview- of actiegebruik", (source) => {
    writeFileSync(path.join(work, "route.ts"), source);
    expect(() => checkWebSource(work)).toThrow(/Next.js-preview|Server Action/);
  },
);

test("weigert een achtergebleven buildsleutel in een extra chunk", () => {
  writeFileSync(path.join(nextDir, "extra.js"), actions.encryptionKey);
  expect(() => removeBuildKeys(bundle, runtime)).toThrow(/buildsleutel achtergebleven/);
});

test("weigert een onafhankelijke sleutel in een routemanifest", () => {
  const route = path.join(nextDir, "server/app/login");
  mkdirSync(route, { recursive: true });
  writeFileSync(path.join(route, "server-reference-manifest.json"),
    JSON.stringify({ node: {}, edge: {}, encryptionKey: randomBytes(32).toString("base64") }));
  expect(() => removeBuildKeys(bundle, runtime)).toThrow(/extra buildsleutel/);
});

test("overschrijft een onleesbare of ongeldige bestaande sleutel niet", () => {
  removeBuildKeys(bundle, runtime);
  prepareManifests(nextDir, dataDir, runtime);
  const file = path.join(dataDir, "web/server-actions.key");
  const initial = read(file);
  chmodSync(file, 0o644);
  expect(() => prepareManifests(nextDir, dataDir, runtime)).toThrow(/0600/);
  expect(read(file) === initial).toBe(true);
  chmodSync(file, 0o600);
  writeFileSync(file, "beschadigd");
  expect(() => prepareManifests(nextDir, dataDir, runtime)).toThrow(/Ongeldige websleutel/);
  expect(read(file)).toBe("beschadigd");
});

test("gelijktijdige eerste starts op dezelfde appmap gebruiken dezelfde sleutel", async () => {
  removeBuildKeys(bundle, runtime);
  const moduleUrl = new URL("../../next-keys.mjs", import.meta.url).href;
  const code = `import { prepareManifests } from ${JSON.stringify(moduleUrl)}; prepareManifests(...process.argv.slice(1));`;
  const dirs = [path.join(work, "runtime-a"), path.join(work, "runtime-b")];
  await Promise.all(dirs.map((dir) => promisify(execFile)(process.execPath, ["--input-type=module", "-e", code, nextDir, dataDir, dir])));
  expect(dirs.every((dir) => readJSON(path.join(dir, "server-reference-manifest.json")).encryptionKey
    === read(path.join(dataDir, "web/server-actions.key")))).toBe(true);
});
