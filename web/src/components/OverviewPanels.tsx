import { Link } from "react-router-dom";
import { formatDay } from "../dates";
import type { Overview } from "../types";
import { Bars, C, Legend, Panel, PerDay, Removed, RiskStack, Stat } from "./Charts";

const TYPE: Record<string, string> = { nda: "NDA", service_agreement: "Service agreement", employment: "Employment", tenancy: "Tenancy", hire_purchase: "Hire purchase" };
const KIND: Record<string, string> = { law: "Law", case: "Court cases", playbook: "Usual terms", company: "Company rules" };

const money = (n: number) => `US$${n < 1 ? n.toFixed(3) : n.toFixed(2)}`;
const secs = (ms: number) => `${(ms / 1000).toFixed(1)} s`;

/** The shared body of the workspace dashboard and the admin dashboard. */
export default function OverviewPanels({ o, days, admin }: { o: Overview; days: number; admin: boolean }) {
  const byType = Object.entries(o.by_contract_type).map(([t, n]) => ({ type: TYPE[t] ?? t, count: n }));
  const risk = [{ label: "All contracts", ...o.by_risk }];
  const kinds = Object.entries(o.citations_by_kind).map(([k, v]) => ({ kind: KIND[k] ?? k, count: v }));
  const removed = (o.dropped_unverified.quotes ?? 0) + (o.dropped_unverified.citations ?? 0);

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Stat label={`Reviews · last ${days} days`} value={String(o.reviews)} note={`${o.by_risk.high} high risk`} />
        <Stat label={admin ? "People" : "Members"} value={String(o.users)} />
        <Stat label="Time per review" value={secs(o.latency_ms.p50)} note={`typical · slowest 1 in 20: ${secs(o.latency_ms.p95)}`} />
        <Stat label="Cost" value={money(o.cost_usd.total)} note={`${money(o.cost_usd.per_review_mean)} per review`} />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Panel title="Reviews per day" summary={`${o.reviews} reviews over ${days} days.`}>
          <PerDay data={o.reviews_by_day} />
        </Panel>
        <div className="grid gap-4">
          <Panel title="Overall risk of each contract" summary={`${o.by_risk.high} high, ${o.by_risk.medium} medium, ${o.by_risk.low} low.`}>
            <RiskStack data={risk} labelKey="label" height={64} />
            <Legend />
          </Panel>
          <Panel title="Contracts by type" summary={byType.map((r) => `${r.type}: ${r.count}`).join(", ")}>
            <Bars data={byType} labelKey="type" valueKey="count" color={C.desk} width={130} />
          </Panel>
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Panel title="Most flagged clauses" summary={o.top_clause_types.map((c) => `${c.clause_type}: ${c.high} high`).join(", ")}>
          <RiskStack data={o.top_clause_types} labelKey="clause_type" />
          <Legend />
        </Panel>
        <Panel title="Company rules broken most" summary={o.policy_breaches_by_rule.map((r) => `${r.rule}: ${r.count}`).join(", ") || "None"}>
          {o.policy_breaches_by_rule.length ? (
            <Bars data={o.policy_breaches_by_rule} labelKey="rule" valueKey="count" color={C.high} width={190} />
          ) : (
            <p className="text-ink-soft">No contract broke a company rule in this period.</p>
          )}
        </Panel>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Panel title="Sources each flag was checked against" summary={kinds.map((k) => `${k.kind}: ${k.count}`).join(", ")}>
          <Bars data={kinds} labelKey="kind" valueKey="count" color={C.desk} width={120} />
        </Panel>
        <Panel title="Laws and cases cited most" visual={false} summary={o.top_cited_sources.map((s) => `${s.title}: ${s.count}`).join(". ")}>
          <ol className="divide-y divide-paper-line">
            {o.top_cited_sources.map((s) => (
              <li key={s.ref} className="flex items-start justify-between gap-3 py-2">
                <Link to={`/knowledge/${encodeURIComponent(s.ref)}`} className="min-w-0 text-[0.95rem] leading-snug text-ink underline-offset-4 hover:underline">
                  {s.title}
                </Link>
                <span className="tab shrink-0 text-ink-mute">{s.count}×</span>
              </li>
            ))}
          </ol>
        </Panel>
      </div>

      <div className="grid gap-4 lg:grid-cols-[3fr_2fr]">
        <Panel title="Verification: points removed because they could not be confirmed" summary={`${o.dropped_unverified.quotes} quotes and ${o.dropped_unverified.citations} sources removed.`}>
          {o.removed_by_day ? (
            <Removed data={o.removed_by_day as unknown as Record<string, string | number>[]} />
          ) : (
            <p className="text-ink-soft">{removed} removed in this period.</p>
          )}
          <p className="mt-2 text-[0.9rem] text-ink-soft">
            {o.dropped_unverified.quotes} quotes not found in the contract · {o.dropped_unverified.citations} sources not in the library. None of them reached a user.
          </p>
        </Panel>
        <Panel title="Deadlines in the next 120 days" visual={false} summary={`${o.upcoming_deadlines.length} deadlines.`}>
          {o.upcoming_deadlines.length === 0 ? (
            <p className="text-ink-soft">No deadlines coming up.</p>
          ) : (
            <ol className="divide-y divide-paper-line">
              {o.upcoming_deadlines.map((d, i) => (
                <li key={i} className="flex items-baseline justify-between gap-3 py-2 text-[0.95rem]">
                  <span>
                    <span className={`tab block ${d.days_left <= 30 ? "text-high" : "text-ink-mute"}`}>
                      {formatDay(d.date)} <span className="normal-case">· in {d.days_left} days</span>
                    </span>
                    <span className="text-ink">{d.kind === "notice_deadline" ? "Last day to cancel" : "Renews"}</span>
                  </span>
                  <span className="tab text-ink-mute">{TYPE[d.contract_type] ?? d.contract_type}</span>
                </li>
              ))}
            </ol>
          )}
        </Panel>
      </div>
    </div>
  );
}
