// Derived from MediaGuard Ultimate (lvos04/media-guard-ultimate@e92c2acc) apps/web/lib/api/hooks.ts — internally versioned copy, see PROVENANCE.md
"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, coreRequest, type RequestOptions } from "./client";

export type ResourceState<T> =
  | { status: "loading"; data: null; error: null }
  | { status: "ok"; data: T; error: null }
  | { status: "error"; data: T | null; error: ApiError };

export interface Resource<T> {
  state: ResourceState<T>;
  data: T | null;
  error: ApiError | null;
  loading: boolean;
  /** True when we have data but the last refresh failed (stale view). */
  stale: boolean;
  fetchedAt: number | null;
  reload: () => Promise<void>;
  setData: (updater: (old: T | null) => T | null) => void;
}

function asApiError(error: unknown, path: string): ApiError {
  if (error instanceof ApiError) return error;
  return new ApiError("server", 0, error instanceof Error ? error.message : String(error), path);
}

/**
 * Load a core resource. `path = null` disables the request (e.g. waiting for a parameter).
 * `refreshMs` re-polls; SSE-driven pages call `reload()` on matching topics instead.
 */
export function useResource<T>(path: string | null, query?: RequestOptions["query"], refreshMs?: number): Resource<T> {
  const [state, setState] = useState<ResourceState<T>>({ status: "loading", data: null, error: null });
  const [fetchedAt, setFetchedAt] = useState<number | null>(null);
  const queryKey = JSON.stringify(query ?? {});
  const controller = useRef<AbortController | null>(null);

  const load = useCallback(async () => {
    if (!path) return;
    controller.current?.abort();
    const ac = new AbortController();
    controller.current = ac;
    try {
      const data = await coreRequest<T>(path, { query: JSON.parse(queryKey) as RequestOptions["query"], signal: ac.signal });
      if (ac.signal.aborted) return;
      setState({ status: "ok", data, error: null });
      setFetchedAt(Date.now());
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") return;
      setState((old) => ({ status: "error", data: old.data, error: asApiError(error, path) }));
    }
  }, [path, queryKey]);

  useEffect(() => {
    if (!path) return;
    setState((old) => (old.data === null ? { status: "loading", data: null, error: null } : old));
    void load();
    return () => controller.current?.abort();
  }, [load, path]);

  useEffect(() => {
    if (!refreshMs || !path) return;
    const id = window.setInterval(() => void load(), refreshMs);
    return () => window.clearInterval(id);
  }, [load, path, refreshMs]);

  const setData = useCallback((updater: (old: T | null) => T | null) => {
    setState((old) => {
      const next = updater(old.data);
      return next === null ? old : { status: "ok", data: next, error: null };
    });
  }, []);

  return {
    state,
    data: state.data,
    error: state.error,
    loading: state.status === "loading",
    stale: state.status === "error" && state.data !== null,
    fetchedAt,
    reload: load,
    setData,
  };
}

export interface Mutation<A extends unknown[], R> {
  run: (...args: A) => Promise<R | undefined>;
  pending: boolean;
  error: ApiError | null;
  reset: () => void;
}

/** Wrap a mutation: tracks pending/error; never throws (returns undefined on failure). */
export function useMutation<A extends unknown[], R>(fn: (...args: A) => Promise<R>): Mutation<A, R> {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const run = useCallback(async (...args: A) => {
    setPending(true);
    setError(null);
    try {
      return await fn(...args);
    } catch (e) {
      setError(asApiError(e, "mutation"));
      return undefined;
    } finally {
      setPending(false);
    }
  }, [fn]);
  return { run, pending, error, reset: () => setError(null) };
}
