import os
import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from urllib.parse import urljoin
import xml.etree.ElementTree as ET

import requests
import discord
from bs4 import BeautifulSoup
from dotenv import load_dotenv

# OpenAI（新SDK）
from openai import OpenAI

# ======================
# Config
# ======================
load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

ai = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

BASE = "https://www.nogizaka46.com"
NEWS_HOME = "https://www.nogizaka46.com/s/n46/?ima=0"
MEDIA_LIST = "https://www.nogizaka46.com/s/n46/media/list"
DIARY_LIST = "https://www.nogizaka46.com/s/n46/diary/MEMBER/list?cd=MEMBER&ct={ct}&ima=0"

# 3/4/5/6期 公式ブログのct（web検索で確認できたもの）
BLOG_CTS = {
    "3期生 公式ブログ": 40004,
    "4期生 公式ブログ": 40005,
    "5期生 公式ブログ": 40007,
    "6期生 公式ブログ": 40008,
}

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?q={q}&hl=ja&gl=JP&ceid=JP:ja"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (NogizakaDiscordBot; +https://github.com/akoustamikuzak/nogizakanews)"
}

# チャンネルごとに「直近データ」と「会話履歴」を短く保持
STATE = {}
MAX_HISTORY = 8

REQUEST_TIMEOUT = 20


# ======================
# Utils
# ======================
def now_jst() -> datetime:
    return datetime.now(ZoneInfo("Asia/Tokyo"))

def yyyymm_jst(dt: datetime) -> str:
    return dt.strftime("%Y%m")

