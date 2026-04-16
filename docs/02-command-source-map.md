# Command and Source Map

Quickly locate the corresponding source files and service call chains for commands, buttons, and events.

---

## 1. Command Group Structure

All slash commands are mounted under `/ticket`, with subgroups defined in `cogs/ticket_command_groups.py`:

```
/ticket                    ticket_group       (line 6)
  /ticket panel ...        panel_group        (line 11)
  /ticket draft ...        draft_group        (line 17)
  /ticket notes ...        notes_group        (line 23)
```

---

## 2. Slash Command Quick Reference

### AdminCog — `cogs/admin_cog.py`

| Command | Line | Description | Call Chain |
|---------|------|-------------|------------|
| `/ticket setup` | 28 | Initialize the server ticket configuration | `SetupService.setup_guild()` |

### PanelCog — `cogs/panel_cog.py`

| Command | Line | Description | Call Chain |
|---------|------|-------------|------------|
| `/ticket panel create` | 32 | Send the public panel in the current channel | `PanelService.create_panel()` |
| `/ticket panel refresh` | 37 | Refresh the server's active panel | `PanelService.refresh_active_panel()` |
| `/ticket panel remove` | 42 | Remove the active panel | `PanelService.remove_active_panel()` |

### DraftCog — `cogs/draft_cog.py`

| Command | Line | Description | Call Chain |
|---------|------|-------------|------------|
| `/ticket draft rename` | 38 | Change the draft ticket channel title | `DraftService.rename_draft_ticket()` |
| `/ticket draft abandon` | 48 | Abandon the draft and delete the channel | `DraftService.abandon_draft_ticket()` |

### SubmitCog — `cogs/submit_cog.py`

| Command | Line | Description | Call Chain |
|---------|------|-------------|------------|
| `/ticket submit` | 46 | Submit a draft to staff | `SubmissionGuardService.inspect_submission()` &#8594; `SubmitService.submit_draft_ticket()` (post-commit helpers: `SubmitSideEffectsService`, `SubmitWelcomeService`); if the title is missing, `DraftSubmitTitleModal` is shown |

### CloseCog — `cogs/close_cog.py`

| Command | Line | Description | Call Chain |
|---------|------|-------------|------------|
| `/ticket close` | 48 | Close a ticket (staff closes directly, creator sends a request) | staff path: `CloseService.initiate_close()` &#8594; `CloseRequestService.dismiss_pending_request()`; creator path: `CloseRequestService.request_close()` |
| `/ticket close-cancel` | 58 | Revoke the closing flow | `CloseService.revoke_close()` |

### EvidenceCog — `cogs/evidence_cog.py`

| Command | Line | Description | Call Chain |
|---------|------|-------------|------------|
| `/ticket message-history` | 34 | View the message snapshot timeline | `TicketAccessService.load_snapshot_context()` &#8594; `SnapshotQueryService.format_message_timeline()` |
| `/ticket recycle-bin` | 45 | Export a summary of deleted messages | `TicketAccessService.load_snapshot_context()` &#8594; `SnapshotQueryService.build_recycle_bin_text()` |
| `/ticket notes add` | 50 | Add an internal note | `TicketAccessService.load_snapshot_context()` &#8594; `NotesService.add_note()` |
| `/ticket notes check` | 56 | View internal notes | `TicketAccessService.load_snapshot_context()` &#8594; `NotesService.format_notes()` |

### StaffCog — `cogs/staff_cog.py`

| Command | Line | Description | Call Chain |
|---------|------|-------------|------------|
| `/ticket claim` | 111 | Claim a ticket | `ClaimService.claim_ticket()` |
| `/ticket unclaim` | 116 | Unclaim a ticket | `ClaimService.unclaim_ticket()` |
| `/ticket transfer-claim` | 121 | Transfer the claim to another staff member | `ClaimService.transfer_claim()` |
| `/ticket mute` | 127 | Mute a member inside the ticket | `ModerationService.mute_member()` |
| `/ticket unmute` | 143 | Unmute a member | `ModerationService.unmute_member()` |
| `/ticket priority` | 149 | Set the priority | `PriorityService.set_priority()` |
| `/ticket sleep` | 167 | Put a ticket to sleep | `SleepService.sleep_ticket()` |
| `/ticket rename` | 172 | Change the title of a submitted/sleeping ticket | `RenameService.rename_ticket()` |
| `/ticket transfer` | 182 | Initiate a cross-category transfer | `TransferService.transfer_ticket()` |
| `/ticket untransfer` | 229 | Cancel a cross-category transfer | `TransferService.cancel_transfer()` |
| `/ticket help` | 234 | Show the workflow help text | No service call; directly calls `build_ticket_help_message()` |

