# 安全问题 / Security

项目涉及个人影评、登录会话、私有图片和档案导入导出。欢迎报告影响这些边界的安全问题。

## 报告方式

如果仓库的 GitHub Security 页面提供 **Report a vulnerability**，请优先使用该私密入口。该功能是否可用，以 [Security 页面](https://github.com/z2995299523-zrz/movie_evaluation/security)的实际显示为准。

如果没有私密入口，请先通过 [GitHub Issue](https://github.com/z2995299523-zrz/movie_evaluation/issues/new)请求建立私密报告渠道，只说明希望报告安全问题，不公开漏洞细节、攻击载荷、凭据或个人数据。等待维护者提供私密渠道后，再提交完整复现材料。

Prefer GitHub's private vulnerability reporting when the repository exposes that option. If it is unavailable, open an issue requesting a private contact channel without disclosing exploit details or sensitive data.

## 报告内容

- 受影响的提交或版本，以及桌面版 / 网页版环境。
- 使用虚构账户和数据的最小复现步骤。
- 预期的访问或数据边界，以及实际影响。
- 可选的修复建议或回归测试。

请只在自己的本地环境或已获授权的环境中复现问题，不在他人的部署上测试。

## 维护范围

目前主要维护 `main` 分支；尚未建立多个历史版本的长期安全支持承诺。部署前应在隔离环境验证准备采用的提交，并保留备份和回滚方案。项目由个人维护，暂不承诺固定响应时间或修复期限。

源码中的身份认证、用户隔离、媒体校验和测试有助于降低风险，但不能代替实际部署中的 HTTPS、访问权限配置及备份恢复验证。部署说明见 [deploy/README.md](deploy/README.md)。
