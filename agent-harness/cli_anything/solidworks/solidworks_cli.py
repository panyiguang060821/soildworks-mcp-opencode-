"""SolidWorks CLI-Anything harness entry point."""

from __future__ import annotations

import json
import sys
from typing import Any

import click

from cli_anything.solidworks.utils.backend import SolidWorksBackend


pass_backend = click.make_pass_decorator(SolidWorksBackend)


def _output(data: Any, json_mode: bool):
    if json_mode:
        click.echo(json.dumps(data, ensure_ascii=False, indent=2))
    elif isinstance(data, dict):
        for key, value in data.items():
            click.echo(f"{key}: {value}")
    elif isinstance(data, list):
        for item in data:
            click.echo(item)
    else:
        click.echo(data)


@click.group(invoke_without_command=True)
@click.option("--json", "json_mode", is_flag=True, help="Output machine-readable JSON.")
@click.option(
    "--server-cmd",
    default=None,
    help="Command to launch the SolidWorks MCP server (default: python -m solidworks_mcp).",
)
@click.pass_context
def cli(ctx: click.Context, json_mode: bool, server_cmd: str | None):
    """CLI-Anything harness for SOLIDWORKS via its MCP server."""
    command = server_cmd.split() if server_cmd else None
    backend = SolidWorksBackend(command=command)
    ctx.obj = backend
    ctx.ensure_object(SolidWorksBackend)
    ctx.meta["json_mode"] = json_mode
    if ctx.invoked_subcommand is None:
        ctx.invoke(repl)


@cli.command()
@pass_backend
def status(backend: SolidWorksBackend):
    """Check SolidWorks and MCP server status."""
    result = backend.call("solidworks_status")
    _output(result, click.get_current_context().meta.get("json_mode", False))


@cli.command()
@click.option("--visible/--hidden", default=True, help="Show SolidWorks window.")
@pass_backend
def launch(backend: SolidWorksBackend, visible: bool):
    """Launch SolidWorks."""
    result = backend.call("launch_solidworks", {"visible": visible})
    _output(result, click.get_current_context().meta.get("json_mode", False))


@cli.command()
@click.option("--force/--no-force", default=False, help="Force close.")
@pass_backend
def close(backend: SolidWorksBackend, force: bool):
    """Close SolidWorks."""
    result = backend.call("close_solidworks", {"force": force})
    _output(result, click.get_current_context().meta.get("json_mode", False))


@cli.group()
def part():
    """Part/assembly document operations."""


@part.command(name="new")
@click.option("--template", default=None, help="Path to part template.")
@pass_backend
def part_new(backend: SolidWorksBackend, template: str | None):
    """Create a new part document."""
    args = {}
    if template:
        args["template_path"] = template
    result = backend.call("new_part", args)
    _output(result, click.get_current_context().meta.get("json_mode", False))


@part.command(name="open")
@click.argument("path")
@click.option("--visible/--hidden", default=True)
@pass_backend
def part_open(backend: SolidWorksBackend, path: str, visible: bool):
    """Open a SolidWorks document."""
    result = backend.call("open_document", {"path": path, "visible": visible})
    _output(result, click.get_current_context().meta.get("json_mode", False))


@part.command(name="save")
@click.option("--path", default=None, help="Target path.")
@click.option("--base-name", default=None, help="Base name to save as.")
@pass_backend
def part_save(backend: SolidWorksBackend, path: str | None, base_name: str | None):
    """Save the active document."""
    args = {}
    if path:
        args["path"] = path
    if base_name:
        args["base_name"] = base_name
    result = backend.call("save_active_document", args)
    _output(result, click.get_current_context().meta.get("json_mode", False))


@part.command(name="active")
@pass_backend
def part_active(backend: SolidWorksBackend):
    """Show the active document."""
    result = backend.call("active_document")
    _output(result, click.get_current_context().meta.get("json_mode", False))


@cli.group()
def sketch():
    """Sketch operations."""


@sketch.command(name="plane")
@click.argument("plane")
@pass_backend
def sketch_plane(backend: SolidWorksBackend, plane: str):
    """Create a sketch on a named plane."""
    result = backend.call("create_sketch_on_plane", {"plane": plane})
    _output(result, click.get_current_context().meta.get("json_mode", False))


