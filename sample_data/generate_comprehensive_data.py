"""
OpenClaw Synthetic Trucking Dataset Generator

Generates realistic, interconnected trucking documents for testing.
All documents are cross-referenced with realistic data relationships.
"""

import json
import random
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import List, Optional
from enum import Enum
from faker import Faker

# Initialize faker for realistic data
fake = Faker()
Faker.seed(42)
random.seed(42)


class EquipmentType(Enum):
    DRY_VAN = "Dry Van"
    REEFER = "Reefer"
    FLATBED = "Flatbed"
    STEP_DECK = "Step Deck"
    VAN_REEFER = "Van/Reefer"


class LoadStatus(Enum):
    PENDING = "Pending"
    DISPATCHED = "Dispatched"
    IN_TRANSIT = "In Transit"
    DELIVERED = "Delivered"
    INVOICED = "Invoiced"
    PAID = "Paid"


@dataclass
class Broker:
    """Realistic broker entities."""
    name: str
    dba: str
    mc_number: str
    dot_number: str
    address: str
    city: str
    state: str
    zip: str
    phone: str
    email_domain: str
    credit_score: int  # 0-100
    quickpay_available: bool
    quickpay_fee: float  # Percentage
    net_days: int
    
    def __post_init__(self):
        self.email_domain = self.email_domain.lower()


@dataclass
class Shipper:
    """Shipper/consignee entities."""
    name: str
    facility_name: str
    address: str
    city: str
    state: str
    zip: str
    contact_name: str
    contact_phone: str
    contact_email: str
    receiving_hours: str
    detention_policy: str
    lumper_fees: bool
    pallet_exchange: bool
    

@dataclass
class Driver:
    """Driver entities."""
    first_name: str
    last_name: str
    driver_id: str
    phone: str
    truck_number: str
    trailer_number: str
    equipment_type: EquipmentType
    hire_date: datetime


@dataclass
class Load:
    """Core load entity that connects all documents."""
    load_id: str
    broker: Broker
    shipper: Shipper
    consignee: Shipper
    driver: Driver
    
    # Lane info
    origin_city: str
    origin_state: str
    destination_city: str
    destination_state: str
    mileage: int
    
    # Equipment
    equipment_type: EquipmentType
    weight: int
    pallets: int
    dims: str
    
    # Financial
    line_haul: float
    fuel_surcharge: float
    accessorials: List[dict] = field(default_factory=list)
    total_rate: float = 0.0
    rate_per_mile: float = 0.0
    
    # Dates
    pickup_scheduled: datetime = field(default_factory=datetime.now)
    delivery_scheduled: datetime = field(default_factory=datetime.now)
    actual_pickup: Optional[datetime] = None
    actual_delivery: Optional[datetime] = None
    
    # Status
    status: LoadStatus = LoadStatus.PENDING
    
    # References
    rate_confirmation_number: str = ""
    bol_number: str = ""
    pro_number: str = ""
    reference_numbers: List[str] = field(default_factory=list)
    
    # Issues
    has_detention: bool = False
    detention_hours: float = 0.0
    detention_amount: float = 0.0
    has_lumper: bool = False
    lumper_amount: float = 0.0
    has_layover: bool = False
    layover_amount: float = 0.0
    
    def __post_init__(self):
        if self.total_rate == 0.0:
            self.total_rate = self.line_haul + self.fuel_surcharge + \
                             sum(a.get('amount', 0) for a in self.accessorials)
        if self.rate_per_mile == 0.0 and self.mileage > 0:
            self.rate_per_mile = round(self.total_rate / self.mileage, 2)


