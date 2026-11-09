// Derived from MediaGuard Ultimate (lvos04/media-guard-ultimate@e92c2acc) apps/web/lib/utils.ts — internally versioned copy, see PROVENANCE.md
import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/** shadcn/ui helper: conditional classes (clsx) with Tailwind conflict resolution (tailwind-merge). */
export function cn(...classes: ClassValue[]): string {
  return twMerge(clsx(classes));
}

export function bytes(value?: number | null): string {
  if (value === null || value === undefined) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let result = value;
  let index = 0;
  while (Math.abs(result) >= 1024 && index < units.length - 1) {
    result /= 1024;
    index += 1;
  }
  return `${result.toLocaleString("nl-NL", { maximumFractionDigits: 1 })} ${units[index]}`;
}

export function duration(seconds?: number | null): string {
  if (seconds === null || seconds === undefined) return "—";
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const rest = Math.floor(seconds % 60);
  return hours > 0 ? `${hours}u ${minutes}m` : `${minutes}m ${rest}s`;
}

export function fileName(path?: string): string {
  return path?.split("/").pop() ?? "Geen actieve job";
}
