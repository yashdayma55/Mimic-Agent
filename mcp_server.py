"""
MimicAgent MCP server - exposes learned workflows as tools other agents can call.
Phase 6, step 1: the empty server that starts and responds.
"""

from mcp.server.fastmcp import FastMCP

# the server object - "mimicagent" is the name agents will see
server = FastMCP("mimicagent")


# a trivial tool just to prove the server works end to end
@server.tool()
def ping() -> str:
    """A health check. Returns 'pong' so a client can confirm MimicAgent is reachable."""
    return "pong from MimicAgent"


if __name__ == "__main__":
    server.run()