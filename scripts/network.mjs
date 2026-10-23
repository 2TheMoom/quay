// Which GenLayer network the scripts talk to. Default is Bradbury; NETWORK=studionet uses the hosted Studio network (chain id 61999).
//   NETWORK=studionet node scripts/deploy.mjs contracts/lineage.py
import { studionet, testnetBradbury } from "genlayer-js/chains";

export const NETWORK = process.env.NETWORK === "studionet" ? "studionet" : "bradbury";
export const chain = NETWORK === "studionet" ? studionet : testnetBradbury;
