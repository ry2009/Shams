"""
Generate supporting documents: PODs, BOLs, Lumper Receipts, Emails, Routing Guides, Policies
"""

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, Image
)
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
from datetime import datetime, timedelta
from pathlib import Path
from typing import List
import random

from generate_comprehensive_data import Load, SHIPPERS


class BOLPDF:
    """Generate Bill of Lading PDFs."""
    
    def __init__(self, load: Load):
        self.load = load
        self.styles = getSampleStyleSheet()
    
    def generate(self, output_path: Path) -> Path:
        doc = SimpleDocTemplate(
            str(output_path),
            pagesize=letter,
            rightMargin=40,
            leftMargin=40,
            topMargin=40,
            bottomMargin=40
        )
        
        story = []
        
        # Title
        title_style = ParagraphStyle(
            'Title',
            parent=self.styles['Heading1'],
            fontSize=24,
            textColor=colors.HexColor('#1e40af'),
            alignment=TA_CENTER,
            fontName='Helvetica-Bold'
        )
        
        story.append(Paragraph("BILL OF LADING", title_style))
        story.append(Spacer(1, 5))
        story.append(Paragraph(f"<b>BOL #:</b> {self.load.bol_number}", self.styles['Normal']))
        story.append(HRFlowable(width="100%", thickness=2, color=colors.HexColor('#1e40af')))
        story.append(Spacer(1, 15))
        
        # Shipper / Consignee
        ship_con_data = [
            [
                Paragraph('<b>SHIPPER</b>', ParagraphStyle('Header', textColor=colors.HexColor('#1e40af'), fontName='Helvetica-Bold')),
                Paragraph('<b>CONSIGNEE</b>', ParagraphStyle('Header', textColor=colors.HexColor('#1e40af'), fontName='Helvetica-Bold'))
            ],
            [self.load.shipper.name, self.load.consignee.name],
            [self.load.shipper.facility_name, self.load.consignee.facility_name],
            [self.load.shipper.address, self.load.consignee.address],
            [
                f"{self.load.shipper.city}, {self.load.shipper.state} {self.load.shipper.zip}",
                f"{self.load.consignee.city}, {self.load.consignee.state} {self.load.consignee.zip}"
            ],
            ['', ''],
            [
                f"Contact: {self.load.shipper.contact_name}",
                f"Contact: {self.load.consignee.contact_name}"
            ],
            [
                f"Phone: {self.load.shipper.contact_phone}",
                f"Phone: {self.load.consignee.contact_phone}"
            ],
        ]
        
        story.append(Table(
            ship_con_data,
            colWidths=[260, 260],
            style=TableStyle([
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#dbeafe')),
                ('FONTSIZE', (0, 0), (-1, -1), 9),
                ('TOPPADDING', (0, 0), (-1, -1), 4),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ])
        ))
        story.append(Spacer(1, 15))
        
        # Load Details
        details_data = [
            ['Pro #:', self.load.pro_number, 'Load #:', self.load.load_id],
            ['Reference:', self.load.reference_numbers[0], 'Equipment:', self.load.equipment_type.value],
            ['Truck #:', self.load.driver.truck_number, 'Trailer #:', self.load.driver.trailer_number],
            ['Driver:', f"{self.load.driver.first_name} {self.load.driver.last_name}", 'Driver ID:', self.load.driver.driver_id],
        ]
        
        story.append(Table(
            details_data,
            colWidths=[80, 180, 80, 180],
            style=TableStyle([
                ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
                ('FONTNAME', (2, 0), (2, -1), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, -1), 9),
                ('TOPPADDING', (0, 0), (-1, -1), 6),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e5e7eb')),
            ])
        ))
        story.append(Spacer(1, 15))
        
        # Freight Description
        story.append(Paragraph("<b>FREIGHT DESCRIPTION</b>", ParagraphStyle('Header', textColor=colors.HexColor('#1e40af'), fontName='Helvetica-Bold')))
        story.append(Spacer(1, 8))
        
        freight_data = [
            ['Packages', 'Weight', 'HM', 'Description', 'Class', 'Cube'],
            [str(self.load.pallets), f"{self.load.weight:,} lbs", '', 'Freight - All Kinds', random.choice(['50', '55', '60', '65']), ''],
        ]
        
        story.append(Table(
            freight_data,
            colWidths=[60, 80, 30, 280, 40, 30],
            style=TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#374151')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('ALIGN', (3, 0), (3, -1), 'LEFT'),
                ('FONTSIZE', (0, 0), (-1, -1), 9),
                ('TOPPADDING', (0, 0), (-1, -1), 8),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e5e7eb')),
            ])
        ))
        story.append(Spacer(1, 15))
        
        # Carrier Info
        story.append(Paragraph("<b>CARRIER INFORMATION</b>", ParagraphStyle('Header', textColor=colors.HexColor('#1e40af'), fontName='Helvetica-Bold')))
        carrier_info = [
            ['Carrier Name:', '[Your Company Name]', 'SCAC:', 'ABCD'],
            ['Truck #:', self.load.driver.truck_number, 'Trailer #:', self.load.driver.trailer_number],
            ['Seal #:', f"SL{random.randint(100000, 999999)}", 'Temperature:', 'N/A'],
        ]
        story.append(Table(
            carrier_info,
            colWidths=[80, 220, 80, 140],
            style=TableStyle([
                ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
                ('FONTNAME', (2, 0), (2, -1), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, -1), 9),
                ('TOPPADDING', (0, 0), (-1, -1), 5),
            ])
        ))
        story.append(Spacer(1, 20))
        
        # Signatures
        story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor('#d1d5db')))
        story.append(Spacer(1, 10))
        
        sig_data = [
            ['SHIPPER SIGNATURE', 'DATE/TIME', 'CARRIER SIGNATURE', 'DATE/TIME'],
            ['\n\n\n', '', '\n\n\n', ''],
            ['_______________________', '', '_______________________', ''],
        ]
        
        story.append(Table(
            sig_data,
            colWidths=[150, 110, 150, 110],
            style=TableStyle([
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, -1), 9),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('VALIGN', (0, 0), (-1, -1), 'BOTTOM'),
            ])
        ))
        
        doc.build(story)
        return output_path


