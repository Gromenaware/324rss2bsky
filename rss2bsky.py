import argparse
import arrow
import fastfeedparser
import logging
import re
import httpx
import time
import charset_normalizer  # Per detectar la codificació del feed
from atproto import Client, client_utils, models
from bs4 import BeautifulSoup
import html  # Import the html library for unescaping HTML entities

# --- Logging ---
LOG_PATH = "rss2bsky_test.log"  # Fitxer de log per a tests
logging.basicConfig(
    format="%(asctime)s %(message)s",
    filename=LOG_PATH,
    encoding="utf-8",
    level=logging.INFO,  # Nivell DEBUG per veure més detalls durant el test
)

# --- Funció per corregir problemes de codificació ---
def fix_encoding(text):
    try:
        # Intenta decodificar i reencodificar a UTF-8
        return text.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        logging.warning(f"Error corregint codificació: {text}")
        return text  # Retorna el text original si hi ha un error

# --- Funció per desescapar caràcters unicode ---
def desescapar_unicode(text):
    try:
        return html.unescape(text)  # Utilitza html.unescape per gestionar HTML entities
    except Exception as e:
        logging.warning(f"Error desescapant unicode: {e}")
        return text  # Retorna el text original si hi ha un error

# --- Funció per processar el títol ---
def process_title(title):
    try:
        if is_html(title):
            title_text = BeautifulSoup(title, "html.parser", from_encoding="utf-8").get_text().strip()
        else:
            title_text = title.strip()
        title_text = desescapar_unicode(title_text)  # Desescapar HTML entities
        title_text = fix_encoding(title_text)  # Corregir problemes de codificació
        return title_text
    except Exception as e:
        logging.warning(f"Error processant el títol: {e}")
        return title

def fetch_link_metadata(url):
    try:
        r = httpx.get(url, timeout=10)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        title = (soup.find("meta", property="og:title") or soup.find("title"))
        desc = (soup.find("meta", property="og:description") or soup.find("meta", attrs={"name": "description"}))
        image = (soup.find("meta", property="og:image") or soup.find("meta", attrs={"name": "twitter:image"}))
        return {
            "title": title["content"] if title and title.has_attr("content") else (title.text if title else ""),
            "description": desc["content"] if desc and desc.has_attr("content") else "",
            "image": image["content"] if image and image.has_attr("content") else None,
        }
    except Exception as e:
        logging.warning(f"Could not fetch link metadata for {url}: {e}")
        return {}

def get_last_bsky(client, handle):
    timeline = client.get_author_feed(handle)
    for titem in timeline.feed:
        # Only care about top-level, non-reply posts
        if titem.reason is None and getattr(titem.post.record, "reply", None) is None:
            logging.info("Record created %s", str(titem.post.record.created_at))
            return arrow.get(titem.post.record.created_at)
    return arrow.get(0)

def make_rich(content):
    text_builder = client_utils.TextBuilder()
    lines = content.split("\n")
    for line in lines:
        # If the line is a URL, make it a clickable link
        if line.startswith("http"):
            url = line.strip()
            text_builder.link(url, url)
        else:
            tag_split = re.split("(#[a-zA-Z0-9]+)", line)
            for i, t in enumerate(tag_split):
                if i == len(tag_split) - 1:
                    t = t + "\n"
                if t.startswith("#"):
                    text_builder.tag(t, t[1:].strip())
                else:
                    text_builder.text(t)
    return text_builder

def get_image_from_url(image_url, client, alt_text="Preview image"):
    try:
        r = httpx.get(image_url)
        if r.status_code != 200:
            return None
        img_blob = client.upload_blob(r.content)
        img_model = models.AppBskyEmbedImages.Image(
            alt=alt_text, image=img_blob.blob
        )
        return img_model
    except Exception as e:
        logging.warning(f"Could not fetch/upload image from {image_url}: {e}")
        return None

def is_html(text):
    return bool(re.search(r'<.*?>', text))

