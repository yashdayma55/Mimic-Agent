"""
Test client for the MimicAgent MCP server.
Launches the server as a subprocess, connects over stdio, lists its tools,
and calls ping - proving the full server<->client loop works.
"""

import asyncio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    # tell the client how to start our server
    server_params = StdioServerParameters(
        command="python",
        args=["mcp_server.py"],
    )

    print("connecting to the MimicAgent MCP server...")
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            print("connected!\n")

            # 1. discover what tools the server offers
            tools = await session.list_tools()
            print("tools the server offers:")
            for t in tools.tools:
                print(f"   - {t.name}: {t.description}")

            # 2. actually call the ping tool
            print("\ncalling ping()...")
            result = await session.call_tool("ping", {})
            # the result content is a list of content blocks
            for block in result.content:
                if hasattr(block, "text"):
                    print(f"   server replied: {block.text}")

    print("\n=== client test complete ===")


if __name__ == "__main__":
    asyncio.run(main())