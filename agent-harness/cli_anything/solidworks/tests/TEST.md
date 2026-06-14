# SolidWorks CLI Harness Tests

## Test Plan

### Unit Tests

- `test_backend.py`: test backend call/list_tools with synthetic/mock functions
- `test_cli.py`: test Click command parsing and JSON flag propagation

### E2E Tests

- `test_full_e2e.py`: run the full CLI against a real SOLIDWORKS 2025 instance:
  1. `status` returns JSON
  2. `launch` starts SOLIDWORKS
  3. `part new` creates a part
  4. `sketch plane` + `sketch rectangle` + `feature extrude` creates a block
  5. `part save` writes a `.sldprt` file
  6. `feature export` writes a `.step` file
  7. `close` terminates SOLIDWORKS

## Manual Test Results

Performed on Windows 11 + SOLIDWORKS 2025 SP3 (revision 33.3.0).

```bash
# Help
cli-anything-solidworks --help          # OK

# Status when SOLIDWORKS is closed
cli-anything-solidworks --json status
# {"ok": true, "results": [{"running": false, ...}]}

# Launch
cli-anything-solidworks --json launch
# {"ok": true, "results": [{"running": true, "revision": "33.3.0", ...}]}

# Part creation workflow
cli-anything-solidworks --json part new --template "C:\ProgramData\SOLIDWORKS\SOLIDWORKS 2025\templates\gb_part.prtdot"
cli-anything-solidworks --json sketch plane "Front Plane"
cli-anything-solidworks --json sketch rectangle 0 0 0.05 0.05
cli-anything-solidworks --json feature extrude 0.01

# Save and export
cli-anything-solidworks --json part save --path C:\temp\block.sldprt
cli-anything-solidworks --json feature export C:\temp\block.step
```

All commands returned `ok: true` and produced real files.

## Known Issues

- `close` without `--force` may leave child processes running.
- Default template path in upstream `solidworks_mcp.server` targets 2023 templates.
