"""Download data from the BGG XML API v2."""

import datetime
import http
import logging
import pathlib
import time
import xml.etree.ElementTree as ET

import requests

logging.basicConfig(format="%(levelname)s: %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)


def get_data(
    query_path: str,
    session: requests.Session,
    timeout: int = 3,
    retries: int = 10,
    delay: int = 5,
) -> bytes:
    """Fetch data from the BGG XML API v2.

    > BGG throttles the requests now, which is to say that if you send requests too
    > frequently, the server will give you 500 or 503 return codes, reporting that it is
    > too busy. Currently, a 5-second delay between requests seems to suffice.
    >
    > -- https://boardgamegeek.com/wiki/page/BGG_XML_API (2015-07-21)

    The BGG XML API v2 returns a 429 Too Many Requests response when the rate limit is
    exceeded.

    Experience shows that using a short, fixed delay of 5 seconds is sufficient to
    prevent excessive throttling, while also avoiding the prolonged download times
    that can result from exponential backoff strategies.
    """
    url = "https://boardgamegeek.com/xmlapi2" + query_path

    response = requests.Response()

    attempts = 0
    while attempts <= retries:
        try:
            response = session.get(url, timeout=timeout)
        except requests.exceptions.ReadTimeout:
            attempts += 1
            logger.warning("Read timeout, retrying in %d seconds...", delay)
            time.sleep(delay)
            continue

        if response.status_code == http.HTTPStatus.OK:
            break

        if response.status_code == http.HTTPStatus.ACCEPTED:
            attempts += 1
            logger.info("Request queued, retrying in %d seconds...", delay)
            time.sleep(delay)
            continue

        if response.status_code == http.HTTPStatus.TOO_MANY_REQUESTS:
            attempts += 1

            retry_after = int(response.headers.get("Retry-After", delay))
            logger.warning(
                "Too many requests, retrying in %d seconds...",
                retry_after,
            )
            time.sleep(retry_after)
            continue

        if response.status_code != http.HTTPStatus.OK:
            raise requests.exceptions.HTTPError(response.status_code)

    logger.info("Retrieved %d bytes from %s", len(response.content), url)

    return response.content


def inspect_data(data: bytes, root_tag: str) -> ET.Element:
    """Inspect data fetched from the BGG XML API v2.

    Checks that the root tag matches the expected value.
    """
    root = ET.fromstring(data)

    if root.tag != root_tag:
        msg = f"Unexpected root tag: {root.tag}, expected: {root_tag}"
        raise ValueError(msg)

    return root


def save_data(data: bytes, file_path: pathlib.Path) -> None:
    """Save data fetched from the BGG XML API v2.

    Writes the data to the specified file path. Will create the parent directories if
    they do not exist and will overwrite the file if it already exists.
    """
    file_path.parent.mkdir(parents=True, exist_ok=True)

    with file_path.open("wb") as file:
        _ = file.write(data)


def download_user_data(
    username: str, session: requests.Session, download_dir: pathlib.Path
) -> None:
    """Download user data from the BGG XML API v2.

    Downloads the user profile, including buddies and guilds, and saves it as a XML file
    in the download directory.
    """
    logger.info("Downloading user profile...")
    query_path = f"/user?name={username}&buddies=1&guilds=1"
    root_tag = "user"
    file_path = download_dir / "user" / "profile.xml"
    logger.debug("User: %s", username)
    data = get_data(query_path, session)
    _ = inspect_data(data, root_tag)
    save_data(data, file_path)
    logger.info("Saved user profile to %s", file_path)


def download_collection_data(
    username: str, session: requests.Session, download_dir: pathlib.Path
) -> list[int]:
    """Download collection data from the BGG XML API v2.

    Downloads the user's collection, including board games, expansions, and accessories,
    and saves each subtype as a separate XML file in the download directory.

    Returns a list of BGG IDs for all items in the collection.

    > Note that the default (or using subtype=boardgame) returns both boardgame and
    > boardgameexpansion's in your collection... but incorrectly gives subtype=boardgame
    > for the expansions. Workaround is to use excludesubtype=boardgameexpansion and
    > make a 2nd call asking for subtype=boardgameexpansion.
    >
    > -- https://boardgamegeek.com/wiki/page/BGG_XML_API2 (2024-10-07)

    > I have updated the exporting of collections function to take it "out of band".
    > What this means is that when you make a request to export your collection in CSV
    > or using the XML API it will get queued on our backend servers.
    >
    > Those servers will then generate the file and save it, so that you can download it
    > over and over without having to wait for the generation time again (which can be
    > quite long for large collections).
    >
    > This also means that API developers need to watch for the HTTP 202 response code
    > when they request a collection that gets queued for generation.
    >
    > Also, since a lot of the data in a collection for the API is dynamic (based on
    > stats, rankings, etc...) we will hard expire them after 7 days. If you make
    > changes to your collection - the data will be expired and you'll have to wait for
    > the queue to export the collection again.
    >
    > ...pubdate is when the collection data was exported.
    >
    > -- https://boardgamegeek.com/thread/1188687/ (2014-06-16)
    """
    logger.info("Downloading collection...")

    item_ids: list[int] = []
    root_tag = "items"

    subtypes = ["boardgame", "boardgameexpansion", "boardgameaccessory"]
    for subtype in subtypes:
        collection_query_path = (
            f"/collection?username={username}&subtype={subtype}&stats=1&version=1"
        )

        # Exclude expansions from board game request.
        if subtype == "boardgame":
            collection_query_path += "&excludesubtype=boardgameexpansion"

        collection_file_path = download_dir / "collection" / f"{subtype}.xml"
        logger.debug("Subtype: %s", subtype)
        data = get_data(collection_query_path, session)
        root = inspect_data(data, root_tag)
        save_data(data, collection_file_path)
        logger.info("Saved %s collection to %s", subtype, collection_file_path)

        for item in root.findall(".//item[@objectid]"):
            item_id = item.get("objectid")
            if item_id is None:
                msg = "None value found in objectid"
                raise ValueError(msg)
            item_ids.append(int(item_id))

    return item_ids


def download_thing_data(
    thing_ids: list[int],
    session: requests.Session,
    download_dir: pathlib.Path,
    batch_size: int = 20,
) -> None:
    """Download thing data from the BGG XML API v2.

    Downloads thing information in batches and saves each batch as a separate XML file
    in the download directory.

    > As of July 2024, the API is limiting multiple game requests to a maximum of 20
    > game IDs per call.
    >
    > — https://boardgamegeek.com/wiki/page/BGG_XML_API (2024-07-15)
    """
    logger.info("Downloading things...")

    root_tag = "items"
    num_things = len(thing_ids)
    num_batches = (len(thing_ids) + batch_size - 1) // batch_size
    logger.debug(
        "Divided %d things into %d batches of %d", num_things, num_batches, batch_size
    )

    for i in range(0, num_things, batch_size):
        batch_num = i // batch_size + 1
        batch_ids = thing_ids[i : i + batch_size]
        batch_csv = ",".join(map(str, batch_ids))
        thing_query_path = f"/thing?id={batch_csv}&stats=1"
        logger.debug("Batch: %d of %d", batch_num, num_batches)
        thing_file_path = download_dir / "thing" / f"batch_{batch_num}.xml"
        data = get_data(thing_query_path, session)
        _ = inspect_data(data, root_tag)
        save_data(data, thing_file_path)
        logger.info("Saved batch %d of things to %s", batch_num, thing_file_path)


def download_play_data(
    username: str, session: requests.Session, download_dir: pathlib.Path
) -> None:
    """Download play data from the BGG XML API v2.

    Downloads all plays logged by a user, handling pagination, and saves each page as a
    separate XML file in the download directory.

    Each page contains up to a maximum of 100 plays. Pagination is handled by
    incrementing the `page` parameter, starting from 1. Downloading stops when a page
    returns no plays, indicating the end of the user's logged plays.
    """
    logger.info("Downloading plays...")

    root_tag = "plays"
    page = 1

    while True:
        query_path = f"/plays?username={username}&page={page}"
        file_path = download_dir / "plays" / f"page_{page}.xml"

        logger.debug("Page: %d", page)
        data = get_data(query_path, session)
        root = inspect_data(data, root_tag)

        if not root.findall("play"):
            logger.debug("No plays on page %d, end of logged plays", page)
            break

        save_data(data, file_path)
        logger.info("Saved page %d of plays to %s", page, file_path)

        page += 1


def write_timestamp_file(download_dir: pathlib.Path) -> None:
    """Write a plain text timestamp file indicating the last download time.

    If the file already exists, it will be overwritten.

    The timestamp is written in ISO 8601 format (e.g., 2000-01-01T01:02:03.456789Z) to a
    plain text file inside the specified download directory. The timestamp file can be
    used to check when the last download was performed.
    """
    logger.info("Adding timestamp...")
    ts_file = download_dir / "timestamp.txt"
    ts_file.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now(datetime.UTC).isoformat().replace("+00:00", "Z")
    _ = ts_file.write_text(ts + "\n", encoding="utf-8")
    logger.info("Saved timestamp to %s", ts_file)


def main() -> None:
    """Download my data from the BGG XML API v2 and save the raw API responses to disk.

    Orchestrates the complete download workflow:
        1. Downloads my BGG user profile.
        2. Downloads my BGG collection.
        3. Downloads the BGG thing for each item in my collection.
        4. Downloads all the plays I've logged on BGG.
        5. Records the time when the download finished.
    """
    username = "les_"
    download_dir = pathlib.Path("data")

    session = requests.Session()
    with session:
        download_user_data(username, session, download_dir)
        item_ids = download_collection_data(username, session, download_dir)
        download_thing_data(item_ids, session, download_dir)
        download_play_data(username, session, download_dir)

    write_timestamp_file(download_dir)
    logger.info("Download complete!")


if __name__ == "__main__":
    main()
