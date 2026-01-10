import os
import re
import time
import html
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional
from urllib.parse import urljoin, quote_plus

import requests
import discord
from bs4 import BeautifulSoup
from dotenv import load_dotenv

# OpenAI（新SDK）
from openai import OpenAI

load_dotenv()

# ==========
# Env
# ==========
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# 任意：SNS反応を拾うための Nitter ホスト（例: https://nitter.net）
# ※ 公開Nitterは落ちたり制限があるので、自前 or 安定したホスト推奨
NITTER_HOST = os.getenv("NITTER_HOST", "").strip().rstrip("/")

# 任意：SNS検索クエリ（デフォルトは乃木坂46関連）
SNS_QUERY = os.getenv("SNS_QUERY", "乃木坂46 OR #乃木坂46 OR nogizaka46").strip()

# 任意：何件拾うか
OFFICIAL_NEWS_LIMIT = int(os.getenv("OFFICIAL_NEWS_LIMIT", "3"))
SCHEDULE_LIMIT = int(os.getenv("SCHEDULE_LIMIT", "6"))
BLOG_LIMIT = int(os.getenv("BLOG_LIMIT", "3"))
SNS_LIMIT = int(os.getenv("SNS_LIMIT", "6"))

# OpenAI client
ai = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

# ==========
# Discord
# ==========
intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

# ==========
# Official site endpoints (Nogizaka46)
# ==========
BASE = "https://www.nogizaka46.com"
HOME = "https://www.nogizaka46.com/s/n46/?ima=0"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (NogizakaDiscordBot; +https://github.com/akoustamikuzak/nogizakanews)"
}

# ==========
# State (per channel)
# ==========
STATE: Dict[int, Dict] = {}
MAX_HISTORY = 8

def jst_today_iso() -> str:
    jst = timezone(timedelta(hours=9))
    return datetime.now(jst).strftime("%Y-%m-%d")

def extract_date(text: str) -> Optional[str]:
    """
    例:
      今日は2026年1月8日です
      今日2026/1/8
    """
    m = re.search(r"(20\d{2})[年/.\-](\d{1,2})[月/.\-](\d{1,2})日?", text)
    if not m:
        return None
    y, mo, d = m.groups()
    return f"{y}-{int(mo):02d}-{int(d):02d}"

def _state(ch_id: int):
    if ch_id not in STATE:
        STATE[ch_id] = {
            "news": [],
            "schedule": [],
            "blog": [],
            "sns": [],
            "history": [],
            "date": None,  # user-set date if provided
            "last_fetch": None,
        }
    return STATE[ch_id]

# ==========
# Helpers
# ==========
def safe_get(url: str, timeout: int = 20) -> Optional[str]:
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout)
        r.raise_for_status()
        return r.text
    except Exception:
        return None

def soup_of(url: str) -> Optional[BeautifulSoup]:
    html_text = safe_get(url)
    if not html_text:
        return None
    return BeautifulSoup(html_text, "lxml")

def normalize_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()

# ==========
# 1) Official News
# ==========
def fetch_official_news_urls(limit: int = 6) -> List[str]:
    soup = soup_of(HOME)
    if not soup:
        return []
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

def fetch_official_news_detail(url: str) -> Dict:
    html_text = safe_get(url)
    if not html_text:
        return {"source": "official_news", "title": "", "date": "", "url": url, "body": ""}

    soup = BeautifulSoup(html_text, "lxml")

    h1 = soup.find("h1")
    title = normalize_ws(h1.get_text(" ", strip=True)) if h1 else ""

    # 日付（ページ内の YYYY.MM.DD）
    m = re.search(r"\b(20\d{2}\.\d{2}\.\d{2})\b", soup.get_text(" ", strip=True))
    date = m.group(1) if m else ""

    # 本文候補：長めのp/divから最大を選ぶ
    texts = [normalize_ws(p.get_text(" ", strip=True)) for p in soup.find_all(["p", "div"])]
    body_candidates = [t for t in texts if len(t) >= 80 and "LATEST NEWS" not in t]
    body = max(body_candidates, key=len) if body_candidates else ""

    return {"source": "official_news", "title": title, "date": date, "url": url, "body": body}

