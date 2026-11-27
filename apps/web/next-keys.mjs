import { createHmac, randomBytes, randomUUID } from "node:crypto";
import {
  closeSync, constants, fstatSync, linkSync, lstatSync, mkdirSync, openSync, readFileSync,
  renameSync, unlinkSync, writeFileSync,
} from "node:fs";
import path from "node:path";

// Elke container heeft een eigen /tmp, ook bij een alleen-lezen rootbestandssysteem.
export const runtimeDir = "/tmp/mso-next";
export const routeTemplate = "mso-prerender.json";
export const manifests = [
  "prerender-manifest.json", "server/preview-props.json",
  "server/server-reference-manifest.json", "server/server-reference-manifest.js",
];
export const jsPrefix = "self.__RSC_SERVER_MANIFEST=";

function privateDirectory(dir) {
  mkdirSync(dir, { recursive: true, mode: 0o700 });
  const st = lstatSync(dir);
  if (!st.isDirectory() || st.uid !== process.getuid() || (st.mode & 0o077) !== 0) {
    throw new Error("De map voor de websleutels moet privé zijn en eigendom van de webgebruiker.");
  }
}

function readKey(file) {
  const fd = openSync(file, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
  try {
    const st = fstatSync(fd);
    if (!st.isFile() || st.uid !== process.getuid() || (st.mode & 0o7777) !== 0o600 || st.size !== 44) {
      throw new Error("Ongeldige websleutel of bestandsrechten. Verwacht een eigen bestand met modus 0600.");
    }
    const encoded = readFileSync(fd, "utf8");
    const key = Buffer.from(encoded, "base64");
    if (key.length !== 32 || key.toString("base64") !== encoded) {
      throw new Error("De opgeslagen websleutel is ongeldig.");
    }
    return key;
  } finally {
    closeSync(fd);
  }
}

function installationKey(dataDir) {
  const dir = path.join(dataDir, "web");
  privateDirectory(dir);
  const file = path.join(dir, "server-actions.key");
  try {
    return readKey(file);
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
  }

  // Publiceer pas na de volledige schrijf. Twee gelijktijdige starts gebruiken de winnaar.
  const candidate = path.join(dir, `.${randomUUID()}.key`);
  writeFileSync(candidate, randomBytes(32).toString("base64"), { flag: "wx", mode: 0o600, flush: true });
  try {
    linkSync(candidate, file);
  } catch (error) {
    if (error.code !== "EEXIST") throw error;
  } finally {
    unlinkSync(candidate);
  }
  return readKey(file);
}

export function prepareManifests(nextDir, dataDir, runtime = runtimeDir) {
  const routes = JSON.parse(readFileSync(path.join(nextDir, routeTemplate), "utf8"));
  privateDirectory(runtime);
  const key = installationKey(dataDir);
  const derive = (purpose) => createHmac("sha256", key).update(purpose).digest();
  const preview = {
    previewModeId: derive("preview-id").subarray(0, 16).toString("hex"),
    previewModeSigningKey: derive("preview-ondertekening").toString("hex"),
    previewModeEncryptionKey: derive("preview-versleuteling").toString("hex"),
  };
  const actions = { node: {}, edge: {}, encryptionKey: key.toString("base64") };
  const contents = [
    JSON.stringify({ ...routes, preview }), JSON.stringify(preview),
    JSON.stringify(actions), jsPrefix + JSON.stringify(JSON.stringify(actions)),
  ];
  for (const [index, name] of manifests.entries()) {
    const temp = path.join(runtime, `.${randomUUID()}`);
    writeFileSync(temp, contents[index], { flag: "wx", mode: 0o600 });
    renameSync(temp, path.join(runtime, path.basename(name)));
  }
  return actions.encryptionKey;
}
