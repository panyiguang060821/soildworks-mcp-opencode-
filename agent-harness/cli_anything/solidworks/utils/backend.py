"""Backend bridge: directly invokes SolidWorks MCP tool functions.

In the future this can be replaced with a stdio MCP client to follow the
CLI-Anything MCP backend pattern exactly; for the proof-of-concept we call the
tool functions directly because it avoids Python 3.11 / anyio task-scope issues.
"""

from typing import Any

from solidworks_mcp.server import (
    active_document,
    close_solidworks,
    create_center_rectangle,
    create_circle,
    create_sketch_on_plane,
    export_file,
    extrude_boss,
    launch_solidworks,
    new_part,
    open_document,
    ping,
    save_active_document,
    solidworks_status,
)


class SolidWorksBackend:
    """Synchronous facade over the SolidWorks MCP server tool functions."""

    _TOOLS = {
        "ping": ping,
        "solidworks_status": solidworks_status,
        "launch_solidworks": launch_solidworks,
        "close_solidworks": close_solidworks,
        "active_document": active_document,
        "save_active_document": save_active_document,
        "open_document": open_document,
        "new_part": new_part,
        "create_sketch_on_plane": create_sketch_on_plane,
        "create_center_rectangle": create_center_rectangle,
        "create_circle": create_circle,
        "extrude_boss": extrude_boss,
        "export_file": export_file,
    }

    def __init__(self, command: list[str] | None = None):
        # command is accepted for CLI-Anything compatibility but ignored here
        self.command = command

    def call(self, tool_name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        func = self._TOOLS.get(tool_name)
        if func is None:
            return {"ok": False, "error": f"Unknown tool: {tool_name}"}
        try:
            result = func(**(arguments or {}))
            return {"ok": True, "results": [result]}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def list_tools(self) -> list[dict[str, Any]]:
        return [
            {"name": name, "description": func.__doc__ or ""}
            for name, func in self._TOOLS.items()
        ]

    def close(self):
        pass
