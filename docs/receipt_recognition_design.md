# Receipt Recognition Design (sprint `receipt-recognition`)

Status: approved on 2026-09-23. RCPT-07 still needs the final provider choice (R1).

## 1. Purpose

Deliver the receipt journey from docs/mvp_definition.md (sections 3, 5, 7): the user sends one
receipt photo in Telegram, MintFlow acknowledges it immediately, recognizes merchant, date,
currency, and total in the background, and presents the same review card as manual capture.
Nothing is saved until the user confirms.

## 2. Settled inputs

These are already decided and are not reopened here:

- Exactly four recognized fields: merchant, date, currency, total. Recognition never assigns a
  category; Uncategorized is proposed (MVP section 7).
- Partial results are normal. Incorrect confident values are worse than missing ones; each
  value needs enough confidence to decide whether to prefill it.
- User edits always win: a result may fill only blank or defaulted fields, never user-supplied
  ones (domain design, "Manual correction before delayed recognition").
- One receipt supports one draft, which creates at most one Expense (decision 16).
- A failure or timeout leaves a usable manual draft; the user never has to resend the photo.
- Recognition results are kept 30 days after the draft is confirmed, cancelled, or expired
  (decision 10). The Expense keeps only its final values and an optional receipt id.
- A user may later remove a receipt image without deleting the Expense (decision 8), so an
  Expense never depends on the image existing.
- The recognition pipeline is infrastructure behind a provider-independent boundary; provider
  types never enter the domain.
- Receipt contents never appear in logs.

## 3. Decisions

### R1. Recognition provider — must be free; final choice after evaluation

The pipeline is built against a `ReceiptRecognizer` protocol with a fake implementation, so
every task except RCPT-07 proceeds without this choice. Candidates:

| Option | Strengths | Weaknesses |
| --- | --- | --- |
| Receipt-specific document API (for example AWS Textract AnalyzeExpense, Azure Document Intelligence prebuilt receipt, Google Document AI expense parser) | Returns total, vendor, and date as typed fields with per-field confidence scores, which map directly onto "prefill only when confident" | Per-page cost; locale and currency coverage differ by vendor; currency often has to be inferred separately |
| Multimodal LLM with structured output | Handles many languages and layouts; can report currency evidence explicitly | No calibrated confidence: MintFlow must ask for evidence and apply its own acceptance rules; per-image cost; output needs strict validation |
| Self-hosted OCR (for example Tesseract) plus MintFlow parsing | No external service or per-image cost | Much lower accuracy on photos; MintFlow owns all total/date/currency heuristics |

Constraint set on approval: the provider must be free. That rules out paid per-page APIs and
leaves these candidates, to be compared in RCPT-07 on a small set of consented real receipts:

| Free candidate | Notes |
| --- | --- |
| Self-hosted open-source OCR (PaddleOCR or Tesseract) with MintFlow's own field extraction | Free without limits and private: images never leave MintFlow. Lower accuracy on photos; MintFlow owns total, date, and currency heuristics (RCPT-03 rules apply either way). Adds a model dependency to the worker image. |
| Azure Document Intelligence prebuilt receipt, free tier | Typed fields with confidence; the free tier is limited per month and needs an Azure account and credentials; receipts leave MintFlow under Microsoft's processing terms. |
| Google Cloud Vision text detection, free monthly quota | OCR text only, so MintFlow still does field extraction; quota-limited; needs a Google Cloud account. |

Free consumer tiers of LLM APIs are excluded: their terms can allow using submitted content to
improve models, which is not acceptable for receipts. The comparison counts, per field,
correct, missing, and confidently wrong values; the fewest confidently wrong values wins, then
coverage.

Decision (2026-09-23, product owner): Azure AI Document Intelligence, prebuilt receipt model, on
the free F0 tier — the most widely used managed option, with typed fields and confidences. It is
called through its REST API with httpx (no SDK dependency). Currency is taken only from what the
printed total shows, never from Azure's locale-based `currencyCode`. WebP is not supported by
Azure and falls back to manual entry. Receipts are sent to Microsoft for analysis under Azure's
data-processing terms. The evaluation on consented receipts confirms the choice before it is
enabled for users.

