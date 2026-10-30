import threading

import pytest

from coins import ledger
from coins.ledger import CoinsError

DAY = 86_400_000
A = "a" * 64
B = "b" * 64


def give_balance(db, account, coins):
    """Seed a balance directly in the ledger (earning is capped per day, so it cannot give an exact amount quickly)."""
    db.execute("INSERT INTO coin_ledger (account, amount, kind, created_at) VALUES (?, ?, 'seed', ?)", (account, coins, 1))


def test_watch_credits_one_coin_per_ten_minutes(clean):
    credited, earned = ledger.credit_watch(clean, A, "b1", 1500, 5 * DAY)
    assert (credited, earned) == (2, 2)
    assert ledger.balance(clean, A) == 2


def test_replayed_block_credited_once(clean):
    ledger.credit_watch(clean, A, "same", 600, 5 * DAY)
    credited, _ = ledger.credit_watch(clean, A, "same", 600, 5 * DAY)
    assert credited == 0
    assert ledger.balance(clean, A) == 1


def test_cap_is_20_per_utc_day(clean):
    ledger.credit_watch(clean, A, "big", 600 * 50, 5 * DAY)
    assert ledger.balance(clean, A) == 20
    credited, earned = ledger.credit_watch(clean, A, "more", 6000, 5 * DAY + 1000)
    assert credited == 0 and earned == 20
    credited, _ = ledger.credit_watch(clean, A, "next-day", 600, 6 * DAY)
    assert credited == 1


def test_fraction_carries_within_the_day_and_resets_at_midnight(clean):
    ledger.credit_watch(clean, A, "f1", 540, 5 * DAY)
    credited, _ = ledger.credit_watch(clean, A, "f2", 540, 5 * DAY + 1)
    assert credited == 1
    ledger.credit_watch(clean, A, "f3", 540, 5 * DAY + 2)
    credited, _ = ledger.credit_watch(clean, A, "f4", 60, 6 * DAY)
    assert credited == 0  # the 540 s left of day 5 is dropped at midnight


def test_cap_holds_with_concurrent_blocks(clean):
    def report(i):
        ledger.credit_watch(clean, A, f"c{i}", 6000, 5 * DAY)

    threads = [threading.Thread(target=report, args=(i,)) for i in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert ledger.balance(clean, A) == 20


def test_gift_moves_one_coin_in_one_transaction(clean):
    give_balance(clean, A, 3)
    assert ledger.give(clean, A, B, "post-9", 7 * DAY) == 2
    assert ledger.balance(clean, B) == 1
    assert ledger.balance(clean, A) == 2
    assert ledger.post_total(clean, "post-9") == 1


def test_cannot_give_without_balance(clean):
    with pytest.raises(CoinsError) as e:
        ledger.give(clean, A, B, "p", 7 * DAY)
    assert e.value.code == "no_balance"
    assert ledger.balance(clean, B) == 0


def test_cannot_give_to_self(clean):
    give_balance(clean, A, 1)
    with pytest.raises(CoinsError) as e:
        ledger.give(clean, A, A, "p", 7 * DAY)
    assert e.value.code == "self_gift"


def test_cannot_give_to_a_malformed_account(clean):
    give_balance(clean, A, 1)
    with pytest.raises(CoinsError) as e:
        ledger.give(clean, A, "not-an-account", "p", 7 * DAY)
    assert e.value.code == "unknown_account"


def test_concurrent_gifts_never_overspend(clean):
    give_balance(clean, A, 2)
    results = []

    def go():
        try:
            ledger.give(clean, A, B, "p", 7 * DAY)
            results.append("ok")
        except CoinsError as e:
            results.append(e.code)

    threads = [threading.Thread(target=go) for _ in range(5)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert results.count("ok") == 2
    assert ledger.balance(clean, A) == 0
    assert ledger.balance(clean, B) == 2
