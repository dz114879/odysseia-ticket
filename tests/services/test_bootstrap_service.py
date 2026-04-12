from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

import services.bootstrap_service as bootstrap_module
from core.constants import CURRENT_SCHEMA_VERSION
from services.bootstrap_service import BootstrapService


class FakeLoggingService:
    def __init__(self) -> None:
        self.info_messages: list[str] = []
        self.debug_messages: list[str] = []

    def child(self, name: str) -> logging.Logger:
        return logging.getLogger(f"tests.bootstrap.{name}")

    def log_local_info(self, message: str, *args, **kwargs) -> None:
        rendered = message % args if args else message
        self.info_messages.append(rendered)

    def log_local_debug(self, message: str, *args, **kwargs) -> None:
        rendered = message % args if args else message
        self.debug_messages.append(rendered)

    async def send_guild_log(self, *args, **kwargs) -> bool:
        return False

    async def send_ticket_log(self, *args, **kwargs) -> bool:
        return False


@pytest.mark.asyncio
async def test_bootstrap_creates_resources_and_registers_scheduler_handlers(
    make_settings,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    fake_logging_service = FakeLoggingService()
    create_mock = MagicMock(return_value=fake_logging_service)
    start_mock = AsyncMock()
    recover_mock = AsyncMock(return_value=[])

    monkeypatch.setattr(bootstrap_module.LoggingService, "create", staticmethod(create_mock))
    monkeypatch.setattr(bootstrap_module.BackgroundScheduler, "start", start_mock)
    monkeypatch.setattr(bootstrap_module.RecoveryService, "recover_incomplete_archive_flows", recover_mock)
    monkeypatch.setattr(bootstrap_module, "STORAGE_DIR", tmp_path / "storage")
    monkeypatch.setattr(
        bootstrap_module,
        "STORAGE_SUBDIRECTORIES",
        ("snapshots", "notes", "archives"),
    )

    settings = make_settings(scheduler_interval_seconds=12)
    service = BootstrapService(settings=settings)

    resources = await service.bootstrap()
    second_bootstrap = await service.bootstrap()

    assert second_bootstrap is resources
    assert create_mock.call_count == 1
    assert start_mock.await_count == 1
    assert settings.sqlite_path.parent.exists() is True
    assert settings.log_file.parent.exists() is True
    assert resources.database.database_path == settings.sqlite_path
    assert resources.migration_report.final_version == CURRENT_SCHEMA_VERSION
    assert resources.scheduler.interval_seconds == 12
    assert resources.scheduler.handler_names == [
        "runtime.cleanup_locks",
        "runtime.cleanup_cooldowns",
        "runtime.cleanup_cache",
        "ticket.draft_timeout_sweep",
        "ticket.draft_warning_sweep",
        "ticket.transfer_execute_sweep",
        "ticket.mute_expire_sweep",
        "ticket.archive_recovery_sweep",
        "ticket.queue_sweep",
    ]
    assert (bootstrap_module.STORAGE_DIR / "snapshots").exists() is True
    assert recover_mock.await_count == 1
    assert (bootstrap_module.STORAGE_DIR / "notes").exists() is True
    assert (bootstrap_module.STORAGE_DIR / "archives").exists() is True
    assert resources.draft_timeout_service is not None
    assert resources.capacity_service is not None
    assert resources.queue_service is not None
    assert resources.sleep_service is not None
    assert resources.moderation_service is not None
    assert resources.transfer_service is not None
    assert resources.close_service is not None
    assert resources.recovery_service is not None
    assert resources.snapshot_service is not None
    assert resources.snapshot_query_service is not None
    assert resources.notes_service is not None


@pytest.mark.asyncio
async def test_shutdown_is_idempotent_after_bootstrap(
    make_settings,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    fake_logging_service = FakeLoggingService()
    scheduler_start_mock = AsyncMock()
    scheduler_shutdown_mock = AsyncMock()
    debounce_shutdown_mock = AsyncMock()

    monkeypatch.setattr(
        bootstrap_module.LoggingService,
        "create",
        staticmethod(MagicMock(return_value=fake_logging_service)),
    )
    monkeypatch.setattr(bootstrap_module.BackgroundScheduler, "start", scheduler_start_mock)
    monkeypatch.setattr(
        bootstrap_module.BackgroundScheduler,
        "shutdown",
        scheduler_shutdown_mock,
    )
    monkeypatch.setattr(
        bootstrap_module.DebounceManager,
        "shutdown",
        debounce_shutdown_mock,
    )
    monkeypatch.setattr(bootstrap_module, "STORAGE_DIR", tmp_path / "storage")
    monkeypatch.setattr(bootstrap_module, "STORAGE_SUBDIRECTORIES", ("snapshots",))

    service = BootstrapService(settings=make_settings())
    await service.bootstrap()

    await service.shutdown()
    await service.shutdown()

    assert scheduler_start_mock.await_count == 1
    assert scheduler_shutdown_mock.await_count == 1
    assert debounce_shutdown_mock.await_count == 1
    assert service.scheduler is None
    assert service.debounce_manager is None
    assert service.resources is None
