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

## Current Challenge: One-Sided Observers and Stable Boundaries

A new test case (`fake-Arunchal-Pradesh`, commit `cae40c8e`) introduces a region
where CHN claims Arunachal Pradesh and IND does not from PAK's perspective
(`IND.perspectives.PAK = [-Arunchal-Pradesh]`). The expected behavior:

- (3.0, 1.5) — IND/CHN boundary at northern edge of Arunachal: **stable for PAK**
- (4.0, 1.0) — CHN/NPL boundary at eastern edge of Arunachal: **stable for PAK**

PAK has taken a side (endorsing CHN's claim), so it should see the outer border
of what it considers CHN territory as stable, not as nonexistent or disputed.

The problem is that PAK only appears as an observer of CHN's Arunachal claim,
not of IND's or NPL's adjacent claim. So `common_observers` (the intersection
of both sides' observer sets) never contains PAK for those boundary combos. PAK
gets classified either as nonexistent (via endorser_split from another combo) or
not at all.

### Approach attempted: one-sided observer promotion

Observers present on only one side of a different-owner boundary, with an
endorsement for that owner, should go to `stable_believers`. Rule: "they've
taken a side and aren't neutral." This was also extended to the same-owner
branch.

### Why it fails

The `itertools.product` loop processes all pairings of claims1 × claims2. PAK
can appear in `endorser_split` from one combo (CHN vs IND, inside endorsed
geometry → nonexistent) and simultaneously be promoted to `stable_believers`
from another combo (IND self-boundary, one-sided rule). The `emit_border()`
assertion then fires because PAK is in both `stable` and `nonexistent`.

The core difficulty: classifications accumulate across all combos and can
conflict. A single observer may be correctly nonexistent for one sub-boundary
segment and correctly stable for another, but the geometry splitting only happens
for `endorser_split`, not for the stable/disputed/nonexistent sets at large.

### Open question

Should per-observer classification be resolved *after* all combos are processed,
or should the geometry be split more aggressively so that stable and nonexistent
segments are never mixed in one record? The latter would require tracking per-observer
endorsed geometries for stable classifications the same way endorser_split does
for nonexistent ones — potentially a much larger refactor.
