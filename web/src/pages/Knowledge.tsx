import { useEffect, useMemo, useState } from "react";
import type { FormEvent } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { getKnowledge, getReview, listKnowledge, listReviews } from "../api";
import { Markdown } from "../markdown";
import type { KnowledgeEntry } from "../types";

const KIND: Record<string, string> = { law: "Law", case: "Court case", playbook: "Usual terms" };
const PLACE: Record<string, string> = { my: "Malaysia", sg: "Singapore" };
const TYPE: Record<string, string> = { nda: "NDA", service_agreement: "Service agreement", employment: "Employment", tenancy: "Tenancy", hire_purchase: "Hire purchase" };
const CLAUSE = (c: string) => c.replace(/_/g, " ");

/** Small margin tab naming where an entry comes from. */
function SourceTab({ e }: { e: Pick<KnowledgeEntry, "kind" | "jurisdiction" | "contract_type"> }) {
  const tone = e.kind === "playbook" ? "bg-paper-soft text-ink border border-paper-line" : e.kind === "case" ? "bg-desk text-white" : "bg-ink text-white";
  const where = e.jurisdiction ? (e.jurisdiction === "my" ? "MY" : "SG") : e.contract_type ? TYPE[e.contract_type] ?? "" : "";
  return (
    <span className={`meta inline-flex items-center gap-1.5 rounded-[3px] px-1.5 py-1 text-xs ${tone}`}>
      {KIND[e.kind]}
      {where && <span className="opacity-80">{where}</span>}
    </span>
  );
}

function Filter<T extends string>(props: { label: string; value: T; options: [T, string][]; onChange: (v: T) => void }) {
  return (
    <fieldset className="min-w-0">
      <legend className="label mb-1.5 text-desk-text">{props.label}</legend>
      <div className="flex flex-wrap gap-1.5">
        {props.options.map(([v, l]) => (
          <button
            key={v || "all"}
            type="button"
            aria-pressed={props.value === v}
            onClick={() => props.onChange(v)}
            className={`inline-flex min-h-[44px] items-center rounded-[3px] border px-3 text-sm transition-colors ${
              props.value === v ? "border-white bg-white font-semibold text-ink" : "border-desk-line text-white hover:border-desk-text"
            }`}
          >
            {l}
          </button>
        ))}
      </div>
    </fieldset>
  );
}

export default function Knowledge() {
  const { id } = useParams();
  return id ? <Entry id={decodeURIComponent(id)} /> : <Browse />;
}

