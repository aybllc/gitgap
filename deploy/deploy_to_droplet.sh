#!/usr/bin/env bash
# ==============================================================================
# gitgap — production deploy to ask.gitgap.org droplet
# ==============================================================================
# Mirrors the eaiou production pattern (do.eaiou.org) end-to-end.
# Single-command install. Idempotent on re-run (skips finished steps).
#
# Run from the gitgap repo root:
#     bash deploy/deploy_to_droplet.sh
#
# What this does, in order:
#   1. apt install python3-venv, nginx, certbot, acl
#   2. create system user `gitgap` with /home/gitgap
#   3. rsync this repo to /home/gitgap/gitgap (or git clone if pushed)
#   4. python venv + pip install -r requirements.txt
#   5. systemd unit /etc/systemd/system/gitgap.service
#   6. nginx site /etc/nginx/sites-available/ask.gitgap.org (HTTP first)
#   7. certbot --nginx for TLS on ask.gitgap.org
#   8. setfacl giving www-data traverse + read on /home/gitgap/gitgap/app/static
#   9. enable + start gitgap.service, reload nginx
#
# What this does NOT do (deliberate):
#   - No autopull webhook — add later via deploy/install_webhook.sh
#   - No DB migration — SQLite at data/gitgap.db, auto-creates on first request
#   - No data restore — fresh DB
# ==============================================================================
set -euo pipefail

# ─── Configuration (edit before running) ──────────────────────────────────────
DROPLET_HOST="64.227.3.227"        # ask.gitgap.org
DROPLET_PORT="63043"
DROPLET_USER="root"
DOMAIN="ask.gitgap.org"
ALT_DOMAIN="gitgap.org"             # also points at this droplet
APP_USER="gitgap"
APP_DIR="/home/${APP_USER}/gitgap"
APP_PORT="8103"                     # eaiou=8102, gitgap=8103
APP_VARIANT="app"                   # app/ (current) — switch to "gitgap-app" for legacy
CERTBOT_EMAIL="doctor.eric.martin@gmail.com"
SSH_KEY="${HOME}/.ssh/id_ed25519"
LOCAL_REPO="$(cd "$(dirname "$0")/.." && pwd)"

ssh_run() {
    ssh -i "${SSH_KEY}" -p "${DROPLET_PORT}" -o StrictHostKeyChecking=accept-new \
        "${DROPLET_USER}@${DROPLET_HOST}" "$@"
}

ssh_run_stdin() {
    ssh -i "${SSH_KEY}" -p "${DROPLET_PORT}" -o StrictHostKeyChecking=accept-new \
        "${DROPLET_USER}@${DROPLET_HOST}" "bash -s"
}

say() { printf '\n\033[1;36m▶ %s\033[0m\n' "$*"; }
ok()  { printf '\033[1;32m  ✓\033[0m %s\n' "$*"; }

# ─── 1. APT packages ──────────────────────────────────────────────────────────
say "Step 1/9: install apt packages"
ssh_run "DEBIAN_FRONTEND=noninteractive apt-get update -qq && \
         DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
         python3 python3-venv python3-pip nginx certbot python3-certbot-nginx acl rsync git" \
        > /dev/null
ok "packages installed"

# ─── 2. System user ───────────────────────────────────────────────────────────
say "Step 2/9: create user ${APP_USER}"
ssh_run "id -u ${APP_USER} >/dev/null 2>&1 || useradd -m -s /bin/bash ${APP_USER}"
ok "user ${APP_USER} ready"

# ─── 3. Sync repo to droplet ──────────────────────────────────────────────────
say "Step 3/9: sync repo to ${APP_DIR}"
ssh_run "mkdir -p ${APP_DIR} && chown -R ${APP_USER}:${APP_USER} /home/${APP_USER}"
rsync -az --delete \
    --exclude '.git' --exclude '__pycache__' --exclude '*.pyc' \
    --exclude 'venv' --exclude 'data/*.db' --exclude '.env' \
    --exclude 'app.zip' --exclude 'gitgap-app.zip' \
    -e "ssh -i ${SSH_KEY} -p ${DROPLET_PORT}" \
    "${LOCAL_REPO}/" "${DROPLET_USER}@${DROPLET_HOST}:${APP_DIR}/"