class PODPDF:
    """Generate Proof of Delivery PDFs."""
    
    def __init__(self, load: Load):
        self.load = load
        self.styles = getSampleStyleSheet()
    
    def generate(self, output_path: Path) -> Path:
        doc = SimpleDocTemplate(
            str(output_path),
            pagesize=letter,
            rightMargin=50,
            leftMargin=50,
            topMargin=50,
            bottomMargin=50
        )
        
        story = []
        
        # Header with DELIVERED stamp effect
        title_style = ParagraphStyle(
            'Title',
            parent=self.styles['Heading1'],
            fontSize=28,
            textColor=colors.HexColor('#059669'),
            alignment=TA_CENTER,
            fontName='Helvetica-Bold'
        )
        
        stamp_style = ParagraphStyle(
            'Stamp',
            parent=self.styles['Heading1'],
            fontSize=48,
            textColor=colors.HexColor('#dc2626'),
            alignment=TA_CENTER,
            fontName='Helvetica-Bold'
        )
        
        story.append(Paragraph("PROOF OF DELIVERY", title_style))
        story.append(Spacer(1, 5))
        story.append(Paragraph("「 DELIVERED 」", stamp_style))
        story.append(Spacer(1, 15))
        
        # Basic Info
        info_data = [
            ['Pro #:', self.load.pro_number, 'Load #:', self.load.load_id],
            ['BOL #:', self.load.bol_number, 'Rate Conf #:', self.load.rate_confirmation_number],
            ['Ship Date:', self.load.actual_pickup.strftime('%m/%d/%Y %H:%M') if self.load.actual_pickup else '', 
             'Delivery Date:', self.load.actual_delivery.strftime('%m/%d/%Y %H:%M') if self.load.actual_delivery else ''],
        ]
        
        story.append(Table(
            info_data,
            colWidths=[80, 180, 80, 180],
            style=TableStyle([
                ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
                ('FONTNAME', (2, 0), (2, -1), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, -1), 10),
                ('TOPPADDING', (0, 0), (-1, -1), 6),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
                ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#f0fdf4')),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#86efac')),
            ])
        ))
        story.append(Spacer(1, 20))
        
        # Consignee Info
        story.append(Paragraph("<b>DELIVERED TO:</b>", ParagraphStyle('Header', fontName='Helvetica-Bold', fontSize=12)))
        story.append(Spacer(1, 5))
        
        delivered_to = [
            [self.load.consignee.name],
            [self.load.consignee.facility_name],
            [self.load.consignee.address],
            [f"{self.load.consignee.city}, {self.load.consignee.state} {self.load.consignee.zip}"],
            [''],
            [f"Signed for by: _________________________"],
            [f"Date/Time: {self.load.actual_delivery.strftime('%m/%d/%Y %H:%M') if self.load.actual_delivery else ''}"],
            [''],
            [f"Number of Pieces Received: {self.load.pallets}"],
            [''],
            ["Condition: ☐ Good  ☐ Damaged  ☐ Shortage"],
        ]
        
        story.append(Table(
            delivered_to,
            colWidths=[500],
            style=TableStyle([
                ('FONTSIZE', (0, 0), (-1, -1), 10),
                ('TOPPADDING', (0, 0), (-1, -1), 5),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
            ])
        ))
        
        # Detention info if applicable
        if self.load.has_detention:
            story.append(Spacer(1, 20))
            story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor('#fca5a5')))
            story.append(Spacer(1, 10))
            story.append(Paragraph(
                f"<b>DETENTION RECORDED:</b> {self.load.detention_hours} hours @ ${int(self.load.detention_amount/self.load.detention_hours)}/hr = ${self.load.detention_amount:,.2f}",
                ParagraphStyle('Alert', textColor=colors.HexColor('#dc2626'), fontName='Helvetica-Bold')
            ))
            story.append(Paragraph("Authorized by facility: _________________________", self.styles['Normal']))
        
        # Notes
        story.append(Spacer(1, 30))
        story.append(Paragraph("<b>DELIVERY NOTES:</b>", ParagraphStyle('Header', fontName='Helvetica-Bold')))
        notes = [
            "• Driver arrived at scheduled appointment time",
            "• Consignee signed for freight in good condition",
            "• All paperwork completed and returned to carrier",
        ]
        for note in notes:
            story.append(Paragraph(note, self.styles['Normal']))
        
        doc.build(story)
        return output_path


