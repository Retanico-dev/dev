import os
import re
import json
import io
import openpyxl
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from supabase import create_client, Client

# ==========================================
# 環境変数・設定
# ==========================================
SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://griosberitrswzkynewx.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
GCP_SA_KEY_JSON = os.environ.get("GCP_SA_KEY_JSON")

FILE_ID = "1Takp4W8W6k14cvyJCToMYVmhlN5m1vWf"

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

def download_xlsx_bytes():
    scopes = ["https://www.googleapis.com/auth/drive.readonly"]
    creds_dict = json.loads(GCP_SA_KEY_JSON)
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    service = build("drive", "v3", credentials=creds)

    request = service.files().get_media(fileId=FILE_ID)
    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, request)
    
    done = False
    while not done:
        status, done = downloader.next_chunk()
    
    fh.seek(0)
    return fh

# ==========================================
# メイン同期処理
# ==========================================
def sync_leads():
    if not GCP_SA_KEY_JSON or not SUPABASE_KEY:
        raise ValueError("環境変数 GCP_SA_KEY_JSON または SUPABASE_SERVICE_ROLE_KEY が設定されていません。")

    print("📥 Google Drive から Excel (.xlsx) ファイルをダウンロード中...")
    xlsx_bytes = download_xlsx_bytes()
    wb = openpyxl.load_workbook(xlsx_bytes, data_only=True)

    ws = wb.active
    print(f"📄 対象シート: 「{ws.title}」")
    
    all_rows = list(ws.iter_rows(values_only=True))
    if len(all_rows) < 2:
        print("データが存在しません。")
        return

    # 【重要】2行目（インデックス1）を見出し行として取得
    header_row = all_rows[1]
    headers = [str(h).strip() if h is not None else "" for h in header_row]
    header_map = {name: idx for idx, name in enumerate(headers) if name}
    print(f"見出し列 (2行目): {list(header_map.keys())}")

    # 列インデックスの特定ヘルパー
    def find_col_idx(candidates):
        for c in candidates:
            for name, idx in header_map.items():
                if c in name:
                    return idx
        return None

    col_assignee = find_col_idx(["担当者", "担当"])
    col_status = find_col_idx(["営業ステータス", "ステータス", "進捗"])
    col_name = find_col_idx(["名前", "チャンネル名"])
    col_twitter = find_col_idx(["X", "SNS", "Twitter"])
    col_yt = find_col_idx(["Youtube", "YouTube"])
    col_sub = find_col_idx(["登録者", "ファン数"])

    print(f"🔍 検出列 -> 担当者: {col_assignee}, ステータス: {col_status}, 名前: {col_name}")

    # 既存リードの重複チェック
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    res = supabase.table("leads").select("client_name").execute()
    existing_names = set(r["client_name"] for r in (res.data or []) if r.get("client_name"))
    print(f"既存リード件数: {len(existing_names)} 件")

    leads_to_insert = []
    
    # 3行目以降（インデックス2〜）が実際のデータ
    for row in all_rows[2:]:
        def get_val(idx):
            if idx is not None and idx < len(row):
                v = row[idx]
                return str(v).strip() if v is not None else ""
            return ""

        assignee = get_val(col_assignee)
        status_val = get_val(col_status)
        name = get_val(col_name)

        # 担当者「れーた」、ステータス「未」の判定（スペース除外・表記ゆれ対応）
        is_reta = any(k in assignee for k in ["れーた", "れたす", "reta", "Reta"])
        is_unsent = status_val.startswith("未") or status_val == "未"

        if not is_reta or not is_unsent:
            continue

        if not name or "（例）" in name or name in existing_names:
            continue

        twitter_id = parse_twitter_id(get_val(col_twitter))
        yt_url = get_val(col_yt) or None
        sub_count = parse_sub_count(get_val(col_sub))

        vibe_lines = []
        for extra in ["所属", "タイプ", "メモ", "参考動画"]:
            e_idx = find_col_idx([extra])
            val = get_val(e_idx)
            if val:
                vibe_lines.append(f"{extra}: {val}")
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
                "text": f"Excel(2行目ヘッダー)より自動取り込み [担当:{assignee}]",
                "at": "now()"
            }]
        })
        existing_names.add(name)

    print(f"🎯 取り込み対象候補: {len(leads_to_insert)} 件")

    # Supabaseへ50件ずつバッチインサート
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
