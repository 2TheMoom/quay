# Quay

A payout rail for GenLayer that pays each recipient exactly once. Someone funds an order and names a releaser. The releaser decides: pay the recipients, or pay the funder back. Anyone then settles, and every leg is paid once, in a transaction of its own. The releaser can be a person, a multisig, or another Intelligent Contract that does the judging (an arbiter, a verifier-backed escrow).

Live on **GenLayer Bradbury Testnet** (chain 4221). One contract, no owner, no admin, no setter, no upgrade. How it works, what it refuses to do and the threats considered are in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

| Contract | Address |
|---|---|
| Quay | [`0xEdB92e9b9E177503602655da2a30b3ba2BF8F52c`](https://explorer-bradbury.genlayer.com/address/0xEdB92e9b9E177503602655da2a30b3ba2BF8F52c) |

## Why it exists

Every GenLayer project that moves money (an escrow, a prediction market, a release vault) needs the same three guarantees, and each one cost us a bug the first time:

1. **A decision and its payment are never in the same transaction.** A leader rotation can leave a stray transfer message behind a nondeterministic block, so a payment must not share a transaction with one.
2. **A payout is recorded once, and nothing can make it happen twice.** Not a repeated call, not a different caller, not a later deadline.
3. **A payout that has not left is visible, and nobody else's money is touched to fix it.** Earlier projects shipped a retry for lost transfers and stewards rejected it twice: a retry pays from a pooled balance, so a retry of a transfer that was only slow pays twice, and the second payment comes out of someone else's order. Quay has no retry. It shows `in_flight` in `get_accounting` instead.

Quay is those three guarantees as a contract anyone can use, with the decision left to whoever the funder trusts.

## Using it

```
fund(order_id, releaser, recipients, amounts, deadline, note)    payable; the amounts must add up to the value sent
release(order_id)    releaser only, while open: records "pay the recipients"
refund(order_id)     releaser only, while open: records "pay the funder back"
reclaim(order_id)    funder only, after the deadline, while still open: records the same refund
settle(order_id)     anyone, once decided: pays every leg, once
get_order(order_id)  get_order_ids(offset, limit)  get_count()  get_accounting()
```

A call from a script, with genlayer-js:

```js
await client.writeContract({
  address: QUAY, functionName: "fund", value: 2_000_000_000_000_000n,
  args: ["job-17", RELEASER, [ALICE, BOB], [1_500_000_000_000_000n, 500_000_000_000_000n], BigInt(deadlineSeconds), "design work"],
});
// later, the releaser decides, then anyone settles
await releaser.writeContract({ address: QUAY, functionName: "release", args: ["job-17"] });
await anyone.writeContract({ address: QUAY, functionName: "settle", args: ["job-17"] });
```

Rules that are checked before anything changes: the order id is 1 to 64 characters from `a-z A-Z 0-9 . _ -` and unused, between 1 and 10 recipients, every amount a plain whole number above zero, the amounts add up to exactly the value sent, every address is real and neither a recipient nor the releaser is Quay itself, and the deadline is at least an hour ahead and within two years.

### Known limit you must design for

On Bradbury a payable call that the contract refuses **keeps the value it carried**, because a refused call cannot send anything back. So check the rules first (the amounts, the deadline, the id) before you send. `get_accounting` reports any such surplus as `in_flight`, and the live run below measures it.

A transfer is also executed only when the settling transaction finalizes, about 30 minutes on Bradbury. Until then it shows as `in_flight`.

## Security model

The threat list was written before the code and each row is tested. It is in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): double payment, wrong recipient, payouts that do not add up, the wrong caller deciding, the funder and the releaser racing, funds locked forever, a refused call keeping its value, one order draining another through the shared balance, hostile recipients, id squatting, type confusion and oversized orders.

What it **cannot** do: it cannot tell whether the releaser is right. Whoever funds an order chooses the releaser and has to trust it, as with any escrow agent.

## Tests

| Test type | Command | What it covers |
|---|---|---|
| Lint | `genvm-lint check contracts/quay.py` | static checks |
| Direct | `python -m pytest tests/direct/ -v` | 36 in-memory tests: every entry point, every refusal, every boundary, random sequences that must always settle to exactly what was funded |
| Live | `QUAY=0x... node scripts/e2e.mjs` | the Bradbury run described below, with separate payer, releaser and recipient wallets |
| Mutation | `python scripts/mutate.py` | breaks the contract 77 ways, one at a time, and checks a test notices |

Mutation result: 75 of 77 breakages are caught. Two survive and are explained. The check that the legs add up to the order total at decision time cannot be reached by any input (the legs are built from amounts that `fund` already checked), so it stays as a second line of defense. Dropping the check that a page size is at least one changes nothing, because a page of zero ids is empty either way.

<!--LIVE_START-->
<!--LIVE_END-->

## Layout

- `contracts/quay.py`
- `tests/direct/test_quay.py`
- `scripts/deploy.mjs` deploys with a testnet wallet kept outside the repo, `scripts/e2e.mjs` is the live run, `scripts/mutate.py` the mutation check
- `docs/ARCHITECTURE.md` the design and the threat table

## License

MIT
