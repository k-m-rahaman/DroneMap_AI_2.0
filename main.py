import os
import json
import cv2
import numpy as np
import base64
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from ultralytics import YOLO
from fpdf import FPDF
import qrcode
from io import BytesIO
import uuid
from datetime import datetime

app = FastAPI(title="DroneMap AI - SVAMITVA Engine")

# ==========================================
# 1. CORS CONFIGURATION (THE FIX)
# ==========================================
# This allows your GitHub Pages URL to securely access the Render backend
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://k-m-rahaman.github.io",  # Your live GitHub Pages site
        "http://localhost:5500",          # For local VS Code testing
        "http://127.0.0.1:5500"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==========================================
# 2. AI MODEL & DATABASE SETUP
# ==========================================
# Load YOLOv8 model (Ensure 'best.pt' is uploaded in your backend folder)
try:
    model = YOLO('best.pt')
except Exception as e:
    print(f"Warning: best.pt not found. Error: {e}")
    model = None

# In-memory database for records (Use SQLite/PostgreSQL for production)
registry_db = []

# ==========================================
# 3. API ENDPOINTS
# ==========================================

@app.get("/")
def read_root():
    return {"status": "Active", "system": "DroneMap AI Backend Running"}

@app.get("/records")
def get_records():
    """Returns all verified property records to the frontend map."""
    features = []
    for record in registry_db:
        features.append({
            "type": "Feature",
            "properties": {
                "parcel_id": record["parcel_id"],
                "owner": record["owner_name"],
                "property_name": record["prop_name"],
                "area_sqm": record["area"],
                "enrollment_no": record["enrollment_no"],
                "officer": record["officer_name"],
                "timestamp": record["timestamp"]
            },
            "geometry": json.loads(record["geometry"])
        })
    return {"type": "FeatureCollection", "features": features}

@app.post("/extract")
async def extract_boundaries(
    file: UploadFile = File(...),
    center_lat: float = Form(...),
    center_lon: float = Form(...),
    gsd: float = Form(...),
    conf_thresh: float = Form(0.15)
):
    """Runs YOLOv8 AI to detect property limits and returns polygons."""
    if not model:
        raise HTTPException(status_code=500, detail="AI Model not loaded on server. Check best.pt.")

    # Read and decode the image snip
    contents = await file.read()
    nparr = np.frombuffer(contents, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

    # Run AI inference
    results = model.predict(img, conf=conf_thresh)
    result = results[0]

    features = []
    
    if result.masks is not None:
        # Draw AI masks onto the image for the UI preview box
        img_with_masks = result.plot()
        
        for i, mask in enumerate(result.masks.xy):
            # Translate image pixel coordinates into Geographic Coordinates (Lat/Lon)
            geo_coords = []
            for point in mask:
                px, py = point
                # Offset from center based on Ground Sample Distance (GSD)
                # 1 degree of latitude is roughly 111,320 meters
                lat_offset = ((img.shape[0]/2) - py) * gsd / 111320.0
                lon_offset = (px - (img.shape[1]/2)) * gsd / (111320.0 * np.cos(np.radians(center_lat)))
                geo_coords.append([center_lon + lon_offset, center_lat + lat_offset])
            
            if len(geo_coords) > 0:
                geo_coords.append(geo_coords[0]) # Close the polygon loop
            
            # Calculate physical area using pixel area * GSD
            pixel_area = cv2.contourArea(np.array(mask).astype(np.float32))
            area_sqm = pixel_area * (gsd ** 2)

            features.append({
                "type": "Feature",
                "properties": {
                    "parcel_id": f"AI-{uuid.uuid4().hex[:6].upper()}",
                    "area_sqm": round(area_sqm, 2)
                },
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [geo_coords]
                }
            })
    else:
        img_with_masks = img

    # Encode the preview image back to the frontend
    _, buffer = cv2.imencode('.jpg', img_with_masks)
    img_b64 = base64.b64encode(buffer).decode('utf-8')

    return {
        "telemetry": {"image_b64": img_b64},
        "geojson": {"type": "FeatureCollection", "features": features}
    }

@app.post("/verify")
async def verify_parcel(
    parcel_id: str = Form(...),
    owner_name: str = Form(...),
    prop_name: str = Form(...),
    area: float = Form(...),
    geometry: str = Form(...),
    officer_name: str = Form(...)
):
    """Saves an audited boundary to the public registry."""
    # Generate an official enrollment number
    enrollment_no = f"SVAMITVA-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:4].upper()}"
    
    record = {
        "parcel_id": parcel_id,
        "owner_name": owner_name,
        "prop_name": prop_name,
        "area": area,
        "geometry": geometry,
        "officer_name": officer_name,
        "enrollment_no": enrollment_no,
        "timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    }
    
    registry_db.append(record)
    return {"status": "success", "enrollment_no": enrollment_no}

@app.post("/generate_card")
async def generate_property_card(parcel_id: str = Form(...)):
    """Generates the Form 7 Property PDF Title Deed."""
    record = next((r for r in registry_db if r["parcel_id"] == parcel_id), None)
    if not record:
        raise HTTPException(status_code=404, detail="Parcel not found in verified registry.")

    pdf = FPDF()
    pdf.add_page()
    
    # Header
    pdf.set_font("Arial", 'B', 16)
    pdf.cell(190, 10, "SVAMITVA SCHEME - GOVERNMENT OF INDIA", ln=True, align='C')
    pdf.set_font("Arial", 'B', 14)
    pdf.cell(190, 10, "Form 7: Provisional Property Title (Property Card)", ln=True, align='C')
    pdf.ln(10)
    
    # Document Body Details
    pdf.set_font("Arial", size=12)
    pdf.cell(190, 8, f"Enrollment Number: {record['enrollment_no']}", ln=True)
    pdf.cell(190, 8, f"Parcel ID: {record['parcel_id']}", ln=True)
    pdf.cell(190, 8, f"Property Name: {record['prop_name']}", ln=True)
    pdf.cell(190, 8, f"Registered Owner: {record['owner_name']}", ln=True)
    pdf.cell(190, 8, f"Calculated Area: {record['area']} Sq. Meters", ln=True)
    pdf.cell(190, 8, f"Surveying Officer: {record['officer_name']}", ln=True)
    pdf.cell(190, 8, f"Date Verified: {record['timestamp']}", ln=True)
    pdf.ln(10)
    
    # Generate Security QR Code
    qr_data = f"ENR:{record['enrollment_no']}|OWNER:{record['owner_name']}|AREA:{record['area']}"
    qr = qrcode.make(qr_data)
    qr_bytes = BytesIO()
    qr.save(qr_bytes, format='PNG')
    
    # Save temp QR to embed in PDF
    with open("temp_qr.png", "wb") as f:
        f.write(qr_bytes.getvalue())
        
    pdf.image("temp_qr.png", x=150, y=45, w=40)
    os.remove("temp_qr.png") 
    
    # Legal Footer
    pdf.ln(25)
    pdf.set_font("Arial", 'I', 10)
    pdf.multi_cell(0, 5, "This is a provisional property card generated by DroneMap AI edge surveying. "
                         "Please present this digitally signed document at your local Panchayat office "
                         "for physical stamping and official registry mutation.")

    # Output directly to browser
    pdf_bytes = bytes(pdf.output(dest='S'))
    
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f"inline; filename=Form7_{record['enrollment_no']}.pdf"}
    )