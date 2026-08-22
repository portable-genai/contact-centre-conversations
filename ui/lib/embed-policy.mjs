// The embedding security policy, as pure functions with no framework and no I/O.
//
// It lives in one plain module, imported by BOTH the proxy (which sets response headers), the
// API route (which decides CORS) and `next.config.mjs` (which refuses to build or boot on a
// policy nobody chose), and covered by `npm test`. A policy expressed twice is a policy that
// disagrees with itself the first time one copy is edited.
//
// Three rules, in the order they matter:
//
// 1. The client never asserts WHO it is. Any actor, principal, tenant or ACL header arriving from
//    the browser is DISCARDED before the request is forwarded. Identity is resolved server-side
//    from the deployment's own signal (a seeded dev persona offline, the platform's signed
//    assertion when the service is behind an identity-aware proxy).
// 2. Framing is an allowlist, never a wildcard. `frame-ancestors` is `'self'` when nobody named
//    a parent origin, and an entry carrying an asterisk ANYWHERE is refused rather than honoured
//    or quietly dropped.
// 3. CORS is per-tenant. A tenant's origin is allowed only if the operator registered it. There is
//    no fallback to `*`, the same wildcard refusal applies because these origins are trusted with
//    credentials, and an unset allowlist denies rather than opening up.
//
// Both allowlists are read in THREE states, and the middle one is the whole point. A module
// doing `parseAllowlist(env.UI_FRAME_ANCESTORS)` makes unset, `""`, `"   "` and `","` all
// produce `frame-ancestors 'self'` plus `X-Frame-Options: SAMEORIGIN`, byte for byte. An
// operator who empties the allowlist to name no parent HAS expressed an intent, and answering
// with the shipped default instead reads that absence as consent; worse, the deployment that
// lost the variable is indistinguishable from the one that locked itself down on purpose. A
// present-but-empty allowlist REFUSES, and `assertEmbedPolicyConfigured` runs that refusal
// from `next.config.mjs`, which `next build` and `next start` both evaluate, so the refusal is a
// build/boot refusal rather than a surprise on some later request.

import { ConfiguredEmptyError, readEnvSetting } from "./env-setting.mjs";

export { ConfiguredEmptyError };

/** Headers a browser may send that would let a caller assert its own identity. Always stripped. */
export const CLIENT_ASSERTED_IDENTITY_HEADERS = [
  "x-actor",
  "x-user",
  "x-user-id",
  "x-principal",
  "x-tenant",
  "x-acl",
  "x-roles",
  "x-goog-authenticated-user-email",
  "x-goog-iap-jwt-assertion",
  "authorization",
];

/** EXACT tokens that must never be accepted as an origin or a framing ancestor.
 *
 * `null` is kept here by name rather than folded into the asterisk test below, because it is a
 * wildcard by BEHAVIOUR and not by spelling: a sandboxed iframe presents a null origin, so
 * allowing it invites exactly the framing and the credentialed cross-origin call these two
 * allowlists exist to refuse. Nothing about the string itself would say so.
 */
const WILDCARD_TOKENS = new Set(["*", "'*'", "null", "*.*"]);

/**
 * True when an entry may not stand in either allowlist.
 *
 * Matching the exact tokens was not enough, and the gap was not theoretical. `https://*.bank.example`
 * is in none of them, so it was accepted and emitted verbatim, and CSP honours a host-source
 * wildcard: every subdomain could frame this console, including one an attacker obtains by
 * subdomain takeover and one that serves user content. The same string reaching the tenant
 * allowlist is worse than useless, because `Access-Control-Allow-Origin` is an exact echo rather
 * than a pattern match: every real subdomain the operator meant to register is denied, while the
 * malformed pattern itself is echoed back to any caller that sends it as an `Origin`.
 *
 * So ANY entry containing an asterisk is refused, on top of the named tokens. That turns away
 * nothing a deployment could correctly hold, since a real origin never contains the character.
 *
 * @param {string} entry
 * @returns {boolean}
 */
function isWildcard(entry) {
  return WILDCARD_TOKENS.has(entry) || entry.includes("*");
}

