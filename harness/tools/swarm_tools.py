"""
Agent Swarm tools for Harness.
Enables the model to delegate parallel work to concurrently running specialized
subagents that coordinate through the shared "main thread" message bus.
Subagents run in background threads. The main agent can continue working
while the swarm processes in the background.
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
        "messages via swarm_send_message / swarm_read_messages. By default runs in the "
        "background so you can continue working. Set background=false to block until all "
        "agents finish and return a combined report."
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
                        "depends_on": {"type": "array", "items": {"type": "string"}, "description": "Agent ids whose results must arrive before this one starts."},
                        "paths": {"type": "array", "items": {"type": "string"}, "description": "Files this agent owns (never shared with another worker)."},
                        "todo_id": {"type": "integer", "description": "Optional todo id to bind this worker to."},
                    },
                    "required": ["agent_type", "task"],
                },
            },
            "background": {
                "type": "boolean",
                "description": "Run in background (default true). Set to false to block until all agents finish.",
            },
            "preset": {
                "type": "string",
                "description": "Topology preset: research-build-verify | parallel-coders | debate (overrides agents if set).",
            },
        },
        "required": [],
    }

    def __init__(self, orchestrator: SubagentOrchestrator):
        self.orchestrator = orchestrator

    def execute(self, agents: List[Dict[str, Any]] = None, background: bool = True,
                preset: str = "", **kwargs) -> str:
        if preset:
            from harness.core.swarm_presets import get_preset
            p = get_preset(preset)
            if p is None:
                return f"Error: unknown preset '{preset}'."
            agents = list(p["agents"])
        if not isinstance(agents, list) or not agents:
            return "Error: `agents` must be a non-empty list of {agent_type, task} objects (or set `preset`)."
        result = self.orchestrator.launch_swarm(agents, background=background)
        if background:
            lines = [f"=== SWARM SPAWNED ({len(agents)} agents) ==="]
            lines.append(f"Status: {result.status} | Running in background run={getattr(result, 'run_id', '?')}")
            lines.append("The swarm is processing in the background. You can continue working.")
            lines.append("Use `swarm_read_messages` to check inter-agent communication.")
            lines.append("=" * 50)
            return "\n".join(lines)
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
            "payload_json": {"type": "string", "description": "Optional JSON structured handoff payload."},
            "artifacts_json": {"type": "string", "description": "Optional JSON array of artifact file paths."},
            "requires_ack": {"type": "boolean", "description": "Ask the recipient to ack this handoff."},
        },
        "required": ["recipient", "message"],
    }

    def __init__(self, orchestrator: SubagentOrchestrator):
        self.orchestrator = orchestrator

    def execute(self, recipient: str, message: str, payload_json: str = "",
                artifacts_json: str = "", requires_ack: bool = False, **kwargs) -> str:
        import json as _json
        recipient = str(recipient or "all").strip()
        payload = None
        artifacts = []
        if payload_json:
            try:
                payload = _json.loads(payload_json)
            except Exception:
                payload = {"raw": payload_json}
        if artifacts_json:
            try:
                artifacts = list(_json.loads(artifacts_json))
            except Exception:
                artifacts = []
        msg = self.orchestrator.post_message(recipient, message, payload=payload,
                                             artifacts=artifacts, requires_ack=requires_ack)
        target = "ALL" if recipient == "all" else recipient
        return f"Message #{msg.id} from {msg.sender} -> {target} posted to swarm bus."


class SwarmAckTool(Tool):
    name = "swarm_ack"
    description = "Acknowledge a swarm handoff message by id."
    action_type = "swarm"
    is_read_only = False
    parameters = {"type": "object", "properties": {"message_id": {"type": "integer"}}, "required": ["message_id"]}

    def __init__(self, orchestrator: SubagentOrchestrator):
        self.orchestrator = orchestrator

    def execute(self, message_id: int, **kwargs) -> str:
        bus = getattr(self.orchestrator, "bus", None)
        if bus is None:
            return "No bus."
        return f"Acked #{message_id}." if bus.ack(int(message_id)) else f"Message #{message_id} not found."


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
