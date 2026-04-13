  ---
  问题 — 多 Staff 角色

  问题： 每个分类只能配置一个 staff_role_id（单个 int），不支持多个身份组对应一个分类。

  涉及文件：
  - core/models.py — TicketCategoryConfig.staff_role_id: int | None（需改为列表）
  - db/migrations.py — 需新增迁移，加 staff_role_ids_json TEXT 列
  - db/repositories/guild_repository.py — 读写 staff_role_id 的所有 SQL
  - services/staff_permission_service.py — resolve_staff_targets() 用 category.staff_role_id
  - services/staff_guard_service.py — is_staff_actor() 检查 staff_role_id
  - services/creation_service.py — _build_draft_overwrites() 用 staff_role_id
  - config/defaults.py — 默认模板的 staff_role_id=None
  - services/guild_config_service.py — build_default_categories()
  - 约 15+ 个测试文件引用 staff_role_id

  目标： staff_role_id → staff_role_ids_json（JSON 数组，和现有的 staff_user_ids_json 同模式），支持每分类配置 0~N 个
  Staff 角色。

  大致改法：
  1. 新增 DB 迁移（V12）：ALTER TABLE ticket_categories ADD COLUMN staff_role_ids_json TEXT NOT NULL DEFAULT
  '[]'，用一段数据迁移把旧 staff_role_id 的值写入新列。
  1. TicketCategoryConfig 字段从 staff_role_id: int | None 改为 staff_role_ids_json: str = "[]"。
  2. 所有 guild.get_role(category.staff_role_id) 单值调用 → 遍历 JSON 解析出的列表，逐个 get_role()。
  3. is_staff_actor() 中从 staff_role_id in actor_role_ids 改为 any(rid in actor_role_ids for rid in parsed_list)。
  4. 全量更新测试中的 staff_role_id=... 为 staff_role_ids_json="[...]"。

  ---
  问题 — 运行时控制面板

  问题： 缺少运行时配置能力。timezone、claim_mode、max_open_tickets 等字段虽然在 DB 里有，但 /ticket setup
  没有暴露这些参数（全用硬编码默认值）。面板文案（title/description/footer）完全是 Python 常量，没有 DB
  列。没有任何命令可以编辑分类配置。

  涉及文件：
  - cogs/admin_cog.py — 现有 /ticket setup 只接受 4 个参数
  - cogs/ticket_command_groups.py — 需新增 config 子命令组
  - 需新增 cogs/config_cog.py — 配置命令 handler
  - core/models.py — GuildConfigRecord 可能需要加面板文案字段
  - db/migrations.py — 新增面板文案列的迁移
  - db/repositories/guild_repository.py — 读写新字段
  - config/defaults.py — 面板文案常量变为 fallback 默认值
  - discord_ui/panel_embeds.py — build_public_panel_embed() 需接受 config 参数

  目标： 提供 /ticket config 系列命令（或按钮+Modal
  UI），允许管理员在运行时修改：timezone、claim_mode、max_open_tickets、enable_download_window、面板文案、分类的
  enable/disable/编辑。

  大致改法：
  1. 先把 /ticket setup 已有但未暴露的参数（timezone、claim_mode、max_open_tickets）直接加到 setup 命令或独立的 config
  命令。
  2. 面板文案需 DB 迁移加列 → panel_title、panel_description 等，null 时 fallback 到 config/defaults.py 常量。
  3. 分类编辑需要 /ticket config category edit <key> 命令或 Modal UI。
  4. 这是工作量最大的问题，且依赖问题 1（多 Staff 角色）先就位，否则分类编辑只能改一个角色。