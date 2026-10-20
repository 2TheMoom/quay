# Quay: design and threat model

Quay holds GEN for an order, lets one named address (the releaser) decide whether it goes to the recipients or back to the payer, and pays the decision exactly once. It is a payout rail, not a marketplace: it does not judge anything. The releaser can be a person, a multisig, or another Intelligent Contract that judges (an arbiter, a verifier-backed escrow).

## Shape

```
fund      payer sends GEN: releaser, recipients and exact amounts, deadline, note. Amounts must add up to what was sent.
release   releaser only, while the order is open. Records the decision "pay the recipients". Moves no money.
refund    releaser only, while the order is open. Records the decision "pay the payer back". Moves no money.
reclaim   payer only, after the deadline, while still open. Records the same refund decision.
settle    anyone, once a decision exists. Pays every leg, once, in its own transaction.
```

A decision (`release`, `refund`, `reclaim`) and its payment (`settle`) are never in the same transaction. The contract has no model, no web request and no nondeterministic block, so a transfer can never share a transaction with one. That is the lesson from our earlier work: a leader rotation can leave a stray transfer message behind a nondeterministic block.

## Deliberate absences

- **No payout retry.** Earlier projects shipped a retry for lost transfers and stewards rejected it twice: a retry pays from a pooled balance, so a retry of a transfer that was only slow (it lands when the transaction finalizes, about 30 minutes later) pays twice and the second payment comes out of someone else's order. Quay instead pays each leg once and shows money that has been scheduled but has not left the contract as `in_flight` in `get_accounting`. If the platform ever loses a transfer, the money stays visible and nobody else's funds are touched.
- **No owner, no admin, no setter, no upgrade.** Nothing about an order can be changed after `fund`, including the releaser, the recipients and the amounts.
- **No fees and no rounding.** Every amount is stated by the payer in wei and has to add up exactly.

## Threats considered

| # | Threat | Defense | Where it is tested |
|---|---|---|---|
| 1 | A payout is paid twice | `settle` needs status `decided`, sets `settled` first, and records each leg in a ledger that refuses a second entry. Direct refunds also end in `settled` | settle and terminal-state tests, ledger guard test |
| 2 | Money goes to the wrong party | Recipients and amounts are fixed at `fund`. The legs are built from them at decision time, never from the caller | decision tests, leg-shape tests |
| 3 | The payouts do not add up to the funds | `fund` checks the amounts sum to exactly the value sent, and decision time checks the legs sum to the order total | sum tests |
| 4 | The wrong person decides | `release` and `refund` are releaser only, `reclaim` is payer only, and only while open | access tests for every entry point |
| 5 | The releaser and the payer race | Whoever reaches the open order first wins, the other is refused. After a decision nothing can change it | race tests |
| 6 | Funds locked forever | The payer can reclaim after the deadline. A decision that nobody settles can be settled by anyone | deadline and settle tests, live run |
| 7 | A refused payable call keeps its value (Bradbury behavior) | Every rule is checked before any state changes and the amount is checked last, so a bad call fails before it is counted. A refused call still keeps the value it carried, so it is shown as `in_flight`. Callers check first | refused-call test, accounting test, live run |
| 8 | One order drains another through the shared balance | There is no retry, no refund that is not backed by a recorded order, and every payment is a leg of one decided order | accounting tests, invariant test |
| 9 | A hostile recipient or releaser contract | Quay makes no call into a recipient except the transfer message. It reads no state from anyone | design, tested by absence of any other call |
| 10 | Order id squatting or collision | An id can be used once, 1 to 64 characters from a fixed alphabet | id tests |
| 11 | Integer and type confusion (bool as an int, a float, a negative amount) | Amounts must be plain ints above zero, times plain ints, and every address is parsed or refused | type tests |
| 12 | Griefing with huge orders | At most 10 recipients, a short note, a bounded deadline | bound tests |

## What it cannot do

It cannot tell whether the releaser is right. Whoever funds an order chooses the releaser and has to trust it, exactly as with any escrow agent. It does not stop a recipient from being a contract that refuses money. A transfer can arrive late, because it is only executed when the settling transaction finalizes, about 30 minutes on Bradbury. And a refused payable call keeps its value on this network, so the app or script must check the rules before it sends.
