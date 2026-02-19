"""Generate sample trucking documents for testing."""
from pathlib import Path
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib import colors
from datetime import datetime, timedelta
import random


SAMPLE_BROKERS = [
    ("Total Quality Logistics", "TQL", "MC-411443"),
    ("Coyote Logistics", "Coyote", "MC-594188"),
    ("XPO Logistics", "XPO", "MC-285668"),
    ("Landstar", "Landstar", "MC-120641"),
    ("Schneider", "Schneider", "MC-133655"),
    ("JB Hunt", "JBHunt", "MC-135797"),
    ("DAT Freight", "DAT", "MC-419870"),
    ("Truckstop", "Truckstop", "MC-400000"),
]

CITIES = [
    ("Chicago, IL", "Los Angeles, CA", 1745),
    ("Dallas, TX", "Atlanta, GA", 780),
    ("Houston, TX", "Denver, CO", 950),
    ("Phoenix, AZ", "Seattle, WA", 1415),
    ("Miami, FL", "New York, NY", 1280),
    ("Memphis, TN", "Detroit, MI", 530),
    ("Columbus, OH", "Nashville, TN", 380),
    ("Kansas City, MO", "Oklahoma City, OK", 350),
]

EQUIPMENT_TYPES = ["Dry Van", "Reefer", "Flatbed", "Step Deck"]


def generate_rate_confirmation(output_dir: Path, index: int) -> Path:
    """Generate a sample rate confirmation PDF."""
    
    broker = random.choice(SAMPLE_BROKERS)
    lane = random.choice(CITIES)
    equipment = random.choice(EQUIPMENT_TYPES)
    
    load_number = f"TQL{random.randint(100000, 999999)}"
    rate = round(random.uniform(2.0, 3.5) * lane[2], 2)
    pickup_date = datetime.now() + timedelta(days=random.randint(1, 5))
    
    filename = output_dir / f"rate_confirmation_{index:03d}_{broker[1].lower()}.pdf"
    
    doc = SimpleDocTemplate(str(filename), pagesize=letter)
    styles = getSampleStyleSheet()
    story = []
    
    # Title
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=24,
        spaceAfter=30,
        textColor=colors.HexColor('#1e40af')
    )
    story.append(Paragraph(f"RATE CONFIRMATION", title_style))
    story.append(Spacer(1, 0.2*inch))
    
    # Broker info
    story.append(Paragraph(f"<b>Broker:</b> {broker[0]}", styles['Normal']))
    story.append(Paragraph(f"<b>MC Number:</b> {broker[2]}", styles['Normal']))
    story.append(Paragraph(f"<b>Load #:</b> {load_number}", styles['Normal']))
    story.append(Spacer(1, 0.3*inch))
    
    # Load details table
    data = [
        ['Equipment', 'Rate', 'Miles', 'Rate/Mile'],
        [equipment, f'${rate:,.2f}', str(lane[2]), f'${rate/lane[2]:.2f}']
    ]
    
    table = Table(data, colWidths=[1.5*inch, 1.5*inch, 1*inch, 1.5*inch])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1e40af')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 12),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
        ('GRID', (0, 0), (-1, -1), 1, colors.black)
    ]))
    story.append(table)
    story.append(Spacer(1, 0.3*inch))
    
    # Stop details
    story.append(Paragraph("<b>PICKUP</b>", styles['Heading3']))
    story.append(Paragraph(f"Location: {lane[0]}", styles['Normal']))
    story.append(Paragraph(f"Date/Time: {pickup_date.strftime('%A, %B %d, %Y')} 08:00 AM", styles['Normal']))
    story.append(Paragraph(f"Contact: John Smith - (555) {random.randint(100, 999)}-{random.randint(1000, 9999)}", styles['Normal']))
    story.append(Paragraph(f"Reference: PO-{random.randint(10000, 99999)}", styles['Normal']))
    story.append(Spacer(1, 0.2*inch))
    
    story.append(Paragraph("<b>DELIVERY</b>", styles['Heading3']))
    story.append(Paragraph(f"Location: {lane[1]}", styles['Normal']))
    delivery_date = pickup_date + timedelta(days=2)
    story.append(Paragraph(f"Date/Time: {delivery_date.strftime('%A, %B %d, %Y')} 02:00 PM", styles['Normal']))
    story.append(Paragraph(f"Contact: Jane Doe - (555) {random.randint(100, 999)}-{random.randint(1000, 9999)}", styles['Normal']))
    story.append(Spacer(1, 0.3*inch))
    
    # Terms
    story.append(Paragraph("<b>TERMS & CONDITIONS</b>", styles['Heading3']))
    story.append(Paragraph("• Detention: $50/hr after 2 hours free time", styles['Normal']))
    story.append(Paragraph("• Layover: $300 per day", styles['Normal']))
    story.append(Paragraph("• TONU: $200 if cancelled within 24 hours", styles['Normal']))
    story.append(Paragraph("• Payment Terms: Net 30", styles['Normal']))
    
    doc.build(story)
    
    return filename


