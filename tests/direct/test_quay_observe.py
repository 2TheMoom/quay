"""Direct-mode tests for Quay's watched orders (fund_watched, observe) and the validator vote inside observe.

A watched order carries a public page and a condition fixed at funding. observe() has the validators fetch that page and agree on whether
the condition holds; only then is the order decided as a release. These tests run the vote the way the network does: the leader proposes
an answer, then a validator that fetches the page on its own accepts exactly the honest answer and nothing else."""

import copy
import hashlib

import pytest

from tests.direct.test_quay import DEADLINE, R1, R2, T0, T0_TS, _capture_sends, _hex

CONTRACT = "contracts/quay.py"
URL = "https://releases.example.com/app.tar.gz"
API = "https://api.example.com/v1/shipments/42"
BUILD = b"the shipped build"
OTHER = b"some other file"
RECHECK = 300


def _d(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _serve(vm, url, body, status=200):
    vm.clear_mocks()
    vm.mock_web("^" + url.replace(".", r"\.") + "$", {"method": "GET", "status": status, "body": body})


def _fund_w(vm, c, payer, releaser, url=URL, kind="sha256", path="", value=None, oid="w1", deadline=DEADLINE, amounts=(600, 400)):
    vm.sender = payer
    vm.value = sum(amounts)
    try:
        c.fund_watched(oid, _hex(releaser), [R1, R2], list(amounts), deadline, "watched", url, kind, path, _d(BUILD) if value is None else value)
    finally:
        vm.value = 0


@pytest.fixture
def W(direct_vm, direct_deploy, direct_alice, direct_bob):
    direct_vm.warp(T0)
    c = direct_deploy(CONTRACT)
    sends = _capture_sends(direct_vm)
    _fund_w(direct_vm, c, direct_alice, direct_bob)
    _serve(direct_vm, URL, BUILD)
    return direct_vm, c, sends


def _leader(vm):
    return copy.deepcopy(vm._captured_validators[-1][0])


# ---------------------------------------------------------------------------------------------------------------- funding


def test_fund_watched_stores_the_watch_and_everything_else_as_fund_does(W):
    vm, c, sends = W
    o = c.get_order("w1")
    assert o["watch"] == {"url": URL, "kind": "sha256", "path": "", "value": _d(BUILD), "observed_at": 0, "observations": 0}
    assert (o["status"], o["total"], o["deadline"]) == ("open", 1000, DEADLINE)
    assert [(p["to"].lower(), p["amount"]) for p in o["payees"]] == [(R1, 600), (R2, 400)]
    assert c.get_accounting()["total_received"] == 1000 and sends == []


def test_a_plain_order_has_no_watch_and_cannot_be_observed(direct_vm, direct_deploy, direct_alice, direct_bob):
    direct_vm.warp(T0)
    c = direct_deploy(CONTRACT)
    direct_vm.sender = direct_alice
    direct_vm.value = 1000
    c.fund("p1", _hex(direct_bob), [R1], [1000], DEADLINE, "plain")
    direct_vm.value = 0
    assert c.get_order("p1")["watch"] == {"url": "", "kind": "", "path": "", "value": "", "observed_at": 0, "observations": 0}
    with direct_vm.expect_revert("no watch"):
        c.observe("p1")


@pytest.mark.parametrize("url", [
    "http://releases.example.com/a", "ftp://releases.example.com/a", "releases.example.com/a", "https://user:pw@releases.example.com/a",
    "https://releases.example.com:8443/a", "https://localhost/a", "https://intranet.corp/a", "https://printer.local/a", "https://10.0.0.1/a",
    "https://-bad.example.com/a", "https://exa mple.com/a", "https://example.com/a'b", "https://" + "a" * 64 + ".com/x", "https://example.com/" + "a" * 300,
])
def test_a_watch_url_must_be_a_public_https_address(direct_vm, direct_deploy, direct_alice, direct_bob, url):
    direct_vm.warp(T0)
    c = direct_deploy(CONTRACT)
    with direct_vm.expect_revert():
        _fund_w(direct_vm, c, direct_alice, direct_bob, url=url)
    assert c.get_count() == 0


@pytest.mark.parametrize("url,why", [
    ("https://releases.example.com/a b", "forbidden character"), ("https://releases.example.com/a\tb", "forbidden character"), ("https://releases.example.com/a\nb", "forbidden character"),
    ("https://user@releases.example.com/x", "credentials or a port"), ("https://releases.example.com:8443/x", "credentials or a port"),
    ("https://example/x", "public domain name"), ("https://a..example.com/x", "invalid url host"), ("https://" + "a" * 64 + ".example.com/x", "invalid url host"),
    ("https://exa_mple.com/x", "invalid url host"), ("https://exa%6Dple.com/x", "invalid url host"),
])
def test_a_bad_watch_url_is_refused_for_the_right_reason(direct_vm, direct_deploy, direct_alice, direct_bob, url, why):
    direct_vm.warp(T0)
    c = direct_deploy(CONTRACT)
    with direct_vm.expect_revert(why):
        _fund_w(direct_vm, c, direct_alice, direct_bob, url=url)
    assert c.get_count() == 0


@pytest.mark.parametrize("kw,why", [
    ({"kind": "regex"}, "watch kind"),
    ({"kind": ""}, "watch kind"),
    ({"kind": "sha256", "path": "a.b"}, "no path"),
    ({"kind": "sha256", "value": "ABCDEF" * 10 + "abcd"}, "64 lowercase hex"),
    ({"kind": "sha256", "value": "ab" * 31}, "64 lowercase hex"),
    ({"kind": "sha256", "value": "zz" * 32}, "64 lowercase hex"),
    ({"kind": "json_eq", "path": "", "value": "x"}, "path"),
    ({"kind": "json_eq", "path": "a..b", "value": "x"}, "path"),
    ({"kind": "json_eq", "path": ".a", "value": "x"}, "path"),
    ({"kind": "json_eq", "path": "a b", "value": "x"}, "path"),
    ({"kind": "json_eq", "path": "a/b", "value": "x"}, "path"),
    ({"kind": "json_eq", "path": "a" * 41, "value": "x"}, "path"),
    ({"kind": "json_eq", "path": "a." * 50 + "b", "value": "x"}, "path"),
    ({"kind": "json_eq", "path": "status", "value": ""}, "value must be"),
    ({"kind": "json_eq", "path": "status", "value": "x" * 201}, "value must be"),
])
def test_the_watch_terms_are_checked_before_any_state_changes(direct_vm, direct_deploy, direct_alice, direct_bob, kw, why):
    direct_vm.warp(T0)
    c = direct_deploy(CONTRACT)
    with direct_vm.expect_revert(why):
        _fund_w(direct_vm, c, direct_alice, direct_bob, **kw)
    assert c.get_count() == 0 and c.get_accounting()["total_received"] == 0


def test_an_empty_url_with_other_watch_fields_is_refused(direct_vm, direct_deploy, direct_alice, direct_bob):
    direct_vm.warp(T0)
    c = direct_deploy(CONTRACT)
    with direct_vm.expect_revert("need a url"):
        _fund_w(direct_vm, c, direct_alice, direct_bob, url="", kind="sha256")
    direct_vm.sender = direct_alice
    direct_vm.value = 1000
    c.fund_watched("e1", _hex(direct_bob), [R1], [1000], DEADLINE, "n", "", "", "", "")  # an all-empty watch is a plain order
    direct_vm.value = 0
    assert c.get_order("e1")["watch"]["url"] == ""


# ---------------------------------------------------------------------------------------------------------------- observing


def test_a_met_sha256_watch_decides_a_release_from_the_terms_and_moves_no_money(W, direct_charlie):
    vm, c, sends = W
    vm.sender = direct_charlie  # nobody in particular: observe is permissionless
    c.observe("w1")
    o = c.get_order("w1")
    assert (o["status"], o["outcome"], o["reason"]) == ("decided", "release", "observed")
    assert [(g["to"].lower(), g["amount"]) for g in o["legs"]] == [(R1, 600), (R2, 400)]
    assert o["watch"]["observations"] == 1 and o["watch"]["observed_at"] == T0_TS
    assert sends == []  # a decision moves nothing


def test_the_observed_release_is_paid_once_by_settle_in_its_own_transaction(W, direct_charlie):
    vm, c, sends = W
    vm.sender = direct_charlie
    c.observe("w1")
    c.settle("w1")
    assert sends == [(R1, 600), (R2, 400)]
    with vm.expect_revert("No outcome to settle"):
        c.settle("w1")
    assert sends == [(R1, 600), (R2, 400)]


def test_an_unmet_watch_records_the_look_and_leaves_the_order_open(W):
    vm, c, sends = W
    _serve(vm, URL, OTHER)
    c.observe("w1")
    o = c.get_order("w1")
    assert (o["status"], o["legs"]) == ("open", [])
    assert o["watch"]["observations"] == 1 and o["watch"]["observed_at"] == T0_TS


@pytest.mark.parametrize("body,path,value,met", [
    ('{"status": "delivered"}', "status", "delivered", True),
    ('{"status": "in transit"}', "status", "delivered", False),
    ('{"a": {"b": {"c": "deep"}}}', "a.b.c", "deep", True),
    ('{"a": {"b": {"c": "deep"}}}', "a.b", "deep", False),  # an object is never equal to a string
    ('{"a": 1}', "b", "1", False),  # a key that is not there
    ('{"a": 1}', "a", "1", True),
    ('{"items": [{"id": "x"}, {"id": "y"}]}', "items.1.id", "y", True),
    ('{"items": [{"id": "x"}, {"id": "y"}]}', "items.2.id", "y", False),  # out of range
    ('{"items": [{"id": "x"}]}', "items.id", "x", False),  # a list needs a position
    ('{"done": true}', "done", "true", True),
    ('{"done": false}', "done", "true", False),
    ('{"n": 5}', "n", "5", True),
    ('{"n": 5.5}', "n", "5.5", True),
    ('{"n": "5"}', "n", "5", True),
    ('{"n": 5}', "n", "5.0", False),
    ('{"gone": null}', "gone", "null", True),
    ('{"status": "Delivered"}', "status", "delivered", False),  # exact, case sensitive
    ('{"status": "delivered"} trailing', "status", "delivered", False),  # not JSON
    ("not json at all", "status", "delivered", False),
    ('[1, 2]', "status", "delivered", False),
    ('{"__class__": "x"}', "__class__", "x", True),  # a key is only ever looked up, never executed
])
def test_a_json_field_watch_compares_the_exact_text_of_one_field(direct_vm, direct_deploy, direct_alice, direct_bob, body, path, value, met):
    direct_vm.warp(T0)
    c = direct_deploy(CONTRACT)
    _fund_w(direct_vm, c, direct_alice, direct_bob, url=API, kind="json_eq", path=path, value=value)
    _serve(direct_vm, API, body)
    c.observe("w1")
    assert (c.get_order("w1")["status"] == "decided") is met
    assert direct_vm._captured_validators[-1][0] == {"kind": "read", "met": met}


@pytest.mark.parametrize("status,body", [(404, "x"), (500, "x"), (503, ""), (200, ""), pytest.param(200, b"x" * 2_000_001, id="too-large")])
def test_an_unreadable_page_records_nothing(W, status, body):
    vm, c, _ = W
    _serve(vm, URL, body, status=status)
    with vm.expect_revert("unreadable"):
        c.observe("w1")
    o = c.get_order("w1")
    assert (o["status"], o["watch"]["observations"], o["watch"]["observed_at"]) == ("open", 0, 0)


def test_a_page_that_cannot_be_reached_at_all_records_nothing(W):
    vm, c, _ = W
    vm.clear_mocks()  # no mock for the url: the request itself fails
    with vm.expect_revert("unreadable"):
        c.observe("w1")
    assert c.get_order("w1")["watch"]["observations"] == 0


def test_observations_are_rate_limited_per_order(W):
    vm, c, _ = W
    _serve(vm, URL, OTHER)
    c.observe("w1")
    with vm.expect_revert("too recently"):
        c.observe("w1")
    vm.warp("2026-01-01T00:04:59Z")
    with vm.expect_revert("too recently"):
        c.observe("w1")
    vm.warp("2026-01-01T00:05:00Z")
    c.observe("w1")
    assert c.get_order("w1")["watch"]["observations"] == 2
    _serve(vm, URL, BUILD)
    vm.warp("2026-01-01T00:10:00Z")
    c.observe("w1")
    assert c.get_order("w1")["status"] == "decided"


def test_an_unreadable_look_does_not_start_the_rate_limit(W):
    vm, c, _ = W
    _serve(vm, URL, "x", status=503)
    with vm.expect_revert("unreadable"):
        c.observe("w1")
    _serve(vm, URL, BUILD)
    c.observe("w1")  # the same instant, and it goes through
    assert c.get_order("w1")["status"] == "decided"


def test_nothing_can_be_observed_after_the_deadline_so_the_payers_reclaim_stays_clean(W, direct_alice):
    vm, c, _ = W
    vm.warp("2026-01-02T00:00:00Z")  # exactly the deadline: still the observers' moment
    c.observe("w1")
    assert c.get_order("w1")["status"] == "decided"


def test_after_the_deadline_observe_is_refused_and_the_payer_can_reclaim(direct_vm, direct_deploy, direct_alice, direct_bob):
    direct_vm.warp(T0)
    c = direct_deploy(CONTRACT)
    sends = _capture_sends(direct_vm)
    _fund_w(direct_vm, c, direct_alice, direct_bob)
    _serve(direct_vm, URL, BUILD)  # the condition IS met, but too late
    direct_vm.warp("2026-01-02T00:00:01Z")
    with direct_vm.expect_revert("deadline has passed"):
        c.observe("w1")
    direct_vm.sender = direct_alice
    c.reclaim("w1")
    c.settle("w1")
    assert sends == [(_hex(direct_alice), 1000)]


def test_a_decided_or_settled_order_cannot_be_observed_again(W, direct_bob):
    vm, c, sends = W
    vm.sender = direct_bob
    c.refund("w1")  # the releaser decides first
    with vm.expect_revert("Not open"):
        c.observe("w1")
    c.settle("w1")
    with vm.expect_revert("Not open"):
        c.observe("w1")
    assert len(sends) == 1


def test_an_observed_release_cannot_be_refunded_or_reclaimed_afterwards(W, direct_alice, direct_bob):
    vm, c, _ = W
    c.observe("w1")
    vm.sender = direct_bob
    with vm.expect_revert("Not open"):
        c.refund("w1")
    vm.warp("2026-01-02T00:00:01Z")
    vm.sender = direct_alice
    with vm.expect_revert("Not open"):
        c.reclaim("w1")


def test_the_releaser_can_still_decide_a_watched_order_before_anyone_observes(W, direct_bob):
    vm, c, sends = W
    vm.sender = direct_bob
    c.release("w1")
    assert c.get_order("w1")["reason"] == "released"


def test_an_unknown_order_cannot_be_observed(W):
    vm, c, _ = W
    with vm.expect_revert("not found"):
        c.observe("nope")


def test_orders_are_watched_independently(direct_vm, direct_deploy, direct_alice, direct_bob):
    direct_vm.warp(T0)
    c = direct_deploy(CONTRACT)
    _fund_w(direct_vm, c, direct_alice, direct_bob, oid="a")
    _fund_w(direct_vm, c, direct_alice, direct_bob, oid="b", url=API, kind="json_eq", path="status", value="delivered")
    _serve(direct_vm, URL, BUILD)
    c.observe("a")
    assert c.get_order("a")["status"] == "decided" and c.get_order("b")["status"] == "open"
    assert c.get_order("b")["watch"]["observations"] == 0


# ---------------------------------------------------------------------------------------------------------------- the validator vote


def test_the_leader_proposes_what_the_page_shows(W):
    vm, c, _ = W
    c.observe("w1")
    assert _leader(vm) == {"kind": "read", "met": True}


def test_a_validator_that_reads_the_same_page_accepts(W):
    vm, c, _ = W
    c.observe("w1")
    assert vm.run_validator() is True


def test_a_validator_that_sees_a_different_page_rejects_a_met_claim(W):
    """The attack the vote exists to stop: a leader that says the condition is met while the page does not satisfy it."""
    vm, c, _ = W
    c.observe("w1")
    _serve(vm, URL, OTHER)
    assert vm.run_validator() is False
    assert vm.run_validator(leader_result={"kind": "read", "met": True}) is False
    assert vm.run_validator(leader_result={"kind": "read", "met": False}) is True  # and it accepts the leader that tells the truth


def test_a_validator_that_sees_the_condition_met_rejects_an_unmet_claim(W):
    vm, c, _ = W
    _serve(vm, URL, OTHER)
    c.observe("w1")
    assert _leader(vm) == {"kind": "read", "met": False}
    _serve(vm, URL, BUILD)
    assert vm.run_validator() is False


@pytest.mark.parametrize("status,body", [(404, "x"), (500, "x"), (200, ""), pytest.param(200, b"x" * 2_000_001, id="too-large")])
def test_a_validator_that_cannot_read_the_page_rejects_a_read_claim(W, status, body):
    vm, c, _ = W
    c.observe("w1")
    _serve(vm, URL, body, status=status)
    assert vm.run_validator() is False


def test_validators_agree_that_an_unreadable_page_is_unreadable(W):
    vm, c, _ = W
    _serve(vm, URL, "x", status=404)
    with vm.expect_revert("unreadable"):
        c.observe("w1")
    assert _leader(vm) == {"kind": "unreadable", "met": False}
    assert vm.run_validator() is True  # it could not read it either
    _serve(vm, URL, BUILD)
    assert vm.run_validator() is False  # but a validator that can read it does not accept "unreadable"


@pytest.mark.parametrize("field,value", [("met", False), ("kind", "unreadable"), ("kind", "other")])
def test_a_validator_rejects_a_leader_result_with_any_field_altered(W, field, value):
    vm, c, _ = W
    c.observe("w1")
    forged = _leader(vm)
    forged[field] = value
    assert vm.run_validator(leader_result=forged) is False


@pytest.mark.parametrize("junk", [None, [], "read", 5, True, {}, {"kind": "read"}, {"met": True}, {"kind": 1, "met": True}, {"kind": "read", "met": "yes"}, {"kind": "read", "met": 1}])
def test_a_validator_rejects_a_leader_result_of_the_wrong_shape(W, junk):
    vm, c, _ = W
    c.observe("w1")
    assert vm.run_validator(leader_result=junk) is False


def test_a_validator_rejects_a_leader_that_errored(W):
    vm, c, _ = W
    c.observe("w1")
    assert vm.run_validator(leader_error=Exception("the leader crashed")) is False


def test_the_vote_judges_a_json_watch_by_what_the_validator_itself_reads(direct_vm, direct_deploy, direct_alice, direct_bob):
    direct_vm.warp(T0)
    c = direct_deploy(CONTRACT)
    _fund_w(direct_vm, c, direct_alice, direct_bob, url=API, kind="json_eq", path="status", value="delivered")
    _serve(direct_vm, API, '{"status": "delivered"}')
    c.observe("w1")
    assert direct_vm.run_validator() is True
    _serve(direct_vm, API, '{"status": "lost"}')
    assert direct_vm.run_validator() is False


# ---------------------------------------------------------------------------------------------------------------- edges


@pytest.mark.parametrize("terms", [(5, "sha256", "", "x"), (URL, 5, "", "x"), (URL, "json_eq", 5, "x"), (URL, "json_eq", "a", 5), (None, "", "", ""), ("", None, "", "")])
def test_watch_terms_must_be_strings(direct_vm, direct_deploy, direct_alice, direct_bob, terms):
    direct_vm.warp(T0)
    c = direct_deploy(CONTRACT)
    direct_vm.sender = direct_alice
    direct_vm.value = 1000
    with direct_vm.expect_revert("must be strings"):
        c.fund_watched("t1", _hex(direct_bob), [R1], [1000], DEADLINE, "n", *terms)
    direct_vm.value = 0
    assert c.get_count() == 0


def test_a_url_fragment_is_dropped_from_the_stored_watch(direct_vm, direct_deploy, direct_alice, direct_bob):
    direct_vm.warp(T0)
    c = direct_deploy(CONTRACT)
    _fund_w(direct_vm, c, direct_alice, direct_bob, url=URL + "#top")
    assert c.get_order("w1")["watch"]["url"] == URL


def test_a_url_may_be_exactly_three_hundred_characters_and_not_one_more(direct_vm, direct_deploy, direct_alice, direct_bob):
    direct_vm.warp(T0)
    c = direct_deploy(CONTRACT)
    base = "https://releases.example.com/"
    ok = base + "a" * (300 - len(base))
    assert len(ok) == 300
    _fund_w(direct_vm, c, direct_alice, direct_bob, url=ok)
    with direct_vm.expect_revert("at most 300"):
        _fund_w(direct_vm, c, direct_alice, direct_bob, url=ok + "a", oid="w2")


def test_an_object_or_list_never_equals_a_text_value(direct_vm, direct_deploy, direct_alice, direct_bob):
    direct_vm.warp(T0)
    c = direct_deploy(CONTRACT)
    _fund_w(direct_vm, c, direct_alice, direct_bob, url=API, kind="json_eq", path="a", value="{'b': 'c'}")
    _serve(direct_vm, API, '{"a": {"b": "c"}}')
    c.observe("w1")
    assert c.get_order("w1")["status"] == "open"
    _fund_w(direct_vm, c, direct_alice, direct_bob, url=API, kind="json_eq", path="a", value="['b']", oid="w2")
    _serve(direct_vm, API, '{"a": ["b"]}')
    c.observe("w2")
    assert c.get_order("w2")["status"] == "open"


def test_a_validator_rejects_an_unreadable_claim_even_when_the_page_it_reads_is_also_unmet(W):
    """Same answer on 'met', different answer on 'was it readable': the vote has to compare both."""
    vm, c, _ = W
    _serve(vm, URL, "x", status=404)
    with vm.expect_revert("unreadable"):
        c.observe("w1")
    _serve(vm, URL, OTHER)  # readable, condition not met
    assert vm.run_validator() is False
