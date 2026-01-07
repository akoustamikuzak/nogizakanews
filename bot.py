import os
import re
import feedparser
import discord
import threading
import os
from http.server import HTTPServer, BaseHTTPRequestHandler
from dotenv import load_dotenv
from openai import OpenAI
from datetime import datetime, timezone, timedelta

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

ai = OpenAI(api_key=OPENAI_API_KEY)

# チャンネル単位の簡易履歴
conversation = {}  # {channel_id: [ {role, content}, ... ]}
MAX_TURNS = 12

JST = timezone(timedelta(hours=9))

def today_jst_str():
    return datetime.now(JST).strftime("%Y-%m-%d")

def fetch_google_news_rss(query: str, n: int = 5):
    # Google News RSS（APIキー不要）
    rss_url = f"https://news.google.com/rss/search?q={query}&hl=ja&gl=JP&ceid=JP:ja"
    feed = feedparser.parse(rss_url)
    items = []
    for entry in feed.entries[:n]:
        items.append({
            "title": entry.get("title", ""),
            "url": entry.get("link", ""),
            "source": entry.get("source", {}).get("title", "") if isinstance(entry.get("source"), dict) else ""
        })
    return items

def looks_like_news_request(text: str) -> bool:
    # 「ニュース」「今日」「最新」などが入ってたらニュース要求とみなす（雑でOK）
    keywords = ["ニュース", "最新", "今日", "速報", "話題", "トピック", "まとめ"]
    return any(k in text for k in keywords)

def format_news_list(items):
    if not items:
        return "（ニュースが取得できませんでした）"
    lines = []
    for i, it in enumerate(items, 1):
        title = it["title"].strip()
        url = it["url"].strip()
        lines.append(f"{i}. {title}\n{url}")
    return "\n\n".join(lines)

def system_prompt():
    return (
        f"今日は{today_jst_str()}（日本時間）です。"
        "あなたはDiscord上でユーザーと自然に会話するアシスタントです。"
        "日本語で会話的に返してください。"
        "最新情報が必要な話題（今日のニュース等）は、ユーザーが提示したニュース一覧（タイトル+URL）を根拠に答えてください。"
        "ニュース本文が無い場合は、タイトルとURLから“断定せず”に要点を整理してください。"
        "不確かなことは不確かだと短く明示してください。"
    )

@client.event
async def on_ready():
    print(f"Logged in as {client.user} (ready)")

@client.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    is_dm = isinstance(message.channel, discord.DMChannel)
    is_mentioned = client.user in message.mentions if client.user else False
    if not (is_dm or is_mentioned):
        return

    user_text = message.content
    if is_mentioned and client.user:
        user_text = user_text.replace(f"<@{client.user.id}>", "").strip()
        user_text = user_text.replace(f"<@!{client.user.id}>", "").strip()

    if not user_text:
        await message.reply("何について話す？（例：@乃木坂BOT 今日のニュース / 推しメン相談）")
        return

    async with message.channel.typing():
        cid = message.channel.id
        history = conversation.get(cid, [])
        history = history[-MAX_TURNS:]

        # ★ ニュース要求ならRSSを取得して“材料”を追加
        if looks_like_news_request(user_text):
            # 乃木坂に寄せる（ユーザーが別キーワードを入れてたらそれを優先）
            q = user_text.strip()
            if len(q) < 2 or q in ["ニュース", "最新ニュース", "今日のニュース"]:
                q = "乃木坂46 今日"

            news = fetch_google_news_rss(q, n=6)
            news_block = "【取得したニュース一覧（タイトル+URL）】\n" + format_news_list(news)

            # ユーザーの質問＋ニュース材料をセットで投げる
            combined = (
                f"ユーザーの要望: {user_text}\n\n"
                f"{news_block}\n\n"
                "このニュース一覧を根拠に、会話として自然に答えて。"
            )
            history.append({"role": "user", "content": combined})
        else:
            history.append({"role": "user", "content": user_text})

        history = history[-MAX_TURNS:]

        try:
            completion = ai.chat.completions.create(
                model="gpt-4.1",
                messages=[{"role": "system", "content": system_prompt()}] + history,
            )
            reply = completion.choices[0].message.content.strip()
        except Exception as e:
            await message.reply(f"OpenAI API エラー: {e}")
            return

        history.append({"role": "assistant", "content": reply})
        conversation[cid] = history[-MAX_TURNS:]

    await message.reply(reply)

class DummyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

def run_dummy_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), DummyHandler)
    server.serve_forever()

threading.Thread(target=run_dummy_server, daemon=True).start()

client.run(DISCORD_TOKEN)