import os
import discord
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# メッセージ内容を読むために intent を有効化
intents = discord.Intents.default()
intents.message_content = True

client = discord.Client(intents=intents)
ai = OpenAI(api_key=OPENAI_API_KEY)

# チャンネル単位の簡易会話履歴
conversation = {}  # {channel_id: [ {role, content}, ... ]}

SYSTEM_PROMPT = (
    "あなたはDiscord上でユーザーと自然に会話するアシスタントです。"
    "日本語で、会話的に返してください。"
    "乃木坂46の話題にも強いですが、一般の質問にも答えます。"
)

MAX_TURNS = 12  # 直近の履歴だけ保持（コスト抑制）

@client.event
async def on_ready():
    print(f"Logged in as {client.user} (ready)")

@client.event
async def on_message(message: discord.Message):
    # Bot自身の発言は無視
    if message.author.bot:
        return

    # DM か、Botへのメンションのときだけ反応（荒れ防止）
    is_dm = isinstance(message.channel, discord.DMChannel)
    is_mentioned = client.user in message.mentions if client.user else False
    if not (is_dm or is_mentioned):
        return

    # メンション部分を除去
    user_text = message.content
    if is_mentioned and client.user:
        user_text = user_text.replace(f"<@{client.user.id}>", "").strip()
        user_text = user_text.replace(f"<@!{client.user.id}>", "").strip()

    if not user_text:
        await message.reply("何について話す？（例：今日の乃木坂ニュース、推しメン相談、など）")
        return

    async with message.channel.typing():
        cid = message.channel.id
        history = conversation.get(cid, [])

        history.append({"role": "user", "content": user_text})
        history = history[-MAX_TURNS:]

        try:
            completion = ai.chat.completions.create(
                model="gpt-4.1",
                messages=[{"role": "system", "content": SYSTEM_PROMPT}] + history,
            )
            reply = completion.choices[0].message.content.strip()
        except Exception as e:
            await message.reply(f"OpenAI API エラー: {e}")
            return

        history.append({"role": "assistant", "content": reply})
        history = history[-MAX_TURNS:]
        conversation[cid] = history

    await message.reply(reply)

client.run(DISCORD_TOKEN)
