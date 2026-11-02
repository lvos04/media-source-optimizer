"use client";

import { Plus } from "lucide-react";
import { useId, useState } from "react";

import { Button, Field, InlineState, Modal } from "@/components/mg/primitives";
import { api, errorMessage } from "@/lib/api/client";
import type { MediaCategory, RequirementsInput } from "@/lib/api/recovery";
import { useI18n } from "@/lib/i18n";

const RESOLUTIONS = [480, 576, 720, 1080, 2160] as const;
const HDR = ["hdr10", "hdr10plus", "dv", "hlg"] as const;

function splitList(v: string): string[] {
  return v.split(/[,\s]+/).map((x) => x.trim().toLowerCase()).filter(Boolean);
}

/** Only-stricter requirement fields (MediaGuard's own requirements can never be relaxed by MSO). */
export function RequirementsEditor({ value, onChange, floor }: {
  value: RequirementsInput; onChange: (v: RequirementsInput) => void; floor?: { min_resolution?: number | null };
}) {
  const { t } = useI18n();
  const id = useId();
  const set = (patch: Partial<RequirementsInput>) => onChange({ ...value, ...patch });
  const toggle = (key: "require_hdr" | "forbid_hdr", h: string) => {
    const cur = new Set(value[key] ?? []);
    if (cur.has(h)) cur.delete(h); else cur.add(h);
    set({ [key]: [...cur] });
  };
  return (
    <div className="stack" style={{ gap: 12 }}>
      <div className="grid" style={{ gap: 12 }}>
        <div className="span-6">
          <Field label={t("rec.req.minRes")} htmlFor={`${id}-min`} hint={floor?.min_resolution ? t("rec.req.floor", { res: floor.min_resolution }) : undefined}>
            <select id={`${id}-min`} className="select" value={value.min_resolution ?? ""} onChange={(e) => set({ min_resolution: e.target.value ? Number(e.target.value) : null })}>
              <option value="">{t("rec.req.noMin")}</option>
              {RESOLUTIONS.map((r) => <option key={r} value={r}>{r}p</option>)}
            </select>
          </Field>
        </div>
        <div className="span-6">
          <Field label={t("rec.req.maxRes")} htmlFor={`${id}-max`}>
            <select id={`${id}-max`} className="select" value={value.max_resolution ?? ""} onChange={(e) => set({ max_resolution: e.target.value ? Number(e.target.value) : null })}>
              <option value="">{t("rec.req.noMax")}</option>
              {RESOLUTIONS.map((r) => <option key={r} value={r}>{r}p</option>)}
            </select>
          </Field>
        </div>
      </div>
      <fieldset style={{ border: 0, padding: 0, margin: 0 }}>
        <legend className="label">{t("rec.req.hdr")}</legend>
        <div className="row wrap" style={{ gap: 12, fontSize: 13 }}>
          {HDR.map((h) => (
            <label key={h} className="row" style={{ gap: 6 }}>
              <input type="checkbox" checked={(value.require_hdr ?? []).includes(h)} disabled={(value.forbid_hdr ?? []).includes(h)} onChange={() => toggle("require_hdr", h)} />
              {t("rec.req.require", { what: h.toUpperCase() })}
            </label>
          ))}
          {HDR.map((h) => (
            <label key={`f-${h}`} className="row" style={{ gap: 6 }}>
              <input type="checkbox" checked={(value.forbid_hdr ?? []).includes(h)} disabled={(value.require_hdr ?? []).includes(h)} onChange={() => toggle("forbid_hdr", h)} />
              {t("rec.req.forbid", { what: h.toUpperCase() })}
            </label>
          ))}
        </div>
      </fieldset>
      <div className="grid" style={{ gap: 12 }}>
        <div className="span-6">
          <Field label={t("rec.req.audio")} htmlFor={`${id}-audio`} hint={t("rec.req.langHint")}>
            <input id={`${id}-audio`} className="input" value={(value.required_audio_languages ?? []).join(", ")} onChange={(e) => set({ required_audio_languages: splitList(e.target.value) })} placeholder="nl, ja" />
          </Field>
        </div>
        <div className="span-6">
          <Field label={t("rec.req.subs")} htmlFor={`${id}-subs`} hint={t("rec.req.langHint")}>
            <input id={`${id}-subs`} className="input" value={(value.required_subtitles ?? []).join(", ")} onChange={(e) => set({ required_subtitles: splitList(e.target.value) })} placeholder="nl" />
          </Field>
        </div>
      </div>
      <label className="row" style={{ gap: 6, fontSize: 13 }}>
        <input type="checkbox" checked={!!value.require_dual_audio} onChange={(e) => set({ require_dual_audio: e.target.checked })} />
        {t("rec.req.dual")}
      </label>
      <p className="hint" style={{ margin: 0 }}>{t("rec.req.hardsubAlways")}</p>
    </div>
  );
}