function Browse() {
  const [params, setParams] = useSearchParams();
  const kind = (params.get("kind") ?? "") as "" | "law" | "case" | "playbook";
  const jurisdiction = (params.get("jurisdiction") ?? "") as "" | "my" | "sg";
  const contractType = (params.get("contract_type") ?? "") as "" | "nda" | "service_agreement" | "employment" | "tenancy" | "hire_purchase";
  const q = params.get("q") ?? "";
  const [draft, setDraft] = useState(q);
  const [entries, setEntries] = useState<KnowledgeEntry[] | null>(null);
  const [all, setAll] = useState<KnowledgeEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  function set(k: string, v: string) {
    const next = new URLSearchParams(params);
    if (v) next.set(k, v);
    else next.delete(k);
    setParams(next, { replace: true });
  }

  useEffect(() => {
    listKnowledge()
      .then(setAll)
      .catch(() => setAll([]));
  }, []);

  useEffect(() => {
    let live = true;
    setEntries(null);
    setError(null);
    listKnowledge({ kind, jurisdiction, contract_type: contractType, q })
      .then((e) => live && setEntries(e))
      .catch((e: Error) => live && setError(e.message));
    return () => {
      live = false;
    };
  }, [kind, jurisdiction, contractType, q]);

  const counts = useMemo(() => {
    const c = { law: 0, case: 0, playbook: 0, my: 0, sg: 0 };
    (all ?? []).forEach((e) => {
      c[e.kind] += 1;
      if (e.jurisdiction === "my" || e.jurisdiction === "sg") c[e.jurisdiction] += 1;
    });
    return c;
  }, [all]);

  function onSearch(e: FormEvent) {
    e.preventDefault();
    set("q", draft.trim());
  }

  const groups: [string, KnowledgeEntry[]][] = useMemo(() => {
    const g = new Map<string, KnowledgeEntry[]>();
    for (const e of entries ?? []) {
      const key = e.kind === "playbook" ? `Usual terms, ${TYPE[e.contract_type ?? ""] ?? "All contracts"}` : `${PLACE[e.jurisdiction ?? ""] ?? "Other"}, ${e.kind === "case" ? "court cases" : "laws"}`;
      g.set(key, [...(g.get(key) ?? []), e]);
    }
    return [...g.entries()];
  }, [entries]);

  return (
    <div className="mx-auto max-w-[1040px] px-5 pb-20 pt-10 sm:px-8 sm:pt-14">
      <h1 className="page-title !mt-0">Law and usual terms</h1>
      <p className="mt-4 max-w-2xl text-lg text-desk-text">
        {all
          ? `${counts.law} sections of Malaysian and Singapore law, ${counts.case} court cases and ${counts.playbook} notes on usual terms. Every source chip on a finding opens one of these.`
          : "Malaysian and Singapore law, court cases and notes on usual terms. Every source chip on a finding opens one of these."}
      </p>

      <form onSubmit={onSearch} role="search" className="mt-7 flex flex-col gap-3 sm:flex-row">
        <label htmlFor="kq" className="sr-only">
          Search the knowledge base
        </label>
        <input
          id="kq"
          type="search"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="For example: penalty, restraint of trade, personal data"
          autoComplete="off"
          className="min-h-[52px] flex-1 rounded-[3px] border-0 bg-white px-4 text-base text-ink placeholder:text-ink-mute"
        />
        <button type="submit" className="btn-go !min-h-[52px] sm:px-8">
          Search
        </button>
      </form>

      <div className="mt-6 grid gap-5 md:grid-cols-[auto_auto_minmax(0,1fr)]">
        <Filter label="Where" value={jurisdiction} onChange={(v) => set("jurisdiction", v)} options={[["", "Both"], ["my", "Malaysia"], ["sg", "Singapore"]]} />
        <Filter label="Kind" value={kind} onChange={(v) => set("kind", v)} options={[["", "All"], ["law", "Law"], ["case", "Cases"], ["playbook", "Usual terms"]]} />
        <div className="min-w-0">
          <label htmlFor="k-contract" className="label mb-1.5 block text-desk-text">Contract</label>
          <select id="k-contract" value={contractType} onChange={(e) => set("contract_type", e.target.value)} className="min-h-[44px] w-full rounded-[3px] border-0 bg-white px-3 text-sm text-ink md:w-56">
            <option value="">Any contract</option>
            <option value="nda">NDA</option>
            <option value="service_agreement">Service agreement</option>
            <option value="employment">Employment or freelance</option>
            <option value="tenancy">Tenancy</option>
            <option value="hire_purchase">Hire purchase</option>
          </select>
        </div>
      </div>

      <div aria-live="polite" className="mt-8">
        {error ? (
          <p role="alert" className="rounded-[3px] bg-high-tint px-4 py-3 font-medium text-high">
            The knowledge base could not be loaded. {error}
          </p>
        ) : entries === null ? (
          <div role="status" aria-live="polite">
            <span className="sr-only">Loading the library…</span>
            <div aria-hidden="true" className="space-y-3">
              {[0, 1, 2].map((i) => (
                <div key={i} className="h-[76px] rounded-[3px] bg-white/10" />
              ))}
            </div>
          </div>
        ) : entries.length === 0 ? (
          <p className="text-desk-text">Nothing matches. Clear a filter or try another word.</p>
        ) : (
          <>
            <p className="meta text-desk-text">
              {entries.length} {entries.length === 1 ? "entry" : "entries"}
            </p>
            <div className="mt-3 space-y-8">
              {groups.map(([title, list]) => (
                <Group key={title} title={title} list={list} open={Boolean(q || kind || jurisdiction || contractType)} />
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

const SHOWN = 5;

/** One group of the index: the first five entries, the rest one click away. A search or filter shows them all. */
function Group({ title, list, open }: { title: string; list: KnowledgeEntry[]; open: boolean }) {
  const [all, setAll] = useState(false);
  const shown = open || all ? list : list.slice(0, SHOWN);
  const id = `g-${title.replace(/[^a-z0-9]+/gi, "-").toLowerCase()}`;
  return (
    <section aria-labelledby={id}>
      <h2 id={id} className="text-lg font-bold text-white">
        {title} <span className="meta font-normal text-desk-text">{list.length}</span>
      </h2>
      <ul className="sheet mt-3 divide-y divide-paper-line">
        {shown.map((e) => (
          <li key={e.id}>
              <Link to={`/knowledge/${encodeURIComponent(e.id)}`} className="grid gap-1.5 px-5 py-4 hover:bg-paper-soft sm:grid-cols-[minmax(0,1fr)_auto] sm:gap-6">
                <span className="min-w-0">
                  <span className="block font-semibold leading-snug text-ink">{e.title}</span>
                  {e.plain && <span className="mt-1 line-clamp-2 block text-sm text-ink-soft">{e.plain}</span>}
                  {(e.clause_types ?? []).filter((c) => c !== "other").length > 0 && (
                    <span className="meta mt-1.5 block text-ink-mute">
                      {(e.clause_types ?? []).filter((c) => c !== "other").map(CLAUSE).join(", ")}
                    </span>
                  )}
                </span>
                <span className="self-start">
                  <SourceTab e={e} />
                </span>
              </Link>
            </li>
        ))}
      </ul>
      {shown.length < list.length && (
        <button type="button" onClick={() => setAll(true)} className="mt-2 inline-flex min-h-[44px] items-center font-semibold text-white underline underline-offset-4">
          Show all {list.length}
        </button>
      )}
    </section>
  );
}

/** Reviews in this workspace whose findings cite the entry. */
function useCitedIn(id: string) {
  const [hits, setHits] = useState<{ id: string; filename: string; finding: string }[] | null>(null);
  useEffect(() => {
    let live = true;
    const bare = id.replace(/^playbook:/, "");
    listReviews()
      .then((list) => Promise.all(list.slice(0, 30).map((x) => getReview(x.id).catch(() => null))))
      .then((rs) => {
        if (!live) return;
        const out: { id: string; filename: string; finding: string }[] = [];
        const seen = new Set<string>();
        for (const r of rs) {
          if (!r || seen.has(r.id)) continue;
          seen.add(r.id);
          const f = r.findings.find((x) => (x.citations ?? []).some((c) => c.ref === id || c.ref === bare || c.ref.endsWith(`/${bare}`)));
          if (f) out.push({ id: r.id, filename: r.filename, finding: f.id });
        }
        setHits(out);
      })
      .catch(() => live && setHits([]));
    return () => {
      live = false;
    };
  }, [id]);
  return hits;
}

/** Splits an entry into its verbatim text (shown as an extract) and the commentary after it. */
function splitText(md: string): { extract: string | null; rest: string } {
  const m = md.match(/## (Text|Holding|What the court said|Extract)\n([\s\S]*?)(?=\n## |$)/);
  if (!m) return { extract: null, rest: md };
  return { extract: m[2].trim(), rest: md.replace(m[0], "").trim() };
}

function Entry({ id }: { id: string }) {
  const [e, setE] = useState<KnowledgeEntry | null>(null);
  const [error, setError] = useState<string | null>(null);
  const cited = useCitedIn(id);

  useEffect(() => {
    let live = true;
    setE(null);
    setError(null);
    getKnowledge(id)
      .then((x) => live && setE(x))
      .catch((x: Error) => live && setError(x.message));
    return () => {
      live = false;
    };
  }, [id]);

  useEffect(() => {
    if (e) document.title = `${e.title} · Contract Co-pilot`;
    return () => {
      document.title = "Contract Co-pilot";
    };
  }, [e]);

  return (
    <div className="mx-auto max-w-[860px] px-5 pb-20 pt-8 sm:px-8 sm:pt-12">
      <Link to="/knowledge" className="meta inline-flex min-h-[44px] items-center gap-1 text-desk-text hover:text-white">
        <span aria-hidden="true">←</span> Knowledge
      </Link>
      {error ? (
        <div role="alert" className="mt-4 text-white">
          <h1 className="display text-2xl">This entry could not be opened</h1>
          <p className="mt-2 text-desk-text">{error}</p>
        </div>
      ) : !e ? (
        <p className="mt-6 text-desk-text" role="status">
          Opening…
        </p>
      ) : (
        <article className="sheet relative mt-3 px-5 py-7 sm:px-10 sm:py-10">
          <span className="absolute right-3 top-3 sm:-right-3 sm:top-8">
            <SourceTab e={e} />
          </span>
          <p className="meta pr-28 text-ink-mute sm:pr-24">
            {[KIND[e.kind], PLACE[e.jurisdiction ?? ""], TYPE[e.contract_type ?? ""]].filter(Boolean).join(", ")}
          </p>
          <h1 className="display mt-2 text-xl !leading-[1.12] text-ink sm:text-2xl">{e.title}</h1>
          {e.citation && <p className="mt-2 font-doc text-base text-ink-soft">{e.citation}</p>}
          {e.plain && (
            <p className="mt-5 border-l-4 border-ink bg-paper-soft px-4 py-3 text-base leading-relaxed text-ink">
              <span className="label block text-ink">In plain words</span>
              {e.plain}
            </p>
          )}
          <div className="mt-6 border-t border-paper-line pt-2">
            {(() => {
              const { extract, rest } = splitText(stripPlain(e.text ?? ""));
              return (
                <>
                  {extract && (
                    <figure className="mt-4">
                      <figcaption className="label text-ink-soft">{e.kind === "case" ? "From the judgment" : e.kind === "law" ? "The provision" : "The note"}</figcaption>
                      <blockquote className="mt-2 border-y-2 border-ink bg-paper-soft px-5 py-4 font-doc text-base leading-[1.7] text-ink [&_p]:mt-2 [&_p:first-child]:mt-0">
                        <Markdown text={extract.replace(/^"|"$/gm, "")} />
                      </blockquote>
                      {e.citation && <p className="mt-1.5 text-right font-doc text-sm italic text-ink-soft">{e.citation}</p>}
                    </figure>
                  )}
                  <Markdown text={rest} />
                </>
              );
            })()}
          </div>
          {(e.source_url || e.url) && (
            <a
              href={(e.source_url || e.url)!}
              target="_blank"
              rel="noopener noreferrer"
              className="btn-ink mt-8"
            >
              Open the official source <span aria-hidden="true">↗</span>
              <span className="sr-only">(opens in a new tab)</span>
            </a>
          )}
          {cited && (
            <div className="mt-6 border-t border-paper-line pt-4">
              <p className="meta text-ink-mute">
                {cited.length === 0 ? "Not cited in any review in this workspace yet" : `Cited in ${cited.length} ${cited.length === 1 ? "review" : "reviews"} in this workspace`}
              </p>
              {cited.length > 0 && (
                <ul className="mt-2 flex flex-wrap gap-2">
                  {cited.slice(0, 12).map((c) => (
                    <li key={c.id}>
                      <Link to={`/reviews/${c.id}?finding=${encodeURIComponent(c.finding)}`} className="inline-flex min-h-[44px] items-center rounded-[3px] border border-paper-line px-3 text-sm text-ink hover:border-ink">
                        {c.filename}
                      </Link>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
          {(e.clause_types ?? []).filter((c) => c !== "other").length > 0 && (
            <p className="mt-6 text-sm text-ink-mute">
              Used when a contract has clauses on {(e.clause_types ?? []).filter((c) => c !== "other").map(CLAUSE).join(", ")}.
            </p>
          )}
        </article>
      )}
    </div>
  );
}

/** The plain-words section is shown as a callout, so drop it from the body. */
function stripPlain(md: string): string {
  return md.replace(/## In plain words\n[\s\S]*?(?=\n## |$)/, "").trim();
}
