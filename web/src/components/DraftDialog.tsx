import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { RefObject } from "react";
import { reducedMotion, settle } from "../motion";
import type { NegotiationDraft } from "../types";
import type { Marked } from "./Reader";
import { FlagTab } from "./Risk";

interface Props {
  /** Who the reply goes to: "employer", "landlord", "lender", "vendor" or "other party" (see counterparty.ts). */
  other?: string;
  draft: NegotiationDraft | null;
  open: boolean;
  items: Marked[];
  onClose: () => void;
  /** Close the letter and show this finding's box on the contract. */
  onShow: (findingId: string) => void;
}

/** A greeting like "Dear [Vendor contact]," cannot be sent; fall back to a neutral one. */
function tidy(body: string): string {
  return body.replace(/^(Dear|Hi|Hello)\s*\[[^\]\n]{1,40}\],?/im, "Hello,");
}

function pageRange(pages: number[]): string {
  const u = [...new Set(pages)].sort((a, b) => a - b);
  if (u.length === 0) return "";
  if (u.length === 1) return `page ${u[0]}`;
  return `pages ${u.slice(0, -1).join(", ")} and ${u[u.length - 1]}`;
}

function useAutoHeight(ref: RefObject<HTMLTextAreaElement>, value: string, on: boolean) {
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight + 2}px`;
  }, [ref, value, on]);
}

const words = (s: string) => new Set(s.toLowerCase().match(/[\p{L}\p{N}]{4,}/gu) ?? []);

interface Block {
  text: string;
  /** the finding this paragraph asks about, if any */
  m: Marked | null;
}

/** Splits the letter into paragraphs and list items, and ties each ask to its finding. */
function toBlocks(body: string, draft: NegotiationDraft, items: Marked[]): Block[] {
  const raw: string[] = [];
  for (const para of body.split(/\n\s*\n/)) {
    const lines = para.split("\n");
    if (lines.length > 1 && lines.every((l) => /^\s*(?:[-*•]|\d+[.)])\s+/.test(l) || !l.trim())) raw.push(...lines.filter((l) => l.trim()));
    else if (para.trim()) raw.push(para.trim());
  }
  const byId = new Map(items.map((m) => [m.f.id, m]));
  const asks = draft.asks
    .map((a) => ({ m: byId.get(a.finding_id), w: words(`${a.ask} ${byId.get(a.finding_id)?.f.suggested_ask ?? ""}`) }))
    .filter((a): a is { m: Marked; w: Set<string> } => !!a.m);
  const used = new Set<string>();
  return raw.map((text) => {
    const w = words(text);
    let best: Marked | null = null;
    let score = 0;
    for (const a of asks) {
      let hit = 0;
      w.forEach((x) => a.w.has(x) && hit++);
      const s = hit / Math.max(4, Math.min(w.size, a.w.size)) - (used.has(a.m.f.id) ? 0.2 : 0);
      if (s > score) {
        score = s;
        best = a.m;
      }
    }
    if (best && score >= 0.34 && w.size >= 5) {
      used.add(best.f.id);
      return { text, m: best };
    }
    return { text, m: null };
  });
}

export default function DraftDialog({ draft, open, items, onClose, onShow, other = "vendor" }: Props) {
  const ref = useRef<HTMLDialogElement>(null);
  const headRef = useRef<HTMLHeadingElement>(null);
  const subjectRef = useRef<HTMLTextAreaElement>(null);
  const bodyRef = useRef<HTMLTextAreaElement>(null);
  const letterRef = useRef<HTMLDivElement>(null);
  const footRef = useRef<HTMLElement>(null);
  const assembled = useRef<NegotiationDraft | null>(null);
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [editing, setEditing] = useState(false);
  const [copied, setCopied] = useState<"" | "ok" | "fail">("");
  const [width, setWidth] = useState(0);

  useEffect(() => {
    if (draft) {
      setSubject(draft.subject);
      setBody(tidy(draft.body));
      setEditing(false);
    }
  }, [draft]);

  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) {
      d.showModal();
      headRef.current?.focus();
      setWidth(window.innerWidth);
    }
    if (!open && d.open) d.close();
  }, [open]);

  // Moment 4: the first time a draft opens, it assembles ask by ask, then the actions settle.
  useEffect(() => {
    if (!open || !draft || assembled.current === draft || !letterRef.current) return;
    assembled.current = draft;
    if (reducedMotion()) return;
    const parts = [...letterRef.current.querySelectorAll<HTMLElement>("[data-part]")];
    let t = 120;
    parts.forEach((el) => {
      const isAsk = el.dataset.part === "ask";
      settle(el, t, 6);
      const flag = el.querySelector<HTMLElement>("[data-flag]");
      if (flag) flag.animate([{ opacity: 0, transform: "translateX(-8px)" }, { opacity: 1, transform: "none" }], { duration: 240, delay: t + 160, easing: "ease-out", fill: "backwards" });
      t += isAsk ? 340 : 160;
    });
    if (footRef.current) settle(footRef.current, t + 120, 10);
  }, [open, draft]);

  useAutoHeight(subjectRef, subject + width, open);
  useAutoHeight(bodyRef, body + width, editing);

  async function copy() {
    try {
      await navigator.clipboard.writeText(`Subject: ${subject}\n\n${body}`);
      setCopied("ok");
    } catch {
      setCopied("fail");
    }
    window.setTimeout(() => setCopied(""), 3000);
  }

  const blocks = useMemo(() => (draft ? toBlocks(body, draft, items) : []), [body, draft, items]);
  const mailto = `mailto:?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`;
  const byId = new Map(items.map((m) => [m.f.id, m]));
  const asks = draft?.asks ?? [];
  const linked = asks.map((a) => byId.get(a.finding_id)).filter((m): m is Marked => !!m);
  const extra = asks.length - linked.length;
  const leftBlank = /\[[^\]\n]{1,40}\]/.test(body);

  const askList = (
    <ol className="space-y-3" data-user-content="">
      {asks.map((a) => {
        const m = byId.get(a.finding_id);
        return (
          <li key={a.finding_id + a.ask} className="flex gap-3 text-sm leading-snug">
            {m ? (
              <FlagTab n={m.n} risk={m.f.risk} className="!rounded-[3px]" />
            ) : (
              <span aria-hidden="true" className="inline-flex h-7 min-w-[2.6rem] shrink-0 items-center justify-center rounded-[3px] border-2 border-dashed border-ink-mute text-lg font-bold leading-none text-ink">
                +
              </span>
            )}
            <span>
              {a.ask}
              <span className="meta mt-0.5 block text-ink-mute">{m ? `Page ${m.f.page}` : "New clause"}</span>
            </span>
          </li>
        );
      })}
    </ol>
  );

  return (
    <dialog
      ref={ref}
      onClose={onClose}
      aria-labelledby="draft-title"
      className="m-0 h-[100dvh] max-h-none w-full max-w-none bg-transparent p-0 sm:m-auto sm:h-auto sm:max-h-[calc(100vh-48px)] sm:w-[min(1080px,calc(100vw-48px))]"
    >
      {draft && open && (
        <div className="flex h-full max-h-[100dvh] flex-col bg-paper sm:max-h-[calc(100vh-48px)] sm:rounded-[3px] sm:shadow-lift">
          <header className="flex shrink-0 items-center justify-between gap-4 bg-ink px-5 py-3 text-white sm:px-7">
            <h2 id="draft-title" ref={headRef} tabIndex={-1} className="label text-white">
              Your reply to the {other}
            </h2>
            <button type="button" onClick={onClose} className="btn -mr-3 min-w-[44px] !px-3 text-white hover:bg-white/10">
              Close
            </button>
          </header>

          <div className="grid min-h-0 flex-1 overflow-y-auto overscroll-contain md:grid-cols-[minmax(0,1fr)_320px]">
            <div className="px-5 pb-8 pt-5 sm:pl-[4.5rem] sm:pr-9 sm:pt-7">
              <details className="mb-5 rounded-[3px] border border-paper-line bg-paper-soft md:hidden">
                <summary className="flex min-h-[48px] cursor-pointer list-none items-center gap-2 px-3 py-2 [&::-webkit-details-marker]:hidden">
                  <span className="flex -space-x-1" aria-hidden="true">
                    {linked.slice(0, 5).map((m) => (
                      <FlagTab key={m.f.id} n={m.n} risk={m.f.risk} className="!h-6 !min-w-[2rem] !rounded-[3px] ring-2 ring-paper-soft" />
                    ))}
                  </span>
                  <span className="flex-1 text-sm font-semibold leading-snug text-ink">
                    {asks.length} {asks.length === 1 ? "ask" : "asks"} from {pageRange(linked.map((m) => m.f.page))}
                    {extra > 0 && `, plus ${extra} new ${extra === 1 ? "clause" : "clauses"}`}
                  </span>
                  <span aria-hidden="true" className="text-ink-mute">▾</span>
                </summary>
                <div className="border-t border-paper-line px-3 py-3">{askList}</div>
              </details>

              <div className="flex items-end justify-between gap-3">
                <label htmlFor="draft-subject" className="label text-ink-soft">
                  Subject
                </label>
                <button
                  type="button"
                  onClick={() => setEditing((e) => !e)}
                  aria-pressed={editing}
                  className="-mb-2 -mr-2 inline-flex min-h-[44px] items-center px-2 text-sm font-semibold text-desk underline underline-offset-4 hover:text-ink"
                >
                  {editing ? "Done editing" : "Edit the letter"}
                </button>
              </div>
              <textarea
                id="draft-subject"
                ref={subjectRef}
                rows={1}
                value={subject}
                onChange={(e) => setSubject(e.target.value.replace(/\n/g, " "))}
                className="mt-1 block w-full resize-none overflow-hidden border-0 border-b-2 border-paper-line bg-transparent p-0 pb-2 text-lg font-semibold leading-snug text-ink focus:border-ink sm:text-xl"
              />

              {editing ? (
                <>
                  <label htmlFor="draft-body" className="label mt-6 block text-ink-soft">
                    Letter
                  </label>
                  <textarea
                    id="draft-body"
                    ref={bodyRef}
                    value={body}
                    onChange={(e) => setBody(e.target.value)}
                    className="mt-2 block w-full resize-none overflow-hidden border-0 bg-transparent p-0 font-doc text-base leading-[1.6] text-ink"
                  />
                </>
              ) : (
                <div ref={letterRef} aria-label="Letter" role="document" data-user-content="" className="mt-6 font-doc text-base leading-[1.6] text-ink">
                  {blocks.map((b, i) => (
                    <div key={i} data-part={b.m ? "ask" : "text"} className={`relative ${i ? "mt-4" : ""}`}>
                      {b.m && (
                        <button
                          type="button"
                          data-flag=""
                          onClick={() => onShow(b.m!.f.id)}
                          title={`Show finding ${b.m.n} on the contract`}
                          className="mb-1 inline-flex min-h-[44px] items-center gap-2 sm:absolute sm:-left-12 sm:top-[-0.55rem] sm:mb-0"
                        >
                          <FlagTab n={b.m.n} risk={b.m.f.risk} className="!h-6 !min-w-[2rem] !rounded-[3px]" />
                          <span className="meta font-sans text-ink-mute sm:sr-only">Finding {b.m.n}, page {b.m.f.page}</span>
                        </button>
                      )}
                      <p className="whitespace-pre-line">{b.text}</p>
                    </div>
                  ))}
                </div>
              )}
              {leftBlank && (
                <p className="mt-3 text-sm font-medium text-medium">Fill in the parts in [square brackets] before you send it.</p>
              )}
            </div>

            <aside aria-labelledby="asks-title" className="hidden border-l border-paper-line bg-paper-soft px-6 py-7 md:block">
              <h3 id="asks-title" className="label text-ink">
                This letter asks for
              </h3>
              <p className="mt-1 text-sm text-ink-mute">Numbers match the flags on the contract.</p>
              <div className="mt-4">{askList}</div>
            </aside>
          </div>

          <footer ref={footRef} className="flex shrink-0 flex-wrap items-center gap-3 border-t border-paper-line bg-paper px-5 py-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] sm:px-9 sm:py-4">
            <button type="button" onClick={copy} className="btn-ink flex-1 whitespace-nowrap !px-3 sm:flex-none sm:!px-5">
              Copy the letter
            </button>
            <a href={mailto} className="btn-line flex-1 whitespace-nowrap !px-3 sm:flex-none sm:!px-5">
              Open in my email
            </a>
            <span role="status" className="w-full text-sm font-semibold text-low sm:w-auto">
              {copied === "ok" && "Copied. Paste it into your email."}
              {copied === "fail" && <span className="text-high">Copy did not work. Select the letter and copy it by hand.</span>}
            </span>
          </footer>
        </div>
      )}
    </dialog>
  );
}
