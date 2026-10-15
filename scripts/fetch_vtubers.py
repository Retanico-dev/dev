import os
import re
import time
import requests
from bs4 import BeautifulSoup
from supabase import create_client, Client

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

def get_channel_details(yt_url):
    """YouTube URLからチャンネルIDと登録者数を取得"""
    if not yt_url or not YOUTUBE_API_KEY:
        return None, None

    channel_id = None
    handle = None

    m_id = re.search(r"youtube\.com/channel/([a-zA-Z0-9_-]+)", yt_url)
    if m_id:
        channel_id = m_id.group(1)
    else:
        m_handle = re.search(r"youtube\.com/@([a-zA-Z0-9_.-]+)", yt_url)
        if m_handle:
            handle = m_handle.group(1)

    api_url = "https://www.googleapis.com/youtube/v3/channels"
    params = {
        "part": "statistics",
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
        print(f"  [API Error] {yt_url}: {e}")
        return None, None

def crawl_and_sync():
    # 既存の channel_id を取得（重複排除用）
    existing_res = supabase.table("leads").select("channel_id").not_.is_("channel_id", "null").execute()
    existing_ids = set(item["channel_id"] for item in existing_res.data if item.get("channel_id"))
    print(f"既存登録チャンネル数: {len(existing_ids)}")

    # V-Stack 一覧ページをクロール
    target_url = "https://v-stack.jp/vtubers"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    
    try:
        resp = requests.get(target_url, headers=headers, timeout=15)
        soup = BeautifulSoup(resp.text, "html.parser")
    except Exception as e:
        print(f"Scraping fetch error: {e}")
        return

    # プロフィール詳細リンクをすべて抽出 (/vtubers/xxxx)
    detail_links = []
    for a in soup.select("a[href*='/vtubers/']"):
        href = a.get("href")
        if href and re.search(r"/vtubers/[a-zA-Z0-9-]+", href):
            full_url = href if href.startswith("http") else f"https://v-stack.jp{href}"
            if full_url not in detail_links and not full_url.endswith("/vtubers"):
                detail_links.append(full_url)

    print(f"発見したVTuberプロフィール数: {len(detail_links)}")

    inserted_count = 0

    for detail_url in detail_links:
        if inserted_count >= 100:
            break

        try:
            time.sleep(0.5)  # 負荷軽減
            d_resp = requests.get(detail_url, headers=headers, timeout=10)
            d_soup = BeautifulSoup(d_resp.text, "html.parser")

            # 名前
            name_el = d_soup.select_one("h1, h2, .name")
            name = name_el.get_text(strip=True) if name_el else "VTuber"

            # YouTube / X (Twitter) リンク探索
            yt_url = None
            x_id = None
            for a in d_soup.select("a[href]"):
                href = a.get("href", "")
                if "youtube.com" in href:
                    yt_url = href
                elif "twitter.com" in href or "x.com" in href:
                    m_x = re.search(r"(?:twitter|x)\.com/([a-zA-Z0-9_]+)", href)
                    if m_x and m_x.group(1).lower() not in ["intent", "share", "home", "search"]:
                        x_id = m_x.group(1)

            if not yt_url:
                continue

            # 自己紹介文
            desc_el = d_soup.select_one(".description, .bio, .profile, p")
            vibe_notes = desc_el.get_text(strip=True) if desc_el else "個人勢VTuber"

            # YouTube Data APIで確認
            channel_id, sub_count = get_channel_details(yt_url)
            if not channel_id:
                continue

            if channel_id in existing_ids:
                print(f"  [スキップ・登録済] {name}")
                continue

            # 登録者数判定: 1,000人 〜 10,000人
            print(f"  [チェック] {name}: 登録者 {sub_count:,}人")
            if 1000 <= sub_count <= 10000:
                lead_data = {
                    "client_name": name,
                    "twitter_id": x_id,
                    "youtube_url": yt_url,
                    "channel_id": channel_id,
                    "subscriber_count": sub_count,
                    "vibe_notes": vibe_notes[:300],
                    "status": "未対応（HOT）",
                    "send_count": 0,
                    "logs": [{
                        "type": "自動発掘",
                        "text": f"クローラーにより自動収集 (登録者数: {sub_count:,}人)",
                        "at": requests.utils.format_datetime(requests.utils.datetime.datetime.now())
                    }]
                }

                supabase.table("leads").insert(lead_data).execute()
                existing_ids.add(channel_id)
                inserted_count += 1
                print(f"  ==> ★新規追加成功: {name} ({sub_count:,}人)")

        except Exception as e:
            print(f"  [Error] {detail_url}: {e}")

    print(f"\n同期完了: {inserted_count} 件の新規HOTリードを追加しました。")

if __name__ == "__main__":
    crawl_and_sync()
