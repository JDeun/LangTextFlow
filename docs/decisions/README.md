# Architecture Decision Records (ADR)

ADRs capture **accepted or proposed technical decisions**, distinct from research notes and implementation tasks. A research document supplies evidence; an ADR records a choice and its consequences. The [research index](../research/README.md) is the evidence entrypoint.

## Index

No formal ADR has been accepted in this directory yet. Do not retroactively label existing architecture documents as approved decisions without review.

## Creating an ADR

1. Copy [0000-template.md](0000-template.md) to the next unused four-digit filename, e.g. `0001-short-title.md`.
2. Record status `proposed` initially. Use `accepted` only after the decision is approved in a linked PR; use `superseded` with a successor link when replaced.
3. Include context, considered alternatives, measurable constraints, trade-offs, validation gates, security/privacy/license implications and rollback.
4. Link the relevant issue, research evidence and implementation PR. Update this index in the same PR.
5. Preserve prior ADR history; supersede rather than silently rewrite an accepted decision.