/** What `frame-ancestors` says when nobody named a parent origin: framed by nothing but itself. */
export const DEFAULT_FRAME_ANCESTORS = "'self'";

/**
 * The two framing policies the pre-CSP `X-Frame-Options` header can also express. A NAMED
 * allowlist has no `X-Frame-Options` spelling, so none is sent there rather than one that
 * contradicts the CSP in an older agent. Mirrors the service's own `_LEGACY_FRAME_OPTIONS`.
 */
const LEGACY_FRAME_OPTIONS = { "'self'": "SAMEORIGIN", "'none'": "DENY" };

/** CSP keywords that are a whole policy rather than an origin. `'none'` must stand alone. */
const FRAME_KEYWORDS = new Set(["'self'", "'none'"]);

/**
 * Parse a comma or space separated allowlist into unique entries, REFUSING every wildcard.
 *
 * The refusal used to `continue`, so a matched wildcard was dropped and the rest of the list was
 * kept and served: `UI_FRAME_ANCESTORS="https://a.bank.example *"` resolved to the one named
 * origin, and nothing an operator could read said the other entry had been discarded. The value
 * written and the value served differed in silence, and silence reads as consent. An operator who
 * writes a wildcard is now told it selected nothing, rather than left believing it was honoured.
 *
 * @param {string | undefined | null} raw
 * @param {string} [name] the variable being parsed, so the refusal can name it
 * @returns {string[]}
 * @throws {ConfiguredEmptyError} when any entry is a wildcard
 */
export function parseAllowlist(raw, name = "The allowlist") {
  if (!raw) return [];
  const seen = new Set();
  for (const piece of String(raw).split(/[\s,]+/)) {
    const entry = piece.trim();
    if (!entry) continue;
    if (isWildcard(entry)) {
      throw new ConfiguredEmptyError(
        name +
          " contains " +
          JSON.stringify(entry) +
          ", which is a wildcard. A wildcard framing ancestor permits UI redress from any site, " +
          "and a wildcard tenant origin is trusted with credentials, so neither is accepted. It " +
          "is REFUSED rather than skipped, because dropping it and serving the rest of the list " +
          "would leave an operator believing the entry was honoured. Name the exact origins.",
      );
    }
    seen.add(entry);
  }
  return [...seen];
}

/**
 * Resolve one allowlist variable in three states, refusing every state that names nothing.
 *
 * Unset returns null, which the caller reads as "no intent expressed" and answers with its own
 * documented default. Present but blank, and present but naming only separators or refused
 * wildcards, both throw: the operator expressed an intent and it selected nothing, and silently
 * substituting the default would be the two-state collapse this module exists to avoid.
 *
 * @param {Record<string, string | undefined>} env
 * @param {string} name
 * @param {string} unsetMeaning what the caller does when nobody chose, for the error message
 * @returns {string[] | null}
 */
function resolveAllowlist(env, name, unsetMeaning) {
  const setting = readEnvSetting(env, name);
  if (setting.isUnset) return null;
  if (setting.isConfiguredEmpty) {
    throw new ConfiguredEmptyError(
      name +
        " is set but empty. An empty allowlist names nothing, and inheriting the unset default (" +
        unsetMeaning +
        ") would make a variable somebody deliberately emptied indistinguishable from one that " +
        "went missing. Unset " +
        name +
        " to keep that default, or name the origins it should carry.",
    );
  }
  // Wildcards raise from inside the parse, naming the offending entry. What can still reach the
  // check below is a value made only of separators.
  const allowed = parseAllowlist(setting.value, name);
  if (allowed.length === 0) {
    throw new ConfiguredEmptyError(
      name +
        "=" +
        JSON.stringify(setting.raw) +
        " names no origin at all: it is nothing but separators. That is not a synonym for the " +
        "default (" +
        unsetMeaning +
        "), so it is refused rather than quietly downgraded to it.",
    );
  }
  return allowed;
}

