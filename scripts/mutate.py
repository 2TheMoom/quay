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
    # watched orders: the terms
    ("fund_watched ignores its terms", "_terms(watch_url, watch_kind, watch_path, watch_value))", '("", "", "", ""))'),
    ("terms: types unchecked", '    _bad(any(not isinstance(x, str) for x in (url, kind, path, value)), "watch terms must be strings")\n', ""),
    ("terms: fields without a url allowed", '        _bad(kind != "" or path != "" or value != "", "watch terms need a url")\n', ""),
    ("terms: kind unchecked", 'kind not in ("sha256", "json_eq")', "False"),
    ("terms: kind loosened", '("sha256", "json_eq")', '("sha256", "json_eq", "regex")'),
    ("terms: sha256 may carry a path", '        _bad(path != "", "a sha256 watch has no path")\n', ""),
    ("terms: sha256 length unchecked", "len(value) != 64 or any", "any"),
    ("terms: sha256 uppercase allowed", 'c not in "0123456789abcdef"', 'c not in "0123456789abcdefABCDEF"'),
    ("terms: sha256 non-hex allowed", " or any(c not in \"0123456789abcdef\" for c in value)", ""),
    ("terms: json path unbounded", "_bad(len(path) > MAX_PATH or any(", "_bad(any("),
    ("terms: empty json path allowed", "not 1 <= len(s) <= 40 or any(c not in SEG for c in s) for s in path.split", "len(s) > 40 or any(c not in SEG for c in s) for s in path.split"),
    ("terms: json segment length unchecked", "not 1 <= len(s) <= 40 or any(c not in SEG for c in s)", "any(c not in SEG for c in s)"),
    ("terms: json segment length ceiling", "not 1 <= len(s) <= 40", "len(s) < 1"),
    ("terms: json segment floor", "not 1 <= len(s) <= 40", "len(s) > 40"),
    ("terms: json segment chars unchecked", "or any(c not in SEG for c in s)", ""),
    ("terms: segment alphabet gains a slash", 'SEG = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"', 'SEG = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-/"'),
    ("terms: segment alphabet gains a space", 'SEG = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"', 'SEG = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_- "'),
    ("terms: value may be empty", "not 1 <= len(value) <= MAX_VALUE", "len(value) > MAX_VALUE"),
    ("terms: value unbounded", "not 1 <= len(value) <= MAX_VALUE", "len(value) < 1"),
    ("terms: value limit constant", "MAX_VALUE = 200", "MAX_VALUE = 201"),
    ("terms: path limit constant", "MAX_PATH = 100", "MAX_PATH = 101"),
    ("terms: path and value swapped", "return (u, kind, path, value)", "return (u, kind, value, path)"),
    ("terms: url not normalised", "    u = _url(url)\n", "    u = url\n"),
    ("terms: watch_url not stored", "watch_url=watch[0],", 'watch_url="",'),
    ("terms: watch_kind not stored", "watch_kind=watch[1],", 'watch_kind="",'),
    ("terms: watch_path not stored", "watch_path=watch[2],", 'watch_path="",'),
    ("terms: watch_value not stored", "watch_value=watch[3],", 'watch_value="",'),
    # watched orders: the url
    ("url: scheme unchecked", 'not u.startswith("https://") or', ""),
    ("url: length unchecked", " or len(u) > MAX_URL", ""),
    ("url: length constant", "MAX_URL = 300", "MAX_URL = 301"),
    ("url: forbidden characters allowed", 'ch.isspace() or ch in "\\"\'<>\\\\`|"', "False"),
    ("url: whitespace allowed", "ch.isspace() or ch in", "ch in"),
    ("url: credentials allowed", '_bad("@" in h or ":" in h,', '_bad(":" in h,'),
    ("url: port allowed", '_bad("@" in h or ":" in h,', '_bad("@" in h,'),
    ("url: one-label host allowed", "len(p) < 2 or", ""),
    ("url: internal suffix allowed", "p[-1] in INTERNAL or", ""),
    ("url: numeric suffix allowed", " or not any(c.isalpha() for c in p[-1])", ""),
    ("url: label length unchecked", "not 1 <= len(x) <= 63 or", ""),
    ("url: label dash edges allowed", 'x[0] == "-" or x[-1] == "-" or', ""),
    ("url: label characters unchecked", " or any(c not in LABEL for c in x)", ""),
    ("url: fragment kept", 'return u.split("#", 1)[0]', "return u"),
    # watched orders: reading the page
    ("page: sha256 compares the wrong way", "hashlib.sha256(body).hexdigest() == value", "hashlib.sha256(body).hexdigest() != value"),
    ("page: bad json counts as met", "    except Exception:\n        return False\n    for seg", "    except Exception:\n        return True\n    for seg"),
    ("page: missing key counts as met", "        else:\n            return False\n    return _text(cur) == value", "        else:\n            return True\n    return _text(cur) == value"),
    ("page: dict key unchecked", "isinstance(cur, dict) and seg in cur", "isinstance(cur, dict)"),
    ("page: list position unchecked", "seg.isdigit() and int(seg) < len(cur)", "seg.isdigit()"),
    ("page: list bound unchecked", "seg.isdigit() and int(seg) < len(cur)", "int(seg) < len(cur)"),
    ("page: lists ignored", "elif isinstance(cur, list) and", "elif False and"),
    ("page: compare ignores case", "return _text(cur) == value", "return _text(cur).lower() == value.lower()"),
    ("page: true reads as True", 'return "true" if v else "false"', 'return "True" if v else "False"'),
    ("page: null reads as None", 'return "null"', 'return "None"'),
    ("page: containers read as text", '        return json.dumps(v)\n    return ""', '        return json.dumps(v)\n    return str(v)'),
    ("page: status unchecked", "r.status != 200 or not r.body", "not r.body"),
    ("page: empty body allowed", "r.status != 200 or not r.body or", "r.status != 200 or"),
    ("page: size unchecked", " or len(r.body) > MAX_BODY", ""),
    ("page: size constant", "MAX_BODY = 2_000_000", "MAX_BODY = 2_000_001"),
    ("page: a failed request reads as a page", 'except Exception:\n            return {"kind": "unreadable", "met": False}', 'except Exception:\n            return {"kind": "read", "met": False}'),
    ("page: unreadable reads as met", 'if r.status != 200 or not r.body or len(r.body) > MAX_BODY:\n            return {"kind": "unreadable", "met": False}', 'if r.status != 200 or not r.body or len(r.body) > MAX_BODY:\n            return {"kind": "unreadable", "met": True}'),
    # the vote
    ("vote: validator ignores met", 'return m["kind"] == t["kind"] and m["met"] == t["met"]', 'return m["kind"] == t["kind"]'),
    ("vote: validator ignores kind", 'return m["kind"] == t["kind"] and m["met"] == t["met"]', 'return m["met"] == t["met"]'),
    ("vote: validator accepts anything", 'return m["kind"] == t["kind"] and m["met"] == t["met"]', "return True"),
    ("vote: validator accepts errors", "        if not isinstance(res, gl.vm.Return):\n            return False\n        t = res.calldata", "        if not isinstance(res, gl.vm.Return):\n            return True\n        t = res.calldata"),
    ("vote: shape unchecked", 'if not isinstance(t, dict) or not isinstance(t.get("kind"), str) or not isinstance(t.get("met"), bool):\n            return False\n', ""),
    ("vote: bool unchecked", ' or not isinstance(t.get("met"), bool)', ""),
    ("vote: kind type unchecked", ' or not isinstance(t.get("kind"), str)', ""),
    # observe
    ("observe: when not open", '_bad(o.status != OPEN, f"Not open (status: {o.status})")\n        _bad(o.watch_url == ""', '_bad(False, "Not open")\n        _bad(o.watch_url == ""'),
    ("observe: without a watch", '        _bad(o.watch_url == "", "This order has no watch")\n', ""),
    ("observe: after the deadline", '        _bad(now > int(o.deadline), "The deadline has passed")\n', ""),
    ("observe: refused at the deadline", "now > int(o.deadline)", "now >= int(o.deadline)"),
    ("observe: no rate limit", '        _bad(int(o.observed_at) > 0 and now < int(o.observed_at) + MIN_RECHECK, "Observed too recently, try again later")\n', ""),
    ("observe: rate limit off by one", "now < int(o.observed_at) + MIN_RECHECK", "now <= int(o.observed_at) + MIN_RECHECK"),
    ("observe: rate limit constant", "MIN_RECHECK = 300", "MIN_RECHECK = 3"),
    ("observe: unreadable recorded", '        _bad(r["kind"] != "read", "Page unreadable, nothing recorded. Try again shortly.")\n', ""),
    ("observe: look not recorded", "        o.observed_at = now\n", ""),
    ("observe: observations not counted", "        o.observations += 1\n", ""),
    ("observe: unmet decides anyway", 'if r["met"]:\n            self._decide(order_id, o, "release", "observed")', 'if True:\n            self._decide(order_id, o, "release", "observed")'),
    ("observe: met never decides", 'if r["met"]:\n            self._decide(order_id, o, "release", "observed")', 'if False:\n            self._decide(order_id, o, "release", "observed")'),
    ("observe: decides a refund", '"release", "observed")', '"refund", "observed")'),
    ("observe: wrong reason", '"release", "observed")', '"release", "released")'),
    ("observe: reads another page", "_look(o.watch_url, o.watch_kind, o.watch_path, o.watch_value)", "_look(o.watch_url, o.watch_kind, o.watch_path, o.watch_path)"),
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
