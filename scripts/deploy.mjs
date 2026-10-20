// Deploys one contract to Bradbury with a test wallet kept outside the repo. Constructor args are a JSON array so numbers stay numbers.
//   node scripts/deploy.mjs contracts/quay.py
import { readFileSync } from "fs";
import { clientFor } from "./lib.mjs";

const [file, argsJson] = process.argv.slice(2);
if (!file) {
  console.error("usage: node scripts/deploy.mjs <contract.py> ['[args json]']");
  process.exit(1);
}
const args = argsJson ? JSON.parse(argsJson).map((a) => (typeof a === "number" ? BigInt(a) : a)) : [];
const client = clientFor("gl-test-1");
const code = new Uint8Array(readFileSync(file));
console.log(`deploying ${file} (${code.length} bytes) args=${JSON.stringify(args, (_, v) => (typeof v === "bigint" ? v.toString() : v))}`);
const hash = await client.deployContract({ code, args });
console.log("tx", hash);
const receipt = await client.waitForTransactionReceipt({ hash, status: "ACCEPTED", retries: 200, interval: 5000 });
console.log("exec", receipt.txExecutionResultName);
console.log("ADDRESS", receipt.txDataDecoded?.contractAddress ?? receipt.data?.contract_address);
