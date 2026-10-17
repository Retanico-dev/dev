import os
import sys
import json
import io
import openpyxl
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload

GCP_SA_KEY_JSON = os.environ.get("GCP_SA_KEY_JSON")
FILE_ID = "1Takp4W8W6k14cvyJCToMYVmhlN5m1vWf"

if len(sys.argv) < 3:
    print("Usage: python update_sheet_status.py <client_name> <new_status>")
    sys.exit(0)

target_name = sys.argv[1].strip()
new_status = sys.argv[2].strip()

print(f"🎯 更新要求: 「{target_name}」 ➔ ステータス: 「{new_status}」")

# 1. 認証 & Driveから.xlsxダウンロード
scopes = ["https://www.googleapis.com/auth/drive"]
creds_dict = json.loads(GCP_SA_KEY_JSON)
creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
service = build("drive", "v3", credentials=creds)

request = service.files().get_media(fileId=FILE_ID)
fh = io.BytesIO()
downloader = MediaIoBaseDownload(fh, request)
done = False
while not done:
    _, done = downloader.next_chunk()
fh.seek(0)

# 2. openpyxl で該当セルを検索して書き換え
wb = openpyxl.load_workbook(fh)
ws = wb.active

rows = list(ws.iter_rows(values_only=False))
header_row = rows[1]  # 2行目が見出し
name_col_idx = None
status_col_idx = None

for col_idx, cell in enumerate(header_row):
    val = str(cell.value or "")
    if "名前" in val or "チャンネル名" in val:
        name_col_idx = col_idx
    if "営業ステータス" in val or "ステータス" in val:
        status_col_idx = col_idx

if name_col_idx is None or status_col_idx is None:
    raise ValueError(f"ヘッダー列が見つかりません。name: {name_col_idx}, status: {status_col_idx}")

updated = False
for row in rows[2:]:  # 3行目以降がデータ
    cell_name = str(row[name_col_idx].value or "").strip()
    if cell_name == target_name:
        row[status_col_idx].value = new_status
        updated = True
        print(f"✓ 行更新完了: {target_name} -> {new_status}")
        break

if updated:
    # 3. Driveへ上書きアップロード
    out_fh = io.BytesIO()
    wb.save(out_fh)
    out_fh.seek(0)
    media = MediaIoBaseUpload(
        out_fh, 
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        resumable=True
    )
    service.files().update(fileId=FILE_ID, media_body=media).execute()
    print("✅ Google Drive の Excel を上書き更新しました！")
else:
    print(f"⚠️ シート内に対象のライバーが見つかりませんでした: {target_name}")
