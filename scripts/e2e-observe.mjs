// Live end to end run of Quay's watched orders (fund_watched, observe) with separate wallets and real pages. Every claim is read back from the
// chain and the run fails if it is not exactly as expected.
//   NETWORK=studionet QUAY=0x.. RUN=studio1 node scripts/e2e-observe.mjs        writes scripts/e2e-observe-log-<RUN>.json
//   ONLY=digest,unmet ...   runs just those        FLIP=1 waits for docs/watch-demo.json on main to say "delivered" (see below)
//
// Wallets (test wallets kept outside the repo): gl-test-1 pays orders and observes (P), gl-test-2 is the named releaser (REL), gl-test-3 and gl-test-4 are
// recipients that never send anything (A, B) so their balance changes are exact, gl-test-5 pays the refund order and sends nothing afterwards (R).
//
// Scenarios, run side by side:
//   digest      P funds an order watching the real jq 1.7.1 source tarball for its published SHA-256. Anyone (P, who is neither releaser nor recipient)
//               calls observe: the validators fetch it, agree, and the order is decided as a release. No money moves at the decision. Settle pays A and B
//               exactly. A second observe and a second settle are refused.
//   unmet       R funds an order watching the same file for a digest that is not its digest. observe records the look and leaves the order open; a second
//               observe is refused as too recent; the named releaser still refunds, and settle pays R back exactly.
//   flip        P funds an order watching a JSON field of docs/watch-demo.json in this repo's main branch for "delivered". While it says "pending" observe leaves the
//               order open. After the file is changed to "delivered" and the cooldown has passed, observe releases it and settle pays A and B exactly.
//   unreadable  an order watching a page that does not exist: observe is refused and records nothing.
//   deadline    P funds an order with the shortest deadline (one hour) watching the real file. Nobody observes. After the deadline observe is refused even though
//               the page matches, the payer reclaims, and settle pays the payer back.
import { randomBytes } from "crypto";
import { existsSync, readFileSync, writeFileSync } from "fs";
import { WALLETS, clientFor, plain, read, reader, refusalOf, send, sleep, ts } from "./lib.mjs";
import { NETWORK, chain } from "./network.mjs";

const Q = process.env.QUAY;
const RUN = process.env.RUN || Date.now().toString(36);
if (!Q) throw new Error("set QUAY=<address>");
const NAMES = { P: "gl-test-1", REL: "gl-test-2", A: "gl-test-3", B: "gl-test-4", R: "gl-test-5" };
const clients = Object.fromEntries(Object.entries(NAMES).map(([k, v]) => [k, clientFor(v)]));
const addr = Object.fromEntries(Object.entries(NAMES).map(([k, v]) => [k, WALLETS[v].address]));
const gen = (w) => (Number(w) / 1e18).toFixed(6);

const JQ = "https://github.com/jqlang/jq/releases/download/jq-1.7.1/jq-1.7.1.tar.gz";
const JQ_SHA = "478c9ca129fd2e3443fe27314b455e211e0d8c60bc8ff7df703873deeee580c2";
const NOT_JQ_SHA = "0".repeat(63) + "1";
const MISSING = "https://github.com/jqlang/jq/releases/download/jq-1.7.1/no-such-file-7f3a9.tar.gz";
const DEMO = "https://raw.githubusercontent.com/2TheMoom/quay/main/docs/watch-demo.json";
const RECHECK = 300;

