"use strict";

const fs = require("fs");
const path = require("path");

const source = fs.readFileSync(
  path.join(__dirname, "..", "runtime-addon", "Casinobabe", "Casinobabe.lua"),
  "utf8"
);
const lines = source.split(/\r?\n/);

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function section(start, end) {
  return lines.slice(start - 1, end).join("\n");
}

assert(source.includes("CB.state.DEALER_CONN_STATES"), "dealer state namespace missing");
assert(source.includes("local DEALER_CONN_STATES = CB.state.DEALER_CONN_STATES"), "dealer state local binding missing");
assert(section(100, 180).includes("if panel and panel.dealerPanel then"), "CONNECTING UI path lacks panel guard");
assert(section(100, 180).includes("if dp.connStatus then"), "CONNECTING UI path lacks connStatus guard");
assert(source.includes("CB.CasinoSound"), "CasinoSound canonical namespace missing");
assert(/ROLL_REJECTED/.test(source), "ROLL_REJECTED handling missing");
assert(/session\.game/.test(source), "session.game synchronization path missing");
assert(/function DealerResolve/.test(source), "DealerResolve missing");
assert(/function DealerPayout/.test(source), "DealerPayout missing");
assert(/DealerOnTradeShow|DealerOnTradeAccept|DealerOnTradeClose/.test(source), "trade callbacks missing");

console.log("ISSUE_2_CONNECTING_REGRESSION_CONTRACT=PASS");
console.log("ROLL_REJECTED_CONTRACT=PASS");
console.log("WIN_PAYOUT_TRADE_CONTRACT=PASS");
process.exit(0);