export function cleanRequirements(v: RequirementsInput): RequirementsInput {
  const out: RequirementsInput = {};
  if (v.min_resolution) out.min_resolution = v.min_resolution;
  if (v.max_resolution) out.max_resolution = v.max_resolution;
  if (v.require_hdr?.length) out.require_hdr = v.require_hdr;
  if (v.forbid_hdr?.length) out.forbid_hdr = v.forbid_hdr;
  if (v.required_audio_languages?.length) out.required_audio_languages = v.required_audio_languages;
  if (v.required_subtitles?.length) out.required_subtitles = v.required_subtitles;
  if (v.require_dual_audio) out.require_dual_audio = true;
  return out;
}

/** Standalone mode: user-entered wanted item (title/category/season/episode/absolute/year + requirements). */
export function ManualWantedDialog({ disabled, disabledReason, onCreated }: { disabled?: boolean; disabledReason?: string; onCreated: (ref: string) => void }) {
  const { t } = useI18n();
  const id = useId();
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState("");
  const [cat, setCat] = useState<MediaCategory>("tv");
  const [season, setSeason] = useState("");
  const [episode, setEpisode] = useState("");
  const [absolute, setAbsolute] = useState("");
  const [year, setYear] = useState("");
  const [imdb, setImdb] = useState("");
  const [req, setReq] = useState<RequirementsInput>({});
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const tv = cat.startsWith("tv");
  const n = (v: string) => (v.trim() === "" ? null : Number(v));

  const submit = async () => {
    setError(null);
    if (!title.trim()) { setError(t("rec.manual.titleRequired")); return; }
    setPending(true);
    try {
      const body = {
        title: title.trim(), media_category: cat, season_number: tv ? n(season) : null, episode_number: tv ? n(episode) : null,
        absolute_episode_number: tv ? n(absolute) : null, year: n(year), imdb_id: imdb.trim() || null,
        requirements: cleanRequirements(req),
      };
      const created = await api.post<{ media_ref: string }>("/recovery/wanted", body);
      setOpen(false);
      setTitle(""); setSeason(""); setEpisode(""); setAbsolute(""); setYear(""); setImdb(""); setReq({});
      onCreated(created.media_ref);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setPending(false);
    }
  };

  return (
    <>
      <Button icon={Plus} disabled={disabled} title={disabled ? disabledReason : undefined} onClick={() => setOpen(true)}>{t("rec.manual.add")}</Button>
      <Modal open={open} onClose={() => setOpen(false)} title={t("rec.manual.title")} icon={Plus} description={t("rec.manual.desc")}
        footer={<>
          <Button onClick={() => setOpen(false)}>{t("action.cancel")}</Button>
          <Button variant="primary" pending={pending} onClick={() => void submit()}>{t("action.add")}</Button>
        </>}>
        <div className="stack" style={{ gap: 12 }}>
          {error ? <InlineState tone="danger" role="alert">{error}</InlineState> : null}
          <Field label={t("rec.manual.name")} htmlFor={`${id}-title`}>
            <input id={`${id}-title`} className="input" value={title} onChange={(e) => setTitle(e.target.value)} maxLength={300} />
          </Field>
          <Field label={t("rec.manual.category")} htmlFor={`${id}-cat`}>
            <select id={`${id}-cat`} className="select" value={cat} onChange={(e) => setCat(e.target.value as MediaCategory)}>
              <option value="tv">{t("rec.cat.tv")}</option>
              <option value="tv-anime">{t("rec.cat.tvAnime")}</option>
              <option value="movies">{t("rec.cat.movies")}</option>
              <option value="movies-anime">{t("rec.cat.moviesAnime")}</option>
            </select>
          </Field>
          {tv ? (
            <div className="grid" style={{ gap: 12 }}>
              <div className="span-4"><Field label={t("rec.manual.season")} htmlFor={`${id}-s`}><input id={`${id}-s`} className="input" inputMode="numeric" value={season} onChange={(e) => setSeason(e.target.value.replace(/\D/g, ""))} /></Field></div>
              <div className="span-4"><Field label={t("rec.manual.episode")} htmlFor={`${id}-e`}><input id={`${id}-e`} className="input" inputMode="numeric" value={episode} onChange={(e) => setEpisode(e.target.value.replace(/\D/g, ""))} /></Field></div>
              <div className="span-4"><Field label={t("rec.manual.absolute")} htmlFor={`${id}-a`} hint={cat === "tv-anime" ? t("rec.manual.absoluteHint") : undefined}><input id={`${id}-a`} className="input" inputMode="numeric" value={absolute} onChange={(e) => setAbsolute(e.target.value.replace(/\D/g, ""))} /></Field></div>
            </div>
          ) : null}
          <div className="grid" style={{ gap: 12 }}>
            <div className="span-6"><Field label={t("rec.manual.year")} htmlFor={`${id}-y`}><input id={`${id}-y`} className="input" inputMode="numeric" value={year} onChange={(e) => setYear(e.target.value.replace(/\D/g, "").slice(0, 4))} /></Field></div>
            <div className="span-6"><Field label={t("rec.manual.imdb")} htmlFor={`${id}-i`}><input id={`${id}-i`} className="input mono" value={imdb} onChange={(e) => setImdb(e.target.value.trim())} placeholder="tt0000000" /></Field></div>
          </div>
          <h3 className="card-title" style={{ fontSize: 13 }}>{t("rec.manual.requirements")}</h3>
          <RequirementsEditor value={req} onChange={setReq} />
        </div>
      </Modal>
    </>
  );
}
