from __future__ import annotations

import threading

import pytest

from pointsbot.core.models import DEDUCT_BOT, POINTS_BOT, TxType
from pointsbot.core.service import (
    AlreadyProcessed,
    CommitStatus,
    NotYourOperation,
    PendingNotFound,
    make_tx_id,
)

from .conftest import ADMIN, OPERATOR, OPERATOR_ON_DISCORD


def prepare_deduct(service, tx_id="t1", amount=10, operator=OPERATOR):
    return service.prepare(bot=DEDUCT_BOT, operator=operator, tx_type=TxType.DEDUCT,
                           target_query="Mia", amount=amount, note="", tx_id=tx_id)


def test_tx_id_is_deterministic_and_unique_per_bot_and_chat():
    assert make_tx_id("deduct", "tg-u123-7") == make_tx_id("deduct", "tg-u123-7") == "deduct-tg-u123-7"
    ids = {make_tx_id(b, e) for b in ("deduct", "points") for e in ("tg-u1-1", "tg-g1-1", "dc-1")}
    assert len(ids) == 6


def test_double_tap_on_yes_writes_once(service, storage):
    prepare_deduct(service)
    first = service.confirm("t1", OPERATOR)
    second = service.confirm("t1", OPERATOR)
    assert first.status is CommitStatus.CREATED
    assert second.status is CommitStatus.DUPLICATE
    assert second.transaction == first.transaction
    assert sum(t.tx_id == "t1" for t in storage.transactions) == 1


def test_redelivered_command_returns_same_pending(service):
    first = prepare_deduct(service)
    again = prepare_deduct(service)
    assert again is first


def test_redelivered_command_after_commit_is_ignored(service, storage):
    prepare_deduct(service)
    service.confirm("t1", OPERATOR)
    with pytest.raises(AlreadyProcessed):
        prepare_deduct(service)
    assert len(storage.transactions) == 3  # two seed rows + one


def test_concurrent_confirmations_write_once(service, storage):
    prepare_deduct(service)
    results, barrier = [], threading.Barrier(8)

    def tap():
        barrier.wait()
        results.append(service.confirm("t1", OPERATOR).status)

    threads = [threading.Thread(target=tap) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count(CommitStatus.CREATED) == 1
    assert results.count(CommitStatus.DUPLICATE) == 7
    assert sum(t.tx_id == "t1" for t in storage.transactions) == 1


def test_no_then_yes_does_not_write(service, storage):
    prepare_deduct(service)
    assert service.cancel("t1", OPERATOR) is True
    with pytest.raises(PendingNotFound):
        service.confirm("t1", OPERATOR)
    assert not storage.transaction_exists("t1")


def test_expired_confirmation_is_rejected(service, clock, storage):
    prepare_deduct(service)
    clock.advance(minutes=6)
    with pytest.raises(PendingNotFound):
        service.confirm("t1", OPERATOR)
    assert not storage.transaction_exists("t1")


def test_only_initiator_can_confirm_or_cancel(service, storage):
    prepare_deduct(service)
    with pytest.raises(NotYourOperation):
        service.confirm("t1", ADMIN)
    with pytest.raises(NotYourOperation):
        service.cancel("t1", ADMIN)
    assert service.confirm("t1", OPERATOR).status is CommitStatus.CREATED


def test_storage_failure_keeps_pending_for_retry(service, storage, monkeypatch):
    prepare_deduct(service)
    original = storage.append_transaction

    def broken(tx):
        raise ConnectionError("Google is down")

    monkeypatch.setattr(storage, "append_transaction", broken)
    with pytest.raises(ConnectionError):
        service.confirm("t1", OPERATOR)
    monkeypatch.setattr(storage, "append_transaction", original)
    assert service.confirm("t1", OPERATOR).status is CommitStatus.CREATED
    assert sum(t.tx_id == "t1" for t in storage.transactions) == 1


def test_same_message_id_on_two_bots_are_different_transactions(service, storage):
    chat, message = 2, 50
    for bot in (DEDUCT_BOT, POINTS_BOT):
        tx_id = make_tx_id(bot.key, f"tg-u{chat}-{message}")
        service.prepare(bot=bot, operator=OPERATOR, tx_type=TxType.DEDUCT,
                        target_query="Mia", amount=1, note="", tx_id=tx_id)
        assert service.confirm(tx_id, OPERATOR).status is CommitStatus.CREATED
    assert len(storage.transactions) == 4


def test_same_person_on_another_platform_cannot_confirm(service):
    # Confirmation is bound to the exact identity that started the operation.
    prepare_deduct(service)
    with pytest.raises(NotYourOperation):
        service.confirm("t1", OPERATOR_ON_DISCORD)


def test_operation_from_another_platform_is_recorded_with_its_identity(service):
    service.prepare(bot=DEDUCT_BOT, operator=OPERATOR_ON_DISCORD, tx_type=TxType.DEDUCT,
                    target_query="Mia", amount=1, note="", tx_id="dc")
    tx = service.confirm("dc", OPERATOR_ON_DISCORD).transaction
    assert tx.operator_id == "discord:8035111022" and tx.operator_name == "Oper"
