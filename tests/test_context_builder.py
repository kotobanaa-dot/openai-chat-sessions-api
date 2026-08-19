"""Context assembly - the requirement that a reply uses the conversation."""

from types import SimpleNamespace

from app.services.context_builder import ContextBuilder


def session(system_prompt: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(system_prompt=system_prompt)


def msg(role: str, content: str, status: str = "complete") -> SimpleNamespace:
    return SimpleNamespace(role=role, content=content, status=status)


def test_history_is_included_not_just_the_last_message() -> None:
    builder = ContextBuilder(max_messages=20)
    history = [msg("user", "My name is Oleh"), msg("assistant", "Noted.")]

    context = builder.build(session(), history, "What is my name?")

    assert [m.content for m in context] == [
        "My name is Oleh",
        "Noted.",
        "What is my name?",
    ]


def test_system_prompt_goes_first() -> None:
    builder = ContextBuilder(max_messages=20)
    context = builder.build(session("Be terse."), [msg("user", "hi")], "hello")
    assert context[0].role == "system"
    assert context[0].content == "Be terse."


def test_old_messages_are_trimmed_but_system_prompt_survives() -> None:
    builder = ContextBuilder(max_messages=2)
    history = [msg("user", f"m{i}") for i in range(10)]

    context = builder.build(session("Stay in role."), history, "latest")

    assert context[0].role == "system"
    # system + 2 kept + the new message
    assert len(context) == 4
    assert [m.content for m in context[1:]] == ["m8", "m9", "latest"]


def test_failed_messages_are_not_sent_to_the_model() -> None:
    builder = ContextBuilder(max_messages=20)
    history = [msg("user", "lost call", status="failed"), msg("user", "kept")]

    context = builder.build(session(), history, "next")

    assert [m.content for m in context] == ["kept", "next"]


def test_new_message_is_always_last() -> None:
    builder = ContextBuilder(max_messages=1)
    history = [msg("user", "a"), msg("assistant", "b")]
    context = builder.build(session(), history, "final")
    assert context[-1].content == "final"
    assert context[-1].role == "user"
