#!/usr/bin/env bash
# Install once as /usr/local/sbin/movie-review-deploy, owned by root, mode 0755.
set -Eeuo pipefail
umask 077

repo=/opt/movie-review/repo.git
remote=https://github.com/z2995299523-zrz/movie_evaluation.git
commit=${1:-}
phase=${2:-all}
if [[ $EUID -ne 0 || ! $commit =~ ^[0-9a-f]{40}$ ]]; then
    echo 'Usage: sudo movie-review-deploy <full-40-character-commit> [all|prepare|trial|activate|verify|rollback]' >&2
    exit 1
fi
case "$phase" in all|prepare|trial|activate|verify|rollback) ;; *) echo 'Invalid phase' >&2; exit 1 ;; esac
if [[ ! -d $repo || $(git --git-dir="$repo" remote get-url origin) != "$remote" ]]; then
    echo 'Expected the dedicated source repository with the approved origin' >&2
    exit 1
fi
exec 9>/run/lock/movie-review-deploy.lock
flock -n 9 || { echo 'Another deployment is running' >&2; exit 1; }
git --git-dir="$repo" fetch --no-tags origin refs/heads/main:refs/remotes/origin/main
git --git-dir="$repo" cat-file -e "$commit^{commit}"
git --git-dir="$repo" merge-base --is-ancestor "$commit" refs/remotes/origin/main
runner=$(mktemp /opt/movie-review/git-deploy-XXXXXXXX.py)
trap 'rm -f -- "$runner"' EXIT
git --git-dir="$repo" show "$commit:deploy/git_deploy.py" > "$runner"
/usr/bin/python3 "$runner" "$phase" --commit "$commit"
