// Derived from MediaGuard Ultimate (lvos04/media-guard-ultimate@e92c2acc) apps/web/components/mg/toast.tsx — internally versioned copy, see PROVENANCE.md
"use client";

import { CircleCheck, CircleX, Info, X } from "lucide-react";
import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";

import { useI18n } from "@/lib/i18n";

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
      <div className="toast-region" aria-live="polite">
        {items.map((item) => {
          const Icon = item.tone === "success" ? CircleCheck : item.tone === "danger" ? CircleX : Info;
          return (
            <div key={item.id} className={`toast toast--${item.tone}`} role={item.tone === "danger" ? "alert" : "status"}>
              <Icon className="ico" aria-hidden="true" />
              <div className="grow"><strong>{item.title}</strong>{item.body ? <div className="t-2" style={{ fontSize: 12.5, marginTop: 2 }}>{item.body}</div> : null}</div>
              <button type="button" className="icon-btn" style={{ width: 24, height: 24 }} onClick={() => dismiss(item.id)} aria-label={t("action.close")}><X className="ico ico-sm" aria-hidden="true" /></button>
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
