# 参与贡献 / Contributing

欢迎提交问题、文档修正、测试与代码改进。项目由个人维护，响应时间视维护者安排而定。

Contributions are welcome: bug reports, documentation, tests, and focused code changes. This is an independently maintained project; response times may vary.

## 报告问题

在 [GitHub Issues](https://github.com/z2995299523-zrz/movie_evaluation/issues) 中先搜索是否已有相同问题，再提供：

- 使用的是桌面版还是网页版，以及源码提交或发布版本。
- 操作系统、Python / 浏览器版本（如适用）。
- 重现步骤、预期行为和实际结果。
- 使用虚构数据的最小示例，以及已去除个人信息的错误日志或截图。

请勿上传个人影评库、完整数据备份、账户密码、会话 Cookie、服务器配置或私钥。安全问题请先阅读 [SECURITY.md](SECURITY.md)。

## 提交改动

1. Fork 仓库并从 `main` 创建工作分支。
2. 较大功能或数据格式变化先通过 Issue 讨论需求与兼容方式。
3. 每个 PR 聚焦一个问题，说明触发场景、修改后的行为和验证结果。
4. 按 [README 的检查说明](README.md#测试与持续集成)运行与改动相关的检查；无法执行的检查请明确说明。
5. 如果修改使用方式、数据字段或部署步骤，同步更新文档。

## 项目约定

- 保持桌面版与网页版既有 JSON / ZIP 档案兼容；不能静默丢弃旧字段或历史记录。
- 数据迁移先在副本上验证，保留原始文件与可恢复备份。
- 桌面数据保存在用户选择的目录；网页版按用户隔离档案和媒体。
- 测试使用临时目录和虚构数据，不读取或修改维护者的日常档案与生产数据。
- 不把数据库、媒体、数据备份、构建产物、凭据和本机配置提交到源码仓库。
- PR 提交前检查 `git diff --check` 与实际文件范围。

提交内容应是你有权贡献的代码或文档，并允许按本项目 [MIT 许可证](LICENSE)分发；第三方内容需保留其适用的声明。

## Useful links

- [Setup and checks](README.md)
- [Issues](https://github.com/z2995299523-zrz/movie_evaluation/issues)
- [Pull requests](https://github.com/z2995299523-zrz/movie_evaluation/pulls)
- [Security reporting](SECURITY.md)
