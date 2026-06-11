# NOTICE: Attributions and Acknowledgments

This repository is a derivative work that builds upon multiple open-source
projects. All code is MIT-licensed unless otherwise noted. We are grateful
to the original authors and contributors.

---

## Direct Codebase Lineage

This fork integrates and extends code from the following repositories, in
order of dependency:

### 1. eyfel / mcp-server-solidworks (Upstream)
- **URL**: https://github.com/eyfel/mcp-server-solidworks
- **License**: MIT
- **Copyright**: (c) 2025 eyfel
- **Role**: Original SolidWorks MCP server concept. Provided the foundational
  architecture: Python MCP stdio server + C# adapter layer + COM bridge
  via PythonNET. Core design patterns (STA thread model, popup guard,
  feature selection marks) originated here.
- **Used in**: Server architecture and bridge subprocess pattern.

### 2. Xuan-BOMS / soildworks-mcp (Direct parent fork)
- **URL**: https://github.com/Xuan-BOMS/soildworks-mcp
- **License**: MIT
- **Copyright**: (c) 2026 Xuan_Boms
- **Role**: Windows-first packaging of the eyfel upstream. Added the
  .csproj configuration, build scripts (`build_bridge.ps1`,
  `bootstrap.ps1`), host-stability guards, and the editable-install
  Python packaging via `pyproject.toml`. This is the direct base we forked.
- **Used in**: Entire repository structure and build pipeline.

### 3. This Repository (Custom additions)
- **URL**: https://github.com/panyiguang060821/soildworks-mcp-opencode-
- **Copyright**: (c) 2026 panyiguang060821
- **Role**: Added 22 new MCP tools (sketch entities, reference planes,
  patterns, assembly operations, export, analysis). Verified end-to-end
  against SOLIDWORKS 2025 SP3 (revision 33.3.0) on a Windows 11 host.
- **Added tools** (47 total, 22 new):
  - `draw_line`, `draw_arc`, `draw_polygon`, `draw_centerline`,
    `create_sketch_on_face`
  - `create_ref_plane`, `mirror_feature`, `circular_pattern`,
    `linear_pattern`, `loft_boss`, `sweep_boss`, `rib`
  - `new_assembly`, `add_component`, `add_mate`, `add_explode_step`,
    `add_dimension_v2`, `get_mass_properties`
  - `export_file`, `check_interference`, `measure_distance`,
    `set_material`

---

## Researched References (Not Directly Forked)

We studied the following repositories and resources during development. While
we did **not** copy their code, their design patterns and API discovery
informed our implementations:

### alisamsam / Solidworks-MCP
- **URL**: https://github.com/alisamsam/Solidworks-MCP
- **License**: MIT
- **Role**: Referenced for the `@mcp.tool()` registration pattern and the
  `create_center_rectangle` / `draw_polygon` API signatures. Their README
  and tool list informed our Phase 1 sketch entity additions.

### vespo92 / SolidworksMCP-TS
- **URL**: https://github.com/vespo92/SolidworksMCP-TS
- **License**: MIT
- **Role**: TypeScript implementation that we **did not** port directly, but
  their tool list (loft, sweep, sheet metal, mass properties) served as a
  roadmap for which COM API methods to integrate. The largest SW MCP server
  on GitHub by stars (167) at the time of research.

### xarial / codestack
- **URL**: https://github.com/xarial/codestack
- **License**: MIT
- **Role**: The most comprehensive SOLIDWORKS API reference site on the
  web (codestack.net). We referenced their VBA/C# code examples to
  determine correct parameter signatures and enum values for SW 2025
  Interop. This was the single most useful resource for writing
  C# Bridge code for advanced features (loft, sweep, mirror, pattern,
  reference planes).

### Glutenberg / swtoolkit & deloarts / pyswx
- **URLs**:
  - https://github.com/Glutenberg/swtoolkit
  - https://github.com/deloarts/pyswx
- **License**: MIT
- **Role**: Python libraries for SOLIDWORKS automation. Surveyed for
  architecture inspiration. Not integrated.

### SOLIDWORKS API Help (Official)
- **URL**: https://help.solidworks.com/2025/english/api/
- **Role**: The authoritative reference for COM API method signatures,
  enum values, and return types. All code in this project targets
  the SOLIDWORKS 2025 API surface.

---

## License Compliance

All upstream projects are MIT-licensed. Per the MIT license terms:

> Permission is hereby granted, free of charge, to any person obtaining a
> copy of this software and associated documentation files (the "Software"),
> to deal in the Software without restriction, including without limitation
> the rights to use, copy, modify, merge, publish, distribute, sublicense,
> and/or sell copies of the Software, and to permit persons to whom the
> Software is furnished to do so, subject to the following conditions:
>
> The above copyright notice and this permission notice shall be included
> in all copies or substantial portions of the Software.

The MIT license and copyright notices from all upstream contributors are
preserved in the [LICENSE](LICENSE) file at the root of this repository.

---

## Build Environment

This project was developed and verified on:

- **OS**: Windows 11
- **SOLIDWORKS**: 2025 SP3 (revision 33.3.0)
- **.NET SDK**: 8.0.422
- **Python**: 3.11.9
- **pywin32**: 312

---

## Contact

For questions about the integrations, please open an issue in this
repository: https://github.com/panyiguang060821/soildworks-mcp-opencode-/issues

For questions about upstream projects, please contact their respective
maintainers via their GitHub repositories.
