# Contributing

Thanks for helping build the Glass Database. Contributions of code, data
corrections, ontology terms, and documentation are all welcome.

## Getting set up

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
make seed          # synthetic demo database
make test          # run the suite (installs pytest/moto/boto3 if needed)
```

For the media features, install the optional tools: `ffmpeg` (video),
`c2pa-python` + `cryptography` (Content Credentials), and set the `MINIO_*`
env vars for object storage. The code degrades gracefully without them.

## Ground rules

- **Never commit real or private data.** The built database and any file under
  `data/` are git-ignored for a reason — public tables still physically contain
  private columns (emails, phones, claim tokens). Use `make seed` or your own
  local ingest.
- **Never commit secrets** — `.env`, `.htpasswd`, `secrets.toml`, or the C2PA
  signing key under `data/c2pa/`.
- Keep pieces **generic**: the API and explorer read the `_datasets`/`_columns`
  registry rather than hard-coding tables. Add data by extending the registry,
  not by special-casing endpoints.
- Add a test for new behaviour. The suite is fast and runs in CI on every push.
- Be honest about maturity in docs — mark things "built", "needs live test", or
  "next" so the distinction between a proof-of-concept and production is clear.

## Pull requests

1. Fork and branch from `main`.
2. `make test` (and `make lint`) should pass.
3. Describe what changed and why; link any related issue.
4. Data/ontology contributions are licensed CC-BY-4.0; code is Apache-2.0.

## Reporting problems

Open an issue, or for anything security- or privacy-sensitive see
[`SECURITY.md`](SECURITY.md).
