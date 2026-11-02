"use client";

import { BrainCircuit, KeyRound, MessageSquareText, PlugZap, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";

import { Button, Card, Field, InlineState, StatusChip, Switch } from "@/components/mg/primitives";
import { StateView } from "@/components/mg/states";
import { useAction } from "@/lib/api/actions";
import { useResource } from "@/lib/api/hooks";
import type { AiAdvice, AiStatus } from "@/lib/api/insights";
import { useI18n, type MessageKey } from "@/lib/i18n";
import { useSession } from "@/lib/session";

const EFFORTS = ["low", "medium", "high", "none"] as const;

/** Opt-in AI advisor: budget, model, write-only key, usage, test (admin) and "explain recent failures" (operator). */
export function AiCard() {
  const { t, num, money } = useI18n();
  const { can } = useSession();
  const isAdmin = can("admin");
  const isOperator = can("operator");
  const status = useResource<AiStatus>("/ai/status");
  const { run, pending } = useAction();
  const s = status.data;
  const [enabled, setEnabled] = useState(false);
  const [budget, setBudget] = useState("0");
  const [model, setModel] = useState("");
  const [effort, setEffort] = useState<string>("low");
  const [key, setKey] = useState("");
  const [advice, setAdvice] = useState<AiAdvice | null>(null);
  const [testMsg, setTestMsg] = useState<{ ok: boolean; message: string | null } | null>(null);

  useEffect(() => {
    if (!s) return;
    setEnabled(s.enabled);
    setBudget(String(s.monthly_budget_eur));
    setModel(s.model);
    setEffort(s.effort);
  }, [s]);

  if (!s) return <Card className="span-12" title={t("ai.title")} icon={BrainCircuit}><StateView kind={status.error ? "error" : "loading"} compact /></Card>;
  const adminReason = isAdmin ? undefined : t("automation.adminOnly");
  const saveConfig = async () => {
    const res = await run<AiStatus>("cfg", "put", "/ai/config", {
      enabled, monthly_budget_eur: Number(budget.replace(",", ".")) || 0, model: model.trim(), effort,
    }, t("ai.saved"));
    if (res) status.setData(() => res);
  };
  const saveKey = async () => {
    const res = await run<{ key: string }>("key", "put", "/ai/key", { api_key: key }, t("ai.keySaved"));
    if (res) { setKey(""); void status.reload(); }
  };
  const deleteKey = async () => {
    const res = await run<{ key: string }>("delkey", "del", "/ai/key", undefined, t("ai.keyDeleted"));
    if (res) void status.reload();
  };
  const test = async () => {
    const res = await run<{ ok: boolean; message: string | null }>("test", "post", "/ai/test");
    if (res) { setTestMsg(res); void status.reload(); }
  };
  const advise = async () => {
    const res = await run<AiAdvice>("advise", "post", "/ai/advise", { subject: "overview" });
    if (res) { setAdvice(res); void status.reload(); }
  };
  const u = s.usage_month;

  return (
    <>
      <Card className="span-7" title={t("ai.title")} sub={t("ai.sub")} icon={BrainCircuit}
        actions={<StatusChip tone={s.active ? "success" : "neutral"}>{s.active ? t("ai.active") : t("ai.inactive")}</StatusChip>}>
        <div className="stack">
          <InlineState tone="info">{t("ai.rules")}</InlineState>
          {s.reason ? <InlineState tone="warning">{s.reason}</InlineState> : null}
          <div className="row-between">
            <label className="label" htmlFor="ai-enabled" style={{ marginBottom: 0 }}>{t("ai.enabled")}</label>
            <span title={adminReason}><Switch id="ai-enabled" checked={enabled} onChange={setEnabled} label={t("ai.enabled")} disabled={!isAdmin} /></span>
          </div>
          <div className="grid" style={{ gap: 12 }}>
            <div className="span-4">
              <Field label={t("ai.budget")} hint={t("ai.budgetHint")} htmlFor="ai-budget">
                <input id="ai-budget" className="input num" inputMode="decimal" value={budget} disabled={!isAdmin} onChange={(e) => setBudget(e.target.value)} />
              </Field>
            </div>
            <div className="span-4">
              <Field label={t("ai.model")} hint={t("ai.modelHint")} htmlFor="ai-model">
                <input id="ai-model" className="input mono" value={model} disabled={!isAdmin} onChange={(e) => setModel(e.target.value)} />
              </Field>
            </div>
            <div className="span-4">
              <Field label={t("ai.effort")} htmlFor="ai-effort">
                <select id="ai-effort" className="select" value={effort} disabled={!isAdmin} onChange={(e) => setEffort(e.target.value)}>
                  {EFFORTS.map((e) => <option key={e} value={e}>{t(`ai.effort.${e}` as MessageKey)}</option>)}
                </select>
              </Field>
            </div>
          </div>
          <div className="toolbar">
            <Button variant="primary" disabled={!isAdmin} title={adminReason} pending={pending === "cfg"} onClick={() => void saveConfig()}>{t("ai.save")}</Button>
          </div>
          <Field label={<>{t("ai.key")} <StatusChip tone={s.key === "ingesteld" ? "success" : "neutral"} icon={KeyRound}>{s.key === "ingesteld" ? t("status.set") : t("status.empty")}</StatusChip></>} hint={t("ai.keyHint")} htmlFor="ai-key">
            <div className="toolbar">
              <input id="ai-key" type="password" className="input mono" autoComplete="off" value={key} disabled={!isAdmin} onChange={(e) => setKey(e.target.value)} style={{ flex: "1 1 220px" }} />
              <Button disabled={!isAdmin || key.trim().length < 10} title={adminReason} pending={pending === "key"} onClick={() => void saveKey()}>{t("action.store")}</Button>
              <Button variant="ghost" icon={Trash2} disabled={!isAdmin || s.key !== "ingesteld"} title={adminReason} pending={pending === "delkey"} onClick={() => void deleteKey()}>{t("ai.keyDelete")}</Button>
            </div>
          </Field>
        </div>
      </Card>
      <Card className="span-5" title={t("ai.usage")} icon={PlugZap}>
        <div className="stack">
          <dl className="kv">
            <dt>{t("ai.usage")}</dt>
            <dd>{t("ai.usageValue", { spent: money(u.spent_cents / 100), budget: money(s.monthly_budget_eur), calls: num(u.calls) })}</dd>
            {u.refused_or_failed ? <><dt /><dd className="t-3">{t("ai.refused", { count: num(u.refused_or_failed) })}</dd></> : null}
            <dt>Tokens</dt><dd className="num">{num(u.input_tokens)} in · {num(u.output_tokens)} uit</dd>
            <dt>{t("ai.price")}</dt>
            <dd>{s.price ? t("ai.priceValue", { input: num(s.price.input_cents_per_mtok), output: num(s.price.output_cents_per_mtok) }) : <StatusChip tone="warning">{t("ai.priceNone")}</StatusChip>}</dd>
          </dl>
          <div className="toolbar">
            <Button icon={PlugZap} disabled={!isAdmin || !s.active} title={!isAdmin ? adminReason : s.reason ?? undefined} pending={pending === "test"} onClick={() => void test()}>{t("ai.test")}</Button>
          </div>
          {testMsg ? <InlineState tone={testMsg.ok ? "success" : "warning"}>{testMsg.ok ? `${t("ai.testOk")}: ${testMsg.message ?? ""}` : `${t("ai.testFail")}: ${testMsg.message ?? ""}`}</InlineState> : null}
        </div>
      </Card>
      <Card className="span-12" title={t("ai.advise")} icon={MessageSquareText}
        actions={<Button variant="primary" disabled={!isOperator} title={isOperator ? undefined : t("automation.operatorOnly")} pending={pending === "advise"} onClick={() => void advise()}>{t("ai.advise")}</Button>}>
        {advice ? (
          <div className="stack-sm">
            <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
              <StatusChip tone={advice.mode === "ai" ? "accent" : "info"} icon={null}>{advice.mode === "ai" ? `${t("ai.adviceAi")} · ${advice.model ?? ""}` : t("ai.adviceRules")}</StatusChip>
              <strong>{advice.title}</strong>
            </div>
            <p style={{ margin: 0, whiteSpace: "pre-wrap", fontSize: 13 }}>{advice.text}</p>
            {advice.fallback_reason ? <p className="hint" style={{ margin: 0 }}>{t("ai.fallback", { reason: advice.fallback_reason })}</p> : null}
            <p className="hint" style={{ margin: 0 }}>{t("ai.adviceStored")}</p>
          </div>
        ) : <p className="hint" style={{ margin: 0 }}>{t("ai.adviseHint")}</p>}
      </Card>
    </>
  );
}
