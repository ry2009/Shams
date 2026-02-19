"""
Generate text-based documents: Emails, Routing Guides, Company Policies
"""

from datetime import datetime, timedelta
from pathlib import Path
from typing import List
import random

from generate_comprehensive_data import Load, BROKERS, SHIPPERS


class EmailGenerator:
    """Generate realistic trucking-related emails."""
    
    EMAIL_TEMPLATES = {
        'load_offer': [
            {
                'subject': 'Load Available: {origin} to {destination} - ${rate}',
                'body': '''Hi,

I have a load available that might work for you:

Pickup: {pickup_location}
Date: {pickup_date}

Delivery: {delivery_location}  
Date: {delivery_date}

Rate: ${rate}
Miles: {miles}
RPM: ${rpm}
Equipment: {equipment}
Weight: {weight} lbs

Load #{load_number}
Ref: {reference}

This is a {broker_name} load. MC: {mc_number}

Let me know if you're interested and I can send over the rate con.

Thanks,
{sender_name}
{sender_title}
{sender_phone}
dispatch@{broker_domain}
'''
            },
            {
                'subject': 'URGENT: {equipment} Needed {origin} to {destination}',
                'body': '''Driver/Owner Operator,

Need coverage ASAP:

{origin}, {origin_state} → {destination}, {dest_state}
{pacckup_date} pickup, {delivery_date} delivery

${rate} all-in ({miles} miles = ${rpm}/mile)

{equipment} | {weight} lbs | {pallets} pallets

Load ID: {load_number}
Broker: {broker_name} (MC {mc_number})

Can you cover? Reply ASAP - this won't last.

--
{sender_name}
Broker Agent
{sender_phone}
dispatch@{broker_domain}
'''
            }
        ],
        'rate_confirmation_followup': [
            {
                'subject': 'RE: Rate Confirmation {rate_conf} - Driver Info Needed',
                'body': '''Thanks for booking this load with {broker_name}.

Please reply with:
- Driver name and phone number
- Truck and trailer numbers
- Expected pickup time

Load Details:
Load # {load_number}
Pickup: {pickup_location}
{pickup_date}

Delivery: {delivery_location}
{delivery_date}

Rate: ${rate}

Tracking link will be sent once driver is assigned.

{sender_name}
Operations
{sender_phone}
'''
            }
        ],
        'detention_request': [
            {
                'subject': 'Detention Request - Load {load_number} - {hours} hours',
                'body': '''Hi {broker_name} Team,

Requesting detention payment for load {load_number}.

Details:
- Pro #: {pro_number}
- Date: {delivery_date}
- Facility: {facility_name}
- Detention Time: {hours} hours
- Rate: ${rate}/hr
- Total Due: ${total}

Driver checked in at {checkin_time} and was not unloaded until {checkout_time}. Facility signed detention form attached.

Please confirm this will be included on the invoice.

Thanks,
{driver_name}
Driver ID: {driver_id}
{driver_phone}
'''
            }
        ],
        'invoice_inquiry': [
            {
                'subject': 'Invoice {invoice_number} - Payment Status?',
                'body': '''Hello Accounts Payable,

Following up on invoice {invoice_number} for load {load_number}.

Invoice Date: {invoice_date}
Amount: ${amount}
Terms: Net {net_days}

Can you provide payment status? This load delivered on {delivery_date}.

Attached: Invoice, POD, BOL, Lumper receipt (if applicable)

Please advise.

Best regards,
{sender_name}
Accounting
{company_phone}
'''
            }
        ],
        'claim_notification': [
            {
                'subject': 'Cargo Claim - Load {load_number} - {claim_type}',
                'body': '''CLAIM NOTIFICATION

Load: {load_number}
Pro #: {pro_number}
Date of Loss: {incident_date}
Claim Type: {claim_type}
Estimated Amount: ${amount}

Description:
{description}

Supporting Documents Attached:
- BOL
- POD  
- Photos
- Repair Estimate

Please confirm receipt and provide claim number.

{sender_name}
Safety Department
{company_phone}
'''
            }
        ]
    }
    
    def __init__(self, load: Load = None):
        self.load = load
        self.sender_names = ['Mike Johnson', 'Sarah Williams', 'Chris Rodriguez', 'Jennifer Chen', 'David Thompson']
        self.titles = ['Dispatcher', 'Broker Agent', 'Operations Manager', 'Load Coordinator']
    
    def generate_load_offer_email(self, load: Load = None) -> dict:
        """Generate a load offer email."""
        if load is None:
            load = self.load
        
        template = random.choice(self.EMAIL_TEMPLATES['load_offer'])
        sender = random.choice(self.sender_names)
        
        subject = template['subject'].format(
            origin=load.origin_city,
            destination=load.destination_city,
            rate=f"{load.total_rate:,.0f}",
            equipment=load.equipment_type.value
        )
        
        body = template['body'].format(
            pickup_location=f"{load.shipper.facility_name}, {load.shipper.city}, {load.shipper.state}",
            delivery_location=f"{load.consignee.facility_name}, {load.consignee.city}, {load.consignee.state}",
            pickup_date=load.pickup_scheduled.strftime('%A, %B %d'),
            delivery_date=load.delivery_scheduled.strftime('%A, %B %d'),
            rate=f"{load.total_rate:,.2f}",
            miles=f"{load.mileage:,}",
            rpm=f"{load.rate_per_mile:.2f}",
            equipment=load.equipment_type.value,
            weight=f"{load.weight:,}",
            pallets=load.pallets,
            load_number=load.load_id,
            reference=load.reference_numbers[0] if load.reference_numbers else 'N/A',
            broker_name=load.broker.name,
            mc_number=load.broker.mc_number,
            broker_domain=load.broker.email_domain,
            sender_name=sender,
            sender_title=random.choice(self.titles),
            sender_phone=load.broker.phone,
            origin=load.origin_city,
            origin_state=load.origin_state,
            destination=load.destination_city,
            dest_state=load.destination_state,
            pacckup_date=load.pickup_scheduled.strftime('%m/%d'),
        )
        
        return {
            'from': f'{sender} <dispatch@{load.broker.email_domain}>',
            'to': 'dispatch@yourcompany.com',
            'subject': subject,
            'date': (datetime.now() - timedelta(days=random.randint(1, 30))).strftime('%a, %d %b %Y %H:%M:%S -0500'),
            'body': body
        }
    
    def generate_detention_email(self, load: Load) -> dict:
        """Generate a detention request email."""
        if not load.has_detention:
            return None
        
        template = random.choice(self.EMAIL_TEMPLATES['detention_request'])
        detention_rate = int(load.detention_amount / load.detention_hours)
        
        checkin = load.actual_delivery - timedelta(hours=load.detention_hours+2) if load.actual_delivery else datetime.now()
        checkout = load.actual_delivery if load.actual_delivery else datetime.now()
        
        body = template['body'].format(
            broker_name=load.broker.dba,
            load_number=load.load_id,
            pro_number=load.pro_number,
            delivery_date=load.actual_delivery.strftime('%m/%d/%Y') if load.actual_delivery else '',
            facility_name=load.consignee.facility_name,
            hours=f"{load.detention_hours:.1f}",
            rate=detention_rate,
            total=f"{load.detention_amount:,.2f}",
            checkin_time=checkin.strftime('%H:%M'),
            checkout_time=checkout.strftime('%H:%M'),
            driver_name=f"{load.driver.first_name} {load.driver.last_name}",
            driver_id=load.driver.driver_id,
            driver_phone=load.driver.phone
        )
        
        return {
            'from': f"{load.driver.first_name} {load.driver.last_name} <{load.driver.driver_id}@yourcompany.com>",
            'to': f"claims@{load.broker.email_domain}",
            'subject': f"Detention Request - Load {load.load_id} - {load.detention_hours:.1f} hours",
            'date': (load.actual_delivery + timedelta(days=1)).strftime('%a, %d %b %Y %H:%M:%S -0500') if load.actual_delivery else datetime.now().strftime('%a, %d %b %Y %H:%M:%S -0500'),
            'body': body
        }
    
    def save_email(self, email: dict, output_path: Path):
        """Save email as a text file."""
        with open(output_path, 'w') as f:
            f.write(f"From: {email['from']}\n")
            f.write(f"To: {email['to']}\n")
            f.write(f"Subject: {email['subject']}\n")
            f.write(f"Date: {email['date']}\n")
            f.write("-" * 60 + "\n\n")
            f.write(email['body'])


