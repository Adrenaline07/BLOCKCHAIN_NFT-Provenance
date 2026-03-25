"""
hasher.py — Perceptual hashing for NFT image fingerprinting.

WHY NOT MD5/SHA256?
  Cryptographic hashes change completely with 1 pixel change.
  Perceptual hashes stay ~identical for visually similar images.

TWO ALGORITHMS:
  dHash (difference hash) — fast, great for exact/near-exact dupes
  pHash (perceptual hash) — slower, robust against color/contrast changes

HAMMING DISTANCE:
  Compare two 64-bit hashes. Count differing bits.
  0  = identical
  1-10 = near-duplicate (resize, minor edit)
  11-20 = similar (color grade, watermark)
  21+  = probably different images
"""

from PIL import Image
import hashlib
import struct
import imagehash as _ih


def dhash(image: Image.Image, hash_size: int = 8) -> int:
    """
    Difference Hash — compares adjacent pixel brightness.
    Returns a 64-bit integer fingerprint.

    Steps:
      1. Resize to (hash_size+1) x hash_size grayscale
      2. For each row, compare each pixel to the next pixel
      3. bit=1 if left > right, else bit=0
      4. Pack all bits into an integer
    """
    image = image.convert("L").resize(
        (hash_size + 1, hash_size), Image.LANCZOS
    )
    pixels = list(image.getdata())
    bits = []
    for row in range(hash_size):
        for col in range(hash_size):
            left = pixels[row * (hash_size + 1) + col]
            right = pixels[row * (hash_size + 1) + col + 1]
            bits.append(1 if left > right else 0)
    # Pack 64 bits into integer
    result = 0
    for bit in bits:
        result = (result << 1) | bit
    return result


def phash(image: Image.Image, hash_size: int = 8) -> int:
    h = _ih.phash(image, hash_size=hash_size)
    return int(str(h), 16)

def hamming_distance(hash1: int, hash2: int, bits: int = 64) -> int:
    """Count differing bits between two hashes. Lower = more similar."""
    return bin(hash1 ^ hash2).count("1")


def similarity_score(hash1: int, hash2: int, bits: int = 64) -> float:
    """Return 0.0 (totally different) to 1.0 (identical)."""
    dist = hamming_distance(hash1, hash2, bits)
    return 1.0 - (dist / bits)


def hash_image_file(filepath: str) -> dict:
    """
    Hash an image from disk.
    Returns dict with both hash types + cryptographic SHA256.
    """
    with Image.open(filepath) as img:
        dh = dhash(img)
        ph = phash(img)

    with open(filepath, "rb") as f:
        sha256 = hashlib.sha256(f.read()).hexdigest()

    return {
        "dhash": dh,
        "phash": ph,
        "sha256": sha256,
        "dhash_hex": format(dh, "016x"),
        "phash_hex": format(ph, "016x"),
    }


# def hash_image_url(url: str) -> dict:
#     """
#     Hash an image from a URL (e.g., IPFS gateway or Arweave).
#     Requires network access. In offline mode, mock this.
#     """
#     import urllib.request
#     import io
#     import ssl as _ssl
#     _ctx = _ssl.create_default_context()
#     _ctx.check_hostname = False
#     _ctx.verify_mode = _ssl.CERT_NONE
#     with urllib.request.urlopen(url, timeout=10, context=_ctx) as response:
#         data = response.read()
#     img = Image.open(io.BytesIO(data))
#     dh = dhash(img)
#     ph = phash(img)
#     sha256 = hashlib.sha256(data).hexdigest()
#     return {
#         "dhash": dh,
#         "phash": ph,
#         "sha256": sha256,
#         "dhash_hex": format(dh, "016x"),
#         "phash_hex": format(ph, "016x"),
#     }
def hash_image_url(url: str) -> dict:
    import urllib.request
    import io
    import ssl as _ssl

    _ctx = _ssl.create_default_context()
    _ctx.check_hostname = False
    _ctx.verify_mode = _ssl.CERT_NONE

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "image/webp,image/*,*/*;q=0.8"
        }
    )

    with urllib.request.urlopen(req, timeout=15, context=_ctx) as response:
        data = response.read()

    img = Image.open(io.BytesIO(data))
    dh = dhash(img)
    ph = phash(img)
    sha256 = hashlib.sha256(data).hexdigest()
    return {
        "dhash": dh,
        "phash": ph,
        "sha256": sha256,
        "dhash_hex": format(dh, "016x"),
        "phash_hex": format(ph, "016x"),
    }

DUPLICATE_THRESHOLD = 10   # hamming distance ≤ 10 → near-duplicate
SIMILAR_THRESHOLD = 20     # hamming distance ≤ 20 → similar


def classify_similarity(hash1: int, hash2: int) -> str:
    dist = hamming_distance(hash1, hash2)
    if dist == 0:
        return "EXACT_DUPLICATE"
    elif dist <= DUPLICATE_THRESHOLD:
        return "NEAR_DUPLICATE"
    elif dist <= SIMILAR_THRESHOLD:
        return "SIMILAR"
    else:
        return "DIFFERENT"
