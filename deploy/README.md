# 影评网站部署准备

后续正式发布采用 **本地提交推送 Git → 服务器拉取指定提交 → 测试与备份恢复演练 → 版本切换**。日常命令、首次接入、证据和回滚见 [Git 发布与服务器上线流程](GIT_RELEASE.md)。下文及原 ZIP/bootstrap 脚本保留首次安装和历史迁移用途。

当前网站使用多用户三范式 SQLite 模型 v2。生产发布、迁移与设备验收记录留在本地，不进入公开源码仓库。

现有服务器为 Ubuntu 24.04，应用只监听 `127.0.0.1:8000`，Nginx 网站入口使用 80/8443，原代理继续使用 443。上线需实际验证正式 HTTPS、私有接口、备份恢复和用户设备访问；服务器成功不能代替手机实机及网络路径验收。

`bootstrap_ubuntu_2404.sh`、`finish_bootstrap_ubuntu_2404.sh` 和 `enable_ip_https_ubuntu_2404.sh` 是固定历史环境的一次性安装脚本，包含特定版本和文件哈希，不直接用于新环境或日常更新。当前 IP 路径使用 Certbot 和 Nginx；`Caddyfile.example` 保留域名部署参考。本机备份无法覆盖整机故障风险。

## 代码与运行

1. 将源码提交并推送至 `origin/main`，服务器运行 `sudo movie-review-deploy <完整SHA>` 拉取并部署。应用代码放在 `/opt/movie-review/releases/git-<完整SHA>/`，`current` 指向当前版本；Web 运行账户不应有修改代码的权限。首次离线安装仍可用 `build_release.py` 制作源码包。
2. 在发布版本内建立 Python 3.12 虚拟环境，安装 `requirements-web.lock`，记录实际 `pip freeze` 与 Python 版本。`requirements-web.txt` 记录直接依赖；锁定清单由隔离环境生成，并已在 Windows Python 3.12 上通过 Web 测试。Linux 服务器仍须安装并重跑测试。
3. 数据目录设为 `/var/lib/movie-review`，在 `/etc/movie-review/app.env` 中写入 `MOVIE_REVIEW_DATA_DIR=/var/lib/movie-review`。此文件不放入仓库。建独立 `movie-review` 账户，仅授予数据目录写权限。
4. 将 `movie-review.service` 安装到 systemd，经配置检查后启用。应用只能监听 `127.0.0.1:8000`，不直接对外开放。
5. 将 `Caddyfile.example` 内的域名替换为真实域名，检查 Caddy 实际版本并校验配置。80 用于 HTTP-01，8443 用于 HTTPS；保留代理节点的 443 和现有 SSH 规则。Caddy 需要支持 `request_body max_size`（v2.10.0 或更新）。

## 账户和迁移

Schema v2 增加多用户与三范式关系表，原单账户升级为管理员，密码哈希及影评保留。正式发布前停止写入、保留并校验旧版完整 ZIP，先在隔离恢复目录用新版运行 `python -m web.manage --data-dir <目录> upgrade-db`。演练通过后再迁移正式库并切换版本。旧程序不能连接 v2 数据库。详细模型及回滚顺序见 [数据库模型](../docs/DATABASE_MODEL.md)。

通过终端运行 `python -m web.manage --data-dir /var/lib/movie-review init-account --username <个人账户>`，交互输入密码，不把密码写进命令历史。忘记密码时运行 `reset-password`，旧会话会失效。

正式迁移前先暂停桌面写入，保存原始目录、未提交源码、全部历史备份以及 SHA-256 清单。`python -m web.manage preflight <桌面数据目录>` 是只读预检；差异必须先处理。`python -m web.manage migrate <桌面数据目录副本> <隔离测试目录>` 只接受全新空目标目录。试迁移并验证 ZIP 往返后，在最终切换窗口对重新取得的快照执行正式迁移。不要把旧桌面目录与服务器目录设为同一个可写路径。

现有桌面档案含一张约 5250 万像素的 JPG。迁移预检允许最高 6000 万像素的历史原图并保留原始字节；网页版新上传仍限制为 4000 万像素。试迁移时要核对该图的原始哈希和缩略图访问，并观察服务器内存。

## 发布和验证

先运行 50 项桌面回归和 Web API 测试。上线前还要实际完成：浏览器登录与退出、手机与电脑相互读写、并发冲突、上传与 ZIP 导入导出、真实 iPhone Safari、小屏触控、正式证书、Wi-Fi 和移动数据直连、原节点连通性、备份恢复演练。HTTP 200 或本地浏览器测试不能代替公网验收。

数据库与媒体需定时一致备份和离机加密副本。SQLite 备份使用在线备份 API，媒体按同一快照的引用清单复制，并核对哈希。设置备份失败告警以及定期隔离恢复演练；只有完成可恢复备份后才开放正式写入。

本地定时备份模板为 `movie-review-backup.service` 与 `movie-review-backup.timer`。备份服务使用应用账户读取私有数据库和媒体，但其系统服务沙箱把数据目录设为只读，只允许写 `/var/backups/movie-review`。需要先建立该目录、核对权限，并用 `systemd-analyze calendar` 和 `systemd-analyze verify` 校验服务器实际 systemd 版本。该定时任务只产生服务器本机副本；离机加密复制、保留份数与失败通知必须根据用户选定的目的地另行配置并实测。
