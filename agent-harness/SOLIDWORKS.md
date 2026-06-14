# SOLIDWORKS CLI-Anything Harness SOP

## Software

- **Name**: SOLIDWORKS 2025
- **Vendor**: Dassault Systèmes
- **Type**: Commercial Windows CAD application
- **Automation interface**: COM API (`SldWorks.Application`)

## Why CLI-Anything Fits

SOLIDWORKS is closed-source and Windows-only, so the standard CLI-Anything
source-code-analysis pipeline cannot be applied directly. However, SOLIDWORKS
exposes a rich COM automation API, and the community already built an
MCP server (`solidworks_mcp`) that wraps this API. This harness follows the
**MCP backend pattern** from CLI-Anything:

- The CLI is a lightweight command-line wrapper.
- The real work is delegated to the SolidWorks MCP server, which talks to
  SOLIDWORKS via COM.
- Agents can discover commands via `--help` and `--json` output.

## Backend Pattern

```
cli-anything-solidworks
    |
    v
utils/backend.py
    |
    v
solidworks_mcp.server tool functions
    |
    v
SldWorks.Application COM object
    |
    v
SOLIDWORKS 2025
```

For stricter isolation the backend could be migrated to a stdio MCP client that
spawns `python -m solidworks_mcp` as a subprocess.

## Command Groups

| Group | Purpose |
|---|---|
| `part` | Document creation, open, save, active-document introspection |
| `sketch` | Sketch creation and 2D geometry (rectangle, circle) |
| `feature` | 3D features (extrude) and export |
| (top-level) | Lifecycle commands: `launch`, `close`, `status`, `tools`, `repl` |

## Output Format

All commands support `--json` for agent consumption. Without the flag, output is
human-readable key-value text.

## Testing

E2E tests require a real SOLIDWORKS 2025 installation. Unit tests use mocked
tool functions. See `cli_anything/solidworks/tests/TEST.md`.

## References

- Parent repository: https://github.com/panyiguang060821/soildworks-mcp-opencode-
- SolidWorks MCP server: `src/solidworks_mcp/server.py`
- CLI-Anything: https://github.com/HKUDS/CLI-Anything