# Realistic broker database
BROKERS = [
    Broker(
        name="Total Quality Logistics, LLC",
        dba="TQL",
        mc_number="MC-411443",
        dot_number="1365748",
        address="4289 Ivy Pointe Blvd",
        city="Cincinnati",
        state="OH",
        zip="45245",
        phone="(800) 580-3101",
        email_domain="tql.com",
        credit_score=95,
        quickpay_available=True,
        quickpay_fee=0.03,
        net_days=30
    ),
    Broker(
        name="Coyote Logistics, LLC",
        dba="Coyote",
        mc_number="MC-594188",
        dot_number="2183386",
        address="2545 W. Diversey Ave",
        city="Chicago",
        state="IL",
        zip="60647",
        phone="(877) 626-9681",
        email_domain="coyote.com",
        credit_score=92,
        quickpay_available=True,
        quickpay_fee=0.025,
        net_days=30
    ),
    Broker(
        name="XPO Logistics Freight, Inc.",
        dba="XPO",
        mc_number="MC-285668",
        dot_number="82866",
        address=" Five American Lane",
        city="Greenwich",
        state="CT",
        zip="06831",
        phone="(855) 976-4636",
        email_domain="xpo.com",
        credit_score=88,
        quickpay_available=True,
        quickpay_fee=0.035,
        net_days=28
    ),
    Broker(
        name="Schneider National Carriers, Inc.",
        dba="Schneider",
        mc_number="MC-133655",
        dot_number="73686",
        address="3101 Packerland Drive",
        city="Green Bay",
        state="WI",
        zip="54313",
        phone="(800) 558-6767",
        email_domain="schneider.com",
        credit_score=97,
        quickpay_available=True,
        quickpay_fee=0.02,
        net_days=21
    ),
    Broker(
        name="Landstar Inway, Inc.",
        dba="Landstar",
        mc_number="MC-120641",
        dot_number="92116",
        address="13410 Sutton Park Drive South",
        city="Jacksonville",
        state="FL",
        zip="32224",
        phone="(800) 635-6787",
        email_domain="landstar.com",
        credit_score=94,
        quickpay_available=True,
        quickpay_fee=0.025,
        net_days=14
    ),
    Broker(
        name="J.B. Hunt Transport, Inc.",
        dba="JBHunt",
        mc_number="MC-135797",
        dot_number="83099",
        address="615 J.B. Hunt Corporate Drive",
        city="Lowell",
        state="AR",
        zip="72745",
        phone="(800) 452-4868",
        email_domain="jbhunt.com",
        credit_score=96,
        quickpay_available=True,
        quickpay_fee=0.025,
        net_days=30
    ),
    Broker(
        name="Uber Freight",
        dba="Uber Freight",
        mc_number="MC-900636",
        dot_number="2776783",
        address="555 Market St",
        city="San Francisco",
        state="CA",
        zip="94105",
        phone="(866) 678-7456",
        email_domain="uber.com",
        credit_score=89,
        quickpay_available=True,
        quickpay_fee=0.015,
        net_days=7
    ),
    Broker(
        name="Convoy Inc.",
        dba="Convoy",
        mc_number="MC-922267",
        dot_number="2794423",
        address="1700 7th Ave",
        city="Seattle",
        state="WA",
        zip="98101",
        phone="(206) 420-9400",
        email_domain="convoy.com",
        credit_score=87,
        quickpay_available=True,
        quickpay_fee=0.02,
        net_days=7
    ),
]

# Major lanes with realistic mileage
LANES = [
    ("Chicago", "IL", "Los Angeles", "CA", 1745),
    ("Dallas", "TX", "Atlanta", "GA", 780),
    ("Houston", "TX", "Denver", "CO", 950),
    ("Phoenix", "AZ", "Seattle", "WA", 1415),
    ("Miami", "FL", "New York", "NY", 1280),
    ("Memphis", "TN", "Detroit", "MI", 530),
    ("Columbus", "OH", "Nashville", "TN", 380),
    ("Kansas City", "MO", "Oklahoma City", "OK", 350),
    ("Indianapolis", "IN", "Philadelphia", "PA", 640),
    ("Charlotte", "NC", "Boston", "MA", 870),
    ("Denver", "CO", "Albuquerque", "NM", 560),
    ("Portland", "OR", "Salt Lake City", "UT", 765),
    ("Las Vegas", "NV", "Dallas", "TX", 1200),
    ("New Orleans", "LA", "Jacksonville", "FL", 545),
    ("Minneapolis", "MN", "Milwaukee", "WI", 340),
]

# Major distribution centers/shippers
SHIPPERS = [
    {
        "name": "Walmart Distribution Center",
        "facilities": [
            ("6010 N Ridge Trail Rd", "Shelbyville", "IN", "46176"),
            ("5100 Commerce Pkwy", "Johnstown", "NY", "12095"),
            ("8500 W Mackenzie Dr", "Phoenix", "AZ", "85037"),
        ],
        "detention_policy": "2 hours free, $40/hr thereafter",
        "lumper_fees": True,
        "pallet_exchange": False,
    },
    {
        "name": "Amazon Fulfillment Center",
        "facilities": [
            ("1210 W Craig Rd", "North Las Vegas", "NV", "89032"),
            ("3350 E Grand River Ave", "Howell", "MI", "48843"),
            ("9850 Conference Center Dr", "Orlando", "FL", "32819"),
        ],
        "detention_policy": "2 hours free, $50/hr thereafter",
        "lumper_fees": True,
        "pallet_exchange": True,
    },
    {
        "name": "Kroger Distribution Center",
        "facilities": [
            ("6500 E Main St", "Zionsville", "IN", "46077"),
            ("1850 Gateway Blvd", "Conyers", "GA", "30013"),
        ],
        "detention_policy": "3 hours free, $35/hr thereafter",
        "lumper_fees": True,
        "pallet_exchange": False,
    },
    {
        "name": "Home Depot Distribution",
        "facilities": [
            ("1200 Commerce Blvd", "McDonough", "GA", "30253"),
            ("600 Raritan Center Pkwy", "Edison", "NJ", "08837"),
        ],
        "detention_policy": "2 hours free, $45/hr thereafter",
        "lumper_fees": False,
        "pallet_exchange": True,
    },
    {
        "name": "Koch Foods",
        "facilities": [
            ("1301 Allendale Blvd", "Allendale", "SC", "29810"),
            ("2500 W Main St", "Gadsden", "AL", "35901"),
        ],
        "detention_policy": "4 hours free, $30/hr thereafter",
        "lumper_fees": True,
        "pallet_exchange": False,
    },
]


