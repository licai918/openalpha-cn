# OpenAlpha CN v1.0.0 发布交接

状态：发布和匿名验收完成。

## 固定发布

- 公开仓库：`https://github.com/ss8875/openalpha-cn`
- 默认分支：`main`
- 源码许可证：MIT
- Release/Tag Commit：`5b82a3558b780c25519b1d471c78e1721ca51c4a`
- 源码 Release：`https://github.com/ss8875/openalpha-cn/releases/tag/v1.0.0`
- 桌面 Release：`https://github.com/ss8875/openalpha-cn/releases/tag/chainlin-desktop-v1.0.9`
- 全绿 CI：`https://github.com/ss8875/openalpha-cn/actions/runs/30086921567`

## 验收摘要

- 后端：78 项测试通过，核心覆盖率 88.86%；
- Web：锁定安装、依赖审计、Lint、2 项单测、生产构建通过；
- 浏览器：Chromium 桌面/移动共 4 项 Playwright 流程通过，无控制台错误和移动端溢出；
- Replay：60 个交易日、300 个事件全部成功、确定性一致、已知前视违规为 0；
- 容器：Windows 本地与 GitHub Ubuntu 均完成构建、健康检查、重启和持久证据恢复；
- 安全：Python/Node 无已知漏洞，公开仓库扫描 0 blocker；
- 功能台账：72 项全部有唯一 ID、证据和终态，真实完成 66 / 72（91.67%）；
- `UNREVIEWED=0`；
- `UNKNOWN=0`；
- 4 项明确排除，2 项明确延后。

## 链邻安装包

- Release 文件：`Lianlin-LimitUp-Review-Setup-1.0.9-x64.exe`
- 字节数：`144,902,921`
- SHA-256：`0DDD3AF69C671C3AF0F7AEC90D57B77363705E38E871B49D640C7A2D0D05838B`
- GitHub 资产状态：`uploaded`
- 校验资产：`SHA256SUMS.txt`（109 bytes）
- 签名：未签名

匿名公开 URL 重新下载后的文件大小和 SHA-256 与本地源文件完全一致。

## 匿名验证

- 使用禁用 Credential Helper 的 Git 完成 `v1.0.0` 浅克隆；
- 检出 Commit 为 `5b82a3558b780c25519b1d471c78e1721ca51c4a`；
- README 与 MIT LICENSE 可读取；
- 公开 Release API 返回两个非草稿、非预发布 Release；
- 安装包公开下载成功，哈希匹配。

## 运行边界

- 默认仅本机回环访问；
- 无实盘券商执行；
- 无商业数据转售；
- 无内置真实凭据；
- 不把合成回放成功率描述为收益证明；
- 组合现金/持仓逐日核算和图形化 Agent Flow Builder 延后。

## 工作区影响

- OpenAlpha CN 是 `D:\d-soft\openalpha-cn` 下的独立新仓库；
- 链邻安装包仅只读取用并上传 GitHub Release；
- 没有修改 `jdfp-next` 的数据接口、数据库、Electron IPC 或桌面运行时代码。
