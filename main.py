import math
import io
import base64
import json
import logging
import qrcode
import random
from datetime import datetime
from PIL import Image, ImageDraw, UnidentifiedImageError
from fastapi import FastAPI, File, UploadFile, Form, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from ultralytics import YOLO
from fpdf import FPDF

Image.MAX_IMAGE_PIXELS = None

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="DroneMap AI - Enterprise System", version="9.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

try:
    model = YOLO("best.pt")
except Exception as e:
    logger.warning("best.pt not found, loading yolov8n.pt")
    model = YOLO("yolov8n.pt")

VERIFIED_PARCELS = {}

def pixel_to_latlon(px_x, px_y, img_w, img_h, center_lat, center_lon, gsd):
    """Accurate Web Mercator Coordinate Projection"""
    dx = (px_x - (img_w / 2.0)) * gsd
    dy = ((img_h / 2.0) - px_y) * gsd
    r_earth = 6378137.0
    d_lat = (dy / r_earth) * (180.0 / math.pi)
    d_lon = (dx / (r_earth * math.cos(math.pi * center_lat / 180.0))) * (180.0 / math.pi)
    return [round(center_lon + d_lon, 7), round(center_lat + d_lat, 7)]

@app.get("/records")
async def get_verified_records():
    features = []
    for pid, data in VERIFIED_PARCELS.items():
        features.append({
            "type": "Feature",
            "properties": {
                "parcel_id": pid,
                "owner": data["owner"],
                "property_name": data["property_name"],
                "area_sqm": data["area"],
                "date_verified": data["date_verified"],
                "status": "Verified",
                "enrollment_no": data["enrollment_no"]
            },
            "geometry": data["geometry"]
        })
    return {"type": "FeatureCollection", "features": features}

@app.post("/extract")
async def extract_cadastral(
    file: UploadFile = File(...),
    center_lat: float = Form(...),
    center_lon: float = Form(...),
    gsd: float = Form(...), 
    conf_thresh: float = Form(0.15)
):
    try:
        img = Image.open(io.BytesIO(await file.read())).convert("RGB")
        img_w, img_h = img.size
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid image file.")

    results = model.predict(img, conf=conf_thresh, iou=0.45, augment=True, imgsz=1024, verbose=False)
    
    preview_img = img.copy()
    draw = ImageDraw.Draw(preview_img)
    features = []
    
    for i, box in enumerate(results[0].boxes):
        x1, y1, x2, y2 = box.xyxy[0].tolist()
        conf = float(box.conf[0])
        parcel_id = f"PRCL-WB-{len(VERIFIED_PARCELS) + 1000 + i}"
        
        draw.rectangle([x1, y1, x2, y2], outline="#00ffcc", width=int(max(3, img_w*0.004)))
        
        p1 = pixel_to_latlon(x1, y1, img_w, img_h, center_lat, center_lon, gsd)
        p2 = pixel_to_latlon(x2, y1, img_w, img_h, center_lat, center_lon, gsd)
        p3 = pixel_to_latlon(x2, y2, img_w, img_h, center_lat, center_lon, gsd)
        p4 = pixel_to_latlon(x1, y2, img_w, img_h, center_lat, center_lon, gsd)

        area_sqm = round(((x2 - x1) * gsd) * ((y2 - y1) * gsd), 2)
        
        features.append({
            "type": "Feature",
            "properties": {
                "parcel_id": parcel_id,
                "confidence": f"{conf * 100:.1f}%",
                "area_sqm": area_sqm,
                "status": "Pending Verification"
            },
            "geometry": {"type": "Polygon", "coordinates": [[p1, p2, p3, p4, p1]]}
        })

    preview_img.thumbnail((800, 800)) 
    buffered = io.BytesIO()
    preview_img.save(buffered, format="JPEG", quality=85)
    img_b64 = base64.b64encode(buffered.getvalue()).decode("utf-8")

    return {
        "status": "success",
        "telemetry": {"center_lat": center_lat, "center_lon": center_lon, "gsd": gsd, "image_b64": img_b64},
        "geojson": {"type": "FeatureCollection", "features": features}
    }