def generate_driver(driver_id: int) -> Driver:
    """Generate a realistic driver profile."""
    equipment = random.choice(list(EquipmentType))
    hire_date = datetime.now() - timedelta(days=random.randint(30, 2000))
    
    return Driver(
        first_name=fake.first_name(),
        last_name=fake.last_name(),
        driver_id=f"DRV{driver_id:04d}",
        phone=fake.phone_number(),
        truck_number=f"{random.randint(100, 999)}",
        trailer_number=f"{random.randint(1000, 9999)}",
        equipment_type=equipment,
        hire_date=hire_date
    )


def generate_shipper(state: str = None) -> Shipper:
    """Generate a shipper, optionally constrained to a state."""
    shipper_data = random.choice(SHIPPERS)
    
    # Filter facilities by state if specified
    facilities = shipper_data["facilities"]
    if state:
        facilities = [f for f in facilities if f[2] == state]
    
    if not facilities:
        facilities = shipper_data["facilities"]
    
    facility = random.choice(facilities)
    
    return Shipper(
        name=shipper_data["name"],
        facility_name=f"{shipper_data['name']} - {facility[1]}",
        address=facility[0],
        city=facility[1],
        state=facility[2],
        zip=facility[3],
        contact_name=fake.name(),
        contact_phone=fake.phone_number(),
        contact_email=f"receiving.{facility[1].lower()}@{shipper_data['name'].lower().replace(' ', '')}.com",
        receiving_hours=random.choice([
            "Mon-Fri 6AM-4PM, Sat 8AM-12PM",
            "Mon-Sat 24 hours",
            "Mon-Fri 8AM-5PM",
            "Mon-Sun 6AM-10PM",
        ]),
        detention_policy=shipper_data["detention_policy"],
        lumper_fees=shipper_data["lumper_fees"],
        pallet_exchange=shipper_data["pallet_exchange"]
    )


