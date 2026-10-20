# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }

from dataclasses import dataclass
from datetime import datetime, timezone
from genlayer import *

MAX_ID = 64
MAX_NOTE = 140
MAX_PAYEES = 10
MIN_AHEAD = 3600
MAX_AHEAD = 730 * 86400
PAGE = 50
ID_CHARS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"

OPEN = "open"  # funded, waiting for the releaser (or the deadline)
DECIDED = "decided"  # an outcome is recorded, settle() pays it
SETTLED = "settled"  # paid, once


@gl.evm.contract_interface
class Payee:
    class View:
        pass

    class Write:
        pass


def _pay(to: Address, value: int) -> None:
    Payee(to).emit_transfer(value=value)


def _bad(c: bool, m: str) -> None:
    if c:
        raise gl.vm.UserError(m)


def _addr(x) -> Address:
    if isinstance(x, Address):
        return x
    _bad(not isinstance(x, str), "an address must be a hex string")
    try:
        return Address(x)
    except Exception:
        raise gl.vm.UserError("not a valid address")


def _int(x, what: str) -> int:
    _bad(isinstance(x, bool) or not isinstance(x, int), f"{what} must be a whole number")
    return x


@allow_storage
@dataclass
class Order:
    payer: Address
    releaser: Address
    status: str
    outcome: str
    reason: str
    note: str
    total: u256
    deadline: u256
    created_at: u256
    decided_at: u256
    n_payees: u256
    n_legs: u256


