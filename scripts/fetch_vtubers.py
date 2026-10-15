import os
import re
import time
from datetime import datetime, timezone
import requests
from bs4 import BeautifulSoup
from supabase import create_client, Client

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

TARGET_INSERT_COUNT = 100

# 運営公式アカウント・除外キーワード（部分一致で一網打尽）
EXCLUDE_KEYWORDS = [
    "vstack", "v_stack", "v-stack",
    "intent", "share", "home", "search", "hashtag", "privacy", "terms"
]

def is_excluded_handle(handle_or_url):
    val = (handle_or_url or "").lower()
    # v_stack, vstack, v-stack に加えて、ハイフンやアンダースコアを無視しても判定
    normalized = re.sub(r"[-_]", "", val)
    if "vstack" in normalized:
        return True
    return any(ex in val for ex in EXCLUDE_KEYWORDS)

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
        else:
            m_custom = re.search(r"youtube\.com/(?:c/|user/)?([a-zA-Z0-9_.-]+)", yt_url)
            if m_custom:
                handle = m_custom.group(1)

    api_url = "https://www.googleapis.com/youtube/v3/channels"
    params = {
        "part": "statistics",
        "key": YOUTUBE_API_KEY
    }

    if channel_id:
        params["id"] = channel_id
    elif handle:
        if is_excluded_handle(handle):
            return None, None
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
        print(f"    [API Error] {yt_url}: {e}")
        return None, None

def crawl_and_sync():
    existing_res = supabase.table("leads").select("channel_id").not_.is_("channel_id", "null").execute()
    existing_ids = set(item["channel_id"] for item in existing_res.data if item.get("channel_id"))
    print(f"既存登録チャンネル数: {len(existing_ids)}")

    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    inserted_count = 0
    current_page = 1
    max_pages = 50

    while inserted_count < TARGET_INSERT_COUNT and current_page <= max_pages:
        target_url = f"https://v-stack.jp/vtubers?page={current_page}"
        print(f"\n--- ページ {current_page} を取得中 (現在: {inserted_count}/{TARGET_INSERT_COUNT}件) ---")
        
        try:
            resp = requests.get(target_url, headers=headers, timeout=15)
            if resp.status_code != 200:
                print(f"ページ終了 (Status: {resp.status_code})。巡回を終了します。")
                break
            soup = BeautifulSoup(resp.text, "html.parser")
        except Exception as e:
            print(f"Scraping fetch error: {e}")
            break

        detail_links = []
        for a in soup.select("a[href*='/vtubers/']"):
            href = a.get("href")
            if href and re.search(r"/vtubers/[a-zA-Z0-9_-]+", href):
                full_url = href if href.startswith("http") else f"https://v-stack.jp{href}"
                if full_url not in detail_links and not full_url.rstrip("/").endswith("/vtubers"):
                    detail_links.append(full_url)

        if not detail_links:
            print("VTuberプロフィールが見つかりませんでした。巡回を終了します。")
            break

        for detail_url in detail_links:
            if inserted_count >= TARGET_INSERT_COUNT:
                break

            try:
                time.sleep(0.4)
                d_resp = requests.get(detail_url, headers=headers, timeout=10)
                d_soup = BeautifulSoup(d_resp.text, "html.parser")

                name_el = d_soup.select_one("h1, h2, .name")
                name = name_el.get_text(strip=True) if name_el else "VTuber"

                yt_url = None
                x_id = None

                for a in d_soup.select("a[href]"):
                    href = a.get("href", "")

                    # YouTube
                    if not yt_url and "youtube.com" in href and not is_excluded_handle(href):
                        if any(pt in href for pt in ["/channel/", "/@", "/c/", "/user/"]):
                            yt_url = href

                    # X (Twitter)
                    if not x_id and ("twitter.com" in href or "x.com" in href):
                        m_x = re.search(r"(?:twitter|x)\.com/([a-zA-Z0-9_]+)", href)
                        if m_x:
                            candidate_user = m_x.group(1)
                            if not is_excluded_handle(candidate_user):
                                x_id = candidate_user

                if not yt_url:
                    continue

                desc_el = d_soup.select_one(".description, .bio, .profile, p")
                vibe_notes = desc_el.get_text(strip=True) if desc_el else "個人勢VTuber"

                channel_id, sub_count = get_channel_details(yt_url)
                if not channel_id:
                    continue

                if channel_id in existing_ids:
                    continue

                print(f"  [チェック] {name}: 登録者 {sub_count:,}人 (X: @{x_id})")

                if 1000 <= sub_count <= 10000:
                    now_iso = datetime.now(timezone.utc).isoformat()
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
                            "at": now_iso
                        }]
                    }

                    supabase.table("leads").insert(lead_data).execute()
                    existing_ids.add(channel_id)
                    inserted_count += 1
                    print(f"  ==> ★新規追加成功 ({inserted_count}/{TARGET_INSERT_COUNT}): {name} (X: @{x_id})")

            except Exception as e:
                print(f"  [Error] {detail_url}: {e}")

        current_page += 1

    print(f"\n全工程完了: 合計 {inserted_count} 件の新規HOTリードを追加しました。")

if __name__ == "__main__":
    crawl_and_sync()
