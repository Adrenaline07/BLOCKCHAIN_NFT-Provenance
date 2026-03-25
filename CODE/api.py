"""
api.py — REST API for NFT provenance queries.

Pure stdlib — no Flask/FastAPI needed.

ENDPOINTS:
  GET  /health                    — liveness check
  GET  /stats                     — DB statistics
  POST /check                     — check provenance of an image URL
  POST /register                  — register a new NFT mint
  POST /index                     — index an NFT from any chain
  GET  /nft/<id>                  — get NFT record by ID

USAGE:
  python api.py                   — starts on port 8080
  python api.py --port 9000       — custom port

EXAMPLE:
  curl -X POST http://localhost:8080/check \
    -H "Content-Type: application/json" \
    -d '{"image_url": "https://ipfs.io/ipfs/Qm..."}'
"""

import json
import sys
import os
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(__file__))
from database import init_db, get_stats, get_nft_by_id, insert_nft, insert_hashes
from indexer import fetch_nft, SUPPORTED_CHAINS
from checker import check_provenance, register_nft_with_image, confidence_score


def _json_response(handler, code: int, data: dict):
    body = json.dumps(data, indent=2).encode()
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", len(body))
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.end_headers()
    handler.wfile.write(body)


def _read_body(handler) -> dict:
    length = int(handler.headers.get("Content-Length", 0))
    if length == 0:
        return {}
    raw = handler.rfile.read(length)
    return json.loads(raw.decode())


class ProvenanceHandler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        print(f"[API] {self.address_string()} - {fmt % args}")

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "/health":
            _json_response(self, 200, {"status": "ok", "service": "nft-provenance"})

        elif path == "/stats":
            stats = get_stats()
            _json_response(self, 200, stats)

        elif path.startswith("/nft/"):
            try:
                nft_id = int(path.split("/")[-1])
                nft = get_nft_by_id(nft_id)
                if nft:
                    _json_response(self, 200, nft)
                else:
                    _json_response(self, 404, {"error": "NFT not found"})
            except ValueError:
                _json_response(self, 400, {"error": "Invalid NFT ID"})

        elif path == "/chains":
            _json_response(self, 200, {"supported_chains": SUPPORTED_CHAINS})

        else:
            _json_response(self, 404, {"error": f"Unknown endpoint: {path}"})

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        try:
            body = _read_body(self)
        except Exception as e:
            _json_response(self, 400, {"error": f"Invalid JSON: {e}"})
            return

        # ── POST /check ──────────────────────────────────────────────────────
        if path == "/check":
            """
            Check if an image URL has been minted before.
            Body: {"image_url": "https://..."}
            """
            image_url = body.get("image_url")
            if not image_url:
                _json_response(self, 400, {"error": "Missing 'image_url'"})
                return
            try:
                result = check_provenance(image_url, source_type="url")
                response = {
                    "verdict": result.verdict,
                    "is_original": result.is_original(),
                    "explanation": result.explanation,
                    "query_image": result.query_image,
                    "match_count": len(result.all_candidates),
                }
                if result.winner:
                    w = result.winner
                    response["winner"] = {
                        "nft_id": w.nft_id,
                        "chain": w.chain,
                        "contract_addr": w.contract_addr,
                        "token_id": w.token_id,
                        "minter_addr": w.minter_addr,
                        "minted_at": w.timestamp_human(),
                        "block_timestamp": w.block_timestamp,
                        "tx_hash": w.tx_hash,
                        "proof_url": w.blockchain_proof_url,
                        "similarity": w.similarity_type,
                        "confidence": confidence_score(w),
                    }
                if result.all_candidates:
                    response["all_matches"] = [
                        {
                            "nft_id": c.nft_id,
                            "chain": c.chain,
                            "token_id": c.token_id,
                            "minted_at": c.timestamp_human(),
                            "dhash_distance": c.dhash_distance,
                            "phash_distance": c.phash_distance,
                            "similarity": c.similarity_type,
                        }
                        for c in result.all_candidates
                    ]
                _json_response(self, 200, response)
            except Exception as e:
                _json_response(self, 500, {"error": str(e)})

        # ── POST /index ───────────────────────────────────────────────────────
        elif path == "/index":
            """
            Index an NFT from any chain.
            Body: {"chain": "ethereum", "contract": "0x...", "token_id": "1"}
            Or provide a full record directly:
            Body: {"record": {...full nft_record dict...}, "image_url": "..."}
            """
            record_data = body.get("record")
            if record_data:
                nft_id = insert_nft(record_data)
                image_uri = record_data.get("image_uri", "")
                if image_uri and image_uri.startswith("local:"):
                    try:
                        from hasher import hash_image_file
                        filepath = image_uri[6:]  # strip "local:"
                        hashes = hash_image_file(filepath)
                        insert_hashes(nft_id, hashes)
                    except Exception as e:
                        print(f"[API] local hash failed: {e}")
                elif image_uri and image_uri.startswith("http"):
                    try:
                        from hasher import hash_image_url
                        hashes = hash_image_url(image_uri)
                        insert_hashes(nft_id, hashes)
                    except Exception:
                        pass
                _json_response(self, 200, {"indexed": True, "nft_id": nft_id})
            else:
                chain = body.get("chain")
                contract = body.get("contract")
                token_id = str(body.get("token_id", ""))
                if not all([chain, contract, token_id]):
                    _json_response(self, 400, {"error": "Need chain, contract, token_id"})
                    return
                try:
                    nft = fetch_nft(chain, contract, token_id)
                    nft_id = insert_nft(nft)
                    if nft.get("image_uri") and nft["image_uri"].startswith("http"):
                        try:
                            import ssl, urllib.request
                            ctx = ssl.create_default_context()
                            ctx.check_hostname = False
                            ctx.verify_mode = ssl.CERT_NONE
                            from hasher import hash_image_url
                            hashes = hash_image_url(nft["image_uri"])
                            insert_hashes(nft_id, hashes)
                        except Exception:
                            pass  # Image fetch failed — NFT still indexed
                    _json_response(self, 200, {
                        "indexed": True, "nft_id": nft_id, "record": nft
                    })
                except Exception as e:
                    _json_response(self, 500, {"error": str(e)})

        # ── POST /register ────────────────────────────────────────────────────
        elif path == "/register":
            """
            Register a new mint + check provenance in one call.
            Body: {"record": {...}, "image_url": "..."}
            Returns provenance verdict + whether this is original.
            """
            record_data = body.get("record")
            image_url = body.get("image_url", "")
            if not record_data:
                _json_response(self, 400, {"error": "Missing 'record'"})
                return
            try:
                result = register_nft_with_image(
                    record_data, image_url, source_type="url" if image_url else None
                )
                _json_response(self, 200, {
                    "verdict": result.verdict,
                    "is_original": result.is_original(),
                    "explanation": result.explanation,
                })
            except Exception as e:
                _json_response(self, 500, {"error": str(e)})

        else:
            _json_response(self, 404, {"error": f"Unknown endpoint: {path}"})


def run(port: int = 8080):
    init_db()
    server = HTTPServer(("0.0.0.0", port), ProvenanceHandler)
    print(f"[API] NFT Provenance API running on http://0.0.0.0:{port}")
    print(f"[API] Endpoints: GET /health /stats /chains /nft/<id>")
    print(f"[API]            POST /check /index /register")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[API] Shutting down.")


if __name__ == "__main__":
    port = 8080
    if "--port" in sys.argv:
        idx = sys.argv.index("--port")
        port = int(sys.argv[idx + 1])
    run(port)
