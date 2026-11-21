"""Split sheet tier headers into a display label and a plain tier name.

nlw / lw write their section headers as "| Extreme Tier", ids / hds as
"↓ Extreme ↓". Most headers name a tier, but a few sections are not tiers at
all ("| Low End", "↓ Legacy ↓", "↓ Demote ↓"), so the card must print what the
sheet actually wrote instead of appending " Tier" to everything.
"""

_TIER_SUFFIX = " tier"


def split_tier_header(text: str) -> tuple[str, str]:
    """Return (label, tier) for one header cell, row markers already stripped.

    label is the sheet text verbatim, e.g. "Extreme Tier", "Low End",
    "Legacy". tier drops a trailing " Tier" so colour lookup and the
    `tier != "Legacy"` comparisons keep seeing the plain name.
    """
    label = text.strip()
    if label.lower().endswith(_TIER_SUFFIX):
        stripped = label[: -len(_TIER_SUFFIX)].strip()
        if stripped:
            return label, stripped
    return label, label
