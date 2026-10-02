#!/usr/bin/env bash
# Public IP HTTPS on :8443; leaves the existing sing-box :443 listener alone.
# Run interactively as: sudo bash /home/deploy/movie-review-stage/enable_ip_https_ubuntu_2404.sh
set -Eeuo pipefail
umask 077

ip=179.255.107.220
stage=/home/deploy/movie-review-stage
certbot=/opt/movie-review/certbot-venv/bin/certbot
webroot=/var/www/movie-review-acme
site=/etc/nginx/sites-available/movie-review

if [[ $EUID -ne 0 || ! -t 0 ]]; then
    echo '请在交互终端使用 sudo 运行此脚本。' >&2
    exit 1
fi
. /etc/os-release
if [[ $ID != ubuntu || $VERSION_ID != 24.04 ]]; then
    echo '服务器系统不是已核验的 Ubuntu 24.04。' >&2
    exit 1
fi
systemctl is-active --quiet sing-box
systemctl is-active --quiet movie-review.service
if [[ -e /etc/systemd/system/movie-review-certbot-renew.service ]] ||
   { [[ -e $site ]] && ! grep -Fq 'Managed by movie-review IP HTTPS setup' "$site"; }; then
    echo '发现既有网站入口或证书续期配置；拒绝覆盖，请先检查。' >&2
    exit 1
fi

DEBIAN_FRONTEND=noninteractive apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y nginx
systemctl stop nginx || true
if [[ -L /etc/nginx/sites-enabled/default ]]; then
    mv /etc/nginx/sites-enabled/default /etc/nginx/default-site-link.before-movie-review
fi
install -d -m 0755 "$webroot/.well-known/acme-challenge"
cat > "$site" <<EOF
# Managed by movie-review IP HTTPS setup. Port 443 belongs to sing-box.
server {
    listen 80;
    server_name $ip;
    location ^~ /.well-known/acme-challenge/ {
        root $webroot;
        default_type text/plain;
        try_files \$uri =404;
    }
    location / { return 308 https://$ip:8443\$request_uri; }
}
EOF
chmod 0644 "$site"
if [[ -L /etc/nginx/sites-enabled/movie-review ]]; then
    [[ $(readlink /etc/nginx/sites-enabled/movie-review) == "$site" ]]
else
    ln -s "$site" /etc/nginx/sites-enabled/movie-review
fi
nginx -t
systemctl enable --now nginx
printf 'ok\n' > "$webroot/.well-known/acme-challenge/local-check"
chmod 0644 "$webroot/.well-known/acme-challenge/local-check"
if [[ $(curl --fail --silent --show-error --header "Host: $ip" http://127.0.0.1/.well-known/acme-challenge/local-check) != ok ]]; then
    echo '80 端口的本机证书验证路径不通。' >&2
    exit 1
fi
rm "$webroot/.well-known/acme-challenge/local-check"

if [[ ! -x $certbot ]]; then
    if [[ ! -d /opt/movie-review/certbot-venv ]]; then
        "$stage/bin/uv" venv --python /usr/bin/python3 /opt/movie-review/certbot-venv
    fi
    "$stage/bin/uv" pip install --python /opt/movie-review/certbot-venv/bin/python certbot==5.8.0
fi
"$certbot" --version

read -r -p '证书联系邮箱（可留空）：' contact_email
certbot_account=(--agree-tos --non-interactive)
if [[ -n $contact_email ]]; then
    certbot_account+=(--email "$contact_email")
else
    certbot_account+=(--register-unsafely-without-email)
fi

"$certbot" certonly --staging --webroot --webroot-path "$webroot" \
    --ip-address "$ip" --preferred-profile shortlived --cert-name movie-review-ip-test \
    "${certbot_account[@]}"
"$certbot" certonly --webroot --webroot-path "$webroot" \
    --ip-address "$ip" --preferred-profile shortlived --cert-name movie-review-ip \
    "${certbot_account[@]}"
openssl x509 -in /etc/letsencrypt/live/movie-review-ip/fullchain.pem -noout -text | grep -F "IP Address:$ip" >/dev/null

cat > "$site" <<EOF
# Managed by movie-review IP HTTPS setup. Port 443 belongs to sing-box.
server {
    listen 80;
    server_name $ip;
    location ^~ /.well-known/acme-challenge/ {
        root $webroot;
        default_type text/plain;
        try_files \$uri =404;
    }
    location / { return 308 https://$ip:8443\$request_uri; }
}

server {
    listen 8443 ssl;
    server_name $ip;
    ssl_certificate /etc/letsencrypt/live/movie-review-ip/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/movie-review-ip/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    error_page 497 =308 https://$ip:8443\$request_uri;
    client_max_body_size 201m;
    server_tokens off;
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host \$http_host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 120s;
    }
}
EOF
chmod 0644 "$site"
nginx -t
systemctl reload nginx

install -d -m 0755 /etc/letsencrypt/renewal-hooks/deploy
cat > /etc/letsencrypt/renewal-hooks/deploy/20-reload-movie-review-nginx.sh <<'HOOK'
#!/bin/sh
set -eu
if [ "${RENEWED_LINEAGE:-}" = /etc/letsencrypt/live/movie-review-ip ]; then
    /usr/bin/systemctl reload nginx
fi
HOOK
chmod 0755 /etc/letsencrypt/renewal-hooks/deploy/20-reload-movie-review-nginx.sh
"$certbot" renew --cert-name movie-review-ip --dry-run --non-interactive
"$certbot" delete --cert-name movie-review-ip-test --non-interactive

cat > /etc/systemd/system/movie-review-certbot-renew.service <<'UNIT'
[Unit]
Description=Renew public IP certificate for private movie archive
After=network-online.target nginx.service
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/opt/movie-review/certbot-venv/bin/certbot renew --quiet --non-interactive
UNIT
cat > /etc/systemd/system/movie-review-certbot-renew.timer <<'UNIT'
[Unit]
Description=Check movie archive certificate daily

[Timer]
OnCalendar=*-*-* 05:10:00 Asia/Shanghai
RandomizedDelaySec=30m
Persistent=true
Unit=movie-review-certbot-renew.service

[Install]
WantedBy=timers.target
UNIT
chmod 0644 /etc/systemd/system/movie-review-certbot-renew.service /etc/systemd/system/movie-review-certbot-renew.timer
systemd-analyze verify /etc/systemd/system/movie-review-certbot-renew.service /etc/systemd/system/movie-review-certbot-renew.timer
systemctl daemon-reload
systemctl enable --now movie-review-certbot-renew.timer movie-review-backup.timer
systemctl start movie-review-backup.service

curl --fail --silent --show-error --resolve "$ip:8443:127.0.0.1" "https://$ip:8443/readyz"
printf '\n'
test "$(curl --silent --output /dev/null --write-out '%{http_code}' --resolve "$ip:8443:127.0.0.1" "https://$ip:8443/api/reviews")" = 401
systemctl is-active --quiet sing-box
echo 'ip_https_local=ready private_api=401 existing_node=active'