@app.post("/verify")
async def verify_parcel(
    parcel_id: str = Form(...), 
    owner_name: str = Form(...), 
    prop_name: str = Form(...), 
    area: str = Form(...),
    geometry: str = Form(...),
    officer_name: str = Form("System Admin")
):
    enrollment_no = f"ENR-WB-{datetime.now().strftime('%Y%m')}-{random.randint(1000,9999)}"
    
    VERIFIED_PARCELS[parcel_id] = {
        "owner": owner_name.upper(),
        "property_name": prop_name.upper(),
        "area": area,
        "date_verified": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "geometry": json.loads(geometry),
        "officer_name": officer_name.upper(),
        "enrollment_no": enrollment_no
    }
    return {"message": "Saved successfully!", "enrollment_no": enrollment_no}

@app.post("/generate_card")
async def generate_pdf(parcel_id: str = Form(...)):
    data = VERIFIED_PARCELS.get(parcel_id)
    if not data:
        raise HTTPException(status_code=404, detail="Parcel not verified.")
        
    qr_data = f"Enrollment: {data['enrollment_no']} | ID: {parcel_id} | Owner: {data['owner']} | Area: {data['area']} sqm"
    qr = qrcode.make(qr_data)
    qr_bytes = io.BytesIO()
    qr.save(qr_bytes, format="PNG")
    qr_bytes.seek(0)

    pdf = FPDF()
    pdf.add_page()
    pdf.set_draw_color(30, 41, 59)
    pdf.set_line_width(1.0)
    pdf.rect(10.0, 10.0, 190.0, 277.0)
    
    pdf.set_font("Arial", 'B', 22)
    pdf.cell(0, 15, "GOVERNMENT OF WEST BENGAL", ln=True, align='C')
    pdf.set_font("Arial", 'B', 12)
    pdf.set_text_color(100, 100, 100)
    pdf.cell(0, 5, "Ministry of Panchayati Raj", ln=True, align='C')
    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Arial", 'B', 14)
    pdf.cell(0, 15, "SVAMITVA Scheme - Provisional Property Title", ln=True, align='C')
    pdf.line(20, 50, 190, 50)
    pdf.ln(15)

    fields = [
        ("Enrollment Number:", data["enrollment_no"]),
        ("Cadastral Parcel ID:", parcel_id),
        ("Property Designation:", data["property_name"]),
        ("Registered Owner:", data["owner"]),
        ("Calculated Area Limit:", f"{data['area']} sq. meters"),
        ("Date of Verification:", data["date_verified"]),
        ("Authorizing Officer:", data["officer_name"])
    ]
    
    for label, value in fields:
        pdf.set_font("Arial", 'B', 11)
        pdf.cell(60, 12, label)
        pdf.set_font("Arial", '', 11)
        pdf.cell(0, 12, value, ln=True)

    pdf.image(qr_bytes, x=145, y=60, w=45)
    
    pdf.ln(20)
    pdf.set_text_color(180, 0, 0)
    pdf.set_font("Arial", 'B', 11)
    pdf.cell(0, 10, "IMPORTANT NOTICE FOR CITIZEN REGISTRATION:", ln=True)
    pdf.set_font("Arial", '', 10)
    pdf.set_text_color(50, 50, 50)
    pdf.multi_cell(0, 7, "This document confirms your provisional digital land boundary registration under the central SVAMITVA Scheme. Your area limit and geospatial coordinates are officially recorded in the registry system. You must visit your local Panchayat or BDO office with this certificate and a valid government ID (Aadhaar/Voter ID) for final physical verification and property tax mutation.")
    
    pdf.ln(30)
    pdf.set_font("Arial", 'I', 10)
    pdf.set_text_color(100, 100, 100)
    pdf.cell(0, 10, f"Digitally Processed by DroneMap AI System. Surveying Officer: {data['officer_name']}", ln=True, align='C')

    return StreamingResponse(
        io.BytesIO(bytes(pdf.output())), 
        media_type="application/pdf", 
        headers={"Content-Disposition": f"attachment; filename={data['enrollment_no']}_Title.pdf"}
    )