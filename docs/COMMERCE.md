# Selling it: licence, credits and subscription

The product is the **local** FL-MCP server. A customer installs it on the computer that runs FL Studio. The only thing hosted is a small licence service that knows who has paid and how many credits are left. Controlling FL never goes through it.

## What is free and what uses credits

| Free, offline, no licence | Uses credits (default price) |
|---|---|
| `fl_status` `fl_list` `fl_transport` `fl_set` `fl_steps` `fl_notes` `fl_param` `fl_call` `fl_api` `fl_library` `fl_ui` `fl_audio` `fl_desk` | new song plan: `fl_produce` with a genre (5) |
| `fl_produce` with `genres`, `show`, `queue`, `apply`, `midi`, `inspect` on a plan you have | project file: `fl_produce` writing a `.flp` (3, on top of the plan) |
| `fl_mix` `meter`, `doctor`, `chain` | mix balancing: `fl_mix balance` (2) |

Reasoning: the credits meter the finished work (a plan, a project, a balanced mix), not compute, because the compute is the customer's own. Measuring (`fl_audio`), plugin control, sound search and the desktop tier cost the service nothing, so they stay free; they are what makes the metered tools worth buying. Re-delivering a plan you already paid for is free. A full project is 8 credits.

The numbers above and below are **placeholders** from `service/flmcp_service/plans.py`. Change them in a JSON file (`FLMCP_CONFIG`) without touching code.

| Plan | Credits | Seats | Notes |
|---|---|---|---|
| trial | 15, once, 30 days | 1 | no renewal, no grace |
| pro | 300 per month | 2 | the allowance expires at the end of the period |
| studio | 1200 per month | 5 | |
| top-up packs | 100, 500 | n/a | do not expire (set `topup_days` to change) |

At 8 credits per finished project, 300 credits are about 37 projects a month.

## Customer journey

