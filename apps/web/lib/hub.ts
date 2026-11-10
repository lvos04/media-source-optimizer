// "Terug naar de Hub" (Peil O-11, ADR-0006 punt 1, ADR-0009): the address comes from the optional setting
// `systeem.hub_url` (GET /api/v1/settings). The UI shows the link ONLY for a value that passes exactly the same rules as
// the core validator (apps/core/mso/settings_registry.py; contract 1.3 §2.3 regel 5 + §3.2): http(s)://<host>[:<port>]
// without path, query, fragment or credentials; IPv4 only as a.b.c.d (four decimal parts 0-255 without a leading zero;
// 127.1, 2130706433, 0x7f000001, 010.0.0.1 and 0 are invalid — a browser would turn them into another address); IPv6
// only between []; one trailing dot of a name does not count; never localhost, loopback, link-local or 0.0.0.0/::.
// Anything else → no link in the DOM (also for a value stored under older rules). There is never a default or example
// address. Shared test vectors with the core: tests/vectoren/hub_url.json.
export const HUB_URL_SETTING = "systeem.hub_url";

const NUMERIC_LABEL = /^(?:0x[0-9a-f]*|[0-9]+)$/;
const NAME_LABEL = /^[a-z0-9_](?:[a-z0-9_-]{0,61}[a-z0-9_])?$/;
const AUTHORITY = /^(\[[^\]]*\]|[^:[\]]*)(?::([0-9]*))?$/;
const CANONICAL_V4 = /^(0|[1-9][0-9]{0,2})\.(0|[1-9][0-9]{0,2})\.(0|[1-9][0-9]{0,2})\.(0|[1-9][0-9]{0,2})$/;

function blockedV4(parts: number[]): boolean {
  const [a, b] = parts;
  return a === 0 || a === 127 || (a === 169 && b === 254) || (a >= 224 && a <= 239) || parts.every((p) => p === 255);
}

/** Canonical IPv4 as four numbers, or null. */
function ipv4(name: string): number[] | null {
  const m = CANONICAL_V4.exec(name);
  if (!m) return null;
  const parts = m.slice(1, 5).map(Number);
  return parts.every((p) => p <= 255) ? parts : null;
}

/** IPv6 literal (without []) → its 8 groups after WHATWG parsing, plus the compressed text; null when invalid. */
function ipv6(inner: string): { groups: number[]; text: string } | null {
  if (inner.includes("%")) return null;
  let host: string;
  try { host = new URL(`http://[${inner}]/`).hostname; } catch { return null; }
  const text = host.slice(1, -1);
  // expand the WHATWG-serialised (compressed, lower-case, hex only) form into 8 groups
  const [head, tail] = text.includes("::") ? text.split("::") : [text, null];
  const h = head ? head.split(":") : [];
  const t = tail ? tail.split(":") : [];
  const fill = tail === null ? [] : Array(8 - h.length - t.length).fill("0");
  const groups = [...h, ...fill, ...t].map((g) => parseInt(g, 16));
  return groups.length === 8 && groups.every((g) => Number.isInteger(g)) ? { groups, text } : null;
}

function blockedV6(g: number[]): boolean {
  const zeroUntil = (n: number) => g.slice(0, n).every((x) => x === 0);
  if (zeroUntil(7) && (g[7] === 0 || g[7] === 1)) return true; // :: and ::1
  if ((g[0] & 0xffc0) === 0xfe80) return true; // fe80::/10
  if ((g[0] & 0xff00) === 0xff00) return true; // multicast
  if (zeroUntil(5) && g[5] === 0xffff) return blockedV4([g[6] >> 8, g[6] & 0xff, g[7] >> 8, g[7] & 0xff]); // ::ffff:a.b.c.d
  return false;
}

/** Normalised host (lower case; IPv6 compressed between []) or null when the host must not produce a link. */
function hubHost(rawHost: string): string | null {
  const h = rawHost.toLowerCase();
  if (h.startsWith("[")) {
    const v6 = ipv6(h.slice(1, -1));
    return v6 && !blockedV6(v6.groups) ? `[${v6.text}]` : null;
  }
  const name = h.endsWith(".") ? h.slice(0, -1) : h; // one trailing dot does not count
  if (!name || name.length > 253) return null;
  const labels = name.split(".");
  if (NUMERIC_LABEL.test(labels[labels.length - 1]) || labels.every((l) => NUMERIC_LABEL.test(l))) {
    const v4 = ipv4(name);
    return v4 && !blockedV4(v4) ? name : null;
  }
  if (!labels.every((l) => NAME_LABEL.test(l))) return null;
  if (name === "localhost" || name.endsWith(".localhost")) return null;
  return name;
}

/** Returns the normalised Hub URL (scheme://host[:port]) or null when the value must not produce a link. */
export function hubUrl(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const raw = value.trim();
  if (!raw || raw.length > 300 || /[\s\\?#@]/.test(raw)) return null;
  const sep = raw.indexOf("://");
  if (sep < 0) return null;
  const scheme = raw.slice(0, sep).toLowerCase();
  if (scheme !== "http" && scheme !== "https") return null;
  const rest = raw.slice(sep + 3);
  const slash = rest.indexOf("/");
  const authority = slash < 0 ? rest : rest.slice(0, slash);
  if (slash >= 0 && rest.slice(slash + 1) !== "") return null;
  const m = AUTHORITY.exec(authority);
  if (!m || !m[1]) return null;
  const host = hubHost(m[1]);
  if (!host) return null;
  const port = m[2] ? Number(m[2]) : null;
  if (port !== null && (port < 1 || port > 65535)) return null;
  return `${scheme}://${host}${port !== null ? `:${port}` : ""}`;
}
