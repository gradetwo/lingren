#!/usr/bin/env python3
"""百度网盘公开分享（带提取码）无登录取文件列表 + 下载直链。

流程：share/verify（surl+pwd -> randsk/sekey）-> share/list（文件列表，
含 sign/timestamp）-> api/sharedownload（fid_list -> dlink）。
只打印信息，不下载文件本体（本体下载另行用 dlink 在会话内跑）。
"""
import json, sys, urllib.parse
import requests

SURL = "1PYdy8zLIX2m5YnqmPcuceQ"
PWD = "0527"
SHAREID = "25053329992"
UK = "4503601959368957"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
s = requests.Session()
s.headers.update({"User-Agent": UA, "Referer": f"https://pan.baidu.com/s/{SURL}"})

# 先 GET 分享页，拿 BAIDUID 等 cookie（浏览器流程；verify 可能校验 cookie）
g = s.get(f"https://pan.baidu.com/s/{SURL}", timeout=60)
print("share page HTTP", g.status_code, "cookies:", list(s.cookies.keys()))

import time
# 1. verify
r = s.post("https://pan.baidu.com/share/verify",
           data={"surl": SURL, "pwd": PWD, "t": str(int(time.time() * 1000)),
                 "channel": "chunlei",
                 "web": "1", "app_id": "250528", "bdstoken": "",
                 "logid": "", "clienttype": "0"},
           timeout=90)
print("verify HTTP", r.status_code, r.text[:300])
try:
    v = r.json()
except Exception:
    print("VERIFY-NONJSON"); sys.exit(1)
if v.get("errno") != 0:
    print("VERIFY-FAIL errno", v.get("errno"), v.get("show_msg") or v.get("errmsg"))
    sys.exit(1)
randsk = v.get("randsk")
print("randsk:", (randsk or "")[:20], "...")

# 2. list
params = {"shareid": SHAREID, "uk": UK, "sekey": urllib.parse.unquote(randsk),
          "page": "1", "num": "100", "order": "time", "desc": "1",
          "showempty": "0", "web": "1", "channel": "chunlei",
          "clienttype": "0", "app_id": "250528", "bdstoken": "",
          "logid": "", "dp-logid": ""}
r = s.get("https://pan.baidu.com/share/list", params=params, timeout=90)
print("list HTTP", r.status_code)
try:
    lst = r.json()
except Exception:
    print("LIST-NONJSON", r.text[:300]); sys.exit(1)
if lst.get("errno") != 0:
    print("LIST-FAIL errno", lst.get("errno"), lst.get("show_msg") or lst.get("errmsg"))
    sys.exit(1)
print("sign:", lst.get("sign"), "timestamp:", lst.get("timestamp"))
files = lst.get("list") or []
for f in files:
    print(f"FILE fs_id={f.get('fs_id')} isdir={f.get('isdir')} size={f.get('size')} "
          f"path={f.get('path')} server_filename={f.get('server_filename')}")
json.dump({"sign": lst.get("sign"), "timestamp": lst.get("timestamp"),
           "files": files, "randsk": randsk},
          open("/tmp/hxx_share.json", "w"), ensure_ascii=False, indent=1)
print("SHARE-INFO-OK /tmp/hxx_share.json")
