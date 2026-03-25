# This project ran script for both online and offline NFT Provenance
The online running 'readme' is a diferent file
---

## What This Project Does

Someone mints "CoolApe #1" on Ethereum in January 2021.
A plagiarist steals the image, mints "TotallyOriginalApe" on Polygon in June 2021.
Both claim to be the original.

This system answers: **"Who minted this image first, on which blockchain, and here is the transaction hash as proof."**

The transaction hash (tx_hash) is verifiable by anyone on Etherscan/Polygonscan/Solscan — it's immutable blockchain evidence.

---

## Project Files

| File | What it does |
|---|---|
| `hasher.py` | Fingerprints images using perceptual hashing (dHash + pHash). Unlike SHA256, perceptual hashes stay similar for visually similar images — catches resized/edited copies |
| `database.py` | SQLite database storing NFT records (chain, minter, timestamp, tx_hash) and image fingerprints |
| `indexer.py` | Connects to Ethereum, Polygon, Solana APIs and fetches real NFT data |
| `checker.py` | Core engine — given an image, finds all matching NFTs in DB and returns the earliest minter |
| `api.py` | REST API server on port 8080. Exposes the system as HTTP endpoints |
| `main.py` | Command line tool. Run demo, check images, view stats |
| `requirements.txt` | Python dependencies |
| `provenance.db` | SQLite database (auto-created when you first run) |
| `start.bat` | Windows shortcut to set API keys and start server in one click |

---

## Setup 

### Step 1 — Install Python dependencies
Open CMD in the project folder and run:
```cmd
pip install Pillow imagehash
```


### Step 2 — Get a free Alchemy API key (takes 5 minutes)
1. Go to **alchemy.com** → Sign up free
2. Dashboard → "Create App" → select **Ethereum Mainnet**
3. Click your app → copy the **API Key**

### Step 3 — Create your start.bat file
In the project folder, create a file called `start.bat` containing:
```bat
set ALCHEMY_API_KEY=paste_your_key_here
python api.py
```
This sets your API key and starts the server every time with one command.

---

## How to Run Everything (Step by Step)

### TERMINAL 1 — Start the server

```cmd
cd path\to\your\project\folder
start.bat
```

You should see:
```
[DB] Initialized at: ...provenance.db
[API] NFT Provenance API running on http://0.0.0.0:8080
[API] Endpoints: GET /health /stats /chains /nft/<id>
[API]            POST /check /index /register
```
**Keep this terminal open the entire time. Do not close it.**

---

### TERMINAL 2 — Run commands (open a second CMD window)

#### Test the server is alive:
```cmd
curl http://localhost:8080/health
```
Expected: `{"status": "ok", "service": "nft-provenance"}`

---

#### Index BAYC #1 from Bored Ape NFT from Ethereum (stores it in DB):
```cmd
curl -X POST http://localhost:8080/index -H "Content-Type: application/json" -d "{\"record\": {\"chain\": \"ethereum\", \"contract_addr\": \"0xBC4CA0EdA7647A8aB7C2061c2E118A18a936f13D\", \"token_id\": \"1\", \"minter_addr\": \"0xABB3273Ed5e66082Ae3e9f9a34Df5Dc52e52853\", \"block_number\": 12346090, \"block_timestamp\": 1619795834, \"tx_hash\": \"0x22199329b0aa1aa68902a78e3b32ca327c872fab166c7a2838273de6ad383eba\", \"image_uri\": \"https://alchemy.mypinata.cloud/ipfs/QmPbxeGcXhYQQNgsC6a36dDyYUcHgMLnGKnF8pVFmGsvqi\", \"token_name\": \"BoredApe #1\", \"collection_name\": \"BoredApeYachtClub\", \"metadata_uri\": \"\"}}"
```
Expected: `{"indexed": true, "nft_id": 1}`