def fetch_official_news(limit: int = 3) -> List[Dict]:
    items = []
    urls = fetch_official_news_urls(limit=max(limit, 3))
    for u in urls[:limit]:
        try:
            items.append(fetch_official_news_detail(u))
        except Exception:
            continue
        time.sleep(0.4)
    return items

# ==========
# 2) Schedule / Media (Official)
#    - 公式サイト内のSCHEDULEページを探して拾う（壊れに強く）
# ==========
def guess_schedule_url() -> Optional[str]:
    """
    HOME から schedule っぽいリンクを探す。
    見つからなければ代表的パスも試す。
    """
    soup = soup_of(HOME)
    candidates = []
    if soup:
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "schedule" in href.lower():
                candidates.append(urljoin(BASE, href))

    # よくある候補（将来変わっても落ちない）
    fallback = [
        urljoin(BASE, "/s/n46/page/schedule?ima=0"),
        urljoin(BASE, "/s/n46/page/schedule"),
        urljoin(BASE, "/s/n46/diary?ima=0"),  # 念のため（誤検出防止のため実際は下でチェック）
    ]

    for u in candidates + fallback:
        if "schedule" not in u.lower():
            continue
        txt = safe_get(u)
        if txt and "SCHEDULE" in txt.upper():
            return u
    return candidates[0] if candidates else None

def fetch_schedule_items(limit: int = 6) -> List[Dict]:
    """
    公式SCHEDULEから「日付/時間/カテゴリ/タイトル/URL」をざっくり抽出。
    HTML構造は変わりやすいので、テキストベースで拾う。
    """
    url = guess_schedule_url()
    if not url:
        return []

    html_text = safe_get(url)
    if not html_text:
        return []

    soup = BeautifulSoup(html_text, "lxml")

    # まずはリンク付きの項目を収集（ページ内に event detail がある場合）
    items = []
    # date-ish pattern (YYYY.MM.DD or MM/DD etc)
    date_pat = re.compile(r"(20\d{2}[./-]\d{1,2}[./-]\d{1,2})|(\d{1,2}[./-]\d{1,2})")

    # リンクを含むブロックを拾って、近いテキストをイベントとして採用
    for a in soup.find_all("a", href=True):
        text = normalize_ws(a.get_text(" ", strip=True))
        href = urljoin(BASE, a["href"])

        # 露骨に関係ないのは除外
        if len(text) < 4:
            continue
        if any(x in href.lower() for x in ["javascript:", "mailto:"]):
            continue

        # schedule内の詳細ページっぽいもの or スケジュール行っぽい
        if "schedule" not in href.lower() and "event" not in href.lower():
            # ただしTV/Radioなど外部リンクの可能性もあるので、テキストにカテゴリがあれば拾う
            pass

        block = a.find_parent(["li", "article", "div", "section"])
        block_text = normalize_ws(block.get_text(" ", strip=True)) if block else text

        # カテゴリっぽいもの
        category = ""
        for key in ["TV", "RADIO", "WEB", "MAGAZINE", "LIVE", "EVENT", "舞台", "テレビ", "ラジオ", "雑誌", "配信"]:
            if key in block_text:
                category = key
                break

        # 日付っぽい
        dm = date_pat.search(block_text)
        date = dm.group(0) if dm else ""

        # それっぽいタイトル
        title = text if text else block_text[:80]

        # スケジュールページそのものへのリンクは除外
        if href.rstrip("/") == url.rstrip("/"):
            continue

        # ノイズ削減：公式ドメイン以外のリンクでもOK（番組サイト等）
        if date or category:
            items.append({
                "source": "official_schedule",
                "date": date,
                "category": category,
                "title": title,
                "url": href,
                "body": block_text[:400],
            })

        if len(items) >= limit * 3:
            break

    # 重複削除（url優先）
    uniq = []
    seen = set()
    for it in items:
        key = (it.get("title",""), it.get("url",""))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(it)

    return uniq[:limit]

# ==========
# 3) Blog (Official)
# ==========
def guess_blog_url() -> str:
    # 代表的に diary を見る（変わっても落ちないように）
    return urljoin(BASE, "/s/n46/diary?ima=0")

