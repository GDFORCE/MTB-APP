// @ts-nocheck
// Mechanical TypeScript port of the Protocol Visit Scheduler kit (src/shared/utils.js). The logic is
// deliberately unchanged so results match the kit; typed entry points are in ./index.ts.
// Shared helpers used by every module.
// All dates are handled as UTC midnight timestamps (milliseconds) so that
// time zones and daylight-saving changes never shift a visit by a day.

export const DAY = 864e5; // one day in milliseconds

/** "2026-10-22" -> UTC midnight timestamp */
export const parseD = (s) => {
  const [y, m, d] = String(s).split("-").map(Number);
  return Date.UTC(y, m - 1, d);
};
/** timestamp + n whole days */
export const addD = (t, n) => t + Math.round(n) * DAY;
/** timestamp -> "2026-10-22" */
export const isoD = (t) => new Date(t).toISOString().slice(0, 10);
/** timestamp -> "Thu, 22 Oct 2026" */
export const fmt = (t) => new Date(t).toLocaleDateString("en-GB", { timeZone: "UTC", weekday: "short", day: "2-digit", month: "short", year: "numeric" });
/** timestamp -> "22 Oct" */
export const fmtS = (t) => new Date(t).toLocaleDateString("en-GB", { timeZone: "UTC", day: "2-digit", month: "short" });
/** timestamp -> "22 Oct 2026" */
export const fmtD = (t) => new Date(t).toLocaleDateString("en-GB", { timeZone: "UTC", day: "2-digit", month: "short", year: "numeric" });
/** 0 = Sunday ... 6 = Saturday */
export const dow = (t) => new Date(t).getUTCDay();
/** today's date (local calendar day) as a UTC midnight timestamp */
export const todayUTC = () => {
  const n = new Date();
  return Date.UTC(n.getFullYear(), n.getMonth(), n.getDate());
};

export const arr = (x) => (Array.isArray(x) ? x : []);
export const clone = (x) => JSON.parse(JSON.stringify(x));
export const uid = () => Math.random().toString(36).slice(2, 10);
/** number or fallback (treats "", null, NaN as missing) */
export const num = (x, d = 0) => (x !== null && x !== "" && Number.isFinite(+x) ? +x : d);

/** "09:15" -> 555 minutes since midnight, or null */
export const toMin = (hhmm) => {
  const m = /^(\d{1,2}):(\d{2})$/.exec(hhmm || "");
  return m ? +m[1] * 60 + +m[2] : null;
};
/** 555 -> "09:15", 1500 -> "01:00 (+1 d)" */
export const fmtMin = (m) => {
  const d = Math.floor(m / 1440);
  m = ((m % 1440) + 1440) % 1440;
  return `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}${d > 0 ? ` (+${d} d)` : d < 0 ? ` (${d} d)` : ""}`;
};
/** minutes offset from dose -> "+2 h", "−30 min", "dose" */
export const fmtOff = (m) => {
  const s = m < 0 ? "−" : "+";
  m = Math.abs(m);
  return m === 0 ? "dose" : m % 60 === 0 ? `${s}${m / 60} h` : m > 60 ? `${s}${Math.floor(m / 60)} h ${m % 60} min` : `${s}${m} min`;
};
/** day offset as a suffix: 0 -> "", 30 -> " + 30 d", -7 -> " − 7 d" */
export const sOff = (n) => (n === 0 ? "" : ` ${n > 0 ? "+" : "−"} ${Math.abs(n)} d`);
