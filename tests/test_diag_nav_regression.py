#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Regression test: the corpse-diagnostic module must fill its own nav block.

Live evidence: 306 Lua errors "CasinobabeDiag.lua:173: attempt to index global
'o' (a nil value)", all spaced exactly 2s apart - the module's C_Timer ticker
that runs D.collect() while the player is a ghost. D.collect() builds
`local out = {...}` but its navigation block indexed the nil global `o`.

The test runs the FIXED module through the same mocks. It also replays a copy
of the original broken snippet to prove the failure mode is still detectable,
so the test cannot silently rot into a no-op.
"""

import io
import os
import sys
import lupa

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNTIME = os.path.join(ROOT, "runtime-addon", "Casinobabe", "CasinobabeDiag.lua")
MOCKS = os.path.join(ROOT, "tests", "diag_mocks.lua")
ASSERTS = os.path.join(ROOT, "tests", "diag_asserts.lua")

# The exact broken line, kept verbatim so the test always demonstrates the
# original failure mode against the fixture below.
BROKEN_LINE = "    if o.playerMap and o.corpseMap then"

def read(path):
    return io.open(path, encoding="utf-8-sig").read()

def run(source):
    lua = lupa.LuaRuntime(unpack_returned_tuples=True)
    lua.execute(read(MOCKS))
    ns = lua.eval("function(t) return t end")({})
    loader = lua.eval("function(s, a, b) return load(s)(a, b) end")
    loader(source, "Casinobabe", ns)
    return lua.eval("function(s, a, b) return load(s)(a, b) end")(
        read(ASSERTS), "test", ns)

def main():
    if not os.path.exists(RUNTIME):
        print("FAIL: runtime module missing: %s" % RUNTIME)
        return 2

    fixed = read(RUNTIME)
    if BROKEN_LINE in fixed:
        print("FAIL: the broken line is still present in the runtime module")
        return 1

    try:
        ok = run(fixed)
    except Exception as exc:
        print("FAIL: fixed module raised: %s" % str(exc).replace("\n", " ")[:200])
        return 1
    if not ok:
        print("FAIL: fixed module did not satisfy the assertions")
        return 1
    print("PASS: D.collect() fills the nav block; format/formatCompact render")

    # Prove the fixture still detects the original defect.
    broken = fixed.replace(
        "    if out.playerMap and out.corpseMap then", BROKEN_LINE)
    try:
        if run(broken):
            print("FAIL: broken variant unexpectedly passed")
            return 1
        print("FAIL: broken variant passed silently")
        return 1
    except Exception as exc:
        msg = str(exc).replace("\n", " ")[:120]
        if "index" not in msg or "'o'" not in msg:
            print("FAIL: broken variant failed differently: %s" % msg)
            return 1
        print("PASS: broken variant reproduces %s" % msg)

    print("DIAG_NAV_REGRESSION=PASS")
    return 0

if __name__ == "__main__":
    sys.exit(main())
