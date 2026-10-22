"""An extraction retry is not an instruction to activate a protocol amendment."""
EXTRACTION_DRAFT = "EXTRACTION_DRAFT"


def activation_eligible(metadata, *, declared_lineage=False):
    # Only newly classified extraction drafts are restricted here. Historical
    # versions retain their established lifecycle; no backfill guesses intent.
    return (metadata or {}).get("version_intent") != EXTRACTION_DRAFT or declared_lineage
