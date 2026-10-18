import known from "./sampleTitles.json";
import { contractTypeLabel } from "./format";
import type { ContractType } from "./types";

const KNOWN = known as Record<string, { title: string; with?: string }>;

interface Named {
  filename: string;
  contract_type?: ContractType;
  /** Not sent yet by the list endpoint; used when it is. */
  title?: string | null;
  parties?: string[] | null;
}

/** "office-cleaning-services.pdf" -> "Office cleaning services"; drops a sample prefix like "svc-02-". */
function fromFilename(name: string): string {
  const base = name
    .replace(/\.(pdf|jpe?g|png)$/i, "")
    .replace(/^(nda|msa|svc|emp|ten|hp)-\d+-/i, "")
    .replace(/[-_]+/g, " ")
    .trim();
  if (!base) return name;
  return base.charAt(0).toUpperCase() + base.slice(1);
}

/** What people call the contract, never its file name when anything better is known. */
export function reviewTitle(r: Named): string {
  if (r.title) return r.title;
  const k = KNOWN[r.filename];
  if (k) return k.title;
  const t = fromFilename(r.filename);
  // A bare slug that says nothing ("scan 0042") reads better with its type.
  return /^(scan|document|contract|file|img|image)\b|^\d/i.test(t) && r.contract_type ? `${contractTypeLabel(r.contract_type)}: ${t}` : t;
}

/** The other side, when known: from the review's parties, or the demo company's own record. */
export function counterpartyName(r: Named): string | null {
  return KNOWN[r.filename]?.with ?? null;
}
