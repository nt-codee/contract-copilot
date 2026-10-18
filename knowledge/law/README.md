# knowledge/law — verified Malaysian and Singapore law for NDAs and service agreements

General information, not legal advice.

A small knowledge base the review agent can cite: a statute section or a case, with the operative
words quoted exactly as found on a source fetched on 2026-10-18. It complements the playbook in
`knowledge/playbook/` (what balanced terms look like) with the law behind it (what a court or statute
says). Validate with `python3 scripts/check_kb.py`.

## Layout

- `law/<jurisdiction>/<id>.md`, jurisdiction `my` or `sg`. Frontmatter: `id, jurisdiction, kind
  (statute|case), title, citation, clause_types, source_url, retrieved, licence_note`.
  `clause_types` uses the `ClauseType` enum in `shared/contracts/review.schema.json`.
- Each file has three sections: `## Text` (statutes quoted verbatim; cases summarised with a short
  quoted holding), `## In plain words`, `## When it matters in an NDA or service agreement`.
- `index.json` lists every entry with `{id, jurisdiction, kind, title, citation, clause_types, file}`;
  `file` is relative to this folder.

## What it covers

| | Statutes | Cases |
|---|---|---|
| Malaysia (16) | Contracts Act 1950 ss 24, 28, 29, 30, 40, 74, 75; PDPA 2010 ss 5, 9, 12A, 12B, 129; Limitation Act 1953 s 6(1); Evidence Act 1950 s 92 | Cubic Electronics v Mars Telecommunications (FC, liquidated damages); CIMB Bank v Bourke (FC, exclusion clause void under s 29) |
| Singapore (16) | UCTA 1977 s 1(3) and First Schedule, ss 2, 3, 11; Misrepresentation Act 1967 s 3; PDPA 2012 ss 2(1)/4(2)-(3), 24, 25, 26C, 26D; Limitation Act 1959 s 6(1); Contracts (Rights of Third Parties) Act 2001 s 2 | Denka v Seraya (penalties); Man Financial v Wong (restraint of trade); Sembcorp Marine v PPL (implied terms); Lee Chee Wei v Tan (entire agreement) |

## What it does not cover

- No Malaysian case on s 28 restraint of trade: none could be verified against a fetched page
  that states the court and holding. The s 28 text itself is included.
- No verified authority on: shortened contractual claim periods in Singapore; whether
  non-solicitation clauses fall under Malaysian s 28; implied terms or entire-agreement clauses in
  Malaysia.
- No subsidiary legislation (PDPA regulations, PDPC/JPDP guidelines), sector rules, employment law,
  or anything outside NDAs and service agreements.
- Statute text reflects the versions shown on the official sites on 2026-10-18. Malaysia's
  Contracts Act reform (including a reasonableness test for s 28) was proposed in June 2026 but was
  not enacted at that date. Re-check before relying on any entry.

## Sources and how they were verified

| Source | Used for | How fetched and checked |
|---|---|---|
| Laws of Malaysia online, `lom.agc.gov.my` (AGC Malaysia) | All Malaysian statutes | The site rejects plain HTTP fetches ("Invalid request"). The official reprint PDFs were opened in a browser and their text extracted; each quote was copied from that text. Versions: Contracts Act reprint incorporating amendments to 1 Jan 2006 (LOM timeline shows no later amendment); PDPA reprint as at 15 Sep 2026, incorporating Act A1727; Limitation Act online updated text as at 1 Sep 2019; Evidence Act reprint as at 1 Nov 2017. Each was the version listed on the LOM "Updated" Principal Acts page. |
| Singapore Statutes Online, `sso.agc.gov.sg` (AGC Singapore) | All Singapore statutes | The site returns 403 to plain HTTP fetches. Each Act's whole-document page was opened in a browser; each showed "Current version as at 18 Oct 2026" (2020 Revised Edition, amendments to 1 Dec 2021). Amendment annotations after 2021 were checked: only the PDPA has them (Act 19 of 2025, Act 2 of 2026), and none touch the quoted sections. Annotations such as [40/2020] are omitted from quotes. |
| eLitigation, `www.elitigation.sg` (Supreme Court of Singapore) | All Singapore cases | Full judgment pages fetched; citation, case number, decision date, coram and the quoted paragraph checked against the page. |
| Wong & Partners/Baker McKenzie Global Litigation News | Both Malaysian cases | Full judgments are in subscription reports (MLJ/CLJ) and were not fetched. Citation, court and holding checked against the law-firm note, and for Cubic Electronics also against a practitioner blog quoting paras 65 and 70 and against University of Malaya law journal pages. Each `licence_note` names the sources. |

## Reuse and licence terms

- **Malaysia (LOM).** Terms & Conditions, read 2026-10-18: copyright Attorney General's Chambers of
  Malaysia, all rights reserved (cl 5); the online versions are not the reprint made under s 14 of
  the Revision of Laws Act 1968 (cl 7); a print-out is not a copy of the Gazette (cl 10); no
  warranty of accuracy (cl 1). The T&C grant no reuse licence. This KB quotes short extracts for
  reference with the source named; wider reproduction would need AGC permission.
- **Singapore (SSO).** Terms of Use, read 2026-10-18: content owned by AGC (cl 2-3) and not to be
  reproduced without permission except under cl 13. Cl 13 permits use or reproduction of Singapore
  legislation in any print or electronic material or platform if (a) it states that the
  legislation is subject to copyright of the Singapore Government and is used with the permission
  of AGC, and that users may check SSO for the latest version; (b) the producer is responsible for
  accuracy; (c) nothing suggests AGC or SSO endorses the material and SSO is not disrupted; and
  (d) automated extraction happens only between 3 a.m. and 7 a.m. Singapore time and is not
  abusive. SSO is an unofficial version, not authoritative (cl 8).
  Required notice: *Singapore legislation in this knowledge base is subject to copyright of the
  Singapore Government and is reproduced with the permission of the Attorney-General's Chambers.
  Users may check Singapore Statutes Online (https://sso.agc.gov.sg) for the latest version.*
- **Singapore judgments (eLitigation).** eLitigation links to the Singapore Courts terms of use
  (judiciary.gov.sg). Cl 6, read 2026-10-18: materials (c) Government of Singapore; "Apart from any
  fair dealings for the purposes of private study, research, criticism or review, as permitted in
  law, no part of The Website may be reproduced or reused for any commercial purposes whatsoever
  without our prior written permission." Only short passages are quoted, for review and research.
- **Law-firm notes and blogs.** Copyright of their authors; no licence. Used to verify holdings and
  quoted briefly with attribution.

The entries' "In plain words" and "When it matters" sections are original writing describing the law
in general terms. They are not advice for any particular person or contract.