class RoutingGuideGenerator:
    """Generate customer routing guides."""
    
    def __init__(self):
        self.shippers = SHIPPERS
    
    def generate_walmart_guide(self, output_path: Path):
        """Generate Walmart routing guide."""
        content = '''WALMART TRANSPORTATION ROUTING GUIDE
Effective Date: January 1, 2024
Version 3.2

================================================================================
CONTACT INFORMATION
================================================================================

Dispatch/Operations: (800) 555-0199 (Available 24/7)
After Hours Emergency: (800) 555-0299
Email: carrier@walmart.com
Website: https://carrier.walmart.com

================================================================================
APPOINTMENT SCHEDULING
================================================================================

Advance Notice Required:
- Standard deliveries: 48 hours minimum
- High-volume facilities (FC 60xx series): 72 hours minimum
- Peak season (Nov 1 - Jan 15): 96 hours minimum

How to Schedule:
1. Visit https://carrier.walmart.com/appointments
2. Enter PRO number or PO number
3. Select available time slot
4. Print confirmation receipt

No-Walk-In Policy:
All deliveries require a scheduled appointment. Drivers arriving without 
an appointment will be turned away.

Rescheduling:
Appointments may be rescheduled up to 24 hours before scheduled time without 
penalty. Within 24 hours, contact dispatch immediately.

================================================================================
RECEIVING HOURS BY FACILITY TYPE
================================================================================

Regional Distribution Centers (RDC):
Monday - Friday: 06:00 - 22:00
Saturday: 08:00 - 18:00
Sunday: CLOSED (Emergency receiving only with prior approval)

Food Distribution Centers (FDC):
Monday - Sunday: 24 hours (Appointment required)

E-commerce Fulfillment Centers (FC):
Monday - Saturday: 07:00 - 20:00
Sunday: CLOSED

================================================================================
CHECK-IN PROCEDURE
================================================================================

Required at Gate:
1. Valid CDL with HazMat endorsement (if applicable)
2. Proof of insurance (COI)
3. Appointment confirmation
4. Bill of Lading
5. Driver ID badge (obtain at first visit, renew annually)

Security Process:
- All drivers must pass background check
- No firearms or weapons permitted on property
- Random drug/alcohol testing may be required
- Trucks subject to search

Check-In Time:
Arrive NO EARLIER than 2 hours before appointment. Late arrivals (after 
30-minute grace period) require rescheduling.

================================================================================
DETENTION POLICY
================================================================================

Free Time:
- Standard unload: 2 hours from check-in
- Live unload: 2 hours from backed into door
- Drop & hook: 1 hour

Detention Rate: $40.00 per hour (or portion thereof)

Requirements for Payment:
1. Driver must request detention BEFORE leaving facility
2. Obtain signed detention authorization form from receiver
3. Note detention start/end times on BOL
4. Submit within 30 days of delivery

Exclusions:
- Delays due to carrier paperwork errors
- Late arrivals (after scheduled appointment + 30 min)
- Driver breaks/meals
- Equipment issues (reefer fueling, etc.)

================================================================================
LUMPER FEES
================================================================================

Walmart uses third-party lumper services at select locations.

Authorized Lumper Providers:
- Lumper Services Inc (LSI)
- Freight unloading Solutions (FUS)

Lumper Fee Reimbursement:
- Maximum reimbursement: $175 without prior approval
- Receipt required (must show: facility, date, load #, amount)
- Submit with invoice as separate line item
- LSI fees paid directly to provider (no carrier reimbursement needed)

================================================================================
PALLET POLICY
================================================================================

Standard Pallet Requirements:
- 48" x 40" 4-way entry wood pallets
- Grade A or B quality
- No broken boards or protruding nails

Pallet Exchange:
- Not offered at Walmart facilities
- Drivers responsible for pallet disposal

Chep/iGPS:
- Walmart accepts Chep and iGPS pallets
- Carrier responsible for pallet exchange reconciliation

================================================================================
TRAILER REQUIREMENTS
================================================================================

Dry Van:
- 53' x 102" interior dimensions minimum
- Air ride suspension required
- E-track or logistics posts every 24"
- DOT inspection current

Reefer:
- All requirements above PLUS:
- Pre-cool to required temperature before arrival
- Continuous temperature monitoring
- Download capability for reefer unit
- Fuel level minimum 75% at check-in

Temperature Requirements by Commodity:
- Frozen: 0°F to -10°F
- Produce: 34°F to 38°F
- Dairy: 34°F to 38°F
- Meat: 28°F to 32°F

================================================================================
HAZARDOUS MATERIALS
================================================================================

Advance Notice: 48 hours required for all hazmat loads

Required Documentation:
- SDS sheets for all products
- Emergency response guide
- Proper placarding (check at gate)

Restrictions:
- No explosives
- No poison inhalation hazards (PIH)
- Limited quantities of flammables (contact dispatch)

================================================================================
CLAIMS & DISPUTES
================================================================================

Damage Reporting:
- Notify receiver immediately
- Photograph damage before unloading
- Document on BOL
- Submit claim within 9 months

Shortage Reporting:
- Count and verify at time of delivery
- Note discrepancies on POD
- Receiver signature required acknowledging shortage

Contact for Claims:
claims@walmart.com
(800) 555-0399

================================================================================
QUICK PAY / PAYMENT TERMS
================================================================================

Standard Terms: Net 30

QuickPay Options:
- Next Day: 3.5% fee
- 2-Day: 2.5% fee  
- 5-Day: 1.5% fee

To Enroll: Contact accounts.payable@walmart.com

Required for Payment:
- Signed rate confirmation
- BOL with receiver signature
- POD showing delivery date/time
- Clean accessorial documentation

================================================================================
SAFETY REQUIREMENTS
================================================================================

Personal Protective Equipment (PPE):
- ANSI approved safety vest (orange or yellow)
- Closed-toe shoes (no sandals)
- Hard hat in designated areas

Yard Speed: 5 MPH maximum

Parking:
- Designated visitor parking only
- No idling in dock areas
- Turn off engines at dock doors

Emergency Procedures:
- Report all accidents immediately to security
- Evacuation routes posted at all facilities
- Assembly point: Visitor parking area

================================================================================
PROHIBITED ITEMS
================================================================================

- Tobacco products (no smoking on property)
- Alcohol or drugs
- Weapons of any kind
- Pets (service animals excepted with documentation)
- Unauthorized passengers
- Recording devices (cameras, phones in dock areas)

================================================================================
FACILITY-SPECIFIC NOTES
================================================================================

SHELBYVILLE, IN (DC 6094):
- Weigh station on-site - all trucks must scale
- Driver lounge available (showers, vending)
- Overnight parking permitted with security approval

JOHNSTOWN, NY (DC 6095):
- Winter weather restrictions Nov 1 - Apr 1
- Chain requirements posted daily
- Call (518) 555-0123 for road conditions

PHOENIX, AZ (DC 6096):
- Summer heat restrictions May 1 - Oct 1
- Reefer units monitored for performance
- Shade parking available for reefers

================================================================================
VERSION HISTORY
================================================================================

v3.2 - Jan 1, 2024: Updated detention rates, added FDC hours
v3.1 - Aug 15, 2023: Revised lumper policy
v3.0 - Jan 1, 2023: Major revision - added e-commerce FCs

================================================================================

This routing guide is a binding part of all rate confirmations issued by 
Walmart Transportation. Failure to comply may result in:
- Accessorial chargebacks
- Service failures
- Removal from approved carrier list

Last Updated: January 1, 2024
Questions: carrier@walmart.com
'''
        
        with open(output_path, 'w') as f:
            f.write(content)
        
        return output_path
    
    def generate_amazon_guide(self, output_path: Path):
        """Generate Amazon routing guide."""
        content = '''AMAZON LOGISTICS CARRIER ROUTING GUIDE
Version 2024.1

================================================================================
TABLE OF CONTENTS
================================================================================

1. Appointment Requirements
2. Check-In Process
3. Dock Operations
4. Detention & Layover
5. Equipment Requirements
6. Safety & Compliance
7. Payment Terms

================================================================================
1. APPOINTMENT REQUIREMENTS
================================================================================

Mandatory Appointment:
ALL deliveries to Amazon facilities require a scheduled appointment. No 
exceptions. Drivers without appointments will be denied entry.

Scheduling Lead Time:
- Standard FC: 72 hours minimum
- Same Day FC (SDF): 4 hours minimum
- Prime Now/In-Store (PNSI): 24 hours minimum

How to Book:
1. Log into Amazon Relay app
2. Select "Book Appointment"
3. Enter load details (BOL, PO, or PRO)
4. Choose available slot
5. Confirm appointment

Appointment Modification:
- Reschedule: Up to 4 hours before appointment without penalty
- Cancel: Up to 8 hours before appointment
- Late cancellation: May result in service failure charge

================================================================================
2. CHECK-IN PROCESS
================================================================================

Required Documents:
□ Valid CDL
□ Vehicle registration  
□ Insurance certificate
□ Appointment confirmation (QR code preferred)
□ Bill of Lading
□ Seal verification form (for sealed loads)

Yard Entry:
- Stop at guard shack
- Present QR code or appointment number
- Receive yard location assignment
- Follow directed route to assigned door

Check-In Time Window:
Arrive within 30 minutes of scheduled appointment. Early arrivals (before 
-30 min) must wait in designated area. Late arrivals (after +30 min) require 
appointment rescheduling.

================================================================================
3. DOCK OPERATIONS
================================================================================

Backing Procedure:
- Wait for door assignment via Amazon Relay app
- Back only when light turns green
- Chock wheels before exiting cab
- Verify trailer number matches assignment

Unloading:
- Driver may remain in cab or designated waiting area
- Do not enter building without escort
- Unloading typically 2-4 hours for live unload
- Drop & hook available at select locations

Seal Integrity:
- High-security loads require seal verification
- Photo of seal required at pickup and delivery
- Broken/missing seals must be reported immediately

================================================================================
4. DETENTION & LAYOVER
================================================================================

Detention - Live Unload:
- Free time: 2 hours from backed into door
- Rate: $50.00 per hour
- Maximum: $500 per occurrence
- Must be requested through Amazon Relay app
- Automatically calculated based on scan times

Detention - Drop & Hook:
- Free time: 1 hour
- Rate: $50.00 per hour
- Maximum: $200 per occurrence

Layover:
- Qualification: 24+ hour delay not caused by carrier
- Rate: $300 per day
- Requires advance approval from Amazon Operations

Submission:
All detention/layover requests must be submitted within 7 days of delivery 
through Amazon Relay app. Late submissions not accepted.

================================================================================
5. EQUIPMENT REQUIREMENTS
================================================================================

Trailer Specifications:
- 53' dry van, swing doors (roll-up doors not accepted)
- Air ride suspension
- Interior height minimum 110"
- E-track or logistics posts
- DOT inspection current

Reefer Requirements:
- Temperature capability: -20°F to 70°F
- Data logger with download capability
- Fuel tank minimum 50% at delivery
- Pre-cool to setpoint before arrival

Prohibited Equipment:
- Flatbeds
- Step decks
- Straight trucks (except for PNSI)
- Damaged or leaking trailers
- Trailers with strong odors

================================================================================
6. SAFETY & COMPLIANCE
================================================================================

Personal Protective Equipment:
- High-visibility vest (yellow or orange)
- Closed-toe shoes
- Safety glasses in designated areas

Facility Rules:
- Speed limit: 5 MPH
- No smoking anywhere on property
- No weapons
- No photography or recording
- Stay in designated areas only
- No sleeping in cabs while in dock (idling rules apply)

Carrier Performance Score:
Amazon tracks carrier performance including:
- On-time performance (OTP)
- Appointment compliance
- Rejection rates
- Safety incidents
- Claim rates

Low scores may result in load restriction or contract termination.

================================================================================
7. PAYMENT TERMS
================================================================================

Standard Payment: Net 30

QuickPay Available:
- 2-Day QuickPay: 2.0% fee
- 5-Day QuickPay: 1.0% fee

Auto-Settlement Option:
Enroll in Amazon Relay for automatic payment processing. Reduces payment 
processing time by 3-5 days.

Required for Payment:
- Signed rate confirmation
- BOL with delivery scan
- POD through Amazon Relay
- No pending claims or disputes

Invoice Submission:
Submit all invoices through Amazon Relay app or carrier portal. Email 
invoices not accepted.

================================================================================
SUPPORT CONTACTS
================================================================================

Technical Support (Relay App): support@amazonrelay.com
Payment Inquiries: carrier-payments@amazon.com
Safety Issues: carrier-safety@amazon.com

Emergency: Call facility number or 911

================================================================================

By hauling for Amazon Logistics, you agree to comply with all terms in this 
routing guide. Amazon reserves the right to update this guide with 30 days 
notice.

Last Updated: January 2024
'''
        
        with open(output_path, 'w') as f:
            f.write(content)
        
        return output_path


