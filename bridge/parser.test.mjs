import assert from "node:assert/strict";
import { parseLua, parsePayloadLine, unescapeLuaString } from "./parser.mjs";

const fixture = String.raw`
CasinoBaeDB = {
  ["queue"] = {
    {
      ["name"] = "PLAYER_JOINED",
      ["state"] = "LOBBY_OPEN",
      ["time"] = "14:02:03",
      ["payload_line"] = "name=Alice;role=dealer"
    },
    {
      ["name"] = "ACTION_REQUIRED",
      ["state"] = "ACTION_REQUIRED",
      ["time"] = "14:02:04",
      ["payload_line"] = "message=Invite player;action=INVITE"
    },
    {
      ["name"] = "ROLL",
      ["state"] = "RUNNING",
      ["time"] = "14:02:05",
      ["payload_line"] = "player=Alice;roll=87"
    },
  },
  ["other"] = true,
}
`;

const events = parseLua(fixture);
assert.equal(events.length, 3);
assert.deepEqual(events[0], {
  name: "PLAYER_JOINED",
  state: "LOBBY_OPEN",
  time: "14:02:03",
  payload: { name: "Alice", role: "dealer" }
});
assert.equal(events[1].name, "ACTION_REQUIRED");
assert.equal(events[1].payload.action, "INVITE");
assert.equal(events[2].payload.roll, "87");

assert.deepEqual(parsePayloadLine("message=a\\;b;key=a\\=b"), {
  message: "a;b",
  key: "a=b"
});
assert.equal(unescapeLuaString("line\\nnext"), "line\nnext");

assert.deepEqual(parseLua("CasinoBaeDB = { [\"queue\"] = {}, }"), []);
console.log("CasinoBae bridge parser tests: PASS (4 assertions)");
