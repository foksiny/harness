"""
Interactive User Questioning Tool for Harness.
Allows the model to proactively prompt the user for clarification, design decisions,
or preference selection during execution.
"""
from typing import Dict, Any, List, Optional, Callable
from harness.tools.base import Tool

class AskUserTool(Tool):
    name = "ask_user"
    description = (
        "Ask the user a structured question when encountering genuine ambiguity, "
        "multiple valid architectural paths, or requiring user confirmation."
    )
    action_type = "ask_user"
    is_read_only = True
    parameters = {
        "type": "object",
        "properties": {
            "question": {"type": "string", "description": "The exact question to present to the user."},
            "options": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of selectable answer options (optional).",
            },
            "allow_custom": {"type": "boolean", "description": "Whether the user can type a custom response (default: true)."},
            "recommended_option": {"type": "string", "description": "Which option is recommended by the model (optional)."},
        },
        "required": ["question"],
    }

    def __init__(self, interactive_handler: Optional[Callable[[str, List[str], bool, Optional[str]], str]] = None):
        self.interactive_handler = interactive_handler

    def execute(
        self,
        question: str,
        options: Optional[List[str]] = None,
        allow_custom: bool = True,
        recommended_option: Optional[str] = None,
        **kwargs,
    ) -> str:
        # Use custom UI handler if attached (e.g. from TUI)
        if self.interactive_handler:
            return self.interactive_handler(question, options or [], allow_custom, recommended_option)

        # Terminal Fallback Prompt
        opts = options or []
        print("\n" + "=" * 60)
        print("🤖 [HARNESS IS ASKING FOR YOUR INPUT]")
        print(f"❓ {question}\n")

        if opts:
            for idx, opt in enumerate(opts, 1):
                rec_badge = " [RECOMMENDED]" if (recommended_option and recommended_option.lower() in opt.lower()) else ""
                print(f"  [{idx}] {opt}{rec_badge}")
            if allow_custom:
                print(f"  [0] Type a custom write-in response")

            print("=" * 60)
            try:
                raw = input("Your selection (number or custom answer): ").strip()
                if raw.isdigit():
                    val = int(raw)
                    if 1 <= val <= len(opts):
                        ans = opts[val - 1]
                        print(f"Selected: {ans}\n")
                        return f"User selected option [{val}]: {ans}"
                    elif val == 0 and allow_custom:
                        custom = input("Enter your custom answer: ").strip()
                        return f"User wrote custom response: {custom}"
                if raw:
                    return f"User replied: {raw}"
                # Fallback to recommended or first option
                def_ans = recommended_option or opts[0]
                return f"User defaulted to: {def_ans}"
            except (EOFError, KeyboardInterrupt):
                return "User skipped / cancelled question prompt."
        else:
            print("=" * 60)
            try:
                raw = input("Your answer: ").strip()
                return f"User replied: {raw}" if raw else "User provided no answer."
            except (EOFError, KeyboardInterrupt):
                return "User skipped question."
