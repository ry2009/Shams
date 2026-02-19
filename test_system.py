#!/usr/bin/env python3
"""Test script to verify OpenClaw system is working."""

import requests
import json
from pathlib import Path
import sys
import pytest

pytestmark = pytest.mark.skip(reason="Integration script. Run directly with: python test_system.py")

def test_health():
    """Test server health."""
    try:
        resp = requests.get("http://localhost:8000/health", timeout=5)
        if resp.status_code == 200:
            print("✅ Server is healthy")
            return True
    except Exception as e:
        print(f"❌ Server not responding: {e}")
        return False

def upload_document(filepath: Path, doc_type: str = None):
    """Upload a document."""
    try:
        files = {"file": open(filepath, "rb")}
        data = {"document_type": doc_type} if doc_type else {}
        resp = requests.post(
            "http://localhost:8000/documents/upload",
            files=files,
            data=data,
            timeout=30
        )
        if resp.status_code == 200:
            result = resp.json()
            print(f"✅ Uploaded: {filepath.name} -> {result.get('document_id')}")
            return result
        else:
            print(f"❌ Upload failed: {resp.text}")
            return None
    except Exception as e:
        print(f"❌ Upload error: {e}")
        return None

def query_documents(question: str):
    """Query documents via RAG."""
    try:
        resp = requests.post(
            "http://localhost:8000/rag/query",
            json={"query": question, "include_sources": True},
            timeout=30
        )
        if resp.status_code == 200:
            result = resp.json()
            print(f"\n📝 Question: {question}")
            print(f"💡 Answer: {result.get('answer', 'No answer')[:200]}...")
            print(f"📊 Confidence: {result.get('confidence', 0):.2f}")
            sources = result.get('sources', [])
            if sources:
                print(f"📄 Sources: {', '.join(s['filename'] for s in sources[:3])}")
            return result
        else:
            print(f"❌ Query failed: {resp.text}")
            return None
    except Exception as e:
        print(f"❌ Query error: {e}")
        return None

def main():
    print("=" * 60)
    print("OpenClaw System Test")
    print("=" * 60)
    
    # Check server
    if not test_health():
        print("\n⚠️  Server not running. Start it with:")
        print("cd openclaw/backend && python3 -m uvicorn app.main:app --reload")
        sys.exit(1)
    
    # Get server info
    resp = requests.get("http://localhost:8000/", timeout=5)
    info = resp.json()
    print(f"📦 {info['name']} v{info['version']}")
    
    # Upload some documents
    doc_dir = Path(__file__).parent / "sample_data" / "documents"
    
    print("\n📤 Uploading documents...")
    
    # Upload routing guide (text - faster)
    guide_path = doc_dir / "guides" / "Routing_Guide_Walmart.txt"
    if guide_path.exists():
        upload_document(guide_path, "routing_guide")
    
    # Upload a policy
    policy_path = doc_dir / "policies" / "Policy_Driver_Handbook.txt"
    if policy_path.exists():
        upload_document(policy_path, "policy")
    
    # Upload an email
    email_path = list((doc_dir / "emails").glob("*.txt"))[0]
    if email_path:
        upload_document(email_path, "email")
    
    # Wait for embeddings
    print("\n⏳ Waiting for embeddings to process...")
    import time
    time.sleep(3)
    
    # Test queries
    print("\n🔍 Testing queries...")
    
    queries = [
        "What is Walmart's detention policy?",
        "How many hours of free time do we get?",
        "What are the requirements for drivers?",
        "Tell me about load offers",
    ]
    
    for query in queries:
        query_documents(query)
        print()
    
    print("=" * 60)
    print("Test complete!")
    print("=" * 60)

if __name__ == "__main__":
    main()
