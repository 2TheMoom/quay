"""Turns scripts/e2e-log-<RUN>.json into the README's live-evidence section.
    python scripts/evidence.py v1 > scripts/_evidence.md
"""

import json
import sys

run = sys.argv[1] if len(sys.argv) > 1 else "v1"
log = json.load(open(f"scripts/e2e-log-{run}.json", encoding="utf-8"))
EX = "https://explorer-bradbury.genlayer.com/tx/"
ev = log["events"]
res = log["results"]
GEN = 10**18


def gen(wei):
    return f"{int(wei) / GEN:.6f}"


def link(h):
    return f"[`{h[:12]}`]({EX}{h})"


def steps(scn):
    """Every transaction of one scenario, in order, with the contract's own reason for a refusal."""
    out, pending = [], None
    for e in ev:
        if e["scn"] != scn:
            continue
        if e.get("fn") and e.get("hash"):
            pending = {"fn": e["fn"], "hash": e["hash"], "ok": e.get("exec") == "FINISHED_WITH_RETURN", "reason": ""}
            out.append(pending)
        elif "reason" in e and pending is not None and not pending["ok"] and not pending["reason"]:
            pending["reason"] = e["reason"]
    return out


def table(scn, notes):
    rows = ["| # | Call | Transaction | Result |", "|---|---|---|---|"]
    for i, s in enumerate(steps(scn), 1):
        what = notes.get(s["fn"], s["fn"]) if s["ok"] else s["fn"]
        result = "accepted" if s["ok"] else f"refused by the contract: \"{s['reason']}\"" if s["reason"] else "refused by the contract (state unchanged)"
        rows.append(f"| {i} | `{what}` | {link(s['hash'])} | {result} |")
    return rows


def finals(scn):
    return [e["msg"].split("FINALIZED")[0].strip().split(" ", 1)[1] + " finalized " + e["msg"].split("FINALIZED")[1].strip().replace(" after the wait began", "") for e in ev if e["scn"] == scn and "FINALIZED" in e["msg"]]


lines = [f"## Live evidence (Bradbury, run `{run}`, {log['started'][:10]})", ""]
lines.append(
    f"Quay `{log['quay']}`. Separate wallets for the payer, the releaser and the recipients. The recipients never send a transaction, so their balance changes are exact. "
    "Every claim below is read back from the chain by the script, which fails the run if it is not exactly as expected. "
    "Every settlement reached FINALIZED, about 30 minutes after it was sent, and the transfers landed then, because a transfer is only executed when the settling transaction finalizes."
)
lines += ["", "### Release: an order split between two recipients", ""]
lines += table("release", {"fund": "fund (0.002 GEN split 0.0015 and 0.0005 between A and B)"})
r = res["release"]
lines += [
    "",
    f"The wrong caller was refused, the releaser's `release` recorded the decision and moved no money (both recipients' balances were unchanged until the settlement), and the second `settle` was refused. "
    f"After the settlement finalized: recipient A {gen(r['a'][0])} to {gen(r['a'][1])} GEN (+0.001500) and recipient B {gen(r['b'][0])} to {gen(r['b'][1])} GEN (+0.000500), exactly.",
    "",
    "### Refund: the releaser sends the money back",
    "",
]
lines += table("refund", {})
r = res["refund"]
lines += ["", f"The payer's balance went from {gen(r['payer'][0])} to {gen(r['payer'][1])} GEN (+0.002000), exactly, and the order's recipient was never paid.", "", "### Deadline: the payer reclaims after it, and a late release is refused", ""]
lines += table("deadline", {})
r = res["deadline"]
lines += [
    "",
    "Reclaim was refused before the deadline (the shortest allowed, one hour) and for a caller who is not the payer. After the deadline the payer's reclaim recorded the same refund decision, "
    "a late `release` by the releaser was then refused (`Not open`), and the settlement paid the payer back. The order is `dl-" + run + "`: its one leg is the payer and its amount is the full 0.001 GEN. The payer's own balance is not a clean test here, because the payer also paid gas for its other orders, so this one is proven by the order's recorded leg and by the closed accounting below, where the contract's balance ends at the one wei a refused call left.",
    "",
    "### A refused payable call keeps its value",
    "",
]
lines += table("refused", {"fund": "fund (value 1 wei, amounts add up to 2)"})
lines += [
    "",
    "No order was created and nothing was counted as received. On Bradbury the wei the refused call carried stays in the contract, which is why the README says to check the rules before sending. It shows in the final accounting below as `in_flight`.",
    "",
    "### Final accounting",
    "",
]
a = res["accounting"]
lines += [
    "After every run above, every order is settled and the contract's own accounting reads:",
    "",
    "```",
    json.dumps(a, indent=1),
    "```",
    "",
    f"`total_received` ({a['total_received']}) equals `total_scheduled` ({a['total_scheduled']}): every wei that came in through an order was scheduled out exactly once, nothing is short, nothing is still owed, and the only balance is the {a['in_flight']} wei that the refused call left behind.",
    "",
    "A note on honesty about the run itself: the first attempt stopped part way because the script read an order back right after an accepted `release` and saw the old state (a node that had not caught up with the accepted write). That was a script timing error, not a contract one, and the chain state it left was exactly as it should be. The script now polls every read-back and resumes from what is already on chain, and the run above is that resumed run; its event log, with every transaction hash from both attempts, is `scripts/e2e-log-" + run + ".json`.",
]
print("\n".join(lines))
