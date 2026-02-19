"""
PDF Document Generator for Synthetic Trucking Data

Generates realistic PDFs:
- Rate Confirmations
- Invoices  
- Proof of Delivery (POD)
- Bills of Lading (BOL)
- Lumper Receipts
"""

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    Image, PageBreak, KeepTogether, HRFlowable
)
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
from reportlab.graphics.shapes import Drawing, Line
from datetime import datetime, timedelta
from pathlib import Path
from typing import List
import random

from generate_comprehensive_data import (
    create_synthetic_dataset, Load, Broker, Shipper, 
    EquipmentType, BROKERS, LANES
)


class RateConfirmationPDF:
    """Generate professional rate confirmation PDFs."""
    
    def __init__(self, load: Load):
        self.load = load
        self.styles = getSampleStyleSheet()
        self._setup_custom_styles()
    
    def _setup_custom_styles(self):
        """Setup custom paragraph styles."""
        self.title_style = ParagraphStyle(
            'Title',
            parent=self.styles['Heading1'],
            fontSize=28,
            textColor=colors.HexColor('#1e3a5f'),
            spaceAfter=20,
            alignment=TA_CENTER,
            fontName='Helvetica-Bold'
        )
        
        self.header_style = ParagraphStyle(
            'Header',
            parent=self.styles['Heading2'],
            fontSize=14,
            textColor=colors.HexColor('#2563eb'),
            spaceBefore=15,
            spaceAfter=8,
            fontName='Helvetica-Bold'
        )
        
        self.label_style = ParagraphStyle(
            'Label',
            parent=self.styles['Normal'],
            fontSize=9,
            textColor=colors.HexColor('#64748b'),
            fontName='Helvetica'
        )
        
        self.value_style = ParagraphStyle(
            'Value',
            parent=self.styles['Normal'],
            fontSize=10,
            textColor=colors.HexColor('#1e293b'),
            fontName='Helvetica-Bold'
        )
        
        self.normal_style = ParagraphStyle(
            'Normal',
            parent=self.styles['Normal'],
            fontSize=10,
            textColor=colors.HexColor('#334155'),
            leading=14
        )
        
        self.terms_style = ParagraphStyle(
            'Terms',
            parent=self.styles['Normal'],
            fontSize=9,
            textColor=colors.HexColor('#475569'),
            leading=12
        )
    
    def generate(self, output_path: Path) -> Path:
        """Generate the rate confirmation PDF."""
        doc = SimpleDocTemplate(
            str(output_path),
            pagesize=letter,
            rightMargin=50,
            leftMargin=50,
            topMargin=50,
            bottomMargin=50
        )
        
        story = []
        
        # Header
        story.append(Paragraph("RATE CONFIRMATION", self.title_style))
        story.append(Spacer(1, 10))
        
        # Confirmation number banner
        story.append(Table(
            [[Paragraph(f"<b>Confirmation #:</b> {self.load.rate_confirmation_number}", self.value_style)]],
            colWidths=[500],
            style=TableStyle([
                ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#dbeafe')),
                ('PADDING', (0, 0), (-1, -1), 12),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ])
        ))
        story.append(Spacer(1, 20))
        
        # Broker Info Section
        story.append(Paragraph("BROKER INFORMATION", self.header_style))
        broker_data = [
            ['Broker:', self.load.broker.name, 'MC #:', self.load.broker.mc_number],
            ['Phone:', self.load.broker.phone, 'Email:', f'dispatch@{self.load.broker.email_domain}'],
            ['Address:', f"{self.load.broker.address}", 'Credit:', f"{self.load.broker.credit_score}/100"],
            ['', f"{self.load.broker.city}, {self.load.broker.state} {self.load.broker.zip}", 'Terms:', f"Net {self.load.broker.net_days}"],
        ]
        story.append(Table(
            broker_data,
            colWidths=[70, 180, 70, 180],
            style=TableStyle([
                ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
                ('FONTNAME', (2, 0), (2, -1), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, -1), 9),
                ('TEXTCOLOR', (0, 0), (-1, -1), colors.HexColor('#334155')),
                ('TOPPADDING', (0, 0), (-1, -1), 4),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
            ])
        ))
        story.append(Spacer(1, 15))
        
        # Rate Summary
        story.append(Paragraph("RATE SUMMARY", self.header_style))
        rate_table = [
            [
                Paragraph('<b>Line Haul</b>', self.normal_style),
                Paragraph('<b>Fuel Surcharge</b>', self.normal_style),
                Paragraph('<b>Total Rate</b>', self.normal_style),
                Paragraph('<b>Rate/Mile</b>', self.normal_style),
            ],
            [
                Paragraph(f"${self.load.line_haul:,.2f}", self.value_style),
                Paragraph(f"${self.load.fuel_surcharge:,.2f}", self.value_style),
                Paragraph(f"${self.load.total_rate:,.2f}", ParagraphStyle(
                    'TotalValue', parent=self.value_style, textColor=colors.HexColor('#059669'), fontSize=14
                )),
                Paragraph(f"${self.load.rate_per_mile:.2f}", self.value_style),
            ]
        ]
        story.append(Table(
            rate_table,
            colWidths=[120, 120, 120, 120],
            style=TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1e3a5f')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('BACKGROUND', (0, 1), (-1, 1), colors.HexColor('#f1f5f9')),
                ('TOPPADDING', (0, 0), (-1, -1), 12),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 12),
                ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#e2e8f0')),
            ])
        ))
        story.append(Spacer(1, 15))
        
        # Load Details
        story.append(Paragraph("LOAD DETAILS", self.header_style))
        details_data = [
            ['Load #:', self.load.load_id, 'Pro #:', self.load.pro_number],
            ['BOL #:', self.load.bol_number, 'Equipment:', self.load.equipment_type.value],
            ['Weight:', f"{self.load.weight:,} lbs", 'Pallets:', str(self.load.pallets)],
            ['Dimensions:', self.load.dims, 'Mileage:', f"{self.load.mileage:,} miles"],
            ['Ref #1:', self.load.reference_numbers[0], 'Ref #2:', self.load.reference_numbers[1] if len(self.load.reference_numbers) > 1 else 'N/A'],
        ]
        story.append(Table(
            details_data,
            colWidths=[70, 180, 70, 180],
            style=TableStyle([
                ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
                ('FONTNAME', (2, 0), (2, -1), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, -1), 9),
                ('TOPPADDING', (0, 0), (-1, -1), 5),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
                ('LINEBELOW', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
            ])
        ))
        story.append(Spacer(1, 20))
        
        # Stops
        story.append(Paragraph("PICKUP", self.header_style))
        pickup_data = [
            [Paragraph(f"<b>{self.load.shipper.facility_name}</b>", self.value_style)],
            [self.load.shipper.address],
            [f"{self.load.shipper.city}, {self.load.shipper.state} {self.load.shipper.zip}"],
            [Spacer(1, 8)],
            [f"<b>Scheduled:</b> {self.load.pickup_scheduled.strftime('%A, %B %d, %Y at %I:%M %p')}"],
            [f"<b>Contact:</b> {self.load.shipper.contact_name} - {self.load.shipper.contact_phone}"],
            [f"<b>Receiving Hours:</b> {self.load.shipper.receiving_hours}"],
        ]
        story.append(Table(
            pickup_data,
            colWidths=[500],
            style=TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#f0fdf4')),
                ('PADDING', (0, 0), (-1, -1), 8),
                ('FONTSIZE', (0, 0), (-1, -1), 9),
            ])
        ))
        story.append(Spacer(1, 15))
        
        story.append(Paragraph("DELIVERY", self.header_style))
        delivery_data = [
            [Paragraph(f"<b>{self.load.consignee.facility_name}</b>", self.value_style)],
            [self.load.consignee.address],
            [f"{self.load.consignee.city}, {self.load.consignee.state} {self.load.consignee.zip}"],
            [Spacer(1, 8)],
            [f"<b>Scheduled:</b> {self.load.delivery_scheduled.strftime('%A, %B %d, %Y at %I:%M %p')}"],
            [f"<b>Contact:</b> {self.load.consignee.contact_name} - {self.load.consignee.contact_phone}"],
            [f"<b>Receiving Hours:</b> {self.load.consignee.receiving_hours}"],
        ]
        story.append(Table(
            delivery_data,
            colWidths=[500],
            style=TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#fef2f2')),
                ('PADDING', (0, 0), (-1, -1), 8),
                ('FONTSIZE', (0, 0), (-1, -1), 9),
            ])
        ))
        story.append(Spacer(1, 20))
        
        # Accessorials if any
        if self.load.accessorials:
            story.append(Paragraph("ACCESSORIALS", self.header_style))
            acc_data = [['Type', 'Description', 'Amount']]
            for acc in self.load.accessorials:
                acc_data.append([
                    acc['type'],
                    acc['description'],
                    f"${acc['amount']:,.2f}"
                ])
            story.append(Table(
                acc_data,
                colWidths=[100, 300, 100],
                style=TableStyle([
                    ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#374151')),
                    ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                    ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                    ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                    ('ALIGN', (2, 0), (2, -1), 'RIGHT'),
                    ('TOPPADDING', (0, 0), (-1, -1), 8),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
                    ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e5e7eb')),
                ])
            ))
            story.append(Spacer(1, 20))
        
        # Terms & Conditions
        story.append(Paragraph("TERMS & CONDITIONS", self.header_style))
        terms_text = f"""
        <b>Payment Terms:</b> Net {self.load.broker.net_days} days from delivery. 
        QuickPay available at {int(self.load.broker.quickpay_fee*100)}% fee.<br/><br/>
        
        <b>Detention Policy:</b> {self.load.consignee.detention_policy}. 
        Driver must request detention before leaving facility.<br/><br/>
        
        <b>Layover:</b> $300 per day after 24 hours of delay attributable to shipper/broker.<br/><br/>
        
        <b>TONU:</b> $200 if load cancelled within 24 hours of scheduled pickup.<br/><br/>
        
        <b>Requirements:</b> Driver must check in with broker at pickup and delivery. 
        All paperwork must be submitted within 24 hours of delivery for payment processing.
        """
        story.append(Paragraph(terms_text, self.terms_style))
        
        # Footer
        story.append(Spacer(1, 30))
        story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor('#e2e8f0')))
        story.append(Spacer(1, 10))
        footer_text = f"This rate confirmation was generated on {datetime.now().strftime('%B %d, %Y at %I:%M %p')} | Questions? Contact dispatch@{self.load.broker.email_domain}"
        story.append(Paragraph(
            footer_text,
            ParagraphStyle('Footer', parent=self.styles['Normal'], fontSize=8, textColor=colors.HexColor('#9ca3af'), alignment=TA_CENTER)
        ))
        
        doc.build(story)
        return output_path


