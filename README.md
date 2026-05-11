# QGIS Research Tools

A collection of QGIS Processing algorithms aimed at spatial analysis and research workflows.
These tools are designed to complement the existing **Research Tools** group in the QGIS Processing Toolbox,
and are being developed with the goal of contributing them to the official QGIS codebase.

---

## Tools

### 1. Max Line Inside Polygon (`mlip_qgis.py`)

Computes the most significant lines for each polygon in a vector layer:

| Output | Description |
|--------|-------------|
| **Interior line** | Longest diagonal completely contained within the polygon (connects two vertices) |
| **Exterior line** | Longest diagonal between vertices that intersects but is not fully inside the polygon |
| **Mid-point perpendicular** | Perpendicular to the interior line, clipped to the polygon boundary |
| **Maximum perpendicular** | The parallel to the above with the greatest length inside the polygon (maximum width) |

> The algorithm uses a brute-force O(n²) approach over the polygon vertices. For highly detailed polygons, consider simplifying the geometry beforehand.

<!-- Screenshot placeholder -->
<!-- ![Max Line Inside Polygon](images/mlip_example.png) -->

---

### 2. Random Move Points Inside (`rmpi_qgis.py`)

Takes a point layer where each point belongs to a group (identified by a field)
and randomly moves and rotates each group within a user-defined extent.

For each group the algorithm:
1. Computes the **center of gravity** (arithmetic mean of coordinates) and the **geometric centroid** (center of the bounding box).
2. Picks a random rotation angle (0–360°) and rotates the group around its center of gravity.
3. Calculates a random displacement that keeps the group inside the extent.
4. Applies the translation.

**Containment modes:**

| Mode | Behaviour | Speed |
|------|-----------|-------|
| **Center of gravity only** (default) | Ensures the center of gravity lands inside the extent. Individual points may go outside. | Fast — random quadrant sampling with up to 100 retries |
| **All points** | Rotates the group first, then derives the valid displacement range directly from the rotated bounding box. Guarantees no point goes outside. If the group is larger than the extent, it is not moved and a warning is raised. | Fast — single random draw, no retries |

**Outputs:**
- Moved and rotated points (original attributes preserved)
- Displaced centers of gravity
- Displaced geometric centroids
- Original centers of gravity
- Original geometric centroids

<!-- Screenshot placeholder -->
<!-- ![Random Move Points Inside](images/rmpi_example.png) -->

---

## Installation

1. Copy the `.py` file(s) into your QGIS Processing scripts folder:
   ```
   ~/.local/share/QGIS/QGIS3/profiles/default/processing/scripts/   # Linux / macOS
   %APPDATA%\QGIS\QGIS3\profiles\default\processing\scripts\        # Windows
   ```
2. In QGIS, open the **Processing Toolbox** and click **Scripts → Reload scripts**.
3. The tools will appear under the **Digdgeo** group.

### Requirements

These scripts run inside the QGIS Python environment and require:

| Package | Included with QGIS |
|---------|-------------------|
| `shapely` | ✅ Yes (≥ 1.8) |
| `numpy` | ✅ Yes |

No additional installation is needed.

---

## Usage

### Max Line Inside Polygon

| Parameter | Type | Description |
|-----------|------|-------------|
| Polygon layer | Vector (Polygon) | Input layer. MultiPolygon features are supported (largest part is used). |
| Calculate exterior line | Boolean | Default: `True` |
| Calculate mid-point perpendicular | Boolean | Default: `True` |
| Calculate maximum perpendicular | Boolean | Default: `True` |

<!-- Screenshot placeholder -->
<!-- ![MLIP dialog](images/mlip_dialog.png) -->

---

### Random Move Points Inside

| Parameter | Type | Description |
|-----------|------|-------------|
| Point layer | Vector (Point) | Input points. Must contain a group identifier field. |
| Movement extent | Extent | Area within which groups are placed. Can be drawn on the map, typed manually, or taken from any loaded layer. |
| Group identifier field | Field | Field that identifies which group each point belongs to. Default: `ID_progres`. |
| All points inside extent | Boolean | Default: `False` (center of gravity mode). Set to `True` to guarantee all points remain within the extent. |

<!-- Screenshot placeholder -->
<!-- ![RMPI dialog](images/rmpi_dialog.png) -->

---

## Roadmap

- [ ] Add remaining scripts (in progress)
- [ ] Add screenshot examples to this README
- [ ] Write unit tests
- [ ] Package as a QGIS plugin
- [ ] Submit to the QGIS **Research Tools** processing provider

---

## Contributing

Issues and pull requests are welcome.
If you use these tools in your research or work, feedback on edge cases and performance is especially appreciated.

---

## License

[GPL-2.0](https://www.gnu.org/licenses/old-licenses/gpl-2.0.html) — same as QGIS itself, consistent with the goal of contributing to the official codebase.
