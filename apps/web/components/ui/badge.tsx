// Peil Badge family (ONTWERPTAAL §4.2–4.5, COMPONENTEN C-03/C-08): one shape per axis, always icon + word.
//   health  → pill (rounded-full), 2 px border; Gezond without fill; Onbekend dashed
//   risk    → angular tag (4 px) with a code block + step glyph (RisicoBadge)
//   label   → neutral pill (no status meaning)
// The hook class `chip` stays on status/label pills (CSS hooks and tests).
import { cva, type VariantProps } from "class-variance-authority";

export const badgeVariants = cva(
  "chip inline-flex w-fit max-w-full min-h-6 items-center gap-1.5 rounded-full border-2 px-2 py-0.5 text-sm font-semibold leading-tight break-words align-middle [&_svg]:size-[1.125rem] [&_svg]:shrink-0",
  {
    variants: {
      tone: {
        healthy: "chip--healthy border-transparent bg-transparent px-0.5 text-healthy-ink [&_svg]:text-healthy",
        success: "chip--done border-transparent bg-muted text-foreground",
        warning: "chip--warning border-degraded bg-degraded-bg text-degraded-ink",
        danger: "chip--danger border-down bg-down-bg text-down-ink",
        info: "chip--info border-transparent bg-info-bg text-info-ink",
        accent: "chip--accent border-transparent bg-muted text-foreground",
        neutral: "border-transparent bg-muted text-foreground",
        unknown: "chip--unknown border-dashed border-unknown bg-unknown-bg text-unknown-ink",
        unlinked: "chip--unknown border-dashed border-unknown bg-unknown-bg text-unknown-ink",
        paused: "chip--paused border-paused bg-paused-bg text-paused-ink",
        linked: "chip--linked border-border-strong bg-transparent text-foreground",
        approval: "chip--approval border-approval bg-approval-bg text-approval-ink",
      },
      size: { standaard: "", groot: "px-2.5 py-1 text-base" },
      outline: { true: "border border-border-strong", false: "" },
    },
    defaultVariants: { tone: "neutral", size: "standaard", outline: false },
  },
);
export type BadgeVariantProps = VariantProps<typeof badgeVariants>;

/** RisicoBadge (T-04): angular tag, code block with 1–4 steps + the fixed word. */
export const riskVariants = cva(
  "risk inline-flex w-fit max-w-full items-stretch overflow-hidden rounded-sm border text-sm font-semibold leading-tight align-middle",
  {
    variants: {
      level: {
        0: "risk--0 border-risk-0 [&_.risk-code]:bg-risk-0-solid [&_.risk-code]:text-risk-0-on-solid [&_.risk-word]:bg-risk-0-bg [&_.risk-word]:text-risk-0-ink",
        1: "risk--1 border-risk-1 [&_.risk-code]:bg-risk-1-solid [&_.risk-code]:text-risk-1-on-solid [&_.risk-word]:bg-risk-1-bg [&_.risk-word]:text-risk-1-ink",
        2: "risk--2 border-risk-2 [&_.risk-code]:bg-risk-2-solid [&_.risk-code]:text-risk-2-on-solid [&_.risk-word]:bg-risk-2-bg [&_.risk-word]:text-risk-2-ink",
        3: "risk--3 border-risk-3 [&_.risk-code]:bg-risk-3-solid [&_.risk-code]:text-risk-3-on-solid [&_.risk-word]:bg-risk-3-bg [&_.risk-word]:text-risk-3-ink",
      },
      size: { standaard: "", groot: "text-base" },
    },
    defaultVariants: { level: 0, size: "standaard" },
  },
);
