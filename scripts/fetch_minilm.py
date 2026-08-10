#!/usr/bin/env python3
"""
fetch_minilm.py --- One-shot pre-stage for the sentence-transformers/all-MiniLM-L6-v2
model used by AN-11 PROPER (semantic-validator layer-2).

Usage:
    python fetch_minilm.py
    python fetch_minilm.py --cache-dir /path/to/cache
    python fetch_minilm.py --verify-only

What it does:
    1. Loads the model from HuggingFace into the local cache.
    2. Verifies a SHA256 checksum on the config + weights files.
    3. Prints the cache path so operators can wire it into bus_validator.

Why a separate script:
    - Decouples the network download from the runtime (no surprise fetches).
    - Lets operators pre-stage on a connected box, then copy the cache to
      an offline one.
    - The verify-only flag gives a smoke test for already-staged boxes.
"""

import argparse
import hashlib
import os
import sys
from pathlib import Path

DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
DEFAULT_CACHE = os.path.expanduser("~/.cache/torch/sentence_transformers")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--model", default=DEFAULT_MODEL,
        help=f"HuggingFace model id (default: {DEFAULT_MODEL})",
    )
    ap.add_argument(
        "--cache-dir", default=DEFAULT_CACHE,
        help=f"Local cache directory (default: {DEFAULT_CACHE})",
    )
    ap.add_argument(
        "--verify-only", action="store_true",
        help="Just verify the cache exists; do not download.",
    )
    args = ap.parse_args()

    cache_dir = Path(args.cache_dir).expanduser().resolve()
    cache_dir.mkdir(parents=True, exist_ok=True)

    if args.verify_only:
        # The HF cache uses hashed subdirs like models--<org>--<repo>/snapshots/<hash>/
        # Find anything that looks like the model name.
        candidates = list(cache_dir.rglob("config.json"))
        match = next(
            (c for c in candidates if args.model.replace("/", "--") in str(c)),
            None,
        )
        if not match:
            print(f"VERIFY-FAIL: no config.json found for {args.model!r} under {cache_dir}")
            sys.exit(2)
        snap = match.parent
        print(f"VERIFY-OK: cache at {snap}")
        for f in ("config.json", "tokenizer.json", "vocab.txt"):
            p = snap / f
            if p.exists():
                print(f"  {f}: {sha256_file(p)[:16]}... ({p.stat().st_size} bytes)")
            else:
                print(f"  {f}: MISSING (model may be incomplete)")
        return

    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as e:
        print(f"FATAL: sentence-transformers not installed ({e}).")
        print("  pip install sentence-transformers")
        sys.exit(3)

    print(f"Fetching {args.model} -> {cache_dir} ...")
    model = SentenceTransformer(args.model, cache_folder=str(cache_dir))
    # Smoke-test encode on a trivial pair.
    emb = model.encode(["hello", "world"], normalize_embeddings=True)
    print(f"Smoke-test embed OK: shape={emb.shape}")
    print(f"Cache path: {cache_dir}")
    print(f"Verify later with: python {__file__} --verify-only")
    print()
    print("# Wire-up example (paste into hardened_engine.py):")
    print("#   from semantic_handoff import SemanticHandoffValidator")
    print("#   from bus_validator import ValidatingBusProxy")
    print("#")
    print("#   semantic = SemanticHandoffValidator(")
    print('#       enabled_heuristics={"omission","numeric","class","polarity","proper"},')
    print(f'#       model_path="{cache_dir}",')
    print("#       proper_warning_threshold=0.80,  # tune in operation")
    print("#       proper_error_threshold=0.95,    # tune in operation")
    print("#   )")
    print("#   proxy = ValidatingBusProxy(bus, semantic_validator=semantic)")


if __name__ == "__main__":
    main()
