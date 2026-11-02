// Derived from MediaGuard Ultimate (lvos04/media-guard-ultimate@e92c2acc) apps/web/app/login/page.tsx — internally versioned copy, see PROVENANCE.md
"use client";

import { ArrowRight } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

import { Button, Field, InlineState } from "@/components/mg/primitives";
import { AuthFrame } from "@/components/shell/auth-frame";
import { api, ApiError, errorMessage, setCsrfToken } from "@/lib/api/client";
import type { LoginResult, SetupStatus } from "@/lib/api/contracts";
import { useI18n } from "@/lib/i18n";

export default function LoginPage() {
  return <AuthFrame><Suspense><LoginForm /></Suspense></AuthFrame>;
}

function safeNext(next: string | null): string {
  return next && next.startsWith("/") && !next.startsWith("//") && !next.startsWith("/login") ? next : "/";
}

function LoginForm() {
  const { t } = useI18n();
  const router = useRouter();
  const params = useSearchParams();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // first run: no administrator yet → setup wizard
    api.get<SetupStatus>("/setup/status").then((s) => { if (s.setup_required && !s.admin_exists) router.replace("/setup"); }).catch(() => undefined);
  }, [router]);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setPending(true);
    setError(null);
    try {
      const result = await api.post<LoginResult>("/auth/login", { username, password });
      if (result?.csrf_token) setCsrfToken(result.csrf_token);
      router.replace(safeNext(params.get("next")));
    } catch (err) {
      setError(err instanceof ApiError && err.kind === "offline" ? t("offline.pageTitle") : t("auth.loginFailed", { message: errorMessage(err) }));
    } finally {
      setPending(false);
    }
  };

  return (
    <form className="stack" onSubmit={submit} noValidate>
      <div>
        <h1 className="page-title">{t("auth.loginTitle")}</h1>
        <p className="page-sub">{t("auth.loginSub")}</p>
      </div>
      {error ? <InlineState tone="danger" role="alert">{error}</InlineState> : null}
      <Field label={t("auth.username")} htmlFor="username">
        <input id="username" name="username" className="input" autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} required />
      </Field>
      <Field label={t("auth.password")} htmlFor="password">
        <input id="password" name="password" type="password" className="input" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
      </Field>
      <Button type="submit" variant="primary" pending={pending} disabled={!username || !password} style={{ height: 40 }}>{t("auth.login")} <ArrowRight className="ico" aria-hidden="true" /></Button>
    </form>
  );
}
