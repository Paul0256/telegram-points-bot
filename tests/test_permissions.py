from __future__ import annotations

import pytest

from pointsbot.core import permissions
from pointsbot.core.models import DEDUCT_BOT, POINTS_BOT, Identity, Role, TxType, User
from pointsbot.core.service import AccessDenied, PermissionDenied

from .conftest import ADMIN, INACTIVE, MEMBER, OPERATOR, OPERATOR_ON_DISCORD, STRANGER


@pytest.mark.parametrize(
    "role, bot, tx_type, allowed",
    [
        (Role.ADMIN, POINTS_BOT, TxType.AWARD, True),
        (Role.ADMIN, POINTS_BOT, TxType.DEDUCT, True),
        (Role.ADMIN, DEDUCT_BOT, TxType.DEDUCT, True),
        (Role.ADMIN, DEDUCT_BOT, TxType.AWARD, False),  # bot #1 can only deduct
        (Role.OPERATOR, POINTS_BOT, TxType.AWARD, True),
        (Role.OPERATOR, DEDUCT_BOT, TxType.DEDUCT, True),
        (Role.OPERATOR, DEDUCT_BOT, TxType.AWARD, False),
        (Role.MEMBER, POINTS_BOT, TxType.AWARD, False),
        (Role.MEMBER, DEDUCT_BOT, TxType.DEDUCT, False),
    ],
)
def test_permission_matrix(role, bot, tx_type, allowed):
    assert permissions.can_operate(User("X", role, {"telegram": "42"}), bot, tx_type) is allowed


def test_users_without_telegram_id_or_inactive_cannot_use_bots():
    assert not permissions.can_use_bots(None)
    assert not permissions.can_use_bots(User("Leo", Role.ADMIN))
    assert not permissions.can_use_bots(User("Gone", Role.ADMIN, {"telegram": "7"}, active=False))
    assert permissions.can_use_bots(User("Mia", Role.MEMBER, {"slack": "U03"}))


def test_only_admin_sees_everyone_and_overrides_negative():
    for role, expected in ((Role.ADMIN, True), (Role.OPERATOR, False), (Role.MEMBER, False)):
        user = User("X", role, {"telegram": "1"})
        assert permissions.can_view_all(user) is expected
        assert permissions.can_override_negative(user) is expected


def test_role_parsing_is_lenient_about_case_and_spaces():
    assert Role.parse(" Admin ") is Role.ADMIN
    with pytest.raises(ValueError):
        Role.parse("superuser")


@pytest.mark.parametrize("telegram_id", [STRANGER, INACTIVE])
def test_unknown_and_inactive_users_are_refused(service, telegram_id):
    with pytest.raises(AccessDenied) as info:
        service.authorize(telegram_id)
    assert info.value.identity == telegram_id


def test_member_cannot_prepare_operation(service):
    with pytest.raises(PermissionDenied):
        service.prepare(bot=POINTS_BOT, operator=MEMBER, tx_type=TxType.AWARD,
                        target_query="Leo", amount=5, note="", tx_id="t1")


def test_deduct_bot_refuses_award_even_for_admin(service):
    with pytest.raises(PermissionDenied, match="can only deduct"):
        service.prepare(bot=DEDUCT_BOT, operator=ADMIN, tx_type=TxType.AWARD,
                        target_query="Mia", amount=5, note="", tx_id="t1")


def test_operator_can_deduct_with_deduct_bot(service):
    pending = service.prepare(bot=DEDUCT_BOT, operator=OPERATOR, tx_type=TxType.DEDUCT,
                              target_query="Mia", amount=5, note="", tx_id="t1")
    assert pending.balance_after == 45


def test_member_sees_only_own_balance(service):
    report = service.balance_report(MEMBER)
    assert report.entries == [("Mia", 50)] and not report.everyone
    with pytest.raises(PermissionDenied):
        service.balance_report(MEMBER, "Leo")


def test_admin_sees_all_active_balances(service):
    report = service.balance_report(ADMIN)
    assert report.everyone
    assert dict(report.entries) == {"Admin": 0, "Oper": 0, "Mia": 50, "Leo": 20, "Nora": 0}


def test_member_history_is_limited_to_own_transactions(service):
    assert {t.target for t in service.history(MEMBER)} == {"Mia"}
    with pytest.raises(PermissionDenied):
        service.history(MEMBER, "Leo")
    assert {t.target for t in service.history(ADMIN)} == {"Mia", "Leo"}


def test_role_change_is_respected_at_confirmation(service, storage):
    service.prepare(bot=POINTS_BOT, operator=OPERATOR, tx_type=TxType.AWARD,
                    target_query="Mia", amount=5, note="", tx_id="t1")
    # The admin demotes the operator before they press "Yes".
    storage.users = [User("Oper", Role.MEMBER, {"telegram": OPERATOR.user_id}) if u.matches(OPERATOR) else u
                     for u in storage.users]
    with pytest.raises(PermissionDenied):
        service.confirm("t1", OPERATOR)
    assert not storage.transaction_exists("t1")


def test_same_person_is_recognized_on_every_platform(service):
    assert service.authorize(OPERATOR).name == "Oper"
    assert service.authorize(OPERATOR_ON_DISCORD).name == "Oper"


def test_ids_are_scoped_per_platform(service):
    # Telegram user 1 is Admin, but Discord user 1 is nobody.
    with pytest.raises(AccessDenied):
        service.authorize(Identity("discord", ADMIN.user_id))
