import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { listPlaybook, revertPosition, savePosition } from "../api";
import { SignInButton } from "../components/Account";
import { formatDate } from "../format";
import { canManage } from "../session";
import type { PlaybookItem } from "../types";
import { useSession } from "../useSession";

const TYPE: Record<string, string> = { nda: "NDA", service_agreement: "Service agreement", employment: "Employment", tenancy: "Tenancy", hire_purchase: "Hire purchase" };
/** Library text is markdown; show it as plain sentences. */
const clean = (s: string) => s.replace(/\*\*([^*]+)\*\*/g, "$1").replace(/`([^`]+)`/g, "$1").replace(/^\s*"|"\s*$/g, "").replace(/\s+/g, " ").trim();
const lines = (s: string) => s.split(/\n/).map((l) => l.replace(/^\s*[-*]\s+/, "")).join("\n").split(/\n(?=\S)/).map(clean).filter(Boolean);
/** "High: a notice window..." -> ["high", "a notice window..."] */
const tagged = (f: string): [string | null, string] => {
  const m = clean(f).match(/^(High|Medium|Low):\s*(.*)$/i);
  return m ? [m[1].toLowerCase(), m[2]] : [null, clean(f)];
};
const CLAUSE = (c: string) => c.replace(/_/g, " ").replace(/^\w/, (x) => x.toUpperCase());

/** The mark an overridden position carries everywhere. */
export function CompanyMark() {
  return <span className="tab inline-flex items-center rounded-r-[3px] bg-desk px-1.5 py-1 text-xs text-white">Your company’s position</span>;
}

export default function Positions() {
  const s = useSession();
  const [type, setType] = useState("service_agreement");
  const [items, setItems] = useState<PlaybookItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const edit = canManage(s);

  useEffect(() => {
    if (s.loading) return;
    let live = true;
    setItems(null);
    listPlaybook(type)
      .then((x) => live && setItems(x))
      .catch((e: Error) => live && setError(e.message));
    return () => {
      live = false;
    };
  }, [type, s.loading, s.workspace?.workspace.id]);

  const overridden = items?.filter((i) => i.overridden).length ?? 0;

  return (
    <div className="mx-auto max-w-[1040px] px-5 pb-20 pt-10 sm:px-8 sm:pt-14">
      <p className="page-eyebrow">{s.workspace?.workspace.name ?? "Sample workspace"}</p>
      <h1 className="display mt-3 text-2xl text-white sm:text-2xl">Standard positions</h1>
      <p className="mt-3 max-w-2xl text-base text-desk-text">
        What a balanced clause looks like, what to push back on, and what to ask for. Every review compares the contract with these. Where your company has its own position, reviews use yours.
      </p>
      {!s.user && !s.loading && (
        <div className="mt-5 flex flex-wrap items-center gap-3 text-desk-text">
          {s.available && <SignInButton label="Sign in with Google to set your own" />}
        </div>
      )}
      {s.user && !edit && <p className="mt-3 text-desk-text">Your workspace owner or an admin can change these.</p>}

      <div role="group" aria-label="Contract type" className="mt-6 flex flex-wrap items-center gap-1.5">
        {Object.entries(TYPE).map(([v, l]) => (
          <button key={v} type="button" aria-pressed={type === v} onClick={() => setType(v)} className={`inline-flex min-h-[44px] items-center rounded-[3px] border px-3 text-sm ${type === v ? "border-white bg-white font-semibold text-ink" : "border-desk-line text-white hover:border-desk-text"}`}>
            {l}
          </button>
        ))}
        {items && <span className="meta ml-2 text-desk-text">{items.length} clauses, {overridden} set by your company</span>}
      </div>

      {error && <p role="alert" className="mt-5 rounded-[3px] bg-high-tint px-4 py-3 font-medium text-high">{error}</p>}
      {items === null ? (
        <p role="status" className="mt-6 text-desk-text">Loading…</p>
      ) : (
        <ul className="mt-5 space-y-4">
          {items.map((it) => (
            <li key={it.contract_type + it.clause_type}>
              {editing === it.clause_type ? (
                <Editor
                  item={it}
                  onCancel={() => setEditing(null)}
                  onSaved={(next) => {
                    setItems((list) => list!.map((x) => (x.clause_type === next.clause_type ? next : x)));
                    setEditing(null);
                  }}
                />
              ) : (
                <Position item={it} canEdit={edit} onEdit={() => setEditing(it.clause_type)} onReverted={async () => setItems(await listPlaybook(type))} />
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Position({ item, canEdit, onEdit, onReverted }: { item: PlaybookItem; canEdit: boolean; onEdit: () => void; onReverted: () => void }) {
  const [busy, setBusy] = useState(false);
  return (
    <article className={`sheet relative px-5 py-5 sm:px-7 ${item.overridden ? "border-l-4 border-desk" : ""}`} aria-label={CLAUSE(item.clause_type)}>
      {item.overridden && (
        <span className="absolute -right-2 top-4">
          <CompanyMark />
        </span>
      )}
      <h2 className="pr-40 font-doc text-lg font-bold leading-snug text-ink">{CLAUSE(item.clause_type)}</h2>
      <p className="mt-0.5 text-sm text-ink-mute">{item.title}</p>
      <dl className="mt-4 grid gap-4 md:grid-cols-[1fr_1fr]">
        <div>
          <dt className="label text-ink-soft">Balanced looks like</dt>
          <dd className="mt-1 text-sm text-ink">
            {lines(item.standard).length > 1 ? (
              <ul className="space-y-1">
                {lines(item.standard).map((l) => (
                  <li key={l} className="flex gap-2">
                    <span aria-hidden="true" className="mt-[0.55em] h-[2px] w-2.5 shrink-0 bg-low" />
                    <span>{l}</span>
                  </li>
                ))}
              </ul>
            ) : (
              clean(item.standard)
            )}
          </dd>
        </div>
        <div>
          <dt className="label text-ink-soft">Push back on</dt>
          <dd className="mt-1">
            <ul className="space-y-1 text-sm text-ink">
              {item.red_flags.slice(0, 5).map((f) => {
                const [level, text] = tagged(f);
                return (
                  <li key={f} className="flex gap-2">
                    <span aria-hidden="true" className={`mt-[0.55em] h-[2px] w-2.5 shrink-0 ${level === "low" ? "bg-low" : level === "medium" ? "bg-medium-box" : "bg-high"}`} />
                    <span>
                      {level && <span className={`tab mr-1.5 ${level === "high" ? "text-high" : level === "medium" ? "text-medium" : "text-low"}`}>{level}</span>}
                      {text}
                    </span>
                  </li>
                );
              })}
            </ul>
          </dd>
        </div>
      </dl>
      {item.ask && (
        <p className="mt-4 border-l-4 border-ink bg-paper-soft px-3 py-2 text-sm text-ink">
          <span className="meta block">Ask for</span>
          {clean(item.ask)}
        </p>
      )}
      {item.note && <p className="mt-3 text-sm text-ink-soft">Note: {item.note}</p>}
      <div className="mt-4 flex flex-wrap items-center gap-3">
        {item.overridden && item.updated_at && (
          <span className="meta text-ink-mute">
            Set {item.updated_by ? `by ${item.updated_by} ` : ""}on {formatDate(item.updated_at)}
          </span>
        )}
        {canEdit && (
          <>
            <button type="button" className="btn-line !min-h-[44px] px-4" onClick={onEdit}>
              {item.overridden ? "Edit your position" : "Set your company’s position"}
            </button>
            {item.overridden && (
              <button
                type="button"
                className="inline-flex min-h-[44px] items-center px-2 font-semibold text-desk underline underline-offset-4 hover:text-ink"
                disabled={busy}
                onClick={async () => {
                  setBusy(true);
                  await revertPosition(item.contract_type, item.clause_type).catch(() => undefined);
                  onReverted();
                }}
              >
                {busy ? "Reverting…" : "Revert to default"}
              </button>
            )}
          </>
        )}
      </div>
    </article>
  );
}

function Editor({ item, onCancel, onSaved }: { item: PlaybookItem; onCancel: () => void; onSaved: (i: PlaybookItem) => void }) {
  const [standard, setStandard] = useState(lines(item.standard).join("\n"));
  const [flags, setFlags] = useState(item.red_flags.map(clean).join("\n"));
  const [ask, setAsk] = useState(clean(item.ask));
  const [note, setNote] = useState(item.note);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const id = `pos-${item.clause_type}`;

  async function submit(e: FormEvent) {
    e.preventDefault();
    const red_flags = flags.split("\n").map((f) => f.trim()).filter(Boolean);
    if (!standard.trim() && !red_flags.length && !ask.trim()) return setError("Write at least your standard, a red flag or an ask.");
    setSaving(true);
    try {
      onSaved(await savePosition(item.contract_type, item.clause_type, { standard: standard.trim(), red_flags, ask: ask.trim(), note: note.trim() }));
    } catch (x) {
      setError((x as Error).message);
      setSaving(false);
    }
  }

  const field = "mt-1 block w-full rounded-[3px] border border-ink/30 bg-white px-3 py-2 text-ink";
  return (
    <form onSubmit={submit} className="sheet border-l-4 border-desk px-5 py-5 sm:px-7" aria-label={`Edit ${CLAUSE(item.clause_type)}`}>
      <h2 className="font-doc text-lg font-bold text-ink">{CLAUSE(item.clause_type)}: your company’s position</h2>
      <label htmlFor={`${id}-std`} className="label mt-4 block text-ink-soft">Balanced looks like</label>
      <textarea id={`${id}-std`} rows={3} value={standard} onChange={(e) => setStandard(e.target.value)} maxLength={4000} className={field} />
      <label htmlFor={`${id}-flags`} className="label mt-4 block text-ink-soft">Push back on (one per line)</label>
      <textarea id={`${id}-flags`} rows={4} value={flags} onChange={(e) => setFlags(e.target.value)} className={field} />
      <label htmlFor={`${id}-ask`} className="label mt-4 block text-ink-soft">Ask for</label>
      <textarea id={`${id}-ask`} rows={2} value={ask} onChange={(e) => setAsk(e.target.value)} maxLength={1000} className={field} />
      <label htmlFor={`${id}-note`} className="label mt-4 block text-ink-soft">Note for your team (optional)</label>
      <input id={`${id}-note`} value={note} onChange={(e) => setNote(e.target.value)} maxLength={2000} className={`${field} min-h-[44px]`} />
      {error && <p role="alert" className="mt-3 font-medium text-high">{error}</p>}
      <div className="mt-5 flex flex-wrap gap-3">
        <button type="submit" className="btn-ink" disabled={saving}>{saving ? "Saving…" : "Save position"}</button>
        <button type="button" className="btn-line" onClick={onCancel}>Cancel</button>
      </div>
    </form>
  );
}
