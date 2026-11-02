import re
log = open("/content/setup_long.log").read()
for key in ["SOULX_P01_OK", "SOULX_P02_OK", "VR_P03_OK", "SETUP_DONE",
            "SOULX_HF_DIR", "VR_HF_DIR"]:
    hits = [l for l in log.splitlines() if key in l]
    print(key, "->", hits[:2] if hits else "MISSING")
for key in ["error", "Error", "FAILED", "failed"]:
    hits = [l for l in log.splitlines() if key in l and "Traceback" not in l]
    if hits:
        print("WARN", key, hits[:3])
print("log_lines:", len(log.splitlines()))
