from __future__ import annotations

import pytest

from pointsbot.core.models import DEDUCT_BOT, POINTS_BOT, Role, TxType, User
from pointsbot.core.service import (
    AmbiguousPerson,
    CommitStatus,
    InsufficientBalance,
    InvalidAmount,
    UnknownPerson,
    compute_balances,
)

from .conftest import ADMIN, OPERATOR, award


def op(service, *, tx_type=TxType.DEDUCT, target="Mia", amount=10, operator=OPERATOR,
       tx_id="t1", bot=POINTS_BOT, note=""):
    return service.prepare(bot=bot, operator=operator, tx_type=tx_type, target_query=target,
                           amount=amount, note=note, tx_id=tx_id)


def test_compute_balances_sums_signed_amounts_by_normalized_name():
    txs = [award("Mia", 50, "a"), award(" mia ", 5, "b")]
    assert compute_balances(txs) == {"mia": 55}


def test_award_and_deduct_update_balance(service):
    op(service, tx_type=TxType.AWARD, amount=25, tx_id="a")
    assert service.confirm("a", OPERATOR).balance_after == 75
    op(service, amount=30, tx_id="d")
    assert service.confirm("d", OPERATOR).balance_after == 45
    assert service.balance_report(ADMIN, "Mia").entries == [("Mia", 45)]


def test_deducting_exact_balance_to_zero_is_allowed(service):
    op(service, amount=50)
    assert service.confirm("t1", OPERATOR).balance_after == 0


def test_operator_cannot_go_below_zero(service, storage):
    with pytest.raises(InsufficientBalance) as info:
        op(service, amount=51)
    assert info.value.balance == 50
    assert len(storage.transactions) == 2


def test_admin_needs_explicit_override_to_go_below_zero(service, storage):
    pending = op(service, amount=80, operator=ADMIN)
    assert pending.requires_override and pending.balance_after == -30
    with pytest.raises(InsufficientBalance):
        service.confirm("t1", ADMIN)  # plain "Yes" is not enough
    assert not storage.transaction_exists("t1")

    op(service, amount=80, operator=ADMIN, tx_id="t2")
    result = service.confirm("t2", ADMIN, allow_negative=True)
    assert result.status is CommitStatus.CREATED and result.balance_after == -30


def test_person_flagged_allow_negative_can_go_below_zero(service):
    pending = op(service, target="Nora", amount=5, bot=DEDUCT_BOT)
    assert not pending.requires_override
    assert service.confirm("t1", OPERATOR).balance_after == -5


def test_balance_is_rechecked_at_confirmation(service, storage):
    op(service, amount=40, tx_id="first")
    op(service, amount=40, tx_id="second")  # valid when prepared: 50 - 40 >= 0
    service.confirm("first", OPERATOR)
    with pytest.raises(InsufficientBalance):
        service.confirm("second", OPERATOR)  # 10 - 40 < 0 now
    assert service.balance_report(ADMIN, "Mia").entries == [("Mia", 10)]


def test_override_flag_is_ignored_for_non_admins(service):
    op(service, amount=40, tx_id="first")
    op(service, amount=40, tx_id="second")
    service.confirm("first", OPERATOR)
    with pytest.raises(InsufficientBalance):
        service.confirm("second", OPERATOR, allow_negative=True)


@pytest.mark.parametrize("amount", [0, -5, 1001])
def test_invalid_amounts_are_rejected(service, amount):
    with pytest.raises(InvalidAmount):
        op(service, tx_type=TxType.AWARD, amount=amount)


def test_person_lookup_exact_prefix_and_ambiguous(service, storage):
    assert service.find_person("  mia ").name == "Mia"
    assert service.find_person("no").name == "Nora"
    storage.users.append(User("Mia Smith", Role.MEMBER, {"telegram": "77"}))
    assert service.find_person("mia").name == "Mia"  # exact match wins over prefix
    assert service.find_person("mia s").name == "Mia Smith"
    storage.users.append(User("Leon", Role.MEMBER))
    with pytest.raises(AmbiguousPerson):
        service.find_person("le")
    with pytest.raises(UnknownPerson):
        service.find_person("Zed")
    with pytest.raises(UnknownPerson):
        service.find_person("Gone")  # inactive people cannot receive points


def test_transaction_records_canonical_names_and_note(service, storage):
    op(service, target="mia", amount=3, note="  late   again ")
    tx = service.confirm("t1", OPERATOR).transaction
    assert tx.target == "Mia" and tx.operator_name == "Oper" and tx.note == "late again"
    assert tx.bot == "points" and tx.type is TxType.DEDUCT and tx.amount == 3


def test_history_is_newest_first_and_limited(service, clock):
    for i in range(12):
        clock.advance(minutes=1)
        op(service, tx_type=TxType.AWARD, amount=1, tx_id=f"h{i}")
        service.confirm(f"h{i}", OPERATOR)
    items = service.history(ADMIN)
    assert len(items) == 10
    assert [t.tx_id for t in items[:2]] == ["h11", "h10"]
