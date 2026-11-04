"use client";

import { RotateCcw, Save, Tags } from "lucide-react";
import { useState } from "react";

import { Button, Card, InlineState, StatusChip } from "@/components/mg/primitives";
import { ResourceView, StateView } from "@/components/mg/states";
import { useToast } from "@/components/mg/toast";
import { api, errorMessage } from "@/lib/api/client";
import { useResource } from "@/lib/api/hooks";
import type { MediaCategory, SabCategories, SabCategory } from "@/lib/api/usenet";
import { useI18n, type MessageKey } from "@/lib/i18n";
import { useSession } from "@/lib/session";

const SOURCE_TONE = { handmatig: "info", automatisch: "success", geen: "neutral" } as const;

function CategoryTable({ data, onSaved }: { data: SabCategories; onSaved: () => void }) {
  const { t } = useI18n();
  const toast = useToast();
  const { can } = useSession();
  const [draft, setDraft] = useState<Record<string, MediaCategory | null>>(
    () => Object.fromEntries(data.items.map((c) => [c.name, c.media_category])),
  );
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const dirty = data.items.some((c) => draft[c.name] !== c.media_category);
  const mediaLabel = (m: MediaCategory | null) => (m ? t(`usenet.cat.media.${m}` as MessageKey) : t("usenet.cat.none"));

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      await api.put<SabCategories>("/usenet/sab/category-map", {
        items: data.items.filter((c) => c.name !== "*").map((c) => ({ name: c.name, media_category: draft[c.name] ?? null })),
      });
      toast.push("success", t("usenet.cat.saved"));
      onSaved();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setSaving(false);
    }
  };

  const row = (c: SabCategory) => {
    const id = `usenet-cat-${c.name.replace(/[^a-zA-Z0-9_-]/g, "_")}`;
    const isDefault = c.name === "*";
    return (
      <tr key={c.name}>
        <td data-label={t("usenet.cat.sab")}>
          <label htmlFor={id} className="mono">{isDefault ? t("usenet.dl.defaultCategory") : c.name}</label>
        </td>
        <td data-label={t("usenet.cat.media")}>
          <select id={id} className="input" value={draft[c.name] ?? ""} disabled={isDefault || !can("admin")}
            title={isDefault ? t("usenet.cat.help") : !can("admin") ? t("usenet.adminOnly") : undefined}
            onChange={(e) => setDraft((d) => ({ ...d, [c.name]: (e.target.value || null) as MediaCategory | null }))}>
            <option value="">{t("usenet.cat.none")}</option>
            {data.media_categories.map((m) => <option key={m} value={m}>{mediaLabel(m)}</option>)}
          </select>
        </td>
        <td data-label={t("usenet.cat.origin")}>
          <span className="row wrap" style={{ gap: 6 }}>
            <StatusChip tone={SOURCE_TONE[c.source]}>{t(`usenet.cat.source.${c.source}` as MessageKey)}</StatusChip>
            {!isDefault && c.source === "handmatig" ? (
              <Button size="sm" variant="ghost" icon={RotateCcw} disabled={!can("admin")}
                title={can("admin") ? `${t("usenet.cat.reset")}: ${mediaLabel(c.auto)}` : t("usenet.adminOnly")}
                onClick={() => setDraft((d) => ({ ...d, [c.name]: c.auto }))}>
                {t("usenet.cat.reset")}
              </Button>
            ) : null}
          </span>
        </td>
      </tr>
    );
  };

  return (
    <>
      <table className="table table--stack">
        <thead>
          <tr><th>{t("usenet.cat.sab")}</th><th>{t("usenet.cat.media")}</th><th>{t("usenet.cat.origin")}</th></tr>
        </thead>
        <tbody>{data.items.map(row)}</tbody>
      </table>
      <div className="card-body stack-sm">
        <p className="t-2" style={{ margin: 0, fontSize: 12.5 }}>{t("usenet.cat.help")}</p>
        {error ? <InlineState tone="danger" role="alert">{error}</InlineState> : null}
        <div>
          <Button variant="primary" icon={Save} onClick={() => void save()} pending={saving} disabled={!dirty || !can("admin")}
            title={!can("admin") ? t("usenet.adminOnly") : undefined}>
            {t("usenet.cat.save")}
          </Button>
        </div>
      </div>
    </>
  );
}

/** SABnzbd categories → library categories (automatic name heuristic, editable; stored in `usenet.category_map`). */
export function CategoryMapCard() {
  const { t } = useI18n();
  const cats = useResource<SabCategories>("/usenet/sab/categories");
  return (
    <Card title={t("usenet.cat.title")} icon={Tags} sub={t("usenet.cat.sub")} className="span-12" flush>
      <ResourceView resource={cats} endpoint="GET /api/v1/usenet/sab/categories" isEmpty={(d) => !d.items.length}
        empty={<StateView kind="empty" compact />}>
        {(d) => <CategoryTable key={JSON.stringify(d.items)} data={d} onSaved={() => void cats.reload()} />}
      </ResourceView>
    </Card>
  );
}
