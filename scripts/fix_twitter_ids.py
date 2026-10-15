import os
import re
import time
import requests
from bs4 import BeautifulSoup
from supabase import create_client, Client

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

EXCLUDE_KEYWORDS = ["vstack", "v_stack", "v-stack", "intent", "share", "home", "search"]

def is_excluded(val):
    val = (val or "").lower()
    normalized = re.sub(r"[-_]", "", val)
    if "vstack" in normalized:
        return True
    return any(ex in val for ex in EXCLUDE_KEYWORDS)

def fix_leads_twitter():
    # 修正対象のリードを取得（X IDが未設定、またはvstack系になっているもの）
    res = supabase.table("leads").select("id, client_name, youtube_url, twitter_id").execute()
    leads = res.data or []

    target_leads = [
        l for l in leads 
        if not l.get("twitter_id") or is_excluded(l.get("twitter_id"))
    ]

    print(f"修正対象のリード件数: {len(target_leads)} 件")
    if not target_leads:
        print("修正が必要なリードはありません。")
        return

    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

    # V-Stackの一覧ページを巡回して、各VTuberのページURLとYouTube URLの対応マップを作る
    print("V-Stackからプロフィールの照合データを収集中...")
    yt_to_x = {}
    current_page = 1
    max_pages = 30

    while current_page <= max_pages:
        url = f"https://v-stack.jp/vtubers?page={current_page}"
        try:
            r = requests.get(url, headers=headers, timeout=15)
            if r.status_code != 200:
                break
            soup = BeautifulSoup(r.text, "html.parser")
        except Exception as e:
            print(f"Error fetching page {current_page}: {e}")
            break

        detail_links = []
        for a in soup.select("a[href*='/vtubers/']"):
            href = a.get("href")
            if href and re.search(r"/vtubers/[a-zA-Z0-9_-]+", href):
                full_url = href if href.startswith("http") else f"https://v-stack.jp{href}"
                if full_url not in detail_links and not full_url.rstrip("/").endswith("/vtubers"):
                    detail_links.append(full_url)

        if not detail_links:
            break

        for d_url in detail_links:
            try:
                time.sleep(0.3)
                dr = requests.get(d_url, headers=headers, timeout=10)
                dsoup = BeautifulSoup(dr.text, "html.parser")

                found_yt = None
                found_x = None

                for a in dsoup.select("a[href]"):
                    href = a.get("href", "")
                    if not found_yt and "youtube.com" in href and not is_excluded(href):
                        if any(pt in href for pt in ["/channel/", "/@", "/c/", "/user/"]):
                            found_yt = href.rstrip("/")
                    
                    if not found_x and ("twitter.com" in href or "x.com" in href):
                        m = re.search(r"(?:twitter|x)\.com/([a-zA-Z0-9_]+)", href)
                        if m and not is_excluded(m.group(1)):
                            found_x = m.group(1)

                if found_yt and found_x:
                    yt_to_x[found_yt.lower()] = found_x

            except Exception:
                pass

        print(f"  ページ {current_page} 完了 (照合可能数: {len(yt_to_x)}件)")
        current_page += 1

    # DBのレコードを更新
    updated_count = 0
    for lead in target_leads:
        yt = (lead.get("youtube_url") or "").lower().rstrip("/")
        real_x = yt_to_x.get(yt)

        # YouTube URL完全一致で見つからなかった場合、ハンドル部分での部分一致
        if not real_x and yt:
            for k, v in yt_to_x.items():
                if ("@" in yt and "@" in k and yt.split("@")[-1] == k.split("@")[-1]) or \
                   ("channel/" in yt and "channel/" in k and yt.split("channel/")[-1] == k.split("channel/")[-1]):
                    real_x = v
                    break

        if real_x:
            supabase.table("leads").update({"twitter_id": real_x}).eq("id", lead["id"]).execute()
            updated_count += 1
            print(f"★更新完了: {lead['client_name']} ➔ @{real_x}")
        else:
            # 見つからなかった場合はゴミ文字のまま残さないよう NULL にクリア
            supabase.table("leads").update({"twitter_id": None}).eq("id", lead["id"]).execute()
            print(f"・クリア (手動確認推奨): {lead['client_name']}")

    print(f"\n==========================================")
    print(f"修正完了: {updated_count} / {len(target_leads)} 件の X ID を正しいものに更新しました。")
    print(f"==========================================")

if __name__ == "__main__":
    fix_leads_twitter()
