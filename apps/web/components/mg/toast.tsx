// Derived from MediaGuard Ultimate (lvos04/media-guard-ultimate@e92c2acc) apps/web/components/mg/toast.tsx — internally versioned copy, see PROVENANCE.md
"use client";

import { Check, Info, OctagonX, X } from "lucide-react";
import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";

import { buttonVariants } from "@/components/ui/button";
import { useI18n } from "@/lib/i18n";
import { cn } from "@/lib/utils";

type ToastTone = "success" | "danger" | "info";
interface ToastItem { id: number; tone: ToastTone; title: string; body?: string }
interface ToastApi { push: (tone: ToastTone, title: string, body?: string) => void }

const ToastContext = createContext<ToastApi>({ push: () => undefined });

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const { t } = useI18n();
  const dismiss = useCallback((id: number) => setItems((all) => all.filter((i) => i.id !== id)), []);
  const push = useCallback((tone: ToastTone, title: string, body?: string) => {
    const id = Date.now() + Math.random();
    setItems((all) => [...all.slice(-3), { id, tone, title, body }]);
    window.setTimeout(() => dismiss(id), tone === "danger" ? 9000 : 5000);
  }, [dismiss]);
  const value = useMemo(() => ({ push }), [push]);
  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="toast-region fixed right-4 bottom-[calc(var(--bottomnav-h)+1rem)] z-toast flex w-[min(24rem,calc(100vw-2rem))] flex-col gap-2 lg:bottom-4" aria-live="polite">
        {items.map((item) => {
          const Icon = item.tone === "success" ? Check : item.tone === "danger" ? OctagonX : Info;
          return (
            <div key={item.id} className={cn("toast flex items-start gap-2.5 rounded-lg border-2 bg-popover px-3.5 py-3 text-base shadow-md", item.tone === "danger" ? "border-down" : "border-border")} role={item.tone === "danger" ? "alert" : "status"}>
              <Icon className={cn("ico mt-0.5 size-5 shrink-0", item.tone === "danger" ? "text-down-ink" : "text-foreground")} aria-hidden="true" />
              <div className="min-w-0 flex-1 break-words"><strong>{item.title}</strong>{item.body ? <div className="mt-0.5 text-sm text-muted-foreground">{item.body}</div> : null}</div>
              <button type="button" className={buttonVariants({ variant: "stil", size: "icoon" })} onClick={() => dismiss(item.id)} aria-label={t("action.close")}><X className="ico" aria-hidden="true" /></button>
            </div>
          );
        })}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastApi {
  return useContext(ToastContext);
}
