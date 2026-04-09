class TicketBotError(Exception):
    """项目统一业务异常基类。"""


class ConfigurationError(TicketBotError):
    """配置缺失或配置值非法。"""


class DatabaseMigrationError(TicketBotError):
    """数据库迁移失败。"""


class TicketNotFoundError(TicketBotError):
    """找不到 ticket。"""


class InvalidTicketStateError(TicketBotError):
    """ticket 当前状态不允许执行某操作。"""


class PermissionDeniedError(TicketBotError):
    """调用者没有执行目标操作的权限。"""


class StaleInteractionError(TicketBotError):
    """交互已过期或对应组件已失效。"""
