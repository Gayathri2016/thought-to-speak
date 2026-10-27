#!/usr/bin/env bash
# Thought → Speak — one-time setup of the free beta on an Oracle Cloud "Always Free" Ubuntu server.
#
# Run on the server (Ubuntu 22.04 / 24.04, ARM or AMD), as the default "ubuntu" user:
#   curl -fsSL https://raw.githubusercontent.com/Gayathri2016/thought-to-speak/main/deploy/oracle/setup.sh -o setup.sh
#   sudo DOMAIN=yourname.duckdns.org bash setup.sh
#
# Optional settings (environment variables):
#   DOMAIN            the web address people will use (required) — e.g. thoughttospeak.duckdns.org
#   DUCKDNS_TOKEN     your DuckDNS token: keeps the DuckDNS address pointed at this server's IP
#   REPO              git repository to deploy (default: the public GitHub repo)
#   BRANCH            branch to deploy (default: main)
#
# Safe to run again: it updates the code and keeps your settings and data.
set -euo pipefail

DOMAIN="${DOMAIN:-}"
REPO="${REPO:-https://github.com/Gayathri2016/thought-to-speak.git}"
BRANCH="${BRANCH:-main}"
DUCKDNS_TOKEN="${DUCKDNS_TOKEN:-}"
ROOT=/opt/thought-to-speak
APP="$ROOT/app"
DATA=/var/lib/thought-to-speak
ENV_FILE=/etc/thought-to-speak/env

say() { printf '\n\033[1;32m==> %s\033[0m\n' "$*"; }
die() { printf '\n\033[1;31mError: %s\033[0m\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "run with sudo: sudo DOMAIN=yourname.duckdns.org bash setup.sh"
[ -n "$DOMAIN" ] || die "set DOMAIN, e.g.: sudo DOMAIN=yourname.duckdns.org bash setup.sh"
case "$DOMAIN" in *[!a-zA-Z0-9.-]*|.*|*.) die "DOMAIN looks wrong: '$DOMAIN'";; esac

say "Installing system packages"
export DEBIAN_FRONTEND=noninteractive
# keep the current firewall rules when iptables-persistent installs (no interactive prompt)
echo "iptables-persistent iptables-persistent/autosave_v4 boolean true" | debconf-set-selections
echo "iptables-persistent iptables-persistent/autosave_v6 boolean true" | debconf-set-selections
apt-get update -y
apt-get install -y python3 python3-venv python3-pip git curl gnupg debian-keyring debian-archive-keyring \
  apt-transport-https iptables-persistent ca-certificates

if ! command -v caddy >/dev/null; then
  say "Installing Caddy (automatic HTTPS)"
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -y
  apt-get install -y caddy
fi

# Small servers (the 1 GB AMD micro shape): add swap so installs and analysis never run out of memory.
MEM_MB=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)
if [ "$MEM_MB" -lt 2000 ] && ! swapon --show | grep -q .; then
  say "Adding a 2 GB swap file (this server has ${MEM_MB} MB of memory)"
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

say "Opening ports 80 and 443 in the server firewall"
# Oracle's Ubuntu images only allow SSH; insert the web ports before their final REJECT rule.
for port in 80 443; do
  if ! iptables -C INPUT -p tcp -m state --state NEW --dport "$port" -j ACCEPT 2>/dev/null; then
    n=$(iptables -L INPUT --line-numbers | awk '/REJECT/ {print $1; exit}')
    if [ -n "$n" ]; then
      iptables -I INPUT "$n" -p tcp -m state --state NEW --dport "$port" -j ACCEPT
    else
      iptables -A INPUT -p tcp -m state --state NEW --dport "$port" -j ACCEPT
    fi
  fi
done
netfilter-persistent save >/dev/null
if command -v ufw >/dev/null && ufw status | grep -q active; then ufw allow 80/tcp; ufw allow 443/tcp; fi

say "Creating the app user and folders"
id tts >/dev/null 2>&1 || useradd --system --home-dir "$ROOT" --shell /usr/sbin/nologin tts
mkdir -p "$ROOT" "$DATA"/{audio,tmp,backups} /etc/thought-to-speak
chown -R tts:tts "$ROOT" "$DATA"
chmod 750 "$DATA"
# let the login user read backups (to copy them off the server)
for u in ubuntu opc; do id "$u" >/dev/null 2>&1 && usermod -aG tts "$u"; done

