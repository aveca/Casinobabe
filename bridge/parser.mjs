export function unescapeLuaString(value) {
  return value
    .replace(/\\(["'\\])/g, "$1")
    .replace(/\\n/g, "\n")
    .replace(/\\r/g, "\r")
    .replace(/\\t/g, "\t");
}

export function parsePayloadLine(line) {
  const out = {};
  if (!line) return out;
  const parts = [];
  let current = "";
  let escaped = false;
  for (const ch of line) {
    if (escaped) { current += ch; escaped = false; continue; }
    if (ch === "\\") { escaped = true; continue; }
    if (ch === ";") { parts.push(current); current = ""; continue; }
    current += ch;
  }
  parts.push(current);
  for (const part of parts) {
    const i = part.indexOf("=");
    if (i <= 0) continue;
    out[part.slice(0, i)] = part.slice(i + 1);
  }
  return out;
}

export function parseLua(text) {
  const records = [];
  const field = (name) =>
    "\\[\\s*[\\\"']" + name + "[\\\"']\\s*\\]\\s*=\\s*[\\\"']((?:\\\\.|[^\\\"'\\\\])*)[\\\"']";
  const re = new RegExp(
    field("name") + "[\\s\\S]*?" +
    field("state") + "[\\s\\S]*?" +
    field("time") + "[\\s\\S]*?" +
    field("payload_line"),
    "gm"
  );
  let m;
  while ((m = re.exec(text))) {
    records.push({
      name: unescapeLuaString(m[1]),
      state: unescapeLuaString(m[2]),
      time: unescapeLuaString(m[3]),
      payload: parsePayloadLine(unescapeLuaString(m[4]))
    });
  }
  return records;
}