@sketch.command(name="rectangle")
@click.argument("center_x", type=float)
@click.argument("center_y", type=float)
@click.argument("corner_x", type=float)
@click.argument("corner_y", type=float)
@click.option("--center-z", default=0.0, type=float)
@click.option("--corner-z", default=0.0, type=float)
@pass_backend
def sketch_rectangle(
    backend: SolidWorksBackend,
    center_x: float,
    center_y: float,
    corner_x: float,
    corner_y: float,
    center_z: float,
    corner_z: float,
):
    """Create a center rectangle in the active sketch."""
    result = backend.call(
        "create_center_rectangle",
        {
            "center_x": center_x,
            "center_y": center_y,
            "corner_x": corner_x,
            "corner_y": corner_y,
            "center_z": center_z,
            "corner_z": corner_z,
        },
    )
    _output(result, click.get_current_context().meta.get("json_mode", False))


@sketch.command(name="circle")
@click.argument("center_x", type=float)
@click.argument("center_y", type=float)
@click.argument("radius", type=float)
@click.option("--center-z", default=0.0, type=float)
@pass_backend
def sketch_circle(
    backend: SolidWorksBackend,
    center_x: float,
    center_y: float,
    radius: float,
    center_z: float,
):
    """Create a circle in the active sketch."""
    result = backend.call(
        "create_circle",
        {
            "center_x": center_x,
            "center_y": center_y,
            "radius": radius,
            "center_z": center_z,
        },
    )
    _output(result, click.get_current_context().meta.get("json_mode", False))


@cli.group()
def feature():
    """Feature operations."""


@feature.command(name="extrude")
@click.argument("depth", type=float)
@pass_backend
def feature_extrude(backend: SolidWorksBackend, depth: float):
    """Extrude boss from the active sketch."""
    result = backend.call("extrude_boss", {"depth": depth})
    _output(result, click.get_current_context().meta.get("json_mode", False))


@feature.command(name="export")
@click.argument("output_path")
@click.option("--version", default=None, help="Export version string.")
@click.option("--options", default=None, help="Export options string.")
@pass_backend
def feature_export(backend: SolidWorksBackend, output_path: str, version: str | None, options: str | None):
    """Export the active document."""
    args = {"output_path": output_path}
    if version:
        args["version"] = version
    if options:
        args["options"] = options
    result = backend.call("export_file", args)
    _output(result, click.get_current_context().meta.get("json_mode", False))


@cli.command()
@pass_backend
def tools(backend: SolidWorksBackend):
    """List available MCP tools."""
    result = backend.list_tools()
    _output(result, click.get_current_context().meta.get("json_mode", False))


@cli.command()
@pass_backend
def repl(backend: SolidWorksBackend):
    """Interactive REPL for SolidWorks commands."""
    click.echo("SolidWorks CLI REPL. Type 'help' for commands, 'exit' to quit.")
    commands = {
        "status": status,
        "launch": launch,
        "close": close,
        "part new": part_new,
        "part open": part_open,
        "part save": part_save,
        "part active": part_active,
        "sketch plane": sketch_plane,
        "sketch rectangle": sketch_rectangle,
        "sketch circle": sketch_circle,
        "feature extrude": feature_extrude,
        "feature export": feature_export,
        "tools": tools,
    }
    while True:
        try:
            line = click.prompt("solidworks", prompt_suffix="> ")
        except (EOFError, KeyboardInterrupt):
            click.echo("\nGoodbye.")
            break
        line = line.strip()
        if not line or line.lower() in ("exit", "quit"):
            click.echo("Goodbye.")
            break
        if line.lower() == "help":
            click.echo("Available commands:")
            for name in commands:
                click.echo(f"  {name}")
            continue
        if line.lower().startswith("help "):
            cmd_name = line[5:].strip()
            if cmd_name in commands:
                click.echo(commands[cmd_name].get_help(click.Context(commands[cmd_name])))
            else:
                click.echo(f"Unknown command: {cmd_name}")
            continue
        # Simple dispatch: split by spaces and try to match
        parts = line.split()
        matched = None
        for name in sorted(commands, key=len, reverse=True):
            name_parts = name.split()
            if parts[: len(name_parts)] == name_parts:
                matched = name
                break
        if matched is None:
            click.echo(f"Unknown command: {line}")
            continue
        cmd = commands[matched]
        args = parts[len(matched.split()):]
        # Run the Click command in isolation
        ctx = click.Context(cmd, parent=click.get_current_context())
        ctx.obj = backend
        ctx.meta["json_mode"] = False
        try:
            cmd.invoke(ctx)
        except click.ClickException as e:
            e.show()
        except Exception as e:
            click.echo(f"Error: {e}")


@cli.result_callback()
def cleanup(result, **kwargs):
    ctx = click.get_current_context()
    backend = ctx.obj
    if isinstance(backend, SolidWorksBackend):
        backend.close()


if __name__ == "__main__":
    cli()
