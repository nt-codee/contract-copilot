// A small markdown renderer for knowledge entries: headings, paragraphs,
// lists, quotes, bold, italics, inline code and links. Builds React nodes,
// never HTML strings, so entry text cannot inject markup.
import type { ReactNode } from "react";

function inline(text: string, key: string): ReactNode[] {
  const out: ReactNode[] = [];
  const re = /(\*\*([^*]+)\*\*|`([^`]+)`|\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)|(?<![\w*])\*([^*\s][^*]*)\*(?!\w)|(?<!\w)_([^_]+)_(?!\w))/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let i = 0;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const k = `${key}-${i++}`;
    if (m[2]) out.push(<strong key={k}>{m[2]}</strong>);
    else if (m[3]) out.push(<code key={k} className="rounded-sm bg-paper-soft px-1 text-[0.92em]">{m[3]}</code>);
    else if (m[4])
      out.push(
        <a key={k} href={m[5]} target="_blank" rel="noopener noreferrer" className="font-semibold text-desk underline underline-offset-4">
          {m[4]}
        </a>,
      );
    else out.push(<em key={k}>{m[6] ?? m[7]}</em>);
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

export function Markdown({ text }: { text: string }) {
  const lines = text.replace(/\r/g, "").split("\n");
  const blocks: ReactNode[] = [];
  let para: string[] = [];
  let list: { ordered: boolean; items: string[] } | null = null;
  let quote: string[] = [];

  const flush = () => {
    const k = `b${blocks.length}`;
    if (para.length) {
      blocks.push(<p key={k} className="mt-3 leading-relaxed">{inline(para.join(" "), k)}</p>);
      para = [];
    }
    if (list) {
      const items = list.items.map((it, j) => <li key={j}>{inline(it, `${k}-${j}`)}</li>);
      blocks.push(
        list.ordered ? (
          <ol key={`${k}l`} className="mt-3 list-decimal space-y-1.5 pl-6 leading-relaxed">{items}</ol>
        ) : (
          <ul key={`${k}l`} className="mt-3 list-disc space-y-1.5 pl-6 leading-relaxed">{items}</ul>
        ),
      );
      list = null;
    }
    if (quote.length) {
      blocks.push(
        <blockquote key={`${k}q`} className="mt-3 border-l-4 border-ink pl-4 font-doc text-[1.05rem] leading-relaxed">
          {inline(quote.join(" "), k)}
        </blockquote>,
      );
      quote = [];
    }
  };

  for (const raw of lines) {
    const line = raw.trimEnd();
    const h = line.match(/^(#{1,4})\s+(.*)$/);
    const li = line.match(/^\s*([-*]|\d+\.)\s+(.*)$/);
    if (!line.trim()) {
      flush();
    } else if (h) {
      flush();
      const k = `h${blocks.length}`;
      blocks.push(
        h[1].length <= 2 ? (
          <h2 key={k} className="tab mt-7 !text-[0.95rem] text-ink first:mt-0">{inline(h[2], k)}</h2>
        ) : (
          <h3 key={k} className="mt-5 font-semibold text-ink">{inline(h[2], k)}</h3>
        ),
      );
    } else if (line.startsWith(">")) {
      if (para.length || list) flush();
      quote.push(line.replace(/^>\s?/, ""));
    } else if (li) {
      if (para.length || quote.length) flush();
      const ordered = /\d/.test(li[1]);
      if (list && list.ordered !== ordered) flush();
      if (!list) list = { ordered, items: [] };
      list.items.push(li[2]);
    } else if (list && /^\s+/.test(raw)) {
      list.items[list.items.length - 1] += ` ${line.trim()}`;
    } else {
      if (list || quote.length) flush();
      // A paragraph that is one quoted sentence reads as quoted law text.
      para.push(line);
    }
  }
  flush();
  return <div className="text-[1rem] text-ink">{blocks}</div>;
}
