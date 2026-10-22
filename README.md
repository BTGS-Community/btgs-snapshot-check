# BTGS snapshot check

Check the BTGS premine and snapshot numbers yourself, on your own node. You don't have to trust anyone.

All scripts only **read** from your node. They never send transactions and never touch a wallet. Every script stops at the same fixed block height, so everyone who runs them gets **exactly the same result files**.

## What you need

- A synced BTGS node on the **existing chain** (built from [btgs-core-old](https://github.com/BTGS-Community/btgs-core-old), commit `6f988e8`), with RPC on (`server=1`)
- Python 3.8 or newer (no extra packages)
- Linux or macOS. On Windows, use WSL.

## Run it

Your node must be synced past block 17416. Check with `bitcoin-cli getblockcount`.

**1. Get the files** (pick one):

```
git clone https://github.com/BTGS-Community/btgs-snapshot-check
cd btgs-snapshot-check
```

or download the ZIP from GitHub (Code > Download ZIP), then:

```
python3 -m zipfile -e btgs-snapshot-check-main.zip .
cd btgs-snapshot-check-main
```

**2. Run the check:**

```
bash run_all.sh 17416
```

If your node uses another data folder or RPC port:

```
bash run_all.sh 17416 /path/to/.btgs 18881
```

It takes about 7–10 minutes in total. You see each script and its progress as it runs.

**3. Read the last line:**

| Last line | Meaning |
| --- | --- |
| `MATCH` | You got exactly the same results as the reference, down to the last digit. |
| `Some files differ` | Something is different. Check that your node is on the official chain (`getchaintips` shows one "active" chain), then post your `SHA256SUMS.txt` in the BTGS Discord. |
| `No reference results for block ... yet` | Nobody has published results for that height yet. Share your `SHA256SUMS.txt`. |

The report also has a supply check. It should say:

```
UTXO total (script): 3,370,750 BTGS
UTXO total (expected): 3,370,750 BTGS   OK - matches
```

Running a script on its own? Start it with `PYTHONHASHSEED=0 python3 scripts/<name>.py --height 17416`, or the order of some lists (and so the fingerprints) can change between runs. `run_all.sh` does this for you.

Your result files are in the `results-17416` folder. The fingerprints of all files are in `results-17416/SHA256SUMS.txt`.

## What each script shows

| Script | Shows |
| --- | --- |
| `btgs_snapshot_analysis.py` | total supply, all holders, address types, which addresses carry over automatically |
| `btgs_peel_follow.py` | where the 2.5M premine went, transaction by transaction |
| `btgs_premine_tree.py` | the premine as a tree, hop by hop |
| `btgs_clusters.py` | addresses spent together (= one wallet), wallets on the premine path |
| `btgs_miners.py` | when the biggest miners mined, and coins that went into the premine path |
| `btgs_link_check.py` | whether coins of the top miner reached the premine wallets |
| `btgs_multisig_check.py` | big multisig addresses and how many signatures they need |
| `btgs_vesting_model.py` | what a gradual unlock for large holders would mean (a model, not a decision) |

Extra tools, not part of the check:

- `btgs_address_origin.py <address>`: where one address's coins came from. Same method for every address.
- `btgs_emission.py`: real blocks per day compared to the target.

## Inputs

`inputs/vesting-exclude.txt` and `inputs/vesting-exempt.txt` are **draft** lists used by the vesting model. They are not a decision. The snapshot rules are decided by community vote.

## Important

- These numbers show **which coins moved where**. They do not say who a person is.
- Wallet grouping (addresses spent together) is a standard method, but it is not proof of ownership.
- What to do with any coins (the snapshot rules) is decided by **community vote**, not by these scripts.

Found a different result or a bug? Open an issue, or post your `SHA256SUMS.txt` in the BTGS Discord.

BTGS Community · MIT licence
