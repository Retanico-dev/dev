import os
import re
import requests
from bs4 import BeautifulSoup
from supabase import create_client, Client

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

EXCLUDE_KEYWORDS = ["vstack", "v_stack", "v-stack", "intent", "share", "home", "search", "merodamero"]

def is_excluded(val):
    val = (val or "").lower()
    normalized = re.sub(r"[-_]", "", val)
    if "vstack" in normalized:
        return True
    return any(ex in val for ex in EXCLUDE_KEYWORDS)

def get_twitter_from_youtube_about(channel_id):
    """YouTubeチャンネルの概要欄・カスタムリンクからXのIDを抽出"""
    if not YOUTUBE_API_KEY or not channel_id:
        return None
    try:
        url = "https://www.googleapis.com/youtube/v3/channels"
        params = {
            "part": "snippet",
            "id": channel_id,
            "key": YOUTUBE_API_KEY
        }
        res = requests.get(url, params=params, timeout=10)
        data = res.json()
        items = data.get("items", [])
        if not items:
            return None

        desc = items[0]["snippet"].get("description", "")
        # 概要欄に書かれた twitter.com/xxx や @xxx を探索
        m = re.search(r"(?:twitter|x)\.com/([a-zA-Z0-9_]{3,20})", desc)
        if m and not is_excluded(m.group(1)):
            return m.group(1)
        
        # 「X: @handle」「Twitter: @handle」の表記
        m2 = re.search(r"(?:Twitter|X|ツイッター)[：:\s]*@([a-zA-Z0-9_]{3,20})", desc, re.IGNORECASE)
        if m2 and not is_excluded(m2.group(1)):
            return m2.group(1)

    except Exception as e:
        print(f"    [YouTube API Snippet Error]: {e}")
    return None

def fix_leads():
    res = supabase.table("leads").select("id, client_name, youtube_url, channel_id, twitter_id").execute()
    leads = res.data or []

    # 17件のリードを対象にする
    print(f"修正対象: {len(leads)} 件")

    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

    # 念のためV-Stackの1〜2ページ目にある全VTuberの {名前: X_ID} 正確な対応表を1度だけ作成（数十秒）
    name_to_x = {}
    for p in range(1, 4):
        list_url = f"https://v-stack.jp/vtubers?page={p}"
        try:
            r = requests.get(list_url, headers=headers, timeout=10)
            soup = BeautifulSoup(r.text, "html.parser")
            for a in soup.select("a[href*='/vtubers/']"):
                href = a.get("href", "")
                if re.search(r"/vtubers/[a-zA-Z0-9_-]+", href) and not href.rstrip("/").endswith("/vtubers"):
                    d_url = href if href.startswith("http") else f"https://v-stack.jp{href}"
                    # 詳細を開いて本人の名前とXをペアで保存
                    dr = requests.get(d_url, headers=headers, timeout=10)
                    dsoup = BeautifulSoup(dr.text, "html.parser")
                    
                    person_name_el = dsoup.select_one("h1, h2, .name")
                    person_name = person_name_el.get_text(strip=True) if person_name_el else ""

                    person_x = None
                    for link in dsoup.select("a[href]"):
                        l_href = link.get("href", "")
                        if "twitter.com" in l_href or "x.com" in l_href:
                            mx = re.search(r"(?:twitter|x)\.com/([a-zA-Z0-9_]+)", l_href)
                            if mx and not is_excluded(mx.group(1)):
                                person_x = mx.group(1)
                                break
                    if person_name and person_x:
                        name_to_x[person_name] = person_x
        except Exception:
            pass

    updated_count = 0
    for lead in leads:
        lead_id = lead["id"]
        c_name = lead.get("client_name", "")
        ch_id = lead.get("channel_id")

        real_x = None

        # 1. まずV-Stackで取得した本人の名前完全一致から引く
        if c_name in name_to_x:
            real_x = name_to_x[c_name]
        else:
            # 部分一致で探す
            for k, v in name_to_x.items():
                if k in c_name or c_name in k:
                    real_x = v
                    break

        # 2. V-Stackで取れなければ、YouTubeの公式概要欄から引く
        if not real_x and ch_id:
            real_x = get_twitter_from_youtube_about(ch_id)

        # 反映
        if real_x:
            supabase.table("leads").update({"twitter_id": real_x}).eq("id", lead_id).execute()
            print(f"★更新成功: {c_name} ➔ @{real_x}")
            updated_count += 1
        else:
            # 不正なめろ田めろさんのIDを消去してNULLに
            supabase.table("leads").update({"twitter_id": None}).eq("id", lead_id).execute()
            print(f"・一致なし (NULLクリア): {c_name}")

    print(f"\n完了: {updated_count} / {len(leads)} 件を正しい個別X IDに修正しました。")

if __name__ == "__main__":
    fix_leads()
