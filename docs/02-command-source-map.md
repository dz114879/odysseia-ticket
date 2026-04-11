# 命令与源码速查表

快速根据命令 / 按钮 / 事件定位对应的源码文件和 service 调用链。

---

## 1. 命令组结构

所有斜杠命令挂在 `/ticket` 下，子组定义在 `cogs/ticket_command_groups.py`：

```
/ticket                    ticket_group       (行 6)
  /ticket panel ...        panel_group        (行 11)
  /ticket draft ...        draft_group        (行 17)
  /ticket notes ...        notes_group        (行 23)
```

---

## 2. 斜杠命令速查表

### AdminCog — `cogs/admin_cog.py`

| 命令 | 行号 | 说明 | 调用链 |
|------|------|------|--------|
| `/ticket setup` | 27 | 初始化服务器工单配置 | `SetupService.setup_guild()` |

### PanelCog — `cogs/panel_cog.py`

| 命令 | 行号 | 说明 | 调用链 |
|------|------|------|--------|
| `/ticket panel create` | 31 | 在当前频道发送公开面板 | `PanelService.create_panel()` |
| `/ticket panel refresh` | 36 | 刷新服务器的 active panel | `PanelService.refresh_active_panel()` |
| `/ticket panel remove` | 41 | 移除 active panel | `PanelService.remove_active_panel()` |

### DraftCog — `cogs/draft_cog.py`

| 命令 | 行号 | 说明 | 调用链 |
|------|------|------|--------|
| `/ticket draft rename` | 36 | 修改 draft ticket 频道标题 | `DraftService.rename_draft_ticket()` |
| `/ticket draft abandon` | 46 | 废弃 draft 并删除频道 | `DraftService.abandon_draft_ticket()` |

### SubmitCog — `cogs/submit_cog.py`

| 命令 | 行号 | 说明 | 调用链 |
|------|------|------|--------|
| `/ticket submit` | 45 | 提交 draft 给 staff | `SubmissionGuardService.inspect_submission()` &#8594; `SubmitService.submit_draft_ticket()`；若缺标题则弹出 `DraftSubmitTitleModal` |

### CloseCog — `cogs/close_cog.py`

| 命令 | 行号 | 说明 | 调用链 |
|------|------|------|--------|
| `/ticket close` | 47 | 关闭 ticket（staff 直接关，创建者发请求） | staff 路径：`CloseService.initiate_close()` &#8594; `CloseRequestService.dismiss_pending_request()`；创建者路径：`CloseRequestService.request_close()` |
| `/ticket close-cancel` | 57 | 撤销 closing 流程 | `CloseService.revoke_close()` |

### EvidenceCog — `cogs/evidence_cog.py`

| 命令 | 行号 | 说明 | 调用链 |
|------|------|------|--------|
| `/ticket history` | 33 | 查看消息快照时间线 | `TicketAccessService.load_snapshot_context()` &#8594; `SnapshotQueryService.format_message_timeline()` |
| `/ticket recycle-bin` | 39 | 导出已删除消息摘要 | `TicketAccessService.load_snapshot_context()` &#8594; `SnapshotQueryService.build_recycle_bin_text()` |
| `/ticket notes add` | 44 | 添加内部备注 | `TicketAccessService.load_snapshot_context()` &#8594; `NotesService.add_note()` |
| `/ticket notes check` | 50 | 查看内部备注 | `TicketAccessService.load_snapshot_context()` &#8594; `NotesService.format_notes()` |

### StaffCog — `cogs/staff_cog.py`

| 命令 | 行号 | 说明 | 调用链 |
|------|------|------|--------|
| `/ticket claim` | 100 | 认领 ticket | `ClaimService.claim_ticket()` |
| `/ticket unclaim` | 105 | 取消认领 | `ClaimService.unclaim_ticket()` |
| `/ticket transfer-claim` | 110 | 转交认领给另一位 staff | `ClaimService.transfer_claim()` |
| `/ticket mute` | 116 | 禁言 ticket 内成员 | `ModerationService.mute_member()` |
| `/ticket unmute` | 132 | 解除禁言 | `ModerationService.unmute_member()` |
| `/ticket priority` | 138 | 设置优先级 | `PriorityService.set_priority()` |
| `/ticket sleep` | 156 | 挂起 ticket | `SleepService.sleep_ticket()` |
| `/ticket rename` | 161 | 修改 submitted/sleep ticket 标题 | `RenameService.rename_ticket()` |
| `/ticket transfer` | 171 | 发起跨分类转交 | `TransferService.transfer_ticket()` |
| `/ticket untransfer` | 185 | 撤销跨分类转交 | `TransferService.cancel_transfer()` |
| `/ticket help` | 190 | 显示工作流帮助文本 | 无 service 调用，直接调用 `build_ticket_help_message()` |

