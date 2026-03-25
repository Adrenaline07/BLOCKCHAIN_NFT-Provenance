"""
indexer.py — Multi-chain NFT indexer.

ARCHITECTURE:
  Each chain has an "adapter" class with a common interface:
    .fetch_nft(contract, token_id) -> NFTRecord dict
    .fetch_collection(contract, limit) -> list[NFTRecord]

REAL API INTEGRATION:
  Set env vars to enable live fetching:
    ALCHEMY_API_KEY   — Ethereum + Polygon (https://alchemy.com)
    HELIUS_API_KEY    — Solana            (https://helius.dev)
    MORALIS_API_KEY   — Multi-chain       (https://moralis.io)

  Without keys, all fetches return realistic mock data so the
  rest of the system (hashing, DB, provenance checks) still runs.

TIMESTAMP GROUND TRUTH:
  Ethereum/Polygon: block.timestamp (unix seconds, set by miners, ±15s)
  Solana: block time from confirmed slot (very accurate)
  All stored as unix timestamps for fair cross-chain comparison.
"""

import os
import json
import time
import random
import hashlib
from typing import Optional
try:
    import urllib.request as urlrequest
    import urllib.parse as urlparse
    HAS_NETWORK = True
except ImportError:
    HAS_NETWORK = False


ALCHEMY_KEY = os.environ.get("ALCHEMY_API_KEY", "")
HELIUS_KEY  = os.environ.get("HELIUS_API_KEY", "")
MORALIS_KEY = os.environ.get("MORALIS_API_KEY", "")

def _to_alchemy_gateway(uri: str) -> str:
    if not uri:
        return uri
    if uri.startswith("ipfs://"):
        return "https://alchemy.mypinata.cloud/ipfs/" + uri[7:]
    if "ipfs.io/ipfs/" in uri:
        return "https://alchemy.mypinata.cloud/ipfs/" + uri.split("ipfs.io/ipfs/")[1]
    return uri

def _resolve_ipfs(uri: str, alchemy_key: str = "") -> str:
    """Convert ipfs:// or ipfs.io URLs to Alchemy's working gateway."""
    if not uri:
        return uri
    if uri.startswith("ipfs://"):
        cid = uri[7:]
        return f"https://alchemy.mypinata.cloud/ipfs/{cid}"
    if "ipfs.io/ipfs/" in uri:
        cid = uri.split("ipfs.io/ipfs/")[1]
        return f"https://alchemy.mypinata.cloud/ipfs/{cid}"
    return uri

def _http_get(url: str, headers: dict = None) -> dict:
    """Simple HTTP GET returning parsed JSON."""
    req = urlrequest.Request(url, headers=headers or {})
    with urlrequest.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())


# ─── ETHEREUM ADAPTER ─────────────────────────────────────────────────────────

