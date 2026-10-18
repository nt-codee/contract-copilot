import type { Sample } from "./types";

const FEATURED = "msa-05-saas";
const TYPE_ORDER = ["service_agreement", "nda", "employment", "tenancy", "hire_purchase"];

/** One sample per contract type, the featured service agreement first. */
export function pickCurated(all: Sample[]): Sample[] {
  return TYPE_ORDER.map((t) => (t === "service_agreement" && all.find((s) => s.id === FEATURED)) || all.find((s) => s.contract_type === t))
    .filter((s): s is Sample => Boolean(s));
}

/** A different sample each time: never the one picked last in this tab. */
export function randomSample(all: Sample[]): Sample | undefined {
  if (all.length === 0) return undefined;
  const last = sessionStorage.getItem("cc.lastSample");
  const pool = all.length > 1 ? all.filter((s) => s.id !== last) : all;
  const s = pool[Math.floor(Math.random() * pool.length)];
  sessionStorage.setItem("cc.lastSample", s.id);
  return s;
}
