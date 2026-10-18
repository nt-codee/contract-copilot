import { useEffect } from "react";
import type { ReactNode } from "react";

const CONTACT = "namtungchoong@gmail.com";
const UPDATED = "18 October 2026";

/** A page set like the documents the app reviews: white paper on the desk, numbered clauses. */
function LegalPage({ title, intro, children }: { title: string; intro: string; children: ReactNode }) {
  useEffect(() => {
    document.title = `${title} · Contract Co-pilot`;
    return () => {
      document.title = "Contract Co-pilot";
    };
  }, [title]);
  return (
    <div className="mx-auto max-w-[860px] px-5 pb-20 pt-8 sm:px-8 sm:pt-12">
      <article className="sheet px-5 py-8 sm:px-12 sm:py-12">
        <p className="meta text-ink-mute">Last updated {UPDATED}</p>
        <h1 className="display mt-2 text-2xl text-ink sm:text-2xl">{title}</h1>
        <p className="mt-4 text-base leading-relaxed text-ink-soft">{intro}</p>
        <ol className="mt-8 space-y-6 border-t border-paper-line pt-6">{children}</ol>
        <p className="mt-10 border-t border-paper-line pt-5 text-sm text-ink-soft">
          Questions: <a href={`mailto:${CONTACT}`} className="font-semibold text-desk underline underline-offset-4 hover:text-ink">{CONTACT}</a>
        </p>
      </article>
    </div>
  );
}

function Clause({ n, title, children }: { n: number; title: string; children: ReactNode }) {
  return (
    <li className="grid grid-cols-[2rem_minmax(0,1fr)] gap-x-2">
      <span className="font-doc text-lg font-bold text-ink">{n}.</span>
      <div>
        <h2 className="font-doc text-lg font-bold leading-snug text-ink">{title}</h2>
        <div className="mt-1.5 space-y-2 text-base leading-relaxed text-ink">{children}</div>
      </div>
    </li>
  );
}

export function Privacy() {
  return (
    <LegalPage title="Privacy" intro="What Contract Co-pilot collects, where it is kept, and how to remove it.">
      <Clause n={1} title="Who runs it">
        <p>Contract Co-pilot is a hackathon project. It reviews vendor contracts for small businesses in Malaysia and Singapore.</p>
      </Clause>
      <Clause n={2} title="Google sign-in">
        <p>When you sign in with Google we receive only your name, email address and profile photo. We use them to identify you and your workspace.</p>
      </Clause>
      <Clause n={3} title="What you upload">
        <p>Contracts and company policies you upload are stored in Google Cloud in Singapore (region asia-southeast1), only for your workspace.</p>
        <p>Uploaded files are deleted after 7 days. Reviews are kept until you delete them.</p>
      </Clause>
      <Clause n={4} title="How your documents are processed">
        <p>To produce a review, the text of your document is processed by Google’s Gemini models on Vertex AI. Your content is not used to train models.</p>
      </Clause>
      <Clause n={5} title="What we do not do">
        <p>No ads. No selling of data. No third-party analytics.</p>
      </Clause>
      <Clause n={6} title="Deleting your data">
        <p>“Delete my data” in your account menu removes everything for your account: uploads, reviews and your sign-in record.</p>
      </Clause>
      <Clause n={7} title="The sample workspace">
        <p>The public sample workspace contains only made-up contracts and a made-up company policy. No real people or companies.</p>
      </Clause>
    </LegalPage>
  );
}

export function Terms() {
  return (
    <LegalPage title="Terms of use" intro="The short version: it helps you prepare, it is not a lawyer, and it is provided as is.">
      <Clause n={1} title="General information, not legal advice">
        <p>Reviews explain contract terms in plain words to help you prepare for a conversation with the other side. They are not legal advice and do not replace a lawyer for a contract that matters.</p>
      </Clause>
      <Clause n={2} title="Use at your own risk">
        <p>You decide what to sign. Check anything important against the contract itself and, where needed, with a qualified lawyer.</p>
      </Clause>
      <Clause n={3} title="Only upload what you may share">
        <p>Do not upload documents you are not allowed to share, for example ones under a confidentiality duty that forbids it.</p>
      </Clause>
      <Clause n={4} title="Keeping the service running">
        <p>We may limit or suspend use, for example the number of reviews per hour, to keep the service available for everyone.</p>
      </Clause>
      <Clause n={5} title="Provided as is">
        <p>Contract Co-pilot is a hackathon project provided as is, without any warranty that it is complete, accurate or always available.</p>
      </Clause>
    </LegalPage>
  );
}
