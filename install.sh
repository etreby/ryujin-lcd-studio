#!/usr/bin/env bash
# Install the launcher and application menu entry. This script deliberately
# does not start the app, touch the cooler service, or alter its configuration.
set -euo pipefail

APP_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${HOME}/.local/share/ryujin-lcd-studio/venv/bin/python"
BIN_DIR="${HOME}/.local/bin"
APPLICATIONS_DIR="${HOME}/.local/share/applications"
LAUNCHER="${BIN_DIR}/ryujin-lcd-studio"
DESKTOP="${APPLICATIONS_DIR}/ryujin-lcd-studio.desktop"
STAMP="$(date +%Y%m%d-%H%M%S)"

if [[ ! -x "${PYTHON}" ]]; then
  echo "Expected Python environment not found: ${PYTHON}" >&2
  exit 1
fi
"${PYTHON}" -c 'import PyQt5, PIL, liquidctl.driver.asus_ryujin_lcd'

mkdir -p "${BIN_DIR}" "${APPLICATIONS_DIR}"
for target in "${LAUNCHER}" "${DESKTOP}"; do
  if [[ -e "${target}" ]]; then
    cp -a -- "${target}" "${target}.before-install-${STAMP}"
  fi
done

cat >"${LAUNCHER}" <<EOF
#!/usr/bin/env bash
exec "${PYTHON}" "${APP_DIR}/app.py" "\$@"
EOF
chmod 0755 "${LAUNCHER}"

cat >"${DESKTOP}" <<EOF
[Desktop Entry]
Type=Application
Name=Ryujin LCD Studio
Comment=Customize the ASUS Ryujin III Extreme LCD
Exec=${LAUNCHER}
Icon=preferences-desktop-display
Terminal=false
Categories=Settings;HardwareSettings;
StartupNotify=true
EOF
chmod 0644 "${DESKTOP}"
echo "Installed launcher and menu entry. Existing files were backed up with .before-install-${STAMP}."
