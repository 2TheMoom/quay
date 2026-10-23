# Quay

A payout rail for GenLayer that pays each recipient exactly once. Someone funds an order and names a releaser. The releaser decides: pay the recipients, or pay the funder back. Anyone then settles, and every leg is paid once, in a transaction of its own. The releaser can be a person, a multisig, or another Intelligent Contract that does the judging (an arbiter, a verifier-backed escrow).

**Version 2 adds an order the world can release.** An order can be funded with a watch: a public page and a condition on it (the file's SHA-256, or one JSON field reading a given value). Anyone can then call `observe`. The validators each fetch the page on their own and must agree on whether the condition holds, and only then is the order decided as a release. Payment is still a separate `settle`, and the named releaser and the deadline refund still work. See [Watched orders](#watched-orders-version-2).

One contract, no owner, no admin, no setter, no upgrade. How it works, what it refuses to do and the threats considered are in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

| Contract | Network | Address |
|---|---|---|
| Quay v2 (the contract in this repo) | GenLayer Studionet (chain 61999) | [`0x1678e8C242AB557ACF772e17aE715EC80Eac1d39`](https://explorer-studio.genlayer.com/address/0x1678e8C242AB557ACF772e17aE715EC80Eac1d39) |
| Quay v1 (no watch; its source is commit `af82e9e`) | GenLayer Bradbury Testnet (chain 4221) | [`0xEdB92e9b9E177503602655da2a30b3ba2BF8F52c`](https://explorer-bradbury.genlayer.com/address/0xEdB92e9b9E177503602655da2a30b3ba2BF8F52c) |

The v2 contract is the v1 contract plus the watch (`fund_watched`, `observe`); nothing about `fund`, `release`, `refund`, `reclaim` or `settle` changed. The v1 live evidence further down is kept as it was.

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
fund_watched(order_id, releaser, recipients, amounts, deadline, note, watch_url, watch_kind, watch_path, watch_value)
                     payable; fund plus a watch fixed now (version 2)
observe(order_id)    anyone, while open and before the deadline: the validators read the watched page and agree; if the condition holds the order is decided as a release
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

## Watched orders (version 2)

```js
// pay A and B when the jq 1.7.1 tarball matches its published digest
await payer.writeContract({
  address: QUAY, functionName: "fund_watched", value: 2_000_000_000_000_000n,
  args: ["jq-bounty", RELEASER, [ALICE, BOB], [1_500_000_000_000_000n, 500_000_000_000_000n], BigInt(deadlineSeconds), "pay on a verified release",
         "https://github.com/jqlang/jq/releases/download/jq-1.7.1/jq-1.7.1.tar.gz", "sha256", "", "478c9ca129fd2e3443fe27314b455e211e0d8c60bc8ff7df703873deeee580c2"],
});
// anyone, later
await anyone.writeContract({ address: QUAY, functionName: "observe", args: ["jq-bounty"] });   // the validators read the page and agree
await anyone.writeContract({ address: QUAY, functionName: "settle", args: ["jq-bounty"] });    // now pay, in its own transaction
```

- `watch_kind` is `sha256` (the body's SHA-256 must equal `watch_value`, 64 lowercase hex digits, `watch_path` empty) or `json_eq` (the field at `watch_path`, dot-separated names or list positions, must read exactly as `watch_value`: a string as it is, `true`, `false`, `null`, a number as JSON writes it).
- The url must be https, at most 300 characters, a public domain name, with no credentials or port. The watch is fixed at funding and shown by `get_order`, so a recipient can read it before relying on the order.
- **The validator vote.** The leader fetches the page and proposes whether the condition holds; every other validator fetches it again on its own and accepts only the same answer. A leader that says "met" for a page that does not satisfy the condition is voted down, and a page nobody can read records nothing. This is the one nondeterministic step in the contract, and it can only record a release that was already spelled out at `fund`. It never pays: `settle` is the only function that sends money.
- `observe` works while the order is open, up to and including the deadline, at most once every 5 minutes per order. After the deadline only the payer's `reclaim` works, so the two never race.
- What it cannot do: tell whether the page is true. It proves what independent validators read at an address and that they agreed.

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
| Direct | `python -m pytest tests/direct/ -v` | 152 in-memory tests: every entry point, every refusal, every boundary, random sequences that must always settle to exactly what was funded, and (`test_quay_observe.py`) the watch terms, the page matching rules, the rate limit, the deadline window and the validator vote with forged and malformed leader results |
| Live, v1 | `QUAY=0x... node scripts/e2e.mjs` | the Bradbury run described below, with separate payer, releaser and recipient wallets |
| Live, v2 | `NETWORK=studionet QUAY=0x... node scripts/e2e-observe.mjs` | the Studionet run of the watched orders described below, against real pages |
| Mutation | `python scripts/mutate.py` | breaks the contract 157 ways, one at a time, and checks a test notices |

Mutation result: 155 of 157 breakages are caught. Two survive and are explained. The check that the legs add up to the order total at decision time cannot be reached by any input (the legs are built from amounts that `fund` already checked), so it stays as a second line of defense. Dropping the check that a page size is at least one changes nothing, because a page of zero ids is empty either way. (The 157 include 81 on the watch: its terms, the url rules, the page matching, the validator vote and `observe`. A first pass left several url and page-matching mutants alive; each was closed with a test that refuses it for its own reason.)

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

<!--OBSERVE_START-->
## Live evidence, version 2 (Studionet, run `studio2`, 2026-10-23)

Quay v2 `0x1678e8C242AB557ACF772e17aE715EC80Eac1d39` on Studionet. Separate wallets for the payer, the named releaser and the recipients; the recipients never send a transaction, so their balance changes are exact. Every claim is read back from the chain by `scripts/e2e-observe.mjs`, which fails the run if it is not exactly as expected, and the pages are real: the jq 1.7.1 source tarball on GitHub, and a JSON page in this repository (`docs/watch-demo.json`) that the run changes mid-order. Studionet finalizes in under a minute, so every transfer landed within a minute of its `settle`.

### Digest: the validators read a real file and release the order

| # | Call | Transaction | Result |
|---|---|---|---|
| 1 | `fund_watched` | [`0x8931f7ea17`](https://explorer-studio.genlayer.com/tx/0x8931f7ea1712e38ce46944b209fe75dfa161aaddcb6a0b3c649d71eb6a7cd0ea) | accepted |
| 2 | `observe (anyone)` | [`0x143c63a3a2`](https://explorer-studio.genlayer.com/tx/0x143c63a3a2f03498f3d353b32d0806db4c624acd6e7805436330270fd2b6b85a) | accepted |
| 3 | `observe again` | [`0xcdbd9bfce8`](https://explorer-studio.genlayer.com/tx/0xcdbd9bfce877f44e12efd3fb4ae51e8359f471e88ecbac766aeafddd18a77ec3) | refused by the contract: "Not open (status: decided)" |
| 4 | `settle` | [`0x6fdfaed3f0`](https://explorer-studio.genlayer.com/tx/0x6fdfaed3f079c86e61f8d78e348ab8e7d0b10fa6e8f8b84dd0eb51d2715ebb8e) | accepted |
| 5 | `settle again` | [`0xc5d6169cab`](https://explorer-studio.genlayer.com/tx/0xc5d6169cab7f9f14d429b2f5dc8e372ddd807e7cb907cd2763b5c5a57bfb6872) | refused by the contract: "No outcome to settle (status: settled)" |

The order watched the real jq 1.7.1 tarball for its published SHA-256 (`478c9ca1...ee580c2`). `observe` was called by the payer, who is neither the releaser nor a recipient: the validators fetched the file, agreed the digest matched, and the order was decided as a release (`observed`) after 1 observation. Both recipients' balances were unchanged at the decision, a second `observe` was refused (`Not open`), `settle` then paid recipient A 0.001500 and recipient B 0.000500 GEN exactly, and a second `settle` was refused.

### Unmet: the watch is not satisfied, the order stays open, and the releaser can still refund

| # | Call | Transaction | Result |
|---|---|---|---|
| 1 | `fund_watched` | [`0xf6a09097bc`](https://explorer-studio.genlayer.com/tx/0xf6a09097bc7d71c05266a9e66df577933352094db80c6cdcbd9660ff8d4d19de) | accepted |
| 2 | `observe` | [`0x32fe07a6a6`](https://explorer-studio.genlayer.com/tx/0x32fe07a6a69cf738db57e823d449364492f1a5a940c8ebbe184c6f7778d40c9b) | accepted |
| 3 | `observe again at once` | [`0xd42ae1f500`](https://explorer-studio.genlayer.com/tx/0xd42ae1f500243364b9deff0e4e001e16fe536e8bebef2925d27e133b2459e4c7) | refused by the contract: "Observed too recently, try again later" |
| 4 | `refund (the named releaser)` | [`0xf654f72019`](https://explorer-studio.genlayer.com/tx/0xf654f7201975e3e2b636e76459743067bd217296a66ccacac54a6d0da46a3b2a) | accepted |
| 5 | `settle` | [`0x306fa68974`](https://explorer-studio.genlayer.com/tx/0x306fa689745854c019605503ff4c12be95a26ee74e90227c824c89b21dda32b0) | accepted |

The order watched the same file for a digest that is not its digest. The validators agreed the condition is not met, so the order stayed open with 1 observation recorded. A second `observe` straight away was refused as too recent. The named releaser then refunded and `settle` paid the payer back 0.002000 GEN, exactly.

### Flip: an order released when a page changes

| # | Call | Transaction | Result |
|---|---|---|---|
| 1 | `fund_watched` | [`0x410d5bba21`](https://explorer-studio.genlayer.com/tx/0x410d5bba21e34d6349fda5485eb443aaf3ffd8afa21f252335777c25b4c73e57) | accepted |
| 2 | `observe while the page says pending` | [`0x1f94bf7920`](https://explorer-studio.genlayer.com/tx/0x1f94bf792013ee111c721fd3561097dcc7fe97254571b113c4715c52891aa23a) | accepted |
| 3 | `observe #2 after the change` | [`0xdbb984f097`](https://explorer-studio.genlayer.com/tx/0xdbb984f097b373f8ff3641ee0264ce2efc621d5bae4c19695feaa49da59207cd) | accepted |
| 4 | `settle` | [`0x3cc2b24b69`](https://explorer-studio.genlayer.com/tx/0x3cc2b24b6965e4f0cb051e013445836b8581a693549d8797208de104d2386577) | accepted |

The order watched the `status` field of `docs/watch-demo.json` for `delivered`. While the page said `pending`, `observe` left the order open. The page was then changed to `delivered` (a commit to this repository, served by raw.githubusercontent.com), and after the cooldown the next `observe` had the validators agree on the new value and decide the order as a release (`observed`, 2 observations in all). `settle` paid recipients A and B exactly.

### Unreadable: a page that does not exist records nothing

| # | Call | Transaction | Result |
|---|---|---|---|
| 1 | `fund_watched` | [`0xd5babc713d`](https://explorer-studio.genlayer.com/tx/0xd5babc713d8edfb2522f3fade799eddd4eab9ccd0bd765f2c1d905e844e0b1d9) | accepted |
| 2 | `observe` | [`0x5473f44aec`](https://explorer-studio.genlayer.com/tx/0x5473f44aec9a4ae9fcc53305e160864ae053ec201d6a72d31dcdfbd3df30beaa) | refused by the contract: "Page unreadable, nothing recorded. Try again shortly." |
| 3 | `refund (so nothing stays locked)` | [`0x223df96ec7`](https://explorer-studio.genlayer.com/tx/0x223df96ec7f714770a119392eec89cf718b58a62ac7a6d4574cf45a53a376116) | accepted |
| 4 | `settle` | [`0xfecd18e8ef`](https://explorer-studio.genlayer.com/tx/0xfecd18e8ef2ac1f17c64a63e780e1159c78675db0ad3caf50d5101c02a17bb43) | accepted |

`observe` on a page that returns 404 was refused (`Page unreadable, nothing recorded`) and left the order as it was: no observation counted, nothing decided, no cooldown started. The releaser then refunded so nothing stays locked.

### Deadline: after it, observe is refused even though the page matches, and the payer reclaims

| # | Call | Transaction | Result |
|---|---|---|---|
| 1 | `fund_watched` | [`0xf7b66edb31`](https://explorer-studio.genlayer.com/tx/0xf7b66edb315f9b99770e4a028cf4a0af9938c79f12a4522c392a21c030fb688f) | accepted |
| 2 | `observe after the deadline` | [`0x0161f0e7c3`](https://explorer-studio.genlayer.com/tx/0x0161f0e7c3ddd42329ddf073050ecd2a2cecab042184ab94994b51379f3f0640) | refused by the contract: "The deadline has passed" |
| 3 | `reclaim (the payer)` | [`0xe94f2aee90`](https://explorer-studio.genlayer.com/tx/0xe94f2aee90cfb201c9377b521a45f508eaad821711ceb57889ea18dfed2af0c3) | accepted |
| 4 | `settle` | [`0xb265a683c8`](https://explorer-studio.genlayer.com/tx/0xb265a683c8915ac3f4c833c7e5bc29ef0086ab27fb733ae3f26928a7b4d47a9a) | accepted |

The order watched a page that matched, but nobody observed before the deadline (the shortest allowed, one hour). After it, `observe` was refused (`The deadline has passed`), the payer's `reclaim` recorded the refund (`deadline`) and `settle` paid it back. `observe` and `reclaim` never overlap.

### Final accounting

```
{
 "balance": 0,
 "in_flight": 0,
 "shortfall": 0,
 "total_received": "9400000000000000",
 "total_scheduled": "9400000000000000",
 "unscheduled": 0
}
```

`total_received` (9400000000000000) against `total_scheduled` (9400000000000000); the contract's own `get_accounting` after the run, with nothing short (`shortfall` 0) and nothing in flight (`in_flight` 0). The full event log, with every transaction hash, is `scripts/e2e-observe-log-studio2.json`.

### About the runs

This is the second full run. The first (`studio1`, log `scripts/e2e-observe-log-studio1.json`, kept) passed the digest, unmet, unreadable and deadline scenarios, and its flip scenario also decided, settled and paid correctly, but the script then reported a failure: it compared the recipients' balances against a starting figure taken before the digest scenario, which runs side by side and pays the same two wallets, so the figure was wrong. That was a mistake in the script's check, not in the contract. The digest and flip orders now pay fresh addresses that nothing else uses, and the script waits for every scheduled transfer to leave the contract before it reads the accounting. The accounting above covers both runs: every wei received by any order in either run was scheduled out exactly once.
<!--OBSERVE_END-->

## Layout

- `contracts/quay.py`
- `tests/direct/test_quay.py`, `tests/direct/test_quay_observe.py`
- `scripts/deploy.mjs` deploys with a testnet wallet kept outside the repo (`NETWORK=studionet` for Studionet), `scripts/e2e.mjs` is the v1 live run, `scripts/e2e-observe.mjs` the v2 live run, `scripts/mutate.py` the mutation check
- `docs/watch-demo.json` is a page this repo controls, used by the live run to show an order released when a page changes
- `docs/ARCHITECTURE.md` the design and the threat table

## License

MIT
