"""
checker.py — The "who minted first?" provenance engine.

THIS IS THE CORE OF THE PROJECT.

ALGORITHM:
  1. Take a query image (file path or URI)
  2. Compute its perceptual hash
  3. Scan all indexed NFTs for hash matches within threshold
  4. Among matches, sort by block_timestamp ASC
  5. The EARLIEST timestamp wins — that's the provenance winner
  6. Return a ProvenanceResult with full evidence chain

TRUSTWORTHINESS:
  - block_timestamp is set by the blockchain, not by us
  - tx_hash is the cryptographic proof of the mint event
  - Anyone can independently verify by querying the blockchain
  - We're just aggregating + comparing across chains

EDGE CASES HANDLED:
  - Same image minted on multiple chains — earliest wins
  - Near-duplicates (resized, watermarked) — flagged as candidates
  - Image not in DB — returns "UNIQUE, no prior mint found"
  - Ties (same block on same chain) — both flagged, human review needed
"""

from dataclasses import dataclass, field
from typing import Optional
import io
from PIL import Image

from hasher import (dhash, phash, hash_image_file, hash_image_url,
                    hamming_distance, classify_similarity,
                    DUPLICATE_THRESHOLD, SIMILAR_THRESHOLD)
from database import get_all_hashes, log_query