class InvoicePDF:
    """Generate professional invoice PDFs."""
    
    def __init__(self, load: Load, invoice_number: str):
        self.load = load
        self.invoice_number = invoice_number
        self.styles = getSampleStyleSheet()
    
    def generate(self, output_path: Path) -> Path:
        """Generate the invoice PDF."""
        doc = SimpleDocTemplate(
            str(output_path),
            pagesize=letter,
            rightMargin=50,
            leftMargin=50,
            topMargin=50,
            bottomMargin=50
        )
        
        story = []
        
        # Header style
        title_style = ParagraphStyle(
            'Title',
            parent=self.styles['Heading1'],
            fontSize=32,
            textColor=colors.HexColor('#059669'),
            fontName='Helvetica-Bold'
        )
        
        header_style = ParagraphStyle(
            'Header',
            parent=self.styles['Heading2'],
            fontSize=12,
            textColor=colors.HexColor('#059669'),
            fontName='Helvetica-Bold'
        )
        
        # Header
        story.append(Paragraph("INVOICE", title_style))
        story.append(Spacer(1, 20))
        
        # Invoice Info Box
        invoice_info = [
            ['Invoice #:', self.invoice_number],
            ['Invoice Date:', datetime.now().strftime('%B %d, %Y')],
            ['Due Date:', (datetime.now() + timedelta(days=self.load.broker.net_days)).strftime('%B %d, %Y')],
            ['Terms:', f'Net {self.load.broker.net_days}'],
        ]
        
        if self.load.broker.quickpay_available:
            invoice_info.append(['QuickPay:', f'Available ({int(self.load.broker.quickpay_fee*100)}% fee)'])
        
        story.append(Table(
            invoice_info,
            colWidths=[100, 200],
            style=TableStyle([
                ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, -1), 10),
                ('TEXTCOLOR', (0, 0), (-1, -1), colors.HexColor('#374151')),
                ('TOPPADDING', (0, 0), (-1, -1), 6),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
            ])
        ))
        story.append(Spacer(1, 25))
        
        # Bill To / Remit To
        bill_to_data = [
            [
                Paragraph('<b>BILL TO:</b>', header_style),
                Paragraph('<b>REMIT TO:</b>', header_style)
            ],
            [
                self.load.broker.name,
                '[Your Company Name]'
            ],
            [
                f"MC: {self.load.broker.mc_number}",
                '[Your MC Number]'
            ],
            [
                f"{self.load.broker.city}, {self.load.broker.state} {self.load.broker.zip}",
                '[Your Address]'
            ],
        ]
        story.append(Table(
            bill_to_data,
            colWidths=[250, 250],
            style=TableStyle([
                ('FONTSIZE', (0, 0), (-1, -1), 10),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('TOPPADDING', (0, 0), (-1, -1), 4),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
            ])
        ))
        story.append(Spacer(1, 25))
        
        # Load Details Header
        story.append(Paragraph("LOAD DETAILS", header_style))
        story.append(Spacer(1, 10))
        
        load_details = [
            ['Load #:', self.load.load_id, 'Pro #:', self.load.pro_number],
            ['BOL #:', self.load.bol_number, 'Rate Conf #:', self.load.rate_confirmation_number],
            ['Origin:', f"{self.load.origin_city}, {self.load.origin_state}", 'Destination:', f"{self.load.destination_city}, {self.load.destination_state}"],
            ['Pickup:', self.load.actual_pickup.strftime('%m/%d/%Y') if self.load.actual_pickup else self.load.pickup_scheduled.strftime('%m/%d/%Y'), 
             'Delivery:', self.load.actual_delivery.strftime('%m/%d/%Y') if self.load.actual_delivery else self.load.delivery_scheduled.strftime('%m/%d/%Y')],
            ['Mileage:', f"{self.load.mileage:,} miles", 'Equipment:', self.load.equipment_type.value],
        ]
        story.append(Table(
            load_details,
            colWidths=[70, 180, 70, 180],
            style=TableStyle([
                ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
                ('FONTNAME', (2, 0), (2, -1), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, -1), 9),
                ('TOPPADDING', (0, 0), (-1, -1), 5),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
                ('LINEBELOW', (0, 0), (-1, -1), 0.5, colors.HexColor('#e5e7eb')),
            ])
        ))
        story.append(Spacer(1, 25))
        
        # Line Items
        story.append(Paragraph("CHARGES", header_style))
        story.append(Spacer(1, 10))
        
        line_items = [
            ['Description', 'Quantity', 'Rate', 'Amount']
        ]
        
        # Line haul
        line_items.append([
            f"Line Haul - {self.load.origin_city} to {self.load.destination_city}",
            f"{self.load.mileage:,} miles",
            f"${self.load.rate_per_mile:.2f}/mile",
            f"${self.load.line_haul:,.2f}"
        ])
        
        # Fuel surcharge
        line_items.append([
            'Fuel Surcharge',
            f"{self.load.mileage:,} miles",
            '$0.48/mile',
            f"${self.load.fuel_surcharge:,.2f}"
        ])
        
        # Accessorials
        for acc in self.load.accessorials:
            line_items.append([
                acc['description'],
                '',
                '',
                f"${acc['amount']:,.2f}"
            ])
        
        # Total
        line_items.append(['', '', '', ''])
        line_items.append([
            Paragraph('<b>TOTAL DUE</b>', ParagraphStyle('Bold', fontName='Helvetica-Bold', fontSize=11)),
            '',
            '',
            Paragraph(f"<b>${self.load.total_rate:,.2f}</b>", ParagraphStyle('Bold', fontName='Helvetica-Bold', fontSize=12, textColor=colors.HexColor('#059669')))
        ])
        
        story.append(Table(
            line_items,
            colWidths=[220, 80, 80, 100],
            style=TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#059669')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 10),
                ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                ('ALIGN', (1, 0), (-1, -1), 'RIGHT'),
                ('TOPPADDING', (0, 0), (-1, -1), 10),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
                ('LINEABOVE', (0, -1), (-1, -1), 2, colors.HexColor('#059669')),
                ('LINEBELOW', (0, 0), (-1, 0), 1, colors.HexColor('#059669')),
            ])
        ))
        
        # Notes
        story.append(Spacer(1, 30))
        story.append(Paragraph("NOTES:", header_style))
        notes = [
            f"• Reference Numbers: {', '.join(self.load.reference_numbers)}",
            f"• Driver: {self.load.driver.first_name} {self.load.driver.last_name} (ID: {self.load.driver.driver_id})",
            "• Please remit payment within terms. Late payments subject to 1.5% monthly service charge.",
        ]
        for note in notes:
            story.append(Paragraph(note, ParagraphStyle('Note', fontSize=9, textColor=colors.HexColor('#6b7280'), leading=14)))
        
        doc.build(story)
        return output_path


