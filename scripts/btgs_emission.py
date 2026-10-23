#!/usr/bin/env python3
"""BTGS actual emission check (read-only): real blocks/day vs the 144/day target."""
import base64, json, os, urllib.request, datetime
from btgs_rpcauth import get_auth
cookie = get_auth(os.path.expanduser("~/.btgs/.cookie"))
hdr = {"Authorization": "Basic " + base64.b64encode(cookie.encode()).decode(), "Content-Type": "application/json"}
def rpc(m, *p):
    req = urllib.request.Request("http://127.0.0.1:18881/", json.dumps({"id": 1, "method": m, "params": list(p)}).encode(), hdr)
    return json.loads(urllib.request.urlopen(req, timeout=60).read())["result"]
tip = rpc("getblockcount"); print(f"Reading {tip} block headers...")
t = {}
for h in range(1, tip + 1):
    t[h] = rpc("getblockheader", rpc("getblockhash", h))["time"]
    if h % 4000 == 0: print(f"  {h}/{tip}")
d = lambda ts: datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y-%m-%d")
def window(days):
    start = t[tip] - days * 86400
    return sum(1 for h in range(2, tip + 1) if t[h] >= start) / days
total_days = (t[tip] - t[2]) / 86400
avg = (tip - 1) / total_days
L = ["BTGS ACTUAL EMISSION (read-only)", "=" * 60,
     f"Block 2: {d(t[2])}   Tip {tip}: {d(t[tip])}   ({total_days:.1f} days)",
     "Target: 144 blocks/day = 7,200 BTGS/day", "",
     f"All-time average : {avg:6.1f} blocks/day = {avg*50:8,.0f} BTGS/day ({avg/144*100:.0f}% of target)"]
for days in (90, 30, 7):
    b = window(days)
    L.append(f"Last {days:2d} days     : {b:6.1f} blocks/day = {b*50:8,.0f} BTGS/day ({b/144*100:.0f}% of target)")
L += ["", "Per difficulty period (1008 blocks):", f"{'blocks':>13}  {'start':10}  {'days':>6}  {'blocks/day':>10}  {'BTGS/day':>9}"]
s = 1
while s < tip:
    e = min(s + 1007, tip)
    days = max((t[e] - t[s]) / 86400, 0.01); bpd = (e - s + 1) / days
    L.append(f"{s:6d}-{e:6d}  {d(t[s])}  {days:6.1f}  {bpd:10.1f}  {bpd*50:9,.0f}")
    s = e + 1
r = "\n".join(L); print("\n" + r)
open("btgs-emission.txt", "w").write(r + "\n")
print("\nSaved: btgs-emission.txt")
