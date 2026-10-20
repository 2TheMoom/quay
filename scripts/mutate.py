"""Mutation check: break the contract one small way at a time and confirm the direct-mode tests notice.
    python scripts/mutate.py            (all)      MUT=ledger,deadline python scripts/mutate.py   (some)
The contract file is restored afterwards, even on Ctrl+C."""

import os
import subprocess
import sys

root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
py = os.environ.get("PY", sys.executable)
p = os.path.join(root, "contracts", "quay.py")
orig = open(p, encoding="utf-8", newline="").read()
NL = "\r\n" if "\r\n" in orig else "\n"


def L(text):
    return "        " + text + "\n"


NOT_OPEN = '_bad(o.status != OPEN, f"Not open (status: {o.status})")'

M = [
    # fund: ids and notes
    ("id may be empty", "not 0 < len(order_id) <= MAX_ID", "len(order_id) > MAX_ID"),
    ("id limit off by one", "not 0 < len(order_id) <= MAX_ID", "not 0 < len(order_id) < MAX_ID"),
    ("id characters unchecked", " or any(c not in ID_CHARS for c in order_id)", ""),
    ("id alphabet gains a slash", '0123456789._-"', '0123456789._-/"'),
    ("id limit constant", "MAX_ID = 64", "MAX_ID = 65"),
    ("duplicate id allowed", L('_bad(order_id in self.orders, "Order already exists")'), ""),
    ("note unbounded", "len(note) > MAX_NOTE", "len(note) > MAX_NOTE * 100"),
    ("note limit constant", "MAX_NOTE = 140", "MAX_NOTE = 141"),
    ("note type unchecked", "not isinstance(note, str) or len(note) > MAX_NOTE", "len(note) > MAX_NOTE"),
    # fund: recipients and amounts
    ("list lengths unchecked", " or len(recipients) != len(amounts)", ""),
    ("list types unchecked", "not isinstance(recipients, list) or not isinstance(amounts, list) or", ""),
    ("recipient floor unchecked", "not 1 <= len(recipients) <= MAX_PAYEES", "len(recipients) > MAX_PAYEES"),
    ("recipient ceiling unchecked", "not 1 <= len(recipients) <= MAX_PAYEES", "len(recipients) < 1"),
    ("recipient limit constant", "MAX_PAYEES = 10", "MAX_PAYEES = 11"),
    ("quay as recipient allowed", "any(a == me for a in addrs) or rel == me", "rel == me"),
    ("quay as releaser allowed", "any(a == me for a in addrs) or rel == me", "any(a == me for a in addrs)"),
    ("zero amount allowed", "any(a <= 0 for a in amts)", "any(a < 0 for a in amts)"),
    ("bool amount allowed", '_bad(isinstance(x, bool) or not isinstance(x, int), f"{what} must be a whole number")', '_bad(not isinstance(x, int), f"{what} must be a whole number")'),
    ("sum unchecked", L('_bad(sum(amts) != int(gl.message.value), "amounts must add up to exactly the value sent")'), ""),
    ("sum may fall short", "sum(amts) != int(gl.message.value)", "sum(amts) > int(gl.message.value)"),
    ("sum may overshoot", "sum(amts) != int(gl.message.value)", "sum(amts) < int(gl.message.value)"),
    ("address parse unchecked", "    try:\n        return Address(x)\n    except Exception:\n        raise gl.vm.UserError(\"not a valid address\")", "    return Address(x)"),
    ("address type unchecked", '_bad(not isinstance(x, str), "an address must be a hex string")', "pass"),
    # fund: deadline
    ("deadline soon allowed", "deadline < now + MIN_AHEAD or", "deadline < now or"),
    ("deadline far allowed", " or deadline > now + MAX_AHEAD", ""),
    ("deadline type unchecked", L('_int(deadline, "deadline")'), ""),
    ("minimum lead constant", "MIN_AHEAD = 3600", "MIN_AHEAD = 60"),
    # fund: what is stored
    ("payer not recorded", "payer=payer, releaser=rel,", "payer=rel, releaser=rel,"),
    ("releaser not recorded", "payer=payer, releaser=rel,", "payer=payer, releaser=payer,"),
    ("total not recorded", "total=gl.message.value, deadline=deadline,", "total=0, deadline=deadline,"),
    ("created time not recorded", "created_at=now,", "created_at=0,"),
    ("payee address not stored", L('self.payee_addr[f"{order_id}:{k}"] = addrs[k]'), ""),
    ("payee amount not stored", L('self.payee_amt[f"{order_id}:{k}"] = amts[k]'), ""),
    ("payee count wrong", "n_payees=len(addrs)", "n_payees=1"),
    ("order not listed", L("self.order_ids.append(order_id)"), ""),
    ("received not counted", L("self.total_received += gl.message.value"), ""),
    # decisions
    ("release legs pay the payer", 'self.leg_addr[f"{i}:{k}"] = self.payee_addr[f"{i}:{k}"]', 'self.leg_addr[f"{i}:{k}"] = o.payer'),
    ("release legs pay the total", 'self.leg_amt[f"{i}:{k}"] = self.payee_amt[f"{i}:{k}"]', 'self.leg_amt[f"{i}:{k}"] = o.total'),
    ("release leg count wrong", "o.n_legs = o.n_payees", "o.n_legs = 1"),
    ("refund pays the releaser", 'self.leg_addr[f"{i}:0"] = o.payer', 'self.leg_addr[f"{i}:0"] = o.releaser'),
    ("refund pays half", 'self.leg_amt[f"{i}:0"] = o.total', 'self.leg_amt[f"{i}:0"] = o.total // 2'),
    ("refund has no leg", "o.n_legs = 1", "o.n_legs = 0"),
    ("leg sum unchecked", L('_bad(sum(int(self.leg_amt[f"{i}:{k}"]) for k in range(int(o.n_legs))) != int(o.total), "Legs do not add up to the order total")'), ""),
    ("decision leaves the order open", "o.status = DECIDED", "o.status = OPEN"),
    ("outcome not recorded", L("o.outcome = outcome"), ""),
    ("reason not recorded", L("o.reason = reason"), ""),
    ("decision time not recorded", L("o.decided_at = self._now()"), ""),
    ("anyone releases", '_bad(gl.message.sender_address != o.releaser, "Only the releaser can release")', '_bad(False, "Only the releaser can release")'),
    ("release when not open", '"Only the releaser can release")\n        ' + NOT_OPEN, '"Only the releaser can release")\n        _bad(False, "Not open")'),
    ("release records a refund", '"release", "released")', '"refund", "released")'),
    ("anyone refunds", '_bad(gl.message.sender_address != o.releaser, "Only the releaser can refund")', '_bad(False, "Only the releaser can refund")'),
    ("refund when not open", '"Only the releaser can refund")\n        ' + NOT_OPEN, '"Only the releaser can refund")\n        _bad(False, "Not open")'),
    ("refund records a release", '"refund", "refunded")', '"release", "refunded")'),
    ("anyone reclaims", '_bad(gl.message.sender_address != o.payer, "Only the payer can reclaim")', '_bad(False, "Only the payer can reclaim")'),
    ("reclaim when not open", '"Only the payer can reclaim")\n        ' + NOT_OPEN, '"Only the payer can reclaim")\n        _bad(False, "Not open")'),
    ("reclaim before the deadline", L('_bad(self._now() <= int(o.deadline), "The deadline has not passed yet")'), ""),
    ("reclaim at the deadline", "self._now() <= int(o.deadline)", "self._now() < int(o.deadline)"),
    ("reclaim records a release", '"refund", "deadline")', '"release", "deadline")'),
    # settle
    ("settle without a decision", L('_bad(o.status != DECIDED, f"No outcome to settle (status: {o.status})")'), ""),
    ("settle does not close the order", L("o.status = SETTLED"), ""),
    ("ledger guard removed", L('_bad(key in self.paid, "Already paid")'), ""),
    ("ledger entry not recorded", L("self.paid[key] = amt"), ""),
    ("scheduled not counted", L("self.total_scheduled += amt"), ""),
    ("settle pays the wrong party", "_pay(self.leg_addr[key], amt)", "_pay(o.payer, amt)"),
    ("settle pays a wei too much", "_pay(self.leg_addr[key], amt)", "_pay(self.leg_addr[key], amt + 1)"),
    ("settle pays nothing", L("_pay(self.leg_addr[key], amt)"), ""),
    ("settle pays only the first leg", 'for k in range(int(o.n_legs)):\n            key = f"{order_id}:{k}"', 'for k in range(min(int(o.n_legs), 1)):\n            key = f"{order_id}:{k}"'),
    ("settle pays twice", "_pay(self.leg_addr[key], amt)", "_pay(self.leg_addr[key], amt)\n            _pay(self.leg_addr[key], amt)"),
    # views and accounting
    ("unknown order not refused", '_bad(not isinstance(i, str) or i not in self.orders, "Order not found")', '_bad(False, "Order not found")'),
    ("leg status hidden", '"scheduled": f"{order_id}:{k}" in self.paid}', '"scheduled": False}'),
    ("paging uncapped", "min(limit, PAGE)", "limit"),
    ("paging negative offset", "offset < 0 or isinstance(limit, bool)", "isinstance(limit, bool)"),
    ("paging zero limit", "isinstance(limit, int) or limit < 1:", "isinstance(limit, int) or limit < 0:"),
    ("paging bool offset", "isinstance(offset, bool) or not isinstance(offset, int)", "not isinstance(offset, int)"),
    ("in_flight wrong", "max(bal - owed, 0)", "max(bal - owed, 0) + 1"),
    ("shortfall hidden", "max(owed - bal, 0)", "0"),
    ("unscheduled hidden", '"unscheduled": owed,', '"unscheduled": 0,'),
]

results = []
try:
    only = os.environ.get("MUT")
    for name, a, b in M:
        a = a.replace("\n", NL)
        b = b.replace("\n", NL)
        if orig.count(a) != 1:
            print("BAD MUTATION (anchor count %d): %s" % (orig.count(a), name), flush=True)
            continue
        if only and not any(k in name for k in only.split(",")):
            continue
        open(p, "w", encoding="utf-8", newline="").write(orig.replace(a, b))
        r = subprocess.run([py, "-m", "pytest", "tests/direct", "-q", "-x", "-p", "no:cacheprovider"], cwd=root, capture_output=True, text=True)
        tag = "CAUGHT  " if r.returncode else "SURVIVED"
        results.append((tag, name))
        print(tag + "  " + name, flush=True)
finally:
    open(p, "w", encoding="utf-8", newline="").write(orig)
print("\n%d caught, %d survived" % (sum(1 for t, _ in results if t.strip() == "CAUGHT"), sum(1 for t, _ in results if t == "SURVIVED")))