class PolicyGenerator:
    """Generate company policy documents."""
    
    def generate_driver_policy(self, output_path: Path):
        """Generate driver handbook/policy."""
        content = '''DRIVER POLICY & PROCEDURES MANUAL
[Your Company Name]
Effective Date: January 1, 2024

================================================================================
SECTION 1: EMPLOYMENT POLICIES
================================================================================

1.1 EMPLOYMENT CLASSIFICATION

All drivers are classified as follows:
- Company Drivers: W-2 employees
- Owner Operators: Independent contractors (1099)

Classification determines benefit eligibility, tax treatment, and operational 
requirements. Misclassification is strictly prohibited.

1.2 DRUG AND ALCOHOL POLICY

This company maintains a ZERO TOLERANCE policy for drug and alcohol use.

Pre-Employment Testing:
All new hires must pass a DOT 5-panel drug screen before operating company 
equipment.

Random Testing:
Company participates in a random testing consortium. Drivers selected for 
random testing must report within 2 hours of notification.

Post-Accident Testing:
Required for:
- Fatalities
- Bodily injury requiring medical treatment
- Disabling damage to vehicle requiring tow

Reasonable Suspicion:
Supervisors trained in detection may require testing based on observed 
behavior.

Refusal to Test:
Refusal is treated as a positive test and will result in immediate 
termination and DOT reporting.

1.3 HOURS OF SERVICE COMPLIANCE

All drivers must comply with Federal Hours of Service regulations:

Property-Carrying Drivers:
- 11-hour driving limit after 10 consecutive hours off-duty
- 14-hour on-duty limit
- 30-minute break after 8 hours of driving time
- 60/70 hour limit in 7/8 consecutive days

ELD Mandate:
All trucks are equipped with FMCSA-registered ELDs. Tampering with or 
disabling ELDs is prohibited and grounds for immediate termination.

Personal Conveyance:
Limited use permitted per FMCSA guidance. Must be properly documented in ELD.
Yard moves must use designated status.

1.4 SAFETY INCENTIVE PROGRAM

Quarterly bonuses available for drivers who meet:
- Zero preventable accidents
- Zero violations (moving or DOT)
- 100% on-time delivery rate
- Clean CSA score

Bonus Tiers:
- Gold: $1,000 (perfect quarter)
- Silver: $500 (1 minor incident allowed)
- Bronze: $250 (2 minor incidents allowed)

================================================================================
SECTION 2: OPERATIONAL PROCEDURES
================================================================================

2.1 PRE-TRIP INSPECTIONS

Required before every trip:
□ Engine compartment check
□ Fluid levels (oil, coolant, washer fluid)
□ Brake inspection
□ Tire condition and pressure
□ Lights and reflectors
□ Coupling devices
□ Cargo securement (if loaded)

Documentation:
Pre-trip inspection must be documented in DVIR (Driver Vehicle Inspection 
Report) and ELD. Defects must be reported immediately.

2.2 LOAD ACCEPTANCE PROCEDURES

Before accepting any load, verify:
□ Rate confirmation received and signed
□ Pickup/delivery addresses confirmed
□ Appointment times scheduled
□ Weight legal (80,000 lbs GVW max)
□ Dimensions legal (13'6" height, 8'6" width)
□ Commodity matches equipment type
□ Shipper/broker legitimate (verify MC if unknown)

Red Flags - DO NOT ACCEPT:
- Rates significantly below market
- Vague pickup/delivery locations
- Requests to use personal payment apps
- Pressure to skip insurance verification
- Communication from suspicious email domains

2.3 PICKUP PROCEDURES

Arrival:
- Arrive within 15 minutes of scheduled appointment
- Check in with shipper/receiver immediately
- Present BOL and rate confirmation
- Verify load matches documentation

Loading:
- Remain in cab during loading unless assisting
- Verify weight after loading
- Check securement before departure
- Photograph loaded trailer (4 sides + interior)
- Obtain signed BOL with piece count

Documentation Required:
- Shipper signature on BOL
- Pickup date/time
- Seal number (if applicable)
- Reference numbers

2.4 DELIVERY PROCEDURES

Check-In:
- Arrive within appointment window
- Check in with receiver
- Present BOL and rate confirmation

Unloading:
- Note check-in time for detention calculations
- Obtain receiver signature on BOL
- Note delivery date/time
- Photograph empty trailer
- Collect POD (Proof of Delivery)

Detention:
- If delay exceeds 2 hours, request detention authorization
- Get signature on detention form
- Note delay reason

2.5 ACCIDENT PROCEDURES

In case of accident:

1. SAFETY FIRST - Check for injuries, call 911 if needed
2. SECENE - Prevent further damage (flares, cones)
3. NOTIFY - Call company immediately: [EMERGENCY NUMBER]
4. DOCUMENT - Photos of all vehicles, damage, scene
5. EXCHANGE - Get other driver's info (license, insurance, registration)
6. WITNESSES - Get contact info for any witnesses
7. STATEMENTS - Do not admit fault or sign anything except police report

Required Documentation:
- Police report number
- Photos of scene
- Other driver information
- Witness statements
- Company accident report form

DOT Reportable Accidents:
Must be reported to company within 24 hours if:
- Fatality
- Injury requiring medical treatment away from scene
- Vehicle towed due to damage

================================================================================
SECTION 3: EQUIPMENT POLICIES
================================================================================

3.1 VEHICLE MAINTENANCE

Preventive Maintenance:
- Oil changes: Every 25,000 miles or 6 months
- Tire rotations: Every 50,000 miles
- DOT inspections: Annual (company schedules)

Driver Responsibilities:
- Report all defects immediately
- Monitor fluid levels
- Keep cab clean and organized
- No unauthorized modifications

3.2 CARGO SECUREMENT

All cargo must be secured per FMCSA regulations:
- 10,001+ lbs: Minimum 2 tie-downs + 1 per 10ft
- Blocking and bracing as needed
- Edge protectors to prevent strap damage
- Driver responsible for load securement

3.3 REEFER OPERATIONS

Temperature Management:
- Pre-cool trailer before loading
- Verify setpoint matches BOL requirements
- Monitor temperature every 4 hours minimum
- Document temperature readings
- Fuel management - never let tank go below 25%

Produce Loads:
- Do not mix incompatible commodities
- Follow pulping procedures when required
- Report temperature deviations immediately

================================================================================
SECTION 4: ADMINISTRATIVE REQUIREMENTS
================================================================================

4.1 DOCUMENTATION SUBMISSION

All documents must be submitted within 24 hours of delivery:

Required Documents:
- Signed BOL (shipper and receiver signatures)
- POD with delivery date/time
- Lumper receipts (if applicable)
- Fuel receipts
- Scale tickets (if overweight)
- Detention forms (if applicable)

Submission Methods:
- Mobile app (preferred)
- Email: docs@yourcompany.com
- Fax: [FAX NUMBER]
- Dropbox at terminal

4.2 EXPENSE REIMBURSEMENT

Reimbursable Expenses:
- Tolls (with receipt)
- Scales (with receipt)
- Lumper fees (with receipt, pre-approved over $150)
- Parking (truck parking only)

Non-Reimbursable:
- Meals (per diem provided separately)
- Personal items
- Unauthorized repairs
- Fines and violations

4.3 COMMUNICATION REQUIREMENTS

Check-In Schedule:
- Morning check-in: Start of duty day
- Pre-pickup: 1 hour before scheduled
- Post-pickup: Within 30 minutes of departure
- Pre-delivery: 1 hour before scheduled
- Post-delivery: Within 30 minutes
- End of day: Before going off-duty

Emergency Contact:
Dispatch available 24/7 at [PHONE NUMBER]

4.4 HOME TIME POLICY

Company Drivers:
- 1 day off per 7 days out (minimum)
- 2 days off per 10 days out (standard)
- Advance notice required for scheduling

Owner Operators:
- Schedule home time with dispatch
- 2 weeks notice preferred

================================================================================
SECTION 5: COMPENSATION
================================================================================

5.1 PAY STRUCTURE

Company Drivers:
- CPM (Cents Per Mile) based on experience
- Performance bonuses (see 1.4)
- Detention pay after 2 hours (authorized)
- Layover pay ($150/day after 24 hours)
- Safety bonuses

Owner Operators:
- Percentage of load revenue (typically 85%)
- Fuel surcharge passed through 100%
- Company-paid liability insurance
- Optional occupational accident insurance

5.2 PAY SCHEDULE

Pay Periods:
- Week 1: Sunday - Saturday
- Week 2: Sunday - Saturday

Pay Date:
Direct deposit every Friday for prior 2-week period

Advances:
Available up to $500 per week against current pay period

5.3 DEDUCTIONS

Company Drivers:
- Federal/state taxes
- Social Security/Medicare
- Health insurance (if elected)
- 401(k) (if elected)

Owner Operators:
- Trailer lease (if applicable)
- Insurance premiums
- Occupational accident
- Accounting fees (if company-managed)

================================================================================
SECTION 6: TERMINATION POLICIES
================================================================================

6.1 GROUNDS FOR IMMEDIATE TERMINATION

- DUI or positive drug/alcohol test
- Theft of cargo or company property
- Abandonment of equipment
- Violent behavior or threats
- Intentional falsification of logs
- Operating without valid CDL
- Refusal of lawful dispatch (insubordination)

6.2 PROGRESSIVE DISCIPLINE

For policy violations not warranting immediate termination:

1. Verbal warning (documented)
2. Written warning
3. Final warning
4. Termination

6.3 EQUIPMENT RETURN

Upon termination, driver must return:
- Truck and trailer (if company equipment)
- Keys and key cards
- Fuel cards
- ELD/tablet
- Company documents/manuals
- Uniforms (if applicable)

================================================================================

ACKNOWLEDGMENT

I, _________________________________, have received and read the Driver 
Policy and Procedures Manual. I understand that compliance with these 
policies is a condition of my employment/contract with the company.

I understand that failure to comply with these policies may result in 
disciplinary action up to and including termination.

I acknowledge that these policies may be updated from time to time, and it 
is my responsibility to stay informed of current policies.

Driver Signature: _________________________ Date: ___________

Driver Printed Name: _________________________

Driver ID: _________________________

================================================================================

Document Control:
Version: 2024.1
Effective Date: January 1, 2024
Next Review: July 1, 2024

For questions about this policy, contact:
Safety Department: [PHONE]
Email: safety@yourcompany.com
'''
        
        with open(output_path, 'w') as f:
            f.write(content)
        
        return output_path
    
    def generate_safety_policy(self, output_path: Path):
        """Generate safety policy document."""
        content = '''SAFETY POLICY & PROCEDURES
[Your Company Name]

================================================================================
POLICY STATEMENT
================================================================================

Safety is our highest priority. Every employee, driver, and contractor is 
expected to actively participate in creating and maintaining a safe work 
environment. No job is so important that it cannot be done safely.

This policy applies to all company operations, including but not limited to:
- All driving operations
- Warehouse and terminal activities
- Maintenance and repair work
- Administrative functions

================================================================================
SAFETY RESPONSIBILITIES
================================================================================

Management Responsibilities:
- Provide safe equipment and work environment
- Ensure compliance with all DOT and OSHA regulations
- Provide safety training and resources
- Investigate all accidents and incidents
- Maintain safety records and statistics

Driver Responsibilities:
- Operate equipment safely and legally
- Conduct thorough pre-trip and post-trip inspections
- Report all safety hazards immediately
- Participate in safety training
- Maintain valid CDL and medical certificate

Dispatch/Operations Responsibilities:
- Do not dispatch fatigued or impaired drivers
- Plan realistic schedules that allow for safe driving
- Monitor HOS compliance
- Report safety concerns to management

================================================================================
VEHICLE SAFETY
================================================================================

Daily Vehicle Inspections:
All drivers must complete pre-trip and post-trip inspections using the 
DVIR (Driver Vehicle Inspection Report).

Required Inspection Items:
- Brakes and air system
- Steering mechanism
- Tires, wheels, and rims
- Lighting devices and reflectors
- Mirrors and windshield
- Emergency equipment (fire extinguisher, triangles, fuses)
- Coupling devices
- Cargo securement

Defect Reporting:
Any defect found during inspection must be reported immediately. Unsafe 
equipment must be removed from service until repaired.

================================================================================
DRIVING SAFETY
================================================================================

Speed Management:
- Never exceed posted speed limits
- Reduce speed for weather, traffic, and road conditions
- Observe advisory speeds on ramps and curves
- Use engine brake appropriately

Following Distance:
- Maintain minimum 6-second following distance in ideal conditions
- Increase following distance in adverse conditions
- Allow extra space for larger vehicles

Lane Changes:
- Signal all lane changes at least 100 feet in advance
- Check all mirrors and blind spots
- Maintain speed during lane change
- Never change lanes in intersections

Intersection Safety:
- Approach with caution
- Scan for traffic in all directions
- Check for pedestrians and cyclists
- Never block intersection

================================================================================
WEATHER SAFETY
================================================================================

Severe Weather Protocols:

Thunderstorms:
- Reduce speed
- Increase following distance
- Turn on headlights
- Pull over if visibility severely limited

Winter Weather:
- Carry tire chains November 1 - April 1 (north routes)
- Reduce speed significantly
- Increase following distance to 10+ seconds
- Brake gently to avoid skidding
- If roads are icy, PARK THE TRUCK

High Winds:
- Be cautious with empty trailers and high-profile loads
- Reduce speed
- Grip steering wheel firmly
- Avoid exposed areas when possible

Fog:
- Use low beam headlights (high beams reflect)
- Reduce speed
- Use right edge of road as guide
- Pull over if visibility < 1/4 mile

Extreme Heat:
- Monitor engine temperatures
- Check tire pressure frequently
- Stay hydrated
- Watch for signs of heat exhaustion

================================================================================
ACCIDENT PREVENTION
================================================================================

Preventable Accident Definition:
An accident is preventable if the driver failed to do everything reasonable 
to prevent it.

Common Preventable Accidents:
- Rear-end collisions (following too close)
- Backing accidents (inadequate lookout)
- Fixed object collisions (inadequate clearance)
- Lane change accidents (inadequate observation)
- Intersection accidents (failure to yield)

Defensive Driving:
- Expect the unexpected
- Be prepared for worst-case scenarios
- Identify escape routes
- Scan 12-15 seconds ahead
- Check mirrors every 8-10 seconds

================================================================================
EMERGENCY PROCEDURES
================================================================================

Breakdown on Roadway:
1. Move vehicle to shoulder if possible
2. Activate hazard warning lights
3. Place warning devices (triangles):
   - 10 feet behind
   - 100 feet behind
   - 100 feet ahead (on divided highways, place behind only)
4. Call for assistance
5. Stay with vehicle if safe to do so

Accident Response:
1. STOP immediately
2. Protect scene (hazard lights, triangles)
3. Assist injured if qualified
4. Call 911
5. Notify company
6. Document everything (photos, witnesses)

Fire:
1. Evacuate vehicle immediately
2. Call 911
3. Attempt extinguishment ONLY if:
   - Fire is small and contained
   - Proper extinguisher available
   - Safe exit route available
4. Do not open trailer doors if cargo is burning

Hazmat Incident:
1. Isolate area
2. Call 911
3. Follow emergency response guidebook
4. Evacuate upwind if necessary
5. Notify shipper and company immediately

================================================================================
SECURITY
================================================================================

Cargo Security:
- Lock trailer doors when unattended
- Park in well-lit, secure areas
- Never discuss cargo details in public
- Report suspicious activity
- Verify receiver identity before unloading

Personal Security:
- Be aware of surroundings
- Avoid high-crime areas when possible
- Keep cab doors locked while sleeping
- Do not pick up hitchhikers
- Report threats or harassment immediately

Hijacking Response:
- DO NOT resist
- Comply with demands
- Observe and remember details
- Call 911 as soon as safe
- Report to company immediately

================================================================================
HEALTH AND WELLNESS
================================================================================

Fatigue Management:
- Get 7-8 hours of sleep per day
- Recognize signs of fatigue (yawning, heavy eyes, lane drift)
- Use 30-minute breaks effectively
- Do not operate if too tired to drive safely
- Park and rest if fatigued (no load is worth your life)

Medical Certification:
- Maintain valid DOT medical certificate
- Report medical conditions that may affect driving
- Take prescribed medications as directed
- Notify company of any medical restrictions

Physical Fitness:
- Stretch before and during trips
- Walk when possible during breaks
- Maintain healthy diet
- Stay hydrated

Mental Health:
- Manage stress effectively
- Stay connected with family
- Seek help if experiencing depression or anxiety
- Company EAP available: [PHONE NUMBER]

================================================================================
TRAINING REQUIREMENTS
================================================================================

Initial Training:
- Orientation safety training (8 hours)
- Defensive driving course
- Hazmat training (if applicable)
- ELD training
- Company-specific procedures

Ongoing Training:
- Annual safety refresher (4 hours)
- Quarterly safety meetings
- Monthly safety bulletins
- Specialized training as needed

Training Records:
All training is documented and maintained in driver qualification files.

================================================================================
INCIDENT REPORTING
================================================================================

Reportable Incidents:
- All accidents (regardless of severity)
- Injuries requiring first aid
- Equipment damage
- Cargo damage or loss
- Near misses
- Hazardous conditions

Reporting Timeline:
- Immediate: Accidents with injuries or major damage
- Within 1 hour: All other accidents
- Within 24 hours: Injuries, equipment damage, near misses

Reporting Method:
1. Call safety department immediately
2. Complete written incident report within 24 hours
3. Submit all supporting documentation

================================================================================
SAFETY INCENTIVES
================================================================================

Recognition Programs:
- Million Mile Safe Driver Awards
- Quarterly Safety Bonuses
- Driver of the Month/Year
- Clean Inspection Rewards

Bonus Eligibility:
- No preventable accidents
- No moving violations
- No CSA violations
- 100% inspection compliance
- Positive participation in safety programs

================================================================================
VIOLATIONS AND CONSEQUENCES
================================================================================

Safety Policy Violations:

Minor Violations (1st offense - verbal warning):
- Incomplete log entries
- Missing paperwork
- Minor uniform violations

Major Violations (1st offense - written warning):
- Speeding 10+ mph over limit
- Seatbelt violation
- Cell phone use while driving
- Failure to inspect

Critical Violations (immediate suspension/termination):
- DUI
- Reckless driving
- Leaving scene of accident
- Falsifying logs
- Operating unsafe equipment

================================================================================
CONTACT INFORMATION
================================================================================

Safety Director: [NAME]
Phone: [PHONE]
Email: safety@yourcompany.com
Emergency: [24/7 NUMBER]

FMCSA Safety Violation Hotline: 1-888-DOT-SAFT (368-7238)

================================================================================

This policy is effective immediately and supersedes all previous versions.
Questions should be directed to the Safety Department.

Last Updated: January 1, 2024
Next Review: January 1, 2025
'''
        
        with open(output_path, 'w') as f:
            f.write(content)
        
        return output_path


