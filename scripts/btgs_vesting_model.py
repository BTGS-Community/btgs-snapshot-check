#!/usr/bin/env python3
"""BTGS vesting model (read-only).
Shows what a gradual unlock for large holders would mean, for several thresholds:
how many wallets are affected, how much is locked, and how much BTGS is circulating
month by month after launch. Default model: 25% at launch, 75% unlocked in 12 equal
monthly steps, applied to wallets above the threshold.

Wallets = addresses spent together in one transaction (same owner), as in btgs_clusters.py.
Optional files (one address per line, # = comment):
  --exclude FILE  addresses left out of the snapshot (one address excludes its whole wallet)
  --exempt  FILE  addresses that never vest (e.g. confirmed exchange wallets)
Output: btgs-vesting-model.txt (keep it private: it lists the affected wallets)."""
import argparse, base64, json, os, time, urllib.request
from collections import defaultdict
from decimal import Decimal

class RPC:
    def __init__(self, port, cookie):
        with open(cookie) as f:
            auth = f.read().strip()
        self.url = f"http://127.0.0.1:{port}/"
        self.hdr = {"Authorization": "Basic " + base64.b64encode(auth.encode()).decode(),
                    "Content-Type": "application/json"}
        self.n = 0
    def __call__(self, method, *params):
        self.n += 1
        body = json.dumps({"jsonrpc": "1.0", "id": self.n, "method": method, "params": list(params)}).encode()
        with urllib.request.urlopen(urllib.request.Request(self.url, body, self.hdr), timeout=300) as r:
            res = json.loads(r.read(), parse_float=Decimal)
        if res.get("error"):
            raise RuntimeError(f"{method}: {res['error']}")
        return res["result"]

class UF:
    def __init__(self): self.p = {}
    def find(self, x):
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]; x = self.p[x]
        return x
    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb: self.p[rb] = ra

def owner(spk):
    return spk.get("address") or f"{spk.get('type','?')}:{spk.get('hex','')[:30]}"

def load_list(path):
    if not path: return set()
    with open(path) as f:
        return {l.split("#")[0].strip() for l in f if l.split("#")[0].strip()}

def fmt(x): return f"{x:,.0f}"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=18881)
    ap.add_argument("--datadir", default=os.path.expanduser("~/.btgs"))
    ap.add_argument("--height", type=int, default=0, help="snapshot height (default: chain tip)")
    ap.add_argument("--thresholds", default="25000,50000,100000")
    ap.add_argument("--now", type=float, default=0.25, help="share unlocked at launch (0.25 = 25%%)")
    ap.add_argument("--months", type=int, default=12)
    ap.add_argument("--exclude"); ap.add_argument("--exempt")
    a = ap.parse_args()
    rpc = RPC(a.port, os.path.join(a.datadir, ".cookie"))
    tip = a.height or rpc("getblockcount")
    exclude = load_list(a.exclude) | {"bcg1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqnmxfsvxdead"}
    exempt = load_list(a.exempt)
    print(f"Reading blocks 1-{tip} (read-only)...")

    out, uf, t0 = {}, UF(), time.time()
    for h in range(1, tip + 1):
        blk = rpc("getblock", rpc("getblockhash", h), 2)
        for tx in blk["tx"]:
            ins = []
            for v in tx["vin"]:
                if "coinbase" in v: continue
                o = out.pop((v["txid"], v["vout"]), None)
                if o: ins.append(o[1])
            for x in ins[1:]: uf.union(ins[0], x)
            for vo in tx["vout"]:
                val = Decimal(str(vo["value"]))
                if val > 0: out[(tx["txid"], vo["n"])] = (val, owner(vo["scriptPubKey"]))
        if h % 2000 == 0 or h == tip:
            print(f"  block {h}/{tip} ({time.time()-t0:.0f}s)", flush=True)

    bal = defaultdict(Decimal)
    for val, ad in out.values(): bal[ad] += val
    supply = sum(bal.values())
    # one excluded address excludes its whole wallet (same owner)
    bad = {uf.find(ad) for ad in exclude}
    free = {uf.find(ad) for ad in exempt}   # exempt works per wallet too, even if that address is empty now
    excluded = sum(v for ad, v in bal.items() if uf.find(ad) in bad)
    wallets = defaultdict(lambda: {"bal": Decimal(0), "addrs": [], "exempt": False})
    for ad, v in bal.items():
        if uf.find(ad) in bad or v <= 0: continue
        w = wallets[uf.find(ad)]
        w["bal"] += v; w["addrs"].append((v, ad))
        if uf.find(ad) in free: w["exempt"] = True
    snap = supply - excluded
    exempt_total = sum(w["bal"] for w in wallets.values() if w["exempt"])

    L = ["BTGS VESTING MODEL (read-only) - PRIVATE, lists affected wallets", "=" * 72,
         f"Snapshot height {tip}. Model: {a.now:.0%} at launch, the rest in {a.months} equal monthly steps,",
         "for every wallet (addresses spent together) above the threshold. Same rule for everyone.", "",
         f"Supply at height:           {fmt(supply)} BTGS",
         f"Left out (--exclude + burn): {fmt(excluded)} BTGS",
         f"In the snapshot:            {fmt(snap)} BTGS in {len(wallets)} wallets",
         f"Exempt wallets (--exempt):  {fmt(exempt_total)} BTGS", ""]
    for th in [Decimal(t) for t in a.thresholds.split(",")]:
        big = sorted((w for w in wallets.values() if w["bal"] >= th and not w["exempt"]), key=lambda w: -w["bal"])
        vest = sum(w["bal"] for w in big)
        locked0 = vest * Decimal(1 - a.now)
        L += [f"--- Threshold {fmt(th)} BTGS ---",
              f"Wallets affected: {len(big)}  holding {fmt(vest)} BTGS ({vest / snap * 100 if snap else 0:.1f}% of snapshot)",
              f"Locked at launch: {fmt(locked0)} BTGS  ->  circulating at launch: {fmt(snap - locked0)} ({(snap - locked0) / snap * 100 if snap else 0:.1f}%)",
              "Circulating from the snapshot, month by month (new mining not included):"]
        row = []
        for m in range(0, a.months + 1):
            still = locked0 * Decimal(a.months - m) / a.months
            row.append(f"m{m}: {fmt(snap - still)}")
        for i in range(0, len(row), 5): L.append("  " + "   ".join(row[i:i + 5]))
        L.append("Affected wallets (largest address shown):")
        for w in big:
            top = max(w["addrs"])
            flag = "  <- many addresses, check if exchange" if len(w["addrs"]) > 100 else ""
            L.append(f"  {fmt(w['bal']):>12} BTGS  {len(w['addrs']):>4} addr  {top[1]}{flag}")
        L.append("")
    L.append("Note: wallet grouping must be checked independently before it affects anyone's balance.")
    report = "\n".join(L)
    with open("btgs-vesting-model.txt", "w") as f:
        f.write(report + "\n")
    print("\n" + report + "\nSaved: btgs-vesting-model.txt")

if __name__ == "__main__":
    main()