def fetch_blog_items(limit: int = 3) -> List[Dict]:
    url = guess_blog_url()
    html_text = safe_get(url)
    if not html_text:
        return []

    soup = BeautifulSoup(html_text, "lxml")
    items = []

    # diary内の詳細リンクを拾う
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/s/n46/diary/detail/" in href:
            full = urljoin(BASE, href)
            title = normalize_ws(a.get_text(" ", strip=True))
            if not title:
                title = "ブログ"
            items.append({"source": "official_blog", "title": title, "url": full})

        if len(items) >= limit:
            break

    # 詳細に入って本文と日付/メンバー名を拾う
    out = []
    for it in items:
        d = fetch_blog_detail(it["url"])
        if d:
            out.append(d)
        time.sleep(0.3)
    return out[:limit]

def fetch_blog_detail(url: str) -> Optional[Dict]:
    html_text = safe_get(url)
    if not html_text:
        return None
    soup = BeautifulSoup(html_text, "lxml")

    # タイトル（h1があれば）
    h1 = soup.find("h1")
    title = normalize_ws(h1.get_text(" ", strip=True)) if h1 else "ブログ"

    # 日付っぽい
    m = re.search(r"\b(20\d{2}\.\d{2}\.\d{2})\b", soup.get_text(" ", strip=True))
    date = m.group(1) if m else ""

    # メンバー名っぽい（ページ内に NAME があるケースが多い）
    author = ""
    # よくある要素をざっくり探索
    for sel in ["p", "span", "div"]:
        node = soup.find(sel, string=re.compile("乃木坂46|ブログ|.*"))
        # これは当てずっぽうなので無理に使わない
        if node:
            break

    # 本文：長めのp/divから最大
    texts = [normalize_ws(p.get_text(" ", strip=True)) for p in soup.find_all(["p", "div"])]
    body_candidates = [t for t in texts if len(t) >= 80]
    body = max(body_candidates, key=len) if body_candidates else ""

    return {
        "source": "official_blog",
        "date": date,
        "author": author,
        "title": title,
        "url": url,
        "body": body[:1200],
    }