const LOGFILE = `scripts/e2e-observe-log-${RUN}.json`;
const PREV = process.env.RESUME && existsSync(LOGFILE) ? JSON.parse(readFileSync(LOGFILE, "utf8")) : null;
const LOG = { run: RUN, network: NETWORK, quay: Q, started: PREV?.started ?? new Date().toISOString(), events: PREV?.events ?? [], results: {}, wallets: addr };
const log = (scn, msg, extra) => {
  console.log(`[${ts()}] ${scn.padEnd(10)} ${msg}`);
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
/** A write the contract must refuse; the reason comes from the refused transaction's receipt. */
const refused = (scn, who, fn, args, text, label) =>
  serial(who, async () => {
    const r = await send(clients[who], Q, fn, args);
    const reason = refusalOf(r.tx) ?? `${r.status} ${r.exec}`;
    log(scn, `${label || fn} ${r.hash.slice(0, 12)} refused: "${reason}"`, { fn, hash: r.hash, exec: r.exec, status: r.status, refused: true, reason });
    if (ok(r)) throw new Error(`${scn}: ${fn} should have been refused`);
    if (text && !reason.includes(text)) throw new Error(`${scn}: ${fn} refused for "${reason}", expected "${text}"`);
    return r;
  });
const balance = async (w) => BigInt(await reader.getBalance({ address: addr[w] ?? w }));
/** A recipient address nobody else ever uses, so its balance moves only because of this scenario (scenarios run side by side). */
const fresh = () => "0x" + randomBytes(20).toString("hex");
const order = (id) => read(Q, "get_order", [id]);
const accounting = () => read(Q, "get_accounting");
const now = () => Math.floor(Date.now() / 1000);

/** Polls a read until it holds (a write can be accepted before it reads back). */
async function until(scn, label, fn, ms = 600000) {
  const t0 = Date.now();
  for (;;) {
    const v = await fn().catch(() => null);
    if (v) return v;
    if (Date.now() - t0 > ms) throw new Error(`${scn}: ${label} did not come true`);
    await sleep(5000);
  }
}
async function waitBalance(scn, who, target, label) {
  await until(scn, `${label} balance to reach ${target}`, async () => {
    const b = await balance(who);
    if (b > target) throw new Error(`${scn}: ${label} balance overshot: ${b} > ${target}`);
    return b === target;
  }, 900000);
  log(scn, `${label} received exactly ${gen(target)} GEN in total (balance now ${gen(target)})`);
}

const fund = (scn, id, releaser, payer, recipients, amounts, deadline, url, kind, path, value, note) =>
  tx(scn, payer, "fund_watched", [id, addr[releaser], recipients.map((r) => addr[r] ?? r), amounts, deadline, note, url, kind, path, value], "fund_watched", amounts.reduce((a, b) => a + b, 0n));
const id = (k) => `${k}-${RUN}`;

// ------------------------------------------------------------------------------------------------------------------ scenarios

async function digest() {
  const scn = "digest", oid = id("dg");
  const A = fresh(), B = fresh();
  const a0 = await balance(A), b0 = await balance(B);
  const r = await fund(scn, oid, "REL", "P", [A, B], [1500000000000000n, 500000000000000n], now() + 7200, JQ, "sha256", "", JQ_SHA, "pay when the jq 1.7.1 tarball matches its published digest");
  if (!ok(r)) throw new Error(`${scn}: fund_watched failed`);
  const o = await until(scn, "the order to read back", async () => (await order(oid)).status === "open" && (await order(oid)));
  if (o.watch.url !== JQ || o.watch.kind !== "sha256" || o.watch.value !== JQ_SHA) throw new Error(`${scn}: the watch was not stored as funded`);
  log(scn, `watch stored: ${o.watch.kind} of ${o.watch.url}`);
  const ob = await tx(scn, "P", "observe", [oid], "observe (anyone)");
  if (!ok(ob)) throw new Error(`${scn}: observe was not accepted`);
  const d = await until(scn, "the order to be decided", async () => (await order(oid)).status === "decided" && (await order(oid)));
  if (d.outcome !== "release" || d.reason !== "observed" || d.watch.observations !== 1) throw new Error(`${scn}: wrong decision ${JSON.stringify(d)}`);
  log(scn, `decided: ${d.outcome} (${d.reason}) after ${d.watch.observations} observation; legs ${d.legs.map((l) => gen(l.amount)).join(" + ")} GEN`);
  if ((await balance(A)) !== a0 || (await balance(B)) !== b0) throw new Error(`${scn}: money moved at the decision`);
  log(scn, "no money moved at the decision");
  await refused(scn, "P", "observe", [oid], "Not open", "observe again");
  const s = await tx(scn, "P", "settle", [oid], "settle");
  if (!ok(s)) throw new Error(`${scn}: settle was not accepted`);
  await waitBalance(scn, A, a0 + 1500000000000000n, "recipient A");
  await waitBalance(scn, B, b0 + 500000000000000n, "recipient B");
  await refused(scn, "P", "settle", [oid], "No outcome to settle", "settle again");
  LOG.results.digest = { id: oid, outcome: d.outcome, reason: d.reason, observations: d.watch.observations, a_received: String(1500000000000000n), b_received: String(500000000000000n) };
}

async function unmet() {
  const scn = "unmet", oid = id("un");
  const r0 = await balance("R");
  const r = await fund(scn, oid, "REL", "R", ["A"], [2000000000000000n], now() + 7200, JQ, "sha256", "", NOT_JQ_SHA, "a watch that is never met");
  if (!ok(r)) throw new Error(`${scn}: fund_watched failed`);
  await until(scn, "the order to read back", async () => (await order(oid)).status === "open");
  const after = await balance("R");
  const ob = await tx(scn, "P", "observe", [oid], "observe");
  if (!ok(ob)) throw new Error(`${scn}: observe was not accepted`);
  const o = await until(scn, "the observation to be recorded", async () => (await order(oid)).watch.observations === 1 && (await order(oid)));
  if (o.status !== "open") throw new Error(`${scn}: an unmet watch must leave the order open`);
  log(scn, "the validators read the page and agreed the condition is not met: the order stays open, observations 1");
  await refused(scn, "P", "observe", [oid], "too recently", "observe again at once");
  const rf = await tx(scn, "REL", "refund", [oid], "refund (the named releaser)");
  if (!ok(rf)) throw new Error(`${scn}: refund was not accepted`);
  await until(scn, "the refund to be decided", async () => (await order(oid)).status === "decided");
  const s = await tx(scn, "P", "settle", [oid], "settle");
  if (!ok(s)) throw new Error(`${scn}: settle was not accepted`);
  await waitBalance(scn, "R", after + 2000000000000000n, "R (the payer)");
  LOG.results.unmet = { id: oid, observations: 1, final: "refunded by the named releaser", payer_refunded: String(2000000000000000n) };
}

async function flip() {
  const scn = "flip", oid = id("fl");
  const A = fresh(), B = fresh();
  const a0 = await balance(A), b0 = await balance(B);
  const r = await fund(scn, oid, "REL", "P", [A, B], [300000000000000n, 200000000000000n], now() + 7200, DEMO, "json_eq", "status", "delivered", "pay when the shipment status reads delivered");
  if (!ok(r)) throw new Error(`${scn}: fund_watched failed`);
  await until(scn, "the order to read back", async () => (await order(oid)).status === "open");
  const ob = await tx(scn, "P", "observe", [oid], "observe while the page says pending");
  if (!ok(ob)) throw new Error(`${scn}: observe was not accepted`);
  const o1 = await until(scn, "the observation to be recorded", async () => (await order(oid)).watch.observations === 1 && (await order(oid)));
  if (o1.status !== "open") throw new Error(`${scn}: the order must stay open while the page says pending`);
  log(scn, `the page said pending: the order stays open. Waiting for docs/watch-demo.json on main to say "delivered" ...`);
  await until(scn, `the demo file to say delivered`, async () => {
    const res = await fetch(DEMO + "?cb=" + Date.now());
    const body = await res.text();
    return body.includes('"delivered"');
  }, 3600000);
  log(scn, `the page now says delivered (as served by raw.githubusercontent.com)`);
  const wait = Math.max(0, Number(o1.watch.observed_at) + RECHECK + 20 - now());
  if (wait > 0) {
    log(scn, `waiting ${wait}s for the observation cooldown`);
    await sleep(wait * 1000);
  }
  let o2;
  for (let a = 1; a <= 8; a++) {
    const ob2 = await tx(scn, "P", "observe", [oid], `observe #${a + 1} after the change`);
    if (ok(ob2)) {
      o2 = await until(scn, "the observation to be recorded", async () => (await order(oid)).watch.observations >= 2 && (await order(oid)));
      if (o2.status === "decided") break;
      log(scn, "the validators still read an older copy of the page (a CDN cache): trying again after the cooldown");
    }
    await sleep((RECHECK + 20) * 1000);
  }
  if (!o2 || o2.status !== "decided" || o2.outcome !== "release" || o2.reason !== "observed") throw new Error(`${scn}: the order was not released by the observation`);
  log(scn, `decided: ${o2.outcome} (${o2.reason}) after ${o2.watch.observations} observations`);
  const s = await tx(scn, "P", "settle", [oid], "settle");
  if (!ok(s)) throw new Error(`${scn}: settle was not accepted`);
  await waitBalance(scn, A, a0 + 300000000000000n, "recipient A");
  await waitBalance(scn, B, b0 + 200000000000000n, "recipient B");
  LOG.results.flip = { id: oid, observations: o2.watch.observations, outcome: o2.outcome, reason: o2.reason };
}

async function unreadable() {
  const scn = "unreadable", oid = id("ur");
  const r = await fund(scn, oid, "REL", "P", ["A"], [100000000000000n], now() + 7200, MISSING, "sha256", "", JQ_SHA, "a page that does not exist");
  if (!ok(r)) throw new Error(`${scn}: fund_watched failed`);
  await until(scn, "the order to read back", async () => (await order(oid)).status === "open");
  await refused(scn, "P", "observe", [oid], "unreadable", "observe");
  const o = await order(oid);
  if (o.watch.observations !== 0 || o.watch.observed_at !== 0 || o.status !== "open") throw new Error(`${scn}: an unreadable page must record nothing`);
  log(scn, "an unreadable page records nothing: observations 0, the order stays open");
  const rf = await tx(scn, "REL", "refund", [oid], "refund (so nothing stays locked)");
  if (!ok(rf)) throw new Error(`${scn}: refund was not accepted`);
  await until(scn, "the refund to be decided", async () => (await order(oid)).status === "decided");
  const s = await tx(scn, "P", "settle", [oid], "settle");
  if (!ok(s)) throw new Error(`${scn}: settle was not accepted`);
  LOG.results.unreadable = { id: oid, observations: 0 };
}

async function deadline() {
  const scn = "deadline", oid = id("dl");
  const dl = now() + 3660;
  const r = await fund(scn, oid, "REL", "P", ["A"], [100000000000000n], dl, JQ, "sha256", "", JQ_SHA, "nobody observes before the deadline");
  if (!ok(r)) throw new Error(`${scn}: fund_watched failed`);
  await until(scn, "the order to read back", async () => (await order(oid)).status === "open");
  log(scn, `funded with the shortest deadline; the page matches but nobody observes. Waiting for the deadline (${new Date(dl * 1000).toISOString().slice(11, 19)} UTC)`);
  await sleep(Math.max(0, dl + 15 - now()) * 1000);
  await refused(scn, "P", "observe", [oid], "deadline has passed", "observe after the deadline");
  const rc = await tx(scn, "P", "reclaim", [oid], "reclaim (the payer)");
  if (!ok(rc)) throw new Error(`${scn}: reclaim was not accepted`);
  const d = await until(scn, "the reclaim to be decided", async () => (await order(oid)).status === "decided" && (await order(oid)));
  if (d.outcome !== "refund" || d.reason !== "deadline") throw new Error(`${scn}: wrong decision ${JSON.stringify(d)}`);
  const acc0 = await accounting();
  const s = await tx(scn, "P", "settle", [oid], "settle");
  if (!ok(s)) throw new Error(`${scn}: settle was not accepted`);
  await until(scn, "the settlement to be counted", async () => BigInt((await accounting()).total_scheduled) === BigInt(acc0.total_scheduled) + 100000000000000n);
  LOG.results.deadline = { id: oid, outcome: d.outcome, reason: d.reason };
  log(scn, "the payer was refunded: observe after the deadline is refused, reclaim works");
}

// ------------------------------------------------------------------------------------------------------------------ run

async function fundWallets() {
  if (!chain.isStudio) return;
  for (const w of Object.values(NAMES)) {
    // Studio hands test GEN to any address on request (the same call the network's own faucet uses)
    const res = await fetch("https://studio.genlayer.com/api", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "sim_fundAccount", params: [WALLETS[w].address, 100000000000000000000] }) });
    await res.text();
  }
}

const all = { digest, unmet, flip, unreadable, deadline };
const only = process.env.ONLY ? process.env.ONLY.split(",") : Object.keys(all);
await fundWallets();
log("setup", `quay ${Q} on ${NETWORK}; payer P ${gen(await balance("P"))} GEN`);
const settled = await Promise.allSettled(only.map((k) => all[k]()));
settled.forEach((s, i) => {
  if (s.status === "rejected") log(only[i], `FAILED: ${String(s.reason?.message || s.reason).slice(0, 600)}`);
});
await until("accounting", "every scheduled transfer to leave the contract", async () => (await accounting()).in_flight === "0" || Number((await accounting()).in_flight) === 0, 300000);
const acc = await accounting();
log("accounting", `received ${acc.total_received}, scheduled ${acc.total_scheduled}, unscheduled ${acc.unscheduled}, in_flight ${acc.in_flight}, shortfall ${acc.shortfall}`);
LOG.accounting = plain(acc);
LOG.finished = new Date().toISOString();
writeFileSync(LOGFILE, JSON.stringify(LOG, null, 1));
console.log("\nRESULTS", JSON.stringify(plain(LOG.results), null, 1));
process.exit(settled.some((s) => s.status === "rejected") ? 1 : 0);
