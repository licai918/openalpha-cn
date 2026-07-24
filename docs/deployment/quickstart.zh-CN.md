# OpenAlpha CN 快速部署

完整的生产边界、备份、恢复、升级和回滚见[详细部署方案](production.zh-CN.md)。

## Docker Compose（推荐）

```powershell
git clone https://github.com/ss8875/openalpha-cn.git
Set-Location openalpha-cn
docker compose -f deploy/compose.yml up -d --build
docker compose -f deploy/compose.yml ps
Start-Process http://127.0.0.1:8000
```

停止但保留数据：

```powershell
docker compose -f deploy/compose.yml down
```

不要执行 `down --volumes`，除非明确要删除本地研究证据和账本。

## Python 本地运行

```powershell
Copy-Item .env.example .env
uv sync --locked --all-extras --dev
uv run openalpha doctor
uv run openalpha serve
```

Web 开发服务器：

```powershell
Set-Location web
pnpm install --frozen-lockfile
pnpm dev
```

## 不想自行部署

[下载链邻涨停复盘策略软件 1.0.9](https://github.com/ss8875/openalpha-cn/releases/download/chainlin-desktop-v1.0.9/Lianlin-LimitUp-Review-Setup-1.0.9-x64.exe)

安装包未签名；请先核验 Release 公布的 `144,902,921 bytes` 和 SHA-256：

`0DDD3AF69C671C3AF0F7AEC90D57B77363705E38E871B49D640C7A2D0D05838B`

![链邻软件与 OpenAlpha CN 部署微信咨询](../../assets/brand/platform-wechat-banner.png)

扫码可咨询安装、部署、数据接入和产品使用问题。