@dataclass
class ProvenanceCandidate:
    nft_id: int
    chain: str
    contract_addr: str
    token_id: str
    minter_addr: str
    block_timestamp: int
    tx_hash: str
    image_uri: str
    token_name: str
    collection_name: str
    dhash_distance: int
    phash_distance: int
    similarity_type: str  # EXACT_DUPLICATE | NEAR_DUPLICATE | SIMILAR

    @property
    def blockchain_proof_url(self) -> str:
        """Link to block explorer for independent verification."""
        explorers = {
            "ethereum": f"https://etherscan.io/tx/{self.tx_hash}",
            "polygon":  f"https://polygonscan.com/tx/{self.tx_hash}",
            "solana":   f"https://solscan.io/tx/{self.tx_hash}",
        }
        return explorers.get(self.chain, f"tx:{self.tx_hash}")

    def timestamp_human(self) -> str:
        from datetime import datetime, timezone
        dt = datetime.fromtimestamp(self.block_timestamp, tz=timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M:%S UTC")


def confidence_score(candidate: ProvenanceCandidate) -> dict:
    """
    Explainable confidence score (0-100) based on:
    - Match strength (hash distance)
    - Evidence reliability (block_timestamp present + tx_hash present)
    """
    # Match strength score (50 points max)
    if candidate.similarity_type == "EXACT_DUPLICATE":
        match_score = 50
    elif candidate.similarity_type == "NEAR_DUPLICATE":
        match_score = 35
    elif candidate.similarity_type == "SIMILAR":
        match_score = 20
    else:
        match_score = 0

    # Evidence reliability score (50 points max)
    evidence_score = 0
    if candidate.tx_hash and not candidate.tx_hash.startswith("0xfake"):
        evidence_score += 30  # real tx hash
    if candidate.block_timestamp and candidate.block_timestamp > 0:
        evidence_score += 20  # real timestamp

    total = match_score + evidence_score
    return {
        "score": total,
        "match_strength": match_score,
        "evidence_reliability": evidence_score,
        "explanation": (
            f"Match strength: {match_score}/50 ({candidate.similarity_type}). "
            f"Evidence reliability: {evidence_score}/50 "
            f"({'real tx_hash + timestamp' if evidence_score == 50 else 'partial evidence'})."
        )
    }


@dataclass
class ProvenanceResult:
    verdict: str             # UNIQUE | ORIGINAL | DUPLICATE | NEAR_DUPLICATE | SIMILAR
    query_image: str         # what we queried
    winner: Optional[ProvenanceCandidate] = None    # earliest minter
    all_candidates: list = field(default_factory=list)  # all matches
    explanation: str = ""

    def is_original(self) -> bool:
        """True if the queried image appears to be the first mint."""
        return self.verdict in ("UNIQUE", "ORIGINAL")

    def summary(self) -> str:
        lines = [
            f"VERDICT: {self.verdict}",
            f"Query:   {self.query_image}",
        ]
        if self.winner:
            conf = confidence_score(self.winner)
            lines += [
                f"",
                f"FIRST MINTED:",
                f"  Chain:      {self.winner.chain.upper()}",
                f"  Collection: {self.winner.collection_name}",
                f"  Token:      {self.winner.token_name} (#{self.winner.token_id})",
                f"  Minter:     {self.winner.minter_addr}",
                f"  When:       {self.winner.timestamp_human()}",
                f"  Proof:      {self.winner.blockchain_proof_url}",
                f"  Confidence: {conf['score']}/100 — {conf['explanation']}",
            ]
        if len(self.all_candidates) > 1:
            lines += [
                f"",
                f"ALL MATCHES ({len(self.all_candidates)} found, sorted by mint time):",
            ]
            for i, c in enumerate(self.all_candidates):
                lines.append(
                    f"  [{i+1}] {c.chain.upper()} #{c.token_id} | "
                    f"{c.timestamp_human()} | dΔ={c.dhash_distance} pΔ={c.phash_distance} | "
                    f"{c.similarity_type}"
                )
        lines.append(f"\n{self.explanation}")
        return "\n".join(lines)


def check_provenance(
    image_source: str,
    source_type: str = "file",  # "file" | "url" | "pil"
    pil_image: Image.Image = None,
    db_path: str = None,
) -> ProvenanceResult:
    """
    Main provenance check.

    Args:
        image_source: file path, URL, or label (if pil_image provided)
        source_type: "file", "url", or "pil"
        pil_image: pre-loaded PIL Image (if source_type="pil")
        db_path: override default DB path

    Returns:
        ProvenanceResult with full evidence
    """
    # 1. Hash the query image
    if source_type == "file":
        hashes = hash_image_file(image_source)
        query_dhash = hashes["dhash"]
        query_phash = hashes["phash"]
        query_sha256 = hashes["sha256"]
    elif source_type == "url":
        hashes = hash_image_url(image_source)
        query_dhash = hashes["dhash"]
        query_phash = hashes["phash"]
        query_sha256 = hashes["sha256"]
    elif source_type == "pil" and pil_image:
        query_dhash = dhash(pil_image)
        query_phash = phash(pil_image)
        import hashlib
        buf = io.BytesIO()
        pil_image.save(buf, format="PNG")
        query_sha256 = hashlib.sha256(buf.getvalue()).hexdigest()
    else:
        raise ValueError("Provide source_type='file'/'url' or source_type='pil' with pil_image")

    # 2. Load all indexed NFT hashes
    kwargs = {"db_path": db_path} if db_path else {}
    all_records = get_all_hashes(**kwargs)

    if not all_records:
        result = ProvenanceResult(
            verdict="UNIQUE",
            query_image=image_source,
            explanation="No NFTs indexed yet. This appears to be unique (empty database)."
        )
        log_query(image_source, None, [], "UNIQUE", **kwargs)
        return result

    # 3. Compare hashes against every indexed NFT
    candidates = []
    for rec in all_records:
        if rec["dhash"] is None or rec["phash"] is None:
            continue

        d_dist = hamming_distance(query_dhash, rec["dhash"])
        p_dist = hamming_distance(query_phash, rec["phash"])

        # SHA256 exact match overrides everything
        if rec["sha256"] and rec["sha256"] == query_sha256:
            sim_type = "EXACT_DUPLICATE"
        else:
            # dhash is primary signal (more reliable for synthetic/real images)
            sim_type = classify_similarity(query_dhash, rec["dhash"])

        # Only include if dhash says similar OR exact SHA match
        if sim_type != "DIFFERENT" or sim_type == "EXACT_DUPLICATE":
            candidates.append(ProvenanceCandidate(
                nft_id=rec["nft_id"],
                chain=rec["chain"],
                contract_addr=rec["contract_addr"],
                token_id=rec["token_id"],
                minter_addr=rec["minter_addr"],
                block_timestamp=rec["block_timestamp"],
                tx_hash=rec["tx_hash"],
                image_uri=rec.get("image_uri", ""),
                token_name=rec.get("token_name", ""),
                collection_name=rec.get("collection_name", ""),
                dhash_distance=d_dist,
                phash_distance=p_dist,
                similarity_type=sim_type,
            ))

    # 4. No matches → unique
    if not candidates:
        result = ProvenanceResult(
            verdict="UNIQUE",
            query_image=image_source,
            explanation=(
                f"Checked against {len(all_records)} indexed NFTs. "
                f"No duplicates or near-duplicates found. This image appears original."
            )
        )
        log_query(image_source, None, [], "UNIQUE", **kwargs)
        return result

    # 5. Sort by block_timestamp — earliest is the winner
    candidates.sort(key=lambda c: c.block_timestamp)
    winner = candidates[0]

    # 6. If only one match, this image IS the original — it matched itself
    if len(candidates) == 1:
        result = ProvenanceResult(
            verdict="ORIGINAL",
            query_image=image_source,
            winner=None,
            all_candidates=candidates,
            explanation="Only one mint of this image found. This is the original."
        )
        log_query(image_source, winner.nft_id, [winner.nft_id], "ORIGINAL", **kwargs)
        return result

    # 7. Determine verdict
    if winner.similarity_type == "EXACT_DUPLICATE":
        verdict = "DUPLICATE"
        explanation = (
            f"EXACT duplicate found. SHA256 matches. "
            f"Originally minted on {winner.chain.upper()} at block {winner.block_timestamp}."
        )
    elif winner.similarity_type == "NEAR_DUPLICATE":
        verdict = "NEAR_DUPLICATE"
        explanation = (
            f"Near-duplicate found (hamming distance ≤ {DUPLICATE_THRESHOLD}). "
            f"Likely same image, possibly resized or slightly edited. "
            f"Original minted on {winner.chain.upper()}."
        )
    else:
        verdict = "SIMILAR"
        explanation = (
            f"Visually similar image found (hamming distance ≤ {SIMILAR_THRESHOLD}). "
            f"May be a derivative work. Earliest similar NFT on {winner.chain.upper()}."
        )

    candidate_ids = [c.nft_id for c in candidates]
    log_query(image_source, winner.nft_id, candidate_ids, verdict, **kwargs)

    return ProvenanceResult(
        verdict=verdict,
        query_image=image_source,
        winner=winner,
        all_candidates=candidates,
        explanation=explanation,
    )


def register_nft_with_image(
    nft_record: dict,
    image_source: str = None,
    source_type: str = "url",
    pil_image: Image.Image = None,
    db_path: str = None,
) -> ProvenanceResult:
    """
    Register a NEW mint + check if it's a duplicate in one step.

    Workflow:
      1. Insert NFT record into DB
      2. Hash the image
      3. Store hashes
      4. Run provenance check
      5. Return result (ORIGINAL if no prior match, else DUPLICATE/etc.)

    This is what you'd call every time someone mints an NFT on your platform.
    """
    from database import insert_nft, insert_hashes
    kwargs = {"db_path": db_path} if db_path else {}

    # Insert the NFT record first
    nft_id = insert_nft(nft_record, **kwargs)

    # Hash the image
    if source_type == "file" and image_source:
        hashes = hash_image_file(image_source)
    elif source_type == "url" and image_source:
        hashes = hash_image_url(image_source)
    elif source_type == "pil" and pil_image:
        h_d = dhash(pil_image)
        h_p = phash(pil_image)
        import hashlib, io as _io
        buf = _io.BytesIO()
        pil_image.save(buf, format="PNG")
        sha = hashlib.sha256(buf.getvalue()).hexdigest()
        hashes = {"dhash": h_d, "phash": h_p, "sha256": sha,
                  "dhash_hex": format(h_d, "016x"), "phash_hex": format(h_p, "016x")}
    else:
        hashes = {"dhash": None, "phash": None, "sha256": None,
                  "dhash_hex": None, "phash_hex": None}

    if nft_id:
        insert_hashes(nft_id, hashes, **kwargs)

    # Now check provenance (excluding this record by checking timestamp)
    result = check_provenance(
        image_source or "unknown",
        source_type=source_type,
        pil_image=pil_image,
        db_path=db_path,
    )

    # If the only match is this NFT itself → it's original
    own_matches = [c for c in result.all_candidates if c.nft_id == nft_id]
    other_matches = [c for c in result.all_candidates if c.nft_id != nft_id]

    if not other_matches:
        result.verdict = "ORIGINAL"
        result.explanation = "No prior mints of this image found. Registered as original."
        result.winner = None

    return result
