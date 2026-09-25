"""Real browser sessions keep their interface language separate from one another."""

import pytest
from playwright.sync_api import Browser, expect

pytestmark = pytest.mark.e2e


def test_browser_language_and_saved_choice_are_session_local(browser: Browser, launch_app_url: str):
    with (
        browser.new_context(locale="fr-FR") as french,
        browser.new_context(locale="en-US") as english,
    ):
        page = french.new_page()
        page.goto(launch_app_url + "/settings/config")
        expect(page.locator("html")).to_have_attribute("lang", "fr")
        expect(page.get_by_role("link", name="Souvenir", exact=True)).to_be_visible()
        other = english.new_page()
        other.goto(launch_app_url + "/settings/config")
        expect(other.get_by_role("link", name="Memory", exact=True)).to_be_visible()

        page.get_by_role("combobox", name="Langue de l’interface").click()
        page.get_by_role("option", name="Deutsch", exact=True).click()
        expect(page.get_by_role("link", name="Erinnerung", exact=True)).to_be_visible()
        page.reload()
        expect(page.get_by_role("link", name="Erinnerung", exact=True)).to_be_visible()
        other.reload()
        expect(other.get_by_role("link", name="Memory", exact=True)).to_be_visible()


def test_french_choices_make_a_cut_without_changing_the_film_language(
    browser: Browser, launch_app_url: str
):
    with browser.new_context(locale="fr-FR") as context:
        page = context.new_page()
        page.goto(launch_app_url + "/step3")
        film_language = page.get_by_role("combobox", name="Langue du film", exact=True)
        expect(film_language).to_be_visible()
        original_film_language = film_language.input_value()
        page.goto(launch_app_url)
        memory_type = page.get_by_role("combobox", name="Type de souvenir")
        expect(memory_type).to_be_visible(timeout=30_000)
        memory_type.click()
        page.get_by_role("option", name="Moments du mois", exact=True).click()
        expect(page.get_by_role("option")).to_have_count(0)
        page.get_by_role("combobox", name="Mois", exact=True).click()
        page.get_by_role("option", name="Juin", exact=True).click()
        expect(page.get_by_role("option")).to_have_count(0)
        page.get_by_role("button", name="Monter", exact=True).click()
        expect(page.get_by_role("button", name="Exporter", exact=True)).to_be_visible(
            timeout=180_000
        )
        page.goto(launch_app_url + "/step3")
        expect(film_language).to_have_value(original_film_language)
        expect(page.locator("html")).to_have_attribute("lang", "fr")
