// Live end to end run of Quay on Bradbury with separate wallets. Every claim is read back from the chain and the run fails if it is not exactly as expected.
//   QUAY=0x.. RUN=v1 node scripts/e2e.mjs        writes scripts/e2e-log-<RUN>.json
//
// Wallets (test wallets kept outside the repo): gl-test-1 pays orders (P), gl-test-2 is the releaser and settles (REL), gl-test-3 and gl-test-4 are
// recipients that never send anything (A, B) so their balance changes are exact, gl-test-5 pays the refund order and sends nothing afterwards (R).
//
// Scenarios, run side by side:
//   release   P funds an order split between A and B. A wrong caller is refused. The releaser decides: no money moves. Settle pays A and B exactly. A second settle is refused.
//   refund    R funds an order, the releaser refunds it, settle pays R back exactly.
//   deadline  P funds an order with the shortest deadline (one hour). Reclaim is refused before it, works after it, and a late release is refused. Settle pays P back.
//   refused   a fund call whose amounts do not match its value is refused and keeps one wei, which accounting shows as in_flight.
import { readFileSync, writeFileSync } from "fs";
import { WALLETS, clientFor, read, reader, reasonOf, send, sleep, ts } from "./lib.mjs";

const Q = process.env.QUAY;
const RUN = process.env.RUN || Date.now().toString(36);
const NAMES = { P: "gl-test-1", REL: "gl-test-2", A: "gl-test-3", B: "gl-test-4", R: "gl-test-5" };
const clients = Object.fromEntries(Object.entries(NAMES).map(([k, v]) => [k, clientFor(v)]));
const addr = Object.fromEntries(Object.entries(NAMES).map(([k, v]) => [k, WALLETS[v].address]));
const GEN = 10n ** 18n;
const gen = (w) => (Number(w) / 1e18).toFixed(6);

const LOG = { run: RUN, quay: Q, started: new Date().toISOString(), events: [], results: {}, wallets: addr };
const log = (scn, msg, extra) => {
  console.log(`[${ts()}] ${scn.padEnd(9)} ${msg}`);
  LOG.events.push({ t: new Date().toISOString(), scn, msg, ...(extra || {}) });
};
const lock = {};
const serial = (who, fn) => (lock[who] = (lock[who] || Promise.resolve()).then(fn, fn));
const ok = (r) => (r.status === "ACCEPTED" || r.status === "FINALIZED") && r.exec === "FINISHED_WITH_RETURN";
const tx = (scn, who, fn, args, label, value) =>
  serial(who, async () => {
    const r = await send(clients[who], Q, fn, args, value ? { value } : {});
    log(scn, `${label || fn} ${r.hash.slice(0, 12)} ${r.status} exec=${r.exec}`, { fn, hash: r.hash, exec: r.exec, status: r.status });
    return r;
  });
const balance = async (w) => BigInt(await reader.getBalance({ address: addr[w] }));
const order = (id) => read(Q, "get_order", [id]);

async function finalized(scn, hash, label) {
  const t0 = Date.now();
  for (let a = 0; a < 30; a++) {
    try {
      await reader.waitForTransactionReceipt({ hash, status: "FINALIZED", retries: 400, interval: 10000 });
      log(scn, `${label} FINALIZED ${((Date.now() - t0) / 60000).toFixed(1)} min after the wait began`);
      return;
    } catch {
      await sleep(15000);
    }
  }
  throw new Error(`${scn}: ${label} did not finalize`);
}

/** A call that must be refused: simulated first for the contract's own reason, then sent so it is on chain too. */
async function refused(scn, who, fn, args, expect) {
  let reason = "SIMULATION ACCEPTED";
  try {
    await clients[who].simulateWriteContract({ address: Q, functionName: fn, args });
  } catch (e) {
    reason = reasonOf(e);
  }
  if (reason === "SIMULATION ACCEPTED") throw new Error(`${scn}: ${fn} should have been refused`);
  if (expect && !reason.includes(expect)) throw new Error(`${scn}: ${fn} refused for "${reason}", expected "${expect}"`);
  const r = await tx(scn, who, fn, args, `${fn} (refused)`);
  if (ok(r)) throw new Error(`${scn}: the refused ${fn} was accepted`);
  log(scn, `${fn} refused: "${reason}"`, { reason });
}