### PermissionCog — `cogs/permission_cog.py`

| Command | Line | Description | Call Chain |
|---------|------|-------------|------------|
| `/ticket permission` | 35 | Upload a JSON file to configure per-category staff permissions | `PermissionConfigService.validate_permission_json()` &#8594; `PermissionConfigService.apply_permission_config()` &#8594; `PanelService.refresh_active_panel()` |
| `/ticket permission-help` | 45 | Get the permission config help doc and JSON format reference | `PermissionConfigService.build_permission_help_text()` |

### ConfigCog — `cogs/config_cog.py`

| Command | Line | Description | Call Chain |
|---------|------|-------------|------------|
| `/ticket config` | 28 | Open the runtime configuration panel (ephemeral) | Builds `ConfigPanelView` with category select in `discord_ui/config_views.py` &#8594; setting/text modals in the split config modal modules |

---

## 3. UI Interaction Entry Points

### Public Panel — `discord_ui/public_panel_view.py`

| Component | custom_id Pattern | Line | Trigger | Call Chain |
|-----------|-------------------|------|---------|------------|
| `PanelCategorySelect` | `panel:create:{guild_id}:{nonce}` | 131 | User selects a category on the public panel | `PanelService.preview_panel_request()` &#8594; show the confirmation button |
| `DraftCreateConfirmButton` | `ticket:draft-confirm:{guild_id}:{nonce}:{category_key}` | 38 | User confirms draft creation | `PanelService.create_draft_from_panel_request()` |

Persistent view: `PublicPanelView` (line 214, `timeout=None`, restored on startup via `bot._restore_active_panel_views()`)

### Draft Submission — `discord_ui/draft_views.py`

| Component | custom_id Pattern | Line | Trigger | Call Chain |
|-----------|-------------------|------|---------|------------|
| `DraftSubmitButton` | `ticket:draft-submit` | 93 | User clicks "Submit to Staff" | `SubmissionGuardService.inspect_submission()` &#8594; `SubmitService.submit_draft_ticket()`; if the title is missing, a modal is shown |
| `DraftSubmitTitleModal` | (Modal, no custom_id) | 57 | Submit after providing a title | `SubmitService.submit_draft_ticket()` (`SubmitWelcomeService` + `SubmitSideEffectsService` run inside the orchestration path) |

Persistent view: `DraftWelcomeView` (line 196, `timeout=None`, registered in `SubmitCog.__init__`)

### Staff Control Panel — `discord_ui/staff_panel_view.py`

| Component | custom_id Pattern | Line | Trigger | Call Chain |
|-----------|-------------------|------|---------|------------|
| `StaffClaimButton` | `staff:claim` | 56 | Staff clicks "Claim" | `ClaimService.claim_ticket()` |
| `StaffUnclaimButton` | `staff:unclaim` | 91 | Staff clicks "Unclaim" | `ClaimService.unclaim_ticket()` |
| `StaffSleepButton` | `staff:sleep` | 126 | Staff clicks "Sleep" | `SleepService.sleep_ticket()` |
| `StaffCloseButton` | `staff:close` | 161 | Staff clicks "Close" | `CloseService.initiate_close()` |
| `StaffRenameButton` | `staff:rename` | 231 | Staff clicks "Rename" | Opens `StaffRenameModal` |
| `StaffRenameModal` | (Modal) | 196 | Staff submits a new title | `RenameService.rename_ticket()` |
| `StaffPrioritySelect` | `staff:priority` | 252 | Staff selects a priority from the dropdown | `PriorityService.set_priority()` |

Persistent view: `StaffPanelView` (line 290, `timeout=None`, registered in `StaffCog.__init__`)

All panel button actions are checked for message staleness through `StaffPanelService.assert_current_panel_interaction()` before execution.

### Close Request — `discord_ui/close_views.py`

