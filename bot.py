import time
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

BASE = "https://www.nogizaka46.com"
HOME = "https://www.nogizaka46.com/s/n46/?ima=0"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (NogizakaDiscordBot; +https://github.com/akoustamikuzak/nogizakanews)"
}

def fetch_official_news_urls(limit: int = 8):
    """
    公式トップの News セクションから /s/n46/news/detail/XXXX を拾う
    """
    r = requests.get(HOME, headers=HEADERS, timeout=15)
    r.raise_for_status()
    html = r.text

    soup = BeautifulSoup(html, "lxml")

    urls = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/s/n46/news/detail/" in href:
            full = urljoin(BASE, href)
            if full not in urls:
                urls.append(full)
        if len(urls) >= limit:
            break

    return urls

def fetch_official_news_detail(url: str):
    """
    ニュース詳細ページから title/date/body をざっくり抽出（多少HTMLが変わっても耐える）
    """
    r = requests.get(url, headers=HEADERS, timeout=15)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "lxml")

    # タイトル（ページ内のh1が安定しやすい）
    h1 = soup.find("h1")
    title = h1.get_text(strip=True) if h1 else ""

    # 日付（ページ中に YYYY.MM.DD が出るので正規表現で拾う）
    import re
    m = re.search(r"\b(20\d{2}\.\d{2}\.\d{2})\b", soup.get_text(" ", strip=True))
    date = m.group(1) if m else ""

    # 本文：まずは「本文っぽい」長めテキストを拾う（厳密セレクタに依存しない）
    # ※必要ならここはCSSセレクタで精密化できる
    texts = [p.get_text(" ", strip=True) for p in soup.find_all(["p", "div"])]

    # タイトル/フッタ等を避けつつ、長めの塊を本文候補に
    body_candidates = [t for t in texts if len(t) >= 40 and "LATEST NEWS" not in t]
    body = max(body_candidates, key=len) if body_candidates else ""

    return {"source": "official", "title": title, "date": date, "url": url, "body": body}

def fetch_official_news(limit: int = 5, sleep_sec: float = 0.6):
    urls = fetch_official_news_urls(limit=limit)
    items = []
    for u in urls:
        try:
            items.append(fetch_official_news_detail(u))
        except Exception:
            # 1件落ちても全体は返す
            continue
        time.sleep(sleep_sec)  # 負荷をかけない
    return items
