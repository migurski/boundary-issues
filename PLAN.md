# Plan: Perspective Radio Buttons in generate_preview_html()

## Context

The preview map in `processor.py` shows all geopolitical features (areas, boundaries, validation points) from all perspectives simultaneously. Each feature has a `perspectives` property — a semicolon-delimited string of ISO3 codes like `"CHN;IND;PAK"` — but no filtering is applied. Adding radio buttons lets the user cycle among perspectives interactively, filtering map layers to show only features relevant to the selected perspective.

## Critical File

`/Users/migurski/Documents/protomaps-political-multiviews/processor.py`

## Changes

### 1. Update call site (line 114)

```python
# Before
err10, _ = generate_preview_html(event, on_failure)
# After
err10, _ = generate_preview_html(event, clone_dir, on_failure)
```

### 2. Update function signature (line 488)

```python
def generate_preview_html(event: dict, clone_dir: str, on_failure: FailCallable) -> tuple[dict|None, None]:
```

### 3. Collect perspectives from CSVs (before building `html`)

Inside the `try` block, read all three CSVs using the same `csv.DictReader` pattern already used in `convert_csvs_to_geojson()`:

```python
csv_names = ('country-areas.csv', 'country-boundaries.csv', 'validation-points.csv')
perspective_set = set()
for csv_name in csv_names:
    csv_path = os.path.join(clone_dir, csv_name)
    if not os.path.exists(csv_path):
        continue
    with open(csv_path, newline='') as f:
        for row in csv.DictReader(f):
            for code in row.get('perspectives', '').split(';'):
                code = code.strip()
                if code:
                    perspective_set.add(code)
all_perspectives = sorted(perspective_set)
perspectives_json = json.dumps(all_perspectives)
```

### 4. Add CSS for controls panel

Extend the existing `<style>` tag with:

```css
#controls { position: absolute; top: 10px; left: 10px; background: rgba(255,255,255,0.9); padding: 8px 12px; border-radius: 4px; font-family: sans-serif; font-size: 13px; z-index: 1; }
#controls label { display: block; margin: 3px 0; cursor: pointer; }
```

(In the f-string, `{` and `}` in CSS must be doubled to `{{` and `}}`.)

### 5. Add controls div to `<body>`

After `<div id="map"></div>`, add:

```html
<div id="controls">
  <strong>Perspective</strong>
  <div id="perspective-radios"></div>
</div>
```

### 6. Add JavaScript after `map.addControl(...)`

Insert this JS block (all JS `{`/`}` doubled in f-string; `{perspectives_json}` is the Python interpolation):

```javascript
const perspectives = {perspectives_json};

function perspective_filter(perspective) {{
  return ["in", perspective, ["get", "perspectives"]];
}}

function apply_perspective(perspective) {{
  map.setFilter('areas', perspective_filter(perspective));
  map.setFilter('boundaries-agreed', ["all",
    ["==", ["get", "disputed"], false],
    perspective_filter(perspective)
  ]);
  map.setFilter('boundaries-disputed', ["all",
    ["==", ["get", "disputed"], true],
    perspective_filter(perspective)
  ]);
  map.setFilter('validation-points-interior', ["all",
    ["==", ["get", "relation"], "interior"],
    perspective_filter(perspective)
  ]);
  map.setFilter('validation-points-exterior', ["all",
    ["==", ["get", "relation"], "exterior"],
    perspective_filter(perspective)
  ]);
}}

const radios_div = document.getElementById('perspective-radios');
perspectives.forEach(function(p, i) {{
  const label = document.createElement('label');
  const input = document.createElement('input');
  input.type = 'radio';
  input.name = 'perspective';
  input.value = p;
  if (i === 0) {{ input.checked = true; }}
  input.addEventListener('change', function() {{
    if (this.checked) {{ apply_perspective(this.value); }}
  }});
  label.appendChild(input);
  label.appendChild(document.createTextNode(' ' + p));
  radios_div.appendChild(label);
}});

map.on('load', function() {{
  if (perspectives.length > 0) {{
    apply_perspective(perspectives[0]);
  }}
}});
```

`map.on('load')` is the correct MapLibre lifecycle point for `setFilter`. Static layer filters in the style definition remain as-is and are overridden on load.

## Notes

- `["in", value, string_expr]` in MapLibre does substring matching — correct for semicolon-delimited strings like `"CHN;IND;PAK"`
- If no CSVs are present, `all_perspectives` is `[]`, the radio container renders empty, and the JS `if (perspectives.length > 0)` guard prevents errors
- The areas layer currently has no filter; the initial perspective application adds one

## Verification

1. Trigger the Lambda/function locally (or inspect generated HTML) to confirm `perspectives` JS array is populated
2. Open the HTML in a browser — radio buttons should appear top-left, one per ISO3 code, sorted alphabetically
3. Click a radio — map layers should filter to show only features whose `perspectives` property contains that code
4. Verify boundaries still split by agreed/disputed, and validation points by interior/exterior, within the selected perspective