# ==========
# 4) SNS Reactions (Optional)
#    - Nitter RSS を使う（X公式API無しで可能性があるルート）
#    - 失敗しても落ちない
# ==========
def fetch_sns_reactions(limit: int = 6, query: str = "") -> List[Dict]:
    if not NITTER_HOST:
        return []

    q = query or SNS_QUERY
    # nitter search rss: /search/rss?f=tweets&q=...
    rss_url = f"{NITTER_HOST}/search/rss?f=tweets&q={quote_plus(q)}"
    try:
        r = requests.get(rss_url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        xml = r.text
    except Exception:
        return []

    # RSSはXML。BeautifulSoupでXMLとして読む
    soup = BeautifulSoup(xml, "xml")
    out = []
    for item in soup.find_all("item"):
        title = normalize_ws(item.title.get_text(" ", strip=True)) if item.title else ""
        link = item.link.get_text(strip=True) if item.link else ""
        pub = item.pubDate.get_text(strip=True) if item.pubDate else ""
        desc = item.description.get_text(" ", strip=True) if item.description else ""
        desc = normalize_ws(html.unescape(desc))
        if title or desc:
            out.append({
                "source": "sns",
                "title": title[:120],
                "date": pub,
                "url": link,
                "body": desc[:600],
            })
        if len(out) >= limit:
            break
    return out

# ==========
# Fetch bundle
# ==========
def fetch_all_bundle(st: Dict) -> Dict:
    news = fetch_official_news(limit=OFFICIAL_NEWS_LIMIT)
    schedule = fetch_schedule_items(limit=SCHEDULE_LIMIT)
    blog = fetch_blog_items(limit=BLOG_LIMIT)
    sns = fetch_sns_reactions(limit=SNS_LIMIT, query=SNS_QUERY)

    st["news"] = news
    st["schedule"] = schedule
    st["blog"] = blog
    st["sns"] = sns
    st["last_fetch"] = datetime.now(timezone.utc).isoformat()

    return {
        "news": news,
        "schedule": schedule,
        "blog": blog,
        "sns": sns,
    }

# ==========
# Formatting / Summarize
# ==========
def format_fallback(bundle: Dict) -> str:
    lines = []

    # Official News
    lines.append("【公式ニュース】")
    if bundle["news"]:
        for n in bundle["news"]:
            lines.append(f"- {n.get('date','')} {n.get('title','')}\n  {n.get('url','')}")
    else:
        lines.append("- 取得できませんでした")

    # Schedule
    lines.append("\n【公式スケジュール / メディア】")
    if bundle["schedule"]:
        for s in bundle["schedule"]:
            d = s.get("date","")
            c = s.get("category","")
            t = s.get("title","")
            u = s.get("url","")
            lines.append(f"- {d} [{c}] {t}\n  {u}")
    else:
        lines.append("- 取得できませんでした")

    # Blog
    lines.append("\n【公式ブログ】")
    if bundle["blog"]:
        for b in bundle["blog"]:
            lines.append(f"- {b.get('date','')} {b.get('title','')}\n  {b.get('url','')}")
    else:
        lines.append("- 取得できませんでした")

    # SNS
    lines.append("\n【SNS反応】")
    if NITTER_HOST:
        if bundle["sns"]:
            for x in bundle["sns"]:
                lines.append(f"- {x.get('title','')}\n  {x.get('url','')}")
        else:
            lines.append("- 取得できませんでした（Nitter検索失敗/制限の可能性）")
    else:
        lines.append("- NITTER_HOST 未設定のためスキップ")

    return "\n".join(lines)

def summarize_bundle_for_chat(bundle: Dict, date_iso: str) -> str:
    # OpenAIが無い/失敗なら簡易フォールバック
    if not ai:
        return "（OpenAI未設定なので一覧表示）\n" + format_fallback(bundle)

    # LLMに渡すテキスト（URLは必ず含める）
    def pack(items: List[Dict], keys: List[str], max_body: int = 900) -> str:
        chunks = []
        for it in items:
            parts = []
            for k in keys:
                v = it.get(k, "")
                if not v:
                    continue
                if k == "body":
                    v = v[:max_body]
                parts.append(f"{k.upper()}: {v}")
            chunks.append("\n".join(parts))
        return "\n\n".join(chunks)

    news_text = pack(bundle["news"], ["date", "title", "url", "body"])
    sched_text = pack(bundle["schedule"], ["date", "category", "title", "url", "body"])
    blog_text = pack(bundle["blog"], ["date", "title", "url", "body"])
    sns_text = pack(bundle["sns"], ["date", "title", "url", "body"])

    system = (
        f"今日は {date_iso}（JST想定）という前提。\n"
        "あなたは乃木坂46ファン向けに情報をまとめる案内役。\n"
        "必ず『公式』『公式以外（ブログ/SNSなど）』を区別し、推測は推測として書く。\n"
        "公式ニュースや公式スケジュールに触れる時は、該当URLを必ず文中に入れる。\n"
        "メディア出演情報（TV/ラジオ/配信/雑誌等）は、日時/番組名/出演者（不明なら不明）をできるだけ明確に。\n"
    )

    user = (
        "以下をまとめて、Discordに貼りやすい形で出して。\n"
        "出力フォーマット:\n"
        "1) 今日のポイント（3〜6行）\n"
        "2) 公式ニュース（各項目にURL必須）\n"
        "3) 公式スケジュール/メディア（日時が分かれば書く。URLがあれば必須）\n"
        "4) 公式ブログ（各項目にURL）\n"
        "5) SNS反応（話題の傾向を2〜4点。URLがあれば添える。憶測は避ける）\n\n"
        f"=== OFFICIAL NEWS ===\n{news_text}\n\n"
        f"=== OFFICIAL SCHEDULE ===\n{sched_text}\n\n"
        f"=== OFFICIAL BLOG ===\n{blog_text}\n\n"
        f"=== SNS (NON-OFFICIAL) ===\n{sns_text}\n"
    )

    try:
        res = ai.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return res.choices[0].message.content
    except Exception:
        return "（OpenAIエラーのため一覧表示）\n" + format_fallback(bundle)

def chat_about_bundle(channel_id: int, user_name: str, user_msg: str) -> str:
    st = _state(channel_id)
    history = st["history"]

    # date: user-set > today(JST)
    date_iso = st["date"] or jst_today_iso()

    # OpenAIが無いなら最低限の返答
    if not ai:
        return "OpenAI_API_KEY が未設定です。対話をするなら OpenAI のキーを .env に設定して、service を再起動してください。"

    # bundle context (news/schedule/blog/sns)
    bundle = {
        "news": st.get("news", []),
        "schedule": st.get("schedule", []),
        "blog": st.get("blog", []),
        "sns": st.get("sns", []),
    }

    def ctx_items(items: List[Dict], label: str, n: int, body_len: int) -> str:
        out = [f"[{label}]"]
        for it in items[:n]:
            d = it.get("date","")
            t = it.get("title","")
            u = it.get("url","")
            b = (it.get("body","") or "")[:body_len]
            out.append(f"- {d} {t}\n  {u}\n  {b}")
        return "\n".join(out)

    ctx = "\n\n".join([
        ctx_items(bundle["news"], "OFFICIAL NEWS", 3, 700),
        ctx_items(bundle["schedule"], "OFFICIAL SCHEDULE", 4, 300),
        ctx_items(bundle["blog"], "OFFICIAL BLOG", 2, 500),
        ctx_items(bundle["sns"], "SNS", 4, 250),
    ])

    system = (
        f"今日は {date_iso}（JST想定）。あなたは乃木坂46ファンと自然に会話する相手。\n"
        "リアルタイムの確定情報が無い場合は『公式発表は確認できていない』と正直に。\n"
        "公式情報（ニュース/スケジュール）に触れる時は、可能ならURLを一緒に示す。\n"
        "ユーザーは『まとめ』より対話を好む。質問返しや深掘りを優先。\n"
        "誤情報は断定しない。\n"
    )

    messages = [{"role": "system", "content": system}]
    messages.append({"role": "user", "content": f"参考コンテキスト:\n{ctx}"})

    for h in history[-MAX_HISTORY:]:
        messages.append(h)

    messages.append({"role": "user", "content": f"{user_name}: {user_msg}"})

    res = ai.chat.completions.create(
        model="gpt-4.1-mini",
        messages=messages,
    )
    answer = res.choices[0].message.content

    # 履歴に追加
    history.append({"role": "user", "content": f"{user_name}: {user_msg}"})
    history.append({"role": "assistant", "content": answer})
    st["history"] = history[-MAX_HISTORY * 2:]

    return answer

# ==========
# Discord events
# ==========
@client.event
async def on_ready():
    print(f"Logged in as {client.user}")

@client.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    # Botへのメンションがなければ無視（今まで通り）
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
        await message.channel.send("呼んだ？（例）`今日は2026年1月9日です` とか `!nogi` とか送って！")
        return

    # 日付インプットを検出（仕様維持）
    date = extract_date(content)
    if date:
        st["date"] = date
        st["history"] = []  # 日付を入れたら会話文脈を軽くリセット
        await message.channel.send(f"了解。**{date}時点**って前提で話すね。今日は何が気になる？")
        return

    # 明示コマンド：最新まとめ取得（公式+メディア+ブログ+SNS）
    if content.startswith("!nogi"):
        await message.channel.send("公式ニュース/メディア/ブログ/SNS反応をまとめて取ってくるね…")
        bundle = fetch_all_bundle(st)
        date_iso = st["date"] or jst_today_iso()
        summary = summarize_bundle_for_chat(bundle, date_iso=date_iso)
        await message.channel.send(summary)
        await message.channel.send("この中で気になったやつ、どれ？（そのまま返信してOK）")
        return

    # まだbundleが無ければ初回だけ取得
    if not st.get("news") and not st.get("schedule") and not st.get("blog") and not st.get("sns"):
        await message.channel.send("まずは最新情報を取りに行ってくる…（`!nogi` でもOK）")
        fetch_all_bundle(st)

    # ここから“そのまま会話”
    try:
        reply = chat_about_bundle(ch_id, message.author.display_name, content)
    except Exception as e:
        reply = f"ごめん、今ちょっとエラー出た：{type(e).__name__}\nもう一回メンションして言ってみて。"

    await message.channel.send(reply)

if __name__ == "__main__":
    if not DISCORD_TOKEN:
        raise RuntimeError("DISCORD_TOKEN is missing. Set it in .env or environment variables.")
    print("Starting Discord bot...")
    client.run(DISCORD_TOKEN)
