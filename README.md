About this repo: https://medium.com/@michalmigurski/weeknotes-2026w04-boundary-issues-f037407a7f45

## Current Release: [v0.1](https://github.com/migurski/boundary-issues/releases/tag/v0.1)

Preview: https://boundary-issues.s3.us-west-2.amazonaws.com/releases/v0.1/preview.html#2.2/37.5/50.0

<img width="1404" height="779" alt="Screenshot 2026-03-23 at 1 13 47 PM" src="https://github.com/user-attachments/assets/06345f20-d41b-44aa-9dd8-23650dff5be1" />

## Output Format

Complete worked example from [`sample-EU.gpkg`](sample-EU.zip)

Countries are referenced by their [three-letter ISO-8166 codes](https://en.wikipedia.org/wiki/ISO_3166-1_alpha-3).

### `country-areas`

Overlapping areas showing extent of varying points of view. May include many rows per
country reflecting different international viewpoints.

- `iso3`: ISO3 code for country in this area
- `perspectives`: semicolon-delimited list of ISO3 codes for countries supporting this view
- `geometry`: Polygon or MultiPolygon in WGS84 coordinates

### `country-claims`

Non-overlapping [MECE](https://en.wikipedia.org/wiki/MECE_principle) coverage showing
assembled viewpoints for each unit of area.

- `claimants`: space-delimited groupings of owners and supporting observers
  - Grouping format: dash-delimited list of owners, then a colon, then semicolon-delimited list of ISO3 supporters
  - `PRT:ESP;FRA;ITA;PRT` – Portugal's territorial extent supported by its neighbors
  - `ESP-FRA:ESP;FRA;ITA;PRT` – Spain/France jointly-administered condominium recognized by all
  - `FRA:FRA ITA:ESP;ITA;PRT` – France and Italy’s competing territorial claims with respective points of view
- `geometry`: Polygon or MultiPolygon in WGS84 coordinates

### `country-boundaries`

Unique geographic boundaries separating `country-claims` with viewpoints for each one’s
status as stable, disputed, or non-existent.

- `stable`: semicolon-delimited list of countries who believe this boundary is stable
- `disputed`: semicolon-delimited list of countries who believe this boundary is disputed
- `nonexistent`: semicolon-delimited list of countries who believe this boundary is nonexistent
- `geometry`: LineString or MultiLineString in WGS84 coordinates

## Configuration Format

Complete worked example from [`sample-EU.yaml`](sample-EU.yaml).

### Single Countries

[Portugal](https://www.openstreetmap.org/relation/295480) has no territorial disputes or overlaps with its neighbors.

Single countries are expressed as dictionaries with a `base` geographic perspective
and optional `interior-points` expressing interior (x, y) test points in WGS84 coordinates.

```yaml
PRT:
  base:
    - [plus, relation, 295480] # Portugal
  interior-points:
    base:
      - [-9.136, 38.707] # Lisbon
```

### Condominiums

[Pheasant Island in the Bidasoa river](https://en.wikipedia.org/wiki/Pheasant_Island)
is a condominium under joint sovereignty of Spain and France. OSM relations for
[Spain](https://www.openstreetmap.org/relation/1311341#map=17/43.342689/-1.765172)
and [France](https://www.openstreetmap.org/relation/2202162#map=17/43.342689/-1.765172)
both claim the island and the two countries jointly administer it. Both borders should
be rendered as stable and un-disputed.

This is easy to model and validate with an interior point:

```yaml
ESP:
  base:
    - [plus, relation, 1311341] # Spain
  interior-points:
    base:
      - [-3.703, 40.416] # Madrid
      - [-1.766388, 43.342600] # Pheasant Island on the Bidasoa

FRA:
  base:
    - [plus, relation, 2202162] # France
  interior-points:
    base:
      - [2.348, 48.853] # Paris
      - [-1.764589, 43.343075] # Pheasant Island on the Bidasoa
```

### Disputes

Ownership of the [Mont Blanc summit](https://en.wikipedia.org/wiki/Mont_Blanc#Ownership_of_the_summit)
is disputed by France and Italy. [Italian officials](https://www.openstreetmap.org/relation/365331#map=14/45.83445/6.86122)
claim the border follows the watershed, splitting both summits between Italy and France.
[French officials](https://www.openstreetmap.org/relation/2202162#map=14/45.83445/6.86122)
claim the border avoids the two summits, placing them entirely with France. OSM relations
for the two countries reflect this by placing the summit within both territories.

As a dispute, this area must be added and subtracted from each country’s point of view
polygon. We also have to choose a base view for outside observers, so we go with NATO’s
decision to use data from the Italian national mapping agency. The France base view has
Italy subtracted from it, then added back at `perspectives` for the self-view. The Italy
base view is untouched but has France subtracted from it for the France view.

An interior test point within the area asserts each side’s point of view for testing,
and `exterior-points` expressing interior (x, y) test points in WGS84 coordinates
further assert each side’s mirroring alternative point of view. The resulting boundaries
around Mont Blance are therefore marked as disputed.


```yaml
FRA:
  base:
    - [plus, relation, 2202162] # France
    - [minus, relation, 365331] # Subtract south side of Mont Blanc, a part of Italy base
  perspectives:
    FRA:
      - [plus, relation, 2202162] # Re-add Mont Blanc
  interior-points:
    base:
      - [2.348, 48.853] # Paris
      - [-1.764589, 43.343075] # Pheasant Island on the Bidasoa
    FRA:
      - [6.867267, 45.830445] # Mont Blanc southern face
  exterior-points:
    ITA:
      - [6.867267, 45.830445] # Mont Blanc southern face

ITA:
  base:
    - [plus, relation, 365331] # Italy
  perspectives:
    FRA:
      - [minus, relation, 2202162] # Subtract south side of Mont Blanc
  interior-points:
    base:
      - [12.482, 41.893] # Rome
    ITA:
      - [6.867267, 45.830445] # Mont Blanc southern face
  exterior-points:
    FRA:
      - [6.867267, 45.830445] # Mont Blanc southern face
```

Disputes can exist among any number of countries, see [`config-IND-PAK-CHN.yaml`](config-IND-PAK-CHN.yaml)
for a representation of China, India, and Pakistan’s mutual territorial perspectives.
