"""Minimal stdlib CLI: replay a scripted contact through either mode, or verify the audit chain.

Both mode subcommands go through ``services.require_mode`` first, so the CLI cannot reach a mode
the deployment has not enabled either. A surface that skipped the gate would be a second way in,
and a gate with a second way in is not a gate.
"""

from __future__ import annotations

import argparse
import sys

from hex_service_kit.logging import configure_logging

from .. import services
from ..config import build_container
from ..domain.kernel import utcnow
from ..domain.models import ContactChannel, ContactRef
from ..domain.modes import ContactMode, ModeDisabledError


def _contact(args: argparse.Namespace, mode: ContactMode) -> ContactRef:
    return ContactRef(
        contact_id=args.contact_id,
        tenant=args.tenant,
        market=args.market,
        locale=args.locale,
        mode=mode,
        channel=ContactChannel(args.channel),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="contact_centre_conversations")
    sub = parser.add_subparsers(dest="command", required=True)

    for name, help_text in (
        ("agent-assist", "Replay a scripted contact through the agent-assist whisper panel."),
        ("self-service", "Replay a scripted contact through the self-service assistant."),
    ):
        command = sub.add_parser(name, help=help_text)
        command.add_argument("contact_id", help="Also names the scripted stream to replay.")
        command.add_argument("--tenant", default="demo-bank")
        command.add_argument("--market", default="SG")
        command.add_argument("--locale", default="en-SG")
        command.add_argument("--channel", default="voice", choices=["voice", "chat"])
        command.add_argument("--actor", default="agent@bank.example")

    sub.add_parser("modes", help="Show which modes this deployment serves.")

    args = parser.parse_args(argv)
    container = build_container()
    # Idempotent: a process that is both an API app and a CLI configures once.
    configure_logging(container.settings.profile, service="contact-centre-conversations")

    if args.command == "modes":
        for gate in (container.settings.modes.agent_assist, container.settings.modes.self_service):
            state = "enabled" if gate.enabled else "disabled"
            bundle = gate.promotion_bundle or "(none)"
            print(f"{gate.mode.value}: {state}  promotion bundle: {bundle}")
        return 0

    mode = ContactMode.AGENT_ASSIST if args.command == "agent-assist" else ContactMode.SELF_SERVICE
    try:
        services.require_mode(container, mode)
    except ModeDisabledError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 3

    built = services.build_services(container)
    contact = _contact(args, mode)
    channel = container.conversation_channel
    channel.open(contact)

    for submission in channel.turns(contact):
        as_of = utcnow()
        if mode is ContactMode.AGENT_ASSIST:
            panel = built.agent_assist.observe(submission, actor=args.actor, as_of=as_of)
            print(
                f"turn {submission.index}: state={panel.progress.state_id} "
                f"next={panel.next_step.instruction[:60]!r} "
                f"due={[s.disclosure_id for s in panel.disclosures.due]} "
                f"missed={[s.disclosure_id for s in panel.disclosures.missed]}"
            )
            review = (panel.requires_human_review, panel.review_ref)
        else:
            reply = built.self_service.handle(submission, actor=args.actor, as_of=as_of)
            handed = reply.handoff.trigger.value if reply.handoff else "none"
            print(
                f"turn {submission.index}: gate={reply.verdict.outcome.value} "
                f"intent={reply.verdict.intent_id or 'none'} handoff={handed}"
            )
            review = (reply.requires_human_review, reply.review_ref)
        if review[0]:
            # Rule R8 on the CLI path too: the service routed it, and the reference says where.
            print(f"  routed to human review: {review[1]}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
