// Types for scripts/check-peil.mjs (used by tests/unit/check-peil.test.ts).
export declare const PEIL_VERSION: string;
export declare const PEIL_COMMIT: string;
export declare const PEIL_REPO: string;
export declare const FILES: Array<{ copy: string; design: string; source: string }>;
export interface Headed {
  version: string | null;
  repo: string | null;
  commit: string | null;
  source: string | null;
  sha: string | null;
  wellFormed: boolean;
  body: string;
}
export declare function parseHeaded(text: string): Headed;
export declare function sha256(s: string): string;
export declare function checkCopy(
  name: string,
  text: string,
  expect: { version: string; commit: string; repo: string; source: string; sha?: string },
): string[];
