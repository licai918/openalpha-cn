# OpenAlpha CN 详细部署方案

## 1. 推荐拓扑

```text
本机浏览器
    │ 127.0.0.1:8000
    ▼
FastAPI + 静态 Web（非 root、只读根文件系统）
    │
    ├── /data/state.sqlite3
    └── /data/evidence/*.parquet
          Docker named volume: openalpha-runtime
```

默认是单机、本地优先部署。Compose 只把端口绑定到回环地址，不包含认证，也不面向公网。

## 2. 前置条件

- Windows 10/11 + Docker Desktop，或当前支持 Docker Compose v2 的 Linux；
- 至少 2 CPU、4 GiB 内存、2 GiB 可用磁盘；
- 如使用外部 Provider，由用户准备合法凭据。

## 3. 首次安装

```powershell
git clone https://github.com/ss8875/openalpha-cn.git
Set-Location openalpha-cn
git checkout v1.0.0
docker compose -f deploy/compose.yml config
docker compose -f deploy/compose.yml up -d --build --wait
Invoke-RestMethod http://127.0.0.1:8000/health
```

期望返回：

```json
{"status":"ok","version":"1.0.0"}
```

浏览器访问 `http://127.0.0.1:8000`，OpenAPI 为 `http://127.0.0.1:8000/docs`。

## 4. 配置

| 变量 | 默认值 | 说明 |
|---|---|---|
| `OPENALPHA_PORT` | `8000` | 主机回环端口 |
| `OPENALPHA_RUNTIME_DIR` | `/data` | 容器内持久目录 |
| `OPENALPHA_MAX_REQUEST_BYTES` | `33554432` | 声明的最大请求体（32 MiB），见 §8 |
| `TUSHARE_TOKEN` | 空 | 用户自带 Tushare Token |
| `CHAINLIN_API_BASE_URL` / `CHAINLIN_API_KEY` | 空 | 链邻数据接口地址与密钥 |

`deploy/compose.yml` 会把上述 Provider 凭据变量，以及 `.env.example` 中"Optional model
providers"下列出的模型密钥（`OPENAI_API_KEY` 等），原样透传进容器；容器内未设置时即为空
字符串，`doctor` 据此报告缺失，不会因此报错。

`Dockerfile` 的 `ENV OPENALPHA_HOST=0.0.0.0` / `OPENALPHA_PORT=8000` 现在由容器
`CMD` 实际读取（`--host "${OPENALPHA_HOST:-0.0.0.0}" --port "${OPENALPHA_PORT:-8000}"`），
不再被硬编码的 `--host`/`--port` 参数架空。`deploy/compose.yml` 不在 `environment:` 里
覆盖这两个变量（容器内绑定地址必须固定为 `0.0.0.0`，Docker 的端口映射才能生效；主机侧端口
仍然只由 `ports:` 里的 `OPENALPHA_PORT` 控制），因此这项修复只影响绕过 Compose、直接
`docker run` 该镜像并显式设置 `-e OPENALPHA_PORT=...` 的场景——之前这样做完全无效，现在会
真正生效。

设置方式二选一：

1. 在执行 `docker compose` 的 Shell 中直接导出该变量（PowerShell: `$env:TUSHARE_TOKEN = "..."`；
   bash/zsh: `export TUSHARE_TOKEN=...`），再执行 `docker compose up`；
2. 在 `deploy/` 目录下（与 `compose.yml` 同级）新建 `.env` 文件。Docker Compose 的项目目录
   默认取自 `-f` 指定的第一个 Compose 文件所在目录，因此按“方式二：Python 源码环境”创建在
   仓库根目录的 `.env` **不会**被这里的 `docker compose -f deploy/compose.yml` 自动读取。

真实 Token 不写入 Compose 文件、Git、Issue、日志或截图，也不要提交 `deploy/.env`。

## 5. 数据持久化与恢复

查看卷：

```powershell
docker volume ls --filter name=openalpha
```

