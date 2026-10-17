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

# 既存のxlsxファイルのID
FILE_ID = "1Takp4W8W6k14cvyJCToMYVmhlN5m1vWf"
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

    if SHEET_NAME not in wb.sheetnames:
        raise ValueError(f"シート「{SHEET_NAME}」が見つかりません。利用可能: {wb.sheetnames}")

    ws = wb[SHEET_NAME]
    
    # ヘッダー行（1行目）の取得
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        print("シートが空です。")
        return

    headers = [str(h).strip() if h is not None else "" for h in rows[0]]
    header_map = {name: idx for idx, name in enumerate(headers) if name}
    print(f"ヘッダー列: {list(header_map.keys())}")

    # Supabase 接続 & 既存リードの名前一覧取得（重複防止）
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    res = supabase.table("leads").select("client_name").execute()
    existing_names = set(r["client_name"] for r in (res.data or []) if r.get("client_name"))
    print(f"既存リード件数: {len(existing_names)} 件")

    leads_to_insert = []
    
    # 2行目以降をパース
    for row in rows[1:]:
        def get_val(col_name):
            idx = header_map.get(col_name)
            if idx is not None and idx < len(row):
                v = row[idx]
                return str(v).strip() if v is not None else ""
            return ""

        assignee = get_val("担当者")
        status_val = get_val("営業ステータス")

        # 【抽出条件】O列が「れーた」かつ P列が「未」
        if assignee != "れーた" or status_val != "未":
            continue

        name = get_val("名前 / チャンネル名")
        if not name or "（例）" in name:
            continue

        if name in existing_names:
            continue

        twitter_id = parse_twitter_id(get_val("X / SNS"))
        yt_url = get_val("Youtube") or None
        sub_count = parse_sub_count(get_val("チャンネル登録者"))

        vibe_lines = []
        affiliation = get_val("所属")
        type_val = get_val("タイプ")
        memo_val = get_val("メモ")
        ref_video = get_val("参考動画")

        if affiliation: vibe_lines.append(f"所属: {affiliation}")
        if type_val: vibe_lines.append(f"タイプ: {type_val}")
        if memo_val: vibe_lines.append(f"メモ: {memo_val}")
        if ref_video: vibe_lines.append(f"参考動画: {ref_video}")
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
                "text": "Excel（担当:れーた / ステータス:未）より自動取り込み",
                "at": "now()"
            }]
        })
        existing_names.add(name)

    print(f"🎯 取り込み対象候補: {len(leads_to_insert)} 件")

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
