# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added - 2026-06-29 by panyiguang060821

**Merge solidworks-automation-skill Python COM tools** (60 tools total, up from 47):

#### New Skill-Integrated Tools (13 tools via Python COM Direct)

**Assembly & Mates (5)**
- `solidworks_add_component_v2` - Enhanced AddComponent with AddComponent5/4 fallback
- `solidworks_set_component_fixed` - Fix/float component by keyword
- `solidworks_add_coincident_mate` - Coincident mate with EN/CN plane name support
- `solidworks_add_distance_mate` - Distance mate with configurable distance
- `solidworks_add_concentric_mate` - Concentric mate by cylinder face radius matching

**Appearance & Export (3)**
- `solidworks_set_appearance` - Set document/component color (#RRGGBB or preset)
- `solidworks_export_active` - Multi-format export (STEP/STL/IGES/Parasolid/PDF/DXF)
- `solidworks_review_active` - Multi-view BMP preview + JSON review report

**Motion Study (1)**
- `solidworks_add_rotary_motor` - Motion Study with constant-speed rotary motor

**Document & Health (4)**
- `solidworks_new_document` - New document with auto template detection
- `solidworks_save_document_v2` - Save document with skill's save logic
- `solidworks_create_basic_part` - One-shot cylinder/box part with color
- `solidworks_health_check` - Environment check (COM deps, SW detection, Motion TLB)

#### Architectural Changes
- Unified global lock `_sw_global_lock` serializes Bridge (.NET) and Direct COM operations
- `bridge/Program.cs` - Enhanced gear commands, template path 2023→2025, zMin/zMax fillet params
- `pyproject.toml` - Added `comtypes>=1.2.0`, `pydantic>=2.0` dependencies
- Path for skill scripts (`solidworks-automation-skill/`) is resolved relative to repo root
- COM attribute compatibility helper `_sw_get(obj, name)` handles pywin32 property/method variants
- Plane name alias `_plane_aliases()` supports both English and Chinese reference plane names

### Added - 2026-06-15 by panyiguang060821

**CLI-Anything harness (experimental)** in `agent-harness/`:
- Installs as `cli-anything-solidworks` via `pip install -e agent-harness`
- Click-based CLI with `--json` output for agent consumption
- REPL mode (`cli-anything-solidworks` with no arguments)
- Command groups: `part`, `sketch`, `feature`, plus lifecycle commands
- Wraps the SolidWorks MCP server tool functions through `utils/backend.py`
- SKILL.md at `skills/cli-anything-solidworks/SKILL.md` for agent discovery
- Verified end-to-end: launch → new part → sketch → extrude → save → export

### Added - 2026-06-11 by panyiguang060821

**22 new MCP tools** (47 tools total, up from 25):

#### Phase 1: Sketch Entities (5 tools)
- `draw_line` - Draw line segments in active sketch (meters)
- `draw_arc` - Draw arcs with start/end points and direction
- `draw_polygon` - Draw regular polygons (3-100 sides, inscribed/circumscribed)
- `draw_centerline` - Draw centerlines (construction lines)
- `create_sketch_on_face` - Start sketch on a selected model face by name

#### Phase 2: Advanced Features (7 tools)
- `create_ref_plane` - Create reference planes with distance/angle/parallel constraints
- `mirror_feature` - Mirror features across a plane
- `circular_pattern` - Circular pattern of features around an axis
- `linear_pattern` - Linear pattern of features along an edge/axis
- `loft_boss` - Lofted boss between two profile sketches
- `sweep_boss` - Swept boss along a path sketch
- `rib` - Rib feature from a sketch

#### Phase 3: Assembly (6 tools)
- `new_assembly` - Create new assembly document
- `add_component` - Insert component into assembly (3 API fallbacks)
- `add_mate` - Add mate between entities (coincident/concentric/parallel/etc.)
- `add_explode_step` - Auto-explode view for components
- `add_dimension_v2` - Smart dimension using `IModelDoc2.AddDimension2` (bypasses VSTA)
- `get_mass_properties` - Get mass, volume, surface area, center of mass

#### Phase 4: Export & Analysis (4 tools)
- `export_file` - Export to STEP, IGES, STL, Parasolid, etc.
- `check_interference` - Interference detection in assemblies
- `measure_distance` - Distance/angle measurement between entities
- `set_material` - Set part material from database

### Changed
- `bridge/Program.cs` - Added UTF-8 console encoding for Chinese-locale plane
  and sketch names. Added plane alias lookup (`Front Plane` / `前视基准面`)
  to `TrySelectPlane`. Selection mark changed from 1 to 0 for first reference
  to fix `InsertRefPlane` failures. Added `GetLastFeature` fallback for
  `void`-returning API methods (`InsertRib`).
- `bridge/SolidWorksBridge.csproj` - Fixed Interop DLL hint paths to point
  to `api\redist\` subdirectory (default SW install does not copy DLLs to root).
- `src/solidworks_mcp/server.py` - Added `DEFAULT_ASSEMBLY_TEMPLATE` env var.
  Added 22 new `@mcp.tool()` registrations.

### Fixed
- `create_ref_plane` now successfully creates reference planes in
  Chinese-locale SolidWorks installations.
- `add_component` now handles AddComponent5/4/single method variations
  with proper error logging.
- `set_material` now works with empty database name for built-in materials.

### Verified
- All 12 representative tools tested end-to-end against SOLIDWORKS 2025 SP3
  (revision 33.3.0) with 100% pass rate on the focused regression suite.
- Memory-efficient test script that closes documents after each test
  to prevent memory exhaustion during long test runs.

## [0.1.0] - 2026-04-24 (Xuan-BOMS base fork)

### Added (from Xuan-BOMS/soildworks-mcp)
- Windows-first packaging of eyfel/mcp-server-solidworks
- `build_bridge.ps1` and `bootstrap.ps1` for one-step setup
- `.csproj` configuration with .NET 8
- Popup guard thread for auto-dismissing SolidWorks dialogs
- `combine_all_bodies`, `run_macro`, `add_dimension` guarded implementations
- `design_from_prompt` natural-language workflow
- 25 MCP tools: ping, solidworks_status, launch_solidworks,
  close_solidworks, active_document, open_document, save_active_document,
  new_part, create_sketch_on_plane, create_center_rectangle, create_circle,
  extrude_boss, extrude_cut, inspect_active_part,
  apply_fillet_to_feature_edges, apply_chamfer_to_feature_edges,
  combine_all_bodies, run_macro, add_dimension, create_rectangular_block,
  create_plate_with_holes, create_feature_showcase_part, design_from_prompt

## [0.0.0] - 2025-04-11 (eyfel original)

### Added (from eyfel/mcp-server-solidworks)
- Original MCP server concept: Python stdio + C# adapter + PythonNET COM bridge
- Core architecture: STA thread model, feature selection marks, popup guard
- 8 initial tools: ping, status, launch, close, new_part, open, save, sketch
