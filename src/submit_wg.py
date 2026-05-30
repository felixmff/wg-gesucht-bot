# -*- coding: utf-8 -*-
from pathlib import Path

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


def remove_cookies_popup(page: Page):
    page.evaluate(
        "document.querySelectorAll('#cmpbox, #cmpbox2').forEach(el => el.remove())"
    )


def remove_lightboxes(page: Page):
    page.evaluate(
        "document.querySelectorAll('.lightbox').forEach(el => el.remove())"
    )


def dismiss_overlays(page: Page):
    remove_cookies_popup(page)
    remove_lightboxes(page)
    for label in ("Accept all", "Alle akzeptieren", "Akzeptieren"):
        try:
            page.get_by_role("button", name=label).click(timeout=1500)
            break
        except PlaywrightTimeoutError:
            pass


def open_login_modal(page: Page):
    login_field = page.locator("#login_email_username")
    if login_field.is_visible():
        return

    openers = [
        lambda: page.get_by_role("link", name="Mein Konto").click(timeout=5000),
        lambda: page.get_by_role("link", name="Bitte loggen Sie sich hier ein.").click(
            timeout=5000
        ),
        lambda: page.goto(
            "https://www.wg-gesucht.de/?modal=sign_in",
            wait_until="domcontentloaded",
        ),
    ]
    for opener in openers:
        try:
            opener()
            login_field.wait_for(state="visible", timeout=5000)
            return
        except PlaywrightTimeoutError:
            continue

    login_field.wait_for(state="visible", timeout=10000)


def is_logged_in(page: Page) -> bool:
    if page.locator("#message_input").is_visible():
        return True
    if page.get_by_role("link", name="Bitte loggen Sie sich hier ein.").is_visible():
        return False
    login_field = page.locator("#login_email_username")
    if login_field.is_visible():
        return False
    return page.locator("a[href*='abmelden'], a[href*='logout']").count() > 0


def attach_file(page, attachment_path: str, logger) -> bool:
    path = Path(attachment_path)
    if not path.is_file():
        logger.info(f"Attachment file not found: {attachment_path}")
        return False

    page.locator('button[data-target="#attachment_options_modal"]').click(
        timeout=10000
    )
    page.locator("button.attach_file").click(timeout=10000)
    page.locator("#attachments_modal").wait_for(state="visible", timeout=10000)

    with page.expect_file_chooser(timeout=10000) as file_chooser:
        page.locator("#upload").click()
    file_chooser.value.set_files(str(path.resolve()))

    page.locator("#file_attached_success").wait_for(state="visible", timeout=30000)
    page.locator("#attachments_modal button:has-text('Bestätigen')").click(
        timeout=10000
    )
    page.locator("#attachments_modal").wait_for(state="hidden", timeout=10000)
    logger.info(f"Attached file: {attachment_path}")
    return True


class SubmitSession:
    def __init__(self, config, logger):
        self.config = config
        self.logger = logger
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._start_browser()
        self._login()

    def _start_browser(self):
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(
            headless=self.config["run_headless"]
        )
        self._context = self._browser.new_context(
            viewport={"width": 1920, "height": 1080}
            if self.config["run_headless"]
            else None,
        )
        self._page = self._context.new_page()

    def _login(self, page: Page | None = None):
        page = page or self._page
        if is_logged_in(page):
            self.logger.info("Already logged in.")
            return

        dismiss_overlays(page)
        open_login_modal(page)

        login_field = page.locator("#login_email_username")
        login_field.wait_for(state="visible", timeout=10000)
        login_field.fill(self.config["wg_gesucht_credentials"]["email"])
        page.locator("#login_password").fill(
            self.config["wg_gesucht_credentials"]["password"]
        )
        page.locator("#login_submit").click()
        page.wait_for_load_state("domcontentloaded")

        try:
            login_field.wait_for(state="hidden", timeout=15000)
        except PlaywrightTimeoutError:
            if login_field.is_visible():
                raise RuntimeError("Login failed — login form still visible.")

        dismiss_overlays(page)
        self.logger.info("Logged in.")

    def restart(self):
        self.logger.info("Restarting browser session...")
        self.close()
        self._start_browser()
        self._login()

    def close(self):
        if self._browser:
            try:
                self._browser.close()
            except Exception:
                pass
        if self._playwright:
            try:
                self._playwright.stop()
            except Exception:
                pass
        self._browser = None
        self._context = None
        self._page = None
        self._playwright = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()

    def get_listings(self, url: str) -> dict:
        from src.listing_getter import ListingGetter

        return ListingGetter(url, page=self._page).get_all_infos()

    def submit(self, config) -> bool:
        page = self._page
        try:
            page.goto(
                "https://www.wg-gesucht.de/nachricht-senden" + config["ref"],
                wait_until="domcontentloaded",
            )

            dismiss_overlays(page)

            if not is_logged_in(page):
                self.logger.info("Not logged in on message page, logging in...")
                self._login(page)

            if not page.locator("#message_input").is_visible(timeout=3000):
                dismiss_overlays(page)
                open_login_modal(page)
                self._login(page)

            if not page.locator("#message_input").is_visible(timeout=5000):
                self.logger.info("Message form not available — login may have failed.")
                return False

            try:
                page.locator("#sicherheit_bestaetigung").click(timeout=3000)
            except PlaywrightTimeoutError:
                self.logger.info("No security check.")

            if page.locator("#message_timestamp").count() > 0:
                self.logger.info(
                    "Message has already been sent previously. Will skip this offer."
                )
                return "already_sent"

            self.logger.info("No message has been sent. Will send now...")
            self.logger.info(
                f"Sending to: {config['user_name']}, {config['address']}."
            )

            text_area = page.locator("#message_input")
            text_area.wait_for(state="visible", timeout=10000)
            text_area.fill("")

            message = config.get("message")
            if not message:
                message_file = config["message_file"]
                try:
                    from src.message_template import load_template

                    message = load_template(message_file)
                except FileNotFoundError:
                    self.logger.info(f"{message_file} file not found!")
                    return False

            text_area.fill(message)

            attachment_file = config.get("attachment_file", "")
            if attachment_file:
                try:
                    attach_file(page, attachment_file, self.logger)
                except PlaywrightTimeoutError:
                    self.logger.info(
                        "Failed to attach file. Sending message without attachment."
                    )
                except Exception as e:
                    self.logger.info(
                        f"Failed to attach file: {e}. Sending without attachment."
                    )

            submit_button = page.locator(
                "button.conversation_send_button, "
                "button[data-ng-click='submit()'], "
                "button:has-text('Nachricht senden'), "
                "button:has-text('Senden')"
            )
            submit_button.click(timeout=10000)
            self.logger.info(f">>>> Message sent to: {config['ref']} <<<<")
            page.wait_for_timeout(2000)
            return True

        except PlaywrightTimeoutError:
            self.logger.info("Cannot find submit button or page element timed out!")
            return False
        except RuntimeError as e:
            self.logger.info(str(e))
            return False


def submit_app(config, logger):
    try:
        with SubmitSession(config, logger) as session:
            return session.submit(config)
    except Exception as e:
        logger.info(
            "Browser crashed! You might be trying to run it without a screen in terminal?"
        )
        raise e
