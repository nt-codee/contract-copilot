import { useEffect, useState } from "react";

/** Only a caller with server-reported progress should supply a stage. */
type Stage = "reading" | "researching" | "verifying" | "locating";
const LABEL: Record<Stage, string> = {
  reading: "Reading the contract",
  researching: "Looking up sources",
  verifying: "Checking quotes and sources",
  locating: "Locating quotes on the pages",
};

/** No estimated stage transitions: the current API returns the completed review in one response. */
export default function Progress({ subject, earlier, stage }: { subject: string; earlier?: string | null; stage?: Stage }) {
  const [secs, setSecs] = useState(0);
  useEffect(() => {
    setSecs(0);
    if (earlier) return;
    const t = setInterval(() => setSecs((s) => s + 1), 1000);
    return () => clearInterval(t);
  }, [subject, earlier]);
  const status = earlier ? `${earlier}. Opening it.` : stage ? LABEL[stage] : "Waiting for the completed review";
  return (
    <div className="mx-auto grid max-w-5xl items-center gap-8 px-5 py-12 sm:px-8 md:grid-cols-[minmax(0,1fr)_280px] md:py-20">
      <div className="min-w-0 text-white">
        <p className="page-eyebrow">{earlier ? "Earlier review" : "Reviewing"}</p>
        <h1 className="display mt-2 break-words text-2xl">{subject}</h1>
        <p role="status" className="mt-6 text-lg font-semibold">{status}</p>
        <p className="mt-3 text-sm text-desk-text">{earlier ? "This result is already available; a new review is not running." : "The review reads your contract, looks up sources and checks its quotes. The completed result includes the recorded steps."}</p>
        {!earlier && <p className="meta mt-4 text-desk-text">{secs} s elapsed.{!stage && " Live stage updates are not available."}</p>}
      </div>
      <svg aria-hidden="true" viewBox="0 0 280 240" className={`progress-paper mx-auto w-full max-w-[280px] ${earlier ? "progress-ready" : ""}`} data-stage={earlier ? "ready" : stage ?? "waiting"}>
        <ellipse cx="140" cy="219" rx="108" ry="12" fill="#152B54" />
        <g transform="rotate(-7 140 120)"><rect x="57" y="23" width="151" height="190" rx="3" fill="#DCE2EC" /></g>
        <rect x="62" y="18" width="151" height="190" rx="3" fill="#FFFFFF" />
        <path d="M83 47h91M83 58h109M83 85h109M83 97h99M83 109h105M83 137h109M83 149h92M83 161h103M83 185h65" stroke="#DCE2EC" strokeWidth="4" />
        <rect className="progress-mark" x="78" y="128" width="120" height="40" rx="2" fill="#E3EBFB" fillOpacity=".6" stroke="#2450C0" strokeWidth="2" />
        <g className="progress-glass"><circle cx="166" cy="105" r="29" fill="#E3EBFB" fillOpacity=".65" stroke="#2450C0" strokeWidth="5" /><path d="m187 127 26 26" stroke="#2450C0" strokeWidth="10" strokeLinecap="round" /><path d="m154 105 8 8 17-19" fill="none" stroke="#2450C0" strokeWidth="3" /></g>
        <path d="M207 128h19v28l-9-6-10 6z" fill="#9DB9F5" />
      </svg>
    </div>
  );
}