def ymd_jst(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")

def safe_get(url: str) -> str:
    r = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.text

def _state(ch_id: int):
    if ch_id not in STATE:
        STATE[ch_id] = {
            "news": [],
            "media": [],
            "blogs": [],
            "external": [],
            "history": [],
            "last_refresh_yyyymm": None,
        }
    return STATE[ch_id]

def extract_yyyymm_from_text(text: str) -> str | None:
    """
    例: 2026年1月 / 2026/01 / 202601
    """
    m = re.search(r"(20\d{2})[年/\-\.]?\s*(\d{1,2})", text)
    if not m:
        m2 = re.search(r"\b(20\d{2})(0[1-9]|1[0-2])\b", text)
        if m2:
            return m2.group(0)
        return None
    y, mo = m.groups()
    mo_i = int(mo)
    if 1 <= mo_i <= 12:
        return f"{y}{mo_i:02d}"
    return None


# ======================
# Official News
# ======================
def fetch_official_news_urls(limit: int = 6):
    html = safe_get(NEWS_HOME)
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
    html = safe_get(url)
    soup = BeautifulSoup(html, "lxml")

    h1 = soup.find("h1")
    title = h1.get_text(strip=True) if h1 else ""

    # 日付（YYYY.MM.DD）
    m = re.search(r"\b(20\d{2}\.\d{2}\.\d{2})\b", soup.get_text(" ", strip=True))
    date = m.group(1) if m else ""

    # 本文（雑に強い）
    texts = [p.get_text(" ", strip=True) for p in soup.find_all(["p", "div"])]
    body_candidates = [t for t in texts if len(t) >= 60 and "LATEST NEWS" not in t]
    body = max(body_candidates, key=len) if body_candidates else ""

    return {"source": "official_news", "title": title, "date": date, "url": url, "body": body}

def fetch_official_news(limit: int = 3):
    items = []
    for u in fetch_official_news_urls(limit=limit):
        try:
            items.append(fetch_official_news_detail(u))
        except Exception:
            continue
        time.sleep(0.4)
    return items


# ======================
# Official Media (Schedule)
# ======================
def fetch_media_urls(yyyymm: str, limit: int = 12):
    """
    /media/list?dy=YYYYMM から /media/detail/ を拾う
    """
    url = f"{MEDIA_LIST}?dy={yyyymm}"
    html = safe_get(url)

    # BeautifulSoupで拾えない（JS埋め込み等）場合もあるので、
    # まずは href から、次に正規表現でURL断片を拾う
    soup = BeautifulSoup(html, "lxml")

    urls = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/s/n46/media/detail/" in href:
            full = urljoin(BASE, href)
            if full not in urls:
                urls.append(full)
        if len(urls) >= limit:
            return urls

    # fallback: HTML全体から拾う
    for m in re.finditer(r'(/s/n46/media/detail/\d+)', html):
        full = urljoin(BASE, m.group(1))
        if full not in urls:
            urls.append(full)
        if len(urls) >= limit:
            break

    return urls

def fetch_media_detail(url: str):
    html = safe_get(url)
    soup = BeautifulSoup(html, "lxml")

    h1 = soup.find("h1")
    title = h1.get_text(strip=True) if h1 else ""

    text_all = soup.get_text(" ", strip=True)

    # 日付候補（YYYY.MM.DD）
    m = re.search(r"\b(20\d{2}\.\d{2}\.\d{2})\b", text_all)
    date = m.group(1) if m else ""

    # 時刻候補（HH:MM）
    tm = re.search(r"\b([01]\d|2[0-3]):[0-5]\d\b", text_all)
    hhmm = tm.group(0) if tm else ""

    # 本文
    texts = [p.get_text(" ", strip=True) for p in soup.find_all(["p", "div"])]
    body_candidates = [t for t in texts if len(t) >= 40]
    body = max(body_candidates, key=len) if body_candidates else ""

    return {
        "source": "official_media",
        "title": title,
        "date": date,
        "time": hhmm,
        "url": url,
        "body": body,
    }

def fetch_media(yyyymm: str, limit: int = 6):
    items = []
    for u in fetch_media_urls(yyyymm, limit=limit * 2):
        try:
            items.append(fetch_media_detail(u))
        except Exception:
            continue
        time.sleep(0.35)
        if len(items) >= limit:
            break
    return items


# ======================
# Official Blogs (3/4/5/6 gen)
# ======================
def fetch_blog_urls_for_ct(ct: int, limit: int = 6):
    url = DIARY_LIST.format(ct=ct)
    html = safe_get(url)
    soup = BeautifulSoup(html, "lxml")

    urls = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        # diary detail
        if "/s/n46/diary/detail/" in href:
            full = urljoin(BASE, href)
            if full not in urls:
                urls.append(full)
        if len(urls) >= limit:
            return urls

    # fallback: regex
    for m in re.finditer(r'(/s/n46/diary/detail/\d+)', html):
        full = urljoin(BASE, m.group(1))
        if full not in urls:
            urls.append(full)
        if len(urls) >= limit:
            break
    return urls

def fetch_blog_detail(url: str):
    html = safe_get(url)
    soup = BeautifulSoup(html, "lxml")
    text_all = soup.get_text(" ", strip=True)

    h1 = soup.find("h1")
    title = h1.get_text(strip=True) if h1 else ""

    # 日付（YYYY.MM.DD）
    m = re.search(r"\b(20\d{2}\.\d{2}\.\d{2})\b", text_all)
    date = m.group(1) if m else ""

    # 著者らしき名前（サイト構造に依存しすぎない範囲で）
    # “〇〇 公式ブログ”よりは detail 側のテキストから拾う
    author = ""
    # 例: “川﨑桜”などが出ることが多いので雑に候補抽出
    # ここは精度より「無いよりマシ」優先
    cand = re.search(r"\s([^\s]{2,8})\s(202\d\.\d{2}\.\d{2})", text_all)
    if cand:
        author = cand.group(1)

    # 本文
    texts = [p.get_text(" ", strip=True) for p in soup.find_all(["p", "div"])]
    body_candidates = [t for t in texts if len(t) >= 80]
    body = max(body_candidates, key=len) if body_candidates else ""

    return {"source": "official_blog", "title": title, "date": date, "author": author, "url": url, "body": body}

def fetch_blogs(limit_total: int = 6):
    items = []
    per_ct = max(1, limit_total // max(1, len(BLOG_CTS)))

    for label, ct in BLOG_CTS.items():
        urls = fetch_blog_urls_for_ct(ct, limit=per_ct)
        for u in urls:
            try:
                it = fetch_blog_detail(u)
                it["blog_group"] = label
                items.append(it)
            except Exception:
                continue
            time.sleep(0.35)

    # 日付が入っているものを優先して新しい順ぽく
    def key_fn(x):
        d = x.get("date", "")
        return d
    items.sort(key=key_fn, reverse=True)
    return items[:limit_total]


# ======================
# External (Unofficial-ish) via Google News RSS
# ======================
def fetch_google_news_rss(query: str, limit: int = 8):
    url = GOOGLE_NEWS_RSS.format(q=requests.utils.quote(query))
    xml_text = safe_get(url)

    root = ET.fromstring(xml_text)
    channel = root.find("channel")
    if channel is None:
        return []

    items = []
    for item in channel.findall("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub = (item.findtext("pubDate") or "").strip()
        if title and link:
            items.append({"source": "external_news", "title": title, "date": pub, "url": link})
        if len(items) >= limit:
            break
    return items


# ======================
# Summaries
# ======================
def format_official_news_list(items):
    lines = []
    for n in items:
        lines.append(f"■ {n.get('date','')} {n.get('title','')}\n{n.get('url','')}")
    return "\n\n".join(lines)

def format_media_list(items):
    lines = []
    for m in items:
        dt = m.get("date", "")
        tm = m.get("time", "")
        when = f"{dt} {tm}".strip()
        lines.append(f"■ {when} {m.get('title','')}\n{m.get('url','')}")
    return "\n\n".join(lines)

def format_blog_list(items):
    lines = []
    for b in items:
        grp = b.get("blog_group", "")
        author = b.get("author", "")
        head = " ".join([x for x in [b.get("date",""), grp, author, b.get("title","")] if x]).strip()
        lines.append(f"■ {head}\n{b.get('url','')}")
    return "\n\n".join(lines)

def format_external_list(items):
    lines = []
    for e in items:
        lines.append(f"■ {e.get('title','')}\n{e.get('url','')}\n({e.get('date','')})")
    return "\n\n".join(lines)

def summarize_with_openai(jst_date: str, news, media, blogs, external):
    """
    まとめ（OpenAIがあれば見やすく、無ければURL羅列）
    """
    if not ai:
        return (
            f"**今日（JST）: {jst_date}**\n\n"
            "【公式ニュース】\n" + (format_official_news_list(news) or "（なし）") + "\n\n"
            "【メディア出演（公式）】\n" + (format_media_list(media) or "（なし）") + "\n\n"
            "【公式ブログ】\n" + (format_blog_list(blogs) or "（なし）") + "\n\n"
            "【公式以外（参考：ニュース見出し）】\n" + (format_external_list(external) or "（なし）")
        )

    # LLMへ渡すコンテキスト
    def pack(items, fields):
        out = []
        for it in items:
            d = {k: it.get(k, "") for k in fields}
            out.append(d)
        return out

    payload = {
        "today_jst": jst_date,
        "official_news": pack(news, ["date", "title", "url", "body"]),
        "official_media": pack(media, ["date", "time", "title", "url", "body"]),
        "official_blogs": pack(blogs, ["date", "blog_group", "author", "title", "url", "body"]),
        "external_headlines": pack(external, ["date", "title", "url"]),
    }

    res = ai.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[
            {
                "role": "system",
                "content": (
                    "あなたは乃木坂46ファン向けの案内役。"
                    "事実（公式）と参考情報（公式以外）を明確に分ける。"
                    "公式は必ずURLを残す。"
                    "メディア出演は日付・時刻があれば必ず明記。"
                    "最後に『気になるトピックを1つ選ばせる質問』を添える。"
                ),
            },
            {
                "role": "user",
                "content": (
                    "次のデータを、Discordに貼りやすい形で簡潔に要約して。\n"
                    "・セクションは『公式ニュース』『メディア出演(公式)』『公式ブログ』『公式以外(参考)』\n"
                    "・各セクション3〜6件まで\n"
                    "・公式はURL必須\n\n"
                    f"{payload}"
                ),
            },
        ],
    )
    return res.choices[0].message.content


# ======================
# Chat about news
# ======================
def chat_about_context(channel_id: int, user_name: str, user_msg: str):
    st = _state(channel_id)
    news = st["news"]
    media = st["media"]
    blogs = st["blogs"]
    external = st["external"]
    history = st["history"]

    jst_date = ymd_jst(now_jst())

    if not ai:
        return (
            f"OpenAI_API_KEY が未設定です。\n"
            f"まず `@bot !nogi` でまとめは出せます（URL一覧）。\n"
            f"会話をするなら OpenAIキーを設定してください。"
        )

    # コンテキスト（長くしすぎない）
    def ctx_block(label, items, mapper, max_items=3):
        lines = []
        for it in items[:max_items]:
            lines.append(mapper(it))
        return f"{label}\n" + ("\n".join(lines) if lines else "（なし）")

    ctx = []
    ctx.append(ctx_block("【公式ニュース】", news, lambda n: f"- {n.get('date','')} {n.get('title','')} {n.get('url','')}"))
    ctx.append(ctx_block("【メディア出演(公式)】", media, lambda m: f"- {m.get('date','')} {m.get('time','')} {m.get('title','')} {m.get('url','')}"))
    ctx.append(ctx_block("【公式ブログ】", blogs, lambda b: f"- {b.get('date','')} {b.get('blog_group','')} {b.get('author','')} {b.get('title','')} {b.get('url','')}"))
    ctx.append(ctx_block("【公式以外(参考見出し)】", external, lambda e: f"- {e.get('title','')} {e.get('url','')}"))

    messages = [{
        "role": "system",
        "content": (
            f"今日はJSTで {jst_date}。\n"
            "あなたは乃木坂46ファンと自然に会話する相手。\n"
            "公式の確定情報は『公式ニュース』『メディア出演(公式)』『公式ブログ』のみ。\n"
            "公式以外は参考情報として扱い、断定しない。\n"
            "質問にはまず結論→根拠(URL)→補足の順で短く。\n"
        )
    }]

    messages.append({"role": "user", "content": "直近情報（要約用）:\n" + "\n\n".join(ctx)})

    for h in history[-MAX_HISTORY:]:
        messages.append(h)

    messages.append({"role": "user", "content": f"{user_name}: {user_msg}"})

    res = ai.chat.completions.create(
        model="gpt-4.1-mini",
        messages=messages,
    )
    answer = res.choices[0].message.content

    history.append({"role": "user", "content": f"{user_name}: {user_msg}"})
    history.append({"role": "assistant", "content": answer})
    st["history"] = history[-MAX_HISTORY * 2:]
    return answer


# ======================
# Discord Events
# ======================
@client.event
async def on_ready():
    print(f"Logged in as {client.user}")

async def refresh_if_needed(st, force: bool = False, yyyymm: str | None = None):
    """
    月が変わった or force のときに更新
    """
    now = now_jst()
    current_yyyymm = yyyymm or yyyymm_jst(now)

    if (not force) and st.get("last_refresh_yyyymm") == current_yyyymm and st.get("news") and st.get("media"):
        return

    st["last_refresh_yyyymm"] = current_yyyymm

    # 公式ニュース
    st["news"] = fetch_official_news(limit=4)

    # メディア（公式）
    st["media"] = fetch_media(current_yyyymm, limit=6)

    # 公式ブログ
    st["blogs"] = fetch_blogs(limit_total=6)

    # 公式以外（参考）
    st["external"] = fetch_google_news_rss("乃木坂46", limit=8)

@client.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    # Botへのメンションがなければ無視（今までの運用を踏襲）
    if client.user not in message.mentions:
        return

    ch_id = message.channel.id
    st = _state(ch_id)

    # メンション部分を除去（<@id> と <@!id> 両対応）
    content = message.content
    content = content.replace(f"<@{client.user.id}>", "")
    content = content.replace(f"<@!{client.user.id}>", "")
    content = content.strip()

    # 空メンション対策
    if not content:
        await message.channel.send("呼んだ？ `!nogi` でまとめるよ。例：`@乃木坂BOT !nogi`")
        return

    # コマンド：!nogi（まとめ）
    if content.startswith("!nogi"):
        # 月指定があれば拾う（例: !nogi 2026年1月）
        yyyymm = extract_yyyymm_from_text(content)
        await message.channel.send("公式ニュース/メディア/ブログ/外部見出しをまとめるね…")
        try:
            await refresh_if_needed(st, force=True, yyyymm=yyyymm)
            jst_date = ymd_jst(now_jst())
            summary = summarize_with_openai(
                jst_date=jst_date,
                news=st["news"],
                media=st["media"],
                blogs=st["blogs"],
                external=st["external"],
            )
            st["history"] = []  # まとめ更新したら会話文脈はリセット
            await message.channel.send(summary)
            await message.channel.send("気になるのある？このままメンションで話しかけてOK。")
        except Exception as e:
            await message.channel.send(f"ごめん、取得でエラー：{type(e).__name__}\nもう一回 `!nogi` してみて。")
        return

    # 通常：そのまま会話（必要なら裏で更新）
    try:
        await refresh_if_needed(st, force=False)
        reply = chat_about_context(ch_id, message.author.display_name, content)
    except Exception as e:
        reply = f"ごめん、今ちょっとエラー：{type(e).__name__}\n`!nogi` からやり直してみて。"

    await message.channel.send(reply)


if __name__ == "__main__":
    if not DISCORD_TOKEN:
        raise RuntimeError("DISCORD_TOKEN is missing. Put it in .env (not committed).")

    print("Starting Discord bot...")
    client.run(DISCORD_TOKEN)
