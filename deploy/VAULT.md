# Signing key custody with HashiCorp Vault (Transit)

Move the C2PA signing key off the app host so the private key is **non-exportable**
and never touches the Streamlit process. The certificate (public) stays on disk;
only signing moves to Vault.

## 1. Install & init
```bash
sudo bash deploy/vault-setup.sh          # installs Vault, localhost listener, file storage
```
Then, as a human (unseal keys are shown ONCE — store them off-box, split among
trusted holders):
```bash
export VAULT_ADDR=http://127.0.0.1:8200
vault operator init -key-shares=5 -key-threshold=3     # save 5 unseal keys + root token
vault operator unseal            # x3 with different keys
vault login <root-token>
vault secrets enable transit
vault write -f transit/keys/glassdb-c2pa type=ecdsa-p256 exportable=false
vault policy write glassdb-c2pa - <<'POL'
path "transit/sign/glassdb-c2pa"        { capabilities = ["update"] }
path "transit/keys/glassdb-c2pa"        { capabilities = ["read"] }
POL
vault token create -policy=glassdb-c2pa -period=768h -orphan   # save this app token
```

## 2. Provision the certificate for the Vault key
Reads Vault's public key, builds a self-signed test cert whose public key **is** the
Vault key, signs the cert via Vault, and writes it — so cert and key are guaranteed
to match (a mismatch is what causes `COSE signature invalid` at sign time):
```bash
VAULT_ADDR=http://127.0.0.1:8200 VAULT_TOKEN=<app-token> \
python /opt/glassdatabase/deploy/vault_provision.py --org "<Your Legal Org>"
```
(For production, generate a CSR for the Vault key and have the C2PA Trust-List CA
issue the cert instead of self-signing.)

## 3. Point the app at Vault
In `/opt/glassdatabase/.env`:
```
C2PA_SIGNER=vault
VAULT_ADDR=http://127.0.0.1:8200
VAULT_TOKEN=<app-token>
VAULT_TRANSIT_KEY=glassdb-c2pa
# optional but recommended — RFC 3161 time-stamp (see C2PA-READINESS.md):
# C2PA_TSA_URL=http://timestamp.digicert.com
```
Then remove the old on-disk key and restart:
```bash
rm -f /opt/glassdatabase/data/c2pa/key.pem
sudo systemctl restart glassdb-glowtbook
```
Signing now runs in Vault; the app has only the public cert. If Vault is sealed or
unreachable, signing fails closed (it does not silently fall back to a disk key
once `C2PA_SIGNER=vault` and no key.pem is present).

## Operational notes
- **Unsealing** is required after every Vault restart (that's the point). Automate
  carefully (auto-unseal via a cloud KMS) only if you accept the trade-off; manual
  unseal is the most secure.
- **Token renewal:** the app token is periodic (768h); renew it before expiry or
  use a longer-lived AppRole for the service.
- **Rotation:** `vault write -f transit/keys/glassdb-c2pa/rotate` creates a new key
  version; re-issue the cert for the new public key.
- **Assurance:** a Vault-held key satisfies "non-exportable software key." For a
  FIPS-140 hardware level, back Vault with an HSM seal or use a cloud HSM KMS.

## Troubleshooting

- **`connection refused` on 8200** — Vault isn't running. `systemctl status vault` +
  `journalctl -u vault -n 50`.
- **Killed on start (`signal=KILL`, restart loop)** — almost always the kernel
  **OOM-killer** on a memory-tight box. Confirm: `journalctl -k | grep -i oom`,
  `free -h`. Fixes: set `disable_mlock = true` in `/etc/vault.d/vault.hcl`, add swap
  (`fallocate -l 2G /swapfile …`), then `systemctl reset-failed vault && systemctl
  restart vault`. If the host is genuinely out of RAM (WordPress + MySQL + Streamlit
  + Apache already resident), a **managed cloud KMS** (AWS/GCP, ~$1/mo, no local
  process) or a larger instance is the better home for the signing key than
  self-hosted Vault.
- **Re-seals on every restart/reboot** — unseal (`vault operator unseal` ×3). With
  `C2PA_SIGNER=vault` and no local key, signing stays broken until unsealed — plan a
  reboot runbook or keep the local-key signer for the test phase.
