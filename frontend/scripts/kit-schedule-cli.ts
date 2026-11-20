// Compute patient schedules from a kit model on the command line, with the same engine the app uses.
//
//   node --experimental-strip-types --import ./tests/kit-resolve-hook.mjs scripts/kit-schedule-cli.ts <job.json> <out.json>
//
// job.json: { "model": <kit model>, "site": { questionId: answer }, "records": { name: <participant record> },
//             "payloads": { name: <GET /patients/{id}/kit or /me/kit response> } }
// out.json: { name: <schedule as the app serializes it>, "patient:<name>": <the patient calendar's visit rows> }
// Used by the protocol bench (backend/scripts/kit_bench_compare.py); no network, no database.
import { readFileSync, writeFileSync } from "node:fs";

import { computeKitSchedule } from "../src/features/kit/index.ts";
import { kitVisitRows } from "../src/features/kit/patientRows.ts";

const [input, output] = process.argv.slice(2);
if (!input || !output) {
  console.error("usage: kit-schedule-cli.ts <job.json> <out.json>");
  process.exit(2);
}
const job = JSON.parse(readFileSync(input, "utf8"));
const out: Record<string, unknown> = {};
for (const [name, record] of Object.entries(job.records || {})) {
  try {
    out[name] = computeKitSchedule(job.model, job.site || {}, record as any);
  } catch (error) {
    out[name] = { error: String((error as Error)?.stack || error) };
  }
}
// The patient app's own consumer (patient/dashboard, calendar and visit screens read these rows).
for (const [name, payload] of Object.entries(job.payloads || {})) {
  try {
    out[`patient:${name}`] = kitVisitRows(payload as any);
  } catch (error) {
    out[`patient:${name}`] = { error: String((error as Error)?.stack || error) };
  }
}
writeFileSync(output, JSON.stringify(out, null, 1));
