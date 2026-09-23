# MintFlow Authentication Design Proposal

## 1. Recommended authentication architecture

MintFlow should own a small, replaceable authentication module inside the modular monolith. A
`User` owns the account and all financial data. Email is the MVP account identity; Telegram is a
separate client connection. Neither an email address nor a Telegram user is the `User` aggregate.

The smallest correct design is:

- one `User` with one active `EmailIdentity`;
- passwordless login through short-lived, single-use email challenges;
- opaque, revocable server-side Web sessions carried by secure cookies; and
- at most one `TelegramConnection`, established through a Web-initiated challenge and explicit
  Web confirmation.

This keeps domain authorization based on `User.id` and hides authentication mechanisms behind
application ports. A future hosted identity provider can replace Magic Link issuance and session
verification while resolving its subject to the existing `User`.

### Responsibility boundaries

**Domain**

- Owns `User` lifecycle and account status.
- Enforces whether an active User may access or change owned financial data.
- Does not generate tokens, send email, set cookies, or interpret Telegram updates.

**Application**

- Orchestrates registration/login, challenge consumption, session creation and revocation,
  Telegram linking and unlinking, and authorization checks.
- Defines ports for challenge persistence, sessions, email delivery, clocks, randomness, rate
  limiting, Telegram identity verification, and audit evidence.
- Resolves authenticated identities to `User.id`; domain use cases receive that ID rather than
  email addresses, session IDs, or Telegram IDs.

**Infrastructure**

- Persists identities, challenges, sessions, connections, rate-limit state, and audit records.
- Generates cryptographically secure random values, hashes stored bearer secrets, sends email,
  verifies Telegram webhook provenance, and provides transaction/uniqueness guarantees.
- Redacts secrets and sensitive identifiers from logs and telemetry.

**Web Client**

- Requests a Magic Link using an email address and always shows the same neutral response.
- Completes login, holds only the secure session cookie, initiates and confirms Telegram linking,
  and performs unlinking with CSRF protection.
- Does not store bearer tokens in browser storage.

**Telegram Client**

- Accepts linking only in a private bot chat through a valid Web-initiated challenge.
- Trusts the Telegram user ID only when it comes from a verified Telegram update; usernames and
  display names are presentation metadata, never identity proof.
- Refuses financial capture and account disclosure while unlinked.

Email access is the MVP recovery mechanism. Losing Telegram must not prevent Web login. Losing the
email mailbox has no automatic recovery path in the MVP; support must not manually reassign an
account without a separately approved, high-assurance recovery policy.

## 2. User journeys

### First registration

The visitor submits an email address. MintFlow validates and normalizes it, returns a generic
response, and sends a Magic Link if allowed by rate limits. When the challenge is consumed, the
application atomically creates the `User` and `EmailIdentity` if they do not exist, marks the email
verified, creates a fresh Web session, and redirects only to an approved internal destination.

### Returning login

The same request and response are used whether or not the email exists. Consuming a valid challenge
resolves the existing identity, rotates any pre-authentication session, creates a new authenticated
session, and redirects to the Web Client. Existing sessions remain valid by default.

### Expired or reused Magic Link

Show the same non-sensitive failure page for expired, invalid, and previously consumed links, with
an action to request a new link. Never reveal whether an account exists or which failure occurred.
Repeated consumption must not create another session.

### Linking Telegram

An authenticated user starts linking in Web settings. MintFlow creates a five-minute, single-use
challenge and presents a Telegram bot deep link. A verified private-chat bot callback claims the
challenge for that Telegram user and makes the connection pending. The Web Client then displays
safe Telegram account metadata and requires explicit confirmation from the same initiating Web
session before the connection becomes active.

### Telegram already linked elsewhere

The claim or confirmation fails generically and atomically. MintFlow never transfers the Telegram
identity, reveals the other account, or replaces either connection. The user must unlink it while
authenticated to its current MintFlow account; exceptional recovery is a support policy, not an
automatic flow.

### Unlinking Telegram

An authenticated Web session submits a CSRF-protected unlink action and confirms the consequence.
MintFlow deactivates the connection and records minimal audit evidence. The bot immediately stops
accepting captures or exposing account data for that Telegram user. Existing financial data stays
owned by the User.

### User loses access to Telegram

The user logs in by email and unlinks the old Telegram connection. They may then link a different
Telegram account. No Telegram access is required because Telegram is not account ownership.

### Changing email