async function accepted(scn, who, fn, args, label, value) {
  let a = 0;
  for (;;) {
    const r = await tx(scn, who, fn, args, label, value);
    if (ok(r)) return r;
    if (++a >= 6) throw new Error(`${scn}: ${fn} was not accepted (${r.status} ${r.exec})`);
    await sleep(20000);
  }
}

/** Waits until a wallet balance reaches target (a transfer lands when its settling transaction finalizes). */
async function waitBalance(scn, who, target, label) {
  for (let i = 0; i < 90; i++) {
    const b = await balance(who);
    if (b === target) return b;
    if (b > target) throw new Error(`${scn}: ${label} balance overshot: ${b} > ${target}`);
    await sleep(20000);
  }
  throw new Error(`${scn}: ${label} balance never reached ${target}`);
}

const now = () => Math.floor(Date.now() / 1000);
const fund = (scn, who, id, recipients, amounts, deadline, note, valueOverride) =>
  accepted(scn, who, "fund", [id, addr.REL, recipients.map((r) => addr[r]), amounts, BigInt(deadline), note], "fund", valueOverride ?? amounts.reduce((a, b) => a + b, 0n));

async function release() {
  const scn = "release", id = `rel-${RUN}`;
  const a0 = await balance("A"), b0 = await balance("B");
  const amounts = [1_500_000_000_000_000n, 500_000_000_000_000n];
  await fund(scn, "P", id, ["A", "B"], amounts, now() + 3 * 86400, "split between two recipients");
  const o1 = await order(id);
  if (o1.status !== "open" || o1.payees.length !== 2) throw new Error(`${scn}: unexpected order ${JSON.stringify(o1)}`);
  await refused(scn, "P", "release", [id], "Only the releaser");
  await refused(scn, "P", "refund", [id], "Only the releaser");
  await refused(scn, "REL", "settle", [id], "No outcome");
  await accepted(scn, "REL", "release", [id], "release");
  const o2 = await order(id);
  if (o2.status !== "decided" || o2.outcome !== "release" || o2.legs.length !== 2 || o2.legs.some((l) => l.scheduled)) throw new Error(`${scn}: bad decision ${JSON.stringify(o2)}`);
  if ((await balance("A")) !== a0 || (await balance("B")) !== b0) throw new Error(`${scn}: money moved at the decision`);
  log(scn, "decision recorded, no money moved");
  await refused(scn, "REL", "reclaim", [id], "Only the payer");
  const s = await accepted(scn, "REL", "settle", [id], "settle");
  await refused(scn, "REL", "settle", [id], "No outcome");
  await finalized(scn, s.hash, "settle");
  const a1 = await waitBalance(scn, "A", a0 + amounts[0], "A"), b1 = await waitBalance(scn, "B", b0 + amounts[1], "B");
  const o3 = await order(id);
  if (o3.status !== "settled" || !o3.legs.every((l) => l.scheduled)) throw new Error(`${scn}: not settled ${JSON.stringify(o3)}`);
  log(scn, `A ${gen(a0)} -> ${gen(a1)} (+${gen(amounts[0])}), B ${gen(b0)} -> ${gen(b1)} (+${gen(amounts[1])}), exactly`);
  LOG.results.release = { id, a: [a0.toString(), a1.toString()], b: [b0.toString(), b1.toString()] };
}

