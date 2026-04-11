# Odysseia Ticket Bot

Discord 工单管理机器人，基于 [discord.py](https://github.com/Rapptz/discord.py) 构建。覆盖工单完整生命周期：公开面板 -> 草稿 -> 提交（含排队/容量控制）-> 客服工作流（认领、转接、优先级、休眠/唤醒、静音）-> 关闭 -> HTML 转录归档。

## 功能特性

- **工单面板** — 在频道中发布公开面板，用户通过按钮创建工单
- **草稿系统** — 用户先编辑草稿，确认后提交，支持超时自动清理
- **排队与容量控制** — 服务器级 FIFO 队列，客服达到容量上限时自动排队
- **客服工作流** — 认领 (claim)、转接 (transfer)、优先级调整、休眠/唤醒、静音
- **关闭与归档** — 工单关闭后生成 HTML 转录，发送归档频道
- **快照与笔记** — JSONL 格式的工单快照和客服笔记持久化存储
- **故障恢复** — 启动时自动检测并恢复中断的工单状态

## 环境要求

- Python 3.11+
- [uv](https://github.com/astral-sh/uv) — Python 包管理器

## 快速开始

### 1. 克隆仓库

```bash
git clone <repo-url>
cd odysseia-ticket
```

### 2. 安装依赖

```bash
uv sync
```

### 3. 配置环境变量

复制 `.env.example` 并填写：

```bash
cp .env.example .env
```

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `DISCORD_BOT_TOKEN` | Discord Bot Token | (必填) |
| `DISCORD_APP_ID` | Discord Application ID | (必填) |
| `BOT_PREFIX` | 命令前缀 | `!` |
| `SQLITE_PATH` | SQLite 数据库路径 | `data/ticket_bot.sqlite3` |
| `LOG_LEVEL` | 日志级别 | `INFO` |
| `LOG_FILE` | 日志文件路径 | `logs/ticket-bot.log` |
| `SCHEDULER_INTERVAL_SECONDS` | 定时任务间隔 (秒) | `30` |
| `AUTO_SYNC_COMMANDS` | 是否自动同步斜杠命令 | `false` |

### 4. 启动机器人

```bash
uv run python bot.py
```

## 项目结构

```
bot.py                  入口：TicketBot，setup_hook() 触发引导流程
cogs/                   Discord 命令处理器 — 委托给 services，不含业务逻辑
  admin_cog.py            管理员命令
  panel_cog.py            面板管理
  draft_cog.py            草稿操作
  submit_cog.py           提交工单
  staff_cog.py            客服操作（认领、转接、优先级等）
  close_cog.py            关闭工单
  evidence_cog.py         证据/附件管理
discord_ui/             Embed/View 构建器 — 不含业务逻辑
services/               业务逻辑与编排 — 在事务中组合 repositories
db/                     数据库层
  repositories/           数据访问 — SQL + 行到 dataclass 映射
  migrations.py           数据库迁移
core/                   纯定义：常量、枚举、错误、冻结 dataclass
runtime/                内存基础设施：缓存、锁、冷却、防抖、调度器
config/                 环境配置与静态路径
storage/                文件系统层：JSONL 快照/笔记存储
tests/                  测试套件
```

## 工单状态流转

```
DRAFT → QUEUED → SUBMITTED → CLOSING → ARCHIVING → ARCHIVE_SENT → DONE
                     ↕            ↑
                   SLEEP      TRANSFERRING
```

- `DRAFT` — 用户正在编辑草稿
- `QUEUED` — 等待客服容量释放
- `SUBMITTED` — 已提交，等待或正在处理
- `SLEEP` — 休眠中，不占用活跃容量
- `TRANSFERRING` — 转接中
- `CLOSING` — 关闭流程进行中
- `ARCHIVING` / `ARCHIVE_SENT` / `DONE` — 归档阶段

## 开发

### 运行测试

```bash
# 全量测试
uv run pytest -q

# 按模块运行
uv run pytest tests/test_migrations.py
uv run pytest tests/repositories/
uv run pytest tests/services/
uv run pytest tests/runtime/
```

### 代码检查

```bash
uv run ruff check .
uv run ruff check . --fix
```

Ruff 配置：行宽 150，规则集 E / F / B (Bugbear) / PERF / UP (pyupgrade)，忽略 E501。

## 技术栈

- **discord.py** >= 2.4 — Discord API 封装
- **SQLite** (WAL 模式) — 数据持久化，自动迁移
- **python-dotenv** — 环境变量加载
- **pytest** + **pytest-asyncio** — 测试框架
- **ruff** — 代码检查与格式化
- **uv** — 包管理与运行

## 许可证

本项目采用 **GNU Affero General Public License v3.0（AGPL-3.0-only）** 进行许可。
如果你修改本项目并通过网络向其他用户提供服务，AGPL 通常要求你同时向这些用户提供相应修改版本的源代码。
详情请见 [LICENSE](LICENSE)。

## 版权

Copyright (c) 2026 KKTsN