Self-service email change is postponed for MVP. It requires proof through the current mailbox, proof
of the new mailbox, conflict handling, session policy, and recovery rules. For the first 100 users,
an email change should not be performed manually unless a reviewed operational procedure is added.

## 3. Data concepts

These are conceptual persistence records, not SQL table designs.

### User

- **Purpose and classification:** Domain aggregate and stable ownership boundary.
- **Essential attributes:** Stable ID, account status, creation time, and domain preferences already
  approved elsewhere.
- **Lifecycle:** Created on first successful email verification; active until deactivated. MVP supports
  deactivation only; hard deletion and anonymization require a separate future policy.
- **Constraints:** Identity mechanisms resolve to exactly one User; ownership is not transferable.
- **Retention:** Follows the approved User and financial-data deletion policy, not token retention.

### EmailIdentity

- **Purpose and classification:** Supporting identity record mapping an email to a User; not the User
  aggregate.
- **Essential attributes:** User ID, original display form, canonical comparison form, verification
  time, creation/update times, and status if deactivation is needed.
- **Lifecycle:** Created and verified during first successful Magic Link consumption. One active
  identity per User in MVP.
- **Constraints:** Canonical email is globally unique. Canonicalization must use the chosen email
  validation library and must not invent provider-specific rules such as removing dots or `+` tags.
- **Retention:** Retained while the account exists; deletion and backup expiry follow privacy policy.

### LoginChallenge

- **Purpose and classification:** Short-lived infrastructure record proving control of a mailbox.
- **Essential attributes:** ID, canonical email, token hash, issued/expiry/consumed times, purpose,
  safe internal return target, request context for abuse controls, and optional invalidation reason.
- **Lifecycle:** Issued, then consumed, expired, or invalidated. Consumption is a single atomic state
  transition.
- **Constraints:** Token hash is unique; only one consumer can succeed. Issuing a newer challenge
  need not invalidate older unexpired challenges for MVP, avoiding confusing multi-device behavior.
- **Retention:** Delete secret-derived data soon after operational need; retain only minimal,
  non-secret audit evidence according to an approved short retention period.

### WebSession

- **Purpose and classification:** Infrastructure authentication state for one browser session.
- **Essential attributes:** ID, User ID, session-secret hash, issued/last-seen/expiry/revoked times,
  and minimal security metadata needed for investigation.
- **Lifecycle:** Created only after successful authentication, expires after 30 days absolute by
  default, and may be revoked by logout, account deactivation, or security action.
- **Constraints:** Session-secret hash is unique; revoked or expired sessions never authenticate.
- **Retention:** Purge expired/revoked records after a short approved audit window. Do not build a
  device-management history.

### TelegramConnection

- **Purpose and classification:** Supporting connection record mapping a verified Telegram user to a
  User. `TelegramIdentity` may be its internal name, but `Connection` better expresses revocability.
- **Essential attributes:** User ID, immutable Telegram user ID, status, linked/unlinked times, and
  optional display metadata that is never used for authorization.
- **Lifecycle:** Pending, active, then unlinked. Relinking creates or reactivates a connection only
  after a new challenge and conflict checks.
- **Constraints:** At most one active Telegram connection per User and one active MintFlow connection
  per Telegram user ID.
- **Retention:** Retain an unlinked connection for 30 days and then delete it. MVP does not
  pseudonymize Telegram identifiers; financial records retain only User ownership.

A separate short-lived `TelegramLinkChallenge` infrastructure record should contain its token hash,
initiating User and Web session, expiry, claim state, claimed Telegram ID, confirmation state, and
timestamps. It follows the same hash-only, single-use, short-retention rules as login challenges.

## 4. Token design

Use opaque random bearer tokens rather than signed self-contained tokens. Opaque tokens support
single use, immediate invalidation, minimal URL contents, and straightforward revocation without
embedding account data in links.

- Generate at least 32 random bytes with `secrets.token_urlsafe`.
- Store only a SHA-256 hash of each high-entropy token; compare digests in constant time where a
  direct comparison occurs. Slow password hashing is unnecessary for uniformly random 256-bit
  secrets.
- Use a 15-minute expiry for Magic Links and a five-minute expiry for Telegram link challenges.
- Consume tokens through one conditional persistence operation inside a transaction. Exactly one
  concurrent request may change an unconsumed, unexpired challenge to consumed.
- Treat invalid, expired, consumed, and unknown tokens uniformly at the client boundary.
- Allow explicit invalidation for account deactivation or a security response. Do not invalidate all
  prior login links merely because a newer one was requested in MVP.
