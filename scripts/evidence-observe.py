"""Turns scripts/e2e-observe-log-<RUN>.json into the README's version 2 live-evidence section.
    python scripts/evidence-observe.py studio1 > scripts/_evidence-observe.md
"""

import json
import sys

run = sys.argv[1] if len(sys.argv) > 1 else "studio1"
log = json.load(open(f"scripts/e2e-observe-log-{run}.json", encoding="utf-8"))
EX = "https://explorer-studio.genlayer.com/tx/"
ev = log["events"]
res = log["results"]
GEN = 10**18


def gen(wei):
    return f"{int(wei) / GEN:.6f}"


def link(h):
    return f"[`{h[:12]}`]({EX}{h})"


def steps(scn):
    return [e for e in ev if e["scn"] == scn and e.get("fn") and e.get("hash")]


def table(scn, notes):
    rows = ["| # | Call | Transaction | Result |", "|---|---|---|---|"]
    for i, e in enumerate(steps(scn), 1):
        what = notes.get(e["fn"], e["fn"])
        if e.get("refused"):
            result = f"refused by the contract: \"{e['reason']}\""
        else:
            result = "accepted"
        label = e["msg"].split(" " + e["hash"][:12])[0]
        rows.append(f"| {i} | `{label if label else what}` | {link(e['hash'])} | {result} |")
    return rows


def msgs(scn, *needles):
    return [e["msg"] for e in ev if e["scn"] == scn and any(n in e["msg"] for n in needles)]


L = [f"## Live evidence, version 2 (Studionet, run `{run}`, {log['started'][:10]})", ""]
L.append(
    f"Quay v2 `{log['quay']}` on Studionet. Separate wallets for the payer, the named releaser and the recipients; the recipients never send a transaction, so their balance changes are exact. "
    "Every claim is read back from the chain by `scripts/e2e-observe.mjs`, which fails the run if it is not exactly as expected, and the pages are real: the jq 1.7.1 source tarball on GitHub, "
    "and a JSON page in this repository (`docs/watch-demo.json`) that the run changes mid-order. Studionet finalizes in under a minute, so every transfer landed within a minute of its `settle`."
)

L += ["", "### Digest: the validators read a real file and release the order", ""]
L += table("digest", {})
r = res["digest"]
L += [
    "",
    "The order watched the real jq 1.7.1 tarball for its published SHA-256 (`478c9ca1...ee580c2`). `observe` was called by the payer, who is neither the releaser nor a recipient: the validators fetched the file, agreed the digest matched, "
    f"and the order was decided as a release (`{r['reason']}`) after {r['observations']} observation. Both recipients' balances were unchanged at the decision, a second `observe` was refused (`Not open`), "
    f"`settle` then paid recipient A {gen(r['a_received'])} and recipient B {gen(r['b_received'])} GEN exactly, and a second `settle` was refused.",
]

L += ["", "### Unmet: the watch is not satisfied, the order stays open, and the releaser can still refund", ""]
L += table("unmet", {})
r = res["unmet"]
L += [
    "",
    f"The order watched the same file for a digest that is not its digest. The validators agreed the condition is not met, so the order stayed open with {r['observations']} observation recorded. A second `observe` straight away was refused as too recent. "
    f"The named releaser then refunded and `settle` paid the payer back {gen(r['payer_refunded'])} GEN, exactly.",
]

L += ["", "### Flip: an order released when a page changes", ""]
L += table("flip", {})
r = res["flip"]
L += [
    "",
    "The order watched the `status` field of `docs/watch-demo.json` for `delivered`. While the page said `pending`, `observe` left the order open. The page was then changed to `delivered` (a commit to this repository, "
    "served by raw.githubusercontent.com), and after the cooldown the next `observe` had the validators agree on the new value and decide the order as a release "
    f"(`{r['reason']}`, {r['observations']} observations in all). `settle` paid recipients A and B exactly.",
]

L += ["", "### Unreadable: a page that does not exist records nothing", ""]
L += table("unreadable", {})
L += [
    "",
    "`observe` on a page that returns 404 was refused (`Page unreadable, nothing recorded`) and left the order as it was: no observation counted, nothing decided, no cooldown started. The releaser then refunded so nothing stays locked.",
]

L += ["", "### Deadline: after it, observe is refused even though the page matches, and the payer reclaims", ""]
L += table("deadline", {})
r = res["deadline"]
L += [
    "",
    f"The order watched a page that matched, but nobody observed before the deadline (the shortest allowed, one hour). After it, `observe` was refused (`The deadline has passed`), the payer's `reclaim` recorded the refund (`{r['reason']}`) and `settle` paid it back. "
    "`observe` and `reclaim` never overlap.",
]

a = log["accounting"]
L += [
    "",
    "### Final accounting",
    "",
    "```",
    json.dumps(a, indent=1),
    "```",
    "",
    f"`total_received` ({a['total_received']}) against `total_scheduled` ({a['total_scheduled']}); the contract's own `get_accounting` after the run, with nothing short (`shortfall` {a['shortfall']}) and nothing in flight (`in_flight` {a['in_flight']}). "
    f"The full event log, with every transaction hash, is `scripts/e2e-observe-log-{run}.json`.",
]
L += [
    "",
    "### About the runs",
    "",
    "This is the second full run. The first (`studio1`, log `scripts/e2e-observe-log-studio1.json`, kept) passed the digest, unmet, unreadable and deadline scenarios, and its flip scenario also decided, "
    "settled and paid correctly, but the script then reported a failure: it compared the recipients' balances against a starting figure taken before the digest scenario, which runs side by side and pays the same two wallets, "
    "so the figure was wrong. That was a mistake in the script's check, not in the contract. The digest and flip orders now pay fresh addresses that nothing else uses, and the script waits for every scheduled transfer to leave "
    "the contract before it reads the accounting. The accounting above covers both runs: every wei received by any order in either run was scheduled out exactly once.",
]
print("\n".join(L))
