#!/usr/bin/env python3
"""
Shams AI - Workflow Demo Script

Demonstrates all 4 high-frequency workflows with measurable outcomes:
1. Invoice Packet Assembly (time saved)
2. Detention Claims (revenue recovered)
3. Load Scoring (revenue optimization)
4. Broker Verification (fraud prevention)
"""

import requests
import json
from datetime import datetime, timedelta
import pytest

BASE_URL = "http://localhost:8000"
pytestmark = pytest.mark.skip(reason="Integration demo script. Run directly with: python test_workflows.py")

def print_section(title):
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70)

def test_invoice_packet_workflow():
    """Demo: Invoice Packet Assembly"""
    print_section("WORKFLOW 1: INVOICE PACKET ASSEMBLY")
    print("Goal: Reduce time from 15 min to 90 seconds")
    print()
    
    # Request packet assembly
    response = requests.post(
        f"{BASE_URL}/workflows/invoice-packet",
        json={"load_id": "LOAD00001", "auto_find_documents": True}
    )
    
    if response.status_code == 200:
        packet = response.json()
        print(f"✅ Packet ID: {packet['packet_id']}")
        print(f"   Load: {packet['load_id']}")
        print(f"   Status: {packet['status']}")
        print()
        print("   Documents Found:")
        if packet.get('rate_confirmation'):
            print(f"     ✓ Rate Confirmation: {packet['rate_confirmation']['filename']}")
        else:
            print(f"     ✗ Rate Confirmation: MISSING")
            
        if packet.get('bol'):
            print(f"     ✓ BOL: {packet['bol']['filename']}")
        else:
            print(f"     ✗ BOL: MISSING")
            
        if packet.get('pod'):
            print(f"     ✓ POD: {packet['pod']['filename']}")
        else:
            print(f"     ✗ POD: MISSING")
            
        if packet.get('lumper_receipt'):
            print(f"     ✓ Lumper: {packet['lumper_receipt']['filename']}")
        
        if packet.get('missing_documents'):
            print(f"\n   ⚠️  Missing: {', '.join(packet['missing_documents'])}")
        
        if packet.get('validation_errors'):
            print(f"   ⚠️  Errors: {', '.join(packet['validation_errors'])}")
    else:
        print(f"❌ Error: {response.text}")
    
    # Get metrics
    print("\n   📊 Workflow Metrics:")
    metrics = requests.get(f"{BASE_URL}/workflows/invoice-packet/metrics").json()
    print(f"      Packets generated: {metrics['total_packets_generated']}")
    print(f"      Avg time: {metrics['average_time_seconds']:.1f} seconds")
    print(f"      Missing doc rate: {metrics['missing_document_rate']*100:.1f}%")
    print(f"      Time saved: {metrics['time_saved_vs_manual']:.1f} hours")

def test_detention_workflow():
    """Demo: Detention Claims"""
    print_section("WORKFLOW 2: DETENTION CLAIMS")
    print("Goal: Increase collection from 60% to 85%+")
    print()
    
    # File a claim
    arrival = datetime.now() - timedelta(hours=6)
    unloaded = datetime.now() - timedelta(hours=1)
    
    response = requests.post(
        f"{BASE_URL}/workflows/detention/claim",
        json={
            "load_id": "LOAD00001",
            "facility_name": "Walmart Distribution Center",
            "arrival_time": arrival.isoformat(),
            "unloaded_time": unloaded.isoformat(),
            "rate_per_hour": 50.0,
            "free_time_hours": 2.0,
            "supporting_document_ids": ["doc_123", "doc_456"]
        }
    )
    
    if response.status_code == 200:
        claim = response.json()
        event = claim['event']
        print(f"✅ Claim Generated: {event['event_id']}")
        print(f"   Load: {event['load_id']}")
        print(f"   Facility: {event['facility_name']}")
        print(f"   Total time: {event['total_hours']:.1f} hours")
        print(f"   Free time: {event['free_time_hours']} hours")
        print(f"   Billable: {event['billable_hours']:.1f} hours")
        print(f"   Rate: ${event['rate_per_hour']:.2f}/hr")
        print(f"   TOTAL CLAIM: ${event['total_amount']:.2f}")
        print()
        print(f"   Success probability: {claim['success_probability']*100:.0f}%")
        print()
        print("   Generated Email:")
        print("   " + "-" * 50)
        for line in claim['claim_email_draft'].split('\n')[:10]:
            print(f"   {line}")
        print("   " + "-" * 50)
    else:
        print(f"❌ Error: {response.text}")
    
    # Get metrics
    print("\n   📊 Workflow Metrics:")
    metrics = requests.get(f"{BASE_URL}/workflows/detention/metrics").json()
    print(f"      Claims tracked: {metrics['total_detentions_tracked']}")
    print(f"      Amount claimed: ${metrics['total_amount_claimed']:,.2f}")
    print(f"      Amount collected: ${metrics['total_amount_collected']:,.2f}")
    print(f"      Collection rate: {metrics['collection_rate']:.1f}%")

