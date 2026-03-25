// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/**
 * @title ProvenanceRegistry
 * @notice Cross-platform NFT provenance registry using perceptual hashing.
 *         Answers "who minted first?" for duplicate/near-duplicate media.
 *
 * Architecture:
 *   - Exact duplicates: registered by SHA-256 hash of raw media bytes
 *   - Near-duplicates:  registered by 64-bit perceptual hash (pHash), grouped
 *                       by clustering prefix (top 16 bits) for efficient lookup
 *   - "First mint wins": block.timestamp is the canonical ordering
 *
 * Deployment: chain-agnostic. Deploy on Ethereum, Polygon, Base, etc.
 *             Cross-chain comparison is done off-chain by the indexer.
 */
contract ProvenanceRegistry {

    // ─── Data Types ──────────────────────────────────────────────────────────

    struct MediaRecord {
        address minter;         // EOA or contract that called registerMint()
        uint64  mintTimestamp;  // block.timestamp at registration
        uint32  chainId;        // originating chain (for cross-chain records relayed by bridge)
        string  platform;       // "opensea" | "blur" | "foundation" | "custom" | etc.
        string  tokenURI;       // IPFS/HTTPS URI of the NFT metadata
        bytes32 exactHash;      // keccak256 / SHA-256 of raw media bytes
        uint64  perceptualHash; // 64-bit pHash computed off-chain, stored on-chain
        bool    exists;
    }

    // ─── Storage ─────────────────────────────────────────────────────────────

    // exactHash → first record (first-mint winner)
    mapping(bytes32 => MediaRecord) public exactRegistry;

    // pHash → array of records (may have near-duplicates)
    mapping(uint64 => MediaRecord[]) public perceptualRegistry;

    // pHash clustering: top 16 bits → list of full pHashes in that bucket
    mapping(uint16 => uint64[]) public pHashBuckets;

    // minter → all their registered hashes (for portfolio lookup)
    mapping(address => bytes32[]) public minterHistory;

    // ─── Events ──────────────────────────────────────────────────────────────

    event MintRegistered(
        address indexed minter,
        bytes32 indexed exactHash,
        uint64  indexed perceptualHash,
        uint64  mintTimestamp,
        string  platform,
        string  tokenURI
    );

    event DuplicateDetected(
        bytes32 indexed exactHash,
        address originalMinter,
        uint64  originalTimestamp,
        address lateMinter
    );

    event NearDuplicateDetected(
        uint64  indexed perceptualHash,
        address firstMinter,
        uint64  firstTimestamp,
        address lateMinter,
        uint8   hammingDistance
    );

    // ─── Errors ──────────────────────────────────────────────────────────────

    error InvalidExactHash();
    error InvalidPerceptualHash();
    error EmptyTokenURI();

    // ─── Core Logic ──────────────────────────────────────────────────────────

    /**
     * @notice Register a newly minted NFT's media fingerprints.
     * @param exactHash       SHA-256 of the raw media file (bytes32)
     * @param perceptualHash  64-bit pHash computed off-chain
     * @param platform        Platform name string
     * @param tokenURI        NFT metadata URI
     */
    function registerMint(
        bytes32 exactHash,
        uint64  perceptualHash,
        string  calldata platform,
        string  calldata tokenURI
    ) external {
        if (exactHash == bytes32(0))    revert InvalidExactHash();
        if (perceptualHash == 0)        revert InvalidPerceptualHash();
        if (bytes(tokenURI).length == 0) revert EmptyTokenURI();

        MediaRecord memory record = MediaRecord({
            minter:         msg.sender,
            mintTimestamp:  uint64(block.timestamp),
            chainId:        uint32(block.chainid),
            platform:       platform,
            tokenURI:       tokenURI,
            exactHash:      exactHash,
            perceptualHash: perceptualHash,
            exists:         true
        });

        // ── Exact duplicate check ──
        if (exactRegistry[exactHash].exists) {
            MediaRecord storage original = exactRegistry[exactHash];
            emit DuplicateDetected(
                exactHash,
                original.minter,
                original.mintTimestamp,
                msg.sender
            );
        } else {
            exactRegistry[exactHash] = record;
        }

        // ── Near-duplicate check (Hamming distance ≤ 10 = near-duplicate) ──
        uint64[] storage bucket = pHashBuckets[uint16(perceptualHash >> 48)];
        for (uint i = 0; i < bucket.length; i++) {
            uint8 dist = _hammingDistance(perceptualHash, bucket[i]);
            if (dist <= 10 && bucket[i] != perceptualHash) {
                MediaRecord storage first = perceptualRegistry[bucket[i]][0];
                emit NearDuplicateDetected(
                    perceptualHash,
                    first.minter,
                    first.mintTimestamp,
                    msg.sender,
                    dist
                );
            }
        }

        // ── Store perceptual record ──
        if (perceptualRegistry[perceptualHash].length == 0) {
            // First time this exact pHash is seen — add to bucket index
            uint16 bucketKey = uint16(perceptualHash >> 48);
            pHashBuckets[bucketKey].push(perceptualHash);
        }
        perceptualRegistry[perceptualHash].push(record);

        minterHistory[msg.sender].push(exactHash);

        emit MintRegistered(
            msg.sender,
            exactHash,
            perceptualHash,
            uint64(block.timestamp),
            platform,
            tokenURI
        );
    }

    // ─── Query Functions ─────────────────────────────────────────────────────

    /**
     * @notice Get the original (first) minter of an exact media hash.
     */
    function getOriginalMinter(bytes32 exactHash)
        external view returns (address minter, uint64 timestamp, string memory platform)
    {
        MediaRecord storage r = exactRegistry[exactHash];
        require(r.exists, "Not registered");
        return (r.minter, r.mintTimestamp, r.platform);
    }

    /**
     * @notice Get all records for a perceptual hash (i.e., all near-duplicate mints).
     */
    function getPerceptualMatches(uint64 pHash)
        external view returns (MediaRecord[] memory)
    {
        return perceptualRegistry[pHash];
    }

    /**
     * @notice Get all exact hashes ever registered by a minter.
     */
    function getMinterHistory(address minter)
        external view returns (bytes32[] memory)
    {
        return minterHistory[minter];
    }

    /**
     * @notice Check if an exact hash is already registered (duplicate guard).
     */
    function isDuplicate(bytes32 exactHash) external view returns (bool) {
        return exactRegistry[exactHash].exists;
    }

    /**
     * @notice Find near-duplicates of a given pHash within Hamming distance threshold.
     * @param pHash     The perceptual hash to query
     * @param maxDist   Maximum Hamming distance (recommend 10 for near-duplicate)
     * @return matches  Array of matching MediaRecords
     */
    function findNearDuplicates(uint64 pHash, uint8 maxDist)
        external view returns (MediaRecord[] memory matches)
    {
        uint16 bucketKey = uint16(pHash >> 48);
        uint64[] storage bucket = pHashBuckets[bucketKey];

        // Count matches first (no dynamic arrays in memory loops in Solidity)
        uint count = 0;
        for (uint i = 0; i < bucket.length; i++) {
            if (_hammingDistance(pHash, bucket[i]) <= maxDist) {
                count += perceptualRegistry[bucket[i]].length;
            }
        }

        matches = new MediaRecord[](count);
        uint idx = 0;
        for (uint i = 0; i < bucket.length; i++) {
            if (_hammingDistance(pHash, bucket[i]) <= maxDist) {
                MediaRecord[] storage recs = perceptualRegistry[bucket[i]];
                for (uint j = 0; j < recs.length; j++) {
                    matches[idx++] = recs[j];
                }
            }
        }
    }

    // ─── Internal ─────────────────────────────────────────────────────────────

    /**
     * @dev Count differing bits between two 64-bit values (popcount of XOR).
     *      Gas-efficient Kernighan method.
     */
    function _hammingDistance(uint64 a, uint64 b) internal pure returns (uint8 dist) {
        uint64 xor = a ^ b;
        while (xor != 0) {
            xor &= xor - 1; // clear lowest set bit
            dist++;
        }
    }
}