def generate_all_text_documents(output_dir: Path, loads: List[Load] = None):
    """Generate all text-based documents."""
    
    (output_dir / "emails").mkdir(parents=True, exist_ok=True)
    (output_dir / "guides").mkdir(parents=True, exist_ok=True)
    (output_dir / "policies").mkdir(parents=True, exist_ok=True)
    
    print("\n📧 Generating text documents...")
    
    # Generate emails for loads
    email_count = 0
    if loads:
        for load in loads:
            email_gen = EmailGenerator(load)
            
            # Load offer email
            offer_email = email_gen.generate_load_offer_email()
            offer_path = output_dir / "emails" / f"email_offer_{load.load_id}_{load.broker.dba.lower()}.txt"
            email_gen.save_email(offer_email, offer_path)
            email_count += 1
            
            # Detention email if applicable
            if load.has_detention:
                detention_email = email_gen.generate_detention_email(load)
                if detention_email:
                    det_path = output_dir / "emails" / f"email_detention_{load.load_id}.txt"
                    email_gen.save_email(detention_email, det_path)
                    email_count += 1
            
            if email_count % 20 == 0:
                print(f"  ✓ Generated {email_count} emails...")
    
    print(f"  ✓ Generated {email_count} emails")
    
    # Generate routing guides
    guide_gen = RoutingGuideGenerator()
    
    walmart_guide = guide_gen.generate_walmart_guide(output_dir / "guides" / "Routing_Guide_Walmart.txt")
    print(f"  ✓ Generated Walmart routing guide")
    
    amazon_guide = guide_gen.generate_amazon_guide(output_dir / "guides" / "Routing_Guide_Amazon.txt")
    print(f"  ✓ Generated Amazon routing guide")
    
    # Generate policies
    policy_gen = PolicyGenerator()
    
    driver_policy = policy_gen.generate_driver_policy(output_dir / "policies" / "Policy_Driver_Handbook.txt")
    print(f"  ✓ Generated driver policy")
    
    safety_policy = policy_gen.generate_safety_policy(output_dir / "policies" / "Policy_Safety.txt")
    print(f"  ✓ Generated safety policy")
    
    print(f"\n✅ Text documents complete!")
    
    return email_count


if __name__ == "__main__":
    output_dir = Path(__file__).parent / "documents"
    generate_all_text_documents(output_dir)