- Rate-limit requests by canonical email and network source. The approved initial limits are three
  reserved email delivery attempts per canonical email per 15 minutes and 30 login requests per
  network source per 15 minutes. Broader service-level protection is postponed.
- Make successful consumption idempotent only in outcome reporting: retries return a neutral result
  but never reveal or recreate the session produced by the first request.

Magic Link secrets inevitably appear in the initial URL. Keep that page free of third-party
resources, send `Referrer-Policy: no-referrer` and no-store responses, avoid logging query strings,
and redirect immediately to a clean URL after completion. Do not mutate authentication state on
`GET`: email scanners commonly follow links. The confirmation page should submit a `POST` to consume
the token. A later enhancement may move the token to a URL fragment and exchange it with first-party
JavaScript, but that complexity is not required for MVP.

Amended 2026-09-24 (found in the first browser run of the Web Client): the magic-link
confirmation page uses `Referrer-Policy: strict-origin` instead of `no-referrer`. Under
`no-referrer`, browsers send `Origin: null` with the page's form POST (Fetch standard), so the
required first-party Origin check rejected every real sign-in. `strict-origin` still keeps the
token out of every Referer (only the bare origin is ever sent); all other authentication
responses keep `no-referrer`.

Return targets must be server-selected identifiers or validated relative paths. Never accept an
arbitrary absolute redirect URL.

## 5. Web session strategy

### Server-side sessions with secure cookies

An opaque cookie identifies revocable server-side state. This requires a persistence lookup, but
supports logout, account deactivation, investigation, and later provider replacement with little
complexity. It also keeps identity and authorization data out of the browser.

### JWT access and refresh tokens

JWTs reduce some session lookups and can help independent services validate identity, but MintFlow is
a modular monolith with no public API or native client. Refresh rotation, revocation, key management,
claim drift, and token storage would add failure modes without an MVP benefit.

### Recommendation

Use server-side sessions. Put only a cryptographically random session secret in a host-only cookie;
store its hash server-side. Configure `HttpOnly`, `Secure`, `SameSite=Lax`, `Path=/`, no `Domain`, and
an explicit 30-day maximum age. Use a production cookie name with the `__Host-` prefix.

Rotate the session on every successful login to prevent fixation. Require CSRF tokens bound to the
session for all state-changing browser requests; SameSite is defense in depth, not the sole CSRF
control. Logout revokes the server record and expires the cookie. Account deactivation revokes all
sessions. Ordinary login leaves other sessions active for MVP. Do not implement sliding expiration
or a device-management UI initially.

## 6. Email delivery

The application should depend on an `EmailSender` port that accepts a typed Magic Link message. The
port must not expose a specific vendor or let domain code compose transport details. The production
adapter may later use SMTP or a transactional-email API and must be replaceable without changing
authentication use cases.

Tests use an in-memory fake that captures typed messages and exposes tokens only to test code. Local
development uses a local mail-capture service or a clearly marked development sink; it must never
send to real recipients. Logging a full Magic Link is not an acceptable production adapter.

Production needs a sending domain, SPF, DKIM and DMARC configuration, TLS, bounce/complaint handling,
delivery metrics, suppression behavior, retry policy, alerting, and a way for support to diagnose
delivery without viewing tokens. Provider selection is an operational deployment decision, not a
permanent architecture choice.

## 7. Telegram linking

1. An authenticated, CSRF-protected Web request creates a five-minute link challenge bound to the
   initiating User and Web session.
2. Web displays a deep link to the configured MintFlow bot containing only the opaque challenge.
3. The bot accepts it only from a private chat. Infrastructure verifies webhook authenticity and
   reads the immutable Telegram sender ID from the update.
4. One atomic claim checks expiry, single use, User eligibility, and global Telegram-ID uniqueness.
   Duplicate delivery from Telegram receives the same safe acknowledgement without repeating work.
5. Web polls or refreshes the challenge status and displays safe Telegram metadata. The same Web
   session explicitly confirms it; only then does the connection become active.
6. The bot acknowledges successful linking only after activation. Expired, invalid, reused, or
   conflicting attempts provide a generic restart instruction.

Binding confirmation to the initiating Web session prevents someone who intercepts the deep link
from silently attaching their Telegram identity. A claim by the wrong person remains pending and
expires; it must not disclose MintFlow account data. Tokens must not be accepted from group chats,
channels, forwarded messages, usernames, phone numbers, or user-supplied Telegram IDs.

