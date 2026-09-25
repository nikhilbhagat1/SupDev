"""Tiny real MCP server (stdio) used to prove the bridge end to end."""
try:  # mcp >= 2
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server

mcp = _Server("echo")


@mcp.tool()
def echo(text: str) -> str:
    """Echo text back."""
    return f"echo: {text}"


@mcp.tool()
def dangerous(text: str) -> str:
    """A tool the admin will NOT tag (must never be exposed)."""
    return "boom"


if __name__ == "__main__":
    mcp.run()
