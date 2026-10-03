# Git 发布与服务器上线流程

本地提交并推送到现有 GitHub 仓库，服务器从同一仓库取得指定提交后部署。远程为 `https://github.com/z2995299523-zrz/movie_evaluation.git`，发布分支为 `main`。仓库目前公开，只保存源码、测试和通用文档。

## 本地发布

桌面开发环境使用 `.venv`；Web 测试环境使用单独的 Python 3.12 环境。首次准备 Web 测试环境：

```powershell
py -3.12 -m venv .runtime-web\release-venv
.\.runtime-web\release-venv\Scripts\python.exe -m pip install -r requirements-web.lock httpx==0.28.1
```

每次修改后，在项目根目录运行：

```powershell
.\deploy\publish.ps1 -Message 'fix: 描述本次改动'
```

脚本核对 origin 与 main、拉取远程分支并检查分支关系，运行桌面及 Web 必需测试，拒绝跳过或缺失测试。随后仅暂存源码范围、扫描凭据特征和私有文件、校验差异、提交、推送并核对远程 SHA。已有暂存改动时停止，保留原暂存内容供人工审阅；远程有更新或分叉时先合并并解决冲突，再重新发布。脚本输出完整提交 SHA 和服务器命令。

不提交个人 JSON、`feils/`、媒体、数据库、备份、运行环境、EXE、`.env` 或 SSH 私钥。生产操作记录与原上线方案留在本地，不进入公开仓库。凭据扫描只覆盖已知特征，新增配置仍需检查内容。

GitHub Actions 在推送/PR 后扫描源码中的私有文件与已知凭据特征，再运行 Windows 桌面测试及 Ubuntu Web 测试。Windows 还使用 Node.js 24 与 runner 中的 Chrome 执行 `tests/browser_security.mjs`，验证合成记录的文本/属性注入防护；缺少浏览器或测试失败会让 job 失败。官方 Actions 固定完整提交 SHA。正式上线前检查相应提交的 CI 成功。CI 不保存生产 SSH 凭据，也不自动部署服务器。当前流程以本地和服务器必需测试为执行门禁；服务器脚本不会查询 GitHub CI 状态。

## 服务器一次性接入

适用于已安装网站的 Ubuntu 24.04 服务器，不用于空服务器首次安装。需 Git、Python 3.12、`flock`、现有应用账户、systemd 服务，以及已安装到 `/opt/movie-review/tools/uv` 的可信 uv 工具。

首次接入或从旧流程升级时，先独立核验 uv 来源、版本及 SHA-256，再以管理员身份复制到 root 控制的路径。`UV_SHA256` 必须替换为已核验的摘要；不能仅对部署账户可写的当前文件计算摘要并视为可信。下列安装步骤拒绝覆盖既有工具；需要升级时先审阅新制品并保存原工具、摘要及配置证据。root 执行工具前会检查工具、摘要和每级父目录均为 root 所有、没有组/其他用户写权限、没有符号链接，并核对摘要。

```bash
set -euo pipefail
UV_SHA256='<已独立核验的64位小写SHA256>'
test ! -L /opt/movie-review/tools
install -d -o root -g root -m 0755 /opt/movie-review/tools
test ! -e /opt/movie-review/tools/uv
test ! -L /opt/movie-review/tools/uv
test ! -e /opt/movie-review/tools/uv.sha256
test ! -L /opt/movie-review/tools/uv.sha256
install -o root -g root -m 0755 /home/deploy/movie-review-stage/bin/uv /opt/movie-review/tools/uv
printf '%s  %s\n' "$UV_SHA256" /opt/movie-review/tools/uv | sha256sum --check --status
printf '%s\n' "$UV_SHA256" > /opt/movie-review/tools/uv.sha256
chown root:root /opt/movie-review/tools/uv.sha256
chmod 0644 /opt/movie-review/tools/uv.sha256
namei -l /opt/movie-review/tools/uv
/opt/movie-review/tools/uv --version
```

安装过程中校验失败时停止，不运行残留工具；保留失败现场并核查来源。不要用符号链接将该工具指回 `/home/deploy`。历史 bootstrap 脚本仅作历史证据，新发布流程不会调用家目录中的 uv。复制成功后，以管理员身份运行以下接入流程，`COMMIT` 替换为刚推送并验证的完整 SHA：

```bash
COMMIT=<完整40位提交SHA>
test ! -e /opt/movie-review/repo.git
git init --bare /opt/movie-review/repo.git
git --git-dir=/opt/movie-review/repo.git remote add origin https://github.com/z2995299523-zrz/movie_evaluation.git
git --git-dir=/opt/movie-review/repo.git fetch --no-tags origin refs/heads/main:refs/remotes/origin/main
git --git-dir=/opt/movie-review/repo.git merge-base --is-ancestor "$COMMIT" refs/remotes/origin/main
git --git-dir=/opt/movie-review/repo.git show "$COMMIT:deploy/movie-review-deploy.sh" > /opt/movie-review/movie-review-deploy.sh
install -o root -g root -m 0755 /opt/movie-review/movie-review-deploy.sh /usr/local/sbin/movie-review-deploy
movie-review-deploy "$COMMIT"
```