---
#### Index BAYC #2 from Bored Ape NFT from Ethereum (stores it in DB):
```cmd
curl -X POST http://localhost:8080/index -H "Content-Type: application/json" -d "{\"record\": {\"chain\": \"ethereum\", \"contract_addr\": \"0xBC4CA0EdA7647A8aB7C2061c2E118A18a936f13D\", \"token_id\": \"2\", \"minter_addr\": \"0xABB3273Ed5e66082Ae3e9f9a34Df5Dc52e52853\", \"block_number\": 12346100, \"block_timestamp\": 1619795900, \"tx_hash\": \"0x33299329b0aa1aa68902a78e3b32ca327c872fab166c7a2838273de6ad383ebb\", \"image_uri\": \"https://alchemy.mypinata.cloud/ipfs/QmcJYkCKK7QPmYWjp4FD2e3Lv5WCGFuHNUByvGKBaytif4\", \"token_name\": \"BoredApe #2\", \"collection_name\": \"BoredApeYachtClub\", \"metadata_uri\": \"\"}}"
```
Expected: `{"indexed": true, "nft_id": 2}`

---

#### Index a fake duplicate on Polygon (simulates a plagiarist):
```cmd
curl -X POST http://localhost:8080/index -H "Content-Type: application/json" -d "{\"record\": {\"chain\": \"polygon\", \"contract_addr\": \"0xFakeApes999\", \"token_id\": \"1\", \"minter_addr\": \"0xEvil000000000000000000000000000000000001\", \"block_number\": 25000000, \"block_timestamp\": 1625000000, \"tx_hash\": \"0xfake999abc\", \"image_uri\": \"local:bayc1_colored.jpg\", \"token_name\": \"TotallyOriginalApe #1\", \"collection_name\": \"FakeApes\", \"metadata_uri\": \"\"}}"

curl -X POST http://localhost:8080/index -H "Content-Type: application/json" -d "{\"record\": {\"chain\": \"ethereum\", \"contract_addr\": \"0xFakeApes999\", \"token_id\": \"1\", \"minter_addr\": \"0xEvil000000000000000000000000000000000001\", \"block_number\": 25000000, \"block_timestamp\": 1625000000, \"tx_hash\": \"0xfake999abc\", \"image_uri\": \"local:punk_NFT.png\", \"token_name\": \"TotallyOriginalApe #1\", \"collection_name\": \"FakeApes\", \"metadata_uri\": \"\"}}"
```
Expected: `{"indexed": true, "nft_id": 3}`

---

#### Check A — BAYC #1 image (proves DUPLICATE + cross-chain winner) :
```cmd
curl -X POST http://localhost:8080/check -H "Content-Type: application/json" -d "{\"image_url\": \"https://alchemy.mypinata.cloud/ipfs/QmPbxeGcXhYQQNgsC6a36dDyYUcHgMLnGKnF8pVFmGsvqi\"}"
```
#### Check B — BAYC #2 image: :
```cmd
curl -X POST http://localhost:8080/check -H "Content-Type: application/json" -d "{\"image_url\": \"https://alchemy.mypinata.cloud/ipfs/QmcJYkCKK7QPmYWjp4FD2e3Lv5WCGFuHNUByvGKBaytif4\"}"
```

Example response:
```json
{
  "verdict": "DUPLICATE",
  "is_original": false,
  "explanation": "EXACT duplicate found. SHA256 matches. Originally minted on ETHEREUM at block 1619795834.",
  "query_image": "https://alchemy.mypinata.cloud/ipfs/QmPbxeGcXhYQQNgsC6a36dDyYUcHgMLnGKnF8pVFmGsvqi",
  "match_count": 3,
  "winner": {
    "nft_id": 1,
    "chain": "ethereum",
    "contract_addr": "0xBC4CA0EdA7647A8aB7C2061c2E118A18a936f13D",
    "token_id": "1",
    "minter_addr": "0xABB3273Ed5e66082Ae3e9f9a34Df5Dc52e52853",
    "minted_at": "2021-04-30 15:17:14 UTC",
    "block_timestamp": 1619795834,
    "tx_hash": "0x22199329b0aa1aa68902a78e3b32ca327c872fab166c7a2838273de6ad383eba",
    "proof_url": "https://etherscan.io/tx/0x22199329b0aa1aa68902a78e3b32ca327c872fab166c7a2838273de6ad383eba",
    "similarity": "EXACT_DUPLICATE"
  },
  "all_matches": [
    {
      "nft_id": 1,
      "chain": "ethereum",
      "token_id": "1",
      "minted_at": "2021-04-30 15:17:14 UTC",
      "dhash_distance": 0,
      "phash_distance": 0,
      "similarity": "EXACT_DUPLICATE"
    },
    {
      "nft_id": 3,
      "chain": "ethereum",
      "token_id": "2",
      "minted_at": "2021-04-30 15:18:20 UTC",
      "dhash_distance": 7,
      "phash_distance": 14,
      "similarity": "NEAR_DUPLICATE"   # NEAR_DUPLICATE (hamm distance ≤10).
    },
    {
      "nft_id": 2,
      "chain": "polygon",
      "token_id": "1",
      "minted_at": "2021-06-29 20:53:20 UTC",
      "dhash_distance": 20,
      "phash_distance": 10,
      "similarity": "SIMILAR" #Current result gave similar cos my crop was too aggressive. The hamming distance came out as dΔ=20 which sits exactly on the SIMILAR boundary
    }
  ]
```
#### check punk_nft: a UNIQUE check:
```cmd
python main.py check punk_NFT.png
```
Expected response:
```json
VERDICT: UNIQUE
Query:   punk_NFT.png

Checked against 3 indexed NFTs. No duplicates or near-duplicates found. This image appears original.
```

