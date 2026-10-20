"""Direct-mode tests for Quay (a once-only payout rail). Quay calls nothing and reads nothing from any other contract, so no fakes are
needed: every payment is observed as the transfer message it emits, and every decision is observed as the absence of one."""

import random
from datetime import datetime, timezone

import pytest

CONTRACT = "contracts/quay.py"
T0 = "2026-01-01T00:00:00Z"
T0_TS = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp())
DEADLINE = T0_TS + 86400
AT_DEADLINE = "2026-01-02T00:00:00Z"
AFTER_DEADLINE = "2026-01-02T00:00:01Z"
R1 = "0x" + "11" * 20
R2 = "0x" + "22" * 20
R3 = "0x" + "33" * 20


def _hex(a):
    return ("0x" + a.hex()).lower() if isinstance(a, (bytes, bytearray)) else str(a).lower()


def _capture_sends(vm):
    sends = []
    inner = vm._gl_call_hook if hasattr(vm, "_gl_call_hook") else None

    def hook(v, request):
        if isinstance(request, dict) and "EthSend" in request:
            s = request["EthSend"]
            sends.append((s["address"].as_hex.lower(), int(s["value"])))
            return None
        return inner(v, request) if inner else None

    vm._gl_call_hook = hook
    return sends


@pytest.fixture
def Q(direct_vm, direct_deploy, direct_alice, direct_bob):
    direct_vm.warp(T0)
    c = direct_deploy(CONTRACT)
    sends = _capture_sends(direct_vm)
    return direct_vm, c, sends


def _fund(vm, c, payer, releaser, recipients=(R1, R2), amounts=(600, 400), oid="o1", deadline=DEADLINE, note="a note", value=None):
    vm.sender = payer
    vm.value = sum(amounts) if value is None else value
    try:
        c.fund(oid, _hex(releaser), [_hex(r) for r in recipients], list(amounts), deadline, note)
    finally:
        vm.value = 0


# ---------------------------------------------------------------------------
# fund
# ---------------------------------------------------------------------------


def test_fund_stores_the_terms_and_counts_what_came_in(Q, direct_alice, direct_bob):
    vm, c, sends = Q
    _fund(vm, c, direct_alice, direct_bob)
    o = c.get_order("o1")
    assert (o["payer"].lower(), o["releaser"].lower(), o["status"], o["outcome"], o["total"]) == (_hex(direct_alice), _hex(direct_bob), "open", "", 1000)
    assert [(p["to"].lower(), p["amount"]) for p in o["payees"]] == [(R1, 600), (R2, 400)]
    assert (o["deadline"], o["note"], o["legs"], o["created_at"], o["decided_at"]) == (DEADLINE, "a note", [], T0_TS, 0)
    assert c.get_count() == 1 and c.get_order_ids(0, 10) == ["o1"]
    assert c.get_accounting()["total_received"] == 1000
    assert sends == []


def test_the_payer_may_be_the_releaser_and_a_recipient_and_recipients_may_repeat(Q, direct_alice):
    vm, c, sends = Q
    _fund(vm, c, direct_alice, direct_alice, recipients=(_hex(direct_alice), R1, R1), amounts=(1, 2, 3))
    vm.sender = direct_alice
    c.release("o1")
    c.settle("o1")
    assert sends == [(_hex(direct_alice), 1), (R1, 2), (R1, 3)]


def test_fund_refuses_bad_ids_and_duplicates(Q, direct_alice, direct_bob):
    vm, c, _ = Q
    _fund(vm, c, direct_alice, direct_bob)
    with vm.expect_revert("already exists"):
        _fund(vm, c, direct_alice, direct_bob)
    for bad in ("", "x" * 65, "has space", "slash/", "emoji✓", 7):
        with vm.expect_revert("order_id"):
            _fund(vm, c, direct_alice, direct_bob, oid=bad)
    _fund(vm, c, direct_alice, direct_bob, oid="x" * 64)
    _fund(vm, c, direct_alice, direct_bob, oid="A.b_c-9")


def test_fund_refuses_amounts_that_do_not_add_up_to_the_value_sent(Q, direct_alice, direct_bob):
    vm, c, _ = Q
    for value in (999, 1001, 0):
        with vm.expect_revert("add up"):
            _fund(vm, c, direct_alice, direct_bob, value=value)
    assert c.get_count() == 0 and c.get_accounting()["total_received"] == 0


