#!/usr/bin/env python3
"""
Simple utility to inspect ChromaDB collections and embeddings.

Usage:
    python scripts/inspect_chromadb.py                    # List all collections
    python scripts/inspect_chromadb.py <collection_name>  # Inspect specific collection
    python scripts/inspect_chromadb.py <collection_name> --sample 10  # Show 10 samples
"""

import os
import sys
import argparse
import json
from typing import List, Dict, Any, Optional

try:
    import chromadb
    from chromadb.config import Settings
except ImportError:
    print("Error: chromadb package not installed. Install with: pip install chromadb")
    sys.exit(1)


def connect_to_chromadb(host: str = None, port: int = None):
    """Connect to ChromaDB instance."""
    host = host or os.getenv("CHROMA_HOST", "localhost")
    port = port or int(os.getenv("CHROMA_PORT", "8000"))
    
    try:
        client = chromadb.HttpClient(
            host=host,
            port=port,
            settings=Settings(allow_reset=True)
        )
        return client
    except Exception as e:
        print(f"❌ Failed to connect to ChromaDB at {host}:{port}")
        print(f"   Error: {e}")
        sys.exit(1)


def list_collections(client):
    """List all collections in ChromaDB."""
    try:
        collections = client.list_collections()
        return [col.name for col in collections]
    except Exception as e:
        print(f"❌ Error listing collections: {e}")
        return []


def inspect_collection(client, collection_name: str, sample_size: int = 5, show_embeddings: bool = False):
    """Inspect a specific collection."""
    try:
        collection = client.get_collection(collection_name)
        count = collection.count()
        
        if count == 0:
            return {
                "collection_name": collection_name,
                "total_count": 0,
                "sample_data": None
            }
        
        # Get sample data
        limit = min(sample_size, count)
        include = ["embeddings"] if show_embeddings else []
        results = collection.get(limit=limit, include=include)
        
        return {
            "collection_name": collection_name,
            "total_count": count,
            "sample_size": limit,
            "ids": results.get("ids", []),
            "documents": results.get("documents", []),
            "metadatas": results.get("metadatas", []),
            "embeddings": results.get("embeddings", []) if show_embeddings else None
        }
    except Exception as e:
        return {"error": f"Error inspecting collection: {e}"}


def format_embedding(embedding: List[float], max_display: int = 5) -> str:
    """Format embedding vector for display."""
    if not embedding:
        return "[]"
    if len(embedding) <= max_display:
        return f"[{', '.join(f'{x:.4f}' for x in embedding)}]"
    return f"[{', '.join(f'{x:.4f}' for x in embedding[:max_display])}, ... ({len(embedding)} total)]"


def print_collection_list(collections: List[str]):
    """Print list of collections."""
    if not collections:
        print("\nNo collections found in ChromaDB.")
        return
    
    print(f"\nFound {len(collections)} collection(s) in ChromaDB:\n")
    for i, name in enumerate(collections, 1):
        print(f"  {i}. {name}")


def print_collection_info(info: Dict[str, Any], show_embeddings: bool = False):
    """Print collection inspection results."""
    if "error" in info:
        print(f"\n❌ Error: {info['error']}")
        return
    
    print(f"\n{'='*80}")
    print(f"Collection: {info['collection_name']}")
    print(f"{'='*80}")
    print(f"Total embeddings: {info['total_count']}")
    
    if info['total_count'] == 0:
        print("\nCollection is empty.")
        return
    
    print(f"\nSample size: {info.get('sample_size', 0)}")
    
    documents = info.get('documents', [])
    metadatas = info.get('metadatas', [])
    ids = info.get('ids', [])
    embeddings = info.get('embeddings', [])
    
    if documents:
        print(f"\n{'─'*80}")
        print("Sample Documents:")
        print(f"{'─'*80}")
        
        for i in range(len(documents)):
            doc_id = ids[i] if i < len(ids) else f"item_{i}"
            doc = documents[i]
            metadata = metadatas[i] if i < len(metadatas) else {}
            
            print(f"\n[{i+1}] ID: {doc_id}")
            print(f"    Text: {doc[:200]}{'...' if len(doc) > 200 else ''}")
            if metadata:
                print(f"    Metadata: {json.dumps(metadata, indent=6)}")
            if show_embeddings and embeddings and i < len(embeddings):
                embedding = embeddings[i]
                print(f"    Embedding: {format_embedding(embedding)}")
                print(f"    Dimension: {len(embedding)}")
    
    if embeddings and len(embeddings) > 0:
        print(f"\nEmbedding dimension: {len(embeddings[0])}")


def main():
    parser = argparse.ArgumentParser(
        description="Inspect ChromaDB collections and embeddings",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        'collection',
        nargs='?',
        help='Collection name to inspect (optional, lists all if not provided)'
    )
    parser.add_argument(
        '--sample',
        type=int,
        default=5,
        help='Number of sample embeddings to show (default: 5)'
    )
    parser.add_argument(
        '--show-embeddings',
        action='store_true',
        help='Show embedding vectors (can be verbose)'
    )
    parser.add_argument(
        '--host',
        type=str,
        help='ChromaDB host (default: from CHROMA_HOST env or localhost)'
    )
    parser.add_argument(
        '--port',
        type=int,
        help='ChromaDB port (default: from CHROMA_PORT env or 8000)'
    )
    parser.add_argument(
        '--json',
        action='store_true',
        help='Output results as JSON'
    )
    
    args = parser.parse_args()
    
    # Connect to ChromaDB
    print(f"Connecting to ChromaDB at {args.host or os.getenv('CHROMA_HOST', 'localhost')}:{args.port or int(os.getenv('CHROMA_PORT', '8000'))}...")
    client = connect_to_chromadb(host=args.host, port=args.port)
    print("✓ Connected to ChromaDB\n")
    
    try:
        if not args.collection:
            # List all collections
            collections = list_collections(client)
            if args.json:
                print(json.dumps({"collections": collections}, indent=2))
            else:
                print_collection_list(collections)
        else:
            # Inspect collection
            info = inspect_collection(
                client,
                args.collection,
                sample_size=args.sample,
                show_embeddings=args.show_embeddings
            )
            if args.json:
                print(json.dumps(info, indent=2, default=str))
            else:
                print_collection_info(info, show_embeddings=args.show_embeddings)
    except KeyboardInterrupt:
        print("\n\nInterrupted by user.")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()