def test_load_scoring_workflow():
    """Demo: Load Scoring"""
    print_section("WORKFLOW 3: LOAD SCORING")
    print("Goal: 10-15% revenue improvement per truck/week")
    print()
    
    # Score a load
    loads_to_score = [
        {
            "origin": "Chicago, IL",
            "destination": "Los Angeles, CA",
            "rate": 4200,
            "miles": 1745,
            "equipment_type": "Dry Van",
            "pickup_date": (datetime.now() + timedelta(days=1)).isoformat(),
            "broker_name": "TQL",
            "broker_mc": "MC-411443"
        },
        {
            "origin": "Dallas, TX",
            "destination": "Atlanta, GA",
            "rate": 1560,
            "miles": 780,
            "equipment_type": "Dry Van",
            "pickup_date": (datetime.now() + timedelta(days=1)).isoformat(),
            "broker_name": "Coyote",
            "broker_mc": "MC-594188"
        },
        {
            "origin": "Houston, TX",
            "destination": "Denver, CO",
            "rate": 1900,
            "miles": 950,
            "equipment_type": "Reefer",
            "pickup_date": (datetime.now() + timedelta(days=1)).isoformat(),
            "broker_name": "Unknown Broker",
            "broker_mc": "MC-999999"
        }
    ]
    
    print("   Scoring 3 load opportunities...\n")
    
    for i, load in enumerate(loads_to_score, 1):
        response = requests.post(
            f"{BASE_URL}/workflows/load-score",
            json=load
        )
        
        if response.status_code == 200:
            score = response.json()
            print(f"   Load {i}: {load['origin']} → {load['destination']}")
            print(f"   Rate: ${load['rate']:,.0f} | Miles: {load['miles']} | RPM: ${score['rpm']:.2f}")
            print(f"   Market range: ${score['market_rate_low']:.2f} - ${score['market_rate_high']:.2f}")
            print(f"   ├─ Score: {score['total_score']}/100")
            print(f"   └─ Recommendation: {score['recommendation']}")
            
            if score.get('warnings'):
                for warning in score['warnings']:
                    print(f"      ⚠️  {warning}")
            if score.get('opportunities'):
                for opp in score['opportunities']:
                    print(f"      💡 {opp}")
            print()
    
    # Batch score
    print("   📊 Batch Ranking (top loads):\n")
    response = requests.post(
        f"{BASE_URL}/workflows/load-score/batch",
        json=loads_to_score
    )
    
    if response.status_code == 200:
        scores = response.json()
        scores.sort(key=lambda x: x['total_score'], reverse=True)
        
        for i, score in enumerate(scores[:3], 1):
            print(f"   #{i}: {score['origin']} → {score['destination']}")
            print(f"       Score: {score['total_score']}/100 | RPM: ${score['rpm']:.2f} | {score['recommendation']}")
    
    # Get metrics
    print("\n   📊 Workflow Metrics:")
    metrics = requests.get(f"{BASE_URL}/workflows/load-score/metrics").json()
    print(f"      Loads scored: {metrics['total_loads_scored']}")
    print(f"      Acceptance rate: {metrics['acceptance_rate']*100:.1f}%")
    print(f"      Counter rate: {metrics['counter_rate']*100:.1f}%")
    print(f"      Avg revenue/load: ${metrics['avg_revenue_per_load']:,.0f}")

def test_verification_workflow():
    """Demo: Broker Verification"""
    print_section("WORKFLOW 4: BROKER VERIFICATION")
    print("Goal: Block 100% of known fraud attempts")
    print()
    
    test_cases = [
        {
            "name": "Legitimate TQL Load",
            "broker": "Total Quality Logistics",
            "mc": "MC-411443",
            "email": "dispatch@tql.com",
            "expected": "VERIFIED"
        },
        {
            "name": "Suspicious Typosquat",
            "broker": "TQL Logistics",
            "mc": "MC-411443",
            "email": "dispatch@tq1.com",  # Typosquat
            "expected": "SUSPICIOUS"
        },
        {
            "name": "Unknown MC",
            "broker": "Fake Broker Inc",
            "mc": "MC-999999",
            "email": "dispatch@fakebroker.xyz",
            "expected": "REJECT"
        }
    ]
    
    for case in test_cases:
        print(f"   Testing: {case['name']}")
        response = requests.post(
            f"{BASE_URL}/workflows/verify-broker",
            params={
                "broker_name": case['broker'],
                "mc_number": case['mc'],
                "email": case['email']
            }
        )
        
        if response.status_code == 200:
            result = response.json()
            status = result['verification_status']
            risk = result['risk_level']
            
            icon = "✅" if status == "VERIFIED" else "⚠️" if status == "SUSPICIOUS" else "❌"
            print(f"   {icon} Status: {status} | Risk: {risk}")
            
            if result.get('warnings'):
                for warning in result['warnings']:
                    print(f"      ⚠️  {warning}")
        print()
    
    # Get fraud stats
    print("   📊 Fraud Prevention Stats:")
    stats = requests.get(f"{BASE_URL}/workflows/verification/fraud-stats").json()
    print(f"      Total verifications: {stats['total_verifications']}")
    print(f"      Verified: {stats['verified']}")
    print(f"      Suspicious: {stats['suspicious']}")
    print(f"      Blocked: {stats['rejected']}")
    print(f"      Block rate: {stats['block_rate']:.1f}%")

def test_overall_metrics():
    """Demo: Overall Copilot Metrics"""
    print_section("OVERALL COPILOT METRICS")
    print()
    
    response = requests.get(f"{BASE_URL}/workflows/metrics")
    
    if response.status_code == 200:
        metrics = response.json()
        
        print("   📈 USAGE")
        print(f"      Active users: {metrics['active_users']}")
        print(f"      Documents processed: {metrics['documents_processed']}")
        print(f"      Workflows completed: {metrics['workflows_completed']}")
        print()
        
        print("   ⏱️  TIME SAVED (Hours/Week)")
        print(f"      Invoice packets: {metrics['time_saved_invoice_packets']:.1f}")
        print(f"      Detention claims: {metrics['time_saved_detention_claims']:.1f}")
        print(f"      Load research: {metrics['time_saved_load_research']:.1f}")
        print(f"      Policy lookups: {metrics['time_saved_policy_lookups']:.1f}")
        print(f"      ─────────────────────────")
        print(f"      TOTAL: {metrics['total_time_saved']:.1f} hours/week")
        print(f"      Value: ${metrics['total_time_saved'] * 25 * 4:.0f}/month (@ $25/hr)")
        print()
        
        print("   💰 REVENUE IMPACT ($/Month)")
        print(f"      Additional detention: ${metrics['additional_detention_collected']:,.0f}")
        print(f"      Better load selection: ${metrics['improved_load_rates']:,.0f}")
        print(f"      Reduced invoice errors: ${metrics['reduced_invoice_errors']:,.0f}")
        print(f"      ─────────────────────────")
        print(f"      TOTAL REVENUE IMPACT: ${metrics['total_revenue_impact']:,.0f}")
        print()
        
        print("   📊 QUALITY IMPROVEMENTS")
        print(f"      Invoice rejection: {metrics['invoice_rejection_rate_before']*100:.0f}% → {metrics['invoice_rejection_rate_after']*100:.0f}%")
        print(f"      Detention collection: {metrics['detention_collection_rate_before']*100:.0f}% → {metrics['detention_collection_rate_after']*100:.0f}%")
        print()
        
        print("   🎯 ROI")
        print(f"      Monthly subscription: ${metrics['monthly_subscription_cost']:,.0f}")
        print(f"      Monthly value created: ${metrics['monthly_value_created']:,.0f}")
        print(f"      ROI: {metrics['roi_multiple']:.1f}×")

def main():
    print("\n" + "=" * 70)
    print("  SHAMS AI - Ops + Revenue Copilot Demo")
    print("  https://shams.ai")
    print("=" * 70)
    
    # Check server
    try:
        response = requests.get(f"{BASE_URL}/health", timeout=5)
        if response.status_code != 200:
            print("\n❌ Server not responding. Start it with:")
            print("   cd openclaw/backend && python3 -m uvicorn app.main:app --reload")
            return
    except Exception as e:
        print(f"\n❌ Server not running: {e}")
        print("   Start it with:")
        print("   cd openclaw/backend && python3 -m uvicorn app.main:app --reload")
        return
    
    print("\n✅ Server connected\n")
    
    # Run all workflow demos
    test_invoice_packet_workflow()
    test_detention_workflow()
    test_load_scoring_workflow()
    test_verification_workflow()
    test_overall_metrics()
    
    print("\n" + "=" * 70)
    print("  Demo Complete!")
    print("  API Documentation: http://localhost:8000/docs")
    print("=" * 70 + "\n")

if __name__ == "__main__":
    main()