def generate_load(
    load_id: int,
    broker: Broker,
    driver: Driver,
    lane: tuple = None,
    equipment_type: EquipmentType = None
) -> Load:
    """Generate a complete load with realistic variations."""
    
    # Select lane
    if lane is None:
        lane = random.choice(LANES)
    
    origin_city, origin_state, dest_city, dest_state, mileage = lane
    
    # Generate shipper/consignee
    shipper = generate_shipper(origin_state)
    consignee = generate_shipper(dest_state)
    
    # Equipment type
    if equipment_type is None:
        equipment_type = random.choice(list(EquipmentType))
    
    # Calculate rate based on market (realistic $/mile)
    base_rate_per_mile = random.uniform(1.80, 3.20)
    if equipment_type == EquipmentType.REEFER:
        base_rate_per_mile += 0.40
    elif equipment_type == EquipmentType.FLATBED:
        base_rate_per_mile += 0.30
    
    # Adjust for lane density
    if mileage < 500:  # Short haul premium
        base_rate_per_mile += 0.50
    
    line_haul = round(mileage * base_rate_per_mile, 2)
    fuel_surcharge = round(mileage * 0.48, 2)  # Approx FSC
    
    # Accessorials
    accessorials = []
    
    # Randomly add issues
    has_detention = random.random() < 0.25  # 25% of loads have detention
    detention_hours = 0.0
    detention_amount = 0.0
    
    has_lumper = random.random() < 0.20 and consignee.lumper_fees
    lumper_amount = 0.0
    
    has_layover = random.random() < 0.05
    layover_amount = 0.0
    
    if has_detention:
        detention_hours = round(random.uniform(2.5, 8.0), 1)
        detention_rate = random.choice([40, 45, 50, 55, 60])
        detention_amount = round(detention_hours * detention_rate, 2)
        accessorials.append({
            "type": "Detention",
            "description": f"{detention_hours} hours detention at delivery",
            "amount": detention_amount
        })
    
    if has_lumper:
        lumper_amount = round(random.choice([75, 100, 125, 150, 175]), 2)
        accessorials.append({
            "type": "Lumper Fee",
            "description": "Third-party unloading service",
            "amount": lumper_amount
        })
    
    if has_layover:
        layover_amount = 300.0
        accessorials.append({
            "type": "Layover",
            "description": "24-hour delay at shipper",
            "amount": layover_amount
        })
    
    # Reference numbers
    ref_numbers = [
        f"PO-{random.randint(100000, 999999)}",
        f"REF-{fake.bothify(text='??###')}",
    ]
    
    # Pickup/delivery dates (within last 30 days)
    pickup = datetime.now() - timedelta(days=random.randint(1, 30))
    transit_days = max(1, mileage // 550)  # Approx 550 miles/day
    delivery = pickup + timedelta(days=transit_days)
    
    # Sometimes actual times differ from scheduled
    actual_pickup = pickup + timedelta(minutes=random.randint(-30, 120))
    actual_delivery = delivery + timedelta(minutes=random.randint(-30, 480) if has_detention else random.randint(-30, 60))
    
    return Load(
        load_id=f"LOAD{load_id:05d}",
        broker=broker,
        shipper=shipper,
        consignee=consignee,
        driver=driver,
        origin_city=origin_city,
        origin_state=origin_state,
        destination_city=dest_city,
        destination_state=dest_state,
        mileage=mileage,
        equipment_type=equipment_type,
        weight=random.choice([25000, 35000, 40000, 42000, 44000, 45000]),
        pallets=random.randint(10, 26),
        dims="48x40x96" if equipment_type != EquipmentType.FLATBED else "48x102",
        line_haul=line_haul,
        fuel_surcharge=fuel_surcharge,
        accessorials=accessorials,
        pickup_scheduled=pickup,
        delivery_scheduled=delivery,
        actual_pickup=actual_pickup,
        actual_delivery=actual_delivery,
        status=LoadStatus.DELIVERED,
        rate_confirmation_number=f"RC{random.randint(1000000, 9999999)}",
        bol_number=f"BOL{random.randint(100000, 999999)}",
        pro_number=f"PRO{random.randint(100000000, 999999999)}",
        reference_numbers=ref_numbers,
        has_detention=has_detention,
        detention_hours=detention_hours,
        detention_amount=detention_amount,
        has_lumper=has_lumper,
        lumper_amount=lumper_amount,
        has_layover=has_layover,
        layover_amount=layover_amount
    )


# Create comprehensive dataset
def create_synthetic_dataset(num_loads: int = 50) -> List[Load]:
    """Create a full synthetic dataset with all supporting entities."""
    
    print(f"🚛 Generating {num_loads} synthetic loads with full documentation...")
    
    # Generate drivers (3-5 drivers)
    num_drivers = random.randint(3, 5)
    drivers = [generate_driver(i+1) for i in range(num_drivers)]
    print(f"  ✓ Generated {num_drivers} drivers")
    
    # Generate loads
    loads = []
    for i in range(num_loads):
        broker = random.choice(BROKERS)
        driver = random.choice(drivers)
        
        load = generate_load(i+1, broker, driver)
        loads.append(load)
    
    print(f"  ✓ Generated {num_loads} loads")
    
    # Summary statistics
    total_revenue = sum(l.total_rate for l in loads)
    avg_rate_per_mile = sum(l.rate_per_mile for l in loads) / len(loads)
    detention_loads = sum(1 for l in loads if l.has_detention)
    lumper_loads = sum(1 for l in loads if l.has_lumper)
    
    print(f"\n📊 Dataset Summary:")
    print(f"  Total Revenue: ${total_revenue:,.2f}")
    print(f"  Average Rate/Mile: ${avg_rate_per_mile:.2f}")
    print(f"  Loads with Detention: {detention_loads} ({detention_loads/len(loads)*100:.0f}%)")
    print(f"  Loads with Lumper: {lumper_loads} ({lumper_loads/len(loads)*100:.0f}%)")
    
    return loads


if __name__ == "__main__":
    dataset = create_synthetic_dataset(50)
    
    # Save metadata
    output_dir = Path(__file__).parent / "documents"
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Save dataset summary as JSON
    summary = {
        "generated_at": datetime.now().isoformat(),
        "total_loads": len(dataset),
        "brokers_used": list(set(l.broker.name for l in dataset)),
        "lanes": list(set(f"{l.origin_city}, {l.origin_state} -> {l.destination_city}, {l.destination_state}" for l in dataset)),
    }
    
    with open(output_dir / "_dataset_summary.json", "w") as f:
        json.dump(summary, f, indent=2, default=str)
    
    print(f"\n✅ Dataset ready for document generation!")
