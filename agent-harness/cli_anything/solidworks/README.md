# CLI-Anything SolidWorks Harness

A command-line harness for SOLIDWORKS 2025 that wraps the SolidWorks MCP server.

## Prerequisites

- Windows 10/11
- SOLIDWORKS 2025 installed
- Python 3.10+
- The SolidWorks MCP server package installed (`solidworks_mcp`)

## Installation

From this directory:

```bash
pip install -e .
```

Verify:

```bash
cli-anything-solidworks --help
```

## Usage

### One-shot commands

```bash
# Check status
cli-anything-solidworks status

# Launch SOLIDWORKS
cli-anything-solidworks launch

# Create a new part (use the 2025 template path on your machine)
cli-anything-solidworks part new --template "C:\ProgramData\SOLIDWORKS\SOLIDWORKS 2025\templates\gb_part.prtdot"

# Create a sketch and extrude a block
cli-anything-solidworks sketch plane "Front Plane"
cli-anything-solidworks sketch rectangle 0 0 0.05 0.05
cli-anything-solidworks feature extrude 0.01

# Save and export
cli-anything-solidworks part save --path "C:\temp\block.sldprt"
cli-anything-solidworks feature export "C:\temp\block.step"
```

### JSON output for agents

Every command supports `--json`:

```bash
cli-anything-solidworks --json status
```

### REPL mode

Run without arguments to enter interactive mode:

```bash
cli-anything-solidworks
solidworks> status
solidworks> launch
solidworks> part new --template "..."
```

## Command Reference

| Command | Description |
|---|---|
| `status` | SOLIDWORKS and MCP server status |
| `launch` | Launch SOLIDWORKS |
| `close` | Close SOLIDWORKS |
| `part new` | Create a new part document |
| `part open PATH` | Open a document |
| `part save` | Save the active document |
| `part active` | Show the active document |
| `sketch plane NAME` | Create a sketch on a plane |
| `sketch rectangle CX CY CRX CRY` | Center rectangle |
| `sketch circle CX CY R` | Circle |
| `feature extrude DEPTH` | Extrude boss |
| `feature export PATH` | Export active document |
| `tools` | List wrapped MCP tools |
| `repl` | Interactive REPL |

## Architecture

This harness follows the CLI-Anything directory layout:

- `core/` — client/project abstractions
- `utils/backend.py` — bridge to the SolidWorks MCP server tool functions
- `solidworks_cli.py` — Click CLI entry point with REPL support
- `tests/TEST.md` — test plan and results

## Limitations

- This proof-of-concept wraps MCP tool functions directly rather than using a
  separate stdio MCP client process. A future version can migrate to the stdio
  MCP backend pattern for stricter process isolation.
- The default part template path in `solidworks_mcp.server` still points to
  SOLIDWORKS 2023 templates; pass `--template` explicitly until that default is
  updated.
- `close` may require `--force` to terminate all SOLIDWORKS child processes.

## License

Same as the parent repository: MIT.
