#!/usr/bin/env python3
"""BTGS top-miner check (read-only): when did the biggest mining wallets mine,
and did their coins flow into the premine-path transactions?"""
from btgs_rpcauth import get_auth
import argparse, base64, json, os, time, urllib.request, datetime
from collections import defaultdict
from decimal import Decimal

class RPC:
    def __init__(self, port, cookie):
        auth = get_auth(cookie)
        self.url = f"http://127.0.0.1:{port}/"
        self.hdr = {"Authorization": "Basic " + base64.b64encode(auth.encode()).decode(),
                    "Content-Type": "application/json"}
        self.n = 0
    def __call__(self, method, *params):
        self.n += 1
        body = json.dumps({"jsonrpc": "1.0", "id": self.n, "method": method, "params": list(params)}).encode()
        req = urllib.request.Request(self.url, body, self.hdr)
        with urllib.request.urlopen(req, timeout=300) as r:
            res = json.loads(r.read(), parse_float=Decimal)
        if res.get("error"):
            raise RuntimeError(f"{method}: {res['error']}")
        return res["result"]

def owner(spk):
    if spk.get("address"):
        return spk["address"]
    if spk.get("type") == "pubkey":
        return "pubkey:" + spk["asm"].split()[0]
    return f"{spk.get('type','?')}:{spk.get('hex','')[:40]}"

def fmt(x):
    return f"{x:,.8f}".rstrip("0").rstrip(".")

class UF:
    def __init__(self): self.p = {}
    def find(self, x):
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]; x = self.p[x]
        return x
    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:                    # root = smallest address, so the result
            if rb < ra: ra, rb = rb, ra  # never depends on processing order
            self.p[rb] = ra

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=18881)
    ap.add_argument("--datadir", default=os.path.expanduser("~/.btgs"))
    ap.add_argument("--cookie")
    ap.add_argument("--top", type=int, default=6)
    ap.add_argument("--branch-min", type=Decimal, default=Decimal(50000))
    ap.add_argument("--height", type=int, default=0, help="stop at this block height (default: chain tip)")
    a = ap.parse_args()
    rpc = RPC(a.port, a.cookie or os.path.join(a.datadir, ".cookie"))
    tip = a.height or rpc("getblockchaininfo")["blocks"]
    print(f"Walking {tip} blocks (read-only)...")
    out = {}; spent_by = {}; tx = {}; unspent = set(); premine = None
    mined_blocks = defaultdict(list)   # address -> [(height, time, value)]
    btime = {}
    uf = UF(); t0 = time.time()
    for h in range(1, tip + 1):
        blk = rpc("getblock", rpc("getblockhash", h), 2); btime[h] = blk["time"]
        for t in blk["tx"]:
            txid = t["txid"]; ins = []; is_cb = "coinbase" in t["vin"][0]
            for vin in t["vin"]:
                if "coinbase" in vin: continue
                k = (vin["txid"], vin["vout"]); ins.append(k)
                spent_by[k] = txid; unspent.discard(k)
            first = None
            for ad in sorted({out[k][1] for k in ins}):
                uf.find(ad)
                if first is None: first = ad
                else: uf.union(first, ad)
            outs = []
            for vo in t["vout"]:
                spk = vo["scriptPubKey"]
                if spk.get("type") == "nulldata": continue
                k = (txid, vo["n"]); o = owner(spk); v = Decimal(vo["value"])
                out[k] = (v, o); uf.find(o); unspent.add(k); outs.append(vo["n"])
            if is_cb and h > 1 and outs:
                cbv = sum(out[(txid, n)][0] for n in outs)
                mined_blocks[out[(txid, outs[0])][1]].append((h, blk["time"], cbv))
            tx[txid] = {"h": h, "ins": ins, "outs": outs}
            if h == 1 and is_cb:
                premine = [(txid, n) for n in outs]
        if h % 4000 == 0 or h == tip:
            print(f"  block {h}/{tip} ({time.time()-t0:.0f}s)", flush=True)

    # premine path (same rules as btgs_peel_follow.py) -> collect "other source" inputs
    path_tx = set(); path_keys = set()
    branches = list(premine)
    while branches:
        key = branches.pop(0)
        while key not in unspent and key in spent_by:
            path_keys.add(key)
            nxt = spent_by[key]
            if nxt in path_tx: break
            path_tx.add(nxt)
            outs = sorted(((out[(nxt, n)][0], n) for n in tx[nxt]["outs"]), reverse=True)
            for x, n in outs[1:]:
                if x >= a.branch_min: branches.append((nxt, n))
            key = (nxt, outs[0][1])
    other_in = defaultdict(Decimal)   # cluster root -> value fed into premine-path txs
    for t in path_tx:
        for k in tx[t]["ins"]:
            if k not in path_keys:
                other_in[uf.find(out[k][1])] += out[k][0]

    clusters = defaultdict(list)
    for ad in mined_blocks:
        clusters[uf.find(ad)].extend(mined_blocks[ad])
    ranked = sorted(clusters.items(), key=lambda kv: (-sum(v for _, _, v in kv[1]), kv[0]))

    def d(ts): return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y-%m-%d %H:%M")
    L = []; w = L.append
    w("BTGS TOP MINERS - TIMING & LINK TO PREMINE (read-only)"); w("=" * 72)
    w(f"Chain launch: block 2 at {d(btime[2])}.  Tip {tip} at {d(btime[tip])}")
    w(f"Coins fed into premine-path transactions from other wallets: {fmt(sum(other_in.values(), Decimal(0)))} BTGS")
    w("")
    buckets = list(range(1, tip + 1, 1000))
    for i, (root, blks) in enumerate(ranked[:a.top], 1):
        blks.sort()
        total = sum(v for _, _, v in blks)
        w(f"#{i}  mined {fmt(total)} BTGS in {len(blks)} blocks")
        w(f"    first block {blks[0][0]} ({d(blks[0][1])})   last block {blks[-1][0]} ({d(blks[-1][1])})")
        hist = defaultdict(int)
        for h, _, _ in blks: hist[(h - 1) // 1000] += 1
        w("    blocks per 1000-block range: " + "  ".join(
            f"{b*1000+1}-{(b+1)*1000}:{hist[b]}" for b in sorted(hist)))
        share_first = sum(1 for h, _, _ in blks if h <= 5400)
        w(f"    share of blocks 2-5400 mined by this wallet: {share_first/5399*100:.1f}%")
        fed = other_in.get(root, Decimal(0))
        w(f"    coins this wallet sent INTO premine-path transactions: {fmt(fed)} BTGS"
          + ("   <-- LINKED TO PREMINE PATH" if fed > 0 else ""))
        w("")
    w("WALLETS THAT FED COINS INTO THE PREMINE PATH (top 10)")
    w("-" * 72)
    mined_by_root = {r: sum(v for _, _, v in b) for r, b in clusters.items()}
    for r, v in sorted(other_in.items(), key=lambda x: (-x[1], x[0]))[:10]:
        w(f"  {r[:24]}...  fed {fmt(v):>16s} BTGS   this wallet mined {fmt(mined_by_root.get(r, Decimal(0)))} BTGS")
    report = "\n".join(L)
    with open("btgs-miners.txt", "w") as f: f.write(report + "\n")
    print("\n" + report + "\n\nSaved: btgs-miners.txt")

if __name__ == "__main__":
    main()
