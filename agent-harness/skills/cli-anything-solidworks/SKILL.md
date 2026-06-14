---
name: "cli-anything-solidworks"
description: "CLI harness for SOLIDWORKS 2025 via the SolidWorks MCP server"
triggers: ["solidworks", "cad", "3d model", "part", "assembly"]
---

# cli-anything-solidworks

Control SOLIDWORKS 2025 from the command line through its MCP server.

## Prerequisites

- Windows with SOLIDWORKS 2025 installed
- `pip install -e agent-harness` from the repository root

## Installation

```bash
pip install -e agent-harness
```

## Command Summary

| Command | Purpose |
|---|---|
| `cli-anything-solidworks status` | Check SOLIDWORKS status |
| `cli-anything-solidworks launch` | Launch SOLIDWORKS |
| `cli-anything-solidworks close` | Close SOLIDWORKS |
| `cli-anything-solidworks part new --template PATH` | New part |
| `cli-anything-solidworks part open PATH` | Open document |
| `cli-anything-solidworks part save --path PATH` | Save active document |
| `cli-anything-solidworks part active` | Active document info |
| `cli-anything-solidworks sketch plane NAME` | Sketch on plane |
| `cli-anything-solidworks sketch rectangle CX CY CRX CRY` | Center rectangle |
| `cli-anything-solidworks sketch circle CX CY R` | Circle |
| `cli-anything-solidworks feature extrude DEPTH` | Extrude boss |
| `cli-anything-solidworks feature export PATH` | Export file |
| `cli-anything-solidworks tools` | List MCP tools |

## Agent Guidance

- Always prefer `--json` output for programmatic parsing.
- Pass `--template` explicitly to `part new`; the upstream default may target a
  different SOLIDWORKS version.
- Close SOLIDWORKS with `--force` if the normal `close` leaves child processes.

## Example Workflow

```bash
cli-anything-solidworks launch
cli-anything-solidworks part new --template "C:\ProgramData\SOLIDWORKS\SOLIDWORKS 2025\templates\gb_part.prtdot"
cli-anything-solidworks sketch plane "Front Plane"
cli-anything-solidworks sketch rectangle 0 0 0.05 0.05
cli-anything-solidworks feature extrude 0.01
cli-anything-solidworks part save --path "C:\temp\block.sldprt"
cli-anything-solidworks feature export "C:\temp\block.step"
cli-anything-solidworks close
```
