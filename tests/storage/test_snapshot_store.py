from __future__ import annotations

from storage.file_store import TicketFileStore
from storage.notes_store import NotesStore
from storage.snapshot_store import SnapshotStore


def test_snapshot_store_tolerates_corrupted_lines(tmp_path) -> None:
    file_store = TicketFileStore(tmp_path)
    store = SnapshotStore(file_store=file_store)
    path = store.get_path("ticket-1")
    path.write_text(
        '{"event":"create","message_id":1,"content":"hello"}\n'
        'not-json\n'
        '[1,2,3]\n'
        '{"event":"delete","message_id":1,"deleted_content":"gone"}\n',
        encoding="utf-8",
    )

    records = store.read_records("ticket-1")

    assert records == [
        {"event": "create", "message_id": 1, "content": "hello"},
        {"event": "delete", "message_id": 1, "deleted_content": "gone"},
    ]
    assert store.delete("ticket-1") is True
    assert store.delete("ticket-1") is False


def test_notes_store_tolerates_corrupted_lines(tmp_path) -> None:
    file_store = TicketFileStore(tmp_path)
    store = NotesStore(file_store=file_store)
    path = store.get_path("ticket-2")
    path.write_text(
        '{"author_id":1,"content":"note 1"}\n'
        '{broken\n'
        '{"author_id":2,"content":"note 2"}\n',
        encoding="utf-8",
    )

    records = store.read_records("ticket-2")

    assert records == [
        {"author_id": 1, "content": "note 1"},
        {"author_id": 2, "content": "note 2"},
    ]
    assert store.delete("ticket-2") is True
