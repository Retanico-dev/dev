import os
import re
import json
import gspread
from google.oauth2.service_account import Credentials
from supabase import create_client, Client

# ==========================================
# 環境変数・設定
# ==========================================
SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://griosberitrswzkynewx.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
GCP_SA_KEY_JSON = os.environ.get("GCP_SA_KEY_JSON")

SPREADSHEET_URL = "https://docs.google.com/spreadsheets/d/1Takp4W8W6k14cvyJCToMYVmhlN5m1vWf/edit"
SHEET_NAME = "候補リスト"

# ==========================================
# ヘルパー関数
# ==========================================
def parse_sub_count(val):
    if not val:
        return None
    val_str = str(val).replace(",", "").strip()
    if "万" in val_str:
        try:
            num = float(val_str.replace("万", ""))
            return int(num * 10000)
        except ValueError:
            return None
    match = re.search(r"\d+", val_str)
    return int(match.group(0)) if match else None

def parse_twitter_id(url_or_id):
    if not url_or_id:
        return None
    val = str(url_or_id).strip()
    match = re.search(r"(?:twitter\.com|x\.com)/([a-zA-Z0-9_]+)", val)
    if match:
        return match.group(1)
    return val.replace("@", "").strip() or None

# ==========================================
# メイン処理
# ==========================================
def sync_leads():
    if not GCP_SA_KEY_JSON or not SUPABASE_KEY:
        raise ValueError("環境変数 GCP_SA_KEY_JSON または SUPABASE_SERVICE_ROLE_KEY が設定されていません。")

    # 1. Google Sheets API 認証 & データ取得
    scopes = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
    creds_dict = json.loads(GCP_SA_KEY_JSON)
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    gc = gspread.authorize(creds)

    sh = gc.open_by_url(SPREADSHEET_URL)
    worksheet = sh.worksheet(SHEET_NAME)
    
    # ヘッダー行を考慮して辞書形式で全行取得
    rows = worksheet.get_all_records()
    print(f"📊 シートから全 {len(rows)} 行を取得しました")

    # 2. Supabase 接続 & 既存リードの名前一覧取得（重複防止）
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    res = supabase.table("leads").select("client_name").execute()
    existing_names = set(r["client_name"] for r in (res.data or []) if r.get("client_name"))
    print(f"既存リード件数: {len(existing_names)} 件")

    leads_to_insert = []
    
    for r in rows:
        # 列名（O列: 担当者 / P列: 営業ステータス）
        assignee = str(r.get("担当者", "")).strip()
        status_val = str(r.get("営業ステータス", "")).strip()

        # 【抽出条件】O列が「れーた」かつ P列が「未」
        if assignee != "れーた" or status_val != "未":
            continue

        name = str(r.get("名前 / チャンネル名", "")).strip()
        if not name or "（例）" in name:
            continue

        # すでにProjectFlowに登録済みの名前はスキップ
        if name in existing_names:
            continue

        twitter_id = parse_twitter_id(r.get("X / SNS"))
        yt_url = str(r.get("Youtube", "")).strip() or None
        sub_count = parse_sub_count(r.get("チャンネル登録者"))

        # プロフィール・メモの成形
        vibe_lines = []
        if r.get("所属"): vibe_lines.append(f"所属: {r['所属']}")
        if r.get("タイプ"): vibe_lines.append(f"タイプ: {r['タイプ']}")
        if r.get("メモ"): vibe_lines.append(f"メモ: {r['メモ']}")
        if r.get("参考動画"): vibe_lines.append(f"参考動画: {r['参考動画']}")
        vibe_notes = "\n".join(vibe_lines) if vibe_lines else None

        leads_to_insert.append({
            "client_name": name,
            "twitter_id": twitter_id,
            "youtube_url": yt_url,
            "subscriber_count": sub_count,
            "vibe_notes": vibe_notes,
            "status": "未対応（HOT）",
            "send_count": 0,
            "logs": [{
                "type": "imported",
                "text": "スプレッドシート（担当:れーた / ステータス:未）より自動取り込み",
                "at": "now()"
            }]
        })
        existing_names.add(name)

    print(f"🎯 取り込み対象候補: {len(leads_to_insert)} 件")

    # 3. Supabaseへ一括投入（チャンク処理）
    if leads_to_insert:
        chunk_size = 50
        for i in range(0, len(leads_to_insert), chunk_size):
            chunk = leads_to_insert[i:i + chunk_size]
            supabase.table("leads").insert(chunk).execute()
        print(f"✅ 新規 {len(leads_to_insert)} 件をProjectFlow（未対応HOT）へ正常に追加しました！")
    else:
        print("新規の取り込み対象はありませんでした。")

if __name__ == "__main__":
    sync_leads()
