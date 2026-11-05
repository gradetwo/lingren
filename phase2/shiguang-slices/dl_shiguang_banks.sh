#!/bin/bash
# 时光切片 · 声库下载（Qixuan＋声码器＋琅／齑）
# 跑法：上传到 /content/dl_shiguang_banks.sh，nohup detached 跑，日志 /content/dl_shiguang_banks.log
# 只下第一阶段要的东西，不下 SoulX／YingMusic。
exec > /content/dl_shiguang_banks.log 2>&1
set -u
cd /content || exit 1

echo "=== 1. 绮萱声库 Qixuan v2.7.0 ==="
mkdir -p /content/voicebanks
if [ -f /content/voicebanks/Qixuan_v2.7.0_DiffSinger_OpenUtau/dsconfig.yaml ]; then
  echo "BANK-EXISTS skip download"
else
  cd /content/voicebanks || exit 1
  curl -sL -o Qixuan_v2.7.0_DiffSinger_OpenUtau.zip \
    https://github.com/yqzhishen/qixuan-diffsinger/releases/download/v2.7.0/Qixuan_v2.7.0_DiffSinger_OpenUtau.zip
  ls -la Qixuan_v2.7.0_DiffSinger_OpenUtau.zip
  unzip -q -o Qixuan_v2.7.0_DiffSinger_OpenUtau.zip -d Qixuan_v2.7.0_DiffSinger_OpenUtau_tmp
  inner=$(find Qixuan_v2.7.0_DiffSinger_OpenUtau_tmp -maxdepth 2 -name "dsconfig.yaml" | head -1 | xargs dirname)
  echo "bank inner dir: $inner"
  mkdir -p Qixuan_v2.7.0_DiffSinger_OpenUtau
  cp -r "$inner"/. Qixuan_v2.7.0_DiffSinger_OpenUtau/
  test -f /content/voicebanks/Qixuan_v2.7.0_DiffSinger_OpenUtau/dsconfig.yaml && echo "BANK-OK" || { echo "BANK-FAIL"; exit 1; }
fi

echo "=== 2. 声码器 pc_nsf_hifigan 44.1k ==="
if [ -f /content/voicebanks/vocoder/unpacked/pc_nsf_hifigan_44.1k_hop512_128bin_2025.02.onnx ]; then
  echo "VOCODER-EXISTS skip download"
else
  mkdir -p /content/voicebanks/vocoder/unpacked
  cd /content/voicebanks/vocoder || exit 1
  curl -sL -o vocoder.oudep \
    https://github.com/openvpi/vocoders/releases/download/pc-nsf-hifigan-44.1k-hop512-128bin-2025.02/pc_nsf_hifigan_44.1k_hop512_128bin_2025.02.oudep
  ls -la vocoder.oudep
  unzip -q -o vocoder.oudep -d unpacked/
  test -f /content/voicebanks/vocoder/unpacked/pc_nsf_hifigan_44.1k_hop512_128bin_2025.02.onnx && echo "VOCODER-OK" || { echo "VOCODER-FAIL"; exit 1; }
fi

echo "=== 3. 琅／齑（BaiShuo GitHub Release）==="
mkdir -p /content/voicebanks_banks
cd /content/voicebanks_banks || exit 1
python3 - <<'PYEOF'
import json, subprocess, sys, urllib.request
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
  if [ -f "$fn" ]; then
    mkdir -p "$dir"
    unzip -q -o "$fn" -d "${dir}_tmp"
    inner=$(find "${dir}_tmp" -maxdepth 2 -name "dsconfig.yaml" | head -1 | xargs dirname)
    echo "$key inner dir: $inner"
    cp -r "$inner"/. "$dir"/
    test -f "$dir/dsconfig.yaml" && echo "BANK-OK-$key $dir" || echo "BANK-FAIL-$key (no dsconfig in $dir)"
  fi
done
ls -la /content/voicebanks_banks/
echo "DL-SHIGUANG-BANKS-DONE"
