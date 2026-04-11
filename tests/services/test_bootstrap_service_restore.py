from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

import services.bootstrap_service as bootstrap_module
from services.bootstrap_service import BootstrapService
from services.snapshot_service import SnapshotRestoreReport


class FakeLoggingService:
    def __init__(self) -> None:
        self.info_messages: list[str] = []
        self.debug_messages: list[str] = []

    def child(self, name: str) -> logging.Logger:
        return logging.getLogger(f"tests.bootstrap.restore.{name}")

    def log_local_info(self, message: str, *args, **kwargs) -> None:
        del kwargs
        self.info_messages.append(message % args if args else message)

    def log_local_debug(self, message: str, *args, **kwargs) -> None:
        del kwargs
        self.debug_messages.append(message % args if args else message)

    async def send_guild_log(self, *args, **kwargs) -> bool:
        return False

    async def send_ticket_log(self, *args, **kwargs) -> bool:
        return False


@pytest.mark.asyncio
async def test_bootstrap_logs_snapshot_restore_report(
    make_settings,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    fake_logging_service = FakeLoggingService()
    create_mock = MagicMock(return_value=fake_logging_service)
    start_mock = AsyncMock()
    restore_mock = AsyncMock(
        return_value=SnapshotRestoreReport(
            tickets_scanned=2,
            tickets_restored=1,
            cached_messages=5,
        )
    )

    monkeypatch.setattr(bootstrap_module.LoggingService, "create", staticmethod(create_mock))
    monkeypatch.setattr(bootstrap_module.BackgroundScheduler, "start", start_mock)
    monkeypatch.setattr(bootstrap_module.SnapshotService, "restore_runtime_state", restore_mock)
    monkeypatch.setattr(bootstrap_module, "STORAGE_DIR", tmp_path / "storage")
    monkeypatch.setattr(
        bootstrap_module,
        "STORAGE_SUBDIRECTORIES",
        ("snapshots", "notes", "archives"),
    )

    service = BootstrapService(settings=make_settings())

    await service.bootstrap()

    assert restore_mock.await_count == 1
    assert start_mock.await_count == 1
    assert "Restored snapshot runtime cache for 1 ticket(s), cached_messages=5." in fake_logging_service.info_messages