ssh_run "chown -R ${APP_USER}:${APP_USER} ${APP_DIR}"
ok "repo synced"

# ─── 4. Python venv + dependencies ────────────────────────────────────────────
say "Step 4/9: build venv + install requirements"
ssh_run_stdin <<EOF
set -euo pipefail
cd ${APP_DIR}
sudo -u ${APP_USER} python3 -m venv venv
sudo -u ${APP_USER} ./venv/bin/pip install --quiet --upgrade pip
sudo -u ${APP_USER} ./venv/bin/pip install --quiet -r requirements.txt
EOF
ok "venv ready"

# ─── 5. systemd unit ──────────────────────────────────────────────────────────
say "Step 5/9: install systemd unit"
ssh_run_stdin <<EOF
cat > /etc/systemd/system/gitgap.service <<'UNIT'
[Unit]
Description=gitgap FastAPI app
After=network.target

[Service]
User=${APP_USER}
WorkingDirectory=${APP_DIR}
ExecStart=${APP_DIR}/venv/bin/uvicorn ${APP_VARIANT}.main:app --host 127.0.0.1 --port ${APP_PORT}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
EOF
ok "gitgap.service installed"

# ─── 6. nginx site (HTTP first; certbot adds TLS in step 7) ──────────────────
say "Step 6/9: install nginx site"
ssh_run_stdin <<EOF
cat > /etc/nginx/sites-available/${DOMAIN} <<'NGINX'
server {
    listen 80;
    listen [::]:80;
    server_name ${DOMAIN} ${ALT_DOMAIN};

    client_max_body_size 50M;
    access_log /var/log/nginx/${DOMAIN}.access.log;
    error_log  /var/log/nginx/${DOMAIN}.error.log;

    location /static/ {
        alias ${APP_DIR}/${APP_VARIANT}/static/;
        access_log off;
        expires 30d;
        add_header Cache-Control "public, no-transform";
    }

    location / {
        proxy_pass http://127.0.0.1:${APP_PORT};
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_redirect off;
        proxy_buffering off;
        proxy_read_timeout 90s;
    }
}
NGINX
ln -sf /etc/nginx/sites-available/${DOMAIN} /etc/nginx/sites-enabled/${DOMAIN}
[ -f /etc/nginx/sites-enabled/default ] && rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl enable --now nginx
systemctl reload nginx
EOF
ok "nginx site live on :80"

# ─── 7. TLS via certbot ───────────────────────────────────────────────────────
say "Step 7/9: TLS via certbot (Let's Encrypt)"
ssh_run "certbot --nginx --non-interactive --agree-tos \
         --email ${CERTBOT_EMAIL} \
         -d ${DOMAIN} -d ${ALT_DOMAIN} \
         --redirect" | tail -10
ok "TLS issued + redirect installed"

# ─── 8. ACL for nginx static reads ────────────────────────────────────────────
say "Step 8/9: setfacl for www-data static reads"
ssh_run_stdin <<EOF
setfacl -m u:www-data:--x /home/${APP_USER}
setfacl -m u:www-data:--x ${APP_DIR}
[ -d ${APP_DIR}/${APP_VARIANT}/static ] && {
    setfacl -R -m u:www-data:rX ${APP_DIR}/${APP_VARIANT}/static
    setfacl -R -d -m u:www-data:rX ${APP_DIR}/${APP_VARIANT}/static
}
EOF
ok "ACLs applied"

# ─── 9. Start service ─────────────────────────────────────────────────────────
say "Step 9/9: enable + start gitgap.service"
ssh_run "systemctl enable --now gitgap.service && sleep 2 && systemctl is-active gitgap"
ok "gitgap.service running"

# ─── Verification ─────────────────────────────────────────────────────────────
say "Verification"
ssh_run "curl -sI -o /dev/null -w 'localhost:${APP_PORT}: %{http_code}\n' http://127.0.0.1:${APP_PORT}/ || true"
curl -sI -o /dev/null -w "https://${DOMAIN}: %{http_code}\n" "https://${DOMAIN}/" || true

printf '\n\033[1;32m✓ gitgap deployed.\033[0m  Browse: https://%s/\n\n' "${DOMAIN}"
