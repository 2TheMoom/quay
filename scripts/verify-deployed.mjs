// Proves the contract source in this repo is exactly what runs on the network: it downloads the deployed code and compares it with the file in
// this repo (line endings ignored). Needs no wallet.
//   NETWORK=studionet node scripts/verify-deployed.mjs
import { readFileSync } from "fs";
import { createHash } from "crypto";
import { createClient } from "genlayer-js";
import { chain, NETWORK } from "./network.mjs";

const DEPLOYMENTS = {
  studionet: [["Quay v2 (contracts/quay.py)", "0x1678e8C242AB557ACF772e17aE715EC80Eac1d39", "contracts/quay.py"]],
};
const client = createClient({ chain });
const norm = (s) => s.replace(/\r\n/g, "\n");
const sha = (s) => createHash("sha256").update(s).digest("hex");
console.log(`network: ${NETWORK} (chain id ${chain.id})`);
let allSame = true;
for (const [label, address, file] of DEPLOYMENTS[NETWORK] ?? []) {
  const onchain = norm(await client.getContractCode(address));
  const local = norm(readFileSync(file, "utf8"));
  const same = onchain === local;
  allSame &&= same;
  console.log(`${same ? "IDENTICAL" : "DIFFERENT"}  ${label}  ${address}\n           on chain sha256 ${sha(onchain)}  (${onchain.length} chars)\n           in repo  sha256 ${sha(local)}  (${local.length} chars)`);
}
process.exitCode = allSame ? 0 : 1;