class LumperReceiptPDF:
    """Generate Lumper Receipt PDFs."""
    
    def __init__(self, load: Load):
        self.load = load
        self.styles = getSampleStyleSheet()
    
    def generate(self, output_path: Path) -> Path:
        if not self.load.has_lumper:
            return None
            
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
        title_style = ParagraphStyle(
            'Title',
            parent=self.styles['Heading1'],
            fontSize=24,
            textColor=colors.HexColor('#7c3aed'),
            alignment=TA_CENTER,
            fontName='Helvetica-Bold'
        )
        
        story.append(Paragraph("LUMPER RECEIPT", title_style))
        story.append(Spacer(1, 5))
        story.append(Paragraph(
            "Third-Party Unloading Service", 
            ParagraphStyle('Subtitle', alignment=TA_CENTER, textColor=colors.HexColor('#8b5cf6'))
        ))
        story.append(Spacer(1, 20))
        
        # Receipt Details
        receipt_num = f"LMP{random.randint(100000, 999999)}"
        
        receipt_data = [
            ['Receipt #:', receipt_num, 'Date:', self.load.actual_delivery.strftime('%m/%d/%Y') if self.load.actual_delivery else ''],
            ['Pro #:', self.load.pro_number, 'Load #:', self.load.load_id],
            ['BOL #:', self.load.bol_number, ''],
            ['', '', '', ''],
            ['Facility:', self.load.consignee.facility_name, '', ''],
            ['Address:', self.load.consignee.address, '', ''],
            ['', f"{self.load.consignee.city}, {self.load.consignee.state} {self.load.consignee.zip}", '', ''],
        ]
        
        story.append(Table(
            receipt_data,
            colWidths=[80, 220, 60, 140],
            style=TableStyle([
                ('FONTNAME', (0, 0), (0, 2), 'Helvetica-Bold'),
                ('FONTNAME', (2, 0), (2, 2), 'Helvetica-Bold'),
                ('FONTNAME', (0, 4), (0, 6), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, -1), 10),
                ('TOPPADDING', (0, 0), (-1, -1), 6),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
            ])
        ))
        story.append(Spacer(1, 20))
        
        # Service Details
        story.append(Paragraph("<b>SERVICE DETAILS</b>", ParagraphStyle('Header', fontName='Helvetica-Bold', fontSize=12)))
        story.append(Spacer(1, 10))
        
        service_data = [
            ['Service Type:', 'Palletized Freight Unloading'],
            ['Number of Pallets:', str(self.load.pallets)],
            ['Weight:', f"{self.load.weight:,} lbs"],
            ['Service Time:', f"{random.randint(30, 90)} minutes"],
            ['', ''],
            ['TOTAL FEE:', f"${self.load.lumper_amount:,.2f}"],
        ]
        
        story.append(Table(
            service_data,
            colWidths=[150, 200],
            style=TableStyle([
                ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
                ('FONTNAME', (0, -1), (0, -1), 'Helvetica-Bold'),
                ('FONTNAME', (1, -1), (1, -1), 'Helvetica-Bold'),
                ('FONTSIZE', (0, -1), (-1, -1), 14),
                ('TEXTCOLOR', (0, -1), (-1, -1), colors.HexColor('#7c3aed')),
                ('TOPPADDING', (0, 0), (-1, -1), 8),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
                ('LINEABOVE', (0, -1), (-1, -1), 2, colors.HexColor('#7c3aed')),
            ])
        ))
        story.append(Spacer(1, 30))
        
        # Signatures
        story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor('#d1d5db')))
        story.append(Spacer(1, 10))
        story.append(Paragraph("This is a paid receipt for lumper services rendered. Keep for your records.", 
                              ParagraphStyle('Note', fontSize=9, textColor=colors.HexColor('#6b7280'))))
        story.append(Spacer(1, 20))
        
        sig_data = [
            ['LUMPER SIGNATURE', 'DRIVER ACKNOWLEDGMENT'],
            ['\n\n\n', '\n\n\n'],
            ['_______________________', '_______________________'],
            [f"{self.load.consignee.facility_name} Lumper Service", f"{self.load.driver.first_name} {self.load.driver.last_name}"],
        ]
        
        story.append(Table(
            sig_data,
            colWidths=[250, 250],
            style=TableStyle([
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, -1), 9),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('VALIGN', (0, 0), (-1, -1), 'BOTTOM'),
            ])
        ))
        
        doc.build(story)
        return output_path