**Ethereum wins. Polygon is the plagiarist. The proof_url is real and verifiable.**

---

#### Check DB stats anytime:
```cmd
curl http://localhost:8080/stats
```

---

## Check a Local Image File Against the DB

Make sure server is running, then:
```cmd
python main.py check yourimage.jpg
```

Example — checking a CryptoPunk against indexed BAYC NFTs:
```cmd
python main.py check punk_NFT.png
```
Expected: `VERDICT: UNIQUE` — completely different art style, no match found.

---

## Reset the Database

To start fresh and clear all indexed NFTs:
```cmd
python main.py reset
del provenance.db
```

---

### Perceptual Hashing
Unlike SHA256 (1 pixel change = completely different hash), perceptual hashes stay similar for visually similar images.

**dHash algorithm:**
1. Resize image to 9×8 grayscale
2. For each row, compare each pixel to its right neighbour
3. bit=1 if left pixel is brighter, bit=0 otherwise
4. Output: 64-bit integer fingerprint

**Hamming distance** measures how many bits differ between two hashes:
- 0 = identical images
- 1–10 = near-duplicate (resized, watermarked, minor edit)
- 11–20 = similar (color graded, derivative)
- 21+ = different images

**Why not SHA256?** SHA256 has no locality-sensitivity — a 1-pixel change produces a completely different hash, making near-duplicate detection impossible. dHash preserves visual similarity in Hamming space.

### Cross-Chain Timestamp Comparison
- Ethereum/Polygon timestamps come from `block.timestamp` (set by miners, accurate to ±15 seconds)
- All timestamps normalized to Unix epoch for fair cross-chain comparison
- Earliest timestamp = provenance winner — this is immutable on-chain data

### Provenance Algorithm
1. Hash query image with dHash + pHash + SHA256
2. Compare against all indexed NFT hashes (Hamming distance scan)
3. Filter matches within similarity threshold (≤20 bits different)
4. Sort by `block_timestamp` ascending
5. Return earliest minter + their `tx_hash` as cryptographic proof

---

## Verdicts

| Verdict | Meaning |
|---|---|
| `UNIQUE` | No matching image found in DB |
| `ORIGINAL` | Only one mint of this image exists — it's the first |
| `DUPLICATE` | Exact copy found — SHA256 match, prior mint exists |
| `NEAR_DUPLICATE` | dHash distance ≤ 10 — same image, probably resized/edited |
| `SIMILAR` | dHash distance ≤ 20 — visually similar, possible derivative |

---

## API Endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/health` | Check server is running |
| GET | `/stats` | Total NFTs, chains, queries |
| GET | `/chains` | List supported chains |
| GET | `/nft/<id>` | Get NFT record by DB id |
| POST | `/index` | Add NFT to DB (by chain+contract+token_id OR full record) |
| POST | `/check` | Check provenance of an image URL |
| POST | `/register` | Index NFT + check provenance in one call |

---

