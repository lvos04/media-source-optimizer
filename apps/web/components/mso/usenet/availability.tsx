"use client";

import { FileUp, FlaskConical, Scale } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Button, Card, InlineState, Modal, Segmented, StatusChip, type Tone } from "@/components/mg/primitives";
import { ResourceView, StateView } from "@/components/mg/states";
import { useToast } from "@/components/mg/toast";
import { ConfidenceChip, fileToBase64, ProviderStatusChip, useRatio } from "@/components/mso/usenet/shared";
import { api, errorMessage } from "@/lib/api/client";
import { useResource } from "@/lib/api/hooks";
import type { SettingsPayload } from "@/lib/api/contracts";
import type { ArticleTest, ContributionOverview, StatBudget, UsenetNzb } from "@/lib/api/usenet";
import { useI18n, type MessageKey } from "@/lib/i18n";
import { useSession } from "@/lib/session";

type Period = "7" | "30" | "90";

/** Automatic STAT sample after every NZB MSO sends: on/off + per-provider budget usage. */
function AutoSampleStatus() {
  const { t, num, bytes, relative } = useI18n();
  const budget = useResource<StatBudget>("/usenet/stat-budget", undefined, 60_000);
  const b = budget.data;
  if (!b) return null;
  return (
    <div className="stack-sm">
      <InlineState tone={b.enabled ? "success" : "neutral"}>
        {b.enabled ? t("usenet.avail.auto.on", { size: num(b.sample_size), when: b.last_auto_test_at ? relative(b.last_auto_test_at) : t("usenet.avail.auto.never") })
          : t("usenet.avail.auto.off")}
      </InlineState>
      {b.enabled && b.providers.length ? (
        <details>
          <summary className="t-2" style={{ cursor: "pointer" }}>{t("usenet.avail.auto.budget")}</summary>
          <ul className="t-3" style={{ margin: "6px 0 0", paddingLeft: 18 }}>
            {b.providers.map((p) => (
              <li key={p.slug}>
                <strong className="t-2">{p.label}</strong>:{" "}
                {!p.eligible ? t("usenet.avail.auto.noCreds") : (
                  <>
                    {t("usenet.avail.auto.usage", {
                      conn: num(p.usage.daily.connections), connMax: num(b.limits.daily.connections),
                      cmd: num(p.usage.daily.commands), cmdMax: num(b.limits.daily.commands),
                      bytes: bytes(p.usage.daily.bytes), bytesMax: bytes(b.limits.daily.bytes),
                      mconn: num(p.usage.monthly.connections), mconnMax: num(b.limits.monthly.connections),
                    })}
                    {p.fit < 1 ? <> · <StatusChip tone="warning">{t("usenet.avail.auto.out")}</StatusChip></> : null}
                  </>
                )}
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </div>
  );
}

/** Per-provider availability/overlap with coverage + confidence; never a guarantee. */
export function AvailabilityCard() {
  const { t, num, bytes } = useI18n();
  const ratio = useRatio();
  const [period, setPeriod] = useState<Period>("30");
  const data = useResource<ContributionOverview>("/usenet/contribution", { period_days: period }, 60_000);
  return (
    <Card title={t("usenet.avail.title")} icon={Scale} sub={t("usenet.avail.sub")} className="span-12" id="beschikbaarheid"
      actions={<Segmented label={t("usenet.avail.period")} value={period} onChange={setPeriod}
        options={(["7", "30", "90"] as Period[]).map((d) => ({ value: d, label: t("usenet.avail.days", { days: d }) }))} />}
      flush>
      <div style={{ padding: "0 16px 12px" }}>
        <div className="stack-sm">
          <InlineState tone="info">{t("usenet.avail.disclaimer")}</InlineState>
          <AutoSampleStatus />
          {data.data?.credentials_hint ? (
            <InlineState tone="neutral">
              <strong>{t("usenet.avail.credsHint")}</strong>
              {" — "}{t("usenet.avail.credsHintBody", { providers: data.data.credentials_hint.providers.join(", ") })}
            </InlineState>
          ) : null}
        </div>
      </div>
      <ResourceView resource={data} endpoint="GET /api/v1/usenet/contribution" isEmpty={(d) => !d.items.length}
        empty={<StateView kind="empty" compact title={t("usenet.avail.empty")} />}>
        {(d) => (
          <table className="table table--stack">
            <thead>
              <tr>
                <th>{t("usenet.avail.provider")}</th><th>{t("usenet.avail.availability")}</th><th>{t("usenet.avail.checked")}</th>
                <th>{t("usenet.avail.unique")}</th><th>{t("usenet.avail.passive")}</th><th>{t("usenet.avail.bytes")}</th>
                <th>{t("usenet.avail.coverage")}</th><th>{t("usenet.avail.confidence")}</th>
              </tr>
            </thead>
            <tbody>
              {d.items.map((c) => (
                <tr key={c.source_id}>
                  <td data-label={t("usenet.avail.provider")}>
                    <strong>{c.label}</strong>
                    <div className="t-3">
                      {t(`usenet.avail.method.${c.method}` as MessageKey)} · {t(c.kind === "sab_server" ? "usenet.avail.sabServer" : c.kind === "block" ? "usenet.providers.kind.block" : "usenet.providers.kind.unlimited")}
                    </div>
                  </td>
                  <td data-label={t("usenet.avail.availability")}>
                    {c.availability?.source ? (
                      <div className="stack-sm" style={{ gap: 2 }}>
                        <strong>{ratio(c.availability.ratio)}</strong>
                        <StatusChip tone={c.availability.source === "stat" ? "success" : "neutral"} icon={null}
                          title={c.availability.source === "passive" ? t("usenet.avail.source.passiveTitle") : undefined}>
                          {t(c.availability.source === "stat" ? "usenet.avail.source.stat" : "usenet.avail.source.passive")}
                        </StatusChip>
                        <span className="t-3">{t("usenet.avail.ofArticles", { n: num(c.availability.articles) })}</span>
                      </div>
                    ) : <span className="t-3">{t("usenet.avail.source.none")}</span>}
                  </td>
                  <td data-label={t("usenet.avail.checked")}>
                    {c.articles_checked ? `${num(c.available)} / ${num(c.articles_checked)}` : "—"}
                  </td>
                  <td data-label={t("usenet.avail.unique")}>
                    {c.unique_available === null ? <span className="t-3">{t("usenet.avail.notComparable")}</span>
                      : t("usenet.avail.uniqueOf", { n: num(c.unique_available), of: num(c.compared_articles) })}
                  </td>
                  <td data-label={t("usenet.avail.passive")}>
                    {c.passive.articles_tried ? t("usenet.avail.passiveValue", { ratio: ratio(c.passive.success_ratio), tried: num(c.passive.articles_tried) }) : "—"}
                  </td>
                  <td data-label={t("usenet.avail.bytes")}>{bytes(c.bytes_downloaded)}</td>
                  <td data-label={t("usenet.avail.coverage")}>{ratio(c.coverage, 0)}</td>
                  <td data-label={t("usenet.avail.confidence")}><ConfidenceChip level={c.confidence} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </ResourceView>
      {data.data?.notes.length ? (
        <ul className="t-3" style={{ margin: 0, padding: "8px 16px 14px 32px" }}>
          {data.data.notes.map((n) => <li key={n}>{n}</li>)}
        </ul>
      ) : null}
    </Card>
  );
}

const TEST_TONE: Record<ArticleTest["status"], Tone> = { queued: "neutral", running: "accent", done: "success", failed: "danger" };

/** Explicit, user-confirmed article tests on real NZB articles (sent by MSO or uploaded). */
export function ArticleTestsCard() {
  const { t, num, relative } = useI18n();
  const ratio = useRatio();
  const toast = useToast();
  const { can } = useSession();
  const nzbs = useResource<{ items: UsenetNzb[] }>("/usenet/nzbs");
  const tests = useResource<{ items: ArticleTest[] }>("/usenet/article-tests", undefined, 10_000);
  const [selected, setSelected] = useState<number | null>(null);
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState<"upload" | "start" | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    const nzb = Number(new URLSearchParams(window.location.search).get("nzb"));
    if (Number.isInteger(nzb) && nzb > 0) setSelected(nzb);
  }, []);
  const operator = can("operator");
  const testable = (nzbs.data?.items ?? []).filter((n) => n.testable);
  const settings = useResource<SettingsPayload>("/settings");
  const sample = Number(settings.data?.values["usenet.article_sample_size"] ?? 100);

  const upload = async (file: File) => {
    setBusy("upload");
    try {
      const row = await api.post<UsenetNzb>("/usenet/nzbs", { filename: file.name, content_b64: await fileToBase64(file) });
      toast.push("success", t("usenet.tests.uploaded", { count: num(row.segment_total) }), row.title);
      setSelected(row.id);
      await nzbs.reload();
    } catch (e) {
      toast.push("danger", errorMessage(e));
    } finally {
      setBusy(null);
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  const start = async () => {
    if (selected === null) return;
    setBusy("start");
    try {
      await api.post("/usenet/article-tests", { nzb_id: selected, sample_size: sample, confirm: true });
      toast.push("success", t("usenet.tests.started"));
      setConfirm(false);
      await tests.reload();
    } catch (e) {
      toast.push("danger", errorMessage(e));
    } finally {
      setBusy(null);
    }
  };

  return (
    <Card title={t("usenet.tests.title")} icon={FlaskConical} className="span-12" id="artikeltest"
      actions={
        <>
          <input ref={fileRef} type="file" accept=".nzb,application/x-nzb" hidden aria-hidden="true"
            onChange={(e) => { const f = e.target.files?.[0]; if (f) void upload(f); }} />
          <Button size="sm" icon={FileUp} onClick={() => fileRef.current?.click()} pending={busy === "upload"}
            disabled={!operator} title={operator ? undefined : t("usenet.operatorOnly")}>{t("usenet.tests.upload")}</Button>
        </>
      }>
      <div className="stack">
        <p className="t-2" style={{ margin: 0 }}>{t("usenet.tests.help")}</p>
        <ResourceView resource={nzbs} compact endpoint="GET /api/v1/usenet/nzbs" isEmpty={() => !testable.length}
          empty={<StateView kind="empty" compact title={t("usenet.tests.noNzbs")} body={t("usenet.tests.noNzbsBody")} />}>
          {() => (
            <div className="row wrap" style={{ alignItems: "flex-end" }}>
              <div style={{ flex: "1 1 260px", minWidth: 0 }}>
                <label className="label" htmlFor="usenet-test-nzb">{t("usenet.tests.nzb")}</label>
                <select id="usenet-test-nzb" className="input" value={selected ?? ""} onChange={(e) => setSelected(e.target.value ? Number(e.target.value) : null)}>
                  <option value="">—</option>
                  {testable.map((n) => (
                    <option key={n.id} value={n.id}>{n.title} · {num(n.segment_total)} art. · {relative(n.created_at)}</option>
                  ))}
                </select>
              </div>
              <Button variant="primary" icon={FlaskConical} onClick={() => setConfirm(true)} disabled={!operator || selected === null}
                title={!operator ? t("usenet.operatorOnly") : undefined}>{t("usenet.tests.start")}</Button>
            </div>
          )}
        </ResourceView>
        <div>
          <span className="eyebrow">{t("usenet.tests.recent")}</span>
          <ResourceView resource={tests} compact endpoint="GET /api/v1/usenet/article-tests" isEmpty={(d) => !d.items.length}
            empty={<p className="t-3" style={{ margin: "4px 0 0" }}>{t("usenet.tests.none")}</p>}>
            {(d) => (
              <ul className="list" style={{ marginTop: 4 }}>
                {d.items.slice(0, 10).map((x) => (
                  <li key={x.id} className="list-row" style={{ flexWrap: "wrap" }}>
                    <div className="list-main" style={{ minWidth: 0, flex: "1 1 220px" }}>
                      <div className="list-title">{x.nzb_title ?? `NZB ${x.nzb_id}`}</div>
                      <div className="list-sub">
                        {t(`usenet.tests.trigger.${x.trigger}` as MessageKey)} · {t("usenet.tests.sample")} {num(x.sample_size)} · {relative(x.created_at)}
                      </div>
                    </div>
                    <div className="row wrap">
                      {Object.entries(x.summary.providers ?? {}).map(([slug, p]) => (
                        p.checked ? (
                          <StatusChip key={slug} tone={p.availability !== null && p.availability >= 0.99 ? "success" : "warning"}
                            title={p.unique_available !== null ? t("usenet.avail.uniqueOf", { n: num(p.unique_available), of: num(x.summary.comparable_articles ?? 0) }) : undefined}>
                            {slug}: {ratio(p.availability)}
                          </StatusChip>
                        ) : <span key={slug} className="row" style={{ gap: 4 }}>{slug}: <ProviderStatusChip status={p.status} detail={p.message} /></span>
                      ))}
                      <StatusChip tone={TEST_TONE[x.status]} pending={x.status === "running"}>{t(`usenet.tests.status.${x.status}` as MessageKey)}</StatusChip>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </ResourceView>
        </div>
      </div>
      <Modal open={confirm} onClose={() => setConfirm(false)} icon={FlaskConical} title={t("usenet.tests.confirmTitle")}
        description={t("usenet.tests.confirmBody", { sample: num(sample) })}
        footer={<><Button onClick={() => setConfirm(false)}>{t("action.cancel")}</Button>
          <Button variant="primary" onClick={() => void start()} pending={busy === "start"}>{t("usenet.tests.confirm")}</Button></>} />
    </Card>
  );
}
