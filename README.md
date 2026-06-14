<div align="right">

[English](README.md) | [中文](README.zh-CN.md)

</div>

# soildworks-mcp

`soildworks-mcp` is a Windows-first MCP server for SolidWorks. It exposes a local SolidWorks COM automation workflow to Codex, Claude Code, opencode, or any other MCP client through a Python stdio server plus a C# bridge.

This repository is structured for direct local build, editable install, MCP registration, and real host-side verification.

## Acknowledgments

This project is a derivative work building on multiple open-source projects.
See [NOTICE.md](NOTICE.md) for the full attribution chain and
[LICENSE](LICENSE) for license terms. Key upstream projects:

- **[eyfel/mcp-server-solidworks](https://github.com/eyfel/mcp-server-solidworks)** - Original architecture (MIT, 2025)
- **[Xuan-BOMS/soildworks-mcp](https://github.com/Xuan-BOMS/soildworks-mcp)** - Windows packaging (MIT, 2026)
- **[xarial/codestack](https://github.com/xarial/codestack)** - SW API reference (MIT)
- **[alisamsam/Solidworks-MCP](https://github.com/alisamsam/Solidworks-MCP)** - Design patterns (MIT)

## What Works

The following capabilities are implemented and verified in the current codebase (47 tools total):

**Document & Session (8)**
- launch or attach to SolidWorks
- close the running SolidWorks instance
- inspect SolidWorks process and active document state
- create a new part
- open and save `.SLDPRT` files
- create a new assembly document
- get/set the current document

**Sketch Entities (8)**
- start a sketch on a base plane
- start a sketch on a selected model face
- draw line segments, arcs, regular polygons, and centerlines
- create center rectangles and circles

**Features (8)**
- create boss extrusions and cut extrusions
- create reference planes (distance/angle/parallel)
- create lofted and swept bosses
- create rib features
- apply fillets and chamfers to feature edges
- inspect bodies and feature history

**Patterns & Mirrors (3)**
- mirror features across a plane
- circular pattern of features around an axis
- linear pattern of features along an edge/axis

**Assembly (6)**
- insert components into an active assembly
- add mates between entities (coincident/concentric/parallel/etc.)
- create auto-explode views
- add smart dimensions (VSTA-bypassing implementation)
- get mass, volume, surface area, and center of mass

**Export & Analysis (5)**
- export to STEP, IGES, STL, Parasolid, etc.
- check for interferences in assemblies
- measure distance/angle between entities
- set part material from database

**Composite / High-level (4)**
- build a rectangular block from dimensions
- build a drilled plate from dimensions
- build a feature-showcase part (boss + cut + fillet + chamfer + combine)
- run a one-sentence natural-language showcase workflow through `design_from_prompt`

**Guarded / Limited (3)**
- `combine_all_bodies` - Exposed but reports unsupported state on some hosts
- `run_macro` - Intentionally disabled (VSTA macro loader can crash SW)
- `add_dimension` (original) - Replaced by `add_dimension_v2`

## Current Limitation

`combine_all_bodies` is exposed and diagnosed, but it is not currently stable on this host. The server reports that state explicitly instead of pretending the operation succeeded.

Current combine result on the verified machine:

- `combineSupported: false`
- `combineStatus.reason: combine_insert_failed`

The rest of the showcase workflow remains usable and returns structured validation for boss, cut, fillet, and chamfer.

## Repository Layout

```text
soildworks-mcp/
|- agent-harness/             # CLI-Anything harness wrapping the MCP server
|  |- SOLIDWORKS.md           # Harness SOP
|  |- setup.py
|  |- cli_anything/solidworks/ # Click CLI + REPL + backend bridge
|  |- skills/cli-anything-solidworks/SKILL.md
|- bridge/
|  |- Program.cs              # C# Bridge with 36+ commands
|  |- SolidWorksBridge.csproj
|- examples/
|  |- codex-config.toml
|  |- install-deploy-prompt.md
|  |- install-deploy-prompt.zh-CN.md
|- scripts/
|  |- bootstrap.ps1
|  |- build_bridge.ps1
|  |- smoke_test.py
|- src/
|  |- solidworks_mcp/
      |- __init__.py
      |- __main__.py
      |- server.py              # Python MCP server with 47 @mcp.tool() registrations
|- tests/
|- server.py
|- pyproject.toml
|- requirements.txt
|- README.md
|- README.zh-CN.md
|- LICENSE                   # MIT with multi-copyright
|- NOTICE.md                  # Full attribution chain
|- CHANGELOG.md               # Version history
```

## CLI-Anything Harness (Experimental)

This repository also ships a [CLI-Anything](https://github.com/HKUDS/CLI-Anything) harness
that exposes the SolidWorks MCP server as a standalone CLI with JSON output and REPL:

```powershell
cd agent-harness
pip install -e .

cli-anything-solidworks --help
cli-anything-solidworks --json status
cli-anything-solidworks launch
cli-anything-solidworks part new --template "C:\ProgramData\SOLIDWORKS\SOLIDWORKS 2025\templates\gb_part.prtdot"
cli-anything-solidworks sketch plane "Front Plane"
cli-anything-solidworks sketch rectangle 0 0 0.05 0.05
cli-anything-solidworks feature extrude 0.01
cli-anything-solidworks part save --path "C:\temp\block.sldprt"
cli-anything-solidworks feature export "C:\temp\block.step"
```

See `agent-harness/cli_anything/solidworks/README.md` for the full command reference.

## Requirements

- Windows
- Python 3.11 or newer
- .NET SDK 8 or newer
- SolidWorks installed locally
- access to `SolidWorks.Interop.sldworks.dll`
- access to `SolidWorks.Interop.swconst.dll`

## Quick Deploy

If you want Codex CLI, Claude Code CLI, or another MCP-capable coding CLI to deploy this repository for you, use this single prompt:

- [examples/install-deploy-prompt.md](examples/install-deploy-prompt.md)
- [中文版本 / Chinese version](examples/install-deploy-prompt.zh-CN.md)

That prompt is written to:

- work across MCP-capable coding CLIs instead of one specific client
- ask the user where the deployment and MCP registration should go before making changes
- perform a real install, real bridge build, real MCP registration, and real SolidWorks verification
- report unsupported host behavior honestly instead of pretending deployment is complete

## Installation

### 1. Clone the repository

```powershell
git clone https://github.com/Xuan-BOMS/soildworks-mcp.git
cd soildworks-mcp
```

### 2. Install Python dependencies

Recommended:

```powershell
python -m pip install -e .
```

Fallback:

```powershell
python -m pip install -r requirements.txt
```

### 3. Build the SolidWorks bridge

If SolidWorks is installed in a default location:

```powershell
.\scripts\build_bridge.ps1
```

If SolidWorks is installed elsewhere:

```powershell
.\scripts\build_bridge.ps1 -SolidWorksInstallDir "<path-to-solidworks-install-dir>"
```

### 4. One-step bootstrap

If you want editable install plus bridge build in one step:

```powershell
.\scripts\bootstrap.ps1 -Python "<path-to-python>" -SolidWorksInstallDir "<path-to-solidworks-install-dir>"
```

### 5. Start the MCP server

Either of these is valid:

```powershell
python .\server.py
```

```powershell
python -m solidworks_mcp
```

## MCP Client Registration

After installation, register the server as a stdio MCP server in your coding client.

Example:

```toml
[mcp_servers.solidworks]
type = "stdio"
command = "python"
args = ["-m", "solidworks_mcp"]
```

If you prefer a direct repository path instead of module execution:

```toml
[mcp_servers.solidworks]
type = "stdio"
command = "<path-to-python>"
args = ["<path-to-repo>/server.py"]
```

The exact config file location depends on the client. The same stdio command and args can be adapted for Codex CLI, Claude Code CLI, and other MCP-capable tools.

## Optional Environment Variables

- `SOLIDWORKS_MCP_BRIDGE_DLL`
  Override the default bridge DLL path.
- `SOLIDWORKS_MCP_TEMPLATE`
  Override the default SolidWorks part template.

Example:

```powershell
$env:SOLIDWORKS_MCP_TEMPLATE = "C:\ProgramData\SOLIDWORKS\SOLIDWORKS 2023\templates\gb_part.prtdot"
python -m solidworks_mcp
```

## Smoke Test

Start with:

```powershell
python .\scripts\smoke_test.py
```

Then run real workflow tests as needed:

```powershell
python .\tests\tests_required_tools.py
python .\tests\tests_rectangular_block_workflow.py
python .\tests\tests_plate_with_holes_workflow.py
python .\tests\tests_design_from_prompt_workflow.py
python .\tests\tests_cut_and_inspect_workflow.py
python .\tests\tests_feature_showcase_workflow.py
python .\tests\tests_showcase_prompt_workflow.py
```

## High-Level Tool Summary

Stable tools:

- `launch_solidworks`
- `close_solidworks`
- `solidworks_status`
- `active_document`
- `open_document`
- `save_active_document`
- `new_part`
- `create_sketch_on_plane`
- `create_center_rectangle`
- `create_circle`
- `extrude_boss`
- `extrude_cut`
- `inspect_active_part`
- `apply_fillet_to_feature_edges`
- `apply_chamfer_to_feature_edges`
- `create_rectangular_block`
- `create_plate_with_holes`
- `create_feature_showcase_part`
- `design_from_prompt`

Guarded or limited tools:

- `combine_all_bodies`
  Exposed, but currently reports unsupported/failed state on the verified machine.
- `run_macro`
  Intentionally disabled because VSTA macro loading can crash SolidWorks on this host.
- `add_dimension`
  Not enabled as a stable production path.

## Verified Behavior In This Codebase

The synchronized code in this repository has already been updated to include:

- cut extrusion support in the bridge
- active part inspection with feature and body summaries
- fillet and chamfer feature-edge workflows
- showcase validation workflow
- explicit reporting for unsupported combine behavior
- prompt-based one-sentence showcase generation

## Upstream Reference

- Direct parent: [Xuan-BOMS/soildworks-mcp](https://github.com/Xuan-BOMS/soildworks-mcp) (MIT, 2026)
- Original: [eyfel/mcp-server-solidworks](https://github.com/eyfel/mcp-server-solidworks) (MIT, 2025)

This repository adds 22 new MCP tools (47 total) verified against SOLIDWORKS 2025
SP3 (revision 33.3.0). See [CHANGELOG.md](CHANGELOG.md) for the full history and
[NOTICE.md](NOTICE.md) for the complete attribution chain including all
referenced upstream projects and API resources.

## Provenance and Contributing

This project accepts contributions that respect the MIT license terms of all
upstream works. If you submit a pull request, please ensure:

1. Your code is MIT-compatible (or you have explicit permission for other licenses)
2. New dependencies are documented in `requirements.txt` and `pyproject.toml`
3. New tools follow the existing naming conventions and return-format patterns
4. The `NOTICE.md` attribution chain is updated if you reference new upstream work
