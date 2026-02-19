"""Structured data extraction from trucking documents."""
import json
import re

from app.core.config import get_settings
from app.core.logging import logger
from app.models.document import (
    Document, 
    RateConfirmationData, 
    InvoiceData,
    BillOfLadingData,
    ProofOfDeliveryData,
    LumperReceiptData,
    DocumentType
)

try:
    from openai import AsyncOpenAI
    HAS_OPENAI = True
except Exception:
    AsyncOpenAI = None
    HAS_OPENAI = False

class ExtractionService:
    """Extract structured data from trucking documents using LLMs."""
    
    def __init__(self):
        self.settings = get_settings()
        api_key = self.settings.resolved_openai_api_key()
        self._deterministic_mode = api_key is None or not HAS_OPENAI
        
        if not self._deterministic_mode:
            self.client = AsyncOpenAI(
                api_key=api_key,
                base_url=self.settings.openai_base_url
            )
            self.model = self.settings.llm_model
        else:
            if not HAS_OPENAI:
                logger.warning("Running deterministic extraction mode - openai package unavailable")
            else:
                logger.warning("Running deterministic extraction mode - no LLM extraction provider configured")

    @staticmethod
    def _first_group(pattern: str, text: str, flags: int = re.IGNORECASE) -> str | None:
        match = re.search(pattern, text, flags)
        if not match:
            return None
        return match.group(1).strip()

    @staticmethod
    def _money_to_float(value: str | None) -> float | None:
        if not value:
            return None
        try:
            return float(value.replace("$", "").replace(",", "").strip())
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _normalize_identifier(prefix: str, value: str | None) -> str | None:
        if not value:
            return None
        cleaned = re.sub(r"[^A-Za-z0-9]", "", value).upper()
        if not cleaned:
            return None
        if cleaned.startswith(prefix):
            return cleaned
        return f"{prefix}{cleaned}"

    def _extract_load_id(self, text: str) -> str | None:
        patterns = [
            r'Load\s*#\s*:?\s*(LOAD[0-9A-Z-]{3,})',
            r'Load\s*(?:Number|No|ID)\s*:?\s*(LOAD[0-9A-Z-]{3,})',
            r'\b(LOAD[0-9]{3,}[A-Z0-9-]*)\b',
        ]
        for pattern in patterns:
            candidate = self._first_group(pattern, text)
            if candidate:
                return self._normalize_identifier("LOAD", candidate)
        return None

    def _extract_pro_number(self, text: str) -> str | None:
        patterns = [
            r'Pro\s*#\s*:?\s*(PRO[0-9A-Z-]{3,})',
            r'Pro\s*(?:Number|No|ID)\s*:?\s*(PRO[0-9A-Z-]{3,})',
            r'\b(PRO[0-9]{4,}[A-Z0-9-]*)\b',
        ]
        for pattern in patterns:
            candidate = self._first_group(pattern, text)
            if candidate:
                return self._normalize_identifier("PRO", candidate)
        return None

    def _extract_bol_number(self, text: str) -> str | None:
        patterns = [
            r'BOL\s*#\s*:?\s*(BOL[0-9A-Z-]{3,})',
            r'BOL\s*(?:Number|No|ID)\s*:?\s*(BOL[0-9A-Z-]{3,})',
            r'\b(BOL[0-9]{4,}[A-Z0-9-]*)\b',
        ]
        for pattern in patterns:
            candidate = self._first_group(pattern, text)
            if candidate:
                return self._normalize_identifier("BOL", candidate)
        return None

    def _extract_rate_conf_number(self, text: str) -> str | None:
        patterns = [
            r'(?:Rate Conf|Rate Confirmation|Confirmation)\s*#\s*:?\s*(RC[0-9A-Z-]{4,})',
            r'\b(RC[0-9]{6,}[A-Z0-9-]*)\b',
        ]
        for pattern in patterns:
            candidate = self._first_group(pattern, text)
            if candidate:
                return self._normalize_identifier("RC", candidate)
        return None
    
    async def extract_rate_confirmation(self, document: Document) -> RateConfirmationData:
        """Extract structured data from a rate confirmation."""
        
        if self._deterministic_mode:
            return self._extract_rate_confirmation_deterministic(document)
        
        prompt = f"""Extract the following information from this rate confirmation document.
Return ONLY a valid JSON object with these fields (use null if not found):

Fields to extract:
- load_number: The load/pro number
- broker_name: Name of the broker
- broker_mc: Broker's MC number
- shipper_name: Name of shipper/consignor
- pickup_location: Full pickup address/location
- pickup_date: Pickup date/time
- delivery_location: Full delivery address/location  
- delivery_date: Delivery date/time
- rate: Total rate as number (no $ or commas)
- rate_per_mile: Rate per mile as number
- miles: Total miles as number
- equipment_type: Type of equipment (dry van, reefer, flatbed, etc.)
- weight: Cargo weight
- contact_name: Broker contact name
- contact_phone: Broker contact phone
- reference_numbers: List of reference/PO numbers
- accessorials: List of accessorial fees or services
- detention_terms: Detention payment terms

Document text:
{document.raw_text[:8000]}  # Limit to avoid token limits
"""

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You are a data extraction specialist for trucking documents. Return only valid JSON."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.1,
                response_format={"type": "json_object"}
            )
            
            extracted = json.loads(response.choices[0].message.content)
            
            # Clean up numeric fields
            for field in ['rate', 'rate_per_mile', 'miles']:
                if field in extracted and extracted[field]:
                    try:
                        # Remove $ and commas, convert to float
                        val = str(extracted[field]).replace('$', '').replace(',', '').strip()
                        extracted[field] = float(val) if val else None
                    except (ValueError, TypeError):
                        extracted[field] = None
            
            return RateConfirmationData(**extracted)
            
        except Exception as e:
            logger.error("Rate confirmation extraction failed", error=str(e))
            return RateConfirmationData()
    
    def _extract_rate_confirmation_deterministic(self, document: Document) -> RateConfirmationData:
        """Mock extraction using regex patterns."""
        text = document.raw_text
        
        data = RateConfirmationData()
        
        # Load number
        data.load_number = self._extract_load_id(text)
        
        # Broker name
        broker_match = re.search(r'Broker:?\s*([^\n]+?)(?:\s+MC\s*#?:|$)', text, re.IGNORECASE)
        if broker_match:
            data.broker_name = broker_match.group(1).strip()
        
        # MC number
        mc_match = re.search(r'MC #?:?\s*(MC-?\d+)', text, re.IGNORECASE)
        if mc_match:
            data.broker_mc = mc_match.group(1).replace("MC", "MC-").replace("--", "-")
        
        # Rate
        summary_match = re.search(
            r'Line Haul[\s\S]{0,120}?(\d[\d,]*\.\d{2})\s+\$?(\d[\d,]*\.\d{2})\s+\$?(\d[\d,]*\.\d{2})\s+\$?(\d[\d,]*\.\d{2})',
            text,
            re.IGNORECASE,
        )
        if summary_match:
            data.rate = float(summary_match.group(3).replace(",", ""))
            data.rate_per_mile = float(summary_match.group(4).replace(",", ""))
        else:
            total_rate = self._first_group(r'(?:Total Rate|TOTAL DUE)[^\d]*(\d[\d,]*\.?\d*)', text)
            if total_rate:
                parsed = self._money_to_float(total_rate)
                if parsed is not None:
                    data.rate = parsed
        
        # Miles
        miles_match = re.search(r'(?:Mileage|Miles?)\s*:?\s*(\d[\d,]*)', text, re.IGNORECASE)
        if miles_match:
            data.miles = float(miles_match.group(1).replace(',', ''))
        
        # Equipment
        equip_match = re.search(r'Equipment:?\s*([^\n]+)', text, re.IGNORECASE)
        if equip_match:
            data.equipment_type = equip_match.group(1).strip()

        shipper = self._first_group(r'PICKUP\s*\n([^\n]+)', text)
        if shipper:
            data.shipper_name = shipper

        pickup_location = self._first_group(r'PICKUP\s*\n[^\n]+\n([^\n]+)', text)
        if pickup_location:
            data.pickup_location = pickup_location

        delivery_location = self._first_group(r'DELIVERY\s*\n[^\n]+\n([^\n]+)', text)
        if delivery_location:
            data.delivery_location = delivery_location

        pickup_date = self._first_group(r'PICKUP[\s\S]*?Scheduled:\s*([^\n]+)', text)
        if pickup_date:
            data.pickup_date = pickup_date

        delivery_date = self._first_group(r'DELIVERY[\s\S]*?Scheduled:\s*([^\n]+)', text)
        if delivery_date:
            data.delivery_date = delivery_date

        detention_terms = self._first_group(r'Detention Policy:\s*([^\n]+)', text)
        if detention_terms:
            data.detention_terms = detention_terms
        
        # Calculate rate per mile
        if data.rate and data.miles:
            data.rate_per_mile = round(data.rate / data.miles, 2)
        
        return data
    
    async def extract_invoice(self, document: Document) -> InvoiceData:
        """Extract structured data from an invoice."""
        
        if self._deterministic_mode:
            return self._extract_invoice_deterministic(document)
        
        prompt = f"""Extract the following information from this invoice document.
Return ONLY a valid JSON object with these fields (use null if not found):

Fields to extract:
- invoice_number: The invoice number
- load_number: Associated load/pro number
- invoice_date: Date invoice was issued
- due_date: Payment due date
- broker_name: Name of broker/customer
- broker_mc: Broker's MC number
- total_amount: Total invoice amount as number (no $ or commas)
- line_items: Array of line items, each with description and amount
- payment_terms: Payment terms (Net 30, QuickPay, etc.)

Document text:
{document.raw_text[:8000]}
"""

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You are a data extraction specialist for trucking documents. Return only valid JSON."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.1,
                response_format={"type": "json_object"}
            )
            
            extracted = json.loads(response.choices[0].message.content)
            
            # Clean up total_amount
            if 'total_amount' in extracted and extracted['total_amount']:
                try:
                    val = str(extracted['total_amount']).replace('$', '').replace(',', '').strip()
                    extracted['total_amount'] = float(val) if val else None
                except (ValueError, TypeError):
                    extracted['total_amount'] = None
            
            return InvoiceData(**extracted)
            
        except Exception as e:
            logger.error("Invoice extraction failed", error=str(e))
            return InvoiceData()
    
    def _extract_invoice_deterministic(self, document: Document) -> InvoiceData:
        """Mock extraction using regex patterns."""
        text = document.raw_text
        
        data = InvoiceData()
        
        # Invoice number
        inv_match = re.search(r'Invoice #?:?\s*([^\n]+)', text, re.IGNORECASE)
        if inv_match:
            data.invoice_number = inv_match.group(1).strip()
        
        # Load number
        data.load_number = self._extract_load_id(text)
        
        # Broker
        broker_match = re.search(r'Bill To:?\s*([^\n]+)', text, re.IGNORECASE)
        if broker_match:
            data.broker_name = broker_match.group(1).strip()

        mc_match = self._first_group(r'MC:?\s*(MC-?\d+)', text)
        if mc_match:
            data.broker_mc = mc_match.replace("MC", "MC-").replace("--", "-")
        
        # Total amount
        total_match = re.search(r'TOTAL[^\d]*(\d[\d,]*\.?\d*)', text, re.IGNORECASE)
        if total_match:
            data.total_amount = float(total_match.group(1).replace(',', ''))
        
        # Payment terms
        terms_match = re.search(r'Terms:?\s*([^\n]+)', text, re.IGNORECASE)
        if terms_match:
            data.payment_terms = terms_match.group(1).strip()
        
        return data

    async def extract_bill_of_lading(self, document: Document) -> BillOfLadingData:
        """Extract key fields from bill of lading."""
        return self._extract_bill_of_lading_deterministic(document)

    def _extract_bill_of_lading_deterministic(self, document: Document) -> BillOfLadingData:
        text = document.raw_text
        return BillOfLadingData(
            bol_number=self._extract_bol_number(text),
            load_number=self._extract_load_id(text),
            pro_number=self._extract_pro_number(text),
            shipper_name=self._first_group(r'SHIPPER CONSIGNEE\s*\n([^\n]+)', text),
            consignee_name=self._first_group(r'SHIPPER CONSIGNEE[\s\S]*?\n[^\n]+\n([^\n]+)', text),
            pickup_location=self._first_group(r'SHIPPER CONSIGNEE[\s\S]*?\n[^\n]+\n([^\n]+)', text),
            delivery_location=self._first_group(r'SHIPPER CONSIGNEE[\s\S]*?\n[^\n]+\n[^\n]+\n([^\n]+)', text),
            driver_name=self._first_group(r'Driver:?\s*([^\n]+)', text),
            equipment_type=self._first_group(r'Equipment:?\s*([^\n]+)', text),
            weight=self._first_group(r'Weight[^\n]*\n[^\n]*\n[^\n]*\n[^\n]*\n([0-9,]+\s*lbs)', text) or self._first_group(r'(\d[\d,]*\s*lbs)', text),
            reference_number=self._first_group(r'Reference:?\s*([^\n]+)', text),
        )

    async def extract_pod(self, document: Document) -> ProofOfDeliveryData:
        """Extract key fields from proof of delivery."""
        return self._extract_pod_deterministic(document)

    def _extract_pod_deterministic(self, document: Document) -> ProofOfDeliveryData:
        text = document.raw_text
        lower = text.lower()
        condition = self._first_group(r'Condition:\s*(.*)', text)
        return ProofOfDeliveryData(
            load_number=self._extract_load_id(text),
            pro_number=self._extract_pro_number(text),
            bol_number=self._extract_bol_number(text),
            rate_conf_number=self._extract_rate_conf_number(text),
            ship_date=self._first_group(r'Ship Date:\s*([^\n]+?)\s+Delivery Date:', text),
            delivery_date=self._first_group(r'Delivery Date:\s*([^\n]+)', text),
            delivered_to=self._first_group(r'DELIVERED TO:\s*\n([^\n]+)', text),
            signed_for_by=self._first_group(r'Signed for by:\s*([^\n]+)', text),
            condition=condition,
            detention_mentioned=(
                "detention" in lower or "wait" in lower or "delay" in lower
            ),
        )

    async def extract_lumper_receipt(self, document: Document) -> LumperReceiptData:
        """Extract key fields from lumper receipt."""
        return self._extract_lumper_receipt_deterministic(document)

    def _extract_lumper_receipt_deterministic(self, document: Document) -> LumperReceiptData:
        text = document.raw_text
        total_fee = self._money_to_float(self._first_group(r'TOTAL FEE:\s*\$?([0-9,]+(?:\.\d{1,2})?)', text))
        return LumperReceiptData(
            receipt_number=self._normalize_identifier("LMP", self._first_group(r'Receipt #?:?\s*([A-Z0-9-]+)', text)),
            load_number=self._extract_load_id(text),
            pro_number=self._extract_pro_number(text),
            bol_number=self._extract_bol_number(text),
            facility_name=self._first_group(r'Facility:\s*([^\n]+)', text),
            service_time=self._first_group(r'Service Time:\s*([^\n]+)', text),
            total_fee=total_fee,
        )

    def _augment_common_identifiers(self, document: Document) -> None:
        """Capture identifiers on every document for cross-doc matching."""
        text = document.raw_text or ""
        extracted = document.extracted_data or {}

        if "load_number" not in extracted or not extracted.get("load_number"):
            extracted_load = self._extract_load_id(text)
            if extracted_load:
                extracted["load_number"] = extracted_load

        if "pro_number" not in extracted or not extracted.get("pro_number"):
            extracted_pro = self._extract_pro_number(text)
            if extracted_pro:
                extracted["pro_number"] = extracted_pro

        if "bol_number" not in extracted or not extracted.get("bol_number"):
            extracted_bol = self._extract_bol_number(text)
            if extracted_bol:
                extracted["bol_number"] = extracted_bol

        if "rate_conf_number" not in extracted or not extracted.get("rate_conf_number"):
            extracted_rate_conf = self._extract_rate_conf_number(text)
            if extracted_rate_conf:
                extracted["rate_conf_number"] = extracted_rate_conf

        document.extracted_data = extracted
    
    async def classify_document(self, document: Document) -> DocumentType:
        """Classify a document into its type."""
        
        if self._deterministic_mode:
            return self._classify_document_deterministic(document)
        
        prompt = f"""Classify this trucking document into ONE of these categories:
- rate_confirmation: Rate confirmation sheet from broker
- invoice: Invoice for payment
- proof_of_delivery: Proof of delivery document
- bill_of_lading: Bill of lading
- lumper_receipt: Lumper fee receipt
- email: Email communication
- routing_guide: Customer routing guide
- insurance_certificate: Insurance documentation
- policy: Company policy or SOP
- other: None of the above

Return ONLY the category name (single word with underscores).

Document text preview:
{document.raw_text[:2000]}
"""

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You classify trucking documents. Return only the category name."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0,
                max_tokens=20
            )
            
            classification = response.choices[0].message.content.strip().lower()
            
            # Map to enum
            type_map = {
                "rate_confirmation": DocumentType.RATE_CONFIRMATION,
                "invoice": DocumentType.INVOICE,
                "proof_of_delivery": DocumentType.POD,
                "bill_of_lading": DocumentType.BOL,
                "lumper_receipt": DocumentType.LUMPER_RECEIPT,
                "email": DocumentType.EMAIL,
                "routing_guide": DocumentType.ROUTING_GUIDE,
                "insurance_certificate": DocumentType.INSURANCE_CERT,
                "policy": DocumentType.POLICY,
                "other": DocumentType.OTHER
            }
            
            return type_map.get(classification, DocumentType.OTHER)
            
        except Exception as e:
            logger.error("Document classification failed", error=str(e))
            return DocumentType.OTHER
    
    def _classify_document_deterministic(self, document: Document) -> DocumentType:
        """Deterministic classification using filename/content patterns."""
        text = document.raw_text.upper()
        filename = document.filename.upper()
        
        # Check filename first
        if 'RATE' in filename or 'CONFIRMATION' in filename:
            return DocumentType.RATE_CONFIRMATION
        if 'INVOICE' in filename or 'INV' in filename:
            return DocumentType.INVOICE
        if 'POD' in filename or 'DELIVERY' in filename:
            return DocumentType.POD
        if 'BOL' in filename or 'BILL OF LADING' in filename:
            return DocumentType.BOL
        if 'LUMPER' in filename:
            return DocumentType.LUMPER_RECEIPT
        if 'EMAIL' in filename or filename.endswith('.EML'):
            return DocumentType.EMAIL
        if 'ROUTING' in filename or 'GUIDE' in filename:
            return DocumentType.ROUTING_GUIDE
        if 'POLICY' in filename or 'HANDBOOK' in filename:
            return DocumentType.POLICY
        
        # Check content
        if 'RATE CONFIRMATION' in text:
            return DocumentType.RATE_CONFIRMATION
        if 'INVOICE' in text and 'BILL TO' in text:
            return DocumentType.INVOICE
        if 'PROOF OF DELIVERY' in text or 'DELIVERED' in text:
            return DocumentType.POD
        if 'BILL OF LADING' in text or 'SHIPPER' in text and 'CONSIGNEE' in text:
            return DocumentType.BOL
        if 'LUMPER' in text:
            return DocumentType.LUMPER_RECEIPT
        if 'FROM:' in text and 'SUBJECT:' in text:
            return DocumentType.EMAIL
        if 'ROUTING GUIDE' in text:
            return DocumentType.ROUTING_GUIDE
        if 'POLICY' in text or 'PROCEDURES' in text:
            return DocumentType.POLICY
        
        return DocumentType.OTHER
    
    async def extract_all(self, document: Document) -> Document:
        """Extract all relevant data from a document based on its type."""
        
        # Classify if type is OTHER
        if document.document_type == DocumentType.OTHER:
            document.document_type = await self.classify_document(document)
        
        # Extract based on type
        if document.document_type == DocumentType.RATE_CONFIRMATION:
            document.extracted_data = (await self.extract_rate_confirmation(document)).model_dump()
        elif document.document_type == DocumentType.INVOICE:
            document.extracted_data = (await self.extract_invoice(document)).model_dump()
        elif document.document_type == DocumentType.BOL:
            document.extracted_data = (await self.extract_bill_of_lading(document)).model_dump()
        elif document.document_type == DocumentType.POD:
            document.extracted_data = (await self.extract_pod(document)).model_dump()
        elif document.document_type == DocumentType.LUMPER_RECEIPT:
            document.extracted_data = (await self.extract_lumper_receipt(document)).model_dump()
        else:
            document.extracted_data = {}

        self._augment_common_identifiers(document)
        
        return document


# Singleton instance
extraction_service = ExtractionService()