say "Getting the code ($BRANCH)"
if [ -d "$APP/.git" ]; then
  sudo -u tts git -C "$APP" fetch --quiet origin "$BRANCH"
  sudo -u tts git -C "$APP" checkout --quiet "$BRANCH"
  sudo -u tts git -C "$APP" pull --quiet --ff-only origin "$BRANCH"
else
  sudo -u tts git clone --quiet --branch "$BRANCH" "$REPO" "$APP"
fi

say "Installing Python dependencies"
[ -x "$ROOT/venv/bin/python" ] || sudo -u tts python3 -m venv "$ROOT/venv"
sudo -u tts "$ROOT/venv/bin/pip" install --quiet --upgrade pip
sudo -u tts "$ROOT/venv/bin/pip" install --quiet -r "$APP/requirements.txt"

say "Writing settings"
if [ ! -f "$ENV_FILE" ]; then
  cp "$APP/deploy/oracle/env.production" "$ENV_FILE"
  if [ "$MEM_MB" -lt 2000 ]; then sed -i 's/^ANALYSIS_WORKERS=.*/ANALYSIS_WORKERS=1/' "$ENV_FILE"; fi
else
  echo "Keeping existing $ENV_FILE"
fi
chown root:tts "$ENV_FILE" && chmod 640 "$ENV_FILE"

say "Installing the services"
install -m 644 "$APP/deploy/oracle/thought-to-speak.service" /etc/systemd/system/thought-to-speak.service
install -m 644 "$APP/deploy/oracle/tts-backup.service" /etc/systemd/system/tts-backup.service
install -m 644 "$APP/deploy/oracle/tts-backup.timer" /etc/systemd/system/tts-backup.timer
sed "s/__DOMAIN__/$DOMAIN/" "$APP/deploy/oracle/Caddyfile.template" > /etc/caddy/Caddyfile
mkdir -p /var/log/caddy && chown caddy:caddy /var/log/caddy
caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null
chown -R caddy:caddy /var/log/caddy   # validate (as root) can create the log file; Caddy runs as "caddy"

if [ -n "$DUCKDNS_TOKEN" ]; then
  say "Keeping $DOMAIN pointed at this server (DuckDNS, every 5 minutes)"
  sub="${DOMAIN%%.duckdns.org}"
  install -m 700 -d /etc/thought-to-speak/duckdns
  printf 'url="https://www.duckdns.org/update?domains=%s&token=%s&ip="\n' "$sub" "$DUCKDNS_TOKEN" > /etc/thought-to-speak/duckdns/curl.cfg
  chmod 600 /etc/thought-to-speak/duckdns/curl.cfg
  echo '*/5 * * * * root curl -fsS -K /etc/thought-to-speak/duckdns/curl.cfg -o /dev/null' > /etc/cron.d/duckdns
  curl -fsS -K /etc/thought-to-speak/duckdns/curl.cfg -o /dev/null || echo "DuckDNS update failed — check the token"
fi

systemctl daemon-reload
systemctl enable --now thought-to-speak tts-backup.timer >/dev/null
systemctl restart thought-to-speak
systemctl reload caddy || systemctl restart caddy

say "Checking the app"
for _ in $(seq 1 30); do
  curl -fsS http://127.0.0.1:8000/api/health >/dev/null 2>&1 && break
  sleep 1
done
curl -fsS http://127.0.0.1:8000/api/health >/dev/null || { journalctl -u thought-to-speak -n 40 --no-pager; die "the app didn't start"; }
echo "App is running."

if curl -fsS --max-time 20 "https://$DOMAIN/api/health" >/dev/null 2>&1; then
  say "Done — your beta is live at https://$DOMAIN"
else
  say "The app is running. HTTPS for $DOMAIN isn't answering yet."
  cat <<EOF
  That's usually one of:
   - The domain doesn't point at this server yet: $DOMAIN should resolve to $(curl -fsS --max-time 5 https://ifconfig.me 2>/dev/null || echo "this server's public IP")
   - Ports 80/443 aren't open in Oracle Cloud: Networking → your VCN → Security List → add Ingress rules for TCP 80 and 443 from 0.0.0.0/0
  Caddy keeps retrying the certificate by itself. Check with:  sudo journalctl -u caddy -n 50 --no-pager
EOF
fi
