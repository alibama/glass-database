# Security Policy

## Reporting a vulnerability

Please report security or privacy issues **privately** rather than opening a
public issue. Email the maintainer (see the repository profile / commit history)
with a description, reproduction steps, and impact. You'll get an acknowledgement
and a fix timeline.

Especially sensitive here:

- **Private-data exposure** — public API/explorer responses must never include
  columns marked `is_public = 0` (emails, phones, addresses, claim tokens,
  internal notes) or rows from `restricted` datasets.
- **The C2PA signing key** under `data/c2pa/key.pem` must never be committed or
  served. It is git-ignored and file-mode `600`.
- **Contribution abuse** — the moderation queue is the gate before anything is
  public; report ways to bypass it.

## Supported

This is an actively developed project; fixes land on `main`.
