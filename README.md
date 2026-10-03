# CasinoBae

CasinoBae is a World of Warcraft companion platform combining a web application and a World of Warcraft addon.

Goal

Build a fully usable social casino experience inside World of Warcraft, with a companion website for players and operators.

Core capabilities:

* Player discovery and lobby
* Player whispers and invitations
* Announcements and event notifications
* Game creation and launch
* /rand-based games and real WoW results
* Player-to-player trading workflows
* Game state and settlement tracking
* Web companion interface
* Addon ↔ web integration
* Automatic state progression
* Explicit ACTION_REQUIRED handling when human interaction is required

Authority and safety model

World of Warcraft is the source of truth.

CasinoBae must never claim that a protected or unavailable Blizzard action succeeded when it did not.

When an action requires the player to interact with WoW manually, the system enters:

ACTION_REQUIRED

It displays the exact required action and resumes automatically only after a real WoW event confirms completion.

Repository

* addon/CasinoBae/ — World of Warcraft addon
* site/ — web application
* docs/ — architecture and technical documentation

Development status

Project bootstrap.

The implementation is being developed incrementally with automated validation and real-event-driven state management.