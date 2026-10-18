// Who the reply goes to: the contract's other party, named the way the reader would.
import type { ContractType, ReviewResult } from "./types";

type Side = "business" | "individual";

const OTHER: Record<ContractType, Record<Side, string>> = {
  employment: { individual: "employer", business: "employee" },
  tenancy: { individual: "landlord", business: "landlord" },
  hire_purchase: { individual: "lender", business: "lender" },
  service_agreement: { individual: "vendor", business: "vendor" },
  nda: { individual: "other party", business: "other party" },
  other: { individual: "other party", business: "other party" },
};

/** "employer", "landlord", "lender", "vendor" or "other party". Perspective defaults to the
 *  usual reader of each pack: an individual for employment, a business otherwise. */
export function counterparty(r: Pick<ReviewResult, "contract_type" | "provenance">): string {
  const chosen = r.provenance?.options?.perspective;
  const side: Side = chosen === "business" || chosen === "individual" ? chosen : r.contract_type === "employment" ? "individual" : "business";
  return (OTHER[r.contract_type] ?? OTHER.other)[side];
}
