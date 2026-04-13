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
        lines.append("=" * 60)
        lines.append("Odysseia Ticket Bot — 权限配置帮助")
        lines.append("=" * 60)
        lines.append("")

        lines.append("## 当前服务器信息")
        lines.append(f"  admin_role_id: {config.admin_role_id or '未设置'}")
        lines.append("  （拥有此角色的成员自动成为所有分类的管理员，无需在 JSON 中重复配置）")
        lines.append("")

        lines.append("## 当前 Ticket 分类")
        for cat in categories:
            role_ids = json.loads(cat.staff_role_ids_json or "[]")
            user_ids = json.loads(cat.staff_user_ids_json or "[]")
            lines.append(f"  - category_key: \"{cat.category_key}\"")
            lines.append(f"    display_name: \"{cat.display_name}\"")
            lines.append(f"    当前 staff_role_ids: {role_ids}")
            lines.append(f"    当前 staff_user_ids: {user_ids}")
            lines.append("")

        lines.append("## JSON 格式说明")
        lines.append("上传的 JSON 文件格式如下：")
        lines.append("")
        lines.append("{")
        lines.append('  "categories": {')
        if categories:
            first = categories[0]
            lines.append(f'    "{first.category_key}": {{')
            lines.append('      "staff_role_ids": [角色ID1, 角色ID2],')
            lines.append('      "staff_user_ids": [用户ID1]')
            lines.append("    }")
        lines.append("  }")
        lines.append("}")
        lines.append("")

        lines.append("## 字段说明")
        lines.append("  - staff_role_ids: 该分类的 staff 角色 ID 列表（数组），拥有这些角色的成员可以查看和处理该分类下的 ticket")
        lines.append("  - staff_user_ids: 该分类的 staff 用户 ID 列表（数组），这些用户可以查看和处理该分类下的 ticket")
        lines.append("  - 只需包含要修改的分类，未包含的分类保持原配置不变")
        lines.append("  - 一个角色可以出现在多个分类中（该角色成员可以看到所有对应分类的 ticket）")
        lines.append("")

        lines.append("## 如何获取 Discord ID")
        lines.append("  1. 打开 Discord 设置 → 高级 → 开启「开发者模式」")
        lines.append("  2. 右键点击角色/用户 → 复制 ID")
        lines.append("")

        lines.append("## AI 提示词模板")
        lines.append("（将以下内容连同你的需求一起发送给 AI，让它帮你生成 JSON）")
        lines.append("")
        lines.append("--- 提示词开始 ---")
        lines.append("")
        lines.append("[在此填写提示词内容]")
        lines.append("")
        lines.append("--- 提示词结束 ---")
        lines.append("")

        return "\n".join(lines)