def generate_invoice(output_dir: Path, index: int) -> Path:
    """Generate a sample invoice PDF."""
    
    broker = random.choice(SAMPLE_BROKERS)
    lane = random.choice(CITIES)
    
    invoice_num = f"INV-{datetime.now().year}-{random.randint(1000, 9999)}"
    load_number = f"LD{random.randint(100000, 999999)}"
    rate = round(random.uniform(2.0, 3.5) * lane[2], 2)
    
    filename = output_dir / f"invoice_{index:03d}_{broker[1].lower()}.pdf"
    
    doc = SimpleDocTemplate(str(filename), pagesize=letter)
    styles = getSampleStyleSheet()
    story = []
    
    # Header
    story.append(Paragraph("INVOICE", ParagraphStyle(
        'InvoiceTitle',
        parent=styles['Heading1'],
        fontSize=28,
        textColor=colors.HexColor('#059669')
    )))
    story.append(Spacer(1, 0.2*inch))
    
    # Invoice details
    story.append(Paragraph(f"<b>Invoice #:</b> {invoice_num}", styles['Normal']))
    story.append(Paragraph(f"<b>Load #:</b> {load_number}", styles['Normal']))
    story.append(Paragraph(f"<b>Date:</b> {datetime.now().strftime('%B %d, %Y')}", styles['Normal']))
    story.append(Paragraph(f"<b>Due Date:</b> {(datetime.now() + timedelta(days=30)).strftime('%B %d, %Y')}", styles['Normal']))
    story.append(Spacer(1, 0.2*inch))
    
    # Bill to
    story.append(Paragraph(f"<b>Bill To:</b> {broker[0]}", styles['Normal']))
    story.append(Paragraph(f"<b>MC #:</b> {broker[2]}", styles['Normal']))
    story.append(Spacer(1, 0.3*inch))
    
    # Line items
    data = [['Description', 'Amount']]
    
    # Add some line items
    line_items = [
        ['Line Haul', f'${rate:,.2f}'],
    ]
    
    if random.random() > 0.5:
        detention = round(random.uniform(100, 300), 2)
        line_items.append(['Detention', f'${detention:,.2f}'])
        rate += detention
    
    if random.random() > 0.7:
        fuel = round(random.uniform(50, 150), 2)
        line_items.append(['Fuel Surcharge', f'${fuel:,.2f}'])
        rate += fuel
    
    data.extend(line_items)
    data.append(['', ''])
    data.append(['<b>TOTAL</b>', f'<b>${rate:,.2f}</b>'])
    
    table = Table(data, colWidths=[4*inch, 2*inch])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#059669')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 12),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('BACKGROUND', (0, 1), (-1, -2), colors.beige),
        ('GRID', (0, 0), (-1, -3), 1, colors.grey),
        ('LINEABOVE', (0, -1), (-1, -1), 2, colors.black),
        ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
    ]))
    story.append(table)
    
    doc.build(story)
    
    return filename


def generate_routing_guide(output_dir: Path) -> Path:
    """Generate a sample customer routing guide."""
    
    filename = output_dir / "routing_guide_walmart.pdf"
    
    doc = SimpleDocTemplate(str(filename), pagesize=letter)
    styles = getSampleStyleSheet()
    story = []
    
    story.append(Paragraph("ROUTING GUIDE", styles['Heading1']))
    story.append(Paragraph("Walmart Distribution Centers", styles['Heading2']))
    story.append(Spacer(1, 0.2*inch))
    
    story.append(Paragraph("<b>Scheduling Requirements:</b>", styles['Heading3']))
    story.append(Paragraph("• All appointments must be scheduled 24 hours in advance", styles['Normal']))
    story.append(Paragraph("• Check in 2 hours prior to appointment", styles['Normal']))
    story.append(Paragraph("• Drivers must have valid ID and clearance", styles['Normal']))
    story.append(Spacer(1, 0.2*inch))
    
    story.append(Paragraph("<b>Detention Policy:</b>", styles['Heading3']))
    story.append(Paragraph("• Free time: 2 hours for unloading", styles['Normal']))
    story.append(Paragraph("• Detention rate: $40/hour after free time", styles['Normal']))
    story.append(Paragraph("• Must request detention within 24 hours", styles['Normal']))
    story.append(Spacer(1, 0.2*inch))
    
    story.append(Paragraph("<b>Contact Information:</b>", styles['Heading3']))
    story.append(Paragraph("Dispatch: (555) 123-4567", styles['Normal']))
    story.append(Paragraph("After Hours: (555) 987-6543", styles['Normal']))
    story.append(Paragraph("Email: dispatch@walmart.example.com", styles['Normal']))
    
    doc.build(story)
    
    return filename


def main():
    """Generate all sample documents."""
    output_dir = Path(__file__).parent / "documents"
    output_dir.mkdir(exist_ok=True)
    
    print("Generating sample trucking documents...")
    
    # Generate rate confirmations
    print("  - Rate Confirmations...")
    for i in range(1, 6):
        generate_rate_confirmation(output_dir, i)
    
    # Generate invoices
    print("  - Invoices...")
    for i in range(1, 6):
        generate_invoice(output_dir, i)
    
    # Generate routing guide
    print("  - Routing Guide...")
    generate_routing_guide(output_dir)
    
    print(f"\n✅ Generated sample documents in: {output_dir}")
    print("\nYou can upload these to test the system!")


if __name__ == "__main__":
    main()
