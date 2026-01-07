import os
import discord
from discord import app_commands
from dotenv import load_dotenv
from openai import OpenAI
import feedparser

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

client = discord.Client(intents=discord.Intents.default())
tree = app_commands.CommandTree(client)
ai = OpenAI(api_key=OPENAI_API_KEY)


# ----------- 乃木坂ニュース取得(RSS) -----------
def fetch_nogi_news():
    RSS_URL = "https://nogizaka46.com/s/n46/?ima=0000&ct=news"  # 公式RSSではないが検知用URL
    FEED_URL = "https://news.yahoo.co.jp/rss/media/nogizakarf/all.xml"

    feed = feedparser.parse(FEED_URL)

    articles = []
    for entry in feed.entries[:5]:
        articles.append({
            "title": entry.title,
            "snippet": entry.summary,
            "url": entry.link
        })

    return articles


# ----------- 要約機能（GPT-4.1） -----------
def summarize_articles(articles):
    text = ""
    for a in articles:
        text += f"■ {a['title']}\n{a['snippet']}\nURL: {a['url']}\n\n"

    prompt = f"""
以下の情報は乃木坂46に関する最新ニュースです。
ファン向けに、わかりやすく・読みやすく・丁寧に要点を3〜5個にまとめてください。

{text}
"""

    completion = ai.chat.completions.create(
        model="gpt-4.1",
        messages=[{"role": "user", "content": prompt}],
    )

    return completion.choices[0].message.content


# ----------- /nogi_news コマンド -----------
@tree.command(name="nogi_news", description="最新の乃木坂ニュースを取得します")
async def nogi_news(interaction: discord.Interaction):
    await interaction.response.defer()

    articles = fetch_nogi_news()
    summary = summarize_articles(articles)

    await interaction.followup.send(summary)


# ----------- /chat コマンド（ChatGPTと会話） -----------
@tree.command(name="chat", description="AIと会話します")
async def chat(interaction: discord.Interaction, message: str):
    await interaction.response.defer()

    try:
        completion = ai.chat.completions.create(
            model="gpt-4.1",
            messages=[
                {"role": "system", "content": "あなたは優しいアシスタントです。"},
                {"role": "user", "content": message}
            ],
        )
        reply = completion.choices[0].message.content

    except Exception as e:
        reply = f"OpenAI API エラー: {e}"

    await interaction.followup.send(reply)


# ----------- 起動処理 -----------
@client.event
async def on_ready():
    await tree.sync()
    print("Nogizaka News Bot（GPT-4.1）起動完了")


client.run(DISCORD_TOKEN)
