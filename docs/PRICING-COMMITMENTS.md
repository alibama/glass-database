# Confidential-but-assertable pricing — design note (prototype)

**The problem you posed:** keep prices private, but be able to *assert* them when it
matters — to an insurer or a tax authority — without publishing them. That's a real,
well-shaped cryptography problem, and it splits into three levels of ambition. The
prototype (`central/price_commit.py`) implements levels 1 and 2.

## The key idea: commit now, reveal later
You don't need to publish a price to make it *provable*. You publish a **commitment**
— a value that (a) **hides** the price and (b) **binds** you to it (you can't change
the number afterwards). The commitment can be signed into the piece's record / C2PA
credential right alongside the public provenance, while the price itself stays with
you. When you need to assert the price, you **reveal** the opening, and the other
party checks it against the commitment you signed earlier.

This is *not* encryption-with-a-shared-key (that just moves the secret); the point is
that the commitment is **public and tamper-evident**, so a revealed price is provably
the one you locked in at the time — you can't inflate it for an insurance claim or
deflate it for taxes after the fact.

## Level 1 — Hash commitment (single price, selective disclosure) ✅ built
`commit = SHA-256(value | currency | salt)`. Sign the commitment; keep `(value, salt)`
private. To assert the price to your insurer, hand them `(value, salt)`; they verify
it matches. Reveals nothing until opened. *This is the 80/20 for "confidential price,
assertable on demand."*

## Level 2 — Pedersen commitment (prove a TOTAL, hide the parts) ✅ built
`C(m,r) = g^m·h^r`. These are **additively homomorphic**: the product of per-piece
commitments equals a commitment to the sum. So you can publish one commitment per
piece and later prove your **portfolio total** (an insurance floor, a declared estate
or inventory value) by revealing only the *total* and the *total blinding factor* —
never the individual prices. The prototype demos a 3-piece portfolio proving a
$20,000 total while each piece's price stays hidden.

## Level 3 — Zero-knowledge range proofs (prove a property, reveal nothing) — not built
The fully-ZK version: prove "this piece is worth **more than $X**" or "my collection
totals **between $A and $B**" while revealing *nothing* — not the price, not even the
total. That needs a **range proof** (Bulletproofs) over the Pedersen commitments,
which means pulling in a ZK library (e.g. a Rust `bulletproofs` crate via bindings, or
`py_ecc`/`petlib` for a slower pure-Python version). Worth it only if an
insurer/authority will accept "≥ threshold" *without* a number — which is the genuinely
novel ask. I'd prototype this next if the use case wants it.

## How it plugs into what you already have
- The **commitment** becomes a public assertion — `price_commit.assertion(...)` returns
  an `org.glassdatabase.price-commitment` assertion that signs into the C2PA credential
  next to the provenance. The `value_display` / value fields stay **private** (they
  already are).
- The **opening** `(value, salt)` — or, for a total, `(total, total_r)` — is what you
  hand to the insurer/authority out of band (a file, a QR, a link with a key). They
  verify against the signed commitment. That's your "share a key to make the data
  assertable" — except the "key" is the opening, and it proves the exact value.

## Honest limits (so the prototype isn't oversold)
- A commitment proves *what you committed*, not that the price is **correct or fair** —
  that's an appraiser's job. Commitments make appraisals **tamper-evident and
  timestamped**, not authoritative.
- Level 2's total proof reveals the **sum**; it hides the parts, not the aggregate.
- The prototype's Pedersen `h` is hashed-to-group (unknown discrete log) for hiding;
  a production system should use a documented, verifiable `h` and, ideally, an
  audited library rather than this teaching implementation.
- None of this is legal/tax advice; whether an authority *accepts* a commitment or a
  ZK proof is a policy question, not a crypto one.

## Refined use cases worth prototyping next
1. **Insurance floor without a number** — a Level-3 range proof that your collection is
   worth ≥ the insured amount, so you never disclose piece prices to the carrier.
2. **Sale-price attestation for tax** — a Level-1 commitment made at sale time, signed
   into the object's provenance, revealed to the authority on request (proves the
   declared price is the pre-committed one).
3. **Escrow / consignment** — commit the agreed price; reveal to the two parties only.
