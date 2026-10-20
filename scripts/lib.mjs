// Shared helpers for the live Bradbury scripts. Test wallets live outside the repo (see the Salvage notes).
import { readFileSync } from "fs";
import { createAccount, createClient } from "genlayer-js";
import { testnetBradbury } from "genlayer-js/chains";

export const WALLETS = JSON.parse(readFileSync("C:/Users/olumi/.genlayer-test-wallets/wallets.json", "utf8"));
export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
export const ts = () => new Date().toISOString().slice(11, 19);
export const plain = (x) => (x instanceof Map ? Object.fromEntries([...x].map(([k, v]) => [k, plain(v)])) : Array.isArray(x) ? x.map(plain) : typeof x === "bigint" ? x.toString() : x);

export const reader = createClient({ chain: testnetBradbury });
export const clientFor = (name) => createClient({ chain: testnetBradbury, account: createAccount(WALLETS[name].privateKey) });

/** The contract's own refusal text from a node error (it arrives as raw bytes inside the message). */
export function reasonOf(err) {
  const text = String(err?.message || err);
  const m = text.match(/ReturnData:\[\]uint8\{([^}]*)\}/);
  if (m) {
    const bytes = m[1].split(",").map((s) => parseInt(s.trim(), 16)).filter((n) => !Number.isNaN(n));
    const runs = [];
    let cur = "";
    for (const b of bytes) {
      if (b >= 32 && b < 127) cur += String.fromCharCode(b);
      else { if (cur.length >= 10) runs.push(cur); cur = ""; }
    }
    if (cur.length >= 10) runs.push(cur);
    const sentence = runs.find((r) => r.includes(" "));
    if (sentence) return sentence;
  }
  return text.slice(0, 200);
}

/** Sends a write and waits for the network's answer, returning the transaction as read back. */
export async function send(client, address, functionName, args = [], { value, retries = 6 } = {}) {
  let hash;
  for (let a = 1; ; a++) {
    try {
      hash = await client.writeContract({ address, functionName, args, ...(value ? { value } : {}) });
      break;
    } catch (e) {
      if (a >= retries) throw e;
      await sleep(10000);
    }
  }
  for (let i = 0; i < 240; i++) {
    const r = await client.getTransaction({ hash }).catch(() => null);
    const st = r?.statusName;
    if (st && !["PENDING", "ACTIVATED", "PROPOSING", "COMMITTING", "REVEALING", "UNDEFINED"].includes(st)) {
      return { hash, status: st, exec: r.txExecutionResultName, result: r.resultName, tx: r };
    }
    await sleep(10000);
  }
  throw new Error(`tx ${hash} did not settle`);
}

export const read = (address, functionName, args = []) => reader.readContract({ address, functionName, args }).then(plain);
