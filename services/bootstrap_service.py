from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from discord.ext import commands

from config.env import EnvSettings
from config.static import APP_NAME, STORAGE_DIR, STORAGE_SUBDIRECTORIES
from db.connection import DatabaseManager
from db.migrations import MigrationReport, apply_migrations
from runtime.cache import RuntimeCacheStore
from runtime.cooldowns import CooldownManager
from runtime.debounce import DebounceManager
from runtime.locks import LockManager
from runtime.scheduler import BackgroundScheduler
from services.draft_timeout_service import DraftTimeoutService
from services.logging_service import LoggingService
from services.sleep_service import SleepService
from services.staff_panel_service import StaffPanelService
from services.transfer_service import TransferService


@dataclass(slots=True)
class BootstrapResources:
    settings: EnvSettings
    database: DatabaseManager
    migration_report: MigrationReport
    logging_service: LoggingService
    scheduler: BackgroundScheduler
    lock_manager: LockManager
    cooldown_manager: CooldownManager
    debounce_manager: DebounceManager
    cache: RuntimeCacheStore
    draft_timeout_service: DraftTimeoutService
    sleep_service: SleepService
    transfer_service: TransferService


class BootstrapService:
    def __init__(self, settings: EnvSettings, bot: commands.Bot | None = None):
        self.settings = settings
        self.bot = bot
        self.resources: BootstrapResources | None = None

        self.database: DatabaseManager | None = None
        self.logging_service: LoggingService | None = None
        self.scheduler: BackgroundScheduler | None = None
        self.lock_manager: LockManager | None = None
        self.cooldown_manager: CooldownManager | None = None
        self.debounce_manager: DebounceManager | None = None
        self.cache: RuntimeCacheStore | None = None
        self.draft_timeout_service: DraftTimeoutService | None = None
        self.sleep_service: SleepService | None = None
        self.transfer_service: TransferService | None = None

    async def bootstrap(self) -> BootstrapResources:
        if self.resources is not None:
            return self.resources

        self._ensure_runtime_directories()
        self.logging_service = LoggingService.create(
            bot=self.bot,
            log_file=self.settings.log_file,
            log_level=self.settings.log_level,
        )
        self.logging_service.log_local_info("Bootstrapping %s...", APP_NAME)

        self.database = DatabaseManager(self.settings.sqlite_path)
        migration_report = apply_migrations(self.database)

        self.lock_manager = LockManager()
        self.cooldown_manager = CooldownManager()
        self.debounce_manager = DebounceManager(self.logging_service.child("debounce"))
        self.cache = RuntimeCacheStore()
        self.draft_timeout_service = DraftTimeoutService(
            self.database,
            bot=self.bot,
            lock_manager=self.lock_manager,
            logger=self.logging_service.child("draft-timeout"),
        )
        staff_panel_service = StaffPanelService(
            self.database,
            bot=self.bot,
            debounce_manager=self.debounce_manager,
        )
        self.sleep_service = SleepService(
            self.database,
            lock_manager=self.lock_manager,
            staff_panel_service=staff_panel_service,
        )
        self.transfer_service = TransferService(
            self.database,
            bot=self.bot,
            lock_manager=self.lock_manager,
            staff_panel_service=staff_panel_service,
            logging_service=self.logging_service,
            logger=self.logging_service.child("transfer"),
        )

        self.scheduler = BackgroundScheduler(
            interval_seconds=self.settings.scheduler_interval_seconds,
            logger=self.logging_service.child("scheduler"),
        )
        self.scheduler.register_handler("runtime.cleanup_locks", self._cleanup_locks)
        self.scheduler.register_handler(
            "runtime.cleanup_cooldowns",
            self._cleanup_cooldowns,
        )
        self.scheduler.register_handler("runtime.cleanup_cache", self._cleanup_cache)
        self.scheduler.register_handler(
            "ticket.draft_timeout_sweep",
            self._run_draft_timeout_sweep,
        )
        self.scheduler.register_handler(
            "ticket.transfer_execute_sweep",
            self._run_transfer_execute_sweep,
        )
        await self.scheduler.start()

        self.resources = BootstrapResources(
            settings=self.settings,
            database=self.database,
            migration_report=migration_report,
            logging_service=self.logging_service,
            scheduler=self.scheduler,
            lock_manager=self.lock_manager,
            cooldown_manager=self.cooldown_manager,
            debounce_manager=self.debounce_manager,
            cache=self.cache,
            draft_timeout_service=self.draft_timeout_service,
            sleep_service=self.sleep_service,
            transfer_service=self.transfer_service,
        )

        self.logging_service.log_local_info(
            "Bootstrap finished. schema=%s applied=%s scheduler_handlers=%s",
            migration_report.final_version,
            migration_report.applied_versions or ["none"],
            self.scheduler.handler_names,
        )
        return self.resources

    async def shutdown(self) -> None:
        if self.logging_service is not None:
            self.logging_service.log_local_info("Shutting down runtime services...")

        if self.scheduler is not None:
            await self.scheduler.shutdown()
            self.scheduler = None

        if self.debounce_manager is not None:
            await self.debounce_manager.shutdown()
            self.debounce_manager = None

        self.resources = None

    def _ensure_runtime_directories(self) -> None:
        self.settings.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings.log_file.parent.mkdir(parents=True, exist_ok=True)
        STORAGE_DIR.mkdir(parents=True, exist_ok=True)
        for directory_name in STORAGE_SUBDIRECTORIES:
            (STORAGE_DIR / directory_name).mkdir(parents=True, exist_ok=True)

    async def _cleanup_locks(self) -> None:
        if self.lock_manager is None or self.logging_service is None:
            return
        removed_count = self.lock_manager.cleanup()
        if removed_count:
            self.logging_service.log_local_debug(
                "Removed %s stale locks from runtime cache.",
                removed_count,
            )

    async def _cleanup_cooldowns(self) -> None:
        if self.cooldown_manager is None or self.logging_service is None:
            return
        removed_count = self.cooldown_manager.sweep()
        if removed_count:
            self.logging_service.log_local_debug(
                "Removed %s expired cooldown entries.",
                removed_count,
            )

    async def _cleanup_cache(self) -> None:
        if self.cache is None or self.logging_service is None:
            return
        removed_count = self.cache.sweep()
        if removed_count:
            self.logging_service.log_local_debug(
                "Removed %s expired cache entries.",
                removed_count,
            )

    async def _run_draft_timeout_sweep(self) -> None:
        if self.draft_timeout_service is None or self.logging_service is None:
            return

        outcomes = await self.draft_timeout_service.sweep_expired_drafts()
        if outcomes:
            self.logging_service.log_local_info(
                "Processed %s expired draft ticket(s).",
                len(outcomes),
            )

    async def _run_transfer_execute_sweep(self) -> None:
        if self.transfer_service is None or self.logging_service is None:
            return

        outcomes = await self.transfer_service.sweep_due_transfers()
        if outcomes:
            self.logging_service.log_local_info(
                "Executed %s due transfer ticket(s).",
                len(outcomes),
            )