Relinking after unlinking always requires a new challenge. It never transfers a Telegram identity
already active elsewhere. Unlinking takes effect immediately for authorization even if Telegram
delivery is delayed. Database uniqueness plus transactional state changes, not pre-checks alone,
must resolve races between claims, confirmations, relinks, and unlinks.

## 8. Security requirements

- **Account enumeration:** Use identical request responses and materially similar timing for known,
  unknown, rate-limited, and suppressed addresses. Avoid account-specific delivery errors in UI.
- **Brute force and flooding:** Use per-email, per-network-source, and global limits; generic
  responses; delivery deduplication/cooldowns; metrics; and operational blocking. Do not make CAPTCHA
  an MVP dependency.
- **Token leakage:** Store hashes only, suppress query strings and cookies from logs, avoid analytics
  on token pages, use TLS/no-store/no-referrer, and keep expiries short.
- **Session fixation:** Ignore or rotate pre-authentication session state and issue a new secret after
  login.
- **CSRF:** Use session-bound CSRF tokens and origin checks on sensitive actions, with SameSite cookies
  as an additional control.
- **Replay:** Atomically consume challenges once and reject reuse without revealing state.
- **Open redirects:** Permit only approved internal route identifiers or validated relative paths.
- **Secret logging:** Apply structured redaction at request, exception, tracing, email, and Telegram
  boundaries; never log raw URLs, cookies, tokens, or authorization headers.
- **Telegram impersonation:** Trust only sender IDs from authenticated Telegram webhook traffic and
  private chats. Never trust usernames or IDs submitted by a client.
- **Race conditions:** Back application checks with uniqueness constraints and conditional atomic
  transitions. Conflicts return stable, non-disclosing outcomes.
- **Deletion and retention:** Define and automate deletion for expired challenges, sessions,
  disconnected Telegram identifiers, audit evidence, and backups. MVP supports account deactivation
  only; destructive deletion and anonymization require a separate future policy.
- **Audit evidence:** Record event type, time, User ID where known, result category, and coarse request
  context for login, session revocation, link, conflict, and unlink events. Never store bearer
  secrets or full Magic Links. This is operational evidence, not enterprise audit logging.

## 9. MVP boundaries

Explicitly postpone:

- passwords and password reset;
- social login and hosted identity-provider integration;
- passkeys and multi-factor authentication;
- multiple email addresses per User;
- multiple Telegram accounts per User;
- organizations, shared accounts, roles, and permissions;
- device-management UI and per-device naming;
- external public API authentication;
- native mobile authentication; and
- automated recovery without access to the verified email mailbox.

Do not add speculative username, phone-number, backup-code, invitation, merge-account, or identity-
transfer features.

## 10. Recommended libraries

Keep the dependency set minimal and confirm versions against the project constraints when
implementation begins; do not modify `pyproject.toml` as part of this proposal.

- **Python standard library:** Use `secrets` for tokens, `hashlib.sha256` for high-entropy bearer-token
  hashes, `hmac.compare_digest` where comparisons are performed in application code, and timezone-
  aware `datetime` values. Use injected clock and randomness interfaces in tests.
- **FastAPI/Starlette:** Use request/response and cookie primitives and dependency injection. Do not
  use Starlette's signed-cookie session middleware as the authenticated session store because the
  recommended sessions are server-side and revocable.
- **Cryptographic libraries:** Add none for the proposed token design. Do not add password hashing or
  JWT libraries. If future requirements introduce encryption or asymmetric signing, use the
  maintained `cryptography` package rather than custom cryptography.
- **Email validation:** Add the established `email-validator` package when implementing address
  syntax, internationalized domains, and normalized comparison. Configure it so login does not
  depend on live DNS deliverability checks.
- **Persistence:** Continue using the approved PostgreSQL/`psycopg` stack when persistence is
  designed. Rely on database transactions, conditional updates, and uniqueness constraints; do not
  introduce an ORM, cache, or authentication framework solely for this feature.

Email transport should begin behind the application port. Select its adapter with deployment
operations; a provider SDK is justified only if the selected transport cannot be supported cleanly
through existing HTTP or SMTP capabilities.

## 11. Testing strategy

Critical automated tests should cover:

