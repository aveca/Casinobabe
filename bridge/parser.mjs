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

/**
 * Parse legacy CasinoBaeDB format (queue[] with name/state/time/payload_line)
 */
function parseLegacyFormat(text) {
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

/**
 * Parse modern CasinobabeDB format (dealer.log + dealer.sessions)
 * Extracts dealer lifecycle events for Supabase ingestion.
 */
export function parseModernFormat(text) {
  const records = [];
  
  // Helper to extract a table by key from Lua text using brace matching
  function extractTable(text, parentKey, childKey) {
    // Find parent table start - SavedVariables uses ["key"] format
    const escapedParent = parentKey.replace(/[.*+?^${}()|\\]/g, '\\$&');
    const parentStartRe = new RegExp('\\["' + escapedParent + '"\\]\\s*=\\s*\\{');
    const parentStartMatch = text.match(parentStartRe);
    if (!parentStartMatch) return null;
    
    const parentStartIdx = parentStartMatch.index + parentStartMatch[0].length;
    // Find matching closing brace for parent
    let braceCount = 1;
    let inString = false;
    let escape = false;
    let parentEndIdx = -1;
    
    for (let i = parentStartIdx; i < text.length; i++) {
      const ch = text[i];
      if (escape) { escape = false; continue; }
      if (ch === '\\') { escape = true; continue; }
      if (ch === '"' && !escape) { inString = !inString; continue; }
      if (!inString) {
        if (ch === '{') braceCount++;
        if (ch === '}') {
          braceCount--;
          if (braceCount === 0) {
            parentEndIdx = i;
            break;
          }
        }
      }
    }
    if (parentEndIdx === -1) return null;
    
    const parentContent = text.substring(parentStartIdx, parentEndIdx);
    
    // Find child table within parent content - SavedVariables uses ["key"] format
    const escapedChild = childKey.replace(/[.*+?^${}()|\\]/g, '\\$&');
    const childStartRe = new RegExp('\\["' + escapedChild + '"\\]\\s*=\\s*\\{');
    const childStartMatch = parentContent.match(childStartRe);
    if (!childStartMatch) return null;
    
    const childStartIdx = childStartMatch.index + childStartMatch[0].length;
    braceCount = 1;
    inString = false;
    escape = false;
    let childEndIdx = -1;
    
    for (let i = childStartIdx; i < parentContent.length; i++) {
      const ch = parentContent[i];
      if (escape) { escape = false; continue; }
      if (ch === '\\') { escape = true; continue; }
      if (ch === '"' && !escape) { inString = !inString; continue; }
      if (!inString) {
        if (ch === '{') braceCount++;
        if (ch === '}') {
          braceCount--;
          if (braceCount === 0) {
            childEndIdx = i;
            break;
          }
        }
      }
    }
    if (childEndIdx === -1) return null;
    
    return parentContent.substring(childStartIdx, childEndIdx);
  }
  
  // Helper to parse Lua table entries (simplified)
  function parseLogEntries(logText) {
    const entries = [];
    // Match lines like: "[HH:MM] sessionId player EVENT details"
    // Lua format: each entry is a quoted string ending with comma
    const lineRe = /\[\s*(\d{2}:\d{2})\]\s+([^\s]+)\s+(\S+)\s+(.+?)(?:",?|$)/g;
    let match;
    while ((match = lineRe.exec(logText)) !== null) {
      // Clean up trailing comma/quote from event
      let event = match[4].trim();
      event = event.replace(/",$/, '').replace(/^"/, '').replace(/"$/, '');
      entries.push({
        time: match[1],
        sessionId: match[2],
        player: match[3],
        event: event
      });
    }
    return entries;
  }
  
  // Parse dealer.log
  const logContent = extractTable(text, "dealer", "log");
  if (logContent) {
    const logEntries = parseLogEntries(logContent);
    for (const e of logEntries) {
      records.push({
        name: e.event.split(" ")[0], // e.g., "CAPITAL_SHOW", "STATE", "WHISPER_REPLY"
        state: e.sessionId === "-----" ? "DEALER_GLOBAL" : e.sessionId,
        time: e.time,
        payload: {
          player: e.player,
          details: e.event,
          sessionId: e.sessionId
        }
      });
    }
  }
  
  // Parse dealer.sessions for active session state changes
  const sessionsContent = extractTable(text, "dealer", "sessions");
  if (sessionsContent) {
    // Find session blocks
    const sessionRe = /\["([^"]+)"\]\s*=\s*\{([\s\S]*?)\n\s*\}/g;
    let sessionMatch;
    while ((sessionMatch = sessionRe.exec(sessionsContent)) !== null) {
      const playerName = sessionMatch[1];
      const sessionData = sessionMatch[2];
      
      // Extract key fields
      const stateMatch = sessionData.match(/\["state"\]\s*=\s*"([^"]+)"/);
      const sessionIdMatch = sessionData.match(/\["sessionId"\]\s*=\s*"([^"]+)"/);
      const stateTimeMatch = sessionData.match(/\["stateTime"\]\s*=\s*(\d+)/);
      const gameMatch = sessionData.match(/\["game"\]\s*=\s*"([^"]+)"/);
      const stakeMatch = sessionData.match(/\["stake"\]\s*=\s*(\d+)/);
      
      if (stateMatch) {
        records.push({
          name: "SESSION_STATE",
          state: stateMatch[1],
          time: stateTimeMatch ? new Date(parseInt(stateTimeMatch[1]) * 1000).toISOString().substr(11, 8) : "00:00:00",
          payload: {
            player: playerName,
            sessionId: sessionIdMatch ? sessionIdMatch[1] : "",
            game: gameMatch ? gameMatch[1] : "",
            stake: stakeMatch ? parseInt(stakeMatch[1]) : 0,
            state: stateMatch[1]
          }
        });
      }
    }
  }
  
  return records;
}

export function parseLua(text) {
  // Try modern format first (CasinobabeDB)
  if (text.includes('CasinobabeDB') || text.includes('["dealer"]')) {
    const modern = parseModernFormat(text);
    if (modern.length > 0) {
      console.log(`[parser] Parsed ${modern.length} events from CasinobabeDB (modern format)`);
      return modern;
    }
  }
  
  // Fallback to legacy format (CasinoBaeDB)
  const legacy = parseLegacyFormat(text);
  if (legacy.length > 0) {
    console.log(`[parser] Parsed ${legacy.length} events from CasinoBaeDB (legacy format)`);
    return legacy;
  }
  
  console.log("[parser] No recognized format found in SavedVariables");
  return [];
}
