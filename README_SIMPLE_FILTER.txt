# Vinted simplified product filter

Replace your old `main.py` with this one.

What changed:
- Filters now use title + description + brand + raw Apify text.
- Apple Watch filter is less strict:
  it allows listings where "SE 2" is in the description, not only title.
  It still rejects obvious Series 1-10 / Ultra.
- iPad filter is less strict:
  it allows broad iPad listings and rejects obvious wrong Apple products/accessory-only listings.
- Redmi filter stays stricter:
  it requires Redmi + Pad + Pro, so Redmi Pad SE should be rejected.

Recommended Railway variables:

CHECK_INTERVAL_SECONDS=300
MAX_ITEMS_PER_SEARCH=10
MIN_AI_SCORE_TO_SEND=5
ONLY_RECENT_MINUTES=10
SKIP_UNKNOWN_AGE=true
REJECT_BAD_CONDITIONS=true
REJECT_ACCESSORIES=true
ENABLE_MODEL_ALLOWLIST=true

Note:
ENABLE_MODEL_ALLOWLIST can stay true, but this simplified function no longer depends on strict allowlists for iPad/Apple Watch.
