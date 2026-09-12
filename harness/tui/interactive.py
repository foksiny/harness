"""
Interactive REPL loop for Harness.
Orchestrates prompt inputs, slash command dispatches, streaming output, and live HUD updates.
"""
from typing import Optional
from harness.core.agent import HarnessAgent
from harness.commands.registry import CommandRegistry
from harness.tui.terminal import TerminalRenderer
from harness.tui.input_handler import InputHandler
from harness.core.compaction import calculate_history_tokens

def run_interactive(agent: HarnessAgent):
    """Run interactive terminal session."""
    renderer = TerminalRenderer(agent.config.theme)
    commands = CommandRegistry()
    input_handler = InputHandler()

    renderer.clear_screen()
    renderer.print_banner()

    while True:
        # Render live status HUD
        tokens = calculate_history_tokens(agent.session.messages) if agent.session is not None else 0
        c_win = agent.compactor.context_window
        todos_summary = agent.todo_manager.summary()

        renderer.print_hud(
            mode=agent.mode.value,
            perm=agent.permission_manager.level.value,
            provider=agent.provider.display_name,
            model=agent.session.model if agent.session is not None else agent.config.model,
            tokens=tokens,
            context_win=c_win,
            todos_summary=todos_summary,
        )
        renderer.print_footer()

        prompt_str = f"[{renderer.theme.primary}]Harness ({agent.mode.value})>[/{renderer.theme.primary}] "
        # Format clean prompt for readline / input
        plain_prompt = f"Harness ({agent.mode.value})> "

        user_input = input_handler.get_input(plain_prompt)
        if not user_input:
            continue

        if user_input.lower() in ("/exit", "/quit", "exit", "quit"):
            renderer.print_info("Saving session and shutting down Harness. Goodbye!")
            if agent.session is not None:
                agent.session_manager.save(agent.session)
            break

        # Check slash command
        if user_input.startswith("/"):
            commands.handle(user_input, agent, renderer)
            continue

        # Execute agent step
        try:
            for ev in agent.step(user_input):
                renderer.render_agent_event(ev)
            renderer.finish_markdown()
            renderer.finish_thinking()
        except KeyboardInterrupt:
            renderer.finish_markdown()
            renderer.finish_thinking()
            renderer.print_warning("\nExecution interrupted by user.")
            agent.is_running = False
        except Exception as ex:
            renderer.finish_markdown()
            renderer.finish_thinking()
            renderer.print_error(f"\nExecution error: {str(ex)}")
