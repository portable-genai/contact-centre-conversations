"""Managed GenerationPort: Gemini, constrained to a response schema and nothing else.

The SDK import is LAZY, inside the method, so the offline profiles import this module with no
cloud SDK installed. That is the property the SDK-free contract test proves in a fresh
interpreter, and it is why there is no module-scope ``import google``.

The request is deliberately narrow: the redacted turn, the retrieved passages, a system
instruction that says "quote, cite, and never state a figure", and a response schema. Whatever
comes back is still UNTRUSTED and still goes through ``domain/suggestions.validate_draft``: a
response schema is a request, not a guarantee, and the validator is the guarantee.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from ...config import Settings
from ...domain.models import RetrievedPassage
from ...domain.suggestions import MAX_SUGGESTION_CHARS

_SYSTEM = (
    "You draft one short reply for a contact-centre agent to read aloud. Use ONLY the supplied "
    "passages. Quote or paraphrase them; never introduce a figure, a date or an amount that is "
    "not in a passage you cite. Cite every passage you used by its id. If the passages do not "
    "answer the question, return an empty text."
)

_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "text": {"type": "string", "maxLength": MAX_SUGGESTION_CHARS},
        "passage_ids": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["text", "passage_ids"],
}


class VertexGenerationAdapter:
    """Draft a grounded reply with the managed model. Lazy import, bounded output."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def draft(
        self, prompt: str, passages: Sequence[RetrievedPassage]
    ) -> Mapping[str, object] | None:
        if not passages:
            # The model is not asked a question the knowledge base did not answer.
            return None
        # Lazy: the offline profiles must import this module with no SDK present.
        from google import genai  # noqa: PLC0415
        from google.genai import types  # noqa: PLC0415

        client = genai.Client(vertexai=True, location=self._settings.region)
        response = client.models.generate_content(
            model=self._settings.model,
            contents=_contents(prompt, passages),
            config=types.GenerateContentConfig(
                system_instruction=_SYSTEM,
                response_mime_type="application/json",
                response_schema=_SCHEMA,
                temperature=0.0,
                max_output_tokens=512,
            ),
        )
        parsed = getattr(response, "parsed", None)
        return parsed if isinstance(parsed, Mapping) else None


def _contents(prompt: str, passages: Sequence[RetrievedPassage]) -> str:
    """The whole prompt body: the redacted turn, then the passages with their ids."""
    lines = [f"Customer turn (redacted): {prompt}", "", "Passages:"]
    lines.extend(f"[{p.citation.source_id}] {p.text}" for p in passages)
    return "\n".join(lines)
