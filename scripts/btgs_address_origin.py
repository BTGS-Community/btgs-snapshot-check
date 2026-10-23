#!/usr/bin/env python3
"""BTGS address origin check (read-only). Same criteria for every address.
For each address you give it, shows where its coins came from:
  - mined directly (block rewards paid to this address)
  - received from others, and how much of that traces back to the block-1 premine
    (proportional "haircut" method: every transaction passes on the premine share of its inputs)
  - the biggest senders, with their own premine share
Usage:  python3 btgs_address_origin.py bcg1q... [bcg1q... more]"""
from btgs_rpcauth import get_auth
import argparse, base64, json, os, time, urllib.request
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
        with urllib.request.urlopen(urllib.request.Request(self.url, body, self.hdr), timeout=300) as r:
            res = json.loads(r.read(), parse_float=Decimal)
        if res.get("error"):
            raise RuntimeError(f"{method}: {res['error']}")
        return res["result"]

def owner(spk):
    return spk.get("address") or f"{spk.get('type','?')}:{spk.get('hex','')[:30]}"

def fmt(x): return f"{x:,.2f}"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("addresses", nargs="+")
    ap.add_argument("--port", type=int, default=18881)
    ap.add_argument("--datadir", default=os.path.expanduser("~/.btgs"))
    a = ap.parse_args()
    rpc = RPC(a.port, os.path.join(a.datadir, ".cookie"))
    targets = set(a.addresses)
    tip = rpc("getblockcount")
    print(f"Reading blocks 1-{tip} (read-only)...")

    out = {}              # (txid,n) -> [value, address, premine_share, mined_share]
    mined = defaultdict(Decimal); recv = defaultdict(Decimal); recv_pre = defaultdict(Decimal)
    sent = defaultdict(Decimal); first = {}; last = {}
    senders = defaultdict(lambda: defaultdict(lambda: [Decimal(0), Decimal(0), 0]))   # target -> sender -> [amount, premine part, count]
    t0 = time.time()
    for h in range(1, tip + 1):
        blk = rpc("getblock", rpc("getblockhash", h), 2)
        btime = time.strftime("%Y-%m-%d", time.gmtime(blk["time"]))
        for tx in blk["tx"]:
            if "coinbase" in tx["vin"][0]:
                pre, mine, in_addrs = (Decimal(1), Decimal(0), []) if h == 1 else (Decimal(0), Decimal(1), [])
                coinbase = True
            else:
                coinbase = False
                tot = tp = tm = Decimal(0); in_addrs = []
                for v in tx["vin"]:
                    o = out.pop((v["txid"], v["vout"]), None)
                    if o is None: continue
                    val, ad, p, m = o
                    tot += val; tp += val * p; tm += val * m
                    in_addrs.append(ad)
                    if ad in targets: sent[ad] += val
                pre = tp / tot if tot else Decimal(0)
                mine = tm / tot if tot else Decimal(0)
            for vo in tx["vout"]:
                val = Decimal(str(vo["value"]))
                if val == 0: continue
                ad = owner(vo["scriptPubKey"])
                out[(tx["txid"], vo["n"])] = [val, ad, pre, mine]
                if ad not in targets: continue
                if coinbase:
                    (mined if h != 1 else recv)[ad] += val
                    if h == 1: recv_pre[ad] += val
                elif ad in in_addrs:
                    continue                                  # own change, not new coins
                else:
                    recv[ad] += val; recv_pre[ad] += val * pre
                    s = max(set(in_addrs), key=in_addrs.count) if in_addrs else "?"
                    e = senders[ad][s]; e[0] += val; e[1] += val * pre; e[2] += 1
                first.setdefault(ad, (h, btime)); last[ad] = (h, btime)
        if h % 2000 == 0 or h == tip:
            print(f"  block {h}/{tip} ({time.time()-t0:.0f}s)", flush=True)

    bal = defaultdict(Decimal); bal_pre = defaultdict(Decimal)
    for val, ad, p, m in out.values():
        if ad in targets:
            bal[ad] += val; bal_pre[ad] += val * p

    L = ["BTGS ADDRESS ORIGIN CHECK (read-only)", "=" * 72,
         f"Chain tip {tip}. Premine = block 1 coinbase. 'Premine share' = proportional (haircut) method:",
         "each transaction passes on the premine share of the coins that went into it.",
         "A share near 0% means the coins came from mining/other sources; near 100% means from the premine.", ""]
    for ad in a.addresses:
        L.append(ad)
        if ad not in first:
            L += ["  never received anything", ""]; continue
        total_in = mined[ad] + recv[ad]
        L.append(f"  active: block {first[ad][0]} ({first[ad][1]}) to block {last[ad][0]} ({last[ad][1]})")
        L.append(f"  mined directly (block rewards): {fmt(mined[ad])} BTGS")
        L.append(f"  received from others:           {fmt(recv[ad])} BTGS")
        pct = (recv_pre[ad] / total_in * 100) if total_in else 0
        L.append(f"    of which traces to premine:   {fmt(recv_pre[ad])} BTGS ({pct:.1f}% of everything received)")
        L.append(f"  sent out:                       {fmt(sent[ad])} BTGS")
        bp = (bal_pre[ad] / bal[ad] * 100) if bal[ad] else 0
        L.append(f"  balance now:                    {fmt(bal[ad])} BTGS (premine share {bp:.1f}%)")
        top = sorted(senders[ad].items(), key=lambda kv: -kv[1][0])[:10]
        if top:
            L.append("  biggest senders (sending address, amount, premine share of it, times):")
            for s, (amt, p, c) in top:
                L.append(f"    {s}  {fmt(amt)} BTGS  premine {p / amt * 100 if amt else 0:.0f}%  x{c}")
        L.append("")
    report = "\n".join(L)
    with open("btgs-address-origin.txt", "w") as f:
        f.write(report + "\n")
    print("\n" + report + "\nSaved: btgs-address-origin.txt")

if __name__ == "__main__":
    main()
