import json
import os
from typing import Any

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


class SolidWorksMCPClient:
    """Stdio MCP client for the SolidWorks MCP server."""

    def __init__(self, command: list[str] | None = None):
        if command is None:
            command = ["python", "-m", "solidworks_mcp"]
        self.command = command
        self.session: ClientSession | None = None
        self._stdio_ctx = None

    async def __aenter__(self) -> "SolidWorksMCPClient":
        params = StdioServerParameters(
            command=self.command[0],
            args=self.command[1:],
            cwd=os.getcwd(),
        )
        self._stdio_ctx = stdio_client(params)
        read, write = await self._stdio_ctx.__aenter__()
        self.session = ClientSession(read, write)
        await self.session.__aenter__()
        await self.session.initialize()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if self.session is not None:
            await self.session.__aexit__(exc_type, exc, tb)
        if self._stdio_ctx is not None:
            await self._stdio_ctx.__aexit__(exc_type, exc, tb)

    async def list_tools(self) -> list[dict[str, Any]]:
        tools = await self.session.list_tools()
        return [
            {"name": t.name, "description": t.description, "parameters": t.inputSchema}
            for t in tools.tools
        ]

    async def call(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        if self.session is None:
            raise RuntimeError("MCP client is not connected")
        result = await self.session.call_tool(name, arguments or {})
        parsed = []
        for content in result.content:
            text = getattr(content, "text", str(content))
            try:
                parsed.append(json.loads(text))
            except json.JSONDecodeError:
                parsed.append(text)
        return {"ok": not result.isError, "results": parsed}
