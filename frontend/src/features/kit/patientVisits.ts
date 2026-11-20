import { api } from "@/src/api/client";
import { getMyKit } from "./api";
import { kitVisitRows } from "./patientRows";

export { kitVisitRows };

/** One of the signed-in kit patient's visits by its key ("c1d1#2"), or null when they are not a kit patient. */
export async function fetchMyKitVisit(key: string): Promise<any | null> {
  const record = await getMyKit().catch(() => null);
  if (!record) return null;
  return kitVisitRows(record).find((row) => row.id === key) || null;
}

/** The signed-in patient's visit rows: computed for a kit patient, from /visits/mine for everyone else. */
export async function fetchMyVisits(): Promise<{ data: any[] }> {
  const record = await getMyKit().catch(() => null);
  return record ? { data: kitVisitRows(record) } : api.get("/visits/mine");
}
