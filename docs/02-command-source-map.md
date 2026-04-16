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
| `/ticket setup` | 27 | Initialize the server ticket configuration | `SetupService.setup_guild()` |

### PanelCog — `cogs/panel_cog.py`

| Command | Line | Description | Call Chain |
|---------|------|-------------|------------|
| `/ticket panel create` | 31 | Send the public panel in the current channel | `PanelService.create_panel()` |
| `/ticket panel refresh` | 36 | Refresh the server's active panel | `PanelService.refresh_active_panel()` |
| `/ticket panel remove` | 41 | Remove the active panel | `PanelService.remove_active_panel()` |

### DraftCog — `cogs/draft_cog.py`

| Command | Line | Description | Call Chain |
|---------|------|-------------|------------|
| `/ticket draft rename` | 36 | Change the draft ticket channel title | `DraftService.rename_draft_ticket()` |
| `/ticket draft abandon` | 46 | Abandon the draft and delete the channel | `DraftService.abandon_draft_ticket()` |

### SubmitCog — `cogs/submit_cog.py`

| Command | Line | Description | Call Chain |
|---------|------|-------------|------------|
| `/ticket submit` | 45 | Submit a draft to staff | `SubmissionGuardService.inspect_submission()` &#8594; `SubmitService.submit_draft_ticket()`; if the title is missing, `DraftSubmitTitleModal` is shown |

### CloseCog — `cogs/close_cog.py`

| Command | Line | Description | Call Chain |
|---------|------|-------------|------------|
| `/ticket close` | 47 | Close a ticket (staff closes directly, creator sends a request) | staff path: `CloseService.initiate_close()` &#8594; `CloseRequestService.dismiss_pending_request()`; creator path: `CloseRequestService.request_close()` |
| `/ticket close-cancel` | 57 | Revoke the closing flow | `CloseService.revoke_close()` |

### EvidenceCog — `cogs/evidence_cog.py`

| Command | Line | Description | Call Chain |
|---------|------|-------------|------------|
| `/ticket history` | 33 | View the message snapshot timeline | `TicketAccessService.load_snapshot_context()` &#8594; `SnapshotQueryService.format_message_timeline()` |
| `/ticket recycle-bin` | 39 | Export a summary of deleted messages | `TicketAccessService.load_snapshot_context()` &#8594; `SnapshotQueryService.build_recycle_bin_text()` |
| `/ticket notes add` | 44 | Add an internal note | `TicketAccessService.load_snapshot_context()` &#8594; `NotesService.add_note()` |
| `/ticket notes check` | 50 | View internal notes | `TicketAccessService.load_snapshot_context()` &#8594; `NotesService.format_notes()` |

### StaffCog — `cogs/staff_cog.py`

| Command | Line | Description | Call Chain |
|---------|------|-------------|------------|
| `/ticket claim` | 100 | Claim a ticket | `ClaimService.claim_ticket()` |
| `/ticket unclaim` | 105 | Unclaim a ticket | `ClaimService.unclaim_ticket()` |
| `/ticket transfer-claim` | 110 | Transfer the claim to another staff member | `ClaimService.transfer_claim()` |
| `/ticket mute` | 116 | Mute a member inside the ticket | `ModerationService.mute_member()` |
| `/ticket unmute` | 132 | Unmute a member | `ModerationService.unmute_member()` |
| `/ticket priority` | 138 | Set the priority | `PriorityService.set_priority()` |
| `/ticket sleep` | 156 | Put a ticket to sleep | `SleepService.sleep_ticket()` |
| `/ticket rename` | 161 | Change the title of a submitted/sleeping ticket | `RenameService.rename_ticket()` |
| `/ticket transfer` | 171 | Initiate a cross-category transfer | `TransferService.transfer_ticket()` |
| `/ticket untransfer` | 185 | Cancel a cross-category transfer | `TransferService.cancel_transfer()` |
| `/ticket help` | 190 | Show the workflow help text | No service call; directly calls `build_ticket_help_message()` |

### PermissionCog — `cogs/permission_cog.py`

| Command | Line | Description | Call Chain |
|---------|------|-------------|------------|
| `/ticket permission` | 34 | Upload a JSON file to configure per-category staff permissions | `PermissionConfigService.validate_permission_json()` &#8594; `PermissionConfigService.apply_permission_config()` &#8594; `PanelService.refresh_active_panel()` |
| `/ticket permission-help` | 44 | Get the permission config help doc and JSON format reference | `PermissionConfigService.build_permission_help_text()` |

### ConfigCog — `cogs/config_cog.py`

| Command | Line | Description | Call Chain |
|---------|------|-------------|------------|
| `/ticket config` | 27 | Open the runtime configuration panel (ephemeral) | Builds `ConfigPanelView` with category select &#8594; Modals for each setting group |

---

## 3. UI Interaction Entry Points

### Public Panel — `discord_ui/public_panel_view.py`

| Component | custom_id Pattern | Line | Trigger | Call Chain |
|-----------|-------------------|------|---------|------------|
| `PanelCategorySelect` | `panel:create:{guild_id}:{nonce}` | 111 | User selects a category on the public panel | `PanelService.preview_panel_request()` &#8594; show the confirmation button |
| `DraftCreateConfirmButton` | `ticket:draft-confirm:{guild_id}:{nonce}:{category_key}` | 35 | User confirms draft creation | `PanelService.create_draft_from_panel_request()` |

Persistent view: `PublicPanelView` (line 194, `timeout=None`, restored on startup via `bot._restore_active_panel_views()`)

### Draft Submission — `discord_ui/draft_views.py`