def test_fund_refuses_bad_amounts(Q, direct_alice, direct_bob):
    vm, c, _ = Q
    for amounts in ((0, 1000), (-5, 1005), (True, 999), ("600", 400)):
        vm.sender = direct_alice
        vm.value = 1000
        with vm.expect_revert():
            c.fund("o1", _hex(direct_bob), [R1, R2], list(amounts), DEADLINE, "n")
        vm.value = 0
    assert c.get_count() == 0


def test_fund_refuses_bad_recipient_lists(Q, direct_alice, direct_bob):
    vm, c, _ = Q
    vm.sender = direct_alice
    vm.value = 1000
    with vm.expect_revert("between 1 and 10"):
        c.fund("o1", _hex(direct_bob), [], [], DEADLINE, "n")
    with vm.expect_revert("same length"):
        c.fund("o1", _hex(direct_bob), [R1, R2], [1000], DEADLINE, "n")
    with vm.expect_revert("between 1 and 10"):
        c.fund("o1", _hex(direct_bob), ["0x" + "%02x" % (i + 1) * 20 for i in range(11)], [1] * 11, DEADLINE, "n")
    with vm.expect_revert("same length"):
        c.fund("o1", _hex(direct_bob), "x", [1000], DEADLINE, "n")
    with vm.expect_revert("same length"):
        c.fund("o1", _hex(direct_bob), [R1], "x", DEADLINE, "n")
    for bad in ("0x1234", "nothex", ""):
        with vm.expect_revert("not a valid address"):
            c.fund("o1", _hex(direct_bob), [bad], [1000], DEADLINE, "n")
    for bad in (5, None, True):
        with vm.expect_revert("must be a hex string"):
            c.fund("o1", _hex(direct_bob), [bad], [1000], DEADLINE, "n")
    with vm.expect_revert("not a valid address"):
        c.fund("o1", "0xnope", [R1], [1000], DEADLINE, "n")
    with vm.expect_revert("must be a hex string"):
        c.fund("o1", 5, [R1], [1000], DEADLINE, "n")
    vm.value = 0
    ten = ["0x" + "%02x" % (i + 1) * 20 for i in range(10)]
    vm.value = 10
    c.fund("o10", _hex(direct_bob), ten, [1] * 10, DEADLINE, "n")
    vm.value = 0
    assert len(c.get_order("o10")["payees"]) == 10


def test_quay_itself_cannot_be_a_recipient_or_the_releaser(Q, direct_alice, direct_bob):
    vm, c, _ = Q
    me = _hex(vm._contract_address)
    with vm.expect_revert("Quay cannot"):
        _fund(vm, c, direct_alice, direct_bob, recipients=(me, R1))
    with vm.expect_revert("Quay cannot"):
        _fund(vm, c, direct_alice, me)


def test_fund_checks_the_deadline_and_the_note(Q, direct_alice, direct_bob):
    vm, c, _ = Q
    for d in (True, "tomorrow", None):
        with vm.expect_revert("deadline must be a whole number"):
            _fund(vm, c, direct_alice, direct_bob, deadline=d)
    for d in (T0_TS - 1, T0_TS + 3599, T0_TS + 730 * 86400 + 1):
        with vm.expect_revert("at least an hour ahead"):
            _fund(vm, c, direct_alice, direct_bob, deadline=d)
    _fund(vm, c, direct_alice, direct_bob, oid="lo", deadline=T0_TS + 3600)
    _fund(vm, c, direct_alice, direct_bob, oid="hi", deadline=T0_TS + 730 * 86400)
    _fund(vm, c, direct_alice, direct_bob, oid="n140", note="n" * 140)
    with vm.expect_revert("note"):
        _fund(vm, c, direct_alice, direct_bob, oid="n141", note="n" * 141)
    with vm.expect_revert("note"):
        _fund(vm, c, direct_alice, direct_bob, oid="nn", note=5)


# ---------------------------------------------------------------------------
# decisions move no money
# ---------------------------------------------------------------------------


