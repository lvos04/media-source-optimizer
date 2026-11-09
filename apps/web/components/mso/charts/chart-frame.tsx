"use client";

// Peil grafiek-kader (ONTWERPTAAL §10, PEIL_MIGRATIE §7 en §9.2 — toegestane toevoeging "tabelweergave en
// toetsenbordbediening bij elke grafiek"):
//  - figure + figcaption (bron · "een gat betekent dat er geen meting was (nooit 0)")
//  - plotvak: tabindex=0, aria-label = samenvatting + toetsenhulp; ←/→ per punt, Home/End, Esc sluit; aanwijzer werkt
//    hetzelfde (tooltip nooit alleen met de muis); voorlezen via één aria-live-regel die ook zichtbaar is
//  - <details> "Toon als tabel" / "Verberg tabel": de WCAG-tweeling met caption, th scope, cijfers rechts
//  - testhaken: data-testid="grafiek-<naam>" en "grafiek-<naam>-tabel"
import { useId, useRef, useState, type KeyboardEvent, type PointerEvent, type ReactNode } from "react";

import { useI18n } from "@/lib/i18n";
import { cn } from "@/lib/utils";

export interface ChartTable {
  caption: string;
  columns: string[];
  /** per column: right-aligned tabular numbers */
  numeric?: boolean[];
  rows: Array<{ key: string; cells: ReactNode[] }>;
}

export function DataTable({ table, testid }: { table: ChartTable; testid?: string }) {
  return (
    <div className="overflow-x-auto" data-testid={testid}>
      <table className="w-full border-collapse text-sm">
        <caption className="mb-1 text-left text-sm font-semibold text-foreground">{table.caption}</caption>
        <thead>
          <tr>{table.columns.map((c, i) => (
            <th key={c} scope="col" className={cn("border-b-2 border-border px-2 py-1.5 font-semibold text-muted-foreground", table.numeric?.[i] ? "text-right" : "text-left")}>{c}</th>
          ))}</tr>
        </thead>
        <tbody>{table.rows.map((r) => (
          <tr key={r.key} className="border-b border-border last:border-b-0">
            {r.cells.map((cell, i) => i === 0
              ? <th key={i} scope="row" className="px-2 py-1.5 text-left font-normal">{cell}</th>
              : <td key={i} className={cn("px-2 py-1.5", table.numeric?.[i] && "cijfers text-right")}>{cell}</td>)}
          </tr>
        ))}</tbody>
      </table>
    </div>
  );
}

/** Collapsible table twin of a chart ("Toon als tabel"). */
export function TableToggle({ table, testid }: { table: ChartTable; testid: string }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  return (
    <details className="mt-2" onToggle={(e) => setOpen((e.currentTarget as HTMLDetailsElement).open)} data-testid={testid}>
      <summary className="focus-ring inline-flex min-h-touch cursor-pointer items-center rounded-lg px-1 text-sm font-semibold text-link">
        {open ? t("chart.hideTable") : t("chart.showTable")}
      </summary>
      <div className="mt-1"><DataTable table={table} /></div>
    </details>
  );
}

/**
 * Keyboard- and pointer-readable plot area. `points` are the read-outs per position ("8 nov: 83,3 % (geschat)");
 * `children(active)` draws the chart and may highlight the active index (crosshair).
 */
export function ChartFrame({ name, summary, points, caption, table, className, children }: {
  name: string; summary: string; points: string[]; caption?: ReactNode; table: ChartTable; className?: string;
  children: (active: number | null) => ReactNode;
}) {
  const { t } = useI18n();
  const [active, setActive] = useState<number | null>(null);
  const plot = useRef<HTMLDivElement>(null);
  const liveId = useId();
  const last = points.length - 1;
  const onKey = (e: KeyboardEvent) => {
    if (!points.length) return;
    let next: number | null | undefined;
    if (e.key === "ArrowRight") next = active === null ? 0 : Math.min(last, active + 1);
    else if (e.key === "ArrowLeft") next = active === null ? last : Math.max(0, active - 1);
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = last;
    else if (e.key === "Escape") next = null;
    if (next === undefined) return;
    e.preventDefault();
    setActive(next);
  };
  const onPointer = (e: PointerEvent) => {
    const box = plot.current?.getBoundingClientRect();
    if (!box || !points.length) return;
    const ratio = Math.min(0.9999, Math.max(0, (e.clientX - box.left) / box.width));
    setActive(Math.floor(ratio * points.length));
  };
  return (
    <figure className={cn("m-0", className)} data-testid={`grafiek-${name}`}>
      <div ref={plot} tabIndex={0} role="img" aria-label={`${summary} ${t("chart.keysHelp")}`} aria-describedby={liveId}
        onKeyDown={onKey} onPointerMove={onPointer} onPointerLeave={() => setActive(null)} onBlur={() => setActive(null)}
        className="focus-ring rounded-md">
        {children(active)}
      </div>
      <p id={liveId} aria-live="polite" className="m-0 min-h-6 text-sm text-foreground cijfers">{active !== null ? points[active] : ""}</p>
      {caption ? <figcaption className="text-sm text-muted-foreground">{caption}</figcaption> : null}
      <TableToggle table={table} testid={`grafiek-${name}-tabel`} />
    </figure>
  );
}
