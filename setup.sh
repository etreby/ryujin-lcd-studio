#!/usr/bin/env bash
# Provision a new per-user environment. Never touch the cooler or display service.
set -euo pipefail
APP_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
VENV="${HOME}/.local/share/ryujin-lcd-studio/venv"
if [[ $# -ne 0 ]]; then
    echo 'Usage: ./setup.sh (no arguments). Creates the standard user environment if absent.' >&2
    exit 1
fi
if [[ -e "${VENV}" ]]; then
    echo 'Preserving existing environment; validating its required imports.'
else
    command -v git >/dev/null || { echo 'Install git first.' >&2; exit 1; }
    python3 -c 'import sys; assert sys.version_info >= (3, 10), "Python 3.10+ required"'
    python3 -m venv "${VENV}"
    "${VENV}/bin/python" -m pip install -r "${APP_DIR}/requirements.txt"
fi
"${VENV}/bin/python" -c 'from PyQt5 import QtWidgets; from PIL import Image; from liquidctl.driver.asus_ryujin_lcd import _render_lcd_stats, _default_lcd_stats_config, _validate_lcd_stats_config'
mkdir -p "${HOME}/.local/bin"
CLI="${HOME}/.local/bin/ryujin-lcd"
if [[ ! -e "${CLI}" ]]; then
    # Emit shell-escaped paths rather than interpolating them into executable code.
    {
        printf '#!/usr/bin/env bash\nexec '
        printf '%q ' "${VENV}/bin/liquidctl" --match 'Ryujin III Extreme'
        printf '"$@"\n'
    } > "${CLI}"
    chmod 0755 "${CLI}"
else
    echo 'Preserving existing ryujin-lcd command.'
fi
"${APP_DIR}/install.sh"
echo 'Setup complete. USB access is configured separately; see README.md.'