def test_release_records_the_legs_and_moves_no_money(Q, direct_alice, direct_bob):
    vm, c, sends = Q
    _fund(vm, c, direct_alice, direct_bob)
    vm.sender = direct_bob
    c.release("o1")
    o = c.get_order("o1")
    assert (o["status"], o["outcome"], o["reason"], o["decided_at"]) == ("decided", "release", "released", T0_TS)
    assert [(l["to"].lower(), l["amount"], l["scheduled"]) for l in o["legs"]] == [(R1, 600, False), (R2, 400, False)]
    assert sends == []
    assert c.get_accounting()["total_scheduled"] == 0


def test_refund_records_one_leg_back_to_the_payer(Q, direct_alice, direct_bob):
    vm, c, sends = Q
    _fund(vm, c, direct_alice, direct_bob)
    vm.sender = direct_bob
    c.refund("o1")
    o = c.get_order("o1")
    assert (o["status"], o["outcome"], o["reason"]) == ("decided", "refund", "refunded")
    assert [(l["to"].lower(), l["amount"]) for l in o["legs"]] == [(_hex(direct_alice), 1000)]
    assert sends == []


def test_only_the_releaser_can_release_or_refund(Q, direct_alice, direct_bob, direct_charlie):
    vm, c, _ = Q
    _fund(vm, c, direct_alice, direct_bob)
    for who in (direct_alice, direct_charlie):
        vm.sender = who
        with vm.expect_revert("Only the releaser"):
            c.release("o1")
        with vm.expect_revert("Only the releaser"):
            c.refund("o1")
    assert c.get_order("o1")["status"] == "open"


def test_a_decision_cannot_be_made_twice_or_changed(Q, direct_alice, direct_bob):
    vm, c, _ = Q
    _fund(vm, c, direct_alice, direct_bob)
    vm.sender = direct_bob
    c.release("o1")
    for fn in (c.release, c.refund):
        with vm.expect_revert("Not open"):
            fn("o1")
    vm.warp(AFTER_DEADLINE)
    vm.sender = direct_alice
    with vm.expect_revert("Not open"):
        c.reclaim("o1")
    assert c.get_order("o1")["outcome"] == "release"


def test_reclaim_is_for_the_payer_after_the_deadline_only(Q, direct_alice, direct_bob, direct_charlie):
    vm, c, sends = Q
    _fund(vm, c, direct_alice, direct_bob)
    vm.sender = direct_alice
    with vm.expect_revert("not passed"):
        c.reclaim("o1")
    vm.warp(AT_DEADLINE)
    with vm.expect_revert("not passed"):
        c.reclaim("o1")
    vm.warp(AFTER_DEADLINE)
    for who in (direct_bob, direct_charlie):
        vm.sender = who
        with vm.expect_revert("Only the payer"):
            c.reclaim("o1")
    vm.sender = direct_alice
    c.reclaim("o1")
    o = c.get_order("o1")
    assert (o["outcome"], o["reason"]) == ("refund", "deadline")
    assert [(l["to"].lower(), l["amount"]) for l in o["legs"]] == [(_hex(direct_alice), 1000)]
    assert sends == []


def test_the_releaser_can_still_release_after_the_deadline_until_the_payer_reclaims(Q, direct_alice, direct_bob):
    vm, c, _ = Q
    _fund(vm, c, direct_alice, direct_bob)
    vm.warp(AFTER_DEADLINE)
    vm.sender = direct_bob
    c.release("o1")
    vm.sender = direct_alice
    with vm.expect_revert("Not open"):
        c.reclaim("o1")


def test_the_payer_who_reclaims_first_beats_a_late_release(Q, direct_alice, direct_bob):
    vm, c, _ = Q
    _fund(vm, c, direct_alice, direct_bob)
    vm.warp(AFTER_DEADLINE)
    vm.sender = direct_alice
    c.reclaim("o1")
    vm.sender = direct_bob
    for fn in (c.release, c.refund):
        with vm.expect_revert("Not open"):
            fn("o1")


# ---------------------------------------------------------------------------
# settle
# ---------------------------------------------------------------------------


def test_settle_pays_every_leg_once_in_its_own_transaction(Q, direct_alice, direct_bob, direct_charlie):
    vm, c, sends = Q
    _fund(vm, c, direct_alice, direct_bob)
    vm.sender = direct_bob
    c.release("o1")
    assert sends == []
    vm.sender = direct_charlie  # anyone can settle
    c.settle("o1")
    assert sends == [(R1, 600), (R2, 400)]
    o = c.get_order("o1")
    assert o["status"] == "settled" and all(l["scheduled"] for l in o["legs"])
    a = c.get_accounting()
    assert (a["total_received"], a["total_scheduled"], a["unscheduled"]) == (1000, 1000, 0)


