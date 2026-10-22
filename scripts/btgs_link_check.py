#!/usr/bin/env python3
"""BTGS link check (read-only): do the coins of the #1 miner wallet reach the
wallet that fed the most coins into the premine path?  Works on wallet level
(addresses spent together = one wallet) and shows the money flow paths."""
import argparse, base64, json, os, time, urllib.request
from collections import defaultdict, deque
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

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=18881)
    ap.add_argument("--datadir", default=os.path.expanduser("~/.btgs"))
    ap.add_argument("--cookie")
    ap.add_argument("--branch-min", type=Decimal, default=Decimal(50000))
    ap.add_argument("--max-hops", type=int, default=4)
    ap.add_argument("--height", type=int, default=0, help="stop at this block height (default: chain tip)")
    a = ap.parse_args()
    rpc = RPC(a.port, a.cookie or os.path.join(a.datadir, ".cookie"))
    tip = a.height or rpc("getblockchaininfo")["blocks"]
    print(f"Walking {tip} blocks (read-only)...")
    out = {}; spent_by = {}; tx = {}; unspent = set(); premine = None
    mined = defaultdict(Decimal); uf = UF(); t0 = time.time()
    txlist = []
    for h in range(1, tip + 1):
        blk = rpc("getblock", rpc("getblockhash", h), 2)
        for t in blk["tx"]:
            txid = t["txid"]; ins = []; is_cb = "coinbase" in t["vin"][0]
            for vin in t["vin"]:
                if "coinbase" in vin: continue
                k = (vin["txid"], vin["vout"]); ins.append(k)
                spent_by[k] = txid; unspent.discard(k)
            first = None
            for ad in {out[k][1] for k in ins}:
                uf.find(ad)
                if first is None: first = ad
                else: uf.union(first, ad)
            outs = []
            for vo in t["vout"]:
                spk = vo["scriptPubKey"]
                if spk.get("type") == "nulldata": continue
                k = (txid, vo["n"]); o = owner(spk); v = Decimal(vo["value"])
                out[k] = (v, o); uf.find(o); unspent.add(k); outs.append(vo["n"])
                if is_cb and h > 1: mined[o] += v
            tx[txid] = {"h": h, "ins": ins, "outs": outs}
            if ins: txlist.append(txid)
            if h == 1 and is_cb: premine = [(txid, n) for n in outs]
        if h % 4000 == 0 or h == tip:
            print(f"  block {h}/{tip} ({time.time()-t0:.0f}s)", flush=True)

    members = defaultdict(list)
    for ad in list(uf.p): members[uf.find(ad)].append(ad)
    mined_c = defaultdict(Decimal)
    for ad, v in mined.items(): mined_c[uf.find(ad)] += v
    M = max(mined_c, key=lambda r: mined_c[r])          # #1 miner wallet

    # premine path + feeder wallets (same rules as btgs_miners.py)
    path_tx = set(); path_keys = set(); branches = list(premine)
    while branches:
        key = branches.pop(0)
        while key not in unspent and key in spent_by:
            path_keys.add(key); nxt = spent_by[key]
            if nxt in path_tx: break
            path_tx.add(nxt)
            outs = sorted(((out[(nxt, n)][0], n) for n in tx[nxt]["outs"]), reverse=True)
            for x, n in outs[1:]:
                if x >= a.branch_min: branches.append((nxt, n))
            key = (nxt, outs[0][1])
    fed = defaultdict(Decimal)
    for t in path_tx:
        for k in tx[t]["ins"]:
            if k not in path_keys: fed[uf.find(out[k][1])] += out[k][0]
    if not fed:
        print("No other wallets fed coins into the premine path - nothing to check."); return
    F = max(fed, key=lambda r: fed[r])                   # biggest feeder wallet
    path_wallets = {uf.find(out[k][1]) for k in path_keys}

    # wallet-to-wallet money flow graph
    edge = defaultdict(Decimal); first_h = {}
    for txid in txlist:
        src = uf.find(out[tx[txid]["ins"][0]][1])
        for n in tx[txid]["outs"]:
            v, o = out[(txid, n)]; dst = uf.find(o)
            if dst != src:
                edge[(src, dst)] += v
                first_h.setdefault((src, dst), tx[txid]["h"])
    nbrs = defaultdict(list)
    for (s, d_), v in edge.items(): nbrs[s].append((d_, v))

    def label(r):
        n = len(members[r]); tags = []
        if r == M: tags.append("#1 MINER")
        if r == F: tags.append("TOP PREMINE FEEDER")
        if r in path_wallets: tags.append("PREMINE PATH")
        if n >= 200: tags.append(f"big wallet {n} addrs - maybe exchange/pool")
        return f"{r[:22]}... ({n} addrs)" + (f" [{', '.join(tags)}]" if tags else "")

    L = []; w = L.append
    w("BTGS LINK CHECK: #1 miner -> premine (read-only)"); w("=" * 72)
    w(f"#1 miner wallet : {label(M)}  mined {fmt(mined_c[M])} BTGS")
    w(f"Top feeder      : {label(F)}  fed {fmt(fed[F])} BTGS into the premine path")
    if M == F:
        w("  >>> The #1 miner and the top feeder are THE SAME WALLET (spent together) <<<")
    if M in path_wallets:
        w("  >>> The #1 miner wallet itself appears on the premine path <<<")
    w("")
    w(f"Direct transfers #1 miner -> top feeder : {fmt(edge.get((M, F), Decimal(0)))} BTGS"
      + (f" (first at block {first_h[(M, F)]})" if (M, F) in edge else ""))
    direct_path = sum((v for (s, d_), v in edge.items() if s == M and d_ in path_wallets), Decimal(0))
    w(f"Direct transfers #1 miner -> any premine-path wallet : {fmt(direct_path)} BTGS")
    w("")
    # BFS shortest paths M -> F (and M -> premine path), with bottleneck amounts
    def paths_to(targets, limit=5):
        res = []; q = deque([(M, [M], None)]); seen = {M: 0}
        while q and len(res) < limit:
            node, p, bott = q.popleft()
            if len(p) - 1 >= a.max_hops: continue
            for d_, v in sorted(nbrs[node], key=lambda x: -x[1]):
                b = v if bott is None else min(bott, v)
                if d_ in targets:
                    res.append((p + [d_], b))
                    if len(res) >= limit: break
                if d_ not in seen or seen[d_] > len(p):
                    seen[d_] = len(p); q.append((d_, p + [d_], b))
        return res
    w(f"MONEY FLOW PATHS #1 miner -> top feeder (up to {a.max_hops} wallet hops)")
    w("-" * 72)
    ps = paths_to({F})
    if not ps: w("  none found")
    for p, b in ps:
        w(f"  {len(p)-1} hop(s), smallest step {fmt(b)} BTGS:")
        for i in range(len(p) - 1):
            w(f"     {label(p[i])}\n       --{fmt(edge[(p[i], p[i+1])])} BTGS (from block {first_h[(p[i], p[i+1])]})-->")
        w(f"     {label(p[-1])}")
    w("")
    w("WHERE THE #1 MINER SENT ITS COINS (top 12 receiving wallets)")
    w("-" * 72)
    for d_, v in sorted(nbrs[M], key=lambda x: -x[1])[:12]:
        w(f"  {fmt(v):>16s} BTGS -> {label(d_)}  (first at block {first_h[(M, d_)]})")
    w("")
    w("WHERE THE TOP FEEDER GOT ITS COINS (top 12 sending wallets)")
    w("-" * 72)
    inc = sorted(((s, v) for (s, d_), v in edge.items() if d_ == F), key=lambda x: -x[1])[:12]
    for s, v in inc:
        w(f"  {fmt(v):>16s} BTGS <- {label(s)}  (first at block {first_h[(s, F)]})")
    w(f"  (feeder also mined {fmt(mined_c[F])} BTGS itself)")
    report = "\n".join(L)
    with open("btgs-link-check.txt", "w") as f: f.write(report + "\n")
    print("\n" + report + "\n\nSaved: btgs-link-check.txt")

if __name__ == "__main__":
    main()
