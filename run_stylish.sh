#!/bin/bash
# HelloStylish — Run pipeline and show top 3 products
EMAIL="${1:-hellostylish2026@gmail.com}"
API="http://localhost:8000"
TMPFILE=$(mktemp)

echo "Creating run for $EMAIL..."
RUN_ID=$(curl -s -X POST "$API/api/runs" \
  -H "Content-Type: application/json" \
  -d "{\"email\": \"$EMAIL\", \"trigger\": \"manual\"}" | python3 -c "import json,sys; print(json.load(sys.stdin)['run_id'])")

echo "Run ID: $RUN_ID"
echo ""

while true; do
  curl -s "$API/api/runs/$RUN_ID" > "$TMPFILE"

  python3 - "$TMPFILE" <<'PYEOF'
import json, sys

with open(sys.argv[1]) as f:
    raw = f.read()

data = json.loads(raw, strict=False)
status = data["run"]["status"]
print(f"Status: {status}")
for s in data["steps"]:
    icons = {"SUCCEEDED": "done", "RUNNING": ">>>>", "PENDING": "    ", "QUEUED": "wait", "FAILED": "FAIL"}
    print(f'  [{icons.get(s["status"], "?")}] {s["step_key"]}')

if status in ("SUCCEEDED", "FAILED"):
    print()
    if status == "FAILED":
        for s in data["steps"]:
            if s.get("error"):
                print(f'Error in {s["step_key"]}: {s["error"]}')
        sys.exit(1)

    sb = [a for a in data["artifacts"] if a["kind"] == "style_brief"][0]["inline_json"]
    print(f'Gender:  {sb.get("gender", "N/A")}')
    print(f'Style:   {sb["style_summary"][:120]}...')
    print(f'Brands:  {", ".join(sb["recommended_brands"])}')
    print()

    rk = [a for a in data["artifacts"] if a["kind"] == "rank"][0]["inline_json"]
    co = [a for a in data["artifacts"] if a["kind"] == "checkout_draft"][0]["inline_json"]

    print("=== TOP 3 PRODUCTS ===")
    for i, item in enumerate(rk["ranked_items"][:3], 1):
        print(f'{i}. {item["title"]}')
        print(f'   Price: ${item["sale_price"]}  ({item["discount_pct"]}% off)')
        print(f'   Link:  {item["product_url"]}')
        print()

    total = sum(x["sale_price"] for x in co["checkout_draft"]["items"])
    print(f"Checkout total: ${total:.2f}")

PYEOF

  STATUS=$(python3 -c "
import json, sys
with open('$TMPFILE') as f: data = json.loads(f.read(), strict=False)
print(data['run']['status'])
" 2>/dev/null)

  if [ "$STATUS" = "SUCCEEDED" ] || [ "$STATUS" = "FAILED" ]; then break; fi
  echo "---"
  sleep 10
done

rm -f "$TMPFILE"