### R2. Background processing uses a PostgreSQL work queue

- The `receipts` table is the queue: a receipt waits in `queued`; a worker claims one with
  `FOR UPDATE SKIP LOCKED`, marks it `processing` with an attempt id, and finishes it as
  `recognized` or `recognition_failed`. A stale attempt (worker crash) is re-queued after a
  lease expires.
- A new process runs `python -m mintflow.commands.receipt_worker`.
- Trade-off: no Redis, Celery, or other new infrastructure; throughput is far beyond the first
  100 users. A dedicated queue can replace it later behind the same use cases.

### R3. Receipt images are stored in PostgreSQL for now

- A separate `receipt_images` table holds the bytes (at most 10 MiB, JPEG, PNG, or WebP), so
  ordinary queries never load them and deleting an image is deleting one row.
- Behind a `ReceiptImageStore` protocol, so object storage can replace it without touching the
  domain or the flow.
- Trade-off: the database and its backups grow with images; at MVP volumes this is small, and it
  avoids a new external storage service and its credentials.

### R4. Images follow the recognition-result retention

- An image and its recognition results are deleted 30 days after the draft is confirmed,
  cancelled, or expired. The Expense keeps its `receipt_id`; the receipt row records that the
  image was removed.
- Trade-off: the Web cannot show images older than 30 days. Showing receipt images on the Web and
  user-initiated image deletion are Should Have items and are not in this sprint.

### R5. Intake in Telegram

- Accepted: one photo (the largest size Telegram offers) or one image document of an accepted
  type, up to 10 MiB. Albums, PDFs, and other files get a clear instruction and create nothing.
- The webhook stores only Telegram's file id, creates the receipt (`queued`) and a
  `TELEGRAM_RECEIPT` draft in `awaiting_recognition`, and acknowledges ("Got it, reading your
  receipt…") — well within the two-second target. The worker downloads the file.
- A photo while another draft is active gets the same Continue / Discard choice as `/add`.

### R6. Applying a result

- The recognizer returns candidates per field with a confidence and evidence kind. A
  provider-independent selection step (RCPT-03) keeps a value only above a per-field threshold
  and only when it is unambiguous (for example, currency only from an explicit code, an
  unambiguous symbol with context, or supported locale evidence).
- Applying fills only fields that are empty or `default`, records `recognition` provenance and
  the result id, proposes Uncategorized, and moves the draft to review. The bot then sends the
  review card, with recognized values marked "(from receipt)".
- If total or currency is still missing, the bot asks for exactly those, as in manual capture.

### R7. Delays and failures

- After 30 seconds without a result the bot says it is taking longer and offers "Enter
  manually". Choosing it turns the draft into a manual draft at once; a late result may still
  fill untouched fields.
- A failed or timed-out recognition turns the draft into a manual draft with a short
  explanation. Explicit retry is not offered in this sprint.
- A worker attempt times out after 60 seconds and is recorded as failed.

## 4. Out of scope

- Showing receipt images on the Web, and user-initiated image deletion (Should Have).
- Line items, taxes, tips, split receipts, multiple receipts per draft, PDFs, albums.
- Category prediction.
- Storing raw provider responses.
- Web upload of receipts.

## 5. Task breakdown

| Task | Summary | Depends on |
| --- | --- | --- |
| RCPT-01 | Domain: Receipt lifecycle, RecognitionResult, draft recognition provenance and receipt link | — |
| RCPT-02 | Persistence: receipts (queue), receipt images, recognition results, draft receipt link | RCPT-01 |
| RCPT-03 | Recognition boundary: recognizer protocol, candidate selection rules, fake recognizer | RCPT-01 |
| RCPT-04 | Telegram intake: photos and image documents, validation, queued receipt, acknowledgement | RCPT-02 |
| RCPT-05 | Receipt worker: claim, download, store, recognize, apply, notify; leases and failures | RCPT-02, RCPT-03, RCPT-04 |
| RCPT-06 | Delay message and "Enter manually"; late results respect user edits | RCPT-05 |
| RCPT-07 | Free provider evaluation and adapter (final choice by a human, R1) | RCPT-03 |
| RCPT-08 | Retention of images and recognition results | RCPT-02 |
