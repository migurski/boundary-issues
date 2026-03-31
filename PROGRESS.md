# Progress: Endorsers cause nonexistent (not disputed) boundaries

## Problem

PAK was added as an endorser of CHN's claim on fake-Trans-Karakoram-Tract
(`CHN.perspectives.PAK = [+fake-Trans-Karakoram-Tract]`). The test suite
expected PAK to see the eastern edge of Trans-Karakoram (4.0, 3.7) as
**nonexistent** (PAK believes both sides are CHN territory), while still seeing
the Aksai Chin boundary (4.0, 2.7) as **disputed** (PAK has no endorsement
there).

## Root Cause

The MECE claims algorithm produces feature 1 as a single MULTIPOLYGON covering
both Aksai Chin (y=2–3) and TKT (y=3.5–4.5) with identical claimants including
PAK in the CHN observer set. Because both sub-polygons share the same claimants
string, `write_country_boundaries` computes a single boundary record for the
feature 1 ∩ feature 2 intersection — a MULTILINESTRING containing both the
Aksai Chin edge and the TKT edge — with uniform attributes across both segments.

The original code put all `common_observers` (including PAK) into
`disputed_believers` for same-ISO3 boundaries, so PAK was `disputed` for both
segments.

## Fix (commit b6dd078c)

**File:** `build-country-polygon.py`, `write_country_boundaries`

1. **Pre-compute endorsed territories.** For each all-additive non-self
   perspective entry (e.g. `CHN:PAK = [+TKT]`), apply the `+` operations from
   an empty base to get just the endorsed geometry (TKT polygon).

2. **Defer endorsers from `disputed` to a split queue.** In the same-ISO3
   branch, instead of unconditionally adding `common_observers` to
   `disputed_believers`, check whether each observer has a pre-computed endorsed
   geometry. If so, collect them in `endorser_split`.

3. **Emit split boundary records.** When `endorser_split` is non-empty, clip
   the boundary line against the union of endorsed territories and emit two
   records: one for the inside portion (endorser → `nonexistent`) and one for
   the outside portion (endorser → `disputed`).

This correctly classifies PAK as `nonexistent` at the TKT eastern edge and
`disputed` at the Aksai Chin eastern edge, while leaving all other boundaries
unaffected.

## Further Work: Removing the All-Plus Restriction (commit 949fe14b)

The original endorsed_geoms code required `all(s[0] == "plus" for s in shapes)`
— only all-additive perspectives were treated as endorsements. This was wrong:
a perspective with mixed or pure-minus ops should still count.

**New approach:** apply the observer's ops to an *empty* geometry. The result
is the territory those ops explicitly reference. For `CHN.perspectives.PAK =
[+TKT]`, applying to empty yields TKT. No all-plus restriction needed.

This required restructuring the test data: CHN's base no longer includes TKT;
instead TKT is added back in CHN's self-perspective and in PAK's perspective.
This matches the real data pattern where disputed territory is absent from the
neutral base.

The endorsed_geoms approach was also extended to the **different-owner** branch:
when PAK endorses CHN's claim on TKT, the IND/CHN boundary *inside* TKT should
also be nonexistent for PAK (PAK sees it as all CHN). This required checking
`endorsed_geoms` in the `iso3a != iso3b` branch too.

## Further Work: Pure-Minus Disendorsements (commit 75248443)

A perspective like `IND.perspectives.PAK = [-TKT]` means PAK says IND doesn't
have TKT — a disendorsement. Applying `[-TKT]` to empty yields empty, so the
all-from-empty approach misses it.

**Fix:** if applying ops to empty yields empty, fall back to applying ops to the
owner's neutral base and taking the difference. For `IND.perspectives.PAK =
[-TKT]`, applying to IND's base gives IND-without-TKT, and the difference is
TKT — the disendorsed territory.

Test data was updated to have both `CHN.perspectives.PAK = [+TKT]` (endorsement)
and `IND.perspectives.PAK = [-TKT]` (mirrored disendorsement), and both pass.

## Consistency Assertion (commit 48b51dd7)

Added `emit_border()` helper that asserts `stable`, `disputed`, and `nonexistent`
sets are mutually exclusive before writing each boundary row. This immediately
catches logic bugs where an observer ends up in two categories simultaneously.

## Fix: Representative Interior Points (current work)

The `fake-Arunchal-Pradesh` test case required classifying PAK as `stable` at
the outer borders of CHN's endorsed Arunachal Pradesh block.

### Root cause

The `endorser_split` approach split boundary lines by endorsed geometry and
classified endorsers as `nonexistent` for the inside portion. But when the
boundary line *is* the outer edge of the endorsed territory (e.g., the
IND/CHN boundary at lat=1.5 which is the top of Arunachal Pradesh), the
intersection of that line with the endorsed polygon was non-empty, causing PAK
to be classified as `nonexistent` rather than `stable`.

### Fix: rep_point containment test

After the `itertools.product` loop, use `shapely.representative_point()` on
each claim polygon (computed once and stored as a GeoDataFrame column) to test
which side of the boundary is "inside" the endorsed geometry:

1. **Refine `endorser_split`:** For each observer in `endorser_split`, check
   whether exactly one side's representative point is inside the endorsed
   geometry. If so, the boundary is the *edge* of endorsed territory → move the
   observer from `endorser_split` to `stable_believers`.

2. **One-sided observer promotion:** For observers in `endorsed_geoms` who never
   appeared in `common_observers` at all, apply the same containment test. If
   exactly one side is inside → `stable`.

This correctly classifies PAK as `stable` at the borders of CHN's endorsed
Arunachal Pradesh block, while preserving `nonexistent` for PAK at the internal
eastern edge of TKT where both sides are CHN territory.
