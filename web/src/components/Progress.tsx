import { useEffect, useState } from "react";
import ReviewPreview from "./ReviewPreview";
import type { ReviewPreviewSource } from "./ReviewPreview";
import type { ReviewStage } from "../api";

/** Only a caller with server-reported progress should supply a stage. */
const LABEL: Record<ReviewStage, string> = {
  reading: "Reading the contract",
  planning: "Planning the source checks",
  drafting: "Drafting the findings",
  researching: "Looking up sources",
  verifying: "Checking quotes and sources",
  locating: "Locating quotes on the pages",
};

/** Stages come only from server events; the clock measures elapsed time, never phase completion. */
export default function Progress({ subject, earlier, stage, source, embedded = false }: { subject: string; earlier?: string | null; stage?: ReviewStage; source?: ReviewPreviewSource; embedded?: boolean }) {
  const [secs, setSecs] = useState(0);
  useEffect(() => {
    setSecs(0);
    if (earlier) return;
    const started = Date.now();
    const t = setInterval(() => setSecs(Math.floor((Date.now() - started) / 1000)), 1000);
    return () => clearInterval(t);
  }, [subject, earlier]);
  const Heading = embedded ? "h2" : "h1";
  const visual = earlier ? "ready" : stage ?? "waiting";
  const status = earlier ? `${earlier}. Opening it.` : stage ? LABEL[stage] : "Waiting for the completed review";
  return (
    <div className={`mx-auto grid min-w-0 items-start gap-8 ${embedded ? "py-6" : "max-w-7xl px-5 py-8 sm:px-8 md:py-12"} ${source ? "lg:grid-cols-[minmax(0,340px)_minmax(0,1fr)]" : "max-w-5xl md:grid-cols-[minmax(0,1fr)_280px]"}`}>
      <div className="min-w-0 text-white">
        <p className="page-eyebrow">{earlier ? "Earlier review" : "Reviewing"}</p>
        <Heading className={`display mt-2 break-words ${embedded ? "text-xl" : "text-2xl"}`}>{subject}</Heading>
        <p role="status" className="mt-6 text-lg font-semibold">{status}</p>
        <p className="mt-3 text-sm text-desk-text">{earlier ? "This result is already available; a new review is not running." : "The review reads your contract, looks up sources and checks its quotes. The completed result includes the recorded steps."}</p>
        {!earlier && <p className="meta mt-4 text-desk-text">{secs} s elapsed.{!stage && " No live stage update received."}</p>}
      </div>
      {source ? <ReviewPreview key={source.url} source={source} running={!earlier} /> : <svg aria-hidden="true" viewBox="0 0 280 240" className={`progress-paper mx-auto w-full max-w-[280px] ${earlier ? "progress-ready" : ""}`} data-stage={visual}>
        <ellipse cx="140" cy="219" rx="108" ry="12" fill="#152B54" />
        <g transform="rotate(-7 140 120)"><rect x="57" y="23" width="151" height="190" rx="3" fill="#DCE2EC" /></g>
        <rect x="62" y="18" width="151" height="190" rx="3" fill="#FFFFFF" />
        <path d="M83 47h91M83 58h109M83 85h109M83 97h99M83 109h105M83 137h109M83 149h92M83 161h103M83 185h65" stroke="#DCE2EC" strokeWidth="4" />
        {(visual === "locating" || visual === "verifying" || visual === "ready") && <rect className="progress-mark" x="78" y="128" width="120" height="40" rx="2" fill="#E3EBFB" fillOpacity=".6" stroke="#2450C0" strokeWidth="2" />}
        {visual === "reading" && <g data-illustration="reading"><rect x="78" y="77" width="120" height="38" rx="2" fill="#E3EBFB" fillOpacity=".7" /><path d="M83 85h109M83 97h99M83 109h105" stroke="#2450C0" strokeWidth="3" /></g>}
        {visual === "planning" && <g data-illustration="planning" fill="#FFFFFF" stroke="#2450C0" strokeWidth="2"><path d="M111 89h56v49h-56zM139 89V69M139 138v22M111 113H91M167 113h21" /><circle cx="139" cy="63" r="7" /><circle cx="84" cy="113" r="7" /><circle cx="195" cy="113" r="7" /><circle cx="139" cy="167" r="7" /><path d="M123 106h31M123 119h22" /></g>}
        {visual === "drafting" && <g data-illustration="drafting"><path d="M83 137h82M83 149h58" stroke="#2450C0" strokeWidth="3" /><path d="m154 140 35-60 13 8-35 60-17 12z" fill="#E3EBFB" stroke="#2450C0" strokeWidth="3" /><path d="m183 90 13 8M154 140l13 8" fill="none" stroke="#2450C0" strokeWidth="2" /></g>}
        {visual === "locating" && <g data-illustration="locating" stroke="#2450C0" strokeWidth="3" fill="none"><path d="M73 141v-18h18M185 123h18v18M203 155v18h-18M91 173H73v-18" /><path d="M133 148h10m-5-5v10" /></g>}
        {(["waiting", "researching", "verifying", "ready"] as string[]).includes(visual) && <g className="progress-glass" data-illustration={visual}><circle cx="166" cy="105" r="29" fill="#E3EBFB" fillOpacity=".65" stroke="#2450C0" strokeWidth="5" /><path d="m187 127 26 26" stroke="#2450C0" strokeWidth="10" strokeLinecap="round" />{(visual === "verifying" || visual === "ready") && <path d="m154 105 8 8 17-19" fill="none" stroke="#2450C0" strokeWidth="3" />}</g>}
        <path d="M207 128h19v28l-9-6-10 6z" fill="#9DB9F5" />
      </svg>}
    </div>
  );
}
