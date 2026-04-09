"""Service layer package.

保持 package init 轻量，避免在导入子模块时触发额外的循环依赖。
需要具体服务时请直接从 `services.<module>` 导入。
"""

__all__: list[str] = []
