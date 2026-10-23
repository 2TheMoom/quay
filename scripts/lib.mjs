// Shared helpers for the live scripts (Bradbury by default, Studionet with NETWORK=studionet, see network.mjs). Test wallets live outside the repo (see the Salvage notes).
import { readFileSync } from "fs";
import { createAccount, createClient } from "genlayer-js";
import { chain } from "./network.mjs";

export const WALLETS = JSON.parse(readFileSync("C:/Users/olumi/.genlayer-test-wallets/wallets.json", "utf8"));
export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
export const ts = () => new Date().toISOString().slice(11, 19);
const STATUS = ["UNINITIALIZED", "PENDING", "PROPOSING", "COMMITTING", "REVEALING", "ACCEPTED", "UNDETERMINED", "FINALIZED", "CANCELED", "APPEAL_REVEALING", "APPEAL_COMMITTING", "READY_TO_FINALIZE", "VALIDATORS_TIMEOUT", "LEADER_TIMEOUT"];
/** Bradbury's records carry statusName and txExecutionResultName. Studionet's carry a numeric status and the leader's execution_result. */
export const statusName = (r) => r?.statusName || (typeof r?.status === "number" || /^[0-9]+$/.test(String(r?.status ?? "x")) ? STATUS[Number(r.status)] : r?.status);
export const execName = (r) => r?.txExecutionResultName ?? (r?.consensus_data?.leader_receipt?.[0]?.execution_result === "SUCCESS" ? "FINISHED_WITH_RETURN" : r?.consensus_data?.leader_receipt?.[0] ? "FINISHED_WITH_ERROR" : undefined);
/** A refused call's own reason as the network recorded it. Studionet's simulation error says only "execution failed", but the leader's receipt carries the text. */
export const refusalOf = (r) => {
  const res = r?.consensus_data?.leader_receipt?.[0]?.result;
  return res?.status === "rollback" && typeof res.payload === "string" ? res.payload : undefined;
};
export const plain = (x) => (x instanceof Map ? Object.fromEntries([...x].map(([k, v]) => [k, plain(v)])) : Array.isArray(x) ? x.map(plain) : typeof x === "bigint" ? x.toString() : x);

export const reader = createClient({ chain });
export const clientFor = (name) => createClient({ chain, account: createAccount(WALLETS[name].privateKey) });

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
    const st = statusName(r);
    if (st && !["PENDING", "ACTIVATED", "PROPOSING", "COMMITTING", "REVEALING", "UNDEFINED"].includes(st)) {
      return { hash, status: st, exec: execName(r), result: r.resultName ?? r.result_name, tx: r };
    }
    await sleep(10000);
  }
  throw new Error(`tx ${hash} did not settle`);
}

export const read = (address, functionName, args = []) => reader.readContract({ address, functionName, args }).then(plain);
