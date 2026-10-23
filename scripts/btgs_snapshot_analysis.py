#!/usr/bin/env python3
"""
BTGS snapshot analysis (read-only).

Walks the whole chain through the local node's RPC, rebuilds the UTXO set,
and reports:
  - total supply in the UTXO set (cross-checked against gettxoutsetinfo)
  - balances per address, holder count, distribution, top holders
  - how much sits on address types that map 1:1 to a new chain (same key)
    vs. types that would need a claim / move before a snapshot
  - where the block-1 premine went (follows the coins forward)
  - balances of the frozen "burn" addresses

Nothing is written to the node. Output files go to the current directory:
  btgs-snapshot-report.txt   human-readable summary
  btgs-balances.csv          address,type,balance,utxos

Usage:
  python3 btgs_snapshot_analysis.py                       # mainnet defaults
  python3 btgs_snapshot_analysis.py --port 18881 --datadir ~/.btgs
"""
from btgs_rpcauth import get_auth
import argparse, base64, json, os, sys, time, urllib.request
from collections import defaultdict, deque
from decimal import Decimal

# ---------------------------------------------------------------- RPC
class RPC:
    def __init__(self, port, cookie):
        auth = get_auth(cookie)
        self.url = f"http://127.0.0.1:{port}/"
        self.hdr = {"Authorization": "Basic " + base64.b64encode(auth.encode()).decode(),
                    "Content-Type": "application/json"}
        self.n = 0

    def __call__(self, method, *params):
        self.n += 1
        body = json.dumps({"jsonrpc": "1.0", "id": self.n, "method": method,
                           "params": list(params)}).encode()
        req = urllib.request.Request(self.url, body, self.hdr)
        with urllib.request.urlopen(req, timeout=300) as r:
            res = json.loads(r.read(), parse_float=Decimal)
        if res.get("error"):
            raise RuntimeError(f"{method}: {res['error']}")
        return res["result"]

# ------------------------------------------------------------ helpers
# Types whose owner can be identified by a plain key (same private key works
# on a Dash-based chain as a normal G... P2PKH address)
MAPPABLE = {"pubkeyhash": "Legacy G... (P2PKH)",
            "witness_v0_keyhash": "SegWit bcg1q... (P2WPKH)",
            "pubkey": "Raw pubkey (P2PK)"}
NEEDS_CLAIM = {"witness_v1_taproot": "Taproot bcg1p...",
               "scripthash": "P2SH (script / multisig)",
               "witness_v0_scripthash": "P2WSH (script / multisig)",
               "multisig": "Bare multisig"}

FROZEN = {"bcg1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqnmxfsvxdead": "burn (P2WSH vanity)",
          "GYcEKCsBURScGnegzQpVRALB3DHEm6au3D": "frozen by consensus at 13874 (P2PKH, not a provable burn)"}

def owner(spk):
    """Return an identifier for who controls an output."""
    if spk.get("address"):
        return spk["address"]
    if spk.get("type") == "pubkey":          # P2PK has no address field
        return "pubkey:" + spk["asm"].split()[0]
    return f"{spk.get('type','?')}:{spk.get('hex','')[:40]}"

def fmt(x):
    return f"{x:,.8f}".rstrip("0").rstrip(".")

