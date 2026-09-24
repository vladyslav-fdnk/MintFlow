# Operations Design: First Production Deployment (sprint `operations`)

Status: approved on 2026-09-24.

## 1. Purpose

Run MintFlow in production for a small first cohort: real sign-in email, HTTPS on MintFlow's own
domain, the Telegram webhook, scheduled jobs, encrypted backups with a rehearsed restore, basic
monitoring, and a runbook for deploy and rollback.

A staging environment, monitoring dashboards, correlation identifiers, incident and support
runbooks, and the controlled cohort release are left to the next sprint (the release milestone in
`mvp_definition.md`, section 11).

## 2. Settled inputs

Chosen by the product owner on 2026-09-24:

- One VPS running Docker Compose, recommended Hetzner Cloud in an EU region.
- Resend sends the sign-in email, over SMTP.
- A domain is still to be bought. This document writes `mintflow.example` for it.
- The scope is the first deployment only (section 1).

From the code:

- All state is in PostgreSQL, including receipt images (receipt design R3), so the app containers
  hold no data.
- Four long-running or scheduled processes exist: the web app, the receipt worker,
  `authentication_retention_cleanup`, and `telegram_retention_cleanup`, which also covers receipt
  retention. The FX sprint added `refresh_exchange_rates`.
- Only the Mailpit email adapter exists. Nothing registers the Telegram webhook, although
  `TelegramBotApi.set_webhook` exists.
- Login throttling reads the client address from `request.client`. Behind a reverse proxy, that
  address would be the proxy's, so every user would share one network-source limit (30 requests
  per 15 minutes). This must be fixed before production (O2).

## 3. Decisions

### O1. One server, one Compose file, images built by CI

- `deploy/compose.production.yaml` runs these services:
  - `caddy`: ports 80 and 443.
  - `app`: uvicorn.
  - `worker`: the receipt worker, from the same image.
  - `postgres`: PostgreSQL 17, with a named volume and no published port.
- CI builds the image on every push to `main` and pushes it to GitHub Container Registry, tagged
  with the commit SHA. The server never builds.
- **Deploying:** pull the tag, then run migrations as a one-off container, then run `up -d`.
- **Rolling back:** start the previous tag.
- **Rule from now on:** migrations must be backward compatible with the previous release, so a
  rollback never needs a downgrade. Destructive schema changes are split across two releases.
- The Dockerfile runs as a non-root user and gets a container health check on `/health/ready`.
- **Trade-off:** one server is a single point of failure. For the first 100 users, one restorable
  server is simpler than any redundancy we could operate.

### O2. HTTPS through Caddy, and the real client address