class EthereumAdapter:
    """
    Uses Alchemy's NFT API v3.
    Docs: https://docs.alchemy.com/reference/getnftmetadata
    """
    BASE = "https://eth-mainnet.g.alchemy.com/nft/v3"

    def fetch_nft(self, contract: str, token_id: str) -> Optional[dict]:
        if not ALCHEMY_KEY:
            return self._mock_nft("ethereum", contract, token_id)
        
        # Step 1 — get NFT metadata
        url = f"{self.BASE}/{ALCHEMY_KEY}/getNFTMetadata?contractAddress={contract}&tokenId={token_id}"
        data = _http_get(url)
        record = self._parse_alchemy(data, "ethereum")
        
        # Step 2 — get real mint tx via getAssetTransfers
        try:
            transfers_url = f"https://eth-mainnet.g.alchemy.com/v2/{ALCHEMY_KEY}"
            payload = json.dumps({
                "jsonrpc": "2.0",
                "id": 1,
                "method": "alchemy_getAssetTransfers",
                "params": [{
                    "fromBlock": "0x0",
                    "toBlock": "latest",
                    "contractAddresses": [contract],
                    "category": ["erc721"],
                    "fromAddress": "0x0000000000000000000000000000000000000000",
                    "withMetadata": True,
                    "maxCount": "0x64"
                }]
            }).encode()
            
            req = urlrequest.Request(
                transfers_url,
                data=payload,
                headers={"Content-Type": "application/json"}
            )
            with urlrequest.urlopen(req, timeout=15) as resp:
                transfer_data = json.loads(resp.read().decode())
            
            transfers = transfer_data.get("result", {}).get("transfers", [])
            
            # Find the specific token_id mint
            for t in transfers:
                t_id = t.get("tokenId", "0x0") or "0x0"
                if str(int(t_id, 16)) == str(token_id):
                    record["minter_addr"] = t.get("to", "")
                    record["tx_hash"] = t.get("hash", "")
                    record["block_number"] = int(t.get("blockNum", "0x0"), 16)
                    meta = t.get("metadata", {})
                    if meta.get("blockTimestamp"):
                        from datetime import datetime, timezone
                        dt = datetime.strptime(
                            meta["blockTimestamp"], "%Y-%m-%dT%H:%M:%S.000Z"
                        ).replace(tzinfo=timezone.utc)
                        record["block_timestamp"] = int(dt.timestamp())
                    break
        except Exception as e:
            print(f"[indexer] mint tx lookup failed: {e}")
        
        return record

    def fetch_collection(self, contract: str, limit: int = 100) -> list:
        if not ALCHEMY_KEY:
            return [self._mock_nft("ethereum", contract, str(i)) for i in range(min(limit, 10))]
        results = []
        page_key = None
        while len(results) < limit:
            url = (f"{self.BASE}/{ALCHEMY_KEY}/getNFTsForContract"
                   f"?contractAddress={contract}&limit=100&withMetadata=true")
            if page_key:
                url += f"&pageKey={page_key}"
            data = _http_get(url)
            for nft in data.get("nfts", []):
                results.append(self._parse_alchemy(nft, "ethereum"))
            page_key = data.get("pageKey")
            if not page_key:
                break
        return results[:limit]

    def _parse_alchemy(self, data: dict, chain: str) -> dict:
        mint = data.get("mint", {})
        return {
            "chain": chain,
            "contract_addr": data.get("contract", {}).get("address", ""),
            "token_id": str(data.get("tokenId", "")),
            "minter_addr": mint.get("mintAddress", "0x0000"),
            "block_number": mint.get("blockNumber", 0),
            "block_timestamp": mint.get("timestamp", 0),
            "tx_hash": mint.get("transactionHash", ""),
            "metadata_uri": data.get("tokenUri", ""),
            "image_uri": _to_alchemy_gateway((data.get("image", {}) or {}).get("originalUrl", "")),
            "token_name": data.get("name", ""),
            "collection_name": (data.get("contract", {}) or {}).get("name", ""),
        }

    def _mock_nft(self, chain: str, contract: str, token_id: str) -> dict:
        """Deterministic mock so tests are reproducible."""
        seed = hashlib.md5(f"{chain}{contract}{token_id}".encode()).hexdigest()
        base_ts = 1620000000  # ~May 2021 (NFT boom era)
        ts_offset = int(seed[:8], 16) % (60 * 60 * 24 * 365)  # within 1 year
        return {
            "chain": chain,
            "contract_addr": contract,
            "token_id": token_id,
            "minter_addr": "0x" + seed[8:48],
            "block_number": 12000000 + (ts_offset // 13),
            "block_timestamp": base_ts + ts_offset,
            "tx_hash": "0x" + seed * 2,
            "metadata_uri": f"ipfs://Qm{seed[:44]}",
            "image_uri": f"ipfs://Qm{seed[4:48]}",
            "token_name": f"Mock NFT #{token_id}",
            "collection_name": "Mock Collection",
        }


# ─── POLYGON ADAPTER ──────────────────────────────────────────────────────────

class PolygonAdapter(EthereumAdapter):
    """
    Same Alchemy API, different endpoint.
    Polygon blocks are ~2s vs Ethereum's ~12s, so block numbers differ.
    """
    BASE = "https://polygon-mainnet.g.alchemy.com/nft/v3"

    def fetch_nft(self, contract: str, token_id: str) -> Optional[dict]:
        if not ALCHEMY_KEY:
            return self._mock_nft("ethereum", contract, token_id)
        
        # Step 1 — get NFT metadata
        url = f"{self.BASE}/{ALCHEMY_KEY}/getNFTMetadata?contractAddress={contract}&tokenId={token_id}"
        data = _http_get(url)
        record = self._parse_alchemy(data, "ethereum")
        
        # Step 2 — get real mint tx via getAssetTransfers
        try:
            transfers_url = f"https://polygon-mainnet.g.alchemy.com/v2/{ALCHEMY_KEY}"
            payload = json.dumps({
                "jsonrpc": "2.0",
                "id": 1,
                "method": "alchemy_getAssetTransfers",
                "params": [{
                    "fromBlock": "0x0",
                    "toBlock": "latest",
                    "contractAddresses": [contract],
                    "category": ["erc721"],
                    "fromAddress": "0x0000000000000000000000000000000000000000",
                    "withMetadata": True,
                    "maxCount": "0x1"
                }]
            }).encode()
            
            req = urlrequest.Request(
                transfers_url,
                data=payload,
                headers={"Content-Type": "application/json"}
            )
            with urlrequest.urlopen(req, timeout=15) as resp:
                transfer_data = json.loads(resp.read().decode())
            
            transfers = transfer_data.get("result", {}).get("transfers", [])
            
            # Find the specific token_id mint
            for t in transfers:
                if str(int(t.get("tokenId", "0x0"), 16)) == str(token_id):
                    record["minter_addr"] = t.get("to", "")
                    record["tx_hash"] = t.get("hash", "")
                    record["block_number"] = int(t.get("blockNum", "0x0"), 16)
                    meta = t.get("metadata", {})
                    if meta.get("blockTimestamp"):
                        from datetime import datetime, timezone
                        dt = datetime.strptime(
                            meta["blockTimestamp"], "%Y-%m-%dT%H:%M:%S.000Z"
                        ).replace(tzinfo=timezone.utc)
                        record["block_timestamp"] = int(dt.timestamp())
                    break
        except Exception as e:
            print(f"[indexer] mint tx lookup failed: {e}")
        
        return record


# ─── SOLANA ADAPTER ───────────────────────────────────────────────────────────

class SolanaAdapter:
    """
    Uses Helius API for Solana NFT data.
    Docs: https://docs.helius.dev/compression-and-das-api/digital-asset-standard-das-api

    Solana uses "slot" instead of block number.
    Helius provides mint timestamps directly.
    """
    BASE = "https://mainnet.helius-rpc.com"

    def fetch_nft(self, mint_address: str, _token_id: str = None) -> Optional[dict]:
        """On Solana, each NFT IS its mint address (no contract+tokenId)."""
        if not HELIUS_KEY:
            return self._mock_nft(mint_address)
        url = f"{self.BASE}/?api-key={HELIUS_KEY}"
        payload = json.dumps({
            "jsonrpc": "2.0", "id": 1,
            "method": "getAsset",
            "params": {"id": mint_address}
        }).encode()
        req = urlrequest.Request(url, data=payload,
                                  headers={"Content-Type": "application/json"})
        with urlrequest.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
        return self._parse_helius(data.get("result", {}))

    def _parse_helius(self, asset: dict) -> dict:
        ownership = asset.get("ownership", {})
        content = asset.get("content", {})
        links = content.get("links", {})
        return {
            "chain": "solana",
            "contract_addr": asset.get("grouping", [{}])[0].get("group_value", ""),
            "token_id": asset.get("id", ""),
            "minter_addr": ownership.get("owner", ""),
            "block_number": asset.get("mint_extensions", {}).get("slot", 0),
            "block_timestamp": asset.get("mint_extensions", {}).get("timestamp", 0),
            "tx_hash": asset.get("id", ""),
            "metadata_uri": content.get("json_uri", ""),
            "image_uri": links.get("image", ""),
            "token_name": content.get("metadata", {}).get("name", ""),
            "collection_name": asset.get("grouping", [{}])[0].get("group_value", ""),
        }

    def _mock_nft(self, mint_address: str) -> dict:
        seed = hashlib.md5(f"solana{mint_address}".encode()).hexdigest()
        base_ts = 1620000000
        ts_offset = int(seed[:8], 16) % (60 * 60 * 24 * 365)
        return {
            "chain": "solana",
            "contract_addr": seed[:44],  # collection address
            "token_id": mint_address,
            "minter_addr": seed[4:48],
            "block_number": int(seed[:6], 16) % 200000000,
            "block_timestamp": base_ts + ts_offset,
            "tx_hash": seed * 2,
            "metadata_uri": f"https://arweave.net/{seed[:43]}",
            "image_uri": f"https://arweave.net/{seed[4:47]}",
            "token_name": f"Solana NFT {mint_address[:8]}",
            "collection_name": "Mock Solana Collection",
        }


# ─── FACTORY ──────────────────────────────────────────────────────────────────

ADAPTERS = {
    "ethereum": EthereumAdapter(),
    "polygon":  PolygonAdapter(),
    "solana":   SolanaAdapter(),
}


def fetch_nft(chain: str, contract: str, token_id: str) -> Optional[dict]:
    """
    Unified entry point. Fetch a single NFT from any supported chain.

    Usage:
        nft = fetch_nft("ethereum", "0xBC4CA0EdA7647A8aB7C2061c2E118A18a936f13D", "1")
        nft = fetch_nft("solana", "DRiP...", "")
    """
    adapter = ADAPTERS.get(chain.lower())
    if not adapter:
        raise ValueError(f"Unsupported chain: {chain}. Supported: {list(ADAPTERS.keys())}")
    return adapter.fetch_nft(contract, token_id)


def fetch_collection(chain: str, contract: str, limit: int = 100) -> list:
    """Fetch multiple NFTs from a collection."""
    adapter = ADAPTERS.get(chain.lower())
    if not adapter:
        raise ValueError(f"Unsupported chain: {chain}")
    return adapter.fetch_collection(contract, limit)


SUPPORTED_CHAINS = list(ADAPTERS.keys())
