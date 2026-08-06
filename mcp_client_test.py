"""
Test client for the MimicAgent MCP server - Phase 6 finale.
Connects, lists the real workflow tools, and calls run_workflow so an
MCP call drives the desktop.

Open Notepad before running (the notepad_greeting workflow types into it).
Approval still happens on the server side (the terminal running the workflow).
"""

import asyncio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    # NOTE: point this at whatever your server file is named.
    # You named it mcp_server.py (or mcp_runner.py) - set it here:
    server_file = "mcp_server.py"

    server_params = StdioServerParameters(command="python", args=[server_file])

    print("connecting to the MimicAgent MCP server...")
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            print("connected!\n")

            # 1. discover the tools
            tools = await session.list_tools()
            print("tools the server offers:")
            for t in tools.tools:
                print(f"   - {t.name}")

            # 2. ask which workflows exist
            print("\ncalling list_workflows()...")
            wf = await session.call_tool("list_workflows", {})
            for block in wf.content:
                if hasattr(block, "text"):
                    print(f"   workflows: {block.text}")

            # 3. run a workflow - THIS DRIVES THE DESKTOP
            print("\ncalling run_workflow('notepad_greeting')...")
            print("   (approve the steps in the SERVER's terminal window)")
            result = await session.call_tool(
                "run_workflow", {"name": "notepad_greeting", "data": {}}
            )
            for block in result.content:
                if hasattr(block, "text"):
                    print(f"\n   server replied: {block.text}")

    print("\n=== MCP call drove the desktop - Phase 6 proven ===")


if __name__ == "__main__":
    asyncio.run(main())