def test_settle_pays_a_refund_to_the_payer(Q, direct_alice, direct_bob):
    vm, c, sends = Q
    _fund(vm, c, direct_alice, direct_bob)
    vm.sender = direct_bob
    c.refund("o1")
    c.settle("o1")
    assert sends == [(_hex(direct_alice), 1000)]


def test_settle_is_refused_before_a_decision_and_after_settlement(Q, direct_alice, direct_bob):
    vm, c, sends = Q
    _fund(vm, c, direct_alice, direct_bob)
    with vm.expect_revert("No outcome"):
        c.settle("o1")
    vm.sender = direct_bob
    c.release("o1")
    c.settle("o1")
    for _ in range(3):
        with vm.expect_revert("No outcome"):
            c.settle("o1")
    assert sends == [(R1, 600), (R2, 400)]


def test_nothing_can_make_a_settled_order_pay_again(Q, direct_alice, direct_bob):
    vm, c, sends = Q
    _fund(vm, c, direct_alice, direct_bob)
    vm.sender = direct_bob
    c.release("o1")
    c.settle("o1")
    vm.warp(AFTER_DEADLINE)
    for who, fn in ((direct_alice, c.reclaim), (direct_bob, c.release), (direct_bob, c.refund), (direct_alice, c.settle)):
        vm.sender = who
        with vm.expect_revert():
            fn("o1")
    assert sum(v for _, v in sends) == 1000


def test_orders_are_independent(Q, direct_alice, direct_bob, direct_charlie):
    vm, c, sends = Q
    _fund(vm, c, direct_alice, direct_bob, oid="a", amounts=(500, 500))
    _fund(vm, c, direct_charlie, direct_bob, oid="b", recipients=(R3,), amounts=(250,))
    vm.sender = direct_bob
    c.release("b")
    c.settle("b")
    assert sends == [(R3, 250)]
    assert c.get_order("a")["status"] == "open"
    c.refund("a")
    c.settle("a")
    assert sends[1:] == [(_hex(direct_alice), 1000)]
    a = c.get_accounting()
    assert (a["total_received"], a["total_scheduled"]) == (1250, 1250)


def test_a_settlement_can_be_made_by_anyone_even_a_recipient(Q, direct_alice, direct_bob):
    vm, c, sends = Q
    _fund(vm, c, direct_alice, direct_bob, recipients=(_hex(direct_bob),), amounts=(7,))
    vm.sender = direct_bob
    c.release("o1")
    c.settle("o1")
    assert sends == [(_hex(direct_bob), 7)]


# ---------------------------------------------------------------------------
# views, accounting, structure
# ---------------------------------------------------------------------------


def test_unknown_orders_are_refused(Q):
    vm, c, _ = Q
    for fn in (c.get_order, c.release, c.refund, c.reclaim, c.settle):
        with vm.expect_revert("not found"):
            fn("nope")
        with vm.expect_revert("not found"):
            fn(5)


def test_paging_is_bounded_and_safe(Q, direct_alice, direct_bob):
    vm, c, _ = Q
    for i in range(7):
        _fund(vm, c, direct_alice, direct_bob, oid=f"o{i}", recipients=(R1,), amounts=(1,))
    assert c.get_order_ids(0, 3) == ["o0", "o1", "o2"]
    assert c.get_order_ids(5, 10) == ["o5", "o6"]
    assert c.get_order_ids(7, 10) == []
    for off, lim in ((-1, 5), (0, 0), (0, -1), (True, 5), (0, True), ("0", 5)):
        assert c.get_order_ids(off, lim) == []
    assert len(c.get_order_ids(0, 10**6)) == 7


def test_a_page_never_holds_more_than_fifty_ids(Q, direct_alice, direct_bob):
    vm, c, _ = Q
    for i in range(53):
        _fund(vm, c, direct_alice, direct_bob, oid=f"p{i}", recipients=(R1,), amounts=(1,))
    assert len(c.get_order_ids(0, 1000)) == 50
    assert c.get_order_ids(50, 1000) == ["p50", "p51", "p52"]
    assert c.get_count() == 53


