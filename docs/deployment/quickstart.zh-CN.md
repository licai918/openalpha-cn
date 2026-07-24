# OpenAlpha CN 部署快速开始

## 开发环境

要求：

- Windows 10/11 或当前受支持的 Linux；
- Python 3.11 或 3.12；
- uv；
- Git。

```powershell
git clone https://github.com/ss8875/openalpha-cn.git
Set-Location openalpha-cn
Copy-Item .env.example .env
uv sync --all-extras --dev
uv run openalpha doctor
uv run pytest
```

真实 Token 只写入本地 `.env`，不要发到 Issue、聊天记录、运行报告或 Git 历史。

## 当前可用命令

```powershell
uv run openalpha version
uv run openalpha doctor
uv run openalpha doctor --json
```

随着垂直切片完成，本文会增加 API、Web、Docker、数据导入和历史回放命令。未实现的命令不会提前写入快速开始。

## 不想自行部署

可以从 `chainlin-desktop-v1.0.9` Release 下载链邻涨停复盘策略软件 Windows x64 安装版。当前安装包未进行数字签名，下载后请按 Release 公布的 SHA-256 校验文件。

![链邻软件与 OpenAlpha CN 部署微信咨询](../../assets/brand/platform-wechat-banner.png)

扫码可咨询安装、部署和产品使用问题。所有研究内容均不构成投资建议。