/**
 * The `frame-ancestors` directive value.
 *
 * Unset means `'self'`: standalone, embeddable by nobody. Set and empty, or set to something
 * that names no origin, REFUSES. `'none'` is the supported spelling for "nobody may frame this",
 * and it must stand alone because CSP gives it no meaning alongside an origin.
 *
 * @param {Record<string, string | undefined>} env
 * @returns {string}
 */
export function frameAncestors(env) {
  const allowed = resolveAllowlist(
    env,
    "UI_FRAME_ANCESTORS",
    DEFAULT_FRAME_ANCESTORS + ", which permits same-origin framing",
  );
  if (allowed === null) return DEFAULT_FRAME_ANCESTORS;
  if (allowed.includes("'none'") && allowed.length > 1) {
    throw new ConfiguredEmptyError(
      "UI_FRAME_ANCESTORS mixes 'none' with named origins. CSP gives 'none' no meaning beside " +
        "an origin, so the combination is refused rather than resolved to whichever half is " +
        "more permissive. Use 'none' alone to refuse framing, or name only the parent origins.",
    );
  }
  return allowed.join(" ");
}

/**
 * Whether any PARENT origin may frame this UI. `'self'` and `'none'` are both "nobody else".
 * @param {Record<string, string | undefined>} env
 */
export function isEmbeddable(env) {
  return !FRAME_KEYWORDS.has(frameAncestors(env));
}

/**
 * The registered tenant origins. Unset is an EMPTY allowlist, which denies: that is already the
 * restrictive branch, so unset needs no refusal. Set and empty still refuses, because an
 * operator who blanks the line has expressed something and deserves to be told it selected
 * nothing rather than to watch every cross-origin call fail with no explanation.
 * @param {Record<string, string | undefined>} env
 * @returns {string[]}
 */
export function tenantOrigins(env) {
  return resolveAllowlist(env, "UI_TENANT_ORIGINS", "an empty allowlist, which denies") ?? [];
}

/**
 * Resolve the CORS `Access-Control-Allow-Origin` for one request origin.
 * Returns null when the origin is not registered, which the caller must treat as a denial.
 * @param {string | null | undefined} origin
 * @param {Record<string, string | undefined>} env
 * @returns {string | null}
 */
export function corsOriginFor(origin, env) {
  // Resolved FIRST, and deliberately before the cheap `!origin` exit: a misconfigured allowlist
  // must refuse on every request, not only on the cross-origin ones that happen to reach it.
  const allowed = tenantOrigins(env);
  // The REQUEST origin is a value the caller wrote, so a wildcard here is DENIED rather than
  // raised on: a header a browser sends must never become a 500. The allowlist can no longer
  // hold a wildcard for this to match, so this is the second line rather than the first.
  if (!origin || isWildcard(origin)) return null;
  return allowed.includes(origin) ? origin : null;
}

/**
 * Resolve every embedding variable, for the side effect of refusing a policy nobody chose.
 *
 * Called at module scope from `next.config.mjs`, which `next build` and `next start` both
 * evaluate, so a deployment whose allowlist rendered empty never comes up at all. A refusal at
 * boot is the one outcome a two-state read cannot imitate.
 *
 * @param {Record<string, string | undefined>} env
 */
export function assertEmbedPolicyConfigured(env) {
  frameAncestors(env);
  tenantOrigins(env);
}

/** Raised when the nonce policy and the rendering mode disagree, which serves un-hydratable HTML. */
export class UnhydratableCspError extends Error {}

/**
 * Refuse a build whose CSP mints a nonce that the rendered HTML can never carry.
 *
 * This exists because the failure it catches is INVISIBLE to every cheaper check. The headers are
 * right, the module tests pass, the build succeeds, the page renders, and a screenshot looks
 * correct; only a browser executing the page reveals that React never attached. Worse, the
 * half-configured state (nonce in the CSP, static rendering) blocks strictly more than the
 * unfixed `script-src 'self'` did, because `'strict-dynamic'` turns off the `'self'` fallback that
 * was at least loading the chunk scripts.
 *
 * `next.config.mjs` reads `app/layout.tsx` and passes it here, so the refusal happens at `next
 * build` and `next start`. No I/O happens in this module: it takes the source as a string, which
 * keeps it importable from the edge-runtime proxy.
 *
 * @param {string} layoutSource contents of `app/layout.tsx`
 * @throws {UnhydratableCspError}
 */
