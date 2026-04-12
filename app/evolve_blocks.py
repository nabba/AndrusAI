"""
evolve_blocks.py — FREEZE-BLOCK / EVOLVE-BLOCK marker infrastructure.

Marks regions within prompt/soul files as frozen (immutable) or evolvable.
The modification engine and evolution pipeline can ONLY modify content
within EVOLVE-BLOCK regions. Any diff that touches FREEZE-BLOCK content
is automatically rejected.

This makes safety architecturally enforced, not just policy-forbidden.
Directly addresses the DGM finding: agents WILL remove their own constraints
if the mechanism allows it.

Marker syntax (HTML comments, invisible to LLM):
    <!-- FREEZE-BLOCK-START -->
    ...immutable content...
    <!-- FREEZE-BLOCK-END -->

    <!-- EVOLVE-BLOCK-START id="strategy" -->
    ...evolvable content...
    <!-- EVOLVE-BLOCK-END -->

IMMUTABLE — infrastructure-level module.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# ── Marker patterns ───────────────────────────────────────────────────────────

FREEZE_START = re.compile(r'<!--\s*FREEZE-BLOCK-START\s*-->')
FREEZE_END = re.compile(r'<!--\s*FREEZE-BLOCK-END\s*-->')
EVOLVE_START = re.compile(r'<!--\s*EVOLVE-BLOCK-START(?:\s+id="([^"]*)")?\s*-->')
EVOLVE_END = re.compile(r'<!--\s*EVOLVE-BLOCK-END\s*-->')

@dataclass
class Block:
    """A parsed block from a prompt file."""
    block_type: str  # "freeze" | "evolve" | "unmarked"
    block_id: str = ""  # Optional ID for evolve blocks
    content: str = ""
    start_line: int = 0
    end_line: int = 0
    content_hash: str = ""  # SHA-256 for freeze blocks

    def __post_init__(self):
        if self.block_type == "freeze" and not self.content_hash:
            self.content_hash = hashlib.sha256(self.content.encode()).hexdigest()[:32]

@dataclass
class ParsedPrompt:
    """A prompt file parsed into freeze/evolve/unmarked blocks."""
    blocks: list[Block] = field(default_factory=list)
    source_text: str = ""

    @property
    def freeze_blocks(self) -> list[Block]:
        return [b for b in self.blocks if b.block_type == "freeze"]

    @property
    def evolve_blocks(self) -> list[Block]:
        return [b for b in self.blocks if b.block_type == "evolve"]

    @property
    def frozen_content(self) -> str:
        """All frozen content concatenated (for integrity checking)."""
        return "\n".join(b.content for b in self.freeze_blocks)

    @property
    def frozen_hash(self) -> str:
        """Hash of all frozen content for integrity verification."""
        return hashlib.sha256(self.frozen_content.encode()).hexdigest()[:32]

    def get_evolve_block(self, block_id: str) -> Block | None:
        """Get a specific evolve block by ID."""
        for b in self.evolve_blocks:
            if b.block_id == block_id:
                return b
        return None

    def replace_evolve_block(self, block_id: str, new_content: str) -> str:
        """Replace an evolve block's content, preserving everything else.

        Returns the full reconstructed prompt text.
        """
        result_lines = self.source_text.split("\n")
        block = self.get_evolve_block(block_id)
        if not block:
            raise ValueError(f"No evolve block with id='{block_id}'")

        # Replace content between markers (exclusive of markers themselves)
        new_lines = new_content.split("\n")
        result_lines[block.start_line:block.end_line] = new_lines
        return "\n".join(result_lines)

    def to_text(self) -> str:
        """Reconstruct the full prompt from blocks."""
        return self.source_text

# ── Parser ────────────────────────────────────────────────────────────────────

def parse_prompt(text: str) -> ParsedPrompt:
    """Parse a prompt into freeze/evolve/unmarked blocks.

    Lines between FREEZE-BLOCK markers are frozen.
    Lines between EVOLVE-BLOCK markers are evolvable.
    All other lines are unmarked (treated as frozen by default — conservative).
    """
    lines = text.split("\n")
    blocks: list[Block] = []
    current_type = "unmarked"
    current_id = ""
    current_lines: list[str] = []
    current_start = 0

    for i, line in enumerate(lines):
        # Check for block markers
        freeze_start = FREEZE_START.search(line)
        freeze_end = FREEZE_END.search(line)
        evolve_start = EVOLVE_START.search(line)
        evolve_end = EVOLVE_END.search(line)

        if freeze_start:
            # Close current block
            if current_lines:
                blocks.append(Block(
                    block_type=current_type, block_id=current_id,
                    content="\n".join(current_lines),
                    start_line=current_start, end_line=i,
                ))
            current_type = "freeze"
            current_id = ""
            current_lines = []
            current_start = i + 1

        elif freeze_end:
            blocks.append(Block(
                block_type="freeze", block_id=current_id,
                content="\n".join(current_lines),
                start_line=current_start, end_line=i,
            ))
            current_type = "unmarked"
            current_id = ""
            current_lines = []
            current_start = i + 1

        elif evolve_start:
            if current_lines:
                blocks.append(Block(
                    block_type=current_type, block_id=current_id,
                    content="\n".join(current_lines),
                    start_line=current_start, end_line=i,
                ))
            current_type = "evolve"
            current_id = evolve_start.group(1) or f"block_{len(blocks)}"
            current_lines = []
            current_start = i + 1

        elif evolve_end:
            blocks.append(Block(
                block_type="evolve", block_id=current_id,
                content="\n".join(current_lines),
                start_line=current_start, end_line=i,
            ))
            current_type = "unmarked"
            current_id = ""
            current_lines = []
            current_start = i + 1

        else:
            current_lines.append(line)

    # Close final block
    if current_lines:
        blocks.append(Block(
            block_type=current_type, block_id=current_id,
            content="\n".join(current_lines),
            start_line=current_start, end_line=len(lines),
        ))

    return ParsedPrompt(blocks=blocks, source_text=text)

# ── Validation ────────────────────────────────────────────────────────────────

def validate_modification(original: str, proposed: str) -> dict:
    """Validate that a proposed prompt modification only touches EVOLVE-BLOCK content.

    Returns: {valid: bool, reason: str, frozen_intact: bool, blocks_changed: list}
    """
    orig_parsed = parse_prompt(original)
    prop_parsed = parse_prompt(proposed)

    result = {
        "valid": True,
        "reason": "",
        "frozen_intact": True,
        "blocks_changed": [],
    }

    # Check 1: All freeze blocks must be identical
    orig_frozen = orig_parsed.frozen_content
    prop_frozen = prop_parsed.frozen_content

    if orig_frozen != prop_frozen:
        result["valid"] = False
        result["frozen_intact"] = False
        result["reason"] = "FREEZE-BLOCK content was modified — modification rejected"
        logger.warning("evolve_blocks: REJECTED modification — freeze block integrity violation")
        return result

    # Check 2: Unmarked content must also be unchanged (conservative default)
    orig_unmarked = "\n".join(
        b.content for b in orig_parsed.blocks if b.block_type == "unmarked"
    )
    prop_unmarked = "\n".join(
        b.content for b in prop_parsed.blocks if b.block_type == "unmarked"
    )

    if orig_unmarked != prop_unmarked:
        result["valid"] = False
        result["reason"] = "Content outside EVOLVE-BLOCK markers was modified"
        return result

    # Check 3: Identify which evolve blocks changed
    for orig_block in orig_parsed.evolve_blocks:
        prop_block = prop_parsed.get_evolve_block(orig_block.block_id)
        if prop_block and prop_block.content != orig_block.content:
            result["blocks_changed"].append(orig_block.block_id)

    return result

def extract_evolvable_content(text: str) -> dict[str, str]:
    """Extract all evolvable block contents from a prompt.

    Returns: {block_id: content}
    """
    parsed = parse_prompt(text)
    return {b.block_id: b.content for b in parsed.evolve_blocks}

def has_evolve_blocks(text: str) -> bool:
    """Check if a prompt contains any EVOLVE-BLOCK markers."""
    return bool(EVOLVE_START.search(text))

def get_frozen_hash(text: str) -> str:
    """Get the integrity hash of all frozen content in a prompt."""
    return parse_prompt(text).frozen_hash

# ── Prompt annotation helper ─────────────────────────────────────────────────

def annotate_prompt(text: str, freeze_sections: list[str] | None = None) -> str:
    """Add FREEZE-BLOCK markers around sections matching the given headers.

    If no freeze_sections specified, auto-detects safety-critical sections
    by looking for keywords like 'values', 'principles', 'safety', 'never'.
    Everything else gets EVOLVE-BLOCK markers.
    """
    SAFETY_KEYWORDS = {
        "values", "principles", "safety", "never", "always",
        "constraints", "ethics", "constitutional", "immutable",
        "core identity", "non-negotiable",
    }

    lines = text.split("\n")
    result = []
    in_section = False
    section_is_frozen = False
    current_header = ""

    for line in lines:
        # Detect markdown headers
        if line.startswith("#"):
            # Close previous section
            if in_section:
                if section_is_frozen:
                    result.append("<!-- FREEZE-BLOCK-END -->")
                else:
                    result.append("<!-- EVOLVE-BLOCK-END -->")
                result.append("")

            current_header = line.lower()
            header_text = line.lstrip("#").strip()

            # Determine if this section should be frozen
            if freeze_sections:
                section_is_frozen = any(
                    fs.lower() in current_header for fs in freeze_sections
                )
            else:
                section_is_frozen = any(
                    kw in current_header for kw in SAFETY_KEYWORDS
                )

            result.append(line)
            if section_is_frozen:
                result.append("<!-- FREEZE-BLOCK-START -->")
            else:
                safe_id = re.sub(r'[^\w]', '_', header_text.lower())[:30]
                result.append(f'<!-- EVOLVE-BLOCK-START id="{safe_id}" -->')
            in_section = True
        else:
            result.append(line)

    # Close final section
    if in_section:
        if section_is_frozen:
            result.append("<!-- FREEZE-BLOCK-END -->")
        else:
            result.append("<!-- EVOLVE-BLOCK-END -->")

    return "\n".join(result)
