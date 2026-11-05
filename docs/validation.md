# Validation and scope

The working editor was tested on Linux/KDE Plasma Wayland with an ASUS Ryujin III
Extreme, USB `0b05:1bcb`, firmware `AURJ3-S5F9-0104`, and the experimental
liquidctl driver commit pinned in requirements.txt.

The saved-profile live dashboard/Apply path was exercised on the LCD during
initial installation. The physical live dashboard was confirmed working. Media
validation and editor previews cover static images/GIFs, but physical static/GIF
output has not been separately observed. Brightness/orientation and pump/fan duty
controls are not exposed by this editor. This release does not claim compatibility
with all ASUS coolers or all firmware versions.

Tests exercise atomic saves, service ownership, write/start failures and rollback,
Apply locking, read-only isolated previews, malformed profiles, actual driver
schema validation, media handling, stable sensor IDs, missing devices, selected
host-sensor sampling, and no execution of arbitrary saved custom commands in
preview. Fixtures/fake runners avoid live systemd or cooler changes.

Native editor layouts were previously inspected at 1080×760 and 900×620.
Selected host values refresh without dirtying the profile. Cooler preview
values remain examples; host preview values can be live when enabled.

Publication checks use a fresh isolated environment, unit tests and the offscreen
smoke test. They do not Apply to hardware, alter saved profiles, replace the
working venv or stop/restart the existing display service. The README screenshot
is generated from an isolated example profile.

The pinned upstream renderer/schema APIs are private and may change in other
liquidctl revisions. This project keeps the upstream dependency and does not
replace the LCD USB driver. Source APIs and dependency licenses are documented
in README.md and requirements.txt.