| Component | custom_id Pattern | Line | Trigger | Call Chain |
|-----------|-------------------|------|---------|------------|
| `DraftSubmitButton` | `ticket:draft-submit` | 89 | User clicks "Submit to Staff" | `SubmissionGuardService.inspect_submission()` &#8594; `SubmitService.submit_draft_ticket()`; if the title is missing, a modal is shown |
| `DraftSubmitTitleModal` | (Modal, no custom_id) | 53 | Submit after providing a title | `SubmitService.submit_draft_ticket()` |

Persistent view: `DraftWelcomeView` (line 130, `timeout=None`, registered in `SubmitCog.__init__`)

### Staff Control Panel — `discord_ui/staff_panel_view.py`

| Component | custom_id Pattern | Line | Trigger | Call Chain |
|-----------|-------------------|------|---------|------------|
| `StaffClaimButton` | `staff:claim` | 55 | Staff clicks "Claim" | `ClaimService.claim_ticket()` |
| `StaffUnclaimButton` | `staff:unclaim` | 90 | Staff clicks "Unclaim" | `ClaimService.unclaim_ticket()` |
| `StaffSleepButton` | `staff:sleep` | 125 | Staff clicks "Sleep" | `SleepService.sleep_ticket()` |
| `StaffCloseButton` | `staff:close` | 160 | Staff clicks "Close" | `CloseService.initiate_close()` |
| `StaffRenameButton` | `staff:rename` | 230 | Staff clicks "Rename" | Opens `StaffRenameModal` |
| `StaffRenameModal` | (Modal) | 195 | Staff submits a new title | `RenameService.rename_ticket()` |
| `StaffPrioritySelect` | `staff:priority` | 251 | Staff selects a priority from the dropdown | `PriorityService.set_priority()` |

Persistent view: `StaffPanelView` (line 289, `timeout=None`, registered in `StaffCog.__init__`)

All panel button actions are checked for message staleness through `StaffPanelService.assert_current_panel_interaction()` before execution.

### Close Request — `discord_ui/close_views.py`

| Component | Line | Trigger | Call Chain |
|-----------|------|---------|------------|
| "Approve Close" button | 47 | Staff approves the creator's close request | `CloseRequestService.approve_request()` |
| "Reject Request" button | 78 | Staff rejects the close request | `CloseRequestService.reject_request()` |
| `on_timeout` | 37 | Close request times out | `CloseRequestService.expire_request_message()` |

Non-persistent view: `CloseRequestView` (line 17, has a timeout), created each time a close request is initiated.

### Runtime Config — `discord_ui/config_views.py`

| Component | Line | Trigger | Call Chain |
|-----------|------|---------|------------|
| `ConfigCategorySelect` | 154 | Admin selects a setting category | Opens the corresponding Modal (basic / draft / close / snapshot) or shows `TextGroupView` for text settings |
| `ConfigPanelView` | 192 | Container for `ConfigCategorySelect` | Ephemeral, timeout=300 |
| `TextGroupSelect` | 201 | Admin selects a text group | Opens the corresponding text Modal (panel / draft welcome / snapshot) |
| `TextGroupView` | 225 | Container for `TextGroupSelect` | Ephemeral, timeout=300 |
| `BasicSettingsModal` | 268 | Admin edits timezone / max tickets and chooses claim mode / download window from selects | `validate_basic_settings()` &#8594; `GuildRepository.update_config()` |
| `DraftTimeoutModal` | 336 | Admin edits inactive close / abandon timeout hours | `validate_draft_timeouts()` &#8594; `GuildRepository.update_config()` |
| `CloseTransferModal` | 369 | Admin edits transfer delay, close revoke window, close request timeout | `validate_close_transfer()` &#8594; `GuildRepository.update_config()` |
| `SnapshotLimitsModal` | 405 | Admin edits snapshot warning threshold / limit | `validate_snapshot_limits()` &#8594; `GuildRepository.update_config()` |
| `PanelTextModal` | 441 | Admin edits panel title / merged body / footer | `validate_text_fields()` &#8594; `GuildRepository.update_config()` &#8594; `PanelService.refresh_active_panel()` |
| `DraftWelcomeTextModal` | 500 | Admin edits draft welcome text | `validate_text_fields()` &#8594; current effective text compare &#8594; `GuildRepository.update_config()` |
| `SnapshotTextModal` | 536 | Admin edits snapshot warning / limit text | `validate_text_fields()` &#8594; current effective text compare &#8594; `GuildRepository.update_config()` |

Non-persistent views: `ConfigPanelView` and `TextGroupView` (both timeout=300s), created per `/ticket config` invocation.

---

## 4. Event Listeners

All event handlers are defined in the `TicketBot` class in `bot.py`:

| Event | Line | Delegated Method |
|-------|------|------------------|
| `on_ready` | 60 | `DraftTimeoutService.sweep_expired_drafts()` |
| `on_message` | 79 | Called in order: `SleepService.handle_message()` &#8594; `DraftTimeoutService.handle_message()` &#8594; `SnapshotService.handle_message()` |
| `on_message_edit` | 97 | `SnapshotService.handle_message_edit()` |
| `on_message_delete` | 102 | `SnapshotService.handle_message_delete()` |
| `on_raw_message_edit` | 107 | `SnapshotService.handle_raw_message_edit()` |
| `on_raw_message_delete` | 112 | `SnapshotService.handle_raw_message_delete()` |
| `on_guild_channel_delete` | 117 | `RecoveryService.handle_channel_deleted()` |
| `_on_tree_error` | 128 | Global app command error handling (not delegated; handled directly) |