export function assertHydratableCsp(layoutSource) {
  if (!/export\s+const\s+dynamic\s*=\s*["']force-dynamic["']/.test(layoutSource)) {
    throw new UnhydratableCspError(
      'app/layout.tsx must set `export const dynamic = "force-dynamic"`. The CSP mints a ' +
        "per-request nonce, and Next can only stamp it onto script tags for a dynamically " +
        "rendered route. Statically prerendered HTML was built before the nonce existed, so " +
        "every script is blocked and the page never hydrates.",
    );
  }
}

/**
 * Copy request headers, dropping every client-asserted identity header.
 * @param {Headers} incoming
 * @returns {Headers}
 */
export function stripClientIdentity(incoming) {
  const forwarded = new Headers(incoming);
  for (const name of CLIENT_ASSERTED_IDENTITY_HEADERS) {
    forwarded.delete(name);
  }
  // Hop-by-hop and routing headers that must not survive a same-origin proxy hop.
  for (const name of ["cookie", "host", "connection", "content-length"]) {
    forwarded.delete(name);
  }
  return forwarded;
}

/**
 * A fresh, unguessable nonce for one response's `script-src`. Base64 of 16 random bytes, from the
 * Web Crypto `crypto` global present in every Next runtime. One per request: a reused nonce is a
 * predictable nonce, so the caller mints it per request and never caches it.
 * @returns {string}
 */
export function generateNonce() {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return btoa(String.fromCharCode(...bytes));
}

/**
 * The response header baseline every surface of this UI serves, mirroring the API side.
 *
 * `script-src` is nonce-based when a nonce is supplied, and this is load-bearing rather than
 * cosmetic. Next ships its hydration bootstrap as INLINE `<script>` tags carrying the Flight
 * payload, so a bare `script-src 'self'` blocks them: the server HTML renders, `__next_f` stays
 * empty, React never attaches, and the console is dead markup that looks fine in a screenshot.
 * `'strict-dynamic'` lets the nonced bootstrap load its own chunks without each chunk carrying
 * the nonce.
 *
 * Two things must BOTH be true or this silently fails, in opposite directions:
 *
 *   1. `proxy.ts` must put this CSP on the REQUEST headers, which is where Next reads the nonce
 *      it stamps onto every script tag it emits.
 *   2. The route must be DYNAMICALLY rendered. A statically prerendered page was built before the
 *      nonce existed, so nothing carries it, and because `'strict-dynamic'` disables the `'self'`
 *      fallback, adding a nonce to a static page blocks strictly MORE than plain `'self'` did.
 *      That is why `app/layout.tsx` sets `export const dynamic = "force-dynamic"`, and why
 *      `assertHydratableCsp` refuses the half-configured combination.
 *
 * With no nonce, `script-src` stays `'self'` with no inline allowance: the right answer for any
 * response that carries no Next-rendered document.
 *
 * @param {Record<string, string | undefined>} env
 * @param {string} [nonce] per-request nonce from {@link generateNonce}; omit for non-document responses
 * @returns {Record<string, string>}
 */
export function securityHeaders(env, nonce) {
  const directive = frameAncestors(env);
  const scriptSrc = nonce
    ? `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'`
    : "script-src 'self'";
  const headers = {
    "Content-Security-Policy": [
      "default-src 'self'",
      scriptSrc,
      "style-src 'self' 'unsafe-inline'",
      "connect-src 'self'",
      "img-src 'self' data:",
      "object-src 'none'",
      "base-uri 'self'",
      "form-action 'self'",
      "frame-ancestors " + directive,
    ].join("; "),
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "Cross-Origin-Opener-Policy": "same-origin",
  };
  const legacy = LEGACY_FRAME_OPTIONS[directive];
  if (legacy) {
    // Only for the two policies X-Frame-Options can actually express. Sending SAMEORIGIN
    // alongside a real frame-ancestors list would contradict it in older agents.
    headers["X-Frame-Options"] = legacy;
  }
  return headers;
}