- token entropy/shape, hash-only persistence, and absence of raw tokens from stored records;
- valid consumption, exact expiry boundaries, invalid tokens, reused tokens, and invalidation;
- two concurrent consumers, proving exactly one creates an authenticated outcome;
- first-login User/identity creation and returning-login resolution without duplicate Users;
- generic responses for existing, unknown, invalid, expired, suppressed, and rate-limited requests;
- rate limits by email and network source, cooldown behavior, and recovery after the window;
- session creation only after authentication, rotation against fixation, absolute expiration,
  logout, account-wide revocation, and rejection of revoked sessions;
- cookie flags, CSRF failures/successes, allowed redirects, and rejection of external redirects;
- authorization boundaries proving one User/session/Telegram connection cannot access another
  User's data or mutate their identity state;
- Telegram private-chat enforcement, verified sender extraction, expiry, interception pending state,
  same-Web-session confirmation, duplicate callbacks, reused challenges, conflicts, relinking, and
  unlinking;
- concurrent Telegram claims and confirmations under uniqueness conflicts; and
- structured-log, exception, tracing, and email-adapter tests proving tokens, cookies, and full Magic
  Link URLs are redacted.

Use unit tests for token/state rules, application tests with fake clock/random/email/Telegram ports,
and PostgreSQL integration tests for atomic consumption and uniqueness. Concurrency behavior must be
tested against the real persistence technology once designed, not only with in-memory fakes.

## 12. Trade-offs and recommendation

Magic Links are stronger than Telegram-only authentication for MintFlow's product direction: the
account remains usable on the Web, survives Telegram loss, avoids making a third-party chat identity
the financial ownership boundary, and gives users an understandable recovery route through email.
They also avoid password storage, password reset, credential stuffing, and password-strength UX.

They are not operationally free. Email becomes a security-critical dependency; delayed or filtered
messages block login, compromised mailboxes compromise MintFlow accounts, link scanners complicate
consumption, and support must diagnose delivery without weakening privacy. Links can be forwarded or
opened on another device, and rate limiting must prevent MintFlow from becoming an email-flooding
tool. Passwordless does not mean riskless.

Building this narrow design remains reasonable for the first 100 users because it needs only opaque
challenges, server-side sessions, one email adapter, and clear operational controls. It remains
reasonable only if production email authentication, monitoring, redaction, atomic persistence, and
security testing are treated as launch requirements rather than postponed polish.

A hosted authentication provider becomes justified when authentication scope expands to social
login, passkeys, MFA, native clients, enterprise federation, sophisticated abuse detection, account
recovery, multiple regions, or a support/incident burden the small team cannot safely operate. It is
also justified if measured email deliverability or security operations are unacceptable. The
replacement boundary should map the provider's verified subject to the existing User; it must not
move financial ownership into provider-specific models.

**Recommendation:** approve email-owned accounts with self-managed Magic Links, opaque server-side
sessions, and two-phase Web-confirmed Telegram linking for the MVP. Keep authentication as a bounded,
replaceable module and reassess outsourcing using observed operational cost rather than speculative
scale.

## 13. Approved MVP decisions and deployment prerequisites

The authentication architecture and persistence behavior are approved:

1. **Challenges:** Magic Links last 15 minutes; Telegram links last five minutes. Tokens are opaque,
   hash-only in storage, single-use, and consumed through atomic state changes.
2. **Login throttling:** reserve no more than three email delivery attempts per canonical email and
   accept no more than 30 login requests per network source per 15 minutes. Provider failure consumes
   a reserved delivery slot. Responses remain generic. Global protection is postponed.
3. **Sessions:** use server-side opaque sessions with 30-day absolute expiry, no sliding expiry, and
   no revocation of other sessions on ordinary login.
4. **Telegram:** use two-phase linking with final confirmation by the initiating Web session, one
   active Telegram account per User, and no automatic transfer. Deactivation immediately unlinks it,
   invalidates pending link challenges, and requires the full ceremony after any future reactivation.
5. **Recovery:** mailbox access is the only MVP recovery proof. Email change and support override are
   postponed.
6. **Retention:** retain unlinked TelegramConnection rows for 30 days and then delete them; do not
   pseudonymize them. Other approved authentication retention periods are defined by the persistence
   design.
7. **Account lifecycle:** MVP supports deactivation only. Hard deletion, anonymization, and future
   audit-correlation behavior are postponed to a separate policy. Foreign keys remain conservative.

Before production deployment, select the replaceable email provider and establish the sending domain,
SPF/DKIM/DMARC, bounce monitoring, alerts, incident ownership, HTTPS-only deployment, approved Web
origins, and backup policy. These are operational deployment prerequisites, not unresolved persistence
or authentication architecture decisions.
