"""Login page — adapts to configured auth provider."""

from __future__ import annotations

import logging

from nicegui import app, ui

from immich_memories.config_models_auth import AuthConfig
from immich_memories.ui.auth import set_session, verify_credentials
from immich_memories.ui.theme import apply_theme

logger = logging.getLogger(__name__)


def render_login_page(auth_config: AuthConfig) -> None:
    """Render the login page, adapting to the configured provider."""
    # WHY: apply_theme() reads app.storage.user for dark/light pref.
    # Works before auth because NiceGUI assigns storage per browser.
    apply_theme()

    with ui.column().classes("absolute-center items-center gap-6"):
        with ui.row().classes("items-center gap-3"):
            ui.icon("movie").classes("text-4xl").style("color: var(--im-primary)")
            ui.label("Immich Memories").classes("text-2xl font-bold").style("color: var(--im-text)")

        with ui.card().classes("w-80 p-6"):
            if auth_config.provider == "basic":
                _render_basic_form(auth_config)
            elif auth_config.provider == "oidc":
                _render_oidc_form(auth_config)


def _render_basic_form(auth_config: AuthConfig) -> None:
    username_input = ui.input("Username").classes("w-full").props("outlined dense")
    password_input = (
        ui.input("Password", password=True, password_toggle_button=True)
        .classes("w-full")
        .props("outlined dense")
    )
    error_label = ui.label("").classes("text-xs").style("color: var(--im-error); display: none")

    async def attempt_login() -> None:
        user = username_input.value.strip()
        pwd = password_input.value
        if verify_credentials(user, pwd, auth_config):
            # WHY: app.storage.user IS request.session in NiceGUI (same signed cookie).
            # Setting here makes it visible to HTTP middleware on next page load.
            set_session(app.storage.user, username=user, provider="basic")
            ui.navigate.to("/")
        else:
            error_label.style("display: block")
            error_label.set_text("Invalid username or password")

    password_input.on("keydown.enter", attempt_login)
    ui.button("Sign in", on_click=attempt_login).classes("w-full mt-2").props(
        "color=primary no-caps"
    )


def _render_oidc_form(auth_config: AuthConfig) -> None:
    async def start_oidc() -> None:
        ui.navigate.to("/auth/authorize")

    ui.button(auth_config.button_text, on_click=start_oidc).classes("w-full").props(
        "color=primary no-caps"
    ).style("font-weight: 500")
    ui.label("You will be redirected to your identity provider").classes(
        "text-xs text-center mt-2"
    ).style("color: var(--im-text-muted)")
