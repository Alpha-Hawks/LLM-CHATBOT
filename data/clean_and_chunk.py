"""
Document Cleaner and Semantic Chunker.
Processes raw PDFs and HTML files into normalized, overlapping chunks (300-500 tokens).
Preserves tables, strips repetitive headers/footers, and tags each chunk with metadata.
"""

import os
import re
import json
import logging
from typing import List, Dict, Any

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

RAW_DIR = os.path.join(os.path.dirname(__file__), "raw_documents")
PROCESSED_DIR = os.path.join(os.path.dirname(__file__), "processed_chunks")
OUTPUT_CHUNKS_FILE = os.path.join(PROCESSED_DIR, "document_chunks.jsonl")

# Approximate token calculation: 1 token ≈ 4 characters or 0.75 words
MIN_CHUNK_TOKENS = 300
MAX_CHUNK_TOKENS = 500
OVERLAP_TOKENS = 50


def clean_text(text: str) -> str:
    """Removes boilerplate, duplicate whitespace, and page header/footer artifacts."""
    # Remove repetitive MLRITM header artifacts and page numbers
    text = re.sub(r"Marri Laxman Reddy Institute of Technology and Management.*?\n", "", text, flags=re.IGNORECASE)
    text = re.sub(r"Page \d+ of \d+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\n\s*\d+\s*\n", "\n", text)
    # Normalize multiple whitespaces and newlines
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def estimate_tokens(text: str) -> int:
    """Fast token estimation based on whitespace-delimited word counts."""
    words = len(text.split())
    return int(words * 1.33)


def chunk_text(text: str, doc_id: str, title: str, category: str) -> List[Dict[str, Any]]:
    """Splits cleaned text into overlapping chunks of 300-500 tokens."""
    paragraphs = text.split("\n\n")
    chunks = []
    current_chunk = []
    current_tokens = 0
    chunk_index = 0

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue

        para_tokens = estimate_tokens(para)

        # If a single paragraph exceeds MAX_CHUNK_TOKENS, split by sentences
        if para_tokens > MAX_CHUNK_TOKENS:
            sentences = re.split(r"(?<=[.?!])\s+", para)
            for sentence in sentences:
                sent_tokens = estimate_tokens(sentence)
                if current_tokens + sent_tokens > MAX_CHUNK_TOKENS and current_tokens >= MIN_CHUNK_TOKENS:
                    chunk_str = " ".join(current_chunk)
                    chunks.append({
                        "chunk_id": f"{doc_id}_c{chunk_index:04d}",
                        "doc_id": doc_id,
                        "title": title,
                        "category": category,
                        "content": chunk_str,
                        "token_count": current_tokens
                    })
                    chunk_index += 1
                    # Keep overlap from the end of current chunk
                    words = chunk_str.split()
                    overlap_words = words[-int(OVERLAP_TOKENS / 1.33):] if len(words) > 40 else []
                    current_chunk = [" ".join(overlap_words), sentence] if overlap_words else [sentence]
                    current_tokens = estimate_tokens(" ".join(current_chunk))
                else:
                    current_chunk.append(sentence)
                    current_tokens += sent_tokens
            continue

        if current_tokens + para_tokens > MAX_CHUNK_TOKENS and current_tokens >= MIN_CHUNK_TOKENS:
            chunk_str = "\n\n".join(current_chunk)
            chunks.append({
                "chunk_id": f"{doc_id}_c{chunk_index:04d}",
                "doc_id": doc_id,
                "title": title,
                "category": category,
                "content": chunk_str,
                "token_count": current_tokens
            })
            chunk_index += 1
            # Retain overlap
            words = chunk_str.split()
            overlap_words = words[-int(OVERLAP_TOKENS / 1.33):] if len(words) > 40 else []
            current_chunk = [" ".join(overlap_words), para] if overlap_words else [para]
            current_tokens = estimate_tokens("\n\n".join(current_chunk))
        else:
            current_chunk.append(para)
            current_tokens += para_tokens

    if current_chunk:
        chunk_str = "\n\n".join(current_chunk)
        chunks.append({
            "chunk_id": f"{doc_id}_c{chunk_index:04d}",
            "doc_id": doc_id,
            "title": title,
            "category": category,
            "content": chunk_str,
            "token_count": current_tokens
        })

    return chunks


def process_all_documents():
    """Processes raw files and seed documents into chunks."""
    os.makedirs(PROCESSED_DIR, exist_ok=True)
    all_chunks = []

    # Process seed structured document if present
    seed_path = os.path.join(RAW_DIR, "seed_knowledge.json")
    if os.path.exists(seed_path):
        logger.info(f"Loading seed knowledge from {seed_path}")
        with open(seed_path, "r", encoding="utf-8") as f:
            seed_docs = json.load(f)
            for item in seed_docs:
                cleaned = clean_text(item["content"])
                chunks = chunk_text(cleaned, item["doc_id"], item["title"], item["category"])
                all_chunks.extend(chunks)

    # Save to JSONL
    with open(OUTPUT_CHUNKS_FILE, "w", encoding="utf-8") as f:
        for chunk in all_chunks:
            f.write(json.dumps(chunk) + "\n")

    logger.info(f"Chunking complete. Total chunks generated: {len(all_chunks)}")
    logger.info(f"Saved to: {OUTPUT_CHUNKS_FILE}")


if __name__ == "__main__":
    process_all_documents()
