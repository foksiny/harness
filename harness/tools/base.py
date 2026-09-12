"""
Base Tool Interface for Harness.
Defines schema declarations, permission classifications, and invocation standards.
"""
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional

class Tool(ABC):
    """Abstract base class for all Harness tools."""

    name: str
    description: str
    parameters: Dict[str, Any]
    action_type: str = "general"   # e.g. "read_file", "write_file", "command", "search", etc.
    is_read_only: bool = False

    @abstractmethod
    def execute(self, **kwargs) -> Any:
        """Execute tool and return structured string or dictionary output."""
        pass

    def to_openai_schema(self) -> Dict[str, Any]:
        """Format tool for OpenAI / OpenRouter / Groq / DeepSeek / NIM function calling."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            }
        }

    def to_anthropic_schema(self) -> Dict[str, Any]:
        """Format tool for Anthropic Claude tools API."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.parameters,
        }

    def to_gemini_schema(self) -> Dict[str, Any]:
        """Format tool for Google Gemini function declarations."""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }
