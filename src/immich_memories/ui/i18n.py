"""Browser language selection for the interface, separate from a film's locale."""

from __future__ import annotations

from operator import itemgetter
from typing import cast

from nicegui import ui
from nicegui.language import Language

from immich_memories.i18n import DEFAULT_LOCALE, SUPPORTED_LOCALES, get_translator


def _supported_language(value: str) -> str | None:
    tag = value.strip().lower().replace("_", "-")
    supported = {code.lower(): code for code in SUPPORTED_LOCALES}
    if tag in supported:
        return supported[tag]
    if tag in {"zh", "zh-cn", "zh-sg"}:
        return "zh-Hans"
    if tag == "pt":
        return "pt-PT"
    return supported.get(tag.split("-")[0])


def resolve_ui_locale(accept_language: str, *, preference: str = "auto") -> str:
    """Use the saved UI choice, or the supported browser language, else English."""
    if chosen := _supported_language(preference):
        return chosen
    candidates: list[tuple[float, str]] = []
    for item in accept_language.split(","):
        tag, *parameters = item.strip().split(";")
        quality = 1.0
        try:
            for parameter in parameters:
                if parameter.strip().startswith("q="):
                    quality = float(parameter.strip()[2:])
        except ValueError:
            continue
        if 0 < quality <= 1 and (code := _supported_language(tag)):
            candidates.append((quality, code))
    candidates.sort(key=itemgetter(0), reverse=True)
    return candidates[0][1] if candidates else DEFAULT_LOCALE


def current_ui_locale() -> str:
    """Resolve the current NiceGUI client's language without changing process-wide state."""
    from nicegui import app
    from nicegui.storage import request_contextvar

    request = request_contextvar.get()
    if request is None:
        return DEFAULT_LOCALE
    try:
        preference = app.storage.user.get("ui_language", "auto")
    except RuntimeError:
        return DEFAULT_LOCALE
    return resolve_ui_locale(request.headers.get("accept-language", ""), preference=preference)


class LocalizedPage(ui.page):
    """Resolve Quasar widgets and the HTML language for each request, not the shared route."""

    def resolve_language(self) -> Language:
        """Use NiceGUI's language tags for the current browser's chosen locale."""
        locale = current_ui_locale()
        return cast(
            Language,
            {"en": "en-US", "pt-PT": "pt", "ko": "ko-KR", "zh-Hans": "zh-CN"}.get(locale, locale),
        )


def tr(message: str, *, locale_code: str | None = None, **values: object) -> str:
    """Translate an interface template for this browser, preserving inserted user content."""
    translator = get_translator(locale_code or current_ui_locale(), domain="ui")
    translated = translator.gettext(message)
    return translated.format(**values) if values else translated


def N_(message: str) -> str:
    """Mark a stored label for extraction; translate it only when a browser renders it."""
    return message


def tr_options(options: dict | list, *, locale_code: str | None = None) -> dict:
    """Translate choice labels while retaining the values used by the pipeline."""
    labels = options if isinstance(options, dict) else {value: value for value in options}
    return {key: tr(label, locale_code=locale_code) for key, label in labels.items()}


def render_language_selector() -> None:
    """Offer a persistent per-browser UI language without editing the film configuration."""
    from nicegui import app, ui

    from immich_memories.i18n import babel_locale

    def change(event) -> None:
        if event.value == "auto" or event.value in SUPPORTED_LOCALES:
            app.storage.user["ui_language"] = event.value
            ui.navigate.reload()

    options = {"auto": tr("Automatic (browser)")}
    for code in SUPPORTED_LOCALES:
        name = babel_locale(code).get_display_name(code.replace("-", "_")) or code
        options[code] = name[:1].upper() + name[1:]
    preference = app.storage.user.get("ui_language", "auto")
    ui.select(
        options,
        value=preference if preference in options else "auto",
        label=tr("Interface language"),
        on_change=change,
    ).classes("w-full").props("dense outlined options-dense")