| Component | Line | Trigger | Call Chain |
|-----------|------|---------|------------|
| "Revoke Close" button | 44 | Staff revokes an in-progress closing notice | `CloseService.revoke_close()` |
| "Approve Close" button | 103 | Staff approves the creator's close request | `CloseRequestService.approve_request()` |
| "Reject Request" button | 134 | Staff rejects the close request | `CloseRequestService.reject_request()` |
| `CloseRequestView.on_timeout` | 92 | Close request times out | `CloseRequestService.expire_request_message()` |

Non-persistent views: `ClosingNoticeView` (line 18, revoke window) and `CloseRequestView` (line 72, request approval window), both created on demand.

### Runtime Config — `discord_ui/config_views.py`, `discord_ui/config_setting_modals.py`, `discord_ui/config_text_modals.py`

Entry selectors stay in `discord_ui/config_views.py`; numeric/time-based modal submit handlers live in `discord_ui/config_setting_modals.py`; text modal submit handlers live in `discord_ui/config_text_modals.py`; shared submit plumbing lives in `discord_ui/config_modal_shared.py`.

| Component | Line | Trigger | Call Chain |
|-----------|------|---------|------------|
| `ConfigCategorySelect` | 20 | Admin selects a setting category | Opens the corresponding modal (basic / draft / close / snapshot) or shows `TextGroupView` for text settings |
| `ConfigPanelView` | 58 | Container for `ConfigCategorySelect` | Ephemeral, timeout=300 |
| `TextGroupSelect` | 64 | Admin selects a text group | Opens the corresponding text modal (panel / draft welcome / snapshot) |
| `TextGroupView` | 88 | Container for `TextGroupSelect` | Ephemeral, timeout=300 |
| `BasicSettingsModal` | 51 | Admin edits timezone / max tickets and chooses claim mode / download window from selects | `load_config_for_submit()` &#8594; `validate_basic_settings()` &#8594; `apply_config_updates()` &#8594; `GuildRepository.update_config()` |
| `DraftTimeoutModal` | 106 | Admin edits inactive close / abandon timeout hours | `load_config_for_submit()` &#8594; `validate_draft_timeouts()` &#8594; `apply_config_updates()` &#8594; `GuildRepository.update_config()` |
| `CloseTransferModal` | 140 | Admin edits transfer delay, close revoke window, close request timeout | `load_config_for_submit()` &#8594; `validate_close_transfer()` &#8594; `apply_config_updates()` &#8594; `GuildRepository.update_config()` |
| `SnapshotLimitsModal` | 177 | Admin edits snapshot warning threshold / limit | `load_config_for_submit()` &#8594; `validate_snapshot_limits()` &#8594; `apply_config_updates()` &#8594; `GuildRepository.update_config()` |
| `PanelTextModal` | 23 | Admin edits panel title / merged body / footer | `load_config_for_submit()` &#8594; `validate_text_fields()` &#8594; `apply_config_updates()` &#8594; `GuildRepository.update_config()` &#8594; `PanelService.refresh_active_panel()` |
| `DraftWelcomeTextModal` | 70 | Admin edits draft welcome text | `load_config_for_submit()` &#8594; `validate_text_fields()` &#8594; `apply_config_updates()` &#8594; `GuildRepository.update_config()` |
| `SnapshotTextModal` | 101 | Admin edits snapshot warning / limit text | `load_config_for_submit()` &#8594; `validate_text_fields()` &#8594; `apply_config_updates()` &#8594; `GuildRepository.update_config()` |

Non-persistent views: `ConfigPanelView` and `TextGroupView` (both timeout=300s), created per `/ticket config` invocation.

---

## 4. Event Listeners

All event handlers are defined in the `TicketBot` class in `bot.py`:

| Event | Line | Delegated Method |
|-------|------|------------------|
| `on_ready` | 61 | `DraftTimeoutService.sweep_expired_drafts()` |
| `on_message` | 80 | Called in order: `SleepService.handle_message()` &#8594; `DraftTimeoutService.handle_message()` &#8594; `SnapshotService.handle_message()` |
| `on_message_edit` | 106 | `SnapshotService.handle_message_edit()` |
| `on_message_delete` | 111 | `SnapshotService.handle_message_delete()` |
| `on_raw_message_edit` | 116 | `SnapshotService.handle_raw_message_edit()` |
| `on_raw_message_delete` | 121 | `SnapshotService.handle_raw_message_delete()` |
| `on_guild_channel_delete` | 126 | `RecoveryService.handle_channel_deleted()` |
| `_on_tree_error` | 137 | Global app command error handling (not delegated; handled directly) |