class Quay(gl.Contract):
    orders: TreeMap[str, Order]
    order_ids: DynArray[str]
    payee_addr: TreeMap[str, Address]
    payee_amt: TreeMap[str, u256]
    leg_addr: TreeMap[str, Address]
    leg_amt: TreeMap[str, u256]
    paid: TreeMap[str, u256]
    total_received: u256
    total_scheduled: u256

    def __init__(self):
        pass

    def _now(self) -> int:
        return int(datetime.now(timezone.utc).timestamp())

    def _get(self, i: str) -> Order:
        _bad(not isinstance(i, str) or i not in self.orders, "Order not found")
        return self.orders[i]

    def _decide(self, i: str, o: Order, outcome: str, reason: str) -> None:
        """Records what will be paid. Builds the legs from the terms fixed at fund(), never from the caller. Moves no money."""
        o.status = DECIDED
        o.outcome = outcome
        o.reason = reason
        o.decided_at = self._now()
        if outcome == "release":
            for k in range(int(o.n_payees)):
                self.leg_addr[f"{i}:{k}"] = self.payee_addr[f"{i}:{k}"]
                self.leg_amt[f"{i}:{k}"] = self.payee_amt[f"{i}:{k}"]
            o.n_legs = o.n_payees
        else:
            self.leg_addr[f"{i}:0"] = o.payer
            self.leg_amt[f"{i}:0"] = o.total
            o.n_legs = 1
        _bad(sum(int(self.leg_amt[f"{i}:{k}"]) for k in range(int(o.n_legs))) != int(o.total), "Legs do not add up to the order total")

    @gl.public.write.payable
    def fund(self, order_id: str, releaser: str, recipients: list, amounts: list, deadline: int, note: str) -> None:
        """Every rule is checked before any state changes. On Bradbury a refused payable call KEEPS its value, so callers must
        pre-check: the amount of a refused call is stranded in the contract and shows as in_flight."""
        _bad(not isinstance(order_id, str) or not 0 < len(order_id) <= MAX_ID or any(c not in ID_CHARS for c in order_id), f"order_id must be 1 to {MAX_ID} characters from a-z A-Z 0-9 . _ -")
        _bad(order_id in self.orders, "Order already exists")
        _bad(not isinstance(note, str) or len(note) > MAX_NOTE, f"note must be at most {MAX_NOTE} characters")
        _bad(not isinstance(recipients, list) or not isinstance(amounts, list) or len(recipients) != len(amounts), "recipients and amounts must be lists of the same length")
        _bad(not 1 <= len(recipients) <= MAX_PAYEES, f"between 1 and {MAX_PAYEES} recipients")
        rel = _addr(releaser)
        me = gl.message.contract_address
        addrs = [_addr(r) for r in recipients]
        _bad(any(a == me for a in addrs) or rel == me, "Quay cannot be a recipient or the releaser")
        amts = [_int(a, "each amount") for a in amounts]
        _bad(any(a <= 0 for a in amts), "each amount must be above zero")
        _bad(sum(amts) != int(gl.message.value), "amounts must add up to exactly the value sent")
        _int(deadline, "deadline")
        now = self._now()
        _bad(deadline < now + MIN_AHEAD or deadline > now + MAX_AHEAD, "deadline must be at least an hour ahead and within two years")
        payer = gl.message.sender_address
        self.orders[order_id] = Order(
            payer=payer, releaser=rel, status=OPEN, outcome="", reason="", note=note, total=gl.message.value, deadline=deadline,
            created_at=now, decided_at=0, n_payees=len(addrs), n_legs=0,
        )
        for k in range(len(addrs)):
            self.payee_addr[f"{order_id}:{k}"] = addrs[k]
            self.payee_amt[f"{order_id}:{k}"] = amts[k]
        self.order_ids.append(order_id)
        self.total_received += gl.message.value

    @gl.public.write
    def release(self, order_id: str) -> None:
        """Releaser only. Records that the recipients are paid. Moves no money."""
        o = self._get(order_id)
        _bad(gl.message.sender_address != o.releaser, "Only the releaser can release")
        _bad(o.status != OPEN, f"Not open (status: {o.status})")
        self._decide(order_id, o, "release", "released")

    @gl.public.write
    def refund(self, order_id: str) -> None:
        """Releaser only. Records that the payer is paid back. Moves no money."""
        o = self._get(order_id)
        _bad(gl.message.sender_address != o.releaser, "Only the releaser can refund")
        _bad(o.status != OPEN, f"Not open (status: {o.status})")
        self._decide(order_id, o, "refund", "refunded")

    @gl.public.write
    def reclaim(self, order_id: str) -> None:
        """Payer only, after the deadline, while nobody has decided. Records the same refund. Moves no money."""
        o = self._get(order_id)
        _bad(gl.message.sender_address != o.payer, "Only the payer can reclaim")
        _bad(o.status != OPEN, f"Not open (status: {o.status})")
        _bad(self._now() <= int(o.deadline), "The deadline has not passed yet")
        self._decide(order_id, o, "refund", "deadline")

    @gl.public.write
    def settle(self, order_id: str) -> None:
        """Permissionless. Pays every leg of a recorded outcome, once, in a transaction of its own."""
        o = self._get(order_id)
        _bad(o.status != DECIDED, f"No outcome to settle (status: {o.status})")
        o.status = SETTLED
        for k in range(int(o.n_legs)):
            key = f"{order_id}:{k}"
            amt = int(self.leg_amt[key])
            _bad(key in self.paid, "Already paid")
            self.paid[key] = amt
            self.total_scheduled += amt
            _pay(self.leg_addr[key], amt)

    @gl.public.view
    def get_order(self, order_id: str) -> dict:
        _bad(not isinstance(order_id, str) or order_id not in self.orders, "Order not found")
        o = self.orders[order_id]
        payees = [{"to": self.payee_addr[f"{order_id}:{k}"].as_hex, "amount": int(self.payee_amt[f"{order_id}:{k}"])} for k in range(int(o.n_payees))]
        legs = [{"to": self.leg_addr[f"{order_id}:{k}"].as_hex, "amount": int(self.leg_amt[f"{order_id}:{k}"]), "scheduled": f"{order_id}:{k}" in self.paid} for k in range(int(o.n_legs))]
        return {
            "id": order_id, "payer": o.payer.as_hex, "releaser": o.releaser.as_hex, "status": o.status, "outcome": o.outcome, "reason": o.reason,
            "note": o.note, "total": int(o.total), "deadline": int(o.deadline), "created_at": int(o.created_at), "decided_at": int(o.decided_at),
            "payees": payees, "legs": legs,
        }

    @gl.public.view
    def get_order_ids(self, offset: int, limit: int) -> list:
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0 or isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            return []
        return [self.order_ids[k] for k in range(offset, min(offset + min(limit, PAGE), len(self.order_ids)))]

    @gl.public.view
    def get_count(self) -> int:
        return len(self.order_ids)

    @gl.public.view
    def get_accounting(self) -> dict:
        """received = everything ever sent in. scheduled = everything a settlement has sent out. in_flight = balance beyond what is
        still owed: payments that have not left yet (they leave when the settling transaction finalizes) or value a refused call kept."""
        bal = int(self.balance)
        owed = int(self.total_received) - int(self.total_scheduled)
        return {
            "balance": bal, "total_received": int(self.total_received), "total_scheduled": int(self.total_scheduled),
            "unscheduled": owed, "in_flight": max(bal - owed, 0), "shortfall": max(owed - bal, 0),
        }
