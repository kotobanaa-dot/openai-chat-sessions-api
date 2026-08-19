"""Turns stored history into the message list sent to the model.

This is the part the task is really about: a reply must be produced from the
conversation, not from the last message alone.
"""

from dataclasses import dataclass

from app.models.chat import ChatSession, Message


@dataclass(frozen=True)
class ChatMessage:
    role: str
    content: str

    def as_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


class ContextBuilder:
    """Builds the prompt context under a message budget.

    Trimming is by message count, not tokens: it is predictable, needs no
    tokenizer, and the real ceiling is enforced by the provider anyway. The
    strategy is isolated here, so switching to a token budget or to
    summarising older turns touches this class only.
    """

    def __init__(self, max_messages: int) -> None:
        if max_messages < 1:
            raise ValueError("max_messages must be >= 1")
        self.max_messages = max_messages

    def build(
        self,
        session: ChatSession,
        history: list[Message],
        new_user_message: str,
    ) -> list[ChatMessage]:
        context: list[ChatMessage] = []

        # The system prompt is pinned: it defines behaviour and must survive
        # trimming no matter how long the dialogue grows.
        if session.system_prompt:
            context.append(ChatMessage(role="system", content=session.system_prompt))

        usable = [m for m in history if m.role in ("user", "assistant") and m.status == "complete"]
        # Keep the newest turns - they carry the current topic.
        recent = usable[-self.max_messages :] if self.max_messages else []
        context.extend(ChatMessage(role=m.role, content=m.content) for m in recent)

        context.append(ChatMessage(role="user", content=new_user_message))
        return context
