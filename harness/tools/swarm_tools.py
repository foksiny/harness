"""
Agent Swarm tools for Harness.
Enables the model to delegate parallel work to concurrently running specialized
subagents that coordinate through the shared "main thread" message bus.
"""
from typing import Dict, Any, List, Optional
from harness.tools.base import Tool
from harness.core.subagents import SubagentOrchestrator

class SpawnSwarmTool(Tool):
    name = "spawn_swarm"
    description = (
        "Dispatch a swarm of specialized subagents that run CONCURRENTLY and coordinate "
        "through a shared message bus. Each agent receives its own isolated context and a "
        "unique agent_id. Use this when a goal decomposes into independent, parallelizable "
        "subtasks (e.g. research + implementation + testing in parallel). Subagents exchange "
        "messages via swarm_send_message / swarm_read_messages. Returns a combined report "
        "including every agent's output and the full mailbox transcript."
    )
    action_type = "subagent"
    is_read_only = False
    parameters = {
        "type": "object",
        "properties": {
            "agents": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "agent_type": {
                            "type": "string",
                            "enum": ["researcher", "planner", "coder", "tester", "reviewer", "general"],
                            "description": "The specialization of the swarm agent.",
                        },
                        "task": {"type": "string", "description": "Precise task prompt with acceptance criteria and expected output format."},
                        "agent_id": {"type": "string", "description": "Optional unique id other agents can address messages to (defaults to <type>_<n>)."},
                    },
                    "required": ["agent_type", "task"],
                },
            },
        },
        "required": ["agents"],
    }

    def __init__(self, orchestrator: SubagentOrchestrator):
        self.orchestrator = orchestrator

    def execute(self, agents: List[Dict[str, Any]], **kwargs) -> str:
        if not isinstance(agents, list) or not agents:
            return "Error: `agents` must be a non-empty list of {agent_type, task} objects."
        result = self.orchestrator.launch_swarm(agents)
        return result.format_report()


class SwarmSendMessageTool(Tool):
    name = "swarm_send_message"
    description = (
        "Send a message through the main thread's swarm bus. Address it to a specific agent_id, "
        "to 'all' for broadcast, or to 'main' for the coordinating parent agent. Other running "
        "swarm agents and the parent read these messages via swarm_read_messages."
    )
    action_type = "swarm"
    is_read_only = False
    parameters = {
        "type": "object",
        "properties": {
            "recipient": {
                "type": "string",
                "description": "Target agent_id, 'all' to broadcast to every swarm agent, or 'main' for the parent agent.",
            },
            "message": {"type": "string", "description": "The message body to deliver."},
        },
        "required": ["recipient", "message"],
    }

    def __init__(self, orchestrator: SubagentOrchestrator):
        self.orchestrator = orchestrator

    def execute(self, recipient: str, message: str, **kwargs) -> str:
        recipient = str(recipient or "all").strip()
        msg = self.orchestrator.post_message(recipient, message)
        target = "ALL" if recipient == "all" else recipient
        return f"Message #{msg.id} from {msg.sender} -> {target} posted to swarm bus."


class SwarmReadMessagesTool(Tool):
    name = "swarm_read_messages"
    description = (
        "Read messages posted on the main thread's swarm bus. Optionally filter by sender or "
        "recipient. Track the highest message id you have processed and pass it as since_index "
        "on subsequent reads to only see new messages."
    )
    action_type = "swarm"
    is_read_only = True
    parameters = {
        "type": "object",
        "properties": {
            "since_index": {
                "type": "integer",
                "description": "Only return messages with id greater than this value (0 for all).",
            },
            "sender": {"type": "string", "description": "Only return messages from this agent_id (optional)."},
            "recipient": {"type": "string", "description": "Only return messages addressed to this agent_id or 'all' (optional)."},
        },
        "required": [],
    }

    def __init__(self, orchestrator: SubagentOrchestrator):
        self.orchestrator = orchestrator

    def execute(self, since_index: int = 0, sender: Optional[str] = None, recipient: Optional[str] = None, **kwargs) -> str:
        messages = self.orchestrator.read_messages(
            since_index=max(0, int(since_index or 0)),
            sender=sender,
            recipient=recipient,
        )
        if not messages:
            return "No swarm messages match the given filters."
        lines = ["=== SWARM MAILBOX ==="]
        for m in messages:
            target = "ALL" if m.recipient == "all" else f"-> {m.recipient}"
            lines.append(f"[#{m.id}] {m.sender} {target}: {m.body}")
        if messages:
            lines.append(f"(latest message id: {messages[-1].id})")
        return "\n".join(lines)