"""Typed source-review resolutions of an extraction finding.

A finding is resolved with a reason (always). Some findings can also carry a TYPED resolution
that says what the reviewer concluded; anything else is refused rather than recorded as free text
that looks like a decision."""

NO_SCHEDULING_EFFECT = "NO_SCHEDULING_EFFECT"

#: issue code -> typed resolutions it accepts
ALLOWED = {"FOOTNOTE_NOT_INTERPRETED": {NO_SCHEDULING_EFFECT}}


def checked_resolution(resolution: str | None, issue_code: str) -> str | None:
    if resolution is None:
        return None
    if resolution not in ALLOWED.get(issue_code, set()):
        raise ValueError(f"{resolution!r} is not a resolution this finding accepts")
    return resolution
