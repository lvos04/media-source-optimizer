// "Terug naar de Hub" (Peil O-11, ADR-0006 punt 1): the address comes from the optional setting `systeem.hub_url`
// (GET /api/v1/settings). The UI shows the link ONLY for a value that passes the same rules as the core validator
// (contract 1.2 §3.2, HUB_URL): http(s)://<host>[:<port>] without path, query, fragment or credentials; never
// loopback, link-local or 0.0.0.0/::. Anything else → no link in the DOM. There is never a default or example address.
export const HUB_URL_SETTING = "systeem.hub_url";

function blockedHost(hostname: string): boolean {
  const h = hostname.replace(/^\[|\]$/g, "").toLowerCase().replace(/\.$/, "");
  if (h === "localhost" || h.endsWith(".localhost")) return true;
  // IPv4 (URL normalises shorthand forms like 127.1 to dotted quads)
  const v4 = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/.exec(h);
  if (v4) {
    const [a, b] = [Number(v4[1]), Number(v4[2])];
    return a === 127 || a === 0 || (a === 169 && b === 254);
  }
  if (h.includes(":")) {
    if (h === "::" || h === "::1" || /^0*:(0*:)*0*1?$/.test(h)) return true;
    if (/^fe[89ab][0-9a-f]?:/.test(h)) return true; // fe80::/10
    if (/^::ffff:(127|0)\./.test(h) || /^::ffff:7f/.test(h)) return true;
  }
  return false;
}

/** Returns the normalised Hub URL (scheme://host[:port]) or null when the value must not produce a link. */
export function hubUrl(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const raw = value.trim();
  if (!raw || /\s|\\/.test(raw)) return null;
  let u: URL;
  try { u = new URL(raw); } catch { return null; }
  if (u.protocol !== "http:" && u.protocol !== "https:") return null;
  if (!u.hostname || u.username || u.password) return null;
  if (u.pathname !== "/" || u.search || u.hash) return null;
  // `new URL` drops an empty "?"/"#": reject those explicitly as the core does
  if (/[?#]/.test(raw)) return null;
  if (blockedHost(u.hostname)) return null;
  return `${u.protocol}//${u.host}`;
}