普通停止不会删除卷：

```powershell
docker compose -f deploy/compose.yml down
docker compose -f deploy/compose.yml up -d --wait
```

仓库提供自动恢复实测：

```powershell
uv run python scripts/verify_compose_recovery.py
```

脚本使用唯一临时 Compose 项目，写入合成证据、重启、复查同一 `evidence_id`，最后只删除它自己创建的临时容器、网络和卷。

### 备份

建议停写后备份 `/data`。生产备份必须同时包含 SQLite 文件、WAL/SHM（若存在）和 Parquet 目录。恢复时放回同一目录并保持容器用户可写。

## 6. 升级

```powershell
git fetch --tags origin
git checkout v1.0.0
docker compose -f deploy/compose.yml build --pull
docker compose -f deploy/compose.yml up -d --wait
Invoke-RestMethod http://127.0.0.1:8000/health
```

升级前备份卷。v1 不执行破坏性数据库迁移。

## 7. 回滚

触发条件包括健康检查失败、无法读取既有证据、关键 API 5xx 或发现安全问题。

```powershell
git checkout <previous-tag>
docker compose -f deploy/compose.yml up -d --build --wait
```

如果只回滚镜像，不要删除持久卷。若新版本引入不可逆数据迁移，必须按该版本 Release Notes 恢复升级前备份；v1.0.0 没有此类迁移。

## 8. 安全边界

容器使用：

- UID/GID `10001`；
- 只读根文件系统；
- `cap_drop: ALL`；
- `no-new-privileges:true`；
- 仅 `/data` 可持久写入，`/tmp` 为受限 tmpfs；
- CSP、禁止 iframe、MIME 嗅探、Referrer/Permissions/COOP/COEP/CORP/HSTS 响应头，按名**替换**而非追加，
  路由无法给同一个策略头再加一个值（`V2-P5-012`）；
- 32 MiB 请求上限，两道闸：声明了 `Content-Length` 的在读体之前拒，未声明长度的（chunked）边收边计数、
  到顶即停止读取（`V2-P5-012`；此前 chunked 可完全绕过）。**这个数字在 `Dockerfile` 与 `compose.yml`
  的环境变量里被显式设定，也就是说它覆盖 `config.py` 的默认值**：`V2-P4-043` 把默认抬到 32 MiB 时
  只改了 `config.py`，两个容器文件都停在旧值，于是出货容器实测仍以旧上限拒绝一个 `MAX_BATCH_ITEMS`
  批量（9,840,054 字节，正是该行自己的实测）—— `V2-P5-012` 一并修正，并由
  `test_every_deployment_that_sets_the_ceiling_sets_the_one_this_service_declares` 钉住；
- `openalpha serve` 与容器 `CMD` 一样不发 `server:` 头（`V2-P5-012`）；
- CORS 只允许本地 Vite 开发源，方法覆盖 `GET/HEAD/POST/PUT/PATCH/DELETE`，不带凭据（`V2-P5-011`）。

若跨机器开放，必须在反向代理增加 TLS、认证、授权、限流、审计日志和网络 ACL。当前 API 不能裸露到公网。
HSTS 头应用已自己发出，但按 RFC 6797 §7.2，非安全传输下用户代理必须忽略它 —— 换言之它只在反向代理已经终结
TLS 时才生效，代理仍需负责 TLS 本身。

## 9. 监控

最小监控项：

- `/health` 状态与版本；
- 容器重启次数；
- API 4xx/5xx；
- `/data` 磁盘余量；
- SQLite/Parquet 读取失败；
- Provider 认证、限流、上游和新鲜度错误；
- Web 控制台错误。

本地产品不默认发送遥测。

## 10. 发布后检查

1. 匿名 clone 指定 Tag；
2. `docker compose config --quiet`；
3. 构建并等待健康；
4. 打开 Web，完成证据→研究→归因；
5. 执行恢复脚本；
6. 检查安全头和控制台；
7. 验证下载链接与 SHA-256。