公开仓库可以匿名只读拉取，服务器不保存本地 GitHub 账户的写入令牌。若以后改为私有仓库，为服务器配置仓库只读 Deploy Key，再调整经过确认的 origin。不要复制个人 GitHub Token 或本地私钥进仓库。

## 日常上线

本地推送成功、CI 成功后，在服务器执行：

```bash
sudo movie-review-deploy <完整40位提交SHA>
```

也可以从 Windows 发起，私钥文件保持在本机；sudo 密码只在终端交互输入：

```powershell
.\deploy\deploy.ps1 -IdentityFile "$env:USERPROFILE\.ssh\dmit_id_rsa.pem" -Commit '<完整40位提交SHA>'
```

脚本持有发布锁，从 origin 获取 main，确认指定提交属于已发布分支，按该提交从 Git 提取源码。每次发布使用独立 Python 运行环境和测试环境，不在正在运行的目录执行 `git pull`。代码来源是服务器拉取的 Git 对象。

执行顺序为：准备目录和依赖 → 必需测试 → 线上备份及隔离恢复演练 → 暂停定时备份并等待在途任务结束 → 停止网站写入 → 最终完整备份及业务行、会话和媒体哈希检查 → 原子切换 current → 启动及健康/鉴权/静态资源检查 → 执行正式备份并隔离恢复。失败切换回原代码并保留最新数据库；备份任务恢复原来的运行状态。

常规发布只接受与当前版本相同的 schema SQL、schema v2 数据库和相同 systemd 模板。结构或系统服务变更会停止，必须另行编写和演练迁移/配置变更。不会在例行上线中自动覆盖生产数据库。

| 位置 | 用途 |
| --- | --- |
| `/opt/movie-review/repo.git` | 服务器只读拉取的 Git 对象 |
| `/opt/movie-review/tools/uv`、`uv.sha256` | root 控制的部署工具及已核验摘要 |
| `/opt/movie-review/releases/git-<完整SHA>/` | 每个提交独立的源码及生产环境 |
| `/opt/movie-review/current` | 正在运行的版本指针 |
| `/opt/movie-review/deployments/<完整SHA>/` | root 私有发布证据及隔离演练 |
| `/var/lib/movie-review/` | 持久数据库、媒体和缩略图 |
| `/var/backups/movie-review/` | 服务器完整数据备份 |
| `/etc/movie-review/app.env` | 服务器运行配置，留在 Git 外 |

`git-release.json` 记录提交、远程及每个源文件的哈希。证据目录包含 `prepared.json`、`trial.json`、`deployment.json`、`verification.json`，准备记录还包含已校验的部署工具 SHA-256。脚本核验 `/readyz=200`、未登录私有 API `401`、实际返回的 Web 静态资源与提交一致，并检查网站、Nginx、sing-box 和备份/证书任务运行状态。

应用继续只监听 `127.0.0.1:8000`，当前 Nginx 入口为 80/8443。发布脚本不修改入口、证书、防火墙或原代理的 443。服务器验证后，另行验证公网 HTTPS、手机实机及需要的 Wi-Fi/移动数据路径。

## 分阶段、失败续跑与回滚

可以分开执行 `prepare`、`trial`、`activate`、`verify`，每个阶段需要前置证据且重新验证源文件。已准备过的提交不会重新覆盖目录；从已成功阶段继续。若准备中途失败，没有 `prepared.json`，先查看失败原因及残留目录，保留证据并处理后重试，不对活动目录执行清理。同一提交已经上线时使用 `verify`。

```bash
sudo movie-review-deploy <SHA> prepare
sudo movie-review-deploy <SHA> trial
sudo movie-review-deploy <SHA> activate
sudo movie-review-deploy <SHA> verify
```

回滚使用需要撤回的当前提交 SHA：

```bash
sudo movie-review-deploy <当前提交SHA> rollback
```

回滚目标来自本次正式发布记录的上一版，先验证 current 和 schema、取得当前数据完整备份，再切回代码；保留上线后的数据库和媒体，执行检查及正式备份恢复验证。数据库发生结构变更时拒绝代码回滚。数据恢复须另行停写、完整保留现有数据、隔离恢复并校验，不能直接用旧备份覆盖新写入。

原 ZIP 发布包、旧发布目录和历史备份保留作历史证据。Git 流程不自动删除发布目录或备份；保留数量和离机备份另行决定。更新启动器时，从已审阅的指定提交取出 `deploy/movie-review-deploy.sh` 再安装；日常部署使用指定提交中的 Python 脚本。
