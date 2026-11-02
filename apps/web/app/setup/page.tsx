// Derived from MediaGuard Ultimate (lvos04/media-guard-ultimate@e92c2acc) apps/web/app/setup/page.tsx — internally versioned copy, see PROVENANCE.md
// (MSO: steps admin → connectors → operating mode → summary; language/theme step dropped.)
"use client";

import { ArrowLeft, ArrowRight, CircleCheck, Eye, ShieldCheck } from "lucide-react";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { ConnectorStatusChip } from "@/components/mg/domain";
import { Button, Field, InlineState, LinkButton } from "@/components/mg/primitives";
import { ErrorState, StateView } from "@/components/mg/states";
import { ConnectorForm, OPTIONAL_CONNECTORS as OPTIONAL, sortConnectors } from "@/components/mso/connector-form";
import { AuthFrame } from "@/components/shell/auth-frame";
import { api, ApiError, errorMessage, setCsrfToken } from "@/lib/api/client";
import { itemsOf, type Connector, type LoginResult, type OperatingMode, type SetupStatus } from "@/lib/api/contracts";
import { useResource } from "@/lib/api/hooks";
import { useI18n } from "@/lib/i18n";
import { cn } from "@/lib/utils";

export default function SetupPage() {
  return <AuthFrame wide><SetupWizard /></AuthFrame>;
}

type Phase = "checking" | "offline" | "done" | "wizard";

