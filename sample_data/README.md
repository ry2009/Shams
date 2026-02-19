# OpenClaw Sample Data Generator

Generates realistic synthetic trucking documents for testing the OpenClaw system.

## Generated Documents

| Type | Format | Count | Description |
|------|--------|-------|-------------|
| Rate Confirmations | PDF | 50 | Professional broker rate cons with full details |
| Invoices | PDF | 50 | Matching invoices for each load |
| Bills of Lading | PDF | 50 | Standard BOLs with shipper/consignee info |
| PODs | PDF | 50 | Proof of delivery with signatures |
| Lumper Receipts | PDF | ~10 | Third-party unloading receipts |
| Emails | TXT | ~70 | Load offers, detention requests, etc. |
| Routing Guides | TXT | 2 | Walmart & Amazon guides |
| Policies | TXT | 2 | Driver handbook & safety policy |

## Data Realism

All documents feature:
- **Real brokers**: TQL, Coyote, XPO, Schneider, Landstar, JB Hunt, Uber, Convoy
- **Real shippers**: Walmart, Amazon, Kroger, Home Depot distribution centers
- **Realistic lanes**: Chicago-LA, Dallas-Atlanta, etc. with accurate mileage
- **Interconnected data**: Rate cons match invoices match PODs
- **Realistic issues**: 25% have detention, 20% have lumper fees
- **Proper rates**: Based on 2024 market rates ($1.80-$3.50/mile)

## Usage

### Install Dependencies

```bash
cd openclaw/sample_data
pip install -r requirements.txt
```

### Generate All Documents

```bash
python generate_all.py
```

This creates all documents in `documents/` folder.

### Upload to OpenClaw

```bash
# Start OpenClaw first
cd ..
docker-compose up

# Then upload documents via web UI at http://localhost:3000
# Or use the API:
curl -X POST -F "file=@documents/rate_cons/RateConf_...pdf" http://localhost:8000/documents/upload
```

## Document Structure

```
documents/
├── rate_cons/           # 50 PDF rate confirmations
├── invoices/            # 50 PDF invoices  
├── bols/               # 50 PDF Bills of Lading
├── pods/               # 50 PDF Proof of Delivery
├── lumpers/            # ~10 PDF lumper receipts
├── emails/             # ~70 TXT email conversations
├── guides/             # 2 TXT routing guides
│   ├── Routing_Guide_Walmart.txt
│   └── Routing_Guide_Amazon.txt
└── policies/           # 2 TXT policy docs
    ├── Policy_Driver_Handbook.txt
    └── Policy_Safety.txt
```

## Testing Queries

Once uploaded, try these queries:

```
"What's the rate on load LOAD00001?"
"Show me all loads from TQL to California"
"What's Walmart's detention policy?"
"Which loads had detention fees?"
"What's the average rate per mile?"
"Show me all emails from Coyote"
"What was our total revenue last month?"
```

## Customization

Edit `generate_comprehensive_data.py` to:
- Add more brokers
- Add more lanes
- Change load counts
- Adjust detention/lumper percentages
- Modify rate ranges

## Notes

- Uses `random.seed(42)` for reproducibility
- Document IDs are deterministic for the same seed
- All phone numbers/emails are fake (555 prefix)
- MC numbers are real public info (brokers)
- All rates are realistic for 2024 market conditions
