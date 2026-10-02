#!/usr/bin/env bash
# One-time privileged install of the already verified private movie archive.
# Run interactively as: sudo bash /home/deploy/movie-review-stage/bootstrap_ubuntu_2404.sh
set -Eeuo pipefail
umask 077

stage=/home/deploy/movie-review-stage
version=20260930-94a62f21
release=/opt/movie-review/releases/$version
source_data=$stage/source-data
data_dir=/var/lib/movie-review
backup_dir=/var/backups/movie-review
code_zip=$stage/releases/$version/release.zip
source_zip=$stage/source-snapshot-20260930.zip
uv=$stage/bin/uv

if [[ $EUID -ne 0 || ! -t 0 ]]; then
    echo '请在交互终端使用 sudo 运行此脚本。' >&2
    exit 1
fi
. /etc/os-release
if [[ $ID != ubuntu || $VERSION_ID != 24.04 ]]; then
    echo '服务器系统不是已核验的 Ubuntu 24.04。' >&2
    exit 1
fi
if ! systemctl is-active --quiet sing-box; then
    echo '现有 sing-box 未运行，先排查节点。' >&2
    exit 1
fi
if getent passwd movie-review >/dev/null || [[ -e /opt/movie-review/current || -e $release || -e /etc/systemd/system/movie-review.service || -e $data_dir/db/app.sqlite3 ]]; then
    echo '发现既有网站安装或部分安装；拒绝覆盖，请先检查。' >&2
    exit 1
fi
if [[ ! -x $uv || ! -d $source_data || ! -f $code_zip || ! -f $source_zip ]]; then
    echo '隔离试运行文件缺失。' >&2
    exit 1
fi
printf '%s  %s\n' '94a62f21222ad077e5d09ba2a7f33cb11e0ad52cf1d2157a8d3ee9ce0e172d29' "$code_zip" | sha256sum --check --status
printf '%s  %s\n' '88a2c91603f5752895bee6f2c7a703fd66e3823cdd70468d7d9a9595322a69bd' "$source_zip" | sha256sum --check --status

useradd --system --user-group --home-dir "$data_dir" --shell /usr/sbin/nologin movie-review
install -d -m 0755 /opt/movie-review /opt/movie-review/releases
install -d -m 0755 "$release"
unzip -q "$code_zip" -d "$release"
chown -R root:root "$release"
find "$release" -type d -exec chmod 0755 {} +
find "$release" -type f -exec chmod 0644 {} +
"$uv" venv --python /usr/bin/python3 "$release/.venv"
"$uv" pip sync --python "$release/.venv/bin/python" "$release/requirements-web.lock"
"$uv" pip check --python "$release/.venv/bin/python"
chown -R root:root "$release/.venv"
chmod -R go+rX,go-w "$release/.venv"

install -d -m 0700 -o movie-review -g movie-review "$data_dir"
cd "$release"
"$release/.venv/bin/python" - "$source_data" "$data_dir" <<'PY'
import sys
from pathlib import Path
from web.manage import migrate
report = migrate(Path(sys.argv[1]), Path(sys.argv[2]))
print(f"migrated_records={report['recordCount']} referenced_media={report['referencedMediaCount']}")
PY
chown -R movie-review:movie-review "$data_dir"

install -d -m 0750 -o root -g movie-review /etc/movie-review
printf 'MOVIE_REVIEW_DATA_DIR=%s\n' "$data_dir" > /etc/movie-review/app.env
chown root:movie-review /etc/movie-review/app.env
chmod 0640 /etc/movie-review/app.env
ln -s "releases/$version" /opt/movie-review/current

echo '请为网站账户 admin 输入新密码两次；输入内容不会显示。'
runuser -u movie-review -- "$release/.venv/bin/python" -m web.manage --data-dir "$data_dir" init-account --username admin

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
