"""
Base types for the Scheduler.ai tool provider system.

Each provider implements list_tools() + call_tool() + handles().
ToolRegistry aggregates providers and routes dispatch.
"""


class ToolNotFoundError(ValueError):
    """Raised when no registered provider handles the requested tool name."""

    def __init__(self, name: str):
        super().__init__(f"No provider found for tool: {name!r}")
        self.tool_name = name
