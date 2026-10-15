import os
import re
import requests
from bs4 import BeautifulSoup
from supabase import create_client, Client

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")  # Service Role Key または 権限のあるAnon Key
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

def get_channel_details(yt_url):
    """YouTube URLからチャンネルIDと登録者数を取得（forHandle または channelId）"""
    if not yt_url or not YOUTUBE_API_KEY:
        return None, None

    channel_id = None
    handle = None

    # URL判定
    m_id = re.search(r"youtube\.com/channel/([a-zA-Z0-9_-]+)", yt_url)
    if m_id:
        channel_id = m_id.group(1)
    else:
        m_handle = re.search(r"youtube\.com/@([a-zA-Z0-9_.-]+)", yt_url)
        if m_handle:
            handle = m_handle.group(1)

    api_url = "https://www.googleapis.com/youtube/v3/channels"
    params = {
        "part": "statistics,snippet",
        "key": YOUTUBE_API_KEY
    }

    if channel_id:
        params["id"] = channel_id
    elif handle:
        params["forHandle"] = handle
    else:
        return None, None

    try:
        res = requests.get(api_url, params=params, timeout=10)
        data = res.json()
        items = data.get("items", [])
        if not items:
            return None, None
        
        real_id = items[0]["id"]
        stats = items[0].get("statistics", {})
        sub_count = int(stats.get("subscriberCount", 0))
        return real_id, sub_count
    except Exception as e:
        print(f"YouTube API Error for {yt_url}: {e}")
        return None, None

def crawl_and_sync():
    # 既存の channel_id を一括取得（重複スキップ用）
    existing_res = supabase.table("leads").select("channel_id").not_.is_("channel_id", "null").execute()
    existing_ids = set(item["channel_id"] for item in existing_res.data if item.get("channel_id"))
    print(f"既存登録済みチャンネル数: {len(existing_ids)}")

    # V-Stack 音楽カテゴリ一覧をクロール（例: 音楽タグページ）
    target_url = "https://v-stack.jp/vtubers?tag=%E9%9F%B3%E6%A5%BD"
    headers = {"User-Agent": "Mozilla/5.0 (ProjectFlow-LeadSync/1.0)"}
    
    try:
        resp = requests.get(target_url, headers=headers, timeout=15)
        soup = BeautifulSoup(resp.text, "html.parser")
    except Exception as e:
        print(f"Scraping fetch error: {e}")
        return

    # VTuberカード要素を抽出（サイト構造に準拠）
    cards = soup.select(".vtuber-card, .card, article")
    inserted_count = 0

    for card in cards:
        if inserted_count >= 100:  # 1回の実行で最大100件まで
            break

        name_el = card.select_one(".name, h2, h3")
        if not name_el:
            continue
        name = name_el.get_text(strip=True)

        # リンク探索
        yt_url = None
        x_id = None
        for a in card.select("a[href]"):
            href = a["href"]
            if "youtube.com" in href:
                yt_url = href
            elif "twitter.com" in href or "x.com" in href:
                m_x = re.search(r"(?:twitter|x)\.com/([a-zA-Z0-9_]+)", href)
                if m_x and m_x.group(1).lower() not in ["intent", "share", "home"]:
                    x_id = m_x.group(1)

        if not yt_url:
            continue

        # 紹介文・傾向
        desc_el = card.select_one(".description, .bio, p")
        vibe_notes = desc_el.get_text(strip=True) if desc_el else "音楽系個人勢VTuber"

        # YouTube Data APIで登録者数とChannel IDを確認
        channel_id, sub_count = get_channel_details(yt_url)
        if not channel_id:
            continue

        # 既に登録済みの場合は完全スキップ
        if channel_id in existing_ids:
            continue

        # 登録者数 1,000人 〜 10,000人の範囲で判定
        if 1000 <= sub_count <= 10000:
            lead_data = {
                "client_name": name,
                "twitter_id": x_id,
                "youtube_url": yt_url,
                "channel_id": channel_id,
                "subscriber_count": sub_count,
                "vibe_notes": vibe_notes[:300],  # 冒頭300文字程度
                "status": "未対応（HOT）",
                "send_count": 0,
                "logs": [{
                    "type": "自動発掘",
                    "text": f"クローラーにより自動収集 (登録者数: {sub_count:,}人)",
                    "at": requests.utils.format_datetime(requests.utils.datetime.datetime.now())
                }]
            }

            try:
                supabase.table("leads").insert(lead_data).execute()
                existing_ids.add(channel_id)
                inserted_count += 1
                print(f"新規追加: {name} ({sub_count:,}人)")
            except Exception as e:
                print(f"Insert error for {name}: {e}")

    print(f"同期完了: {inserted_count} 件の新規HOTリードを追加しました。")

if __name__ == "__main__":
    crawl_and_sync()
