"""The Gemini Live message mapper accumulates input transcription fragments into one utterance.

``_map`` is pure over duck-typed message objects, so it is tested with no SDK: the risk it
guards is that the Live API streams input transcription as incremental deltas and the shadow
gate must judge the WHOLE utterance, not its trailing fragment.
"""

from __future__ import annotations

from types import SimpleNamespace

from contact_centre_conversations.adapters.gcp.voice_live import GeminiLiveVoiceSession
from contact_centre_conversations.ports.voice_engine import CallerUtterance


def _session() -> GeminiLiveVoiceSession:
    # __init__ stores its arguments and does no I/O, so a bare instance is enough to drive _map.
    return GeminiLiveVoiceSession(
        settings=SimpleNamespace(),  # type: ignore[arg-type]
        types=SimpleNamespace(),
        connection=SimpleNamespace(),
        session=SimpleNamespace(),
    )


def _input_message(text: str, finished: bool) -> SimpleNamespace:
    content = SimpleNamespace(
        input_transcription=SimpleNamespace(text=text, finished=finished),
        output_transcription=None,
        interrupted=False,
        turn_complete=False,
    )
    return SimpleNamespace(
        server_content=content,
        session_resumption_update=None,
        go_away=None,
        tool_call=None,
        data=None,
    )


def test_input_fragments_accumulate_into_one_final_utterance() -> None:
    session = _session()
    finals: list[CallerUtterance] = []
    for text, finished in [("I want", False), (" to close", False), (" my account", True)]:
        for event in session._map(_input_message(text, finished)):
            if isinstance(event, CallerUtterance) and event.final:
                finals.append(event)
    assert len(finals) == 1
    assert finals[0].text == "I want to close my account", (
        "the gate must see the whole utterance, not the trailing fragment"
    )


def test_turn_complete_flushes_an_unfinished_buffer() -> None:
    """If the model completes its turn without a finished-flagged transcription, whatever
    accumulated is still emitted so the shadow gate is never left inert."""
    session = _session()
    list(session._map(_input_message("check my", False)))
    message = _input_message("", False)
    message.server_content.turn_complete = True
    finals = [e for e in session._map(message) if isinstance(e, CallerUtterance) and e.final]
    assert [f.text for f in finals] == ["check my"]
