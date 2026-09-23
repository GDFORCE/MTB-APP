import assert from "node:assert/strict";
import test from "node:test";

import {
  CAPA_OUTSTANDING,
  canCancelQuery,
  canDecide,
  canReopen,
  canReviewEffectiveness,
  canRespond,
  capaNextAction,
  computeOverdue,
  normalizeCapa,
  normalizeEffectivenessReview,
  normalizeQuery,
} from "../src/features/remote-audit/queries.ts";
import type { Capa } from "../src/features/remote-audit/queries.ts";

/**
 * Remote Audit — query/CAPA presentation rules (spec §11).
 *
 * These predicates decide which buttons a screen shows. Getting one wrong
 * doesn't crash anything — it offers an action the server then refuses, or
 * worse, hides an action someone needed. Both are silent, so they're tested.
 */

// Overrides are raw backend-shaped fields, since they go through
// `normalizeCapa` rather than being constructed directly.
const capa = (over: Record<string, unknown> = {}): Capa => normalizeCapa({
  id: "c1", audit_id: "a1", title: "Retrain staff", description: "",
  criticality: "minor", status: "OPEN", owner_user_id: "site-1",
  due_date: "2030-01-01", version: 1, ...over,
});

// ── Query action gating ─────────────────────────────────────────────────────

test("the site answers only while the ball is in its court", () => {
  assert.equal(canRespond("OPEN"), true);
  assert.equal(canRespond("REOPENED"), true);
  // Already answered, already closed, or withdrawn: nothing to answer.
  assert.equal(canRespond("RESPONDED"), false);
  assert.equal(canRespond("CLOSED"), false);
  assert.equal(canRespond("CANCELLED"), false);
});

test("a reviewer rules only on something that has been answered", () => {
  assert.equal(canDecide("RESPONDED"), true);
  // Closing an unanswered query would record a finding as resolved when
  // nobody ever addressed it.
  assert.equal(canDecide("OPEN"), false);
  assert.equal(canDecide("CLOSED"), false);
  assert.equal(canDecide("CANCELLED"), false);
});

test("only a closed query can be reopened", () => {
  assert.equal(canReopen("CLOSED"), true);
  for (const status of ["OPEN", "RESPONDED", "REOPENED", "CANCELLED"] as const) {
    assert.equal(canReopen(status), false, `${status} must not offer reopen`);
  }
});

test("a cancelled query offers nothing at all", () => {
  assert.equal(canRespond("CANCELLED"), false);
  assert.equal(canDecide("CANCELLED"), false);
  assert.equal(canReopen("CANCELLED"), false);
  assert.equal(canCancelQuery("CANCELLED"), false);
});

// ── Query normalization ─────────────────────────────────────────────────────

test("an unrecognized query status reads as OPEN, not as resolved", () => {
  // Defaulting to CLOSED would make a malformed response look like a finished
  // finding and quietly remove it from someone's work.
  assert.equal(normalizeQuery({ id: "q1" }).status, "OPEN");
});

test("an unrecognized criticality reads as minor, never as critical", () => {
  // The UI must not manufacture a decision-blocking finding out of a
  // malformed field — the server blocks on its own stored value.
  assert.equal(normalizeQuery({ id: "q", criticality: "URGENT!!" }).criticality, "minor");
  assert.equal(normalizeQuery({ id: "q", criticality: "critical" }).criticality, "critical");
});

test("the thread is preserved in order with every prior response", () => {
  const query = normalizeQuery({
    id: "q1", status: "RESPONDED", last_rejection_reason_code: "INSUFFICIENT_DETAIL",
    reopen_count: 2,
    responses: [
      { id: "r1", sequence: 1, author_id: "site", body: "First answer." },
      { id: "r2", sequence: 2, author_id: "site", body: "Second, fuller answer." },
    ],
  });
  assert.equal(query.responses.length, 2);
  assert.equal(query.responses[0].body, "First answer.");
  // A rejected answer stays visible; the record does not tidy itself up.
  assert.equal(query.lastRejectionReasonCode, "INSUFFICIENT_DETAIL");
  assert.equal(query.reopenCount, 2);
});

// ── CAPA: overdue is computed ───────────────────────────────────────────────

const AS_OF = new Date("2026-09-17T12:00:00Z");

test("a CAPA is overdue strictly after its due date", () => {
  assert.equal(computeOverdue("IN_PROGRESS", "2026-09-16", AS_OF), true);
  // Due today is not yet overdue: a CAPA has the whole of its due date.
  assert.equal(computeOverdue("IN_PROGRESS", "2026-09-17", AS_OF), false);
  assert.equal(computeOverdue("IN_PROGRESS", "2026-09-18", AS_OF), false);
});

