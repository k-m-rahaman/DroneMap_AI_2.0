"""Unicode survey evidence PDFs with vector boundaries and signed-link QR codes.

The signature authenticates the stored report snapshot; it is not a government
signature, proof of ownership, or a digital signature on the PDF document.
"""
from __future__ import annotations
import io
import math
import os
from pathlib import Path

from fpdf import FPDF
import qrcode


def _fonts(pdf, root):
    configured = os.environ.get("DRONEMAP_FONT_PATH")
    regular = Path(configured) if configured else root / "fonts" / "NotoSans-Regular.ttf"
    if not regular.is_file():
        raise RuntimeError("Report font unavailable. Supply fonts/NotoSans-Regular.ttf or DRONEMAP_FONT_PATH.")
    pdf.add_font("Survey",style="",fname=str(regular))
    bold = root / "fonts" / "NotoSans-Bold.ttf"
    pdf.add_font("Survey",style="B",fname=str(bold if bold.is_file() else regular))
    fallbacks = []
    for family, filename in (("Bengali","NotoSansBengali-Regular.ttf"),("Devanagari","NotoSansDevanagari-Regular.ttf")):
        font = root / "fonts" / filename
        if font.is_file():
            pdf.add_font(family,fname=str(font))
            fallbacks.append(family)
    if fallbacks:
        pdf.set_fallback_fonts(fallbacks,exact_match=False)
    try:
        import uharfbuzz  # noqa: F401 - optional shaping engine
        pdf.set_text_shaping(True)
    except ImportError:
        # Latin names remain usable when an optional shaping package is missing.
        # The caller checks Indic text below to avoid silently broken reports.
        return False
    return True


class SurveyPDF(FPDF):
    def footer(self):
        self.set_y(-14)
        self.set_font("Survey",size=8)
        self.set_text_color(110,120,130)
        self.cell(0,5,f"DroneMap AI | Survey evidence only | Page {self.page_no()}",align="C")


def _text(value):
    # Remove control codes while preserving newlines and supported Unicode.
    return "".join(character for character in str(value if value is not None else "") if character in "\n\t" or ord(character)>=32)


def _sketch(pdf, geometry, top):
    from pyproj import Transformer
    components = [geometry["coordinates"]] if geometry["type"]=="Polygon" else geometry["coordinates"]
    exterior_points = [point for rings in components for point in rings[0][:-1]]
    center_lon = sum(point[0] for point in exterior_points) / len(exterior_points)
    center_lat = sum(point[1] for point in exterior_points) / len(exterior_points)
    project = Transformer.from_crs("EPSG:4326",f"+proj=aeqd +lat_0={center_lat} +lon_0={center_lon} +datum=WGS84 +units=m",always_xy=True)
    projected = [[[project.transform(point[0],point[1]) for point in ring] for ring in rings] for rings in components]
    xs = [point[0] for rings in projected for point in rings[0]]
    ys = [point[1] for rings in projected for point in rings[0]]
    width,height = max(xs)-min(xs),max(ys)-min(ys)
    left,box_width,box_height = 16,178,77
    pdf.set_fill_color(247,250,252)
    pdf.set_draw_color(218,226,232)
    pdf.rect(left,top,box_width,box_height,"DF")
    scale = min((box_width-38)/max(width,0.001),(box_height-21)/max(height,0.001))
    center_x,center_y = left+box_width/2,top+box_height/2
    mx,my = (max(xs)+min(xs))/2,(max(ys)+min(ys))/2
    pdf.set_line_width(0.55)
    pdf.set_draw_color(0,111,123)
    for rings in projected:
        for index,ring in enumerate(rings):
            points = [(center_x+(x-mx)*scale,center_y-(y-my)*scale) for x,y in ring]
            if index:
                pdf.set_fill_color(247,250,252)
            else:
                pdf.set_fill_color(204,235,235)
            pdf.polygon(points,style="DF")
    pdf.set_font("Survey","B",9)
    pdf.set_text_color(54,68,78)
    pdf.text(left+box_width-12,top+10,"N")
    pdf.line(left+box_width-10.7,top+13,left+box_width-10.7,top+22)
    pdf.line(left+box_width-10.7,top+13,left+box_width-12.7,top+16)
    pdf.line(left+box_width-10.7,top+13,left+box_width-8.7,top+16)
    metres = min(width,height)*0.25
    if metres>0:
        magnitude = 10**math.floor(math.log10(metres))
        metres = max(magnitude,math.floor(metres/magnitude)*magnitude)
        length = metres*scale
        pdf.line(left+6,top+box_height-9,left+6+length,top+box_height-9)
        pdf.line(left+6,top+box_height-10.5,left+6,top+box_height-7.5)
        pdf.line(left+6+length,top+box_height-10.5,left+6+length,top+box_height-7.5)
        pdf.set_font("Survey",size=7)
        pdf.text(left+6,top+box_height-12,f"{metres:g} m")
    pdf.set_y(top+box_height+2)
    pdf.set_x(16)
    pdf.set_font("Survey",size=7)
    pdf.set_text_color(106,118,128)
    pdf.multi_cell(178,4,"Boundary sketch in a local metre projection. North is up. WGS84 coordinates; no cadastral control or ownership determination.")


