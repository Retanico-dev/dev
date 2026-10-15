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

TARGET_INSERT_COUNT = 100  # 追加目標件数
EXCLUDE_HANDLES = ["v-stack", "vstack", "intent", "share", "home", "search"]

def get_channel_details(yt_url):
    """YouTube URLからチャンネルIDと登録者数を取得"""
    if not yt_url or not YOUTUBE_API_KEY:
        return None, None

    channel_id = None
    handle = None

    # 1. /channel/UC... 形式
    m_id = re.search(r"youtube\.com/channel/([a-zA-Z0-9_-]+)", yt_url)
    if m_id:
        channel_id = m_id.group(1)
    else:
        # 2. /@handle 形式
        m_handle = re.search(r"youtube\.com/@([a-zA-Z0-9_.-]+)", yt_url)
        if m_handle:
            handle = m_handle.group(1)
        else:
            # 3. /c/name や /user/name 形式
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
        if any(ex in handle.lower() for ex in EXCLUDE_HANDLES):
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
    # 既存 channel_id を取得（重複追加防止）
    existing_res = supabase.table("leads").select("channel_id").not_.is_("channel_id", "null").execute()
    existing_ids = set(item["channel_id"] for item in existing_res.data if item.get("channel_id"))
    print(f"既存登録チャンネル数: {len(existing_ids)}")

    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    inserted_count = 0
    current_page = 1
    max_pages = 50  # 無限ループ防止用の最大ページ上限

    while inserted_count < TARGET_INSERT_COUNT and current_page <= max_pages:
        target_url = f"https://v-stack.jp/vtubers?page={current_page}"
        print(f"\n--- ページ {current_page} を取得中: {target_url} (現在の新規追加: {inserted_count}/{TARGET_INSERT_COUNT}件) ---")
        
        try:
            resp = requests.get(target_url, headers=headers, timeout=15)
            if resp.status_code != 200:
                print(f"ページ終了またはステータス異常 (Status: {resp.status_code})。巡回を終了します。")
                break
            soup = BeautifulSoup(resp.text, "html.parser")
        except Exception as e:
            print(f"Scraping fetch error: {e}")
            break

        # 詳細リンクを抽出
        detail_links = []
        for a in soup.select("a[href*='/vtubers/']"):
            href = a.get("href")
            if href and re.search(r"/vtubers/[a-zA-Z0-9_-]+", href):
                full_url = href if href.startswith("http") else f"https://v-stack.jp{href}"
                if full_url not in detail_links and not full_url.rstrip("/").endswith("/vtubers"):
                    detail_links.append(full_url)

        if not detail_links:
            print("これ以上VTuberプロフィールが見つかりませんでした。巡回を終了します。")
            break

        print(f"ページ {current_page}: 発見したプロフィール {len(detail_links)} 件")

        for detail_url in detail_links:
            if inserted_count >= TARGET_INSERT_COUNT:
                break

            try:
                time.sleep(0.4)
                d_resp = requests.get(detail_url, headers=headers, timeout=10)
                d_soup = BeautifulSoup(d_resp.text, "html.parser")

                # 名前取得
                name_el = d_soup.select_one("h1, h2, .name")
                name = name_el.get_text(strip=True) if name_el else "VTuber"

                # YouTube / X 抽出
                yt_url = None
                x_id = None
                for a in d_soup.select("a[href]"):
                    href = a.get("href", "")
                    if "youtube.com" in href and not any(ex in href.lower() for ex in EXCLUDE_HANDLES):
                        if any(pt in href for pt in ["/channel/", "/@", "/c/", "/user/"]):
                            yt_url = href
                    elif "twitter.com" in href or "x.com" in href:
                        m_x = re.search(r"(?:twitter|x)\.com/([a-zA-Z0-9_]+)", href)
                        if m_x:
                            user = m_x.group(1)
                            if user.lower() not in EXCLUDE_HANDLES:
                                x_id = user

                if not yt_url:
                    continue

                desc_el = d_soup.select_one(".description, .bio, .profile, p")
                vibe_notes = desc_el.get_text(strip=True) if desc_el else "個人勢VTuber"

                # YouTube API判定
                channel_id, sub_count = get_channel_details(yt_url)
                if not channel_id:
                    continue

                if channel_id in existing_ids:
                    continue

                print(f"  [チェック] {name}: 登録者 {sub_count:,}人 ({yt_url})")

                # 条件判定: 1,000人 〜 10,000人
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
                    print(f"  ==> ★新規追加成功 ({inserted_count}/{TARGET_INSERT_COUNT}): {name} ({sub_count:,}人)")

            except Exception as e:
                print(f"  [Error] {detail_url}: {e}")

        current_page += 1

    print(f"\n==========================================")
    print(f"全工程完了: 合計 {inserted_count} 件の新規HOTリードを追加しました。")
    print(f"==========================================")

if __name__ == "__main__":
    crawl_and_sync()
