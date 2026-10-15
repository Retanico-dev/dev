import os
import re
import requests
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

def extract_twitter_from_text(text):
    if not text:
        return None
    # 1. twitter.com/xxx や x.com/xxx 形式
    m = re.search(r"(?:twitter|x)\.com/([a-zA-Z0-9_]{3,20})", text)
    if m:
        handle = m.group(1)
        if not is_excluded(handle):
            return handle
    
    # 2. X: @xxx や Twitter: @xxx 形式
    m2 = re.search(r"(?:Twitter|X|ツイッター)[：:\s]*@([a-zA-Z0-9_]{3,20})", text, re.IGNORECASE)
    if m2:
        handle = m2.group(1)
        if not is_excluded(handle):
            return handle

    return None

def fix_leads_fast():
    # 既存のリードを取得
    res = supabase.table("leads").select("id, client_name, channel_id, twitter_id").execute()
    leads = res.data or []

    print(f"対象リード数: {len(leads)} 件")
    if not leads:
        print("リードが存在しません。")
        return

    updated_count = 0

    for idx, lead in enumerate(leads, 1):
        lead_id = lead["id"]
        c_name = lead.get("client_name", "")
        ch_id = lead.get("channel_id")

        print(f"[{idx}/{len(leads)}] 確認中: {c_name} (Channel: {ch_id})")

        real_x = None

        if ch_id and YOUTUBE_API_KEY:
            try:
                # YouTube APIでチャンネルの概要欄・カスタムリンク説明を取得
                api_url = "https://www.googleapis.com/youtube/v3/channels"
                params = {
                    "part": "snippet",
                    "id": ch_id,
                    "key": YOUTUBE_API_KEY
                }
                r = requests.get(api
