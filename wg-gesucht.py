import argparse
import datetime
import logging
import os
import random
import signal
import time

import yaml
from dotenv import load_dotenv

from src import ListingGetter, SubmitSession
from src.db import ListingStore
from src.listing_info_getter import ListingInfoGetter
from src.message_generator import build_message

DEFAULT_POLL_INTERVAL_SECONDS = 30
DEFAULT_MESSAGE_DELAY_MIN_SECONDS = 45
DEFAULT_MESSAGE_DELAY_MAX_SECONDS = 120
DEFAULT_POLL_INTERVAL_JITTER_SECONDS = 10

logging.basicConfig(
    format="[%(asctime)s | %(levelname)s] - %(message)s ",
    level=logging.INFO,
    datefmt="%Y-%m-%d_%H:%M:%S",
    handlers=[logging.FileHandler("debug.log"), logging.StreamHandler()],
)
logger = logging.getLogger("bot")

_shutdown_requested = False


def _request_shutdown(signum, _frame):
    global _shutdown_requested
    signal_name = signal.Signals(signum).name
    logger.info(f"Received {signal_name}, shutting down after current cycle...")
    _shutdown_requested = True


def parse_args():
    parser = argparse.ArgumentParser(description="WG-Gesucht application bot")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch listings and print matches without sending messages or updating the database",
    )
    return parser.parse_args()


def load_config(*, require_credentials: bool = True):
    load_dotenv()

    if require_credentials:
        required_env = ("WG_GESUCHT_EMAIL", "WG_GESUCHT_PASSWORD")
        missing = [name for name in required_env if not os.environ.get(name)]
        if missing:
            raise RuntimeError(
                f"Missing required environment variables: {', '.join(missing)}"
            )

    with open("config.yaml", "r") as stream:
        config = yaml.safe_load(stream)

    if require_credentials:
        config["wg_gesucht_credentials"] = {
            "email": os.environ["WG_GESUCHT_EMAIL"],
            "password": os.environ["WG_GESUCHT_PASSWORD"],
        }

    config["ai"] = {
        "api_key": os.environ.get("DEEPSEEK_API_KEY", ""),
        "model": os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
        "base_url": os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
    }
    return config


def sleep_interruptible(seconds: float):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if _shutdown_requested:
            return
        time.sleep(min(1, end - time.monotonic()))


def message_delay(config):
    min_seconds = config.get(
        "message_delay_min_seconds", DEFAULT_MESSAGE_DELAY_MIN_SECONDS
    )
    max_seconds = config.get(
        "message_delay_max_seconds", DEFAULT_MESSAGE_DELAY_MAX_SECONDS
    )
    delay = random.uniform(min_seconds, max_seconds)
    logger.info(f"Waiting {delay:.0f}s before next message.")
    sleep_interruptible(delay)


def poll_delay(config):
    base = config.get("poll_interval_seconds", DEFAULT_POLL_INTERVAL_SECONDS)
    jitter = config.get(
        "poll_interval_jitter_seconds", DEFAULT_POLL_INTERVAL_JITTER_SECONDS
    )
    delay = base + random.uniform(0, jitter)
    logger.info(f"Sleeping {delay:.0f}s until next poll.")
    sleep_interruptible(delay)


def get_listings_with_retry(session, url):
    try:
        return session.get_listings(url)
    except Exception:
        logger.exception("Listing fetch failed, restarting browser session...")
        session.restart()
        return session.get_listings(url)


def submit_with_retry(session, config):
    try:
        return session.submit(config)
    except Exception:
        logger.exception("Submit failed, restarting browser session...")
        session.restart()
        return session.submit(config)


def is_within_rental_start(listing, rental_start_config) -> bool:
    desired_start = datetime.datetime(
        rental_start_config["year"],
        rental_start_config["month"],
        rental_start_config["day"],
    )
    buffer = datetime.timedelta(days=rental_start_config["buffer_days"])
    earliest = desired_start - buffer
    latest = desired_start + buffer
    listing_start = listing["rental_start"]
    return earliest <= listing_start <= latest


def skip_reason(listing, config, contacted_pairs: set[tuple[str, str]] | None = None) -> str | None:
    rental_start_config = config["rental_start"]
    if not is_within_rental_start(listing, rental_start_config):
        desired = datetime.datetime(
            rental_start_config["year"],
            rental_start_config["month"],
            rental_start_config["day"],
        )
        buffer = datetime.timedelta(days=rental_start_config["buffer_days"])
        return (
            f"Mietbeginn {listing['rental_start'].date()} liegt außerhalb "
            f"{(desired - buffer).date()} – {(desired + buffer).date()}"
        )

    if contacted_pairs is not None and (
        listing["user_name"],
        listing["address"],
    ) in contacted_pairs:
        return "bereits angeschrieben (gleicher Name + Adresse)"

    return None


