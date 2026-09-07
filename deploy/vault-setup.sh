#!/usr/bin/env bash
# Install and configure HashiCorp Vault (Transit engine) on this host for C2PA
# signing-key custody. Run as root. This sets up a single-node Vault with file
# storage bound to localhost. See deploy/VAULT.md for the manual init/unseal steps
# (they must be done by a human — Vault prints the unseal keys + root token ONCE).
set -euo pipefail

echo "==> Installing Vault"
apt-get update -y
apt-get install -y gpg wget
wget -qO- https://apt.releases.hashicorp.com/gpg | gpg --dearmor -o /usr/share/keyrings/hashicorp-archive-keyring.gpg
echo "deb [signed-by=/usr/share/keyrings/hashicorp-archive-keyring.gpg] https://apt.releases.hashicorp.com $(lsb_release -cs) main" \
  > /etc/apt/sources.list.d/hashicorp.list
apt-get update -y && apt-get install -y vault

echo "==> Config (localhost only, file storage)"
install -d -o vault -g vault -m 0750 /opt/vault/data
cat > /etc/vault.d/vault.hcl <<'HCL'
ui = false
storage "file" { path = "/opt/vault/data" }
listener "tcp" {
  address     = "127.0.0.1:8200"
  tls_disable = true          # local-only; Apache/host firewall is the boundary
}
disable_mlock = false
HCL
chown vault:vault /etc/vault.d/vault.hcl
systemctl enable --now vault

cat <<'NEXT'

==> Vault installed and running on 127.0.0.1:8200.

Do these ONCE, as a human (the keys are shown only once — store them safely,
ideally split among trusted holders, NOT on this box):

  export VAULT_ADDR=http://127.0.0.1:8200
  vault operator init -key-shares=5 -key-threshold=3      # save the 5 unseal keys + root token
  vault operator unseal   (x3, with 3 different unseal keys)
  vault login <root-token>

  # enable Transit + create a policy + a token for the app:
  vault secrets enable transit
  vault write -f transit/keys/glassdb-c2pa type=ecdsa-p256 exportable=false
  vault policy write glassdb-c2pa - <<POL
  path "transit/sign/glassdb-c2pa"      { capabilities = ["update"] }
  path "transit/keys/glassdb-c2pa"      { capabilities = ["read"] }
  POL
  vault token create -policy=glassdb-c2pa -period=768h -orphan   # save this token for the app

Then provision the key + cert (see deploy/VAULT.md), set C2PA_SIGNER=vault and the
VAULT_* env in /opt/glassdatabase/.env, restart glassdb-glowtbook, and remove the
old data/c2pa/key.pem.
NEXT
