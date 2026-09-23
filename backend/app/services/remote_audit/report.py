"""Audit report PDF — spec §13.4.

The document a sponsor hands to a regulator, so its job is to be honest rather
than flattering. Three rules it follows that a generic "export the score"
implementation would not:

1. **Blockers appear BEFORE the score**, on the first page. A reader who stops
   after the headline number must already have seen what is wrong with it.
2. **The two axes are printed as two numbers, never averaged.** A report that
   said "88%" for a site with perfect compliance on unverifiable evidence
   would be the single most misleading thing this module could produce.
3. **An override prints the machine's verdict alongside the human's**, with
   the stated reason. "The system said not approved, a person approved it, and
   here is why" is exactly what an inspector is looking for.

reportlab is already a dependency of this backend (see admin_routes.py's
existing export), so this adds no new vendor.
"""
from __future__ import annotations

import io
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


def _fmt_percent(value: Any) -> str:
    """NaN and None are not zero, and must not print as it."""
    if value is None:
        return "not scored"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "not scored"
    if number != number:  # NaN
        return "not scored"
    return f"{round(number, 1)}%"


def build_audit_report_pdf(
    *,
    audit: Dict[str, Any],
    score_run: Optional[Dict[str, Any]],
    decision: Optional[Dict[str, Any]],
    queries: Optional[List[Dict[str, Any]]] = None,
    capas: Optional[List[Dict[str, Any]]] = None,
    site_name: str = "",
    generated_by: str = "",
) -> bytes:
    from reportlab.lib import colors as rl_colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
    )

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=18 * mm, bottomMargin=18 * mm,
        title=f"Remote audit report — {audit.get('id', '')}")

    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("RAH1", parent=styles["Heading1"], fontSize=16, spaceAfter=6)
    h2 = ParagraphStyle("RAH2", parent=styles["Heading2"], fontSize=12, spaceBefore=10, spaceAfter=4)
    body = ParagraphStyle("RABody", parent=styles["BodyText"], fontSize=9.5, leading=13,
                          alignment=TA_LEFT)
    muted = ParagraphStyle("RAMuted", parent=body, textColor=rl_colors.HexColor("#666666"),
                           fontSize=8.5)

    story: List[Any] = []
    story.append(Paragraph("Remote Audit Report", h1))
    story.append(Paragraph(
        f"Audit {audit.get('id', '')}"
        + (f" &mdash; {site_name}" if site_name else ""), body))
    story.append(Paragraph(
        f"Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
        + (f" by {generated_by}" if generated_by else ""), muted))
    if audit.get("jurisdiction") or audit.get("governing_authority"):
        story.append(Paragraph(
            f"Jurisdiction: {audit.get('jurisdiction') or 'not stated'} &middot; "
            f"Governing authority: {audit.get('governing_authority') or 'not stated'}", muted))
    story.append(Spacer(1, 6 * mm))

    # ── Blockers FIRST, before any number ─────────────────────────────────
    blockers = list((score_run or {}).get("blockers") or [])
    story.append(Paragraph("Findings that affect this result", h2))
    if not score_run:
        story.append(Paragraph(
            "This audit has not been scored. There is no result to report.", body))
    elif not blockers:
        story.append(Paragraph("No blocking findings were recorded.", body))
    else:
        blockers.sort(key=lambda b: _SEVERITY_ORDER.get(str(b.get("severity")), 9))
        rows: List[List[Any]] = [["Severity", "Finding"]]
        for blocker in blockers:
            detail = str(blocker.get("explanation", ""))
            ids = blocker.get("requirement_ids") or []
            if ids:
                detail += f"<br/><font size=8 color='#666666'>{', '.join(str(i) for i in ids)}</font>"
            rows.append([str(blocker.get("severity", "")).upper(), Paragraph(detail, body)])
        table = Table(rows, colWidths=[24 * mm, 150 * mm], repeatRows=1)
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), rl_colors.HexColor("#F0E4E7")),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("GRID", (0, 0), (-1, -1), 0.4, rl_colors.HexColor("#DDDDDD")),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(table)
    story.append(Spacer(1, 6 * mm))

    # ── The two axes, side by side and never merged ───────────────────────
    story.append(Paragraph("Result", h2))
    if score_run:
        axes = Table(
            [
                ["Compliance", "Evidence confidence"],
                [_fmt_percent(score_run.get("compliance_percent")),
                 _fmt_percent(score_run.get("evidence_confidence_percent"))],
                [Paragraph("Did the site meet the requirements?", muted),
                 Paragraph("How far can the evidence for that be relied on?", muted)],
            ],
            colWidths=[87 * mm, 87 * mm])
        axes.setStyle(TableStyle([
            ("FONTSIZE", (0, 1), (-1, 1), 20),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("ALIGN", (0, 0), (-1, 1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("BOX", (0, 0), (-1, -1), 0.4, rl_colors.HexColor("#DDDDDD")),
            ("INNERGRID", (0, 0), (-1, -1), 0.4, rl_colors.HexColor("#DDDDDD")),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ]))
        story.append(axes)
        story.append(Spacer(1, 3 * mm))
        story.append(Paragraph(
            "These two figures answer different questions and are deliberately not combined. "
            "High compliance recorded on evidence that could not be verified is not the same as "
            "a well-evidenced pass.", muted))
        for note in score_run.get("notes") or []:
            story.append(Paragraph(f"&bull; {note}", muted))
        story.append(Spacer(1, 3 * mm))
        story.append(Paragraph(
            f"System proposal: <b>{score_run.get('proposed_decision', 'n/a')}</b> "
            f"(algorithm {score_run.get('algorithm_version', 'n/a')}, "
            f"run {score_run.get('score_run_id', 'n/a')})", body))
    story.append(Spacer(1, 5 * mm))

    # ── Sections ──────────────────────────────────────────────────────────
    sections = (score_run or {}).get("sections") or []
    if sections:
        story.append(Paragraph("By section", h2))
        rows = [["Section", "Weight", "Score", "N/A", "Unanswered", "Waived"]]
        for section in sections:
            rows.append([
                str(section.get("section_id", "")),
                f"{section.get('weight_percent', 0)}%",
                _fmt_percent(section.get("compliance_percent")),
                str(section.get("not_applicable_count", 0)),
                str(section.get("missing_count", 0)),
                str(section.get("waived_count", 0)),
            ])
        table = Table(rows, colWidths=[54 * mm, 22 * mm, 26 * mm, 24 * mm, 26 * mm, 22 * mm],
                      repeatRows=1)
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), rl_colors.HexColor("#F4E5D3")),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("GRID", (0, 0), (-1, -1), 0.4, rl_colors.HexColor("#DDDDDD")),
            ("ALIGN", (1, 1), (-1, -1), "CENTER"),
        ]))
        story.append(table)
        story.append(Paragraph(
            "Not-applicable requirements are excluded from the denominator rather than scored "
            "as zero. Unanswered requirements are counted and scored as zero.", muted))

    # ── The decision ──────────────────────────────────────────────────────
    story.append(PageBreak())
    story.append(Paragraph("Decision", h1))
    if not decision:
        story.append(Paragraph("No final decision has been recorded for this audit.", body))
    else:
        story.append(Paragraph(f"<b>{decision.get('decision', '')}</b>", h2))
        story.append(Paragraph(
            f"Recorded by {decision.get('decided_by', '')} on "
            f"{decision.get('decided_at', '')}", muted))
        if decision.get("signature_statement"):
            story.append(Spacer(1, 2 * mm))
            story.append(Paragraph(str(decision["signature_statement"]), body))
        if decision.get("overridden"):
            story.append(Spacer(1, 4 * mm))
            story.append(Paragraph("This decision overrode the system's proposal", h2))
            story.append(Paragraph(
                f"The system proposed <b>{decision.get('machine_proposed', 'n/a')}</b>; "
                f"<b>{decision.get('decision')}</b> was recorded instead.", body))
            story.append(Paragraph(
                f"Stated reason: {decision.get('override_reason') or 'none recorded'}", body))
        if decision.get("linked_capa_ids"):
            story.append(Spacer(1, 3 * mm))
            story.append(Paragraph(
                "Conditions: " + ", ".join(str(c) for c in decision["linked_capa_ids"]), body))

    # ── Outstanding work ──────────────────────────────────────────────────
    open_capas = [c for c in (capas or [])
                  if c.get("status") in ("OPEN", "IN_PROGRESS", "COMPLETED", "REOPENED")]
    if open_capas:
        story.append(Spacer(1, 5 * mm))
        story.append(Paragraph("Outstanding corrective actions", h2))
        rows: List[List[Any]] = [["Criticality", "Title", "Owner", "Due", "Status"]]
        for capa in open_capas:
            rows.append([
                str(capa.get("criticality", "")),
                Paragraph(str(capa.get("title", "")), body),
                str(capa.get("owner_user_id", "")),
                str(capa.get("due_date", "")),
                str(capa.get("status", "")),
            ])
        table = Table(rows, colWidths=[22 * mm, 66 * mm, 34 * mm, 26 * mm, 26 * mm], repeatRows=1)
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), rl_colors.HexColor("#F4E5D3")),
            ("FONTSIZE", (0, 0), (-1, -1), 8.5),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("GRID", (0, 0), (-1, -1), 0.4, rl_colors.HexColor("#DDDDDD")),
        ]))
        story.append(table)

    open_queries = [q for q in (queries or [])
                    if q.get("status") in ("OPEN", "RESPONDED", "REOPENED")]
    if open_queries:
        story.append(Spacer(1, 5 * mm))
        story.append(Paragraph("Open queries", h2))
        for query in open_queries:
            story.append(Paragraph(
                f"&bull; [{query.get('criticality', '')}] {query.get('title', '')} "
                f"&mdash; {query.get('status', '')}", body))

    story.append(Spacer(1, 8 * mm))
    story.append(Paragraph(
        "This report reflects the audit record at the time of generation. Evidence verification "
        "results, document checks and decisions are append-only: earlier runs remain retrievable "
        "and are not rewritten by later ones.", muted))

    doc.build(story)
    return buffer.getvalue()
