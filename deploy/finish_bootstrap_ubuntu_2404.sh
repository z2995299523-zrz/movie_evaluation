#!/usr/bin/env bash
# Continue the verified one-time install after the account prompt stopped it.
set -Eeuo pipefail
umask 077

release=/opt/movie-review/releases/20260930-94a62f21
data_dir=/var/lib/movie-review
backup_dir=/var/backups/movie-review

if [[ $EUID -ne 0 || ! -t 0 ]]; then
    echo '请在交互终端使用 sudo 运行此脚本。' >&2
    exit 1
fi
if [[ $(readlink /opt/movie-review/current) != releases/20260930-94a62f21 ]] ||
   [[ ! -f $release/requirements-web.lock || ! -f $data_dir/db/app.sqlite3 ]] ||
   ! getent passwd movie-review >/dev/null ||
   ! systemctl is-active --quiet sing-box; then
    echo '服务器不是预期的部分安装状态；拒绝续跑。' >&2
    exit 1
fi

chown -R root:root "$release/.venv"
chmod -R go+rX,go-w "$release/.venv"
cd "$release"
account_name=$("$release/.venv/bin/python" - "$data_dir/db/app.sqlite3" <<'PY'
import sqlite3
import sys
with sqlite3.connect(sys.argv[1]) as conn:
    record_count = conn.execute("SELECT COUNT(*) FROM reviews").fetchone()[0]
    account = conn.execute("SELECT username FROM account WHERE id=1").fetchone()
if record_count != 39:
    raise SystemExit(f"迁移记录数量异常：{record_count}")
print(account[0] if account else "")
PY
)
if [[ -n $account_name && $account_name != admin ]]; then
    echo '发现非 admin 网站账户；拒绝续跑。' >&2
    exit 1
fi
if [[ -z $account_name ]]; then
    echo '请为网站账户 admin 输入新密码两次；至少 12 个字符，输入内容不会显示。'
    runuser -u movie-review -- "$release/.venv/bin/python" -m web.manage --data-dir "$data_dir" init-account --username admin
fi

install -d -m 0700 -o movie-review -g movie-review "$backup_dir"
runuser -u movie-review -- "$release/.venv/bin/python" -m web.manage --data-dir "$data_dir" backup-daily "$backup_dir"

install -m 0644 "$release/deploy/movie-review.service" /etc/systemd/system/movie-review.service
install -m 0644 "$release/deploy/movie-review-backup.service" /etc/systemd/system/movie-review-backup.service
install -m 0644 "$release/deploy/movie-review-backup.timer" /etc/systemd/system/movie-review-backup.timer
systemd-analyze verify /etc/systemd/system/movie-review.service /etc/systemd/system/movie-review-backup.service /etc/systemd/system/movie-review-backup.timer
systemctl daemon-reload
systemctl enable --now movie-review.service
curl --retry 10 --retry-delay 1 --retry-connrefused --fail --silent --show-error http://127.0.0.1:8000/readyz
printf '\n'
test "$(curl --silent --output /dev/null --write-out '%{http_code}' http://127.0.0.1:8000/api/reviews)" = 401
systemctl is-active --quiet sing-box
echo 'app_service=active private_api=401 existing_node=active; public_entry=not_configured'
