"""
Knowledge Health Checker — Phase 4
Author: Hammad Ahmed | AI/ML Engineer

Validates the quality of a tenant's knowledge base before going live.
Reports: chunk count, coverage gaps, average chunk length, duplicate detection,
and a health signal (HEALTHY / WARN / CRITICAL).
"""

import os
import asyncio
import dotenv
from typing import Any, Dict, List
from supabase import create_client

dotenv.load_dotenv()


class KnowledgeHealthChecker:
    """
    Checks a tenant's knowledge_chunks for quality signals.
    Run before going live with a new tenant to catch KB issues early.
    """

    # Thresholds
    MIN_CHUNKS = 10          # Below this: CRITICAL — not enough content
    WARN_CHUNKS = 20         # Below this: WARN — content may be thin
    MIN_AVG_LENGTH = 100     # Characters — very short chunks lose context
    WARN_AVG_LENGTH = 200    # Characters — ideal is 300-500
    MAX_DUPLICATE_RATIO = 0.15  # > 15% duplicates is a problem

    def __init__(self, supabase_client):
        self.supabase = supabase_client

    def check(self, tenant_id: str) -> Dict[str, Any]:
        """
        Runs all health checks and returns a structured report.
        """
        resp = self.supabase.table("knowledge_chunks").select(
            "id, content, category, title"
        ).eq("tenant_id", tenant_id).execute()

        chunks = resp.data or []
        report = self._analyze(chunks)
        report["tenant_id"] = tenant_id
        return report

    def _analyze(self, chunks: List[Dict[str, Any]]) -> Dict[str, Any]:
        total = len(chunks)
        issues = []
        warnings = []

        # 1. Chunk count
        if total < self.MIN_CHUNKS:
            issues.append(f"Only {total} chunks — minimum is {self.MIN_CHUNKS}. Ingest more content.")
        elif total < self.WARN_CHUNKS:
            warnings.append(f"Only {total} chunks — recommend at least {self.WARN_CHUNKS} for good coverage.")

        # 2. Average chunk length
        if total > 0:
            avg_len = sum(len(c.get("content", "")) for c in chunks) / total
        else:
            avg_len = 0

        if avg_len < self.MIN_AVG_LENGTH:
            issues.append(f"Avg chunk length {avg_len:.0f} chars is too short — content likely too fragmented.")
        elif avg_len < self.WARN_AVG_LENGTH:
            warnings.append(f"Avg chunk length {avg_len:.0f} chars — ideal is 300-500 chars.")

        # 3. Duplicate detection (first 80 chars fingerprint)
        fingerprints = []
        for c in chunks:
            content = c.get("content", "")
            fp = " ".join(content[:80].lower().split())
            fingerprints.append(fp)

        unique = len(set(fingerprints))
        dup_ratio = (total - unique) / total if total > 0 else 0
        if dup_ratio > self.MAX_DUPLICATE_RATIO:
            warnings.append(
                f"{dup_ratio*100:.1f}% duplicate chunks detected — "
                f"re-run ingestion with overlap cleanup."
            )

        # 4. Category coverage
        categories = set(c.get("category") for c in chunks if c.get("category"))
        expected_categories = {"services", "pricing", "process", "faq", "contact"}
        missing = expected_categories - {cat.lower() if cat else "" for cat in categories}
        if missing:
            warnings.append(f"Missing expected categories: {', '.join(missing)}.")

        # 5. Empty content check
        empty = [c for c in chunks if not c.get("content", "").strip()]
        if empty:
            issues.append(f"{len(empty)} chunks have empty content.")

        # Determine signal
        if issues:
            signal = "🔴 CRITICAL"
        elif warnings:
            signal = "🟡 WARN"
        else:
            signal = "🟢 HEALTHY"

        return {
            "signal": signal,
            "total_chunks": total,
            "unique_chunks": unique,
            "duplicate_ratio": round(dup_ratio, 3),
            "avg_chunk_length": round(avg_len),
            "categories_found": sorted(categories),
            "issues": issues,
            "warnings": warnings,
            "recommendation": (
                "Knowledge base is ready for launch." if not issues and not warnings
                else "Address issues before going live."
            ),
        }

    def print_report(self, report: Dict[str, Any]):
        print(f"\n{'='*60}")
        print(f"  Knowledge Health Report")
        print(f"  Tenant: {report['tenant_id']}")
        print(f"{'='*60}")
        print(f"  Signal:          {report['signal']}")
        print(f"  Total chunks:    {report['total_chunks']}")
        print(f"  Unique chunks:   {report['unique_chunks']}")
        print(f"  Duplicate ratio: {report['duplicate_ratio']*100:.1f}%")
        print(f"  Avg chunk len:   {report['avg_chunk_length']} chars")
        print(f"  Categories:      {', '.join(report['categories_found']) or 'None tagged'}")

        if report["issues"]:
            print(f"\n  ❌ ISSUES:")
            for i in report["issues"]:
                print(f"     • {i}")

        if report["warnings"]:
            print(f"\n  ⚠️  WARNINGS:")
            for w in report["warnings"]:
                print(f"     • {w}")

        print(f"\n  💡 {report['recommendation']}")
        print(f"{'='*60}\n")


if __name__ == "__main__":
    tenant_id = os.getenv("TEST_TENANT_ID")
    if not tenant_id:
        print("ERROR: Set TEST_TENANT_ID in .env first.")
        exit(1)

    supabase_key = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_KEY")
    supabase = create_client(os.getenv("SUPABASE_URL"), supabase_key)

    checker = KnowledgeHealthChecker(supabase_client=supabase)
    report = checker.check(tenant_id)
    checker.print_report(report)
