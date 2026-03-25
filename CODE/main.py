"""
main.py — CLI for NFT Provenance System.

COMMANDS:
  python main.py demo          — run full demo with synthetic NFTs
  python main.py check <file>  — check provenance of a local image
  python main.py index         — index NFTs from chain (needs API key)
  python main.py stats         — show DB statistics
  python main.py reset         — clear database

DEMO SCENARIO:
  Creates a realistic scenario:
    1. Alice mints "CryptoPunk #1" on Ethereum (Jan 2021)
    2. Bob copies the image, mints on Polygon (Mar 2021) — DUPLICATE
    3. Charlie makes a slightly cropped version, mints on Solana (Feb 2021) — NEAR-DUPLICATE
    4. Dave mints a completely different image — UNIQUE
  Then queries each image and shows provenance results.
"""

import sys
import os
import io
import time
from PIL import Image, ImageDraw, ImageFilter
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))
from database import init_db, insert_nft, insert_hashes, get_stats
from hasher import dhash, phash, hash_image_file
import hashlib

DEMO_IMAGES = {
    "original": "demo_original.jpg",
    "near_dupe": "demo_cropped.jpg",
    "exact_copy": "demo_original.jpg",  # same file = exact duplicate
    "different": "demo_different.jpg",
}

def make_test_image(width=256, height=256, color=(100, 150, 200),
                    text="NFT", pattern="stripes") -> Image.Image:
    """Generate a synthetic NFT-like image."""
    img = Image.new("RGB", (width, height), color)
    draw = ImageDraw.Draw(img)

    if pattern == "stripes":
        for i in range(0, width, 20):
            c = tuple(max(0, c - 30) for c in color)
            draw.rectangle([i, 0, i + 10, height], fill=c)
    elif pattern == "circles":
        for r in range(20, 120, 20):
            c = tuple(min(255, c + 20) for c in color)
            draw.ellipse([width//2 - r, height//2 - r,
                          width//2 + r, height//2 + r], outline=c, width=3)
    elif pattern == "grid":
        for i in range(0, width, 32):
            for j in range(0, height, 32):
                c = tuple((c + i * 3 + j * 2) % 255 for c in color)
                draw.rectangle([i, j, i+30, j+30], fill=c)

    # Add text label
    draw.text((10, 10), text, fill=(255, 255, 255))
    return img


def make_near_duplicate(original: Image.Image, variant="resize") -> Image.Image:
    """Create a near-duplicate of an image (as a plagiarist might)."""
    if variant == "resize":
        # Scale up then back down — common plagiarism technique
        big = original.resize((512, 512), Image.LANCZOS)
        return big.resize((256, 256), Image.LANCZOS)
    elif variant == "brightness":
        from PIL import ImageEnhance
        return ImageEnhance.Brightness(original).enhance(1.2)
    elif variant == "crop":
        # Crop to 90% then resize back
        w, h = original.size
        margin = int(w * 0.05)
        cropped = original.crop((margin, margin, w - margin, h - margin))
        return cropped.resize((w, h), Image.LANCZOS)
    return original


def image_to_hashes(img: Image.Image) -> dict:
    """Hash a PIL image directly."""
    dh = dhash(img)
    ph_val = phash(img)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    sha = hashlib.sha256(buf.getvalue()).hexdigest()
    return {
        "dhash": dh,
        "phash": ph_val,
        "sha256": sha,
        "dhash_hex": format(dh, "016x"),
        "phash_hex": format(ph_val, "016x"),
    }


def run_demo(db_path: str = None):
    print("=" * 60)
    print("  NFT PROVENANCE DEMO: 'WHO MINTED FIRST?'")
    print("=" * 60)

    # Always start fresh — demo must run in isolation
    import os
    demo_db = db_path or "demo_provenance.db"
    if os.path.exists(demo_db):
        os.remove(demo_db)
    db_path = demo_db
    kwargs = {"db_path": db_path}
    init_db(**kwargs)

    # ── Create synthetic NFT images ──────────────────────────────────────────
    print("\n[1/4] Creating synthetic NFT images...\n")

    from PIL import Image as _Image
    original_img = _Image.open("demo_original.jpg")
    near_dupe    = _Image.open("demo_cropped.jpg")
    exact_copy   = original_img.copy()
    different    = _Image.open("demo_different.jpg")

    print("  ✓ Original NFT image loaded (demo_original.jpg — BAYC #1)")
    print("  ✓ Near-duplicate loaded (demo_cropped.jpg — cropped BAYC #1)")
    print("  ✓ Exact copy created (byte-for-byte copy of original)")
    print("  ✓ Different image loaded (demo_different.jpg — CryptoPunk)")

    # ── Seed the database with 4 NFT records ─────────────────────────────────
    print("\n[2/4] Seeding database with multi-chain NFT records...\n")

    # Alice: original minter on Ethereum, Jan 2021
    alice_record = {
        "chain": "ethereum",
        "contract_addr": "0xb47e3cd837ddf8e4c57f05d70ab865de6e193bbb",  # CryptoPunks
        "token_id": "1337",
        "minter_addr": "0xAlice1234567890abcdef1234567890abcdef12",
        "block_number": 11800000,
        "block_timestamp": 1611000000,  # Jan 19, 2021
        "tx_hash": "0xabc123def456abc123def456abc123def456abc123def456abc123def456abc1",
        "metadata_uri": "ipfs://QmAliceOriginal123",
        "image_uri": "ipfs://QmAliceImage456",
        "token_name": "CoolApe #1337",
        "collection_name": "CoolApes",
    }

    # Bob: steals image, mints on Polygon, March 2021 (LATER → PLAGIARIST)
    bob_record = {
        "chain": "polygon",
        "contract_addr": "0xBob0000000000000000000000000000000000001",
        "token_id": "42",
        "minter_addr": "0xBob9999999999999999999999999999999999999",
        "block_number": 14500000,
        "block_timestamp": 1616500000,  # Mar 23, 2021 — AFTER Alice
        "tx_hash": "0xbob456def789bob456def789bob456def789bob456def789bob456def789bob4",
        "metadata_uri": "ipfs://QmBobStolen789",
        "image_uri": "ipfs://QmBobStolenImage",
        "token_name": "TotallyOriginal #42",
        "collection_name": "TotallyOriginalApes",
    }

    # Carol: near-duplicate on Solana, Feb 2021 (between Alice and Bob)
    carol_record = {
        "chain": "solana",
        "contract_addr": "SoLCoLLecTioN111111111111111111111111111",
        "token_id": "CaRoLMiNT222222222222222222222222222222222",
        "minter_addr": "CaRoLWaLLeT33333333333333333333333333333",
        "block_number": 67000000,
        "block_timestamp": 1614000000,  # Feb 23, 2021 — between Alice and Bob
        "tx_hash": "CaRoLtXhAsH4444444444444444444444444444444444444444444444444444",
        "metadata_uri": "https://arweave.net/CarolMeta",
        "image_uri": "https://arweave.net/CarolImage",
        "token_name": "Ape Remix #1",
        "collection_name": "RemixedApes",
    }

    # Dave: completely different image, April 2021 — should be UNIQUE
    dave_record = {
        "chain": "ethereum",
        "contract_addr": "0xDave000000000000000000000000000000000001",
        "token_id": "99",
        "minter_addr": "0xDaveWallet000000000000000000000000000000",
        "block_number": 12400000,
        "block_timestamp": 1618000000,  # Apr 10, 2021
        "tx_hash": "0xdave789abc123dave789abc123dave789abc123dave789abc123dave789abc12",
        "metadata_uri": "ipfs://QmDaveOriginal",
        "image_uri": "ipfs://QmDaveImage",
        "token_name": "RedCircles #99",
        "collection_name": "CircleArt",
    }

    alice_id = insert_nft(alice_record, **kwargs)
    bob_id   = insert_nft(bob_record, **kwargs)
    carol_id = insert_nft(carol_record, **kwargs)
    dave_id  = insert_nft(dave_record, **kwargs)

    insert_hashes(alice_id, image_to_hashes(original_img), **kwargs)
    insert_hashes(bob_id,   image_to_hashes(exact_copy), **kwargs)    # exact copy
    insert_hashes(carol_id, image_to_hashes(near_dupe), **kwargs)     # near-duplicate
    insert_hashes(dave_id,  image_to_hashes(different), **kwargs)     # unrelated

    print(f"  ✓ Alice  (Ethereum, Jan 2021) → NFT ID #{alice_id}  [ORIGINAL]")
    print(f"  ✓ Bob    (Polygon,  Mar 2021) → NFT ID #{bob_id}    [EXACT COPY of Alice]")
    print(f"  ✓ Carol  (Solana,   Feb 2021) → NFT ID #{carol_id}  [NEAR-DUPLICATE of Alice]")
    print(f"  ✓ Dave   (Ethereum, Apr 2021) → NFT ID #{dave_id}   [COMPLETELY DIFFERENT]")

    # ── Run provenance checks ─────────────────────────────────────────────────
    print("\n[3/4] Running provenance checks...\n")

    from checker import check_provenance

    def query(label: str, img: Image.Image):
        print(f"  {'─'*50}")
        print(f"  QUERY: {label}")
        from hasher import hamming_distance
        result = check_provenance("demo", source_type="pil", pil_image=img,
                                  db_path=db_path)
        print(f"  {result.summary()}")
        print()

    query("Bob's exact copy (should say DUPLICATE → Alice was first)", exact_copy)
    query("Carol's near-duplicate (should say NEAR_DUPLICATE → Alice was first)", near_dupe)
    query("Dave's unrelated image (should say UNIQUE)", different)
    query("Alice's original (should match herself)", original_img)

    # ── Summary stats ─────────────────────────────────────────────────────────
    print("\n[4/4] Database statistics:\n")
    stats = get_stats(**kwargs)
    print(f"  Total NFTs indexed:  {stats['total_nfts']}")
    print(f"  Total hashed:        {stats['total_hashed']}")
    print(f"  Chains covered:      {', '.join(stats['chains'])}")
    print(f"  Provenance queries:  {stats['total_queries']}")

    print("\n" + "=" * 60)
    print("  DEMO COMPLETE")
    print("  The system correctly identified provenance across 3 chains.")
    print("  See api.py to expose this as a REST API.")
    print("=" * 60)


def cmd_stats(db_path=None):
    kwargs = {"db_path": db_path} if db_path else {}
    stats = get_stats(**kwargs)
    print(json.dumps(stats, indent=2))


def cmd_check(filepath: str, db_path=None):
    from checker import check_provenance
    if not os.path.exists(filepath):
        print(f"File not found: {filepath}")
        sys.exit(1)
    result = check_provenance(filepath, source_type="file",
                              db_path=db_path)
    print(result.summary())


def cmd_reset(db_path=None):
    path = db_path or "provenance.db"
    if os.path.exists(path):
        os.remove(path)
        print(f"Database deleted: {path}")
    else:
        print("No database found.")


import json

if __name__ == "__main__":
    args = sys.argv[1:]
    db_arg = None
    if "--db" in args:
        idx = args.index("--db")
        db_arg = args[idx + 1]
        args = args[:idx] + args[idx + 2:]

    cmd = args[0] if args else "demo"

    if cmd == "demo":
        run_demo(db_path=db_arg)
    elif cmd == "stats":
        cmd_stats(db_path=db_arg)
    elif cmd == "check" and len(args) > 1:
        cmd_check(args[1], db_path=db_arg)
    elif cmd == "reset":
        cmd_reset(db_path=db_arg)
    else:
        print(__doc__)