1. The customer buys a plan on your site (a payment provider's hosted checkout).
2. The provider sends a webhook; the service creates the account and a licence key (`FLMCP-XXXXX-XXXXX-XXXXX-XXXXX`) and queues it for e-mail (`outbox`).
3. The customer installs FL-MCP (see README), then runs `fl-mcp login` and pastes the key. With the desktop extension the key is asked for once at install time and passed in as `FL_MCP_LICENSE`.
4. A tool that uses credits reserves them, does the work, and settles. The balance is shown by `fl_status` and `fl-mcp account`.
5. Each month the provider's renewal webhook grants the next allowance. Running low, the tool answers with the top-up link.
6. A new computer: `fl-mcp login` again. If all seats are used, `fl-mcp login KEY --replace "old pc"` frees one.

## Where things run

```
customer computer                        your server                     payment provider
  MCP client                              licence service (HTTPS)          checkout, invoices
  fl-mcp  -- reserve/settle, login -->    ledger (SQLite or Postgres)  <-- signed webhooks
  FL Studio
```

What leaves the computer: the licence key (once, at login), a random device id, the computer's host name, and the names of the operations used (`produce.compose`). Never project data, notes, audio, file names or paths. The device token is stored in `~/.fl-mcp/account.json` readable by the user only.

## Credit semantics

- A tool **reserves** its price before it works: the credits leave the balance at once, so two parallel calls cannot overspend.
- It **settles** afterwards for what was delivered. A failure costs nothing; a call that produced the plan but could not write the project is charged for the plan only.
- A reservation that is never settled (crash, lost connection) is returned automatically after 10 minutes.
- Retries are safe: every reservation carries an idempotency key, so a lost answer never charges twice. Grants from webhooks are idempotent per event and per billing period.
- Credits sit in **lots** (monthly allowance, top-up, refund). The lot that expires first is used first. Credits returned for a lot that expired meanwhile arrive as a new 30-day lot.
- Every movement is a ledger entry (`grant`, `hold`, `release`, `expire`); the balance is always their sum. A test checks this after random sequences of operations, and mutations of the ledger code are caught by it.

## Licence service

Reference implementation in `service/` (FastAPI, SQLite, about 1000 lines). Run it:

```bash
uv pip install -e ".[service]"
```

```bash
FLMCP_DB=/data/flmcp.db FLMCP_ADMIN_TOKEN=... FLMCP_WEBHOOK_SECRETS=generic=... \
FLMCP_TOPUP_URL=https://your-site/pricing uvicorn flmcp_service.app:create_app --factory --port 8080
```

Put it behind HTTPS (the client refuses plain http except for localhost) and a reverse proxy that rate-limits and caps request bodies (a few KB is plenty). Back up the database file. For high volume replace the `DB` class with Postgres; the other modules only need a connection with `execute()`.

| Setting | Meaning |
|---|---|
| `FLMCP_DB` | SQLite file (durable disk, not an ephemeral one) |
| `FLMCP_ADMIN_TOKEN` | enables the admin API; empty means it does not exist |
| `FLMCP_WEBHOOK_SECRETS` | `name=secret,...`: one entry per payment provider |
| `FLMCP_CONFIG` | JSON with `costs`, `plans`, `packs`, `topup_days`, `refund_days`, `hold_seconds` |
| `FLMCP_TOPUP_URL` | shown to the customer when credits run out |

Do not host it on a free tier that sleeps or has an ephemeral disk (the CrownCode backend on a free Hugging Face Space is such a place). A licence server must be up and keep its data.

Back-office commands: `python -m flmcp_service.admin create-license --email a@b.c --plan pro`, `grant`, `show`, `outbox` (prints licence keys waiting to be e-mailed, then erases them).

### API (version 1, JSON, errors as `application/problem+json`)

| Endpoint | Auth | Purpose |
|---|---|---|
| `POST /v1/activate` | licence key in body | sign a device in; returns a device token, plan, balance, prices. 409 `device_limit` lists the seats in use; `replace` frees one |
| `GET /v1/account` | device token | plan, balance, prices, devices, top-up link |
| `POST /v1/credits/reserve` | device token | `{ops, idem}`; 402 `insufficient_credits` carries `cost`, `balance`, `topup_url` |
| `POST /v1/credits/{id}/settle` | device token | `{done}`; idempotent |
| `POST /v1/devices/self/revoke` | device token | sign out and free the seat |
| `POST /v1/webhooks/{provider}` | HMAC signature | billing events |
| `/v1/admin/*` | admin token | issue licences, grant credits, look up an account |

Keys and device tokens are stored only as SHA-256 hashes (they have at least 95 and 256 bits of entropy). Secrets are compared in constant time.

### Billing events

A thin adapter turns the provider's payload into one of these and signs the body `X-FLMCP-Signature: t=<unix>,v1=<hex HMAC-SHA256 of "t.body">` (5 minute tolerance):

| Event | Fields | Effect |
|---|---|---|
| `subscription.started` | id, email, plan, ref, period_start, period_end | account + licence + first allowance |
| `subscription.renewed` | id, ref, period_start, period_end | active again, allowance for the new period |
| `subscription.plan_changed` | id, ref, plan | takes effect for the next allowance |
| `subscription.canceled` | id, ref | works until `period_end` |
| `subscription.past_due` | id, ref | tools refuse until renewed |
| `subscription.expired` | id, ref | licence revoked |
| `topup.paid` | id, email, pack | adds the pack |

Events carry an id and are applied once. An event for an unknown subscription answers 409 so the provider retries it later.

Typical provider events to map (check the exact names and fields in your provider's sandbox before going live): Paddle Billing `subscription.created`, `subscription.updated`, `subscription.canceled`, `subscription.past_due`, `transaction.completed`; Lemon Squeezy `subscription_created`, `subscription_updated`, `subscription_payment_success`, `subscription_payment_failed`, `subscription_cancelled`, `subscription_expired`, `order_created`; Stripe `checkout.session.completed`, `invoice.paid`, `invoice.payment_failed`, `customer.subscription.updated`, `customer.subscription.deleted`. The adapters are not written here because they cannot be tested without your provider account.

## Enforcement, honestly

The client is Python on the customer's machine; anyone can read it and remove the check. What the design gives you: a real ledger with fair-use limits per seat, a licence that ends when the payment ends, no free riding for honest users, and no fragile tricks. What really protects revenue is value that stays on your side and value that keeps coming: updates, new genres and presets, support, and (if you want hard enforcement) moving the composition engine behind the service so the client only receives finished plans. `music.compose` is a pure function, so that move is small. It is not done because the product is local-first.

## Build and ship

```bash
python packaging/build_mcpb.py --service https://licence.example.com
```

writes `dist/fl-mcp-<version>.mcpb` (reproducible; the address is baked in). Without `--service` the bundle is unmetered. For other clients publish the wheel (`uv tool install fl-mcp`, `uvx fl-mcp`) built with the same `issuer.json`. The `.mcpb` follows manifest 0.4 with the `uv` runtime and a sensitive `license_key` setting; it was checked against the specification and by starting the unpacked bundle, but **not** installed in Claude Desktop, so install it once yourself before announcing it.

## Decisions that are yours

- Prices, credit costs, plan sizes, trial length, whether monthly credits roll over.
- Payment provider. A merchant of record (it sells to your customers and handles VAT or sales tax) is the lowest-effort route for a solo seller; check which providers pay out in your country.
- Legal: terms of use, privacy notice (the service stores e-mail, device names and a usage ledger), refund policy, and the rules for expiring prepaid credits in your customers' countries. Ask a lawyer.
- Open source or not. The repository is MIT. A commercial build can live in a private fork, or the client can stay open while the service, presets and support are what customers pay for.
- Where to host the service, and how licence e-mails are sent (provider e-mail, or a transactional e-mail API fed from the outbox).
