import requests
from bs4 import BeautifulSoup


class ListingInfoGetter:
    def __init__(self, ref: str):
        url = "https://www.wg-gesucht.de" + ref
        self.r = requests.get(url, timeout=30).text

    def get_listing_text(self) -> str:
        soup = BeautifulSoup(self.r, "lxml")
        ad_description = soup.find("div", {"id": "ad_description_text"})
        if not ad_description:
            return ""

        chunks = ad_description.find_all(["p", "h3"])
        parts = []
        for chunk in chunks:
            parts.extend([chunk.getText().strip(), "\n\n"])
        return "".join(parts).strip()
