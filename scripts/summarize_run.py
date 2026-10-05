"""Read `n8n execute --rawOutput` JSON from stdin and print a one-line result."""
import json
import sys

name = sys.argv[1]
raw = sys.stdin.read()
if "{" not in raw:
    print(f"✗ {name}: no output from n8n\n{raw[-500:]}")
    sys.exit(1)
try:
    run = json.loads(raw[raw.index("{"):])
except ValueError:  # n8n prints a plain error instead of JSON when a node throws
    lines = [l for l in raw.splitlines() if l.strip() and not l.startswith(" ")]
    hint = next((l for l in lines if "Error" in l or l[:3].isdigit()), lines[-1] if lines else "?")
    print(f"✗ {name}: {hint[:300]}")
    sys.exit(1)
data = run["data"]["resultData"]
if run.get("status") != "success" or data.get("error"):
    err = data.get("error") or {}
    print(f"✗ {name}: {err.get('message', run.get('status'))} (node: {data.get('lastNodeExecuted')})")
    sys.exit(1)
last = data["lastNodeExecuted"]
out = data["runData"][last][0]["data"]["main"][0]
print(f"✓ {name} → {last}: " + json.dumps([i["json"] for i in out][:6], ensure_ascii=False, default=str)[:400])
