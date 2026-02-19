# Shams AI - Ops + Revenue Copilot for Trucking

**Turn scattered documents and tribal knowledge into faster, safer, more profitable decisions.**

---

## 🎯 The Problem

Small-to-mid trucking companies (10-100 trucks) are drowning in paperwork:

| Pain Point | Current State | Cost |
|------------|---------------|------|
| Invoice assembly | 15 min per load, searching emails/files | $6/invoice × 200 loads/mo = $1,200 |
| Detention claims | 60% collection rate due to poor documentation | $200 avg × 40% missed = $80/load |
| Load selection | Gut feel, no market data | $0.30/mile below market × 10K mi/mo = $3,000 |
| Fraud detection | Reactive, after the fact | $5,000+ per fraud incident |
| Policy lookups | "Ask the old dispatcher" | 15 min × 10 queries/day = 25 hrs/week |

**Total monthly waste: $5,000+ per dispatcher**

---

## ✅ The Solution

Shams AI is an **Ops + Revenue Copilot** with 4 measurable workflows:

### 1. Invoice Packet Assembly
**What it does:** Finds Rate Con, BOL, POD, Lumper receipts by load ID, validates completeness, flags discrepancies.

**Measurable outcome:**
- Before: 15 minutes searching, matching, validating
- After: 90 seconds automated assembly
- **ROI: 10 hours/week saved per dispatcher**

### 2. Detention Tracking & Claims
**What it does:** Detects detention from PODs, looks up facility policies, generates claim emails with evidence.

**Measurable outcome:**
- Industry average: 60% collection rate
- With Shams: 85%+ collection rate
- **ROI: $400-800/month additional revenue per truck**

### 3. Load Scoring
**What it does:** Scores load opportunities on 0-100 scale using rate vs market, broker credit, facility history.

**Measurable outcome:**
- Before: Gut feel decisions
- After: Data-driven ACCEPT/COUNTER/DECLINE
- **ROI: 10-15% revenue improvement per truck/week**

### 4. Broker Verification
**What it does:** FMCSA lookup, insurance check, email domain validation, fraud pattern detection.

**Measurable outcome:**
- Before: Reactive fraud detection
- After: 100% of known scams blocked at booking
- **ROI: $5,000+ per fraud prevented**

---

## 📊 Live Metrics Dashboard

```
┌─────────────────────────────────────────────────────────────┐
│  SHAMS AI - Ops + Revenue Copilot                          │
│  https://api.shams.ai/workflows/metrics                    │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  TIME SAVED (Hours/Week)                                    │
│  ┌──────────────────────────────────────────┐              │
│  │ Invoice packets:     ████████████ 8.5 hrs │              │
│  │ Detention claims:    ████ 3.2 hrs        │              │
│  │ Load research:       █████ 4.1 hrs       │              │
│  │ Policy lookups:      ██ 2.5 hrs          │              │
│  │                      ───────────────────  │              │
│  │ TOTAL:               18.3 hrs/week       │              │
│  └──────────────────────────────────────────┘              │
│                                                             │
│  REVENUE IMPACT ($/Month)                                   │
│  ┌──────────────────────────────────────────┐              │
│  │ Additional detention:    $1,250         │              │
│  │ Better load selection:   $2,100         │              │
│  │ Fewer invoice errors:      $250         │              │
│  │                          ─────────      │              │
│  │ TOTAL VALUE:             $3,600/mo      │              │
│  │ Subscription cost:         $500/mo      │              │
│  │                          ─────────      │              │
│  │ NET ROI:                 7.2×           │              │
│  └──────────────────────────────────────────┘              │
│                                                             │
│  QUALITY IMPROVEMENTS                                       │
│  • Invoice rejection rate: 5% → 1%                         │
│  • Detention collection: 60% → 85%                         │
│  • Fraud attempts blocked: 12 this month                   │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    SHAMS AI PLATFORM                       │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  DOCUMENT INTAKE              WORKFLOW ENGINE              │
│  ┌──────────────┐            ┌──────────────────┐          │
│  │ PDFs         │───────────▶│ Invoice Packet   │          │
│  │ Emails       │            │ Detention Claims │          │
│  │ Images       │            │ Load Scoring     │          │
│  │ TMS exports  │            │ Fraud Detection  │          │
│  └──────────────┘            └──────────────────┘          │
│         │                             │                    │
│         ▼                             ▼                    │
│  ┌─────────────────────────────────────────┐              │
│  │      RAG + EXTRACTION LAYER            │              │
│  │  • Vector search (ChromaDB)            │              │
│  │  • Document parsing (pdfplumber)       │              │
│  │  • Data extraction (OpenAI)            │              │
│  │  • Citation tracking                   │              │
│  └─────────────────────────────────────────┘              │
│         │                             │                    │
│         ▼                             ▼                    │
│  ┌─────────────────────────────────────────┐              │
│  │         AUDIT & ANALYTICS              │              │
│  │  • Every decision timestamped          │              │
│  │  • Source citations for disputes       │              │
│  │  • ROI calculator with real data       │              │
│  └─────────────────────────────────────────┘              │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

## 💰 Pricing

| Tier | Price | Includes |
|------|-------|----------|
| **Starter** | $299/mo | 1-2 dispatchers, Invoice + Detention workflows |
| **Growth** | $599/mo | 3-5 dispatchers, All workflows, API access |
| **Fleet** | $1,499/mo | 6-15 dispatchers, TMS integration, dedicated support |

**All tiers include:**
- Unlimited document processing
- Unlimited queries
- ROI dashboard
- Audit trail
- Email support

**ROI Guarantee:** If you don't save 3× your subscription cost in 90 days, we'll refund the difference.

---

## 🚀 Traction

| Metric | Value |
|--------|-------|
| Pilot customers | 3 (50-truck carriers) |
| Documents processed | 2,400+ |
| Invoice packets assembled | 180+ |
| Detention claims filed | 45+ |
| Loads scored | 320+ |
| Fraud attempts blocked | 8 |
| Avg time saved | 18 hrs/week per customer |
| Avg revenue lift | $3,200/month per customer |

---

## 🎯 The Ask

**Raising $500K Seed to:**

1. **Product (40%)**: TMS integrations (McLeod, TMW, Samsara)
2. **Sales (35%)**: 3 enterprise customers, case studies
3. **Engineering (25%)**: Real-time market data, mobile app

**Use of funds:**
- 6 months runway to $20K MRR
- 10 referenceable customers
- Clear path to Series A metrics

---

## 🏆 Why Now?

1. **AI is ready**: GPT-4, embeddings cheap enough for SMB
2. **Regulatory tailwind**: FMCSA pushing digitization
3. **Market timing**: Post-COVID driver shortage → need efficiency
4. **Competitive gap**: Enterprise TMS too expensive, nothing for 10-100 truck fleets

---

## 📞 Contact

**Shams AI**  
Ops + Revenue Copilot for Trucking  

Website: https://shams.ai  
API: https://api.shams.ai/docs  
Demo: https://demo.shams.ai  

Email: founders@shams.ai  
Phone: (555) 123-4567

---

*"We don't sell AI. We sell hours back to dispatchers and revenue back to carriers."*
