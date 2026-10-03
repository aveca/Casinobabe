# CasinoBae architecture

WoW is authoritative. The addon observes real WoW events; the web layer consumes confirmed state.

State flow:
IDLE -> READY -> ACTION_REQUIRED -> READY

No protected Blizzard action is represented as successful unless a real event confirms it. Human-required operations explicitly enter ACTION_REQUIRED and resume only after confirmation.