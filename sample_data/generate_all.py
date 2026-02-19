#!/usr/bin/env python3
"""
OpenClaw Master Data Generator

Generates a complete synthetic trucking dataset including:
- 50 Rate Confirmations (PDF)
- 50 Invoices (PDF)
- 50 BOLs (PDF)
- 50 PODs (PDF)
- ~10-15 Lumper Receipts (PDF)
- ~70-80 Emails (TXT)
- 2 Routing Guides (TXT)
- 2 Company Policies (TXT)

All documents are interconnected with realistic relationships.
"""

import sys
from pathlib import Path
from datetime import datetime

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from generate_comprehensive_data import create_synthetic_dataset
from generate_pdf_documents import RateConfirmationPDF, InvoicePDF
from generate_supporting_docs import BOLPDF, PODPDF, LumperReceiptPDF
from generate_text_docs import generate_all_text_documents


def main():
    """Generate complete synthetic dataset."""
    
    print("=" * 80)
    print("OPENCLAW SYNTHETIC TRUCKING DATASET GENERATOR")
    print("=" * 80)
    print()
    
    output_dir = Path(__file__).parent / "documents"
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Create subdirectories
    for subdir in ['rate_cons', 'invoices', 'bols', 'pods', 'lumpers', 'emails', 'guides', 'policies']:
        (output_dir / subdir).mkdir(exist_ok=True)
    
    # Generate dataset
    num_loads = 50
    loads = create_synthetic_dataset(num_loads)
    
    print("\n" + "=" * 80)
    print("GENERATING PDF DOCUMENTS")
    print("=" * 80)
    
    # Track counts
    stats = {
        'rate_cons': 0,
        'invoices': 0,
        'bols': 0,
        'pods': 0,
        'lumpers': 0,
        'emails': 0,
        'guides': 0,
        'policies': 0
    }
    
    # Generate PDF documents
    print("\n📄 Rate Confirmations & Invoices...")
    for i, load in enumerate(loads):
        # Rate Confirmation
        rc_path = output_dir / "rate_cons" / f"RateConf_{load.rate_confirmation_number}_{load.broker.dba}.pdf"
        rc_gen = RateConfirmationPDF(load)
        rc_gen.generate(rc_path)
        stats['rate_cons'] += 1
        
        # Invoice
        inv_num = f"INV-{datetime.now().year}-{load.load_id}"
        inv_path = output_dir / "invoices" / f"Invoice_{inv_num}_{load.broker.dba}.pdf"
        inv_gen = InvoicePDF(load, inv_num)
        inv_gen.generate(inv_path)
        stats['invoices'] += 1
        
        if (i + 1) % 10 == 0:
            print(f"  ✓ Generated {i+1} rate confirmations and invoices")
    
    print("\n📄 BOLs & PODs...")
    for i, load in enumerate(loads):
        # BOL
        bol_path = output_dir / "bols" / f"BOL_{load.bol_number}.pdf"
        bol_gen = BOLPDF(load)
        bol_gen.generate(bol_path)
        stats['bols'] += 1
        
        # POD
        pod_path = output_dir / "pods" / f"POD_{load.pro_number}.pdf"
        pod_gen = PODPDF(load)
        pod_gen.generate(pod_path)
        stats['pods'] += 1
        
        # Lumper (conditional)
        if load.has_lumper:
            lumper_path = output_dir / "lumpers" / f"Lumper_{load.load_id}.pdf"
            lumper_gen = LumperReceiptPDF(load)
            if lumper_gen.generate(lumper_path):
                stats['lumpers'] += 1
        
        if (i + 1) % 10 == 0:
            print(f"  ✓ Generated {i+1} BOLs and PODs")
    
    # Generate text documents
    print("\n📧 Generating Emails, Guides, and Policies...")
    email_count = generate_all_text_documents(output_dir, loads)
    stats['emails'] = email_count
    stats['guides'] = 2
    stats['policies'] = 2
    
    # Save manifest
    print("\n📝 Saving manifest...")
    manifest = {
        "generated_at": datetime.now().isoformat(),
        "total_loads": len(loads),
        "documents": stats,
        "brokers_used": list(set(l.broker.name for l in loads)),
        "lanes": list(set(f"{l.origin_city}, {l.origin_state} -> {l.destination_city}, {l.destination_state}" for l in loads)),
        "total_revenue": sum(l.total_rate for l in loads),
        "detention_loads": sum(1 for l in loads if l.has_detention),
        "lumper_loads": sum(1 for l in loads if l.has_lumper),
    }
    
    import json
    with open(output_dir / "_manifest.json", "w") as f:
        json.dump(manifest, f, indent=2, default=str)
    
    # Print summary
    print("\n" + "=" * 80)
    print("GENERATION COMPLETE!")
    print("=" * 80)
    print(f"""
📁 Output Directory: {output_dir}

📊 Document Summary:
   • Rate Confirmations:  {stats['rate_cons']}
   • Invoices:            {stats['invoices']}
   • Bills of Lading:     {stats['bols']}
   • Proofs of Delivery:  {stats['pods']}
   • Lumper Receipts:     {stats['lumpers']}
   • Emails:              {stats['emails']}
   • Routing Guides:      {stats['guides']}
   • Policies:            {stats['policies']}
   ─────────────────────────
   • TOTAL:               {sum(stats.values())} documents

💰 Dataset Statistics:
   • Total Loads:         {len(loads)}
   • Total Revenue:       ${manifest['total_revenue']:,.2f}
   • Avg Rate/Mile:       ${manifest['total_revenue'] / sum(l.mileage for l in loads):.2f}
   • Loads w/ Detention:  {manifest['detention_loads']} ({manifest['detention_loads']/len(loads)*100:.0f}%)
   • Loads w/ Lumper:     {manifest['lumper_loads']} ({manifest['lumper_loads']/len(loads)*100:.0f}%)

🚀 Next Steps:
   1. Upload documents to OpenClaw at http://localhost:3000
   2. Test queries like "What's the rate on load LOAD00001?"
   3. Test extraction on rate confirmations and invoices
   4. Generate counter-offers for low-paying loads

📄 Manifest saved to: {output_dir / '_manifest.json'}
""")


if __name__ == "__main__":
    main()
