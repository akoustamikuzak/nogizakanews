import os
import time
import requests
import discord
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from dotenv import load_dotenv

# =====================
# 環境変数
# =====================
load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

# =====================
# Discord 設定
# =====================
intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

# =====================
# 乃木坂公式ニュース取得
# =====================
BASE = "https://www.nogizaka46.com"
HOME = "https://www.nogizaka46.com/s/n46/?ima=0"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (NogizakaDiscordBot; +https://github.com/akoustamikuzak/nogizakanews)"
}

def fetch_official_news(limit=3):
    r = requests.get(HOME, headers=HEADERS, timeout=15)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "lxml")

    urls = []
    for a in soup.find_all("a", href=True):
        if "/s/n46/news/detail/" in a["href"]:
            full = urljoin(BASE, a["href"])
            if full not in urls:
                urls.append(full)
        if len(urls) >= limit:
            break

    news = []
    for url in urls:
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            soup = BeautifulSoup(r.text, "lxml")
            title = soup.find("h1").get_text(strip=True)
            news.append(f"■ {title}\n{url}")
            time.sleep(0.5)
        except Exception:
            continue

    return news

# =====================
# Discord イベント
# =====================
@client.event
async def on_ready():
    print(f"Logged in as {client.user}")

@client.event
async def on_message(message):
    if message.author.bot:
        return

    if message.content == "!nogi":
        news = fetch_official_news()
        await message.channel.send("\n\n".join(news))

# =====================
# 起動
# =====================
if __name__ == "__main__":
    print("Starting Discord bot...")
    client.run(DISCORD_TOKEN)