test("a resolved CAPA is never overdue however old", () => {
  // Showing a CAPA closed last year as "overdue" would keep a resolved
  // finding permanently on someone's list.
  assert.equal(computeOverdue("CLOSED", "2019-01-01", AS_OF), false);
  assert.equal(computeOverdue("CANCELLED", "2019-01-01", AS_OF), false);
  assert.equal(computeOverdue("IN_PROGRESS", "2019-01-01", AS_OF), true);
});

test("a malformed or missing due date is not reported as overdue", () => {
  assert.equal(computeOverdue("OPEN", "not a date", AS_OF), false);
  assert.equal(computeOverdue("OPEN", "", AS_OF), false);
});

test("CAPA_OUTSTANDING treats cancelled as resolved, not outstanding", () => {
  // A decision blocked forever by a withdrawn finding is a real failure mode;
  // `status !== CLOSED` would produce exactly that.
  assert.equal(CAPA_OUTSTANDING.has("CANCELLED"), false);
  assert.equal(CAPA_OUTSTANDING.has("CLOSED"), false);
  for (const outstanding of ["OPEN", "IN_PROGRESS", "COMPLETED", "REOPENED"]) {
    assert.equal(CAPA_OUTSTANDING.has(outstanding), true);
  }
});

test("a completed CAPA is still outstanding until it has been reviewed", () => {
  // "The site says they fixed it" is not "we confirmed they fixed it".
  assert.equal(CAPA_OUTSTANDING.has("COMPLETED"), true);
});

// ── CAPA action gating ──────────────────────────────────────────────────────

test("only the accountable owner is offered the progress action", () => {
  assert.equal(capaNextAction(capa({ status: "OPEN" }), "site-1"), "START");
  assert.equal(capaNextAction(capa({ status: "IN_PROGRESS" }), "site-1"), "COMPLETE");
  // A reviewer watching the CAPA gets no button the server would refuse.
  assert.equal(capaNextAction(capa({ status: "OPEN" }), "reviewer-9"), null);
});

test("a reopened CAPA can be started again", () => {
  assert.equal(capaNextAction(capa({ status: "REOPENED" }), "site-1"), "START");
});

test("a closed or cancelled CAPA offers no further progress", () => {
  assert.equal(capaNextAction(capa({ status: "CLOSED" }), "site-1"), null);
  assert.equal(capaNextAction(capa({ status: "CANCELLED" }), "site-1"), null);
  assert.equal(capaNextAction(capa({ status: "COMPLETED" }), "site-1"), null);
});

test("the person who completed the work is never offered the effectiveness review", () => {
  const completed = capa({ status: "COMPLETED", completed_by: "site-1" });
  // Self-certification is precisely the failure an effectiveness review
  // exists to prevent, so the button is not shown — and the server refuses it
  // too, independently.
  assert.equal(canReviewEffectiveness(completed, "site-1"), false);
  assert.equal(canReviewEffectiveness(completed, "reviewer-9"), true);
});

test("the effectiveness review is offered only on COMPLETED", () => {
  for (const status of ["OPEN", "IN_PROGRESS", "REOPENED", "CLOSED", "CANCELLED"] as const) {
    assert.equal(
      canReviewEffectiveness(capa({ status, completed_by: "site-1" }), "reviewer-9"),
      false,
      `${status} must not offer an effectiveness review`,
    );
  }
});

test("an effectiveness review with an unknown outcome is dropped, not rendered", () => {
  assert.equal(normalizeEffectivenessReview({ outcome: "MAYBE", note: "…" }), null);
  assert.equal(normalizeEffectivenessReview(null), null);
  assert.equal(
    normalizeEffectivenessReview({ outcome: "EFFECTIVE", note: "Verified.", reviewed_by: "r" })?.outcome,
    "EFFECTIVE",
  );
});

test("a server-sent is_overdue is trusted, and absence falls back to computing it", () => {
  assert.equal(normalizeCapa({ id: "c", status: "IN_PROGRESS", due_date: "2030-01-01", is_overdue: true }).isOverdue, true);
  // No flag from the server: recompute rather than defaulting to "fine".
  assert.equal(normalizeCapa({ id: "c", status: "IN_PROGRESS", due_date: "2019-01-01" }).isOverdue, true);
  assert.equal(normalizeCapa({ id: "c", status: "IN_PROGRESS", due_date: "2099-01-01" }).isOverdue, false);
});
