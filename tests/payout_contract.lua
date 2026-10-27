
local NS, R, CP
for k, v in pairs(_G) do
    if type(v) == "table" and rawget(v, "ComputePayoutFromMultiplier") then
        NS = v; R = rawget(v, "GameRules"); CP = rawget(v, "ComputePayoutFromMultiplier")
    end
end
assert(NS and CP and R, "payout helpers not exposed on the addon namespace")
local out = {}
local function row(label, mult, stake, expect)
    local got = CP(label, stake, mult)
    out[#out+1] = string.format("%-9s mult=x%-2s stake=%-4s -> net %-5s (attendu %-5s) %s",
        label, tostring(mult), tostring(stake), tostring(got), tostring(expect),
        (got == expect) and "OK" or "MISMATCH")
end
row("normal", 0, 10, -10); row("high", 0, 10, -10); row("lucky7", 0, 10, -10)
row("roulette", 0, 10, -10); row("dice", 0, 10, -10); row("blackjack", 0, 10, -10)
row("normal", 2, 10, 10); row("normal", 3, 10, 20)
row("high", 3, 10, 20); row("high", 4, 10, 30)
row("lucky7", 7, 10, 60)
row("roulette", 2, 10, 10); row("roulette", 5, 10, 40)
row("dice", 2, 10, 10); row("dice", 4, 10, 30)
row("blackjack", 1, 10, 10); row("blackjack", 2, 10, 0)
out[#out+1] = "--- multiplicateurs resolus avec les BONS arguments ---"
out[#out+1] = "normal   59/100/12      -> " .. R.normal.resolve(59) .. "/" .. R.normal.resolve(100) .. "/" .. R.normal.resolve(12)
out[#out+1] = "high     76/100/10      -> " .. R.high.resolve(76) .. "/" .. R.high.resolve(100) .. "/" .. R.high.resolve(10)
out[#out+1] = "lucky7   97/90          -> " .. R.lucky7.resolve(97) .. "/" .. R.lucky7.resolve(90)
out[#out+1] = "roulette 10red/50red/60black/50green -> " .. R.roulette.resolve(10,"red") .. "/" .. R.roulette.resolve(50,"red") .. "/" .. R.roulette.resolve(60,"black") .. "/" .. R.roulette.resolve(50,"green")
out[#out+1] = "dice     over(4,4)/seven(3,4)/under(3,4) -> " .. R.dice.resolve("over",{4,4}) .. "/" .. R.dice.resolve("seven",{3,4}) .. "/" .. R.dice.resolve("under",{3,4})
out[#out+1] = "blackjack 100v40/100v100/95v100 -> " .. R.blackjack.resolve(100,40) .. "/" .. R.blackjack.resolve(100,100) .. "/" .. R.blackjack.resolve(95,100)
return table.concat(out, "\n")
