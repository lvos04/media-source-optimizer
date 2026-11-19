"use client";

import { HardDrive } from "lucide-react";

import { Card, InlineState } from "@/components/mg/primitives";
import { ResourceView, StaleMark, StateView } from "@/components/mg/states";
import type { Resource } from "@/lib/api/hooks";
import type { MaintenanceStatus, ResourceSource, ResourceTrend } from "@/lib/api/maintenance";
import { useI18n } from "@/lib/i18n";

const METRICS = {
  database_bytes: "maintenance.growth.databaseBytes",
  database_connections: "maintenance.growth.databaseConnections",
  backups_bytes: "maintenance.growth.backupBytes",
  backups_count: "maintenance.growth.backupCount",
  backup_free_bytes: "maintenance.growth.backupFree",
  appdata_free_bytes: "maintenance.growth.appdataFree",
  header_index_bytes: "maintenance.growth.headerBytes",
  header_index_estimated_bytes: "maintenance.growth.headerEstimated",
  torrent_free_bytes: "maintenance.growth.torrentFree",
  container_log_bytes: "maintenance.growth.logBytes",
} as const;
const DAILY_METRICS = ["database_bytes", "backups_bytes", "appdata_free_bytes", "database_connections"] as const;
const COUNTS = new Set<string>(["database_connections", "backups_count"]);

export function ResourceGrowthCard({ resource }: { resource: Resource<MaintenanceStatus> }) {
  const { t } = useI18n();
  return (
    <Card id="opslag-en-groei" className="span-12" title={t("maintenance.growth.title")} icon={HardDrive}>
      <ResourceView resource={resource} endpoint="GET /api/v1/system/maintenance" compact
        isEmpty={(m) => !m.growth?.latest}
        empty={<StateView kind="empty" compact title={t("maintenance.growth.empty")} body={t("maintenance.growth.emptyHelp")} />}>
        {(data) => <GrowthFigures data={data} />}
      </ResourceView>
    </Card>
  );
}

function GrowthFigures({ data: { growth, provenance } }: { data: MaintenanceStatus }) {
  const { t, bytes, num, dateTime, locale } = useI18n();
  const date = new Intl.DateTimeFormat(locale, { timeZone: "UTC", day: "numeric", month: "short", year: "numeric" });
  const dayLabel = (iso: string) => date.format(new Date(iso));
  const { latest } = growth;
  if (!latest?.observed_at) return null;

  const unknown = t("maintenance.growth.quality.onbekend");
  const headers = [t("maintenance.growth.series"), t("maintenance.growth.value"),
    t("maintenance.growth.source"), t("maintenance.growth.trend")];
  const format = (key: string, value: number | null | undefined) => {
    if (value === null || value === undefined) return unknown;
    return COUNTS.has(key) ? num(value, 2) : bytes(value);
  };

  const rows = [
    ...Object.entries(METRICS).map(([key, label]) => ({
      key, title: t(label), value: latest.values?.[key], source: provenance[key], trend: growth.per_day[key],
    })),
    ...growth.tables.map((table) => ({
      key: table.name, title: t("maintenance.growth.tableName", { name: table.name }), value: table.bytes,
      source: provenance.table_bytes, trend: table.per_day,
    })),
  ];

  return (
    <div className="space-y-3">
      <p className="text-sm">
        {t("maintenance.growth.last")}{" "}
        <time dateTime={latest.observed_at}>{dateTime(latest.observed_at)}</time>
      </p>
      {latest.day !== growth.days[0]?.day ? <StaleMark>{t("maintenance.growth.stale")}</StaleMark> : null}
      {growth.days.filter((day) => day.observed_at !== null).length < 2
        ? <InlineState tone="info">{t("maintenance.growth.insufficient")}</InlineState> : null}

      <div className="table-wrap" tabIndex={0} role="region" aria-label={t("maintenance.growth.series")}>
        <table className="table table--compact table--stack" role="table">
          <thead><tr>{headers.map((label) => <th key={label} scope="col">{label}</th>)}</tr></thead>
          <tbody>{rows.map((row) => (
            <tr key={row.key}>
              <td className="cell-primary font-semibold break-words">{row.title}</td>
              <td data-label={headers[1]}><span>{format(row.key, row.value)}</span></td>
              <td data-label={headers[2]} className="cell-block">
                <SourceLabel source={row.source} />
              </td>
              <td data-label={headers[3]} className="cell-block">
                <TrendLabel trend={row.trend} format={(value) => format(row.key, value)} dayLabel={dayLabel} />
              </td>
            </tr>
          ))}</tbody>
        </table>
      </div>

      <details>
        <summary className="focus-ring min-h-touch cursor-pointer rounded-lg text-sm font-semibold text-link">
          {t("maintenance.growth.daily")}
        </summary>
        <div className="table-wrap" tabIndex={0} role="region" aria-label={t("maintenance.growth.daily")}>
          <table className="table table--compact table--stack" role="table">
            <thead><tr>
              <th scope="col">{t("maintenance.growth.day")}</th>
              {DAILY_METRICS.map((key) => <th key={key} scope="col">{t(METRICS[key])}</th>)}
            </tr></thead>
            <tbody>{growth.days.map((day) => (
              <tr key={day.day}>
                <td className="cell-primary font-semibold"><time dateTime={day.day}>{dayLabel(day.day)}</time></td>
                {day.values ? DAILY_METRICS.map((key) => (
                  <td key={key} data-label={t(METRICS[key])}><span>{format(key, day.values?.[key])}</span></td>
                )) : <td colSpan={DAILY_METRICS.length}>{t("maintenance.growth.gap")}</td>}
              </tr>
            ))}</tbody>
          </table>
        </div>
      </details>
    </div>
  );
}

function SourceLabel({ source }: { source?: ResourceSource }) {
  const { t, dateTime } = useI18n();
  if (!source) return null;
  return (
    <div>
      <div>{source.source} · {t(`maintenance.growth.quality.${source.quality}`)}</div>
      {source.observed_at ? <time dateTime={source.observed_at}>{dateTime(source.observed_at)}</time> : null}
      {source.reason ? <div>{source.reason}</div> : null}
    </div>
  );
}

function TrendLabel({ trend, format, dayLabel }: {
  trend?: ResourceTrend;
  format: (value: number) => string;
  dayLabel: (day: string) => string;
}) {
  const { t } = useI18n();
  if (!trend || trend.value === null || !trend.from_day || !trend.to_day) {
    return <span>{t("maintenance.growth.quality.onbekend")}</span>;
  }
  return (
    <div>
      <div>{t("maintenance.growth.perDay", { value: format(trend.value) })}</div>
      <div>{t(`maintenance.growth.quality.${trend.quality}`)} · {t("maintenance.growth.period", {
        from: dayLabel(trend.from_day), to: dayLabel(trend.to_day),
      })}</div>
    </div>
  );
}