def generate_all_documents(num_loads: int = 50, output_dir: Path = None):
    """Generate complete document set for all loads."""
    
    if output_dir is None:
        output_dir = Path(__file__).parent / "documents"
    
    # Create subdirectories
    (output_dir / "rate_cons").mkdir(parents=True, exist_ok=True)
    (output_dir / "invoices").mkdir(parents=True, exist_ok=True)
    (output_dir / "pods").mkdir(parents=True, exist_ok=True)
    (output_dir / "bols").mkdir(parents=True, exist_ok=True)
    (output_dir / "lumpers").mkdir(parents=True, exist_ok=True)
    
    # Generate dataset
    loads = create_synthetic_dataset(num_loads)
    
    print("\n📄 Generating PDF documents...")
    
    rate_con_paths = []
    invoice_paths = []
    
    for i, load in enumerate(loads):
        # Rate Confirmation
        rc_path = output_dir / "rate_cons" / f"RateConf_{load.rate_confirmation_number}_{load.broker.dba}.pdf"
        rc_gen = RateConfirmationPDF(load)
        rc_gen.generate(rc_path)
        rate_con_paths.append(rc_path)
        
        # Invoice
        inv_num = f"INV-{datetime.now().year}-{load.load_id}"
        inv_path = output_dir / "invoices" / f"Invoice_{inv_num}_{load.broker.dba}.pdf"
        inv_gen = InvoicePDF(load, inv_num)
        inv_gen.generate(inv_path)
        invoice_paths.append(inv_path)
        
        if (i + 1) % 10 == 0:
            print(f"  ✓ Generated {i+1}/{num_loads} document pairs")
    
    print(f"\n✅ Generated {len(rate_con_paths)} rate confirmations")
    print(f"✅ Generated {len(invoice_paths)} invoices")
    print(f"\n📁 Documents saved to: {output_dir}")
    
    return loads, rate_con_paths, invoice_paths


if __name__ == "__main__":
    generate_all_documents(50)