---

## 3. UI 交互入口

### 公开面板 — `discord_ui/public_panel_view.py`

| 组件 | custom_id 模式 | 行号 | 触发场景 | 调用链 |
|------|----------------|------|----------|--------|
| `PanelCategorySelect` | `panel:create:{guild_id}:{nonce}` | 111 | 用户在公开面板选择分类 | `PanelService.preview_panel_request()` &#8594; 展示确认按钮 |
| `DraftCreateConfirmButton` | `ticket:draft-confirm:{guild_id}:{nonce}:{category_key}` | 35 | 用户确认创建 draft | `PanelService.create_draft_from_panel_request()` |

持久化 View：`PublicPanelView`（行 194，`timeout=None`，启动时通过 `bot._restore_active_panel_views()` 恢复）

### Draft 提交 — `discord_ui/draft_views.py`

| 组件 | custom_id 模式 | 行号 | 触发场景 | 调用链 |
|------|----------------|------|----------|--------|
| `DraftSubmitButton` | `ticket:draft-submit` | 89 | 用户点击 "提交给 Staff" | `SubmissionGuardService.inspect_submission()` &#8594; `SubmitService.submit_draft_ticket()`；缺标题时弹出 Modal |
| `DraftSubmitTitleModal` | （Modal，无 custom_id） | 53 | 补充标题后提交 | `SubmitService.submit_draft_ticket()` |

持久化 View：`DraftWelcomeView`（行 130，`timeout=None`，在 `SubmitCog.__init__` 注册）

### Staff 控制面板 — `discord_ui/staff_panel_view.py`

| 组件 | custom_id 模式 | 行号 | 触发场景 | 调用链 |
|------|----------------|------|----------|--------|
| `StaffClaimButton` | `staff:claim` | 47 | Staff 点击 "认领" | `ClaimService.claim_ticket()` |
| `StaffUnclaimButton` | `staff:unclaim` | 82 | Staff 点击 "取消认领" | `ClaimService.unclaim_ticket()` |
| `StaffHelpButton` | `staff:help` | 117 | Staff 点击 "帮助" | 无 service 调用，直接调用 `build_ticket_help_message()` |
| `StaffPrioritySelect` | `staff:priority` | 137 | Staff 从下拉选择优先级 | `PriorityService.set_priority()` |

持久化 View：`StaffPanelView`（行 175，`timeout=None`，在 `StaffCog.__init__` 注册）

所有面板按钮操作前都会通过 `StaffPanelService.assert_current_panel_interaction()` 进行消息过期检查。

### 关闭请求 — `discord_ui/close_views.py`

| 组件 | 行号 | 触发场景 | 调用链 |
|------|------|----------|--------|
| "同意关闭" 按钮 | 47 | Staff 同意创建者的关闭请求 | `CloseRequestService.approve_request()` |
| "拒绝请求" 按钮 | 78 | Staff 拒绝关闭请求 | `CloseRequestService.reject_request()` |
| `on_timeout` | 37 | 关闭请求超时 | `CloseRequestService.expire_request_message()` |

非持久化 View：`CloseRequestView`（行 17，有 timeout），每次发起关闭请求时创建。

---

## 4. 事件监听器

所有事件处理器定义在 `bot.py` 的 `TicketBot` 类中：

| 事件 | 行号 | 委托方法 |
|------|------|----------|
| `on_ready` | 60 | `DraftTimeoutService.sweep_expired_drafts()` |
| `on_message` | 79 | 依次调用：`SleepService.handle_message()` &#8594; `DraftTimeoutService.handle_message()` &#8594; `SnapshotService.handle_message()` |
| `on_message_edit` | 97 | `SnapshotService.handle_message_edit()` |
| `on_message_delete` | 102 | `SnapshotService.handle_message_delete()` |
| `on_raw_message_edit` | 107 | `SnapshotService.handle_raw_message_edit()` |
| `on_raw_message_delete` | 112 | `SnapshotService.handle_raw_message_delete()` |
| `on_guild_channel_delete` | 117 | `RecoveryService.handle_channel_deleted()` |
| `_on_tree_error` | 128 | 全局 app command 错误处理（非委托，直接处理） |