def build_report(snapshot: dict, verification_url: str | None, root: Path) -> bytes:
    feature,project,report = snapshot["feature"],snapshot["project"],snapshot["report"]
    props,source = feature["properties"],snapshot.get("source")
    pdf = SurveyPDF(format="A4")
    pdf.set_margins(16,14,16)
    pdf.set_auto_page_break(True,margin=19)
    shaping = _fonts(pdf,Path(root))
    combined = str(snapshot)
    if not shaping and any("\u0900"<=letter<="\u09ff" for letter in combined):
        raise RuntimeError("Indic report text requires the uharfbuzz package. Install the documented report dependencies.")
    pdf.set_title("DroneMap AI - Survey Record")
    pdf.set_author("GeoVision / DroneMap AI")
    pdf.add_page()
    pdf.set_fill_color(8,84,97)
    pdf.rect(0,0,210,5,"F")
    pdf.set_font("Survey","B",19)
    pdf.set_text_color(18,45,61)
    pdf.cell(0,11,"BUILDING & LAND SURVEY RECORD",new_x="LMARGIN",new_y="NEXT")
    pdf.set_font("Survey",size=9)
    pdf.set_text_color(83,103,115)
    pdf.cell(0,6,"A versioned record of mapped geometry and human review",new_x="LMARGIN",new_y="NEXT")
    pdf.ln(3)

    def detail(label, value):
        pdf.set_x(16)
        y = pdf.get_y()
        pdf.set_font("Survey","B",8)
        pdf.set_text_color(96,113,125)
        pdf.cell(37,5,label.upper())
        pdf.set_font("Survey",size=9)
        pdf.set_text_color(28,48,61)
        pdf.multi_cell(141,5,_text(value) or "Not recorded",new_x="LMARGIN",new_y="NEXT")
        pdf.set_y(max(pdf.get_y(),y+5)+1)

    detail("Feature",props.get("name"))
    detail("Project",project.get("name"))
    detail("Location",project.get("location"))
    detail("Record / version",f"{feature['id']} / {props['version']}")
    if props.get("reference_id"):
        detail("Reference / parcel",props["reference_id"])
    detail("Feature / status",f"{props['feature_type'].title()} / {props['status'].title()}")
    detail("Claimant (entered)",props.get("owner") or "No claimant recorded; ownership not verified")
    detail("Area / perimeter",f"{props['area_sqm']:,.2f} m² / {props['perimeter_m']:,.2f} m")
    detail("Reviewer",props.get("reviewed_by") or "Pending human review")
    detail("Review date",props.get("reviewed_at") or "Not reviewed")
    detail("Report generated",report["created_at"])

    pdf.ln(2)
    if pdf.get_y()>158:
        pdf.add_page()
    pdf.set_font("Survey","B",9)
    pdf.set_text_color(22,69,81)
    pdf.cell(0,6,"MAPPED BOUNDARY",new_x="LMARGIN",new_y="NEXT")
    _sketch(pdf,feature["geometry"],pdf.get_y()+1)
    pdf.ln(3)

    if pdf.get_y()>220:
        pdf.add_page()
    block_top = pdf.get_y()
    pdf.set_x(16)
    pdf.set_font("Survey","B",10)
    pdf.set_text_color(22,69,81)
    pdf.cell(137,6,"Verify this report snapshot" if verification_url else "Temporary SIH demo report",new_x="LMARGIN",new_y="NEXT")
    pdf.set_font("Survey",size=8)
    pdf.set_text_color(70,87,99)
    if verification_url:
        qr = qrcode.make(verification_url)
        qr_bytes = io.BytesIO()
        qr.save(qr_bytes,format="PNG")
        qr_bytes.seek(0)
        pdf.image(qr_bytes,x=158,y=block_top,w=34,h=34)
        pdf.multi_cell(136,4,"The QR link opens a server record signed by this workspace. It identifies this version and shows whether the feature has since changed or been deleted. Anyone with the link can see the shared report data.",new_x="LMARGIN",new_y="NEXT")
        pdf.set_font("Survey",size=7)
        pdf.multi_cell(136,3.6,_text(verification_url),link=verification_url,new_x="LMARGIN",new_y="NEXT")
        pdf.set_y(max(pdf.get_y(),block_top+36)+2)
    else:
        pdf.multi_cell(178,4,"This document was generated in a temporary visitor workspace. Its sample data expires after two hours. Public verification links are disabled for demo records. Use a private local installation for real survey evidence.",new_x="LMARGIN",new_y="NEXT")
        pdf.ln(3)
    pdf.set_fill_color(255,246,224)
    pdf.set_text_color(110,74,22)
    pdf.set_font("Survey","B",8)
    pdf.multi_cell(178,4.5,"SURVEY EVIDENCE ONLY. This report is not a property title, ownership certificate, cadastral approval, or government-issued document. A signed verification link does not digitally sign this PDF.",fill=True,new_x="LMARGIN",new_y="NEXT")

    # Source provenance and full notes are on a separate page when needed;
    # long names/notes are allowed to flow without clipping the geometry.
    if source or props.get("notes"):
        pdf.add_page()
        pdf.set_font("Survey","B",14)
        pdf.set_text_color(18,45,61)
        pdf.cell(0,10,"PROVENANCE & REVIEW NOTES",new_x="LMARGIN",new_y="NEXT")
        pdf.ln(4)
        if source:
            detail("Imagery source",source.get("name"))
            detail("Source type",source.get("kind"))
            detail("Attribution",source.get("attribution") or "Not supplied")
            detail("Acquisition date",source.get("acquisition_date") or "Unknown; not inferred from the upload date")
            detail("Imported",source.get("created_at"))
            detail("Source CRS",source.get("crs"))
            detail("Source bounds",source.get("bounds"))
            detail("Resolution",f"{source['resolution_m']:.3f} m/pixel" if isinstance(source.get("resolution_m"),(int,float)) else "Not recorded")
        else:
            detail("Imagery source","Manual geometry; no imagery source linked")
        if props.get("confidence") is not None:
            detail("Model confidence",f"{props['confidence']:.1%}. Detection confidence is not boundary accuracy.")
        warnings = []
        if "inference_tile_edge" in props.get("quality_flags",[]) or props.get("touches_tile_edge"):
            warnings.append("Mask touches an internal inference tile edge; the boundary may be incomplete.")
        if "source_image_edge" in props.get("quality_flags",[]) or props.get("touches_source_edge"):
            warnings.append("Mask touches the source image edge; the building may extend outside the survey image.")
        if warnings:
            detail("Boundary checks"," ".join(warnings))
        detail("Notes",props.get("notes") or "No notes recorded")
    return bytes(pdf.output())
