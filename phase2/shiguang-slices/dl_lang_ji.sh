#!/bin/bash
# 时光切片 · 补下琅／齑（上一版资产名大小写匹配漏了 DiffSinger_lang.zip / DiffSinger_ji.zip）
# 日志 /content/dl_lang_ji.log；失败即退出，不再空打 DONE。
exec > /content/dl_lang_ji.log 2>&1
set -e
cd /content/voicebanks_banks || exit 1
python3 - <<'PYEOF'
import json, sys, urllib.request
API = "https://api.github.com/repos/BaiShuoQwQ/DiffSinger_VoiceBanks_release/releases?per_page=100"
req = urllib.request.Request(API, headers={"Accept": "application/vnd.github+json",
                                           "User-Agent": "colab-dl-shiguang"})
data = json.load(urllib.request.urlopen(req, timeout=60))
want = {"lang": ("lang", "琅"), "ji": ("ji", "齑")}
found = {}
for rel in data:
    for asset in rel.get("assets", []):
        low = asset["name"].lower()
        for key, needles in want.items():
            if key not in found and any(n.lower() in low or n in asset["name"] for n in needles):
                found[key] = (asset["name"], asset["browser_download_url"])
for key in ("lang", "ji"):
    if key in found:
        print(f"ASSET-{key.upper()}: {found[key][0]} :: {found[key][1]}")
    else:
        print(f"ASSET-{key.upper()}-MISSING")
        sys.exit(1)
json.dump(found, open("/content/voicebanks_banks/assets.json", "w"), ensure_ascii=False, indent=1)
PYEOF
for key in lang ji; do
  url=$(python3 -c "import json; print(json.load(open('/content/voicebanks_banks/assets.json'))['$key'][1])")
  fn=$(python3 -c "import json; print(json.load(open('/content/voicebanks_banks/assets.json'))['$key'][0])")
  echo "downloading $key: $fn"
  curl -sL -o "$fn" "$url"
  ls -la "$fn"
done
for key in lang ji; do
  fn=$(python3 -c "import json; print(json.load(open('/content/voicebanks_banks/assets.json'))['$key'][0])")
  dir="${fn%.zip}"
  mkdir -p "$dir"
  unzip -q -o "$fn" -d "${dir}_tmp"
  inner=$(find "${dir}_tmp" -maxdepth 2 -name "dsconfig.yaml" | head -1 | xargs dirname)
  echo "$key inner dir: $inner"
  cp -r "$inner"/. "$dir"/
  test -f "$dir/dsconfig.yaml" && echo "BANK-OK-$key $dir" || { echo "BANK-FAIL-$key"; exit 1; }
done
ls -la /content/voicebanks_banks/
echo "DL-LANG-JI-DONE"
