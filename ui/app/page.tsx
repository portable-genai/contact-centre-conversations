"use client";

import { useEffect, useState } from "react";

// Every request goes to THIS origin. The browser never learns the service's address and never
// holds its credential; the route handler under /api/agent forwards, having discarded whatever
// identity the client tried to assert.
const API = "/api/agent";

// Mirrors the service's seeded local personas. The picker is a DEV convenience: the server
// validates the selection against its own list, so a hand-crafted value cannot invent a persona.
const PERSONAS = ["analyst", "approver", "auditor", "other-tenant"];

// The two separately gated modes. They are SEPARATE PANELS on purpose: they carry different
// things, they are enabled independently, and a single merged panel would let an operator think
// that seeing one working says anything about the other.
type Mode = "agent_assist" | "self_service";

interface CardSummary {
  name?: string;
  description?: string;
  skills?: { id: string; name: string }[];
}

interface ModeStatus {
  mode: string;
  enabled: boolean;
  promotion_bundle?: string;
}

interface Citation {
  source_id: string;
  title: string;
  snippet?: string;
}

interface Disclosure {
  disclosure_id: string;
  state: string;
  severity: string;
  jurisdiction: string;
  due_by_ms?: number | null;
  reminder_text?: string;
}

interface Suggestion {
  text: string;
  citations: Citation[];
  passage_ids: string[];
}

interface AssistPanel {
  mode: string;
  state_id: string;
  completed_state_ids: string[];
  next_step: { instruction: string; rationale: string; citations: Citation[] };
  disclosures: Disclosure[];
  due_disclosure_ids: string[];
  missed_disclosure_ids: string[];
  suggestion: Suggestion | null;
  screen: string;
  deterministic_only: boolean;
  requires_human_review: boolean;
  review_ref: string;
}

interface SelfServiceReply {
  mode: string;
  verdict: {
    outcome: string;
    intent_id: string;
    confidence: number;
    confidence_floor: number;
    reasons: { code: string; outcome: string; detail: string }[];
  };
  suggestion: Suggestion | null;
  action: { action_id: string; executed: boolean; detail: string; review_ref: string } | null;
  handoff: { trigger: string; summary: string; carry_over_state_ids: string[] } | null;
  screen: string;
  contained: boolean;
  requires_human_review: boolean;
  review_ref: string;
}

const ASSIST_SCRIPT = [
  "Thank you for calling. This call is being recorded for quality.",
  "We use your information to service the account. Confirm your date of birth.",
  "Which merchant was it, and the amount of the transaction?",
];