def test_accounting_tracks_a_payment_until_it_lands(Q, direct_alice, direct_bob):
    vm, c, _ = Q
    me = vm._contract_address
    _fund(vm, c, direct_alice, direct_bob)
    vm.deal(me, 1000)
    a = c.get_accounting()
    assert (a["total_received"], a["unscheduled"], a["in_flight"], a["shortfall"]) == (1000, 1000, 0, 0)
    vm.sender = direct_bob
    c.refund("o1")
    c.settle("o1")
    a = c.get_accounting()
    assert (a["total_scheduled"], a["unscheduled"], a["in_flight"]) == (1000, 0, 1000)
    vm.deal(me, 0)
    a = c.get_accounting()
    assert (a["in_flight"], a["shortfall"]) == (0, 0)


def test_accounting_reports_a_shortfall_and_stranded_value(Q, direct_alice, direct_bob):
    vm, c, _ = Q
    _fund(vm, c, direct_alice, direct_bob)
    vm.deal(vm._contract_address, 400)
    a = c.get_accounting()
    assert (a["in_flight"], a["shortfall"]) == (0, 600)
    vm.deal(vm._contract_address, 1003)  # three wei a refused payable call kept
    a = c.get_accounting()
    assert (a["in_flight"], a["shortfall"]) == (3, 0)


def test_a_refused_fund_leaves_no_trace(Q, direct_alice, direct_bob):
    vm, c, _ = Q
    with vm.expect_revert():
        _fund(vm, c, direct_alice, direct_bob, value=5)
    assert c.get_count() == 0
    with vm.expect_revert("not found"):
        c.get_order("o1")
    _fund(vm, c, direct_alice, direct_bob)  # the id is still free
    assert c.get_count() == 1


def test_removed_entry_points_stay_removed(Q):
    _, c, _ = Q
    for name in ("retry", "retry_payout", "set_releaser", "set_owner", "withdraw", "cancel", "sweep", "upgrade", "transfer_ownership"):
        assert not hasattr(c, name)


def test_the_contract_has_no_model_and_no_nondeterministic_block():
    src = open(CONTRACT, encoding="utf-8").read()
    for forbidden in ("nondet", "exec_prompt", "web.render", "web.get", "run_nondet", "get_contract_at", "random"):
        assert forbidden not in src


def test_exactly_one_transfer_call_exists_and_only_in_settle():
    src = open(CONTRACT, encoding="utf-8").read()
    assert src.count("_pay(") == 2  # the definition and the single use
    body = src.split("def settle", 1)[1].split("@gl.public.view", 1)[0]
    assert "_pay(" in body
    before = src.split("def settle", 1)[0]
    assert "_pay(" not in before.split("def _pay", 1)[1].split("\n", 3)[3]


@pytest.mark.parametrize("seed", range(6))
def test_random_orders_always_settle_to_exactly_what_was_funded(Q, direct_alice, direct_bob, seed):
    """Whatever the mix of releases, refunds, reclaims and repeated calls, the transfers add up to what came in, once each."""
    vm, c, sends = Q
    rnd = random.Random(seed)
    funded = {}
    for i in range(12):
        n = rnd.randint(1, 5)
        amts = [rnd.randint(1, 10**9) for _ in range(n)]
        _fund(vm, c, direct_alice, direct_bob, oid=f"r{i}", recipients=[f"0x{'%02x' % (j + 1) * 20}" for j in range(n)], amounts=amts)
        funded[f"r{i}"] = sum(amts)
    settled = set()
    for _ in range(80):
        oid = f"r{rnd.randint(0, 11)}"
        action = rnd.choice(["release", "refund", "reclaim", "settle"])
        vm.sender = direct_alice if action == "reclaim" else direct_bob
        if action == "reclaim":
            vm.warp(AFTER_DEADLINE)
        try:
            getattr(c, action)(oid)
        except Exception:
            continue
        if action == "settle":
            assert oid not in settled
            settled.add(oid)
    a = c.get_accounting()
    assert a["total_scheduled"] == sum(v for _, v in sends) == sum(funded[o] for o in settled)
    assert a["total_received"] == sum(funded.values())
    for o in settled:
        order = c.get_order(o)
        assert sum(l["amount"] for l in order["legs"]) == order["total"] == funded[o]