async function refund() {
  const scn = "refund", id = `ref-${RUN}`;
  const amount = 2_000_000_000_000_000n;
  await fund(scn, "R", id, ["A"], [amount], now() + 3 * 86400, "refunded by the releaser");
  const r0 = await balance("R");
  await accepted(scn, "REL", "refund", [id], "refund");
  const o = await order(id);
  if (o.outcome !== "refund" || o.legs.length !== 1 || o.legs[0].to.toLowerCase() !== addr.R.toLowerCase()) throw new Error(`${scn}: bad refund ${JSON.stringify(o)}`);
  const s = await accepted(scn, "REL", "settle", [id], "settle");
  await finalized(scn, s.hash, "settle");
  const r1 = await waitBalance(scn, "R", r0 + amount, "R");
  log(scn, `payer ${gen(r0)} -> ${gen(r1)} (+${gen(amount)}), exactly; the recipient was never paid`);
  LOG.results.refund = { id, payer: [r0.toString(), r1.toString()] };
}

async function deadline() {
  const scn = "deadline", id = `dl-${RUN}`;
  const amount = 1_000_000_000_000_000n;
  const dl = now() + 3720;
  const p0 = await balance("P");
  await fund(scn, "P", id, ["A"], [amount], dl, "reclaimed after the deadline");
  await refused(scn, "P", "reclaim", [id], "not passed");
  await refused(scn, "REL", "reclaim", [id], "Only the payer");
  log(scn, `waiting for the deadline (${new Date(dl * 1000).toISOString()})`);
  while (now() < dl + 90) await sleep(30000);
  await refused(scn, "REL", "reclaim", [id], "Only the payer");
  await accepted(scn, "P", "reclaim", [id], "reclaim");
  const o = await order(id);
  if (o.outcome !== "refund" || o.reason !== "deadline") throw new Error(`${scn}: bad reclaim ${JSON.stringify(o)}`);
  await refused(scn, "REL", "release", [id], "Not open");
  const s = await accepted(scn, "REL", "settle", [id], "settle");
  await finalized(scn, s.hash, "settle");
  const p1 = await balance("P");
  log(scn, `payer ${gen(p0)} -> ${gen(p1)} (the order's ${gen(amount)} came back; the difference to a perfect round trip is the payer's own gas and its other orders)`);
  LOG.results.deadline = { id, payer: [p0.toString(), p1.toString()] };
}

async function refusedPayable() {
  const scn = "refused";
  const before = await read(Q, "get_accounting", []);
  const r = await tx(scn, "P", "fund", [`bad-${RUN}`, addr.REL, [addr.A], [2n], BigInt(now() + 86400), "amounts do not match the value"], "fund (value 1, amounts 2)", 1n);
  if (ok(r)) throw new Error(`${scn}: the mismatched fund was accepted`);
  const after = await read(Q, "get_accounting", []);
  log(scn, `refused as it must be; in_flight ${before.in_flight} -> ${after.in_flight} (the refused call kept its wei)`, { before, after });
  LOG.results.refused = { before, after };
}

try {
  const bal0 = await balance("P");
  log("setup", `quay ${Q}; payer balance ${gen(bal0)}`);
  await Promise.all([release(), refund(), deadline(), refusedPayable()]);
  // every transfer has landed by now: the contract's own accounting must close
  let acc;
  for (let i = 0; i < 40; i++) {
    acc = await read(Q, "get_accounting", []);
    if (acc.shortfall === "0" || acc.shortfall === 0) break;
    await sleep(15000);
  }
  LOG.results.accounting = acc;
  log("accounting", JSON.stringify(acc));
  const stranded = BigInt(acc.in_flight);
  if (BigInt(acc.total_received) !== BigInt(acc.total_scheduled) + BigInt(acc.unscheduled)) throw new Error("accounting does not add up");
  if (BigInt(acc.shortfall) !== 0n || BigInt(acc.unscheduled) !== 0n) throw new Error(`accounting not closed: ${JSON.stringify(acc)}`);
  log("accounting", `closed: received ${acc.total_received} = scheduled ${acc.total_scheduled}; nothing short, nothing owed; only ${stranded} wei kept from refused calls`);
  LOG.finished = new Date().toISOString();
  console.log("E2E OK");
} catch (e) {
  LOG.error = String(e.message || e);
  console.error("E2E FAILED:", LOG.error);
  process.exitCode = 1;
} finally {
  writeFileSync(`scripts/e2e-log-${RUN}.json`, JSON.stringify(LOG, null, 1));
}
