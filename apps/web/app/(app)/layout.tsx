// Derived from MediaGuard Ultimate (lvos04/media-guard-ultimate@e92c2acc) apps/web/app/(app)/layout.tsx — internally versioned copy, see PROVENANCE.md
import { AppShell } from "@/components/shell/app-shell";

export default function AuthenticatedLayout({ children }: { children: React.ReactNode }) {
  return <AppShell>{children}</AppShell>;
}
