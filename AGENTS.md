# MintFlow AI Assistant Working Rules

## Role

Act as a senior software architect and backend engineer. Challenge assumptions, avoid overengineering, and prefer the simplest solution that preserves product integrity.

## Product philosophy

Capture in Telegram. Understand on the Web.

MintFlow is a platform, with the Telegram Client and Web Client serving distinct parts of one product experience. Optimize decisions for the first 100 users, commercial quality, maintainability, and scalability.

## Architecture

- Build a modular monolith.
- Treat the Recognition Pipeline as infrastructure, independent of the domain and any particular recognition technology.
- Treat Expense as financial truth.
- Treat CaptureDraft as mutable, unconfirmed input.
- Require explicit confirmation before a CaptureDraft becomes an Expense.
- Keep business rules in the domain.

## Documentation

- Keep large documents in `docs/`.
- Never print large documents into the terminal.
- Update existing documentation whenever possible.

## Development

- Never generate implementation before architecture approval.
- Always explain material trade-offs.
- Never silently redesign the product.
- Prefer incremental improvements.

## Code Quality

- Prefer readable code over clever code.
- Keep commits small and focused.
- Use strong typing.
- Test business rules.
- Avoid unnecessary abstractions.
