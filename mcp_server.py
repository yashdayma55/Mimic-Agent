"""
MimicAgent MCP server - exposes learned workflows as tools other agents can call.

Tools:
  ping()                    - health check
  list_workflows()          - names of workflows MimicAgent can run
  run_workflow(name, data)  - run a saved workflow on this desktop

Principle: MimicAgent is a capability, not a decision maker. It exposes only
whole named workflows a human already taught, and the human still approves each
step (unless a workflow is explicitly trusted for unattended use).
"""

from fastmcp import FastMCP
from workflow_runner import list_workflow_names, run_workflow_by_name

server = FastMCP("mimicagent")

# workflows the user has explicitly blessed to run without per-step approval.
# empty by default: everything requires human approval.
TRUSTED_UNATTENDED = {"notepad_greeting"}   # safe test workflow, runs without per-step approval


@server.tool()
def ping() -> str:
    """A health check. Returns 'pong' so a client can confirm MimicAgent is reachable."""
    return "pong from MimicAgent"


@server.tool()
def list_workflows() -> list:
    """List the workflows this MimicAgent has learned and can run.
    Returns a list of workflow names that can be passed to run_workflow."""
    return list_workflow_names()


@server.tool()
def run_workflow(name: str, data: dict = None) -> str:
    """Run a saved MimicAgent workflow on this computer.

    name: the workflow to run, e.g. 'notepad_greeting' (see list_workflows)
    data: optional values to fill into the workflow, e.g. {'email': 'me@x.com'}

    By default a human approves each step on the machine. Returns a short
    summary of what happened.
    """
    unattended = name in TRUSTED_UNATTENDED
    return run_workflow_by_name(name, data or {}, require_approval=not unattended)


if __name__ == "__main__":
    server.run()