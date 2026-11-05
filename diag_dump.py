"""一次性：把今天 Circuit 檔每一列讀值印到 log（gzip+base64），供電表異常檢查。"""
import base64, gzip, json
from datetime import datetime, timedelta
import build_ems_live as b
now = datetime.utcnow() + timedelta(hours=8)
texts = b.fetch_circuit_files(b._creds(), [now.date()])
rows = b.parse_wide(texts[-1])
head = texts[-1].lstrip("\ufeff").splitlines()[0].split(",")[1:]
out = {"tags": head, "rows": [[t.strftime("%H:%M"), [v.get(k) for k in head]] for t, v in rows]}
blob = base64.b64encode(gzip.compress(json.dumps(out).encode())).decode()
print("DUMPSTART")
for i in range(0, len(blob), 3000):
    print("D:" + blob[i:i + 3000])
print("DUMPEND")
