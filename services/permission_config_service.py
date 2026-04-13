from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from core.models import GuildConfigRecord, TicketCategoryConfig
from db.connection import DatabaseManager
from db.repositories.guild_repository import GuildRepository


@dataclass(frozen=True, slots=True)
class PermissionApplyResult:
    updated_categories: list[str]
    skipped_categories: list[str]
    summary_lines: list[str]


class PermissionConfigService:
    def __init__(
        self,
        database: DatabaseManager,
        *,
        guild_repository: GuildRepository | None = None,
    ) -> None:
        self.database = database
        self.guild_repository = guild_repository or GuildRepository(database)

    def validate_permission_json(
        self,
        data: Any,
        *,
        guild: Any,
        categories: list[TicketCategoryConfig],
    ) -> list[str]:
        errors: list[str] = []

        if not isinstance(data, dict):
            return ["JSON 根元素必须是对象。"]

        cat_data = data.get("categories")
        if not isinstance(cat_data, dict):
            return ["缺少 \"categories\" 字段或其值不是对象。"]

        valid_keys = {c.category_key for c in categories}

        for category_key, entry in cat_data.items():
            if category_key not in valid_keys:
                errors.append(f"分类 \"{category_key}\" 不存在。可用分类：{', '.join(sorted(valid_keys))}")
                continue

            if not isinstance(entry, dict):
                errors.append(f"分类 \"{category_key}\" 的值必须是对象。")
                continue

            staff_role_ids = entry.get("staff_role_ids", [])
            if not isinstance(staff_role_ids, list):
                errors.append(f"分类 \"{category_key}\".staff_role_ids 必须是数组。")
            else:
                for i, role_id in enumerate(staff_role_ids):
                    if not isinstance(role_id, int):
                        errors.append(f"分类 \"{category_key}\".staff_role_ids[{i}] 必须是整数。")
                    elif guild is not None:
                        role = getattr(guild, "get_role", lambda _: None)(role_id)
                        if role is None:
                            errors.append(f"分类 \"{category_key}\".staff_role_ids[{i}]：角色 {role_id} 在服务器中不存在。")

            staff_user_ids = entry.get("staff_user_ids", [])
            if not isinstance(staff_user_ids, list):
                errors.append(f"分类 \"{category_key}\".staff_user_ids 必须是数组。")
            else:
                for i, user_id in enumerate(staff_user_ids):
                    if not isinstance(user_id, int):
                        errors.append(f"分类 \"{category_key}\".staff_user_ids[{i}] 必须是整数。")

        return errors

    def apply_permission_config(
        self,
        guild_id: int,
        data: dict[str, Any],
        *,
        connection: sqlite3.Connection | None = None,
    ) -> PermissionApplyResult:
        cat_data: dict[str, Any] = data.get("categories", {})
        updated: list[str] = []
        skipped: list[str] = []
        summary: list[str] = []

        for category_key, entry in cat_data.items():
            category = self.guild_repository.get_category(guild_id, category_key, connection=connection)
            if category is None:
                skipped.append(category_key)
                continue

            staff_role_ids = entry.get("staff_role_ids", [])
            staff_user_ids = entry.get("staff_user_ids", [])
            new_role_ids_json = json.dumps(staff_role_ids)
            new_user_ids_json = json.dumps(staff_user_ids)

            from dataclasses import replace
            updated_category = replace(
                category,
                staff_role_ids_json=new_role_ids_json,
                staff_user_ids_json=new_user_ids_json,
            )
            self.guild_repository.upsert_category(updated_category, connection=connection)
            updated.append(category_key)
            summary.append(
                f"  {category_key}：role_ids={staff_role_ids}, user_ids={staff_user_ids}"
            )

        return PermissionApplyResult(
            updated_categories=updated,
            skipped_categories=skipped,
            summary_lines=summary,
        )

    @staticmethod
    def build_permission_help_text(
        config: GuildConfigRecord,
        categories: list[TicketCategoryConfig],
    ) -> str:
        lines: list[str] = []

        # ── Part 1: 系统指令 ──
        lines.append("# 任务")
        lines.append("")
        lines.append("根据文末「用户指令」部分的要求，生成一份符合下方 Schema 的 JSON。")
        lines.append("")
        lines.append("## 输出要求")
        lines.append("- 只输出纯 JSON，不要用 Markdown 代码块包裹，不要附加任何解释文字。JSON 必须可以直接保存为符合格式要求的 .json 文件")
        lines.append("- 只包含需要变更的分类；未提及的分类不要出现在输出中（它们会保持原配置不变）")
        lines.append("- 如果用户指令没有明确提到某个字段，保持该字段的当前值不变（从「当前配置」中复制）")
        lines.append("")

        # ── Part 2: 上下文信息 ──
        lines.append("# 上下文信息")
        lines.append("")

        lines.append("## 全局管理角色")
        lines.append(f"admin_role_id: {config.admin_role_id or '未设置'}")
        lines.append("说明：拥有此角色的成员自动成为所有分类的管理员，无需在 JSON 中配置。")
        lines.append("")

        lines.append("## 当前 Ticket 分类及其 Staff 配置")
        if not categories:
            lines.append("（暂无分类）")
        for cat in categories:
            role_ids = json.loads(cat.staff_role_ids_json or "[]")
            user_ids = json.loads(cat.staff_user_ids_json or "[]")
            lines.append(f"- category_key: \"{cat.category_key}\"")
            lines.append(f"  display_name: \"{cat.display_name}\"")
            lines.append(f"  当前 staff_role_ids: {role_ids}")
            lines.append(f"  当前 staff_user_ids: {user_ids}")
        lines.append("")

        lines.append("## JSON Schema")
        lines.append("")
        lines.append("{")
        lines.append('  "categories": {')
        lines.append('    "<category_key>": {')
        lines.append('      "staff_role_ids": [整数, ...],')
        lines.append('      "staff_user_ids": [整数, ...]')
        lines.append("    }")
        lines.append("  }")
        lines.append("}")
        lines.append("")

        lines.append("## 字段说明")
        lines.append("- category_key: 分类的唯一标识符，必须与上方「当前 Ticket 分类」中列出的 category_key 完全一致。")
        lines.append("- staff_role_ids: 该分类的 staff 身份组 ID 列表（整数数组）。拥有这些身份组的成员可以查看和处理该分类下的 ticket。")
        lines.append("- staff_user_ids: 该分类的 staff 用户 ID 列表（整数数组）。这些用户可以查看和处理该分类下的 ticket。")
        lines.append("- 一个身份组/用户可以出现在多个分类中。")
        lines.append("")

        # ── Part 3: 用户指令区 ──
        lines.append("# 用户指令")
        lines.append("")
        lines.append("（在三横杠分隔线下方，写出你的配置需求。示例：）")
        lines.append("- 把身份组 123456789 设为「答题处罚」分类的 staff")
        lines.append("- 把用户 987654321 单独加到「申诉」分类的 staff")
        lines.append("- 移除「闲聊」分类的所有 staff 身份组")
        lines.append("## 如何获取 Discord ID")
        lines.append("1. 打开 Discord 设置 → 高级 → 开启「开发者模式」")
        lines.append("2. 右键点击身份组/用户 → 复制 ID（手机端：单击用户信息面板里的身份组即可复制）")
        lines.append("实际发给AI时，删除上方从'（在三横杠'到'即可复制）'的部分。")
        lines.append("")
        lines.append("---")
        lines.append("")

        return "\n".join(lines)