# --------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=18881)
    ap.add_argument("--datadir", default=os.path.expanduser("~/.btgs"))
    ap.add_argument("--cookie", help="path to .cookie (default: <datadir>/.cookie)")
    ap.add_argument("--trace-hops", type=int, default=50,
                    help="how many transaction hops to follow the premine")
    ap.add_argument("--height", type=int, default=0, help="stop at this block height (default: chain tip)")
    a = ap.parse_args()

    rpc = RPC(a.port, a.cookie or os.path.join(a.datadir, ".cookie"))
    info = rpc("getblockchaininfo")
    tip = a.height or info["blocks"]
    print(f"Chain: {info['chain']}  tip: {tip}  — walking all blocks (read-only)...")

    utxo = {}                     # (txid, n) -> (value, owner, type, height)
    spent_by = {}                 # (txid, n) -> spending txid
    tx_outs = {}                  # txid -> list of (n, value, owner, type)   (only kept for traced txs)
    tx_ins = defaultdict(list)    # txid -> list of (prev_txid, prev_n)
    premine_outs = []
    t0 = time.time()

    for h in range(1, tip + 1):   # genesis coinbase is unspendable, skip it
        blk = rpc("getblock", rpc("getblockhash", h), 2)
        for tx in blk["tx"]:
            txid = tx["txid"]
            for vin in tx["vin"]:
                if "coinbase" in vin:
                    continue
                key = (vin["txid"], vin["vout"])
                utxo.pop(key, None)
                spent_by[key] = txid
                tx_ins[txid].append(key)
            outs = []
            for vo in tx["vout"]:
                spk = vo["scriptPubKey"]
                t = spk.get("type", "?")
                if t == "nulldata":                 # OP_RETURN, unspendable
                    continue
                o = owner(spk)
                v = Decimal(vo["value"])
                utxo[(txid, vo["n"])] = (v, o, t, h)
                outs.append((vo["n"], v, o, t))
            tx_outs[txid] = outs
            if h == 1 and "coinbase" in tx["vin"][0]:
                premine_outs = [(txid, n, v, o, t) for n, v, o, t in outs]
        if h % 1000 == 0 or h == tip:
            print(f"  block {h}/{tip}  ({time.time()-t0:.0f}s)", flush=True)

    # ---- totals & cross-check
    total = sum(v for v, _, _, _ in utxo.values())
    if a.height:
        # fixed height: the node's live UTXO set is at its own tip, so check the
        # emission schedule instead (2.5M premine + 50 BTGS per block from block 2)
        node_total = Decimal(2500000) + Decimal(50) * (tip - 1)
        check_label = "expected"
    else:
        node_total = Decimal(rpc("gettxoutsetinfo")["total_amount"])
        check_label = "node"

    # ---- per owner
    bal = defaultdict(Decimal); cnt = defaultdict(int); typ = {}
    for v, o, t, _ in utxo.values():
        bal[o] += v; cnt[o] += 1; typ[o] = t
    holders = {o: b for o, b in bal.items() if b > 0}

    by_type = defaultdict(lambda: [Decimal(0), 0, 0])   # amount, utxos, owners
    for o, b in holders.items():
        by_type[typ[o]][0] += b; by_type[typ[o]][2] += 1
    for v, o, t, _ in utxo.values():
        by_type[t][1] += 1

    # ---- premine trace (follow coins forward, BFS over spending txs)
    trace_lines = []
    tainted_unspent = defaultdict(Decimal)
    seen = set()
    q = deque((txid, n, 0) for txid, n, *_ in premine_outs)
    while q:
        txid, n, hop = q.popleft()
        key = (txid, n)
        if key in seen:
            continue
        seen.add(key)
        if key in utxo:
            v, o, t, _ = utxo[key]
            tainted_unspent[o] += v
            continue
        nxt = spent_by.get(key)
        if nxt and hop < a.trace_hops:
            for n2, v2, o2, t2 in tx_outs.get(nxt, []):
                q.append((nxt, n2, hop + 1))

    # ---- report
    L = []
    w = L.append
    w("BTGS SNAPSHOT ANALYSIS (read-only)")
    w("=" * 60)
    w(f"Chain tip analysed : {tip}")
    w(f"UTXO total (script): {fmt(total)} BTGS")
    w(f"UTXO total ({check_label}){' ' if check_label=='node' else ''}: {fmt(node_total)} BTGS   "
      f"{'OK - matches' if abs(total-node_total) < Decimal('0.00000001') else '!! MISMATCH - check'}")
    w(f"Unspent outputs    : {len(utxo):,}")
    w(f"Holders (balance>0): {len(holders):,}")
    w("")
    w("BY ADDRESS TYPE")
    w("-" * 60)
    mapp = Decimal(0); claim = Decimal(0); other = Decimal(0)
    for t, (amt, nu, no) in sorted(by_type.items(), key=lambda x: -x[1][0]):
        label = MAPPABLE.get(t) or NEEDS_CLAIM.get(t) or t
        group = "maps automatically" if t in MAPPABLE else ("needs move/claim" if t in NEEDS_CLAIM else "other")
        if t in MAPPABLE: mapp += amt
        elif t in NEEDS_CLAIM: claim += amt
        else: other += amt
        w(f"  {label:32s} {fmt(amt):>22s} BTGS  {no:>6,} holders  [{group}]")
    w("")
    if total:
        w(f"  Maps automatically (same key) : {fmt(mapp)} BTGS ({mapp/total*100:.2f}%)")
        w(f"  Needs move/claim first        : {fmt(claim)} BTGS ({claim/total*100:.2f}%)")
        if other: w(f"  Other / non-standard          : {fmt(other)} BTGS ({other/total*100:.2f}%)")
    w("")
    w("DISTRIBUTION")
    w("-" * 60)
    buckets = [(0, 1), (1, 100), (100, 1_000), (1_000, 10_000), (10_000, 100_000),
               (100_000, 1_000_000), (1_000_000, 10**12)]
    for lo, hi in buckets:
        sel = [b for b in holders.values() if lo <= b < hi]
        w(f"  {fmt(Decimal(lo)):>10s} - {('∞' if hi >= 10**12 else fmt(Decimal(hi))):>10s} BTGS : "
          f"{len(sel):>6,} holders  {fmt(sum(sel, Decimal(0))):>22s} BTGS")
    w("")
    w("TOP 25 HOLDERS")
    w("-" * 60)
    for i, (o, b) in enumerate(sorted(holders.items(), key=lambda x: -x[1])[:25], 1):
        pct = b / total * 100 if total else 0
        tag = "  <- FROZEN" if o in FROZEN else ("  <- holds premine coins" if o in tainted_unspent else "")
        w(f"  {i:2d}. {o:62s} {fmt(b):>20s}  {pct:5.2f}%  {MAPPABLE.get(typ[o]) or NEEDS_CLAIM.get(typ[o]) or typ[o]}{tag}")
    w("")
    w("PREMINE (block 1 coinbase)")
    w("-" * 60)
    for txid, n, v, o, t in premine_outs:
        st = "UNSPENT" if (txid, n) in utxo else f"spent in {spent_by.get((txid, n), '?')}"
        w(f"  output {n}: {fmt(v)} BTGS -> {o}  [{st}]")
    w(f"  Followed forward up to {a.trace_hops} transaction hops.")
    w(f"  Premine-derived coins still unspent: {fmt(sum(tainted_unspent.values(), Decimal(0)))} BTGS "
      f"across {len(tainted_unspent)} addresses")
    w("  (Coins mixed with other funds count fully here - upper bound, not exact.)")
    for o, b in sorted(tainted_unspent.items(), key=lambda x: -x[1])[:25]:
        w(f"    {o:62s} {fmt(b):>20s}{'  <- FROZEN' if o in FROZEN else ''}")
    w("")
    w("FROZEN ADDRESSES")
    w("-" * 60)
    for addr, note in FROZEN.items():
        w(f"  {addr}  {fmt(bal.get(addr, Decimal(0)))} BTGS  ({note})")
    report = "\n".join(L)

    with open("btgs-snapshot-report.txt", "w") as f:
        f.write(report + "\n")
    with open("btgs-balances.csv", "w") as f:
        f.write("address,type,balance,utxos\n")
        for o, b in sorted(holders.items(), key=lambda x: -x[1]):
            f.write(f"{o},{typ[o]},{b},{cnt[o]}\n")
    print("\n" + report)
    print("\nSaved: btgs-snapshot-report.txt, btgs-balances.csv")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(1)
