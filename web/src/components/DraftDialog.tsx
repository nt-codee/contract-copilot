import { useEffect, useLayoutEffect, useRef, useState } from "react";
import type { RefObject } from "react";
import type { NegotiationDraft } from "../types";
import type { Marked } from "./Reader";
import { FlagTab } from "./Risk";

interface Props {
  draft: NegotiationDraft | null;
  open: boolean;
  items: Marked[];
  onClose: () => void;
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

/** Grows a textarea to fit its text, so the letter scrolls with the dialog, not inside itself. */
function useAutoHeight(ref: RefObject<HTMLTextAreaElement>, value: string, open: boolean) {
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight + 2}px`;
  }, [ref, value, open]);
}

/** The reply letter, laid over the contract it answers. */
export default function DraftDialog({ draft, open, items, onClose }: Props) {
  const ref = useRef<HTMLDialogElement>(null);
  const headRef = useRef<HTMLHeadingElement>(null);
  const subjectRef = useRef<HTMLTextAreaElement>(null);
  const bodyRef = useRef<HTMLTextAreaElement>(null);
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [copied, setCopied] = useState<"" | "ok" | "fail">("");
  const [width, setWidth] = useState(0);

  useEffect(() => {
    if (draft) {
      setSubject(draft.subject);
      setBody(tidy(draft.body));
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

  useAutoHeight(subjectRef, subject + width, open);
  useAutoHeight(bodyRef, body + width, open);

  async function copy() {
    try {
      await navigator.clipboard.writeText(`Subject: ${subject}\n\n${body}`);
      setCopied("ok");
    } catch {
      setCopied("fail");
    }
    window.setTimeout(() => setCopied(""), 3000);
  }

  const mailto = `mailto:?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`;
  const byId = new Map(items.map((m) => [m.f.id, m]));
  const asks = draft?.asks ?? [];
  const linked = asks.map((a) => byId.get(a.finding_id)).filter((m): m is Marked => !!m);
  const extra = asks.length - linked.length;
  const leftBlank = /\[[^\]\n]{1,40}\]/.test(body);

  const askList = (
    <ol className="space-y-3">
      {asks.map((a) => {
        const m = byId.get(a.finding_id);
        return (
          <li key={a.finding_id + a.ask} className="flex gap-3 text-[0.95rem] leading-snug">
            {m ? (
              <FlagTab n={m.n} risk={m.f.risk} className="!rounded-[3px]" />
            ) : (
              <span aria-hidden="true" className="inline-flex h-7 min-w-[2.6rem] shrink-0 items-center justify-center rounded-[3px] border-2 border-dashed border-ink-mute text-[1.1rem] font-bold leading-none text-ink">
                +
              </span>
            )}
            <span>
              {a.ask}
              <span className="tab mt-0.5 block text-ink-mute">{m ? `Page ${m.f.page}` : "New clause"}</span>
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
        <div className="rise flex h-full max-h-[100dvh] flex-col bg-paper sm:max-h-[calc(100vh-48px)] sm:rounded-[3px] sm:shadow-lift">
          <header className="flex shrink-0 items-center justify-between gap-4 bg-ink px-5 py-3 text-white sm:px-7">
            <h2 id="draft-title" ref={headRef} tabIndex={-1} className="tab !text-[0.95rem]">
              Your reply to the vendor
            </h2>
            <button type="button" onClick={onClose} className="btn -mr-3 min-w-[44px] !px-3 text-white hover:bg-white/10">
              Close
            </button>
          </header>

          <div className="grid min-h-0 flex-1 overflow-y-auto overscroll-contain md:grid-cols-[minmax(0,1fr)_320px]">
            <div className="px-5 pb-8 pt-5 sm:px-9 sm:pt-7">
              {/* Phones: what the letter asks for, in one line that opens. */}
              <details className="mb-5 rounded-[3px] border border-paper-line bg-paper-soft md:hidden">
                <summary className="flex min-h-[48px] cursor-pointer list-none items-center gap-2 px-3 py-2 [&::-webkit-details-marker]:hidden">
                  <span className="flex -space-x-1" aria-hidden="true">
                    {linked.slice(0, 5).map((m) => (
                      <FlagTab key={m.f.id} n={m.n} risk={m.f.risk} className="!h-6 !min-w-[2rem] !rounded-[3px] ring-2 ring-paper-soft" />
                    ))}
                  </span>
                  <span className="flex-1 text-[0.92rem] font-semibold leading-snug text-ink">
                    {asks.length} {asks.length === 1 ? "ask" : "asks"} from {pageRange(linked.map((m) => m.f.page))}
                    {extra > 0 && `, plus ${extra} new ${extra === 1 ? "clause" : "clauses"}`}
                  </span>
                  <span aria-hidden="true" className="text-ink-mute">▾</span>
                </summary>
                <div className="border-t border-paper-line px-3 py-3">{askList}</div>
              </details>

              <label htmlFor="draft-subject" className="tab text-ink-mute">
                Subject
              </label>
              <textarea
                id="draft-subject"
                ref={subjectRef}
                rows={1}
                value={subject}
                onChange={(e) => setSubject(e.target.value.replace(/\n/g, " "))}
                className="mt-1 block w-full resize-none overflow-hidden border-0 border-b-2 border-paper-line bg-transparent p-0 pb-2 text-[1.15rem] font-semibold leading-snug text-ink focus:border-ink sm:text-[1.25rem]"
              />
              <label htmlFor="draft-body" className="tab mt-6 block text-ink-mute">
                Letter
              </label>
              <textarea
                id="draft-body"
                ref={bodyRef}
                value={body}
                onChange={(e) => setBody(e.target.value)}
                className="mt-2 block w-full resize-none overflow-hidden border-0 bg-transparent p-0 font-doc text-[1.06rem] leading-[1.6] text-ink"
              />
              {leftBlank && (
                <p className="mt-3 text-[0.92rem] font-medium text-medium">Fill in the parts in [square brackets] before you send it.</p>
              )}
            </div>

            <aside aria-labelledby="asks-title" className="hidden border-l border-paper-line bg-paper-soft px-6 py-7 md:block">
              <h3 id="asks-title" className="tab text-ink">
                This letter asks for
              </h3>
              <p className="mt-1 text-[0.88rem] text-ink-mute">Numbers match the flags on the contract.</p>
              <div className="mt-4">{askList}</div>
            </aside>
          </div>

          <footer className="flex shrink-0 flex-wrap items-center gap-3 border-t border-paper-line bg-paper px-5 py-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] sm:px-9 sm:py-4">
            <button type="button" onClick={copy} className="btn-ink flex-1 whitespace-nowrap !px-3 sm:flex-none sm:!px-5">
              Copy the letter
            </button>
            <a href={mailto} className="btn-line flex-1 whitespace-nowrap !px-3 sm:flex-none sm:!px-5">
              Open in my email
            </a>
            <span role="status" className="w-full text-[0.95rem] font-semibold text-low sm:w-auto">
              {copied === "ok" && "Copied. Paste it into your email."}
              {copied === "fail" && <span className="text-high">Copy did not work. Select the letter and copy it by hand.</span>}
            </span>
          </footer>
        </div>
      )}
    </dialog>
  );
}