- Caddy obtains Let's Encrypt certificates, redirects HTTP to HTTPS, and sends HSTS.
- Only Caddy is reachable from outside. `app` listens on the internal Compose network.
- The application, not the server command, decides whom to believe: `MINTFLOW_TRUSTED_PROXIES`
  lists the addresses or networks of the proxy (Caddy's Compose network), and the app then adds
  uvicorn's `ProxyHeadersMiddleware` for exactly those as its outermost layer. uvicorn itself
  runs with `--no-proxy-headers`. With the list empty, forwarding headers are ignored as before.
  Login throttling then sees the real client address.
- Tests prove that a spoofed `X-Forwarded-For` from an untrusted peer is ignored, and that only
  the rightmost untrusted hop counts behind a trusted one.

### O3. A generic SMTP sender, configured for Resend

- `email_backend: "smtp"` adds these settings: host, port, TLS mode (implicit TLS or STARTTLS),
  username, password, and sender address.
- Resend is configured as `smtp.resend.com:465`, with username `resend` and the API key as the
  password. Changing provider is a configuration change, not a code change.
- The Mailpit backend stays for development. Messages and delivery errors never log the link or
  the recipient address in full (the existing redaction rules apply).
- The sending domain gets SPF, DKIM, and DMARC (`p=quarantine` to start), and the sender is
  `no-reply@mintflow.example`.
- At MVP volume, bounces and complaints are watched on the Resend dashboard. A bounce webhook is
  postponed.

### O4. Production settings fail fast

When `environment` is `production`, startup refuses a configuration where any of these holds:
- API docs are on.
- The Web origin is not `https://`.
- The email backend is not `smtp`.
- A Telegram setting is missing.
- The receipt recognizer is `fake`.

A mistake stops the deploy before users see it.

### O5. Scheduled jobs from the host's cron, with dead man's switches

- A versioned `deploy/crontab` runs each job with `docker compose run --rm app python -m ...`.
  There is no scheduler service and no new dependency.

  | Job | When (UTC) | Why |
  | --- | --- | --- |
  | `refresh_exchange_rates` | 15:30 and 06:00 daily | ECB publishes around 16:00 CET; NBU in the morning |
  | `authentication_retention_cleanup` | 03:10 daily | Retention periods are in days |
  | `telegram_retention_cleanup` | 03:20 daily | Drafts expire after seven days; receipt images after 30 |
  | `backup` (O7) | 02:30 daily | Before the cleanups |

- After each successful run the job pings its check on Healthchecks.io (free tier). A missed or
  failed run sends an alert email. The ping URLs are secrets in the server's environment file.

### O6. Telegram webhook registered on every deploy

- A new command, `register_telegram_webhook`, calls `setWebhook` with
  `<web origin>/telegram/webhook` and the configured secret token.
- It is idempotent and runs as a deploy step, after migrations. Local polling stays for
  development only.

### O7. Nightly encrypted backups, restore rehearsed

- `deploy/backup.sh` runs `pg_dump --format=custom` from a small tools container
  (`deploy/tools`: PostgreSQL 17 client, `age`, AWS CLI) on the internal network and encrypts the
  dump with `age` to a public key as it streams, so no unencrypted copy is written. The private key is kept offline by the owner,
  never on the server. The script uploads the file to S3-compatible object storage, recommended
  Hetzner Object Storage in the same region.
- A lifecycle rule deletes backups after 30 days. That period matches the retention statement in
  `authentication_persistence_design.md`: deleted data may live on in backups until they expire.
  After a full restore, both cleanup commands run before traffic returns. The authentication
  cleanup also deletes again every account listed in the `deleted_accounts` tombstones that the
  restore brought back (account deletion design, A5). An account deleted after the restored
  backup was taken is not in its tombstones: the restore note says to check for one by hand.
- **Targets:** at most 24 hours of lost data (RPO) and service back within 4 hours (RTO).
- `deploy/restore.sh` restores a chosen backup into a new database (never an existing one). The
  private key is piped into the tools container for that run only. The rehearsal restores the
  latest backup on a scratch server, runs the migrations check and the readiness endpoint, and
  compares row counts. It is recorded in the runbook with its date.
- Weekly Hetzner server snapshots are an extra layer, not a replacement.

### O8. Basic monitoring

- An external uptime check on `https://mintflow.example/health/ready` every minute, recommended
  UptimeRobot's free tier, alerts by email.
- Docker's `json-file` logging with rotation (`max-size` 10 MB, 5 files), so logs cannot fill the
  disk.
- A daily disk-space check pings Healthchecks.io only while usage is below 80%.
- Structured logs, error tracking, and dashboards are left to the next sprint.

### O9. Server access and secrets

- Ubuntu 24.04 LTS with unattended security upgrades.
- A non-root `deploy` user; SSH keys only, with password and root login disabled.
- The firewall allows only ports 22, 80, and 443.
- Secrets live only in `/srv/mintflow/.env` (mode 600), generated once with
  `secrets.token_urlsafe(32)` and never committed. `.env.example` documents every variable.
- GHCR pulls use a read-only token.

## 4. Manual steps for the owner

The runbook lists these with exact values:
1. Buy the domain and point its A record at the server.
2. Create the Hetzner server and object storage bucket.
3. Create a Resend account, verify the domain (SPF, DKIM, DMARC), and create an API key.
4. Create Healthchecks.io and UptimeRobot accounts.
5. Generate the `age` key pair and keep the private key offline.
6. Set the production bot token in BotFather.
7. Create the Azure F0 resource, if RCPT-07 is not done by then; otherwise receipts use manual
   fallback only.

## 5. Out of scope

- Staging, dashboards, correlation identifiers, error tracking, and incident and support
  runbooks (next sprint).
- High availability, multiple servers, managed PostgreSQL, and zero-downtime deploys.
- Bounce webhooks and email analytics.
- Account deletion (separate sprint).

## 6. Task breakdown

| Task | Summary | Depends on |
| --- | --- | --- |
| OPS-01 | Generic SMTP sender (Resend), production settings validation, trusted proxy headers | — |
| OPS-02 | Production image (non-root, health check), `compose.production.yaml`, Caddyfile, CI image push to GHCR | OPS-01 |
| OPS-03 | `register_telegram_webhook` command, `deploy/crontab`, Healthchecks.io pings, log rotation | OPS-02 |
| OPS-04 | `backup.sh` and `restore.sh` with `age` encryption, restore rehearsal procedure | OPS-02 |
| OPS-05 | Runbook: server setup, first deploy, deploy, rollback, restore, manual steps; first real deploy | OPS-03, OPS-04 |

## 7. New external services (approved on 2026-09-24)

| Service | Purpose | Cost at MVP scale |
| --- | --- | --- |
| Hetzner Cloud VPS (CX22 or similar) | Runs everything | About €5 a month |
| Hetzner Object Storage | Encrypted backups | About €5 a month (minimum) |
| Resend | Sign-in email | Free up to 3,000 emails a month |
| GitHub Container Registry | Images | Free for this use |
| Healthchecks.io | Missed-job and backup alerts | Free tier |
| UptimeRobot | Uptime alerts | Free tier |
| Let's Encrypt (through Caddy) | TLS certificates | Free |
| Domain registrar | The domain | About €10–15 a year |