function SetupWizard() {
  const { t } = useI18n();
  const router = useRouter();
  const [phase, setPhase] = useState<Phase>("checking");
  const [step, setStep] = useState(0);
  const [mode, setMode] = useState<OperatingMode>("read_only");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const check = useCallback(async () => {
    setPhase("checking");
    try {
      const status = await api.get<SetupStatus>("/setup/status");
      if (!status.setup_required) { setPhase("done"); return; }
      if (!status.admin_exists) { setStep(0); setPhase("wizard"); return; }
      // administrator exists but setup is unfinished: continue as that (logged-in) administrator
      try {
        const me = await api.get<LoginResult>("/auth/me");
        setCsrfToken(me.csrf_token);
        setStep(1);
        setPhase("wizard");
      } catch (e) {
        if (e instanceof ApiError && e.kind === "unauthorized") router.replace("/login?next=/setup");
        else throw e;
      }
    } catch {
      setPhase("offline");
    }
  }, [router]);

  useEffect(() => { void check(); }, [check]);

  if (phase === "checking") return <StateView kind="loading" />;
  if (phase === "offline") {
    return <StateView kind="offline" title={t("offline.pageTitle")} body={t("offline.pageBody")} action={<Button variant="primary" onClick={() => void check()}>{t("action.retry")}</Button>} />;
  }
  if (phase === "done") {
    return (
      <div className="stack">
        <h1 className="page-title">{t("setup.title")}</h1>
        <InlineState tone="info">{t("setup.done")}</InlineState>
        <LinkButton href="/login" variant="primary">{t("setup.goLogin")}</LinkButton>
      </div>
    );
  }

  const complete = async () => {
    setPending(true);
    setError(null);
    try {
      await api.post("/setup/complete", { mode });
      router.replace("/");
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setPending(false);
    }
  };

  const steps = [t("setup.stepAdmin"), t("setup.stepConnectors"), t("setup.stepMode"), t("setup.stepSummary")];
  return (
    <div className="stack">
      <ol className="hstepper" aria-label={t("setup.title")} style={{ listStyle: "none", margin: "0 0 8px", padding: 0 }}>
        {steps.map((label, i) => (
          <li key={label} className="hstep" style={i === step ? { color: "var(--mg-text)" } : undefined} aria-current={i === step ? "step" : undefined}>
            {i > 0 ? <span className="hstep-line" aria-hidden="true" /> : null}
            <span className={cn("step-no")} style={i <= step ? { borderColor: "var(--mg-accent)", color: "var(--mg-accent)" } : undefined}>{i < step ? <CircleCheck className="ico ico-sm" aria-hidden="true" /> : i + 1}</span>{label}
          </li>
        ))}
      </ol>

      {step === 0 ? <AdminStep onDone={() => setStep(1)} /> : null}
      {step === 1 ? <ConnectorsStep onNext={() => setStep(2)} /> : null}

      {step === 2 ? (
        <div className="stack">
          <div><h1 className="page-title">{t("setup.modeTitle")}</h1><p className="page-sub">{t("setup.modeSub")}</p></div>
          <div className="stack-sm" role="radiogroup" aria-label={t("setup.stepMode")}>
            <label className="choice accept-box"><input type="radio" name="mode" checked={mode === "read_only"} onChange={() => setMode("read_only")} />
              <span><strong className="row" style={{ gap: 6 }}><Eye className="ico ico-sm" aria-hidden="true" />{t("setup.modeReadOnly")}</strong><span className="t-2">{t("setup.modeReadOnlyBody")}</span></span>
            </label>
            <label className="choice accept-box"><input type="radio" name="mode" checked={mode === "manage"} onChange={() => setMode("manage")} />
              <span><strong className="row" style={{ gap: 6 }}><ShieldCheck className="ico ico-sm" aria-hidden="true" />{t("setup.modeManage")}</strong><span className="t-2">{t("setup.modeManageBody")}</span></span>
            </label>
          </div>
          <div className="row-between">
            <Button icon={ArrowLeft} onClick={() => setStep(1)}>{t("action.previous")}</Button>
            <Button variant="primary" onClick={() => setStep(3)}>{t("action.next")} <ArrowRight className="ico" aria-hidden="true" /></Button>
          </div>
        </div>
      ) : null}

      {step === 3 ? (
        <div className="stack">
          <div><h1 className="page-title">{t("setup.summaryTitle")}</h1><p className="page-sub">{t("setup.summarySub")}</p></div>
          <Summary mode={mode} />
          {error ? <InlineState tone="danger" role="alert">{error}</InlineState> : null}
          <div className="row-between">
            <Button icon={ArrowLeft} onClick={() => setStep(2)}>{t("action.previous")}</Button>
            <Button variant="primary" pending={pending} onClick={() => void complete()} style={{ height: 40 }} data-testid="setup-complete">{t("setup.complete")} <ArrowRight className="ico" aria-hidden="true" /></Button>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function AdminStep({ onDone }: { onDone: () => void }) {
  const { t } = useI18n();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [repeat, setRepeat] = useState("");
  const [touched, setTouched] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const userError = touched && username.trim().length < 2 ? t("auth.usernameTooShort") : null;
  const passError = touched && password.length < 12 ? t("auth.passwordTooShort") : null;
  const repeatError = touched && repeat !== password ? t("auth.passwordMismatch") : null;

  const createAdmin = async (e: React.FormEvent) => {
    e.preventDefault();
    setTouched(true);
    if (username.trim().length < 2 || password.length < 12 || repeat !== password) return;
    setPending(true);
    setError(null);
    try {
      const created = await api.post<Partial<LoginResult> | null>("/setup/admin", { username: username.trim(), password });
      // the backend may log the new admin in directly; otherwise log in to get a session + CSRF token
      if (created && typeof created === "object" && created.csrf_token) setCsrfToken(created.csrf_token);
      else {
        const session = await api.post<LoginResult>("/auth/login", { username: username.trim(), password });
        setCsrfToken(session.csrf_token);
      }
      onDone();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setPending(false);
    }
  };

  return (
    <form className="stack" onSubmit={createAdmin} noValidate>
      <div><h1 className="page-title">{t("setup.title")}</h1><p className="page-sub">{t("setup.sub")}</p></div>
      {error ? <InlineState tone="danger" role="alert">{error}</InlineState> : null}
      <Field label={t("auth.username")} htmlFor="setup-username" error={userError}>
        <input id="setup-username" className="input" autoComplete="username" value={username} aria-invalid={Boolean(userError)} onChange={(e) => setUsername(e.target.value)} />
      </Field>
      <Field label={t("auth.password")} htmlFor="setup-password" hint={t("auth.passwordHint")} error={passError}>
        <input id="setup-password" type="password" className="input" autoComplete="new-password" value={password} aria-invalid={Boolean(passError)} onChange={(e) => setPassword(e.target.value)} />
      </Field>
      <Field label={t("auth.passwordRepeat")} htmlFor="setup-repeat" error={repeatError}>
        <input id="setup-repeat" type="password" className="input" autoComplete="new-password" value={repeat} aria-invalid={Boolean(repeatError)} onChange={(e) => setRepeat(e.target.value)} />
      </Field>
      <Button type="submit" variant="primary" pending={pending} style={{ height: 40 }}>{t("setup.createAccount")} <ArrowRight className="ico" aria-hidden="true" /></Button>
    </form>
  );
}

function ConnectorsStep({ onNext }: { onNext: () => void }) {
  const { t } = useI18n();
  const connectors = useResource<Connector[] | { items: Connector[] }>("/connectors");
  const rows = sortConnectors(itemsOf(connectors.data));
  const missing = rows.filter((c) => !OPTIONAL.has(String(c.id)) && (!c.url || !c.enabled));
  return (
    <div className="stack">
      <div><h1 className="page-title">{t("setup.connectorsTitle")}</h1><p className="page-sub">{t("setup.connectorsSub")}</p></div>
      {connectors.loading ? <StateView kind="loading" />
        : connectors.error && !connectors.data ? <ErrorState error={connectors.error} onRetry={() => void connectors.reload()} endpoint="GET /api/v1/connectors" />
          : rows.length === 0 ? <StateView kind="empty" title={t("connector.empty")} />
            : rows.map((c) => <ConnectorForm key={c.id} connector={c} optional={OPTIONAL.has(String(c.id))} onSaved={() => connectors.reload()} />)}
      {missing.length ? <InlineState tone="info">{t("setup.connectorsLater")}</InlineState> : null}
      <div className="row" style={{ justifyContent: "flex-end" }}>
        <Button variant="primary" onClick={onNext}>{t("action.next")} <ArrowRight className="ico" aria-hidden="true" /></Button>
      </div>
    </div>
  );
}

function Summary({ mode }: { mode: OperatingMode }) {
  const { t } = useI18n();
  const connectors = useResource<Connector[] | { items: Connector[] }>("/connectors");
  const rows = sortConnectors(itemsOf(connectors.data));
  return (
    <dl className="kv">
      <dt>{t("setup.summaryAdmin")}</dt><dd>{t("setup.summaryAdminValue")}</dd>
      {rows.map((c) => (
        <div key={c.id} style={{ display: "contents" }}>
          <dt>{c.label}</dt>
          <dd className="row wrap" style={{ gap: 6 }}><ConnectorStatusChip connector={c} />{c.url ? <span className="mono t-2" style={{ fontSize: 12 }}>{c.url}</span> : null}</dd>
        </div>
      ))}
      {connectors.error ? <><dt>{t("setup.stepConnectors")}</dt><dd className="t-danger">{connectors.error.message}</dd></> : null}
      <dt>{t("setup.summaryMode")}</dt><dd><strong>{mode === "manage" ? t("setup.modeManage") : t("setup.modeReadOnly")}</strong></dd>
    </dl>
  );
}
