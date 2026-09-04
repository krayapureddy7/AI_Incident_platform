"""
Base Agent abstraction enforcing structured A2A messaging, versioning, and lifecycle.
"""
from abc import ABC, abstractmethod
from typing import Dict, Any
from ..models import AgentRole, A2AMessage, MessageType


class BaseAgent(ABC):
    """Abstract base class for all participating agents in the platform."""

    def __init__(self, role: AgentRole, version: str = "1.0.0"):
        self.role = role
        self.version = version

    def create_message(
        self,
        recipient: AgentRole,
        message_type: MessageType,
        correlation_id: str,
        payload: Dict[str, Any]
    ) -> A2AMessage:
        """Create a structured envelope for outbound A2A communication."""
        return A2AMessage(
            sender=self.role,
            recipient=recipient,
            message_type=message_type,
            correlation_id=correlation_id,
            payload=payload
        )

    @abstractmethod
    def run_node(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Process state as a LangGraph node, enforcing Pydantic structured I/O validation."""
        pass

    def __call__(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Allow direct callable registration as a LangGraph node."""
        return self.run_node(state)

    @abstractmethod
    def handle_message(self, message: A2AMessage, context: Dict[str, Any]) -> A2AMessage:
        """Process incoming structured A2A message and return structured response."""
        pass
