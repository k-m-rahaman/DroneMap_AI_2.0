# 🛰️ DroneMap AI — Cadastral Survey & Title Generation System

[![FastAPI](https://img.shields.io/badge/Backend-FastAPI-009688?style=flat&logo=fastapi)](https://fastapi.tiangolo.com/)
[![YOLOv8](https://img.shields.io/badge/AI%20Model-YOLOv8-FF6F00?style=flat&logo=ultralytics)](https://ultralytics.com)
[![Leaflet](https://img.shields.io/badge/Mapping-Leaflet.js-199900?style=flat&logo=leaflet)](https://leafletjs.com/)
[![License](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

An end-to-end automated cadastral mapping and property registry platform designed in alignment with the **SVAMITVA Scheme** (Ministry of Panchayati Raj, Government of India). 

**DroneMap AI** processes high-resolution drone orthomosaics and live satellite imagery to detect property footprints using computer vision, calculates legally sound geodesic area limits, and issues verifiable, QR-coded provisional property registration certificates (Form 7).

---

## 🌟 Key Features

- **Automated AI Cadastral Extraction:** Employs a fine-tuned **YOLOv8** instance segmentation engine with test-time augmentation (TTA) to detect parcel footprints directly from drone captures.
- **Geodesic Survey-Grade Math:** Integrates **Turf.js** to compute exact geodesic area limits ($m^2$, sq. ft, acres) accounting for the Earth's true ellipsoidal curvature rather than distorted planar approximations.
- **Web Mercator GSD Auto-Resolution:** Automatically synchronizes map zoom, latitude scaling, and drone telemetry to accurately project pixel bounds onto geographic coordinates without perspective drift.
- **Manual GIS Drawing Fallback:** Built-in **Leaflet-Geoman** tools allow surveyors to manually audit, adjust, or sketch parcel boundaries over satellite layers when drone snips are unavailable.
- **Dynamic Map Geocoding:** Search any village, municipality, or GPS coordinate in India to center the cadastral viewport immediately.
- **Official Form 7 PDF Generator:** Generates automated, verifiable provisional property titles featuring secure QR verification codes and unique government enrollment numbers (`ENR-WB-YYYYMM-XXXX`).
- **Role-Based Access Control (RBAC):** Distinct workflows for **Citizen Viewers** (read-only boundary lookups and certificate downloads) and **Government Survey Officers** (AI execution, manual audits, and official ledger sealing).
- **Multilingual Support:** Localized interface support for English, Hindi, and Bengali.

---

## 🏗️ System Architecture

```text
├── Frontend (Browser / WebGIS)
│   ├── Leaflet.js + Esri World Imagery (Satellite Tiles)
│   ├── Turf.js (Geodesic Ellipsoidal Area Calculations)
│   ├── Leaflet-Geoman (Manual Polygon & Boundary Tools)
│   └── Leaflet Control Geocoder (Location Search)
│
└── Backend (FastAPI Microservice)
    ├── main.py (REST API Endpoints)
    ├── YOLOv8 Engine (Instance Segmentation / best.pt)
    ├── Pillow (EXIF Telemetry & Drawing)
    ├── FPDF & QRCode (Provisional Title Certificate Generation)
    └── requirements.txt (Dependencies)
```

---

## 🚀 Quick Start

1. Prerequisites

`Python 3.9 – 3.11`
`Git`
`Modern Web Browser (Chrome, Edge, Firefox)`

2. Clone Repository

git clone [https://github.com/k-m-rahaman/DroneMap_AI_2.0.git](https://github.com/k-m-rahaman/DroneMap_AI_2.0.git)
cd DroneMap_AI

3. Setup Backend Environment

# Create and activate virtual environment
python -m venv venv

# Windows (PowerShell)
.\venv\Scripts\Activate.ps1

# Linux / macOS
source venv/bin/activate

# Install required dependencies
pip install -r requirements.txt

4. Run the AI Microservice

uvicorn main:app --host 127.0.0.1 --port 8000 --reload
`The FastAPI backend will start at http://127.0.0.1:8000. You can inspect interactive API documentation at http://127.0.0.1:8000/docs.`

5. Launch the Frontend

Open index.html directly in your browser or run a simple static server:
# Using VS Code Live Server extension, or Python:
python -m http.server 5500
`Visit http://127.0.0.1:5500 to access the portal.`

---

### 🔒 Authentication (Demo Access)

| Role | Access Level | Permissions | Demo Credentials |
| :--- | :--- | :--- | :--- |
| **Citizen Viewer** | Public Read-Only | Search map, view verified cadastral boundaries, and download Form 7 certificates | Direct Click (No PIN required) |
| **Govt. Surveyor** | Full Administrative / GIS | Run YOLOv8 detection, manual polygon drawing, area calibration, and ledger verification | Officer PIN: `1234` |

---

### 📡 API Reference

| Method | Endpoint | Request Payload / Params | Response Format | Description |
| :--- | :--- | :--- | :--- | :--- |
| `GET` | `/records` | None | `application/json` (GeoJSON FeatureCollection) | Retrieves all government-verified land parcels, owner metadata, and boundary polygons. |
| `POST` | `/extract` | `multipart/form-data`<br>• `file`: Image file<br>• `center_lat`: Float<br>• `center_lon`: Float<br>• `gsd`: Float<br>• `conf_thresh`: Float (default `0.15`) | `application/json`<br>(Telemetry + GeoJSON detection boxes) | Executes YOLOv8 instance segmentation on uploaded drone snip and projects pixel boundaries into real-world geographic coordinates. |
| `POST` | `/verify` | `multipart/form-data`<br>• `parcel_id`: String<br>• `owner_name`: String<br>• `prop_name`: String<br>• `area`: String<br>• `geometry`: JSON String<br>• `officer_name`: String | `application/json`<br>(Status + `enrollment_no`) | Validates and commits an audited parcel boundary to the master registry and assigns an official enrollment number (`ENR-WB-YYYYMM-XXXX`). |
| `POST` | `/generate_card` | `multipart/form-data`<br>• `parcel_id`: String | `application/pdf`<br>(Binary file stream) | Generates and downloads the official, cryptographically verifiable, QR-coded Form 7 provisional title deed PDF. |

---

## 🛠️ Tech Stack
Frontend: HTML5, CSS3, Tailwind CSS, Leaflet.js, Turf.js, Leaflet-Geoman, Leaflet-Geocoder

Backend: Python 3, FastAPI, Uvicorn, Ultralytics YOLOv8, Pillow, FPDF, Python-QRCode

Spatial Processing: Web Mercator EPSG:3857 to WGS84 EPSG:4326 coordinate transforms

---

## 📄 License
Distributed under the MIT License. See LICENSE for more information.


