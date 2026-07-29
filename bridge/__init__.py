"""ALiver Windows Bridge package initialization."""

from bridge.vtube_motion import install_vtube_motion_patch
from bridge.vtube_studio_auth_fix import install_vtube_studio_auth_fix

install_vtube_studio_auth_fix()
install_vtube_motion_patch()
