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

# 引数から更新対象のライバー名と新しいステータスを受け取る
# 例: python update_sheet_status.py "ライバー名" "送信済"
if len(sys.argv) < 3:
    print("Usage: python update_sheet_status.py <client_name> <new_status>")
    sys.exit(0)

target_name = sys.argv[1].strip()
new_status = sys.argv[2].strip()

# 1. 認証 & Driveからダウンロード
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

# 2. openpyxlで編集
wb = openpyxl.load_workbook(fh)
ws = wb.active

# 2行目ヘッダーから列を特定
rows = list(ws.iter_rows(values_only=False))
header_row = rows[1]
name_col_idx = None
status_col_idx = None

for col_idx, cell in enumerate(header_row):
    val = str(cell.value or "")
    if "名前" in val or "チャンネル名" in val:
        name_col_idx = col_idx
    if "営業ステータス" in val or "ステータス" in val:
        status_col_idx = col_idx

updated = False
for row in rows[2:]:
    cell_name = str(row[name_col_idx].value or "").strip()
    if cell_name == target_name:
        row[status_col_idx].value = new_status
        updated = True
        print(f"✓ 更新: {target_name} -> {new_status}")
        break

if updated:
    # 3. Driveへ上書きアップロード
    out_fh = io.BytesIO()
    wb.save(out_fh)
    out_fh.seek(0)
    media = MediaIoBaseUpload(out_fh, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    service.files().update(fileId=FILE_ID, media_body=media).execute()
    print("✅ DriveのExcelを上書き保存しました")
else:
    print(f"⚠️ 対象のライバーが見つかりませんでした: {target_name}")