export default function Home() {
  const [mode, setMode] = useState<Mode>("agent_assist");
  const [persona, setPersona] = useState(PERSONAS[0]);
  const [card, setCard] = useState<CardSummary | null>(null);
  const [modes, setModes] = useState<ModeStatus[]>([]);

  const [contactId, setContactId] = useState("ui-contact-0001");
  const [turnIndex, setTurnIndex] = useState(0);
  const [text, setText] = useState(ASSIST_SCRIPT[0]);
  const [customerText, setCustomerText] = useState("What is my card balance please?");
  const [requestedAction, setRequestedAction] = useState("");

  const [panel, setPanel] = useState<AssistPanel | null>(null);
  const [reply, setReply] = useState<SelfServiceReply | null>(null);
  const [failure, setFailure] = useState("");
  const [busy, setBusy] = useState(false);

  // The service names itself and reports its own mode posture, so this UI carries no hardcoded
  // product name and no hardcoded assumption about which modes a deployment serves.
  useEffect(() => {
    let live = true;
    fetch(API + "/.well-known/agent-card.json", { cache: "no-store" })
      .then((response) => (response.ok ? response.json() : null))
      .then((body) => {
        if (live) setCard(body as CardSummary | null);
      })
      .catch(() => undefined);
    fetch(API + "/healthz", { cache: "no-store" })
      .then((response) => (response.ok ? response.json() : null))
      .then((body) => {
        if (live && body) setModes((body.modes ?? []) as ModeStatus[]);
      })
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, []);

  const gate = modes.find((entry) => entry.mode === mode);
  const disabled = gate ? !gate.enabled : false;

  async function send(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setFailure("");
    const assist = mode === "agent_assist";
    const body = assist
      ? {
          contact_id: contactId,
          market: "SG",
          locale: "en-SG",
          text,
          index: turnIndex,
          speaker_id: "agent-1",
          role: "agent",
          start_ms: turnIndex * 10000,
          end_ms: turnIndex * 10000 + 6000,
        }
      : {
          contact_id: contactId,
          market: "SG",
          locale: "en-SG",
          text: customerText,
          index: turnIndex,
          speaker_id: "customer",
          role: "customer",
          channel: "chat",
          requested_action: requestedAction,
        };
    try {
      const response = await fetch(API + (assist ? "/v1/agent-assist/turn" : "/v1/self-service/turn"), {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Dev-Persona": persona },
        body: JSON.stringify(body),
      });
      const parsed = await response.json();
      if (!response.ok) {
        setFailure(typeof parsed.detail === "string" ? parsed.detail : JSON.stringify(parsed));
      } else if (assist) {
        setPanel(parsed as AssistPanel);
        const next = turnIndex + 1;
        setTurnIndex(next);
        if (next < ASSIST_SCRIPT.length) setText(ASSIST_SCRIPT[next]);
      } else {
        setReply(parsed as SelfServiceReply);
        setTurnIndex(turnIndex + 1);
      }
    } catch (error) {
      setFailure(String(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main>
      <h1>{card?.name ?? "Contact centre console"}</h1>
      <p className="sub">
        {card?.description ??
          "Two separately gated modes on one kernel. Every consequential decision is deterministic and cited."}
      </p>

      <form onSubmit={send}>
        <fieldset>
          <legend>Mode</legend>
          <label>
            Which gated mode this turn belongs to
            <select value={mode} onChange={(event) => setMode(event.target.value as Mode)}>
              <option value="agent_assist">Agent assist (whisper panel)</option>
              <option value="self_service">Self service (customer chat)</option>
            </select>
          </label>
          <p className="sub">
            {modes.length === 0
              ? "Mode posture unknown: the service has not reported one yet."
              : modes
                  .map(
                    (entry) =>
                      entry.mode + ": " + (entry.enabled ? "enabled" : "disabled") +
                      (entry.promotion_bundle ? " (" + entry.promotion_bundle + ")" : ""),
                  )
                  .join("  |  ")}
          </p>
          {disabled ? (
            <p className="sub">
              This mode is not enabled in this deployment, so its route will refuse with 503.
              Each mode is its own gated release with its own promotion evidence.
            </p>
          ) : null}
        </fieldset>

        <fieldset>
          <legend>Who you are</legend>
          <label>
            Seeded dev persona (local profile only; the server resolves identity, not this field)
            <select value={persona} onChange={(event) => setPersona(event.target.value)}>
              {PERSONAS.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
          </label>
        </fieldset>

        <fieldset>
          <legend>The turn</legend>
          <label>
            Contact id
            <input value={contactId} onChange={(event) => setContactId(event.target.value)} />
          </label>
          {mode === "agent_assist" ? (
            <label>
              What the agent said (turn {turnIndex})
              <textarea value={text} onChange={(event) => setText(event.target.value)} />
            </label>
          ) : (
            <>
              <label>
                What the customer said
                <textarea
                  value={customerText}
                  onChange={(event) => setCustomerText(event.target.value)}
                />
              </label>
              <label>
                Requested action (optional; a consequential one never auto-executes)
                <input
                  value={requestedAction}
                  onChange={(event) => setRequestedAction(event.target.value)}
                />
              </label>
            </>
          )}
          <button type="submit" disabled={busy}>
            {busy ? "Working" : mode === "agent_assist" ? "Send turn to the panel" : "Send message"}
          </button>
        </fieldset>
      </form>

      {failure ? <pre className="result error">{failure}</pre> : null}

      {mode === "agent_assist" && panel ? <WhisperPanel panel={panel} /> : null}
      {mode === "self_service" && reply ? <ChatPanel reply={reply} /> : null}

      <footer>
        Synthetic, obviously fictional data only. Identity is resolved server-side and the
        client-asserted actor is discarded; see ui/README.md for the embedding contract.
      </footer>
    </main>
  );
}

function WhisperPanel({ panel }: { panel: AssistPanel }) {
  return (
    <section>
      <h2>Whisper panel</h2>
      {panel.requires_human_review ? (
        <p className="banner">
          Review required. Routed to human review: {panel.review_ref || "no reference"}
        </p>
      ) : null}
      {panel.deterministic_only ? (
        <p className="banner">
          The guardrail screen is unavailable, so this panel is deterministic only and the
          suggestion is suppressed.
        </p>
      ) : null}
      <dl>
        <dt>Procedure state</dt>
        <dd>{panel.state_id}</dd>
        <dt>Completed</dt>
        <dd>{panel.completed_state_ids.join(", ") || "none"}</dd>
        <dt>Next best step</dt>
        <dd>{panel.next_step.instruction}</dd>
        <dt>Because</dt>
        <dd>{panel.next_step.rationale}</dd>
        <dt>Screen</dt>
        <dd>{panel.screen}</dd>
      </dl>

      <h3>Disclosure reminders</h3>
      <ul>
        {panel.disclosures.map((entry) => (
          <li key={entry.disclosure_id}>
            <strong>{entry.disclosure_id}</strong>: {entry.state}
            {entry.due_by_ms ? " (due by " + entry.due_by_ms + " ms)" : ""}
            {panel.due_disclosure_ids.includes(entry.disclosure_id)
              ? " " + (entry.reminder_text ?? "")
              : ""}
          </li>
        ))}
      </ul>

      <h3>Suggested reply</h3>
      {panel.suggestion ? (
        <>
          <p>{panel.suggestion.text}</p>
          <ul>
            {panel.suggestion.citations.map((citation) => (
              <li key={citation.source_id}>
                {citation.source_id}: {citation.title}
              </li>
            ))}
          </ul>
        </>
      ) : (
        <p className="sub">Suppressed: no retrieved passage, so nothing is suggested.</p>
      )}
    </section>
  );
}

function ChatPanel({ reply }: { reply: SelfServiceReply }) {
  return (
    <section>
      <h2>Self service</h2>
      {reply.handoff ? (
        <p className="banner">
          Handing you to a person. Reason: {reply.handoff.trigger}. {reply.handoff.summary}
        </p>
      ) : null}
      <dl>
        <dt>Gate verdict</dt>
        <dd>{reply.verdict.outcome}</dd>
        <dt>Intent</dt>
        <dd>
          {reply.verdict.intent_id || "none"} (match {reply.verdict.confidence}, floor{" "}
          {reply.verdict.confidence_floor})
        </dd>
        <dt>Screen</dt>
        <dd>{reply.screen}</dd>
        <dt>Contained</dt>
        <dd>{String(reply.contained)}</dd>
      </dl>

      <h3>Why</h3>
      <ul>
        {reply.verdict.reasons.map((reason) => (
          <li key={reason.code}>
            <strong>{reason.code}</strong> ({reason.outcome}): {reason.detail}
          </li>
        ))}
      </ul>

      {reply.action ? (
        <>
          <h3>Action</h3>
          <p>
            {reply.action.action_id}: {reply.action.executed ? "executed" : "not executed"}.{" "}
            {reply.action.detail}
            {reply.action.review_ref ? " Pending review: " + reply.action.review_ref : ""}
          </p>
        </>
      ) : null}

      <h3>Reply</h3>
      {reply.suggestion ? (
        <>
          <p>{reply.suggestion.text}</p>
          <ul>
            {reply.suggestion.citations.map((citation) => (
              <li key={citation.source_id}>
                {citation.source_id}: {citation.title}
              </li>
            ))}
          </ul>
        </>
      ) : (
        <p className="sub">
          No grounded reply: the gate denied this turn, or nothing was retrieved to ground one in.
        </p>
      )}
    </section>
  );
}