def skip_reason_for_store(listing, config, store: ListingStore) -> str | None:
    reason = skip_reason(listing, config)
    if reason is not None:
        return reason
    if store.is_contacted(listing["user_name"], listing["address"]):
        return "bereits angeschrieben (gleicher Name + Adresse)"
    return None


def format_listing(listing) -> str:
    return (
        f"{listing['user_name']} | {listing['address']} | "
        f"frei ab {listing['rental_start'].date()} | {listing['ref']}"
    )


def dry_run(config):
    logger.info("Dry run — keine Nachrichten, keine DB-Änderungen.")

    contacted_pairs = ListingStore.read_contacted_pairs(
        config.get("db_path", "bot.db")
    )
    current_listings = ListingGetter(config["url"]).get_all_infos()

    logger.info(f"{len(current_listings)} Inserate auf der Suchseite gefunden.")

    would_contact = []
    for listing in current_listings.values():
        reason = skip_reason(listing, config, contacted_pairs)
        if reason:
            logger.info(f"[SKIP] {format_listing(listing)} — {reason}")
        else:
            would_contact.append(listing)
            logger.info(f"[WÜRDE ANSCHREIBEN] {format_listing(listing)}")
            listing_text = ListingInfoGetter(listing["ref"]).get_listing_text()
            message = build_message(config, listing, listing_text, logger)
            logger.info(
                f"[NACHRICHT für {listing['user_name']}]\n"
                f"{'-' * 60}\n"
                f"{message}\n"
                f"{'-' * 60}"
            )

    logger.info(
        f"Zusammenfassung: {len(would_contact)}/{len(current_listings)} Inserate würden angeschrieben."
    )


def run_once(config, store, session):
    url = config["url"]
    current_listings = get_listings_with_retry(session, url)

    new_listings = store.get_unseen_listings(current_listings)
    uncontacted_listings = store.get_uncontacted_listings(current_listings)

    if new_listings:
        logger.info(f"Found {len(new_listings)} new listings.")
    elif uncontacted_listings:
        logger.info(
            f"Retrying {len(uncontacted_listings)} uncontacted listing(s)."
        )
    else:
        logger.info("No new offers.")

    if uncontacted_listings:
        messages_sent_this_cycle = 0
        for listing in uncontacted_listings:
            ref = listing["ref"]

            config["ref"] = ref
            config["user_name"] = listing["user_name"]
            config["address"] = listing["address"]
            logger.info(f"Trying to send message to: {listing}")

            reason = skip_reason_for_store(listing, config, store)
            if reason:
                logger.info(f"Skipping ... {reason}")
                continue

            if messages_sent_this_cycle > 0:
                message_delay(config)

            listing_text = ListingInfoGetter(ref).get_listing_text()
            config["message"] = build_message(config, listing, listing_text, logger)

            submit_result = submit_with_retry(session, config)
            if submit_result in (True, "already_sent"):
                store.mark_contacted(listing["user_name"], listing["address"], ref)
                messages_sent_this_cycle += 1

    store.mark_seen(current_listings)


def main(config):
    store = ListingStore(config.get("db_path", "bot.db"))

    signal.signal(signal.SIGINT, _request_shutdown)
    signal.signal(signal.SIGTERM, _request_shutdown)

    poll_interval = config.get("poll_interval_seconds", DEFAULT_POLL_INTERVAL_SECONDS)
    logger.info(
        f"Bot started. Polling every ~{poll_interval}s "
        f"(+0–{config.get('poll_interval_jitter_seconds', DEFAULT_POLL_INTERVAL_JITTER_SECONDS)}s jitter)."
    )

    with SubmitSession(config, logger) as session:
        while not _shutdown_requested:
            try:
                run_once(config, store, session)
            except Exception:
                logger.exception("Error during poll cycle.")
                try:
                    session.restart()
                except Exception:
                    logger.exception("Failed to restart browser session.")

            if _shutdown_requested:
                break

            poll_delay(config)

    logger.info("Bot stopped.")


if __name__ == "__main__":
    args = parse_args()
    config = load_config(require_credentials=not args.dry_run)
    if args.dry_run:
        dry_run(config)
    else:
        main(config)