def generate_supporting_documents(loads: List[Load], output_dir: Path):
    """Generate all supporting documents for the loads."""
    
    (output_dir / "bols").mkdir(parents=True, exist_ok=True)
    (output_dir / "pods").mkdir(parents=True, exist_ok=True)
    (output_dir / "lumpers").mkdir(parents=True, exist_ok=True)
    
    print("\n📄 Generating supporting documents...")
    
    bol_count = 0
    pod_count = 0
    lumper_count = 0
    
    for load in loads:
        # BOL
        bol_path = output_dir / "bols" / f"BOL_{load.bol_number}.pdf"
        bol_gen = BOLPDF(load)
        bol_gen.generate(bol_path)
        bol_count += 1
        
        # POD
        pod_path = output_dir / "pods" / f"POD_{load.pro_number}.pdf"
        pod_gen = PODPDF(load)
        pod_gen.generate(pod_path)
        pod_count += 1
        
        # Lumper receipt (only if applicable)
        if load.has_lumper:
            lumper_path = output_dir / "lumpers" / f"Lumper_{load.load_id}.pdf"
            lumper_gen = LumperReceiptPDF(load)
            if lumper_gen.generate(lumper_path):
                lumper_count += 1
        
        if bol_count % 10 == 0:
            print(f"  ✓ Generated {bol_count} BOLs/PODs...")
    
    print(f"\n✅ Generated {bol_count} BOLs")
    print(f"✅ Generated {pod_count} PODs")
    print(f"✅ Generated {lumper_count} Lumper Receipts")
    
    return bol_count, pod_count, lumper_count


if __name__ == "__main__":
    from generate_pdf_documents import create_synthetic_dataset
    
    loads = create_synthetic_dataset(10)
    output_dir = Path(__file__).parent / "documents"
    
    generate_supporting_documents(loads, output_dir)
