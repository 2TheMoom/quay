# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }

import hashlib
import json
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
MAX_URL = 300
MAX_BODY = 2_000_000  # largest page an observation will read
MAX_PATH = 100
MAX_VALUE = 200
MIN_RECHECK = 300  # seconds between two recorded observations of one order
HEADERS = {"Accept": "application/json,text/plain,*/*", "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
LABEL = "abcdefghijklmnopqrstuvwxyz0123456789-"
SEG = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
INTERNAL = ("local", "internal", "lan", "localhost", "home", "corp", "intranet", "localdomain", "arpa")

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


def _url(u) -> str:
    _bad(not isinstance(u, str) or not u.startswith("https://") or len(u) > MAX_URL, f"url must be https, at most {MAX_URL} characters")
    for ch in u:
        _bad(ch.isspace() or ch in "\"'<>\\`|", "url has a forbidden character")
    h = u[8:].split("/", 1)[0].split("?", 1)[0].split("#", 1)[0].lower()
    _bad("@" in h or ":" in h, "url may not have credentials or a port")
    p = h.split(".")
    _bad(len(p) < 2 or p[-1] in INTERNAL or not any(c.isalpha() for c in p[-1]), "url host must be a public domain name")
    for x in p:
        _bad(not 1 <= len(x) <= 63 or x[0] == "-" or x[-1] == "-" or any(c not in LABEL for c in x), "invalid url host")
    return u.split("#", 1)[0]


def _terms(url, kind, path, value) -> tuple:
    """The watch an order is funded with, or four empty strings for none. Fixed at fund(), never changed."""
    _bad(any(not isinstance(x, str) for x in (url, kind, path, value)), "watch terms must be strings")
    if url == "":
        _bad(kind != "" or path != "" or value != "", "watch terms need a url")
        return ("", "", "", "")
    u = _url(url)
    _bad(kind not in ("sha256", "json_eq"), "watch kind must be sha256 or json_eq")
    if kind == "sha256":
        _bad(path != "", "a sha256 watch has no path")
        _bad(len(value) != 64 or any(c not in "0123456789abcdef" for c in value), "a sha256 watch needs 64 lowercase hex digits")
    else:
        _bad(len(path) > MAX_PATH or any(not 1 <= len(s) <= 40 or any(c not in SEG for c in s) for s in path.split(".")), "path is dot-separated names or list positions")  # an empty path is an empty segment
        _bad(not 1 <= len(value) <= MAX_VALUE, f"value must be 1 to {MAX_VALUE} characters")
    return (u, kind, path, value)


def _text(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if v is None:
        return "null"
    if isinstance(v, str):
        return v
    if isinstance(v, (int, float)):
        return json.dumps(v)
    return ""


def _met(body: bytes, kind: str, path: str, value: str) -> bool:
    """Does the page satisfy the watch? sha256: its bytes hash to the value. json_eq: the field at the path reads exactly as the value."""
    if kind == "sha256":
        return hashlib.sha256(body).hexdigest() == value
    try:
        cur = json.loads(body.decode("utf-8"))
    except Exception:
        return False
    for seg in path.split("."):
        if isinstance(cur, dict) and seg in cur:
            cur = cur[seg]
        elif isinstance(cur, list) and seg.isdigit() and int(seg) < len(cur):
            cur = cur[int(seg)]
        else:
            return False
    return _text(cur) == value


def _look(url: str, kind: str, path: str, value: str) -> dict:
    """One validator (the leader) fetches the page and proposes whether the watch holds. Every other validator fetches it again on its
    own and votes to accept only if it reaches the same answer. An unreadable page is its own answer, and it records nothing."""
    def leader_fn() -> dict:
        try:
            r = gl.nondet.web.request(url, method="GET", headers=HEADERS)
        except Exception:
            return {"kind": "unreadable", "met": False}
        if r.status != 200 or not r.body or len(r.body) > MAX_BODY:
            return {"kind": "unreadable", "met": False}
        return {"kind": "read", "met": _met(r.body, kind, path, value)}

    def validator_fn(res) -> bool:
        if not isinstance(res, gl.vm.Return):
            return False
        t = res.calldata
        if not isinstance(t, dict) or not isinstance(t.get("kind"), str) or not isinstance(t.get("met"), bool):
            return False
        m = leader_fn()
        return m["kind"] == t["kind"] and m["met"] == t["met"]

    return gl.vm.run_nondet_unsafe(leader_fn, validator_fn)


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
    watch_url: str
    watch_kind: str
    watch_path: str
    watch_value: str
    observed_at: u256
    observations: u256


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
        """An order the named releaser decides, or the deadline refunds."""
        self._fund(order_id, releaser, recipients, amounts, deadline, note, ("", "", "", ""))

    @gl.public.write.payable
    def fund_watched(self, order_id: str, releaser: str, recipients: list, amounts: list, deadline: int, note: str, watch_url: str, watch_kind: str, watch_path: str, watch_value: str) -> None:
        """The same order, plus a watch fixed now: a public page and a condition on it (its bytes hash to a value, or one JSON field reads
        as a value). Anyone can then call observe(), and if the validators agree the condition holds, the order is decided as a release."""
        self._fund(order_id, releaser, recipients, amounts, deadline, note, _terms(watch_url, watch_kind, watch_path, watch_value))

    def _fund(self, order_id: str, releaser: str, recipients: list, amounts: list, deadline: int, note: str, watch: tuple) -> None:
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
            watch_url=watch[0], watch_kind=watch[1], watch_path=watch[2], watch_value=watch[3], observed_at=0, observations=0,
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
    def observe(self, order_id: str) -> None:
        """Permissionless. Validators fetch the page this order names and agree on whether its condition holds. If it does, the order is
        decided as a release, built from the terms fixed at fund(). A page that cannot be read records nothing. Moves no money."""
        o = self._get(order_id)
        _bad(o.status != OPEN, f"Not open (status: {o.status})")
        _bad(o.watch_url == "", "This order has no watch")
        now = self._now()
        _bad(now > int(o.deadline), "The deadline has passed")
        _bad(int(o.observed_at) > 0 and now < int(o.observed_at) + MIN_RECHECK, "Observed too recently, try again later")
        r = _look(o.watch_url, o.watch_kind, o.watch_path, o.watch_value)
        _bad(r["kind"] != "read", "Page unreadable, nothing recorded. Try again shortly.")
        o.observed_at = now
        o.observations += 1
        if r["met"]:
            self._decide(order_id, o, "release", "observed")

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
            "watch": {"url": o.watch_url, "kind": o.watch_kind, "path": o.watch_path, "value": o.watch_value, "observed_at": int(o.observed_at), "observations": int(o.observations)},
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
