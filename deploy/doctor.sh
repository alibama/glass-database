#!/usr/bin/env bash
# Glass Database — doctor. Run:  sudo bash deploy/doctor.sh
# Prints a report that localizes a problem to the service / proxy / TLS layer.
echo "==================== SERVICES ===================="
for s in api admin explore glowtbook; do
    printf "  glassdb-%-9s : %s\n" "$s" "$(systemctl is-active glassdb-$s 2>/dev/null || echo 'missing')"
done

echo
echo "============ LOCAL HEALTH (bypasses Apache) ======"
check() { # name port path
    code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "http://127.0.0.1:$2$3" 2>/dev/null)
    printf "  %-9s 127.0.0.1:%s%-28s -> %s\n" "$1" "$2" "$3" "${code:-no-answer}"
}
check api      8000 /datasets
check admin    8501 /admin/_stcore/health
check explore  8502 /explore/_stcore/health
check glowtbook 8503 /glowtbook/_stcore/health

echo
echo "===== STREAMLIT LOGS (last 25 lines; look for tracebacks) ====="
for s in explore glowtbook; do
    echo "----- glassdb-$s -----"
    journalctl -u glassdb-$s -n 25 --no-pager 2>/dev/null | tail -25
done

echo
echo "==================== APACHE ======================"
apache2ctl configtest 2>&1
echo "-- vhosts mentioning glassdatabase --"
apache2ctl -S 2>&1 | grep -i glassdatabase || echo "  (none — vhost not loaded?)"

echo
echo "================ LISTENING PORTS ================="
ss -ltnp 2>/dev/null | grep -E ':(80|443|8000|8501|8502|8503)\b' || echo "  (ss found nothing)"

echo
echo "==================== TLS / DNS ==================="
if command -v certbot >/dev/null; then
    certbot certificates 2>/dev/null | grep -E "Certificate Name|Domains|Expiry Date" || echo "  (no certs issued yet)"
else
    echo "  certbot not installed"
fi
echo -n "  DNS  glassdatabase.org -> "; dig +short glassdatabase.org 2>/dev/null | tr '\n' ' '; echo
echo -n "  this box public IP     -> "; curl -s --max-time 5 https://api.ipify.org 2>/dev/null || echo "(couldn't fetch)"; echo
echo
echo "Done. Paste this whole report back for a diagnosis."
