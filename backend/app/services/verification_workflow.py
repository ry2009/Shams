"""
Broker Verification & Fraud Detection Workflow

Verifies broker legitimacy and detects potential fraud:
1. FMCSA authority check (active MC number)
2. Insurance validation
3. Email domain verification
4. Rate confirmation authenticity checks
5. Known fraud pattern detection

MEASURABLE OUTCOME: Fraud prevention (target: 100% of known scams blocked)
"""

from datetime import datetime
from typing import List, Optional, Dict, Any
import re

from app.models.workflows import BrokerVerificationResult
from app.core.logging import logger


class VerificationWorkflow:
    """
    High-frequency workflow: Broker Verification
    
    Fraud in trucking is rampant:
    - Fake carrier identity theft
    - Phishing emails impersonating brokers
    - Fake rate confirmations
    - Double-brokering
    
    This workflow provides automated verification
    before committing to a load.
    """
    
    # Known legitimate broker domains
    LEGITIMATE_DOMAINS = {
        "tql.com", "coyote.com", "schneider.com", "landstar.com",
        "xpo.com", "jbhunt.com", "uber.com", "convoy.com",
        "chrobinson.com", "dat.com", "truckstop.com",
    }
    
    # Known suspicious patterns
    FRAUD_PATTERNS = [
        r"urgent.*wire.*transfer",
        r"payment.*before.*delivery",
        r"send.*money.*release.*load",
        r"rate.*too.*good.*to.*be.*true",
        r"new.*email.*address.*previous.*hacked",
    ]
    
    # Mock FMCSA data (would come from real API)
    FMCSA_DATA = {
        "MC-411443": {  # TQL
            "name": "Total Quality Logistics, LLC",
            "status": "ACTIVE",
            "insurance": True,
            "insurance_exp": "2025-12-31",
            "safety_rating": "Satisfactory"
        },
        "MC-594188": {  # Coyote
            "name": "Coyote Logistics, LLC",
            "status": "ACTIVE",
            "insurance": True,
            "insurance_exp": "2025-10-15",
            "safety_rating": "Satisfactory"
        },
    }
    
    def __init__(self):
        self.verifications: List[BrokerVerificationResult] = []
        self.fraud_attempts_blocked = 0
    
    async def verify_broker(
        self,
        broker_name: str,
        mc_number: str,
        email: Optional[str] = None,
        phone: Optional[str] = None,
        rate_con_text: Optional[str] = None
    ) -> BrokerVerificationResult:
        """
        Comprehensive broker verification.
        
        Checks:
        1. MC number format and FMCSA lookup
        2. Insurance status
        3. Email domain authenticity
        4. Rate confirmation patterns
        5. Known fraud indicators
        """
        
        result = BrokerVerificationResult(
            broker_name=broker_name,
            mc_number=mc_number,
            verification_timestamp=datetime.utcnow()
        )
        
        # 1. FMCSA Authority Check
        fmcsa_data = self.FMCSA_DATA.get(mc_number.upper())
        if fmcsa_data:
            result.fmscs_authority_status = fmcsa_data["status"]
            result.insurance_on_file = fmcsa_data["insurance"]
            result.insurance_expiration = datetime.strptime(fmcsa_data["insurance_exp"], "%Y-%m-%d")
            result.safety_rating = fmcsa_data["safety_rating"]
            
            # Name match check
            if broker_name.lower() not in fmcsa_data["name"].lower():
                result.warnings.append(f"Broker name '{broker_name}' doesn't match FMCSA record '{fmcsa_data['name']}'")
        else:
            result.warnings.append("MC number not found in FMCSA database")
            result.fmscs_authority_status = "UNKNOWN"
        
        # 2. Email Domain Verification
        if email:
            domain = email.split("@")[-1].lower()
            
            # Check if domain matches known legitimate domains
            known_domain = any(known in domain for known in self.LEGITIMATE_DOMAINS)
            
            # Check for suspicious domains
            suspicious_tlds = [".tk", ".ml", ".ga", ".cf", ".top", ".xyz"]
            suspicious = any(domain.endswith(tld) for tld in suspicious_tlds)
            
            result.email_domain_match = known_domain
            result.email_suspicious = suspicious
            
            if suspicious:
                result.warnings.append(f"Suspicious email domain: {domain}")
            elif not known_domain:
                result.warnings.append(f"Email domain {domain} not in verified list - verify independently")
        
        # 3. Rate Confirmation Analysis
        if rate_con_text:
            # Check for fraud patterns
            text_lower = rate_con_text.lower()
            for pattern in self.FRAUD_PATTERNS:
                if re.search(pattern, text_lower):
                    result.warnings.append(f"Fraud pattern detected: suspicious language in rate confirmation")
                    result.rate_confirmation_authentic = False
                    break
            
            # Check for required elements
            required_elements = ["rate", "pickup", "delivery", "mc"]
            missing = [elem for elem in required_elements if elem not in text_lower]
            if missing:
                result.warnings.append(f"Rate confirmation missing standard elements: {', '.join(missing)}")
        
        # 4. Determine Overall Risk
        risk_score = 0
        if result.fmscs_authority_status != "ACTIVE":
            risk_score += 40
        if not result.insurance_on_file:
            risk_score += 30
        if result.email_suspicious:
            risk_score += 50
        if not result.rate_confirmation_authentic:
            risk_score += 40
        if result.warnings:
            risk_score += len(result.warnings) * 10
        
        if risk_score >= 70:
            result.risk_level = "HIGH"
            result.verification_status = "REJECT"
        elif risk_score >= 30:
            result.risk_level = "MEDIUM"
            result.verification_status = "SUSPICIOUS"
        else:
            result.risk_level = "LOW"
            result.verification_status = "VERIFIED"
        
        if result.verification_status == "REJECT":
            self.fraud_attempts_blocked += 1
        
        self.verifications.append(result)
        
        logger.info(
            "Broker verification complete",
            broker=broker_name,
            mc=mc_number,
            status=result.verification_status,
            risk=result.risk_level,
            warnings=len(result.warnings)
        )
        
        return result
    
    async def verify_email_domain(self, email: str) -> Dict[str, Any]:
        """Quick check of email domain."""
        domain = email.split("@")[-1].lower()
        
        # Check for slight misspellings (typosquatting)
        typosquats = {
            "tql.com": ["tq1.com", "tqi.com", "tql.net", "tql.org"],
            "coyote.com": ["coyotte.com", "coyotee.com", "coyote.net"],
            "schneider.com": ["schne1der.com", "schnelder.com"],
        }
        
        for legit, fakes in typosquats.items():
            if domain in fakes:
                return {
                    "valid": False,
                    "warning": f"Possible typosquatting: {domain} vs {legit}",
                    "risk": "HIGH"
                }
        
        # Check legitimate list
        is_legit = any(known in domain for known in self.LEGITIMATE_DOMAINS)
        
        return {
            "valid": True,
            "known_legitimate": is_legit,
            "domain": domain,
            "risk": "LOW" if is_legit else "MEDIUM"
        }
    
    def get_fraud_stats(self) -> Dict[str, Any]:
        """Get fraud prevention statistics."""
        total = len(self.verifications)
        verified = sum(1 for v in self.verifications if v.verification_status == "VERIFIED")
        suspicious = sum(1 for v in self.verifications if v.verification_status == "SUSPICIOUS")
        rejected = sum(1 for v in self.verifications if v.verification_status == "REJECT")
        
        return {
            "total_verifications": total,
            "verified": verified,
            "suspicious": suspicious,
            "rejected": rejected,
            "fraud_attempts_blocked": self.fraud_attempts_blocked,
            "block_rate": (rejected / total * 100) if total > 0 else 0
        }


# Singleton
verification_workflow = VerificationWorkflow()
