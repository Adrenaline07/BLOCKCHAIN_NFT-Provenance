# This is for online script for NFT Provenance

#### Index BAYC #1 from Bored Ape NFT from Ethereum (stores it in DB):
```cmd
curl -X POST http://localhost:8888/index -H "Content-Type: application/json" -d "{\"chain\": \"ethereum\", \"contract\": \"0xBC4CA0EdA7647A8aB7C2061c2E118A18a936f13D\", \"token_id\": \"1\"}"
```
Expected: `{"indexed": true, "nft_id": 1}`

---
#### Index BAYC #2 from Bored Ape NFT from Ethereum (stores it in DB):
```cmd
curl -X POST http://localhost:8888/index -H "Content-Type: application/json" -d "{\"chain\": \"ethereum\", \"contract\": \"0xBC4CA0EdA7647A8aB7C2061c2E118A18a936f13D\", \"token_id\": \"2\"}"
```
Expected: `{"indexed": true, "nft_id": 2}`

---

#### Index a fake duplicate on Polygon (simulates a plagiarist):
```cmd
curl -X POST http://localhost:8888/index -H "Content-Type: application/json" -d "{\"record\": {\"chain\": \"polygon\", \"contract_addr\": \"0xFakeApes999\", \"token_id\": \"1\", \"minter_addr\": \"0xEvil000000000000000000000000000000000001\", \"block_number\": 25000000, \"block_timestamp\": 1625000000, \"tx_hash\": \"0xfake999abc\", \"image_uri\": \"local:bayc1_colored.jpg\", \"token_name\": \"TotallyOriginalApe #1\", \"collection_name\": \"FakeApes\", \"metadata_uri\": \"\"}}"
```
#### Index a unique on Polygon (simulates a plagiarist):

curl -X POST http://localhost:8888/index -H "Content-Type: application/json" -d "{\"record\": {\"chain\": \"ethereum\", \"contract_addr\": \"0xFakeApes999\", \"token_id\": \"1\", \"minter_addr\": \"0xEvil000000000000000000000000000000000001\", \"block_number\": 25000000, \"block_timestamp\": 1625000000, \"tx_hash\": \"0xfake999abc\", \"image_uri\": \"local:punk_NFT.png\", \"token_name\": \"TotallyOriginalApe #1\", \"collection_name\": \"FakeApes\", \"metadata_uri\": \"\"}}"

Expected: `{"indexed": true, "nft_id": 3}`

---

#### Check A — BAYC #1 image (proves DUPLICATE + cross-chain winner) :
```cmd
curl -X POST http://localhost:8888/check -H "Content-Type: application/json" -d "{\"image_url\": \"https://alchemy.mypinata.cloud/ipfs/QmPbxeGcXhYQQNgsC6a36dDyYUcHgMLnGKnF8pVFmGsvqi\"}"
```
#### Check B — BAYC #2 image: :
```cmd
curl -X POST http://localhost:8888/check -H "Content-Type: application/json" -d "{\"image_url\": \"https://alchemy.mypinata.cloud/ipfs/QmcJYkCKK7QPmYWjp4FD2e3Lv5WCGFuHNUByvGKBaytif4\"}"

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
