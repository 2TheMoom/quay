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
## Live evidence (Bradbury, run `v1`, 2026-10-20)

Quay `0xEdB92e9b9E177503602655da2a30b3ba2BF8F52c`. Separate wallets for the payer, the releaser and the recipients. The recipients never send a transaction, so their balance changes are exact. Every claim below is read back from the chain by the script, which fails the run if it is not exactly as expected. Every settlement reached FINALIZED, about 30 minutes after it was sent, and the transfers landed then, because a transfer is only executed when the settling transaction finalizes.

### Release: an order split between two recipients

| # | Call | Transaction | Result |
|---|---|---|---|
| 1 | `fund (0.002 GEN split 0.0015 and 0.0005 between A and B)` | [`0x7262bfee0a`](https://explorer-bradbury.genlayer.com/tx/0x7262bfee0a7ba40512dc7c9672ef6697374730fe9831cfc21f4f08df6d7c513e) | accepted |
| 2 | `release` | [`0xace73fec41`](https://explorer-bradbury.genlayer.com/tx/0xace73fec4144c30e2015f5b8662f2af77354c23dfadb764c30f0a3d7117438b6) | refused by the contract: "Only the releaser can release" |
| 3 | `refund` | [`0x2c78468388`](https://explorer-bradbury.genlayer.com/tx/0x2c7846838873b901ea34caca8e9c44973b0e3645a15d49e585ef90494996c2b9) | refused by the contract: "Only the releaser can refund" |
| 4 | `settle` | [`0x5a716622e8`](https://explorer-bradbury.genlayer.com/tx/0x5a716622e82972169096b17745717a9aacb34c9a50c180357bcf8a008393baa5) | refused by the contract: "No outcome to settle (status: open)" |
| 5 | `release` | [`0x6794fe582c`](https://explorer-bradbury.genlayer.com/tx/0x6794fe582c2bb7bbd66ab1681c0069b341e65080ab6da52f39a8711f56eb0d6f) | accepted |
| 6 | `reclaim` | [`0x419c2bb057`](https://explorer-bradbury.genlayer.com/tx/0x419c2bb057a3b14bc6c64f0391352e47f86e1096e02131f9dbb5d6660368bbe9) | refused by the contract: "Only the payer can reclaim" |
| 7 | `settle` | [`0xce0514db1a`](https://explorer-bradbury.genlayer.com/tx/0xce0514db1a02c7a62ac7048b1adaf560e2ae24c4035b65e04a436feb1bb3f498) | accepted |
| 8 | `settle` | [`0x51f87fcd75`](https://explorer-bradbury.genlayer.com/tx/0x51f87fcd752aa81fe5678112e911e72499a1d9eaed7e50fc76d78e18499ff5c2) | refused by the contract: "No outcome to settle (status: settled)" |

The wrong caller was refused, the releaser's `release` recorded the decision and moved no money (both recipients' balances were unchanged until the settlement), and the second `settle` was refused. After the settlement finalized: recipient A 4.671683 to 4.673183 GEN (+0.001500) and recipient B 0.144572 to 0.145072 GEN (+0.000500), exactly.

### Refund: the releaser sends the money back

| # | Call | Transaction | Result |
|---|---|---|---|
| 1 | `fund` | [`0xad8fdf75ea`](https://explorer-bradbury.genlayer.com/tx/0xad8fdf75ea54009d1d6a0b17f76e189bc01088bdc3f39371e78bc531fc77ae62) | accepted |
| 2 | `refund` | [`0x43f5043cc7`](https://explorer-bradbury.genlayer.com/tx/0x43f5043cc71be7e9a44205f49a56bbbb5306baae0302890b8601c1ca38853761) | accepted |
| 3 | `settle` | [`0x648f7188d0`](https://explorer-bradbury.genlayer.com/tx/0x648f7188d0068758c344c598931e17960d8e33eb7131f5779d9481e25a56fcd3) | accepted |

The payer's balance went from 0.149973 to 0.151973 GEN (+0.002000), exactly, and the order's recipient was never paid.

### Deadline: the payer reclaims after it, and a late release is refused

| # | Call | Transaction | Result |
|---|---|---|---|
| 1 | `fund` | [`0x3a8d2d0664`](https://explorer-bradbury.genlayer.com/tx/0x3a8d2d06642a2deb1e633f889169e6d6d9c5940e5e19d79a77350ac5ff80bfe6) | accepted |
| 2 | `reclaim` | [`0x707f17192b`](https://explorer-bradbury.genlayer.com/tx/0x707f17192b4338ae9dedb4814fad31e029ddbdeee9649b001c9d1d6415c657e4) | refused by the contract: "The deadline has not passed yet" |
| 3 | `reclaim` | [`0x40c12df213`](https://explorer-bradbury.genlayer.com/tx/0x40c12df21359e5f1c5694b0d6440a8634a564d90b4d00e85b75f877e580468f0) | refused by the contract: "Only the payer can reclaim" |
| 4 | `reclaim` | [`0x8ca9b8ee46`](https://explorer-bradbury.genlayer.com/tx/0x8ca9b8ee46aa4d53f7f4ba33db77fd15813a1846a392adb58125845448ac9601) | refused by the contract: "The deadline has not passed yet" |
| 5 | `reclaim` | [`0x265185c9e6`](https://explorer-bradbury.genlayer.com/tx/0x265185c9e6f9bf5617a3fb1d9f9937c15bb672f04f5a8067ea4de293cafdbd2e) | refused by the contract: "Only the payer can reclaim" |
| 6 | `reclaim` | [`0x349424e50e`](https://explorer-bradbury.genlayer.com/tx/0x349424e50e9b673a63b0fc01331fc062342733b5e0acab942150402910a3fa59) | refused by the contract: "Only the payer can reclaim" |
| 7 | `reclaim` | [`0x8d90e0004e`](https://explorer-bradbury.genlayer.com/tx/0x8d90e0004e86f2aabdf20b42bd9016f2e0ddff2089b02f5cef846fec105d3cf8) | accepted |
| 8 | `release` | [`0x3eac0b5b8a`](https://explorer-bradbury.genlayer.com/tx/0x3eac0b5b8a4c0d9fe58468fd61cf2d509d0e8903315264ff9ef982d36b752c31) | refused by the contract: "Not open (status: decided)" |
| 9 | `settle` | [`0xec19668a2c`](https://explorer-bradbury.genlayer.com/tx/0xec19668a2c23f95ff57a3490edd9e6b225284949017450dadacc50681c97f4ee) | accepted |

Reclaim was refused before the deadline (the shortest allowed, one hour) and for a caller who is not the payer. After the deadline the payer's reclaim recorded the same refund decision, a late `release` by the releaser was then refused (`Not open`), and the settlement paid the payer back. The order is `dl-v1`: its one leg is the payer and its amount is the full 0.001 GEN. The payer's own balance is not a clean test here, because the payer also paid gas for its other orders, so this one is proven by the order's recorded leg and by the closed accounting below, where the contract's balance ends at the one wei a refused call left.

### A refused payable call keeps its value

| # | Call | Transaction | Result |
|---|---|---|---|
| 1 | `fund` | [`0x89947c33ea`](https://explorer-bradbury.genlayer.com/tx/0x89947c33ea1d18adaa88330793b2f054254ae1dca18067a658e52c0029ae3d5b) | refused by the contract (state unchanged) |

No order was created and nothing was counted as received. On Bradbury the wei the refused call carried stays in the contract, which is why the README says to check the rules before sending. It shows in the final accounting below as `in_flight`.

### Final accounting

After every run above, every order is settled and the contract's own accounting reads:

```
{
 "balance": 1,
 "in_flight": 1,
 "shortfall": 0,
 "total_received": 5000000000000000,
 "total_scheduled": 5000000000000000,
 "unscheduled": 0
}
```

`total_received` (5000000000000000) equals `total_scheduled` (5000000000000000): every wei that came in through an order was scheduled out exactly once, nothing is short, nothing is still owed, and the only balance is the 1 wei that the refused call left behind.

A note on honesty about the run itself: the first attempt stopped part way because the script read an order back right after an accepted `release` and saw the old state (a node that had not caught up with the accepted write). That was a script timing error, not a contract one, and the chain state it left was exactly as it should be. The script now polls every read-back and resumes from what is already on chain, and the run above is that resumed run; its event log, with every transaction hash from both attempts, is `scripts/e2e-log-v1.json`.

<!--LIVE_END-->

## Layout

- `contracts/quay.py`
- `tests/direct/test_quay.py`
- `scripts/deploy.mjs` deploys with a testnet wallet kept outside the repo, `scripts/e2e.mjs` is the live run, `scripts/mutate.py` the mutation check
- `docs/ARCHITECTURE.md` the design and the threat table

## License

MIT
