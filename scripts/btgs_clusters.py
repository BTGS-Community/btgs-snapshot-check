#!/usr/bin/env python3
"""BTGS wallet cluster analysis (read-only).
Groups addresses that were spent together in one transaction (common-input
heuristic = same wallet), then reports the wallet(s) linked to the premine,
how much they hold now and how much they mined."""
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
        if ra != rb: self.p[ra] = rb

FROZEN = {"bcg1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqnmxfsvxdead",
          "GYcEKCsBURScGnegzQpVRALB3DHEm6au3D"}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=18881)
    ap.add_argument("--datadir", default=os.path.expanduser("~/.btgs"))
    ap.add_argument("--cookie")
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--height", type=int, default=0, help="stop at this block height (default: chain tip)")
    a = ap.parse_args()
    rpc = RPC(a.port, a.cookie or os.path.join(a.datadir, ".cookie"))
    tip = a.height or rpc("getblockchaininfo")["blocks"]
    print(f"Walking {tip} blocks (read-only)...")
    out = {}; spent_by = {}; tx = {}; unspent = set(); premine = None
    mined = defaultdict(Decimal); blocks_mined = defaultdict(int)
    uf = UF(); t0 = time.time()
    for h in range(1, tip + 1):
        blk = rpc("getblock", rpc("getblockhash", h), 2)
        for t in blk["tx"]:
            txid = t["txid"]; ins = []; is_cb = "coinbase" in t["vin"][0]
            for vin in t["vin"]:
                if "coinbase" in vin: continue
                k = (vin["txid"], vin["vout"]); ins.append(k)
                spent_by[k] = txid; unspent.discard(k)
            in_addrs = {out[k][1] for k in ins}
            first = None
            for ad in in_addrs:                 # common-input heuristic
                uf.find(ad)
                if first is None: first = ad
                else: uf.union(first, ad)
            outs = []
            for vo in t["vout"]:
                spk = vo["scriptPubKey"]
                if spk.get("type") == "nulldata": continue
                k = (txid, vo["n"]); o = owner(spk); v = Decimal(vo["value"])
                out[k] = (v, o); uf.find(o); unspent.add(k); outs.append(vo["n"])
                if is_cb and h > 1:
                    mined[o] += v
            if is_cb and h > 1 and outs:
                blocks_mined[out[(txid, outs[0])][1]] += 1
            tx[txid] = {"h": h, "ins": ins, "outs": outs}
            if h == 1 and is_cb:
                premine = [(txid, n) for n in outs]
        if h % 4000 == 0 or h == tip:
            print(f"  block {h}/{tip} ({time.time()-t0:.0f}s)", flush=True)

    balance = defaultdict(Decimal)
    for k in unspent:
        v, o = out[k]; balance[o] += v

    # cluster stats
    members = defaultdict(list)
    for ad in list(uf.p):
        members[uf.find(ad)].append(ad)
    def stats(root):
        ads = members[root]
        return (sum((balance[x] for x in ads), Decimal(0)),
                sum((mined[x] for x in ads), Decimal(0)),
                sum(blocks_mined[x] for x in ads), ads)

    # follow the premine "keep" path (largest output = change) to find related clusters
    path_addrs = []
    key = premine[0]
    seen = set()
    while key not in unspent and key in spent_by and spent_by[key] not in seen:
        nxt = spent_by[key]; seen.add(nxt)
        outs = sorted(((out[(nxt, n)][0], n) for n in tx[nxt]["outs"]), reverse=True)
        key = (nxt, outs[0][1]); path_addrs.append(out[key][1])

    L = []; w = L.append
    total_mined = sum(mined.values(), Decimal(0))
    total_supply = sum(balance.values(), Decimal(0))
    w("BTGS WALLET CLUSTER ANALYSIS (read-only)")
    w("=" * 72)
    w("Method: addresses spent together as inputs of one transaction = same wallet.")
    w(f"Chain tip {tip}.  Mined after block 1: {fmt(total_mined)} BTGS.  Supply in UTXO: {fmt(total_supply)} BTGS")
    w("")

    pre_addr = out[premine[0]][1]
    rootA = uf.find(pre_addr)
    bal, mn, nb, ads = stats(rootA)
    w("WALLET A - the wallet that held the premine (strong link)")
    w("-" * 72)
    w(f"  addresses: {len(ads)}   balance now: {fmt(bal)} BTGS   mined: {fmt(mn)} BTGS in {nb} blocks"
      f" ({(mn/total_mined*100 if total_mined else 0):.1f}% of all mined)")
    for x in sorted(ads, key=lambda x: -balance[x])[:20]:
        if balance[x] > 0 or mined[x] > 0:
            w(f"    {x:62s} bal {fmt(balance[x]):>16s}  mined {fmt(mined[x]):>14s}{'  FROZEN/BURN' if x in FROZEN else ''}")
    w("")

    roots_B = []
    for x in path_addrs:
        r = uf.find(x)
        if r != rootA and r not in roots_B:
            roots_B.append(r)
    w("WALLETS ON THE PREMINE PATH but not linked to wallet A by inputs (medium link)")
    w("-" * 72)
    tb = tm = Decimal(0)
    for r in roots_B:
        bal, mn, nb, ads = stats(r)
        tb += bal; tm += mn
        top = sorted(ads, key=lambda x: -balance[x])[0]
        w(f"  wallet {r[:20]}...  {len(ads):4d} addrs  bal {fmt(bal):>16s}  mined {fmt(mn):>14s}  biggest: {top} ({fmt(balance[top])})")
    w(f"  total: balance {fmt(tb)} BTGS, mined {fmt(tm)} BTGS")
    w("")

    w(f"TOP {a.top} WALLETS ON THE CHAIN (by balance now)")
    w("-" * 72)
    allroots = sorted(members, key=lambda r: -stats(r)[0])
    for i, r in enumerate(allroots[:a.top], 1):
        bal, mn, nb, ads = stats(r)
        tag = "  <- WALLET A (premine)" if r == rootA else ("  <- on premine path" if r in roots_B else "")
        top = sorted(ads, key=lambda x: -balance[x])[0]
        w(f"  {i:2d}. {len(ads):4d} addrs  bal {fmt(bal):>16s} ({bal/total_supply*100:5.2f}%)  mined {fmt(mn):>14s} ({nb} blk){tag}")
        w(f"        biggest address: {top}{'  FROZEN/BURN' if top in FROZEN else ''}")
    w("")
    w(f"TOP {a.top} MINING WALLETS (all-time)")
    w("-" * 72)
    for i, r in enumerate(sorted(members, key=lambda r: -stats(r)[1])[:a.top], 1):
        bal, mn, nb, ads = stats(r)
        tag = "  <- WALLET A (premine)" if r == rootA else ("  <- on premine path" if r in roots_B else "")
        w(f"  {i:2d}. mined {fmt(mn):>14s} ({mn/total_mined*100:5.1f}%) in {nb:6d} blocks  bal now {fmt(bal):>16s}  {len(ads)} addrs{tag}")
    report = "\n".join(L)
    with open("btgs-clusters.txt", "w") as f:
        f.write(report + "\n")
    with open("btgs-cluster-members.csv", "w") as f:
        f.write("wallet,address,balance,mined\n")
        for r in allroots:
            for x in members[r]:
                if balance[x] > 0 or mined[x] > 0:
                    tag = "A" if r == rootA else ("PATH" if r in roots_B else "")
                    f.write(f"{r[:20]}{'-'+tag if tag else ''},{x},{balance[x]},{mined[x]}\n")
    print("\n" + report + "\n\nSaved: btgs-clusters.txt, btgs-cluster-members.csv")

if __name__ == "__main__":
    main()
