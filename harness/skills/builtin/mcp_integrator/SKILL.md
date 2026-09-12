---
name: mcp_integrator
description: Enables Harness to install, configure, verify, and register Model Context Protocol (MCP) servers (stdio & sse) to expand its own tool capabilities on user request.
triggers: [mcp, add mcp, install mcp, configure mcp, connect mcp, setup mcp, new mcp server]
---
# MCP Integrator for Harness

Use this skill whenever the user asks to add an MCP server, connect to an external MCP tool, or integrate tools via the Model Context Protocol.

## MCP Configuration Schema
Harness stores MCP server definitions in `~/.harness/mcp.json` (global) or `.harness/mcp.json` (workspace):

```json
{
  "mcpServers": {
    "server_name": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-postgres", "postgresql://localhost/mydb"],
      "env": {
        "DEBUG": "true"
      }
    },
    "remote_sse_server": {
      "url": "http://localhost:8000/sse"
    }
  }
}
```

## Step-by-Step Procedure to Integrate an MCP Server

1. **Identify Server Type & Requirements**:
   - **Local stdio server**: Executed as a child process using `command`, `args`, and optional `env` (e.g. `npx`, `python -m`, `docker run`).
   - **Remote SSE/HTTP server**: Uses an HTTP/HTTPS endpoint URL supporting Server-Sent Events.

2. **Verify Dependencies**:
   - Check if the command binary exists using `run_command` (e.g., `which npx`, `which uvx`, `which docker`).
   - If missing, advise the user on installation or provide the command.

3. **Update `mcp.json`**:
   - Read the existing `~/.harness/mcp.json` or `.harness/mcp.json` using `view_file` (create if absent).
   - Insert the new server definition under `"mcpServers"`.
   - Write back using `write_file` or `edit_file`.

4. **Verify Connectivity & Tools**:
   - Test connecting to the server to ensure valid JSON-RPC 2.0 initialization.
   - List discovered tools and confirm they are bound to the agent's tool catalog.

5. **Report to User**:
   - Provide the user with a summary of the newly registered MCP server and its exposed tools.
