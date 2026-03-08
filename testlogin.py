import argparse
import logging
import time
from atproto import Client

# --- Logging ---
LOG_PATH = "rss2bsky_test.log"
logging.basicConfig(
    format="%(asctime)s %(message)s",
    filename=LOG_PATH,
    encoding="utf-8",
    level=logging.INFO,
)

def main():
    # --- Parse command-line arguments ---
    parser = argparse.ArgumentParser(description="Post RSS to Bluesky.")
    parser.add_argument("rss_feed", help="RSS feed URL")
    parser.add_argument("bsky_handle", help="Bluesky handle")
    parser.add_argument("bsky_username", help="Bluesky username")
    parser.add_argument("bsky_app_password", help="Bluesky app password")
    parser.add_argument("--service", default="https://bsky.social", help="Bluesky server URL (default: https://bsky.social)")
    
    args = parser.parse_args()
    bsky_username = args.bsky_username
    bsky_password = args.bsky_app_password
    service_url = args.service

    # --- Login ---
    # SOLUCIÓ: Passem el base_url directament al constructor del Client
    client = Client(base_url=service_url)
    
    backoff = 60
    while True:
        try:
            logging.info(f"Attempting login to server: {service_url} with user: {bsky_username}")
            client.login(bsky_username, bsky_password)
            logging.info(f"Login successful for user: {bsky_username}")
            break
        except Exception as e:
            logging.exception("Login exception")
            time.sleep(backoff)
            backoff = min(backoff + 60, 600)

if __name__ == "__main__":
    main()
