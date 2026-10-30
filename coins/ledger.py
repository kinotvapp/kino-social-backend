"""The ledger: the only place a balance changes. A balance is the sum of its entries, never a stored number, so it cannot drift.

Earning: 1 coin per 10 minutes watched, at most DAILY_CAP coins per UTC day per account. Giving: one coin, from a balance that
has it, to another account, as one transaction."""
import re

SECONDS_PER_COIN = 600
DAILY_CAP = 20
DAY_MS = 86_400_000
ACCOUNT_RE = re.compile(r"^[0-9a-f]{64}$")


class CoinsError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def balance(db, account: str) -> int:
    return int(db.execute("SELECT COALESCE(SUM(amount), 0) FROM coin_ledger WHERE account = ?", (account,)).fetchone()[0])


def earned_today(db, account: str, now_ms: int) -> int:
    row = db.execute("SELECT coins FROM coin_daily WHERE account = ? AND day = ?", (account, now_ms // DAY_MS)).fetchone()
    return int(row["coins"]) if row else 0


def post_total(db, context_id: str) -> int:
    """The coins given on a post (a context): the number its coin button shows."""
    return int(db.execute("SELECT COUNT(*) FROM coin_ledger WHERE context_id = ? AND kind = 'gift_in'", (context_id,)).fetchone()[0])


def _lock(tx, account: str) -> None:
    tx.execute("SELECT pg_advisory_xact_lock(hashtext(?))", (account,))


def credit_watch(db, account: str, block_id: str, seconds: int, now_ms: int) -> tuple[int, int]:
    """Credits the whole 10-minute units a reported block completes. Returns (coins credited, coins earned today).
    A block id already used credits nothing; what is left of a unit carries within the day and is dropped at midnight UTC."""
    if seconds <= 0:
        raise CoinsError("bad_block")
    day = now_ms // DAY_MS
    with db.transaction() as tx:
        _lock(tx, account)
        fresh = tx.execute("INSERT INTO coin_blocks (block_id, account, created_at) VALUES (?, ?, ?) ON CONFLICT DO NOTHING",
                           (block_id, account, now_ms)).rowcount
        row = tx.execute("SELECT carry_seconds, coins FROM coin_daily WHERE account = ? AND day = ?", (account, day)).fetchone()
        carry, coins = (int(row["carry_seconds"]), int(row["coins"])) if row else (0, 0)
        if not fresh:
            return 0, coins
        total = carry + seconds
        units = min(total // SECONDS_PER_COIN, DAILY_CAP - coins)
        new_coins = coins + units
        new_carry = 0 if new_coins >= DAILY_CAP else total - (total // SECONDS_PER_COIN) * SECONDS_PER_COIN
        tx.execute("INSERT INTO coin_daily (account, day, carry_seconds, coins) VALUES (?, ?, ?, ?)"
                   " ON CONFLICT (account, day) DO UPDATE SET carry_seconds = EXCLUDED.carry_seconds, coins = EXCLUDED.coins",
                   (account, day, new_carry, new_coins))
        if units > 0:
            tx.execute("INSERT INTO coin_ledger (account, amount, kind, created_at) VALUES (?, ?, 'watch', ?)", (account, units, now_ms))
    return units, new_coins


def give(db, giver: str, to: str, context_id: str, now_ms: int) -> int:
    """One coin from [giver] to [to] on [context_id]. Returns the giver's balance after."""
    if not isinstance(to, str) or not ACCOUNT_RE.match(to):
        raise CoinsError("unknown_account")
    if to == giver:
        raise CoinsError("self_gift")
    with db.transaction() as tx:
        for account in sorted((giver, to)):  # always in the same order, so two gifts crossing cannot deadlock
            _lock(tx, account)
        have = int(tx.execute("SELECT COALESCE(SUM(amount), 0) FROM coin_ledger WHERE account = ?", (giver,)).fetchone()[0])
        if have < 1:
            raise CoinsError("no_balance")
        tx.execute("INSERT INTO coin_ledger (account, amount, kind, counterpart, context_id, created_at)"
                   " VALUES (?, -1, 'gift_out', ?, ?, ?)", (giver, to, context_id, now_ms))
        tx.execute("INSERT INTO coin_ledger (account, amount, kind, counterpart, context_id, created_at)"
                   " VALUES (?, 1, 'gift_in', ?, ?, ?)", (to, giver, context_id, now_ms))
    return have - 1
