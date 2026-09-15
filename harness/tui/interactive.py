"""
Interactive REPL loop for Harness.
Orchestrates prompt inputs, slash command dispatches, streaming output, and live HUD updates.
"""
from typing import Optional
from harness.core.agent import HarnessAgent
from harness.commands.registry import CommandRegistry
from harness.tui.terminal import TerminalRenderer
from harness.tui.input_handler import InputHandler, SENTINEL_OPEN_AGENTS, SENTINEL_BACK, VIEW_AGENTS, VIEW_PARENT, is_command_input, no_echo_stdin
from harness.core.compaction import calculate_history_tokens

def run_interactive(agent: HarnessAgent):
    """Run interactive terminal session."""
    renderer = TerminalRenderer(agent.config.theme)
    commands = CommandRegistry()
    input_handler = InputHandler()

    renderer.clear_screen()
    renderer.print_banner()

    view = VIEW_PARENT

    while True:
        if view == VIEW_AGENTS:
            renderer.print_subagent_board(agent.subagent_orchestrator.list_records())
            plain_prompt = "Agents> "
        else:
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
            plain_prompt = f"Harness ({agent.mode.value})> "

        user_input = input_handler.get_input(plain_prompt, view)
        if user_input == SENTINEL_OPEN_AGENTS:
            view = VIEW_AGENTS
            continue
        if user_input == SENTINEL_BACK:
            view = VIEW_PARENT
            renderer.print_info("Returned to parent agent view.")
            continue
        if not user_input:
            continue

        if user_input.lower() in ("/exit", "/quit", "exit", "quit"):
            renderer.print_info("Saving session and shutting down Harness. Goodbye!")
            if agent.session is not None:
                agent.session_manager.save(agent.session)
            break

        # Check slash command — but only for real commands; a prompt whose first
        # token is a path (pasted/swiped image or video) goes to the agent.
        if user_input.startswith("/") and is_command_input(user_input, commands.commands):
            command_result = commands.handle(user_input, agent, renderer)
            if command_result == VIEW_AGENTS:
                view = VIEW_AGENTS
            elif command_result == VIEW_PARENT:
                view = VIEW_PARENT
            continue

        # In the agents board view only commands, ESC, and /back drive the
        # session; free text would be misread as a task for the parent agent.
        if view == VIEW_AGENTS:
            renderer.print_info("In the agents view. Use /agent <id>, /back, or ESC to return.")
            continue

        # Execute agent step (echo suppressed so keypresses during the run
        # never leak as ^C / ^[ control garbage into the terminal).
        try:
            with no_echo_stdin():
                renderer.start_fun_animation()
                first_event = True
                for ev in agent.step(user_input):
                    if first_event:
                        renderer.stop_fun_animation()
                        first_event = False
                    renderer.render_agent_event(ev)
                renderer.stop_fun_animation()
            renderer.finish_markdown()
            renderer.finish_thinking()
        except KeyboardInterrupt:
            renderer.stop_fun_animation()
            renderer.finish_markdown()
            renderer.finish_thinking()
            renderer.print_warning("\nExecution interrupted by user.")
            agent.is_running = False
        except Exception as ex:
            renderer.stop_fun_animation()
            renderer.finish_markdown()
            renderer.finish_thinking()
            renderer.print_error(f"\nExecution error: {str(ex)}")
