import os
import re
import time
from urllib.parse import quote
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

def fix_leads_twitter_fast():
    # 修正対象のリードを取得（X IDが未設定、またはvstack系になっているもの）
    res = supabase.table("leads").select("id, client_name, twitter_id, youtube_url").execute()
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
    updated_count = 0

    for idx, lead in enumerate(target_leads, 1):
        name = lead.get("client_name") or ""
        clean_name = re.sub(r"[【】\[\]（）()@/].*$", "", name).strip()
        if not clean_name:
            clean_name = name

        print(f"[{idx}/{len(target_leads)}] 検索中: {name}")

        try:
            # V-Stack 内で名前検索
            search_url = f"https://v-stack.jp/vtubers?search={quote(clean_name)}"
            time.sleep(0.3)
            s_res = requests.get(search_url, headers=headers, timeout=10)
            soup = BeautifulSoup(s_res.text, "html.parser")

            detail_link = None
            for a in soup.select("a[href*='/vtubers/']"):
                href = a.get("href", "")
                if re.search(r"/vtubers/[a-zA-Z0-9_-]+", href) and not href.rstrip("/").endswith("/vtubers"):
                    detail_link = href if href.startswith("http") else f"https://v-stack.jp{href}"
                    break

            real_x = None

            if detail_link:
                time.sleep(0.3)
                d_res = requests.get(detail_link, headers=headers, timeout=10)
                d_soup = BeautifulSoup(d_res.text, "html.parser")

                for a in d_soup.select("a[href]"):
                    href = a.get("href", "")
                    if "twitter.com" in href or "x.com" in href:
                        m = re.search(r"(?:twitter|x)\.com/([a-zA-Z0-9_]+)", href)
                        if m:
                            user = m.group(1)
                            if not is_excluded(user):
                                real_x = user
                                break

            if real_x:
                supabase.table("leads").update({"twitter_id": real_x}).eq("id", lead["id"]).execute()
                updated_count += 1
                print(f"  ==> ★更新成功: {name} ➔ @{real_x}")
            else:
                # 本人のXが見つからない場合はゴミを残さず NULL にクリア
                supabase.table("leads").update({"twitter_id": None}).eq("id", lead["id"]).execute()
                print(f"  ==> ・X未設定 (NULLにクリア): {name}")

        except Exception as e:
            print(f"  ==> [Error] {name}: {e}")

    print(f"\n==========================================")
    print(f"爆速修正完了: 合計 {updated_count} / {len(target_leads)} 件の X ID を修復しました！")
    print(f"==========================================")

if __name__ == "__main__":
    fix_leads_twitter_fast()
