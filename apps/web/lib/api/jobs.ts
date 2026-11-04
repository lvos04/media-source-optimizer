"use client";

import { api } from "@/lib/api/client";
import type { Job } from "@/lib/api/contracts";
import { jobFinished } from "@/lib/status";

/**
 * Poll `GET /api/v1/jobs/{id}` until the job is done / failed / cancelled (or the timeout passes).
 * Resolves with the last job row seen; `timedOut` is true when it never finished in time.
 * Network errors while polling are tolerated (the job keeps running server-side).
 */
export async function waitForJob(
  id: string | number,
  { intervalMs = 1500, timeoutMs = 10 * 60_000, onUpdate, signal }: { intervalMs?: number; timeoutMs?: number; onUpdate?: (job: Job) => void; signal?: AbortSignal } = {},
): Promise<{ job: Job | null; timedOut: boolean }> {
  const deadline = Date.now() + timeoutMs;
  let last: Job | null = null;
  while (Date.now() < deadline) {
    if (signal?.aborted) break;
    try {
      last = await api.get<Job>(`/jobs/${encodeURIComponent(String(id))}`);
      onUpdate?.(last);
      if (jobFinished(last.status)) return { job: last, timedOut: false };
    } catch {
      // transient error: keep polling until the deadline
    }
    await new Promise((r) => window.setTimeout(r, intervalMs));
  }
  return { job: last, timedOut: true };
}