def main():
    # --- Parse command-line arguments ---
    parser = argparse.ArgumentParser(description="Post RSS to Bluesky.")
    parser.add_argument("rss_feed", help="RSS feed URL")
    parser.add_argument("bsky_handle", help="Bluesky handle")
    parser.add_argument("bsky_username", help="Bluesky username")
    parser.add_argument("bsky_app_password", help="Bluesky app password")
    args = parser.parse_args()
    feed_url = args.rss_feed
    bsky_handle = args.bsky_handle
    bsky_username = args.bsky_username
    bsky_password = args.bsky_app_password

    # --- Login ---
    client = Client()
    backoff = 60
    while True:
        try:
            client.login(bsky_username, bsky_password)
            break
        except Exception as e:
            logging.exception("Login exception")
            time.sleep(backoff)
            backoff = min(backoff + 60, 600)

    # --- Get last Bluesky post time ---
    last_bsky = get_last_bsky(client, bsky_handle)

    # --- Parse feed ---
    response = httpx.get(feed_url)
    response.raise_for_status()  # Comprova que la resposta sigui correcta

    try:
        # Detecta automàticament la codificació i converteix a UTF-8
        result = charset_normalizer.from_bytes(response.content).best()
        if not result or not hasattr(result, "text"):
            raise ValueError("No s'ha pogut detectar la codificació del feed o el text no és accessible.")
        feed_content = result.text  # Contingut decodificat com UTF-8
    except ValueError:
        logging.warning("No s'ha pogut detectar la codificació amb charset_normalizer. Provant amb latin-1.")
        try:
            feed_content = response.content.decode("latin-1")
        except UnicodeDecodeError:
            logging.warning("No s'ha pogut decodificar amb latin-1. Provant amb utf-8 amb errors ignorats.")
            feed_content = response.content.decode("utf-8", errors="ignore")

    feed = fastfeedparser.parse(feed_content)  # Passa el contingut decodificat al parser

    for item in feed.entries:
        rss_time = arrow.get(item.published)
        logging.info("RSS Time: %s", str(rss_time))
        # Processar el títol per evitar problemes de codificació
        title_text = process_title(item.title)

        post_text = f"{title_text}\n{item.link}"
        logging.info("Title+link used as content: %s", post_text)
        rich_text = make_rich(post_text)
        logging.info("Rich text length: %d" % (len(rich_text.build_text())))
        logging.info("Filtered Content length: %d" % (len(post_text)))
        #if True:
        #    logging.info("Always posting: %s" % (item.link))
        if rss_time > last_bsky:  # Només publicar si és més nou que l'últim post
            link_metadata = fetch_link_metadata(item.link)
            images = []

            # Try to fetch image from snippet (Open Graph/Twitter Card)
            if link_metadata.get("image"):
                # Prefer the RSS title, fall back to the link_metadata's title
                alt_text = title_text or link_metadata.get("title") or "Preview image"
                img = get_image_from_url(link_metadata["image"], client, alt_text=alt_text)
                if img:
                    images.append(img)

            logging.info("Images length: %d" % (len(images)))

            # --- Add external embed for link preview ---
            external_embed = None
            if link_metadata.get("title") or link_metadata.get("description"):
                external_embed = models.AppBskyEmbedExternal.Main(
                    external=models.AppBskyEmbedExternal.External(
                        uri=item.link,
                        title=link_metadata.get("title") or "Link",
                        description=link_metadata.get("description") or "",
                        thumb=None,
                    )
                )

            # Compose embed (images or link preview)
            embed = None
            if images:
                embed = models.AppBskyEmbedImages.Main(images=images)
            elif external_embed:
                embed = external_embed

            # TEST MODE: No enviar el post, només registrar l'acció
            try:
                logging.info("Test mode: Preparing to send post %s" % (item.link))
                client.send_post(rich_text, embed=embed)  # DESACTIVAT PER TEST
                logging.info("Test mode: Post prepared %s" % (item.link))
            except Exception as e:
                logging.exception("Failed to prepare post %s" % (item.link))
        else:
            logging.debug("Not sending %s" % (item.link))

if __name__ == "__main__":
    main()