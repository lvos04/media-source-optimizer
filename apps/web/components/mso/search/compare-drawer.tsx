"use client";

import { GitCompareArrows, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { Button, InlineState, StatusChip } from "@/components/mg/primitives";
import { StateView } from "@/components/mg/states";
import { flagLabel } from "@/components/mso/search/parts";
import { api, errorMessage } from "@/lib/api/client";
import type { CompareResult, MediaCategory } from "@/lib/api/search";
import { useI18n } from "@/lib/i18n";

function fmt(key: string, value: unknown, num: (n: number, d?: number) => string): string {
  if (value === null || value === undefined || value === "") return "—";
  if (key === "resolution" && typeof value === "number") return `${value}p`; // never "1.080"
  if (typeof value === "boolean") return value ? "ja" : "nee";
  if (Array.isArray(value)) return value.length ? value.map(String).join(", ") : "—";
  if (typeof value === "number") return num(value, value % 1 ? 2 : 0);
  return String(value);
}

/** Right-hand drawer: side-by-side comparison of 2–6 cached search results, optionally tested against
 * quality requirements (MediaGuard's requirements stay leading; MSO never relaxes them). */
export function CompareDrawer({ tokens, category, onClose }: { tokens: string[]; category: MediaCategory | null; onClose: () => void }) {
  const { t, bytes, num } = useI18n();
  const [data, setData] = useState<CompareResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [minRes, setMinRes] = useState<number | "">("");
  const [dv, setDv] = useState(false);
  const [dual, setDual] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  const load = useCallback(async (withReq: boolean) => {
    setPending(true);
    setError(null);
    try {
      const requirements = withReq && category ? {
        media_category: category, min_resolution: minRes === "" ? null : minRes, require_hdr: dv ? ["dv"] : [], require_dual_audio: dual,
      } : undefined;
      setData(await api.post<CompareResult>("/search/compare", { tokens, requirements }));
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setPending(false);
    }
  }, [category, dual, dv, minRes, tokens]);

  useEffect(() => { void load(false); // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tokens.join(",")]);
  useEffect(() => {
    ref.current?.focus();
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <>
      <div className="drawer-overlay" onClick={onClose} aria-hidden="true" />
      <div className="drawer drawer--right" ref={ref} tabIndex={-1} role="dialog" aria-modal="true" aria-labelledby="compare-title"
        style={{ width: "min(760px, 96vw)", display: "flex", flexDirection: "column", gap: 12, padding: 16 }}>
        <div className="row-between">
          <h2 className="card-title" id="compare-title"><GitCompareArrows className="ico" aria-hidden="true" />{t("zoeken.compareTitle")}</h2>
          <Button size="sm" variant="ghost" icon={X} iconOnly onClick={onClose}>{t("action.close")}</Button>
        </div>
        {category ? (
          <div className="row wrap" style={{ gap: 10, alignItems: "flex-end" }}>
            <label className="stack-sm" htmlFor="cmp-minres">
              <span className="label">{t("zoeken.compareMinRes")}</span>
              <select id="cmp-minres" className="select input--sm" value={minRes} onChange={(e) => setMinRes(e.target.value ? Number(e.target.value) : "")}>
                <option value="">—</option>
                {[720, 1080, 2160].map((r) => <option key={r} value={r}>{r}p</option>)}
              </select>
            </label>
            <label className="row" style={{ gap: 6 }}><input type="checkbox" className="check" checked={dv} onChange={(e) => setDv(e.target.checked)} />{t("zoeken.compareDv")}</label>
            <label className="row" style={{ gap: 6 }}><input type="checkbox" className="check" checked={dual} onChange={(e) => setDual(e.target.checked)} />{t("zoeken.compareDual")}</label>
            <Button size="sm" pending={pending} onClick={() => void load(true)}>{t("zoeken.compareRun")}</Button>
          </div>
        ) : null}
        {error ? <InlineState tone="danger" role="alert">{error}</InlineState> : null}
        {!data && !error ? <StateView kind="loading" compact /> : null}
        {data ? (
          <>
            {data.same_identity ? <InlineState tone="info">{t("zoeken.compareSame")}</InlineState> : null}
            <div className="table-wrap" tabIndex={0} role="region" aria-label="Tabel (horizontaal scrollbaar)" style={{ overflowX: "auto" }}>
              <table className="table table--compact">
                <thead>
                  <tr>
                    <th scope="col" style={{ minWidth: 120 }}><span className="sr-only">{t("zoeken.compareField")}</span></th>
                    {data.candidates.map((c) => (
                      // .table th is nowrap + fixed height: let long release names wrap instead of being cut off
                      <th key={c.token} scope="col" title={c.title}
                        style={{ minWidth: 160, maxWidth: 260, whiteSpace: "normal", height: "auto", paddingTop: 8, paddingBottom: 8, verticalAlign: "bottom" }}>
                        <span className="cell-title" style={{ overflowWrap: "anywhere", display: "block", lineHeight: 1.35 }}>{c.title}</span>
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {data.candidates.some((c) => c.rejections !== null) ? (
                    <tr>
                      <th scope="row">{t("zoeken.compareReq")}</th>
                      {data.candidates.map((c) => (
                        <td key={c.token}>
                          {c.rejections && c.rejections.length === 0
                            ? <StatusChip tone="success">{t("zoeken.compareOk")}</StatusChip>
                            : <><StatusChip tone="neutral" icon={X}>{t("zoeken.compareRejected")}</StatusChip><div className="cell-sub">{c.rejections?.join(" · ")}</div></>}
                        </td>
                      ))}
                    </tr>
                  ) : null}
                  {data.fields.map((f) => {
                    const differs = data.differs.includes(f.key);
                    return (
                      <tr key={f.key} style={differs ? { background: "color-mix(in srgb, var(--mg-accent) 7%, transparent)" } : undefined}>
                        <th scope="row">{f.label}{differs ? <span className="sr-only"> ({t("zoeken.compareDiffers")})</span> : null}</th>
                        {data.candidates.map((c) => (
                          <td key={c.token} className={f.key === "source_label" ? undefined : "mono"}>
                            {f.key === "size_bytes" && typeof c.fields[f.key] === "number" ? bytes(c.fields[f.key] as number)
                              : f.key === "false_positive_flags" && Array.isArray(c.fields[f.key])
                                ? fmt(f.key, (c.fields[f.key] as string[]).map((x) => flagLabel(x, t)), num)
                                : fmt(f.key, c.fields[f.key], num)}
                          </td>
                        ))}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <p className="hint">{data.note}</p>
          </>
        ) : null}
      </div>
    </>
  );
}
