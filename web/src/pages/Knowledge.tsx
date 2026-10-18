import { useEffect, useMemo, useState } from "react";
import type { FormEvent } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { getKnowledge, listKnowledge } from "../api";
import { Markdown } from "../markdown";
import type { KnowledgeEntry } from "../types";

const KIND: Record<string, string> = { law: "Law", case: "Court case", playbook: "Usual terms" };
const PLACE: Record<string, string> = { my: "Malaysia", sg: "Singapore" };
const TYPE: Record<string, string> = { nda: "NDA", service_agreement: "Service agreement" };
const CLAUSE = (c: string) => c.replace(/_/g, " ");

/** Small margin tab naming where an entry comes from. */
function SourceTab({ e }: { e: Pick<KnowledgeEntry, "kind" | "jurisdiction" | "contract_type"> }) {
  const tone = e.kind === "playbook" ? "bg-paper-soft text-ink border border-paper-line" : e.kind === "case" ? "bg-desk text-white" : "bg-ink text-white";
  const where = e.jurisdiction ? (e.jurisdiction === "my" ? "MY" : "SG") : e.contract_type ? TYPE[e.contract_type] ?? "" : "";
  return (
    <span className={`tab inline-flex items-center gap-1.5 rounded-[3px] px-1.5 py-1 !text-[0.72rem] ${tone}`}>
      {KIND[e.kind]}
      {where && <span className="opacity-80">· {where}</span>}
    </span>
  );
}

function Filter<T extends string>(props: { label: string; value: T; options: [T, string][]; onChange: (v: T) => void }) {
  return (
    <fieldset className="min-w-0">
      <legend className="tab mb-1.5 text-desk-text">{props.label}</legend>
      <div className="flex flex-wrap gap-1.5">
        {props.options.map(([v, l]) => (
          <button
            key={v || "all"}
            type="button"
            aria-pressed={props.value === v}
            onClick={() => props.onChange(v)}
            className={`inline-flex min-h-[44px] items-center rounded-[3px] border px-3 text-[0.92rem] transition-colors ${
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
  const contractType = (params.get("contract_type") ?? "") as "" | "nda" | "service_agreement";
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
      const key = e.kind === "playbook" ? `Usual terms · ${TYPE[e.contract_type ?? ""] ?? "All contracts"}` : `${PLACE[e.jurisdiction ?? ""] ?? "Other"} · ${e.kind === "case" ? "court cases" : "laws"}`;
      g.set(key, [...(g.get(key) ?? []), e]);
    }
    return [...g.entries()];
  }, [entries]);

  return (
    <div className="mx-auto max-w-[1040px] px-5 pb-20 pt-10 sm:px-8 sm:pt-14">
      <p className="tab text-desk-text">Knowledge</p>
      <h1 className="display mt-3 text-[2.1rem] text-white sm:text-[2.9rem]">What every flag is checked against</h1>
      <p className="mt-4 max-w-2xl text-[1.1rem] text-desk-text">
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
          className="min-h-[52px] flex-1 rounded-[3px] border-0 bg-white px-4 text-[1.05rem] text-ink placeholder:text-ink-mute"
        />
        <button type="submit" className="btn-go !min-h-[52px] sm:px-8">
          Search
        </button>
      </form>

      <div className="mt-6 grid gap-5 md:grid-cols-3">
        <Filter label="Where" value={jurisdiction} onChange={(v) => set("jurisdiction", v)} options={[["", "Both"], ["my", "Malaysia"], ["sg", "Singapore"]]} />
        <Filter label="Kind" value={kind} onChange={(v) => set("kind", v)} options={[["", "All"], ["law", "Law"], ["case", "Cases"], ["playbook", "Usual terms"]]} />
        <Filter label="Contract" value={contractType} onChange={(v) => set("contract_type", v)} options={[["", "Any"], ["nda", "NDA"], ["service_agreement", "Service"]]} />
      </div>

      <div aria-live="polite" className="mt-8">
        {error ? (
          <p role="alert" className="rounded-[3px] bg-high-tint px-4 py-3 font-medium text-high">
            The knowledge base could not be loaded. {error}
          </p>
        ) : entries === null ? (
          <p className="text-desk-text" role="status">
            Loading…
          </p>
        ) : entries.length === 0 ? (
          <p className="text-desk-text">Nothing matches. Clear a filter or try another word.</p>
        ) : (
          <>
            <p className="tab text-desk-text">
              {entries.length} {entries.length === 1 ? "entry" : "entries"}
            </p>
            <div className="mt-3 space-y-8">
              {groups.map(([title, list]) => (
                <section key={title} aria-label={title}>
                  <h2 className="text-[1.15rem] font-bold text-white">{title}</h2>
                  <ul className="sheet mt-3 divide-y divide-paper-line">
                    {list.map((e) => (
                      <li key={e.id}>
                        <Link to={`/knowledge/${encodeURIComponent(e.id)}`} className="grid gap-1.5 px-5 py-4 hover:bg-paper-soft sm:grid-cols-[minmax(0,1fr)_auto] sm:gap-6">
                          <span className="min-w-0">
                            <span className="block font-semibold leading-snug text-ink">{e.title}</span>
                            {e.plain && <span className="mt-1 line-clamp-2 block text-[0.95rem] text-ink-soft">{e.plain}</span>}
                            {(e.clause_types ?? []).filter((c) => c !== "other").length > 0 && (
                              <span className="tab mt-1.5 block text-ink-mute">
                                {(e.clause_types ?? []).filter((c) => c !== "other").map(CLAUSE).join(" · ")}
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
                </section>
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function Entry({ id }: { id: string }) {
  const [e, setE] = useState<KnowledgeEntry | null>(null);
  const [error, setError] = useState<string | null>(null);

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
      <Link to="/knowledge" className="tab inline-flex min-h-[44px] items-center gap-1 text-desk-text hover:text-white">
        <span aria-hidden="true">←</span> Knowledge
      </Link>
      {error ? (
        <div role="alert" className="mt-4 text-white">
          <h1 className="display text-[2rem]">This entry could not be opened</h1>
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
          <p className="tab pr-28 text-ink-mute sm:pr-24">
            {[KIND[e.kind], PLACE[e.jurisdiction ?? ""], TYPE[e.contract_type ?? ""]].filter(Boolean).join(" · ")}
          </p>
          <h1 className="display mt-2 text-[1.6rem] !leading-[1.12] text-ink sm:text-[2rem]">{e.title}</h1>
          {e.citation && <p className="mt-2 font-doc text-[1.05rem] text-ink-soft">{e.citation}</p>}
          {e.plain && (
            <p className="mt-5 border-l-4 border-ink bg-paper-soft px-4 py-3 text-[1.02rem] leading-relaxed text-ink">
              <span className="tab block text-ink">In plain words</span>
              {e.plain}
            </p>
          )}
          <div className="mt-6 border-t border-paper-line pt-2">
            <Markdown text={stripPlain(e.text ?? "")} />
          </div>
          {e.url && (
            <a
              href={e.url}
              target="_blank"
              rel="noopener noreferrer"
              className="mt-8 inline-flex min-h-[44px] items-center gap-1.5 font-semibold text-desk underline underline-offset-4 hover:text-ink"
            >
              Read the official source <span aria-hidden="true">↗</span>
              <span className="sr-only">(opens in a new tab)</span>
            </a>
          )}
          {(e.clause_types ?? []).filter((c) => c !== "other").length > 0 && (
            <p className="mt-6 text-[0.92rem] text-ink-mute">
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
