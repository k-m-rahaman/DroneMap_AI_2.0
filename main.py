"""Authenticated building survey workspace. No legal ownership or title claims."""
from __future__ import annotations
import base64
import csv
import hashlib
import hmac
import io
import json
import logging
import os
import re
import secrets
import threading
import time
import anyio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool
from storage import Store, feature_json, new_id, now

ROOT = Path(__file__).resolve().parent
VERSION = "4.2.0"
COOKIE = "dronemap_session"
SESSION_SECONDS = 43200
MAX_UPLOAD = 64 * 1024 * 1024
logger = logging.getLogger("dronemap")

class SetupBody(BaseModel):
    name: str = Field(min_length=1,max_length=120)
    email: str = Field(min_length=3,max_length=254)
    password: str = Field(min_length=12,max_length=256)

class LoginBody(BaseModel):
    email: str = Field(min_length=3,max_length=254)
    password: str = Field(min_length=1,max_length=256)

class ProjectBody(BaseModel):
    name: str = Field(min_length=1,max_length=180)
    location: str = Field(default="",max_length=300)
    description: str = Field(default="",max_length=5000)

class FeatureProperties(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str = Field(default="Untitled feature",max_length=200)
    owner: str = Field(default="",max_length=200)
    feature_type: Literal["building","parcel","other"] = "building"
    status: Literal["draft","reviewed"] = "draft"
    notes: str = Field(default="",max_length=10000)
    reference_id: str = Field(default="",max_length=200)
    source_id: str | None = Field(default=None,max_length=100)
    confidence: float | None = Field(default=None,ge=0,le=1,allow_inf_nan=False)
    quality_flags: list[Literal["inference_tile_edge","source_image_edge","scan_aoi_edge"]] = Field(default_factory=list,max_length=3)
    touches_tile_edge: bool = False
    touches_source_edge: bool = False
    mask_area_px: float | None = Field(default=None,ge=0,allow_inf_nan=False)
    version: int | None = Field(default=None,ge=1)

class GeometryBody(BaseModel):
    geometry: dict

class FeatureBody(GeometryBody):
    properties: FeatureProperties = Field(default_factory=FeatureProperties)

class ScanBody(BaseModel):
    confidence: float = Field(default=0.5,ge=0.05,le=0.95,allow_inf_nan=False)
    min_area_sqm: float = Field(default=4,ge=0,le=10000,allow_inf_nan=False)
    bounds: list[float] | None = Field(default=None,min_length=4,max_length=4)

class ReconcileBody(BaseModel):
    features: list[dict] = Field(max_length=1000)
    existing: list[dict] = Field(default_factory=list,max_length=1000)

class ImportBody(BaseModel):
    collection: dict
    feature_type: Literal["parcel","building","other"] = "parcel"

class MapScanBody(ScanBody):
    bounds: list[float] = Field(min_length=4,max_length=4)

class DroneConnectionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    transport: Literal["udpin","tcp","serial"] = "udpin"
    host: str = Field(default="127.0.0.1",max_length=64)
    port: int = Field(default=14551,ge=1024,le=65535)
    device: str = Field(default="",max_length=100)
    baud: int = Field(default=57600,ge=9600,le=921600)

class MissionBody(GeometryBody):
    altitude_m: float = Field(default=60,ge=10,le=120,allow_inf_nan=False)
    speed_m_s: float = Field(default=5,ge=0.5,le=15,allow_inf_nan=False)
    front_overlap: float = Field(default=0.8,ge=0.1,le=0.95,allow_inf_nan=False)
    side_overlap: float = Field(default=0.7,ge=0.1,le=0.95,allow_inf_nan=False)
    sensor_width_mm: float = Field(default=13.2,ge=1,le=100,allow_inf_nan=False)
    sensor_height_mm: float = Field(default=8.8,ge=1,le=100,allow_inf_nan=False)
    focal_length_mm: float = Field(default=8.8,ge=1,le=200,allow_inf_nan=False)
    image_width_px: int = Field(default=5472,ge=100,le=20000)
    image_height_px: int = Field(default=3648,ge=100,le=20000)
    angle_deg: float = Field(default=0,ge=0,le=180,allow_inf_nan=False)

def password_hash(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256",password.encode("utf-8"),salt,600000)
    return "pbkdf2_sha256$600000$" + salt.hex() + "$" + digest.hex()

def password_matches(password, encoded):
    try:
        _, rounds, salt, digest = encoded.split("$")
        candidate = hashlib.pbkdf2_hmac("sha256",password.encode("utf-8"),bytes.fromhex(salt),int(rounds))
        return hmac.compare_digest(candidate.hex(),digest)
    except (ValueError,TypeError):
        return False

def user_json(row):
    return {key:row[key] for key in ("id","name","email","role")}

def source_json(source):
    result = {k:v for k,v in source.items() if k not in {"raster_path","preview_path"}}
    result["preview_url"] = f"/api/sources/{source['id']}/preview"
    return result

def create_app(data_dir: Path | str | None = None, imagery_service=None, drone_service=None, public_demo: bool | None = None) -> FastAPI:
    from public_demo import DemoPolicy, initialize_mode, unavailable_drone, NOTICE, SESSION_SECONDS as DEMO_SECONDS, MAX_UPLOAD as DEMO_UPLOAD
    is_demo = public_demo if public_demo is not None else os.environ.get("DRONEMAP_PUBLIC_DEMO", "").casefold() in {"1", "true", "yes"}
    directory = data_dir or os.environ.get("DRONEMAP_DATA_DIR")
    if is_demo and (not directory or Path(directory).resolve() == (ROOT / "data").resolve()):
        raise RuntimeError("Public demo requires an explicit, isolated DRONEMAP_DATA_DIR outside the private data directory.")
    store = Store(Path(directory or ROOT / "data"))
    initialize_mode(store, is_demo)
    demo_policy = DemoPolicy(store) if is_demo else None
    cookie_name = "dronemap_demo_session" if is_demo else COOKIE
    session_seconds = DEMO_SECONDS if is_demo else SESSION_SECONDS
    upload_limit = DEMO_UPLOAD if is_demo else MAX_UPLOAD
    if imagery_service is None:
        try:
            from imagery import SurveyImagery
            imagery_service = SurveyImagery(store.data_dir,ROOT / "best.pt")
        except ImportError:
            imagery_service = None
    # Ignore even injected adapters in public mode: none of their methods may
    # touch the host's private network, ports, or aircraft connections.
    if is_demo:
        drone_service = None
    elif drone_service is None:
        from drone import DroneHub
        drone_service = DroneHub()
    @asynccontextmanager
    async def lifespan(application):
        yield
        if drone_service is not None:
            await run_in_threadpool(drone_service.close)
    app = FastAPI(title="DroneMap AI Survey Workspace",version=VERSION,lifespan=lifespan)
    app.state.store = store
    app.state.imagery = imagery_service
    app.state.drone = drone_service
    app.state.public_demo = is_demo
    app.state.demo_policy = demo_policy
    app.state.inference_lock = threading.Lock()
    login_attempts = {}
    login_lock = threading.Lock()

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        if request.method in {"POST","PUT","PATCH","DELETE"}:
            origin = request.headers.get("origin")
            expected = urlsplit(str(request.base_url))
            if origin:
                try:
                    actual = urlsplit(origin)
                except ValueError:
                    return JSONResponse(status_code=403,content={"detail":"Invalid request origin."})
                if actual.scheme != expected.scheme or actual.netloc.lower() != expected.netloc.lower():
                    return JSONResponse(status_code=403,content={"detail":"Cross-origin changes are not allowed."})
            if request.headers.get("sec-fetch-site") == "cross-site":
                return JSONResponse(status_code=403,content={"detail":"Cross-site changes are not allowed."})
            length = request.headers.get("content-length")
            limit_message = f"Request exceeds the {upload_limit // (1024*1024)} MB upload limit."
            if length and (not length.isdecimal() or int(length) > upload_limit + 1024 * 1024):
                return JSONResponse(status_code=413,content={"detail":limit_message})
            # Enforce the bound while receiving as well, so chunked multipart
            # bodies cannot bypass Content-Length checks and fill spool storage.
            original_receive = request.receive
            received_bytes = 0
            async def bounded_receive():
                nonlocal received_bytes
                message = await original_receive()
                if message["type"] == "http.request":
                    received_bytes += len(message.get("body",b""))
                    if received_bytes > upload_limit + 1024 * 1024:
                        raise HTTPException(413,limit_message)
                return message
            request._receive = bounded_receive
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-Frame-Options"] = "DENY"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def optional_user(request):
        token = request.cookies.get(cookie_name)
        if not token:
            return None
        digest = hashlib.sha256(token.encode()).hexdigest()
        with store.connection() as db:
            user = db.execute("SELECT u.* FROM users u JOIN sessions s ON s.user_id=u.id WHERE s.token_hash=? AND s.expires_at>?",(digest,time.time())).fetchone()
        if is_demo and user:
            from datetime import datetime
            if user["role"] != "demo" or datetime.fromisoformat(user["created_at"]).timestamp() + session_seconds <= time.time():
                return None
        return user

    def require_user(request: Request):
        user = optional_user(request)
        if user is None:
            raise HTTPException(401,"Sign in to access this workspace.")
        return user

    def session(response, user_id, request):
        token = secrets.token_urlsafe(32)
        with store.connection() as db:
            db.execute("DELETE FROM sessions WHERE expires_at<=?",(time.time(),))
            old_token = request.cookies.get(cookie_name)
            if old_token:
                db.execute("DELETE FROM sessions WHERE token_hash=?",(hashlib.sha256(old_token.encode()).hexdigest(),))
            db.execute("INSERT INTO sessions VALUES(?,?,?)",(hashlib.sha256(token.encode()).hexdigest(),user_id,time.time()+session_seconds))
        response.set_cookie(cookie_name,token,max_age=session_seconds,httponly=True,secure=request.url.scheme=="https",samesite="strict",path="/")

    def project_access(db, project_id, user):
        row = db.execute("SELECT * FROM projects WHERE id=? AND user_id=?",(project_id,user["id"])).fetchone()
        if row is None:
            raise HTTPException(404,"Project not found.")
        return row

    def feature_access(db, feature_id, user):
        row = db.execute("SELECT f.* FROM features f JOIN projects p ON p.id=f.project_id WHERE f.id=? AND p.user_id=?",(feature_id,user["id"])).fetchone()
        if row is None:
            raise HTTPException(404,"Feature not found.")
        return row

    def source_access(db, source_id, user):
        row = db.execute("SELECT s.* FROM sources s JOIN projects p ON p.id=s.project_id WHERE s.id=? AND p.user_id=?",(source_id,user["id"])).fetchone()
        if row is None:
            raise HTTPException(404,"Imagery source not found.")
        return row

    def model_readiness():
        if app.state.imagery is None:
            return {"available":False,"state":"unavailable","message":"Imagery dependencies unavailable. Manual geometry editing remains available."}
        try:
            return app.state.imagery.readiness()
        except Exception:
            logger.exception("Model readiness check failed")
            return {"available":False,"state":"error","message":"Model readiness check failed. Review server logs."}

    def measure_geometry(geometry):
        try:
            if is_demo:
                from public_demo import MAX_GEOMETRY_BYTES
                if len(json.dumps(geometry).encode("utf-8")) > MAX_GEOMETRY_BYTES:
                    raise ValueError("Public demo boundaries are limited to 64 KB of geometry. Simplify the boundary or use the local installation.")
            if len(json.dumps(geometry)) > 4*1024*1024:
                raise ValueError("Geometry is too large; simplify the boundary before saving.")
            from geospatial import validate_geometry
            return validate_geometry(geometry)
        except ValueError as exc:
            raise HTTPException(422,str(exc)) from exc

    def validate_feature(body, db, project_id, user):
        measured = measure_geometry(body.geometry)
        props = body.properties.model_dump(exclude={"version"})
        props["name"] = props["name"].strip() or "Untitled feature"
        if props["source_id"]:
            source = source_access(db,props["source_id"],user)
            if source["project_id"] != project_id:
                raise HTTPException(422,"Source must belong to this project.")
        props.update(area_sqm=measured["area_sqm"],perimeter_m=measured["perimeter_m"],bbox=measured["bbox"])
        props.update(updated_by=user["name"],reviewed_by=user["name"] if props["status"]=="reviewed" else None,reviewed_at=now() if props["status"]=="reviewed" else None)
        return measured["geometry"],props

    def history(db, feature, action, user):
        if is_demo:
            demo_policy.history_check(db,user["id"])
        props = feature["properties"]
        db.execute("INSERT INTO feature_history(feature_id,project_id,version,action,snapshot,user_id,timestamp) VALUES(?,?,?,?,?,?,?)",(feature["id"],props["project_id"],props["version"],action,json.dumps(feature),user["id"],now()))

    def persist_source(project_id, source):
        source = dict(source)
        source["id"] = str(source.get("id") or new_id())
        source["project_id"] = project_id
        source["created_at"] = source.get("created_at") or now()
        for field in ("raster_path","preview_path"):
            if source.get(field):
                candidate = Path(source[field]).resolve()
                if not candidate.is_relative_to(store.data_dir / "sources"):
                    raise HTTPException(503,"Imagery service returned an invalid storage path.")
                source[field] = str(candidate)
        with store.connection() as db:
            db.execute("INSERT INTO sources VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET metadata=excluded.metadata WHERE sources.project_id=excluded.project_id",(source["id"],project_id,json.dumps(source,default=str),source["created_at"]))
        return source

    async def imagery_call(method, *args, before=None, finish=None, **kwargs):
        if app.state.imagery is None:
            raise HTTPException(503,"Imagery service unavailable; install the documented dependencies.")
        # Admission happens before consuming a shared worker thread. Quota
        # checks, inference and persistence share this same admission slot.
        if not app.state.inference_lock.acquire(blocking=False):
            raise HTTPException(429,"Another imagery job is running. Please try again shortly.",headers={"Retry-After":"5"})
        started = False
        def execute():
            nonlocal started
            started = True
            previous_files = None
            try:
                previous_files = demo_policy.files() if is_demo else None
                if before:
                    before()
                result = getattr(app.state.imagery,method)(*args,**kwargs)
                if is_demo and previous_files is not None:
                    demo_policy.storage_check()
                return finish(result) if finish else result
            except BaseException:
                if is_demo and previous_files is not None:
                    # A failed upload/download must not leave unowned raster
                    # files behind and consume the entire shared demo quota.
                    demo_policy.remove_files(demo_policy.files() - previous_files)
                raise
            finally:
                app.state.inference_lock.release()
        try:
            # Cancelling an HTTP request must not release admission while its
            # native inference thread continues to run in the background.
            with anyio.CancelScope(shield=True):
                return await run_in_threadpool(execute)
        except HTTPException:
            raise
        except ValueError as exc:
            raise HTTPException(422,str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(503,str(exc)) from exc
        except Exception as exc:
            logger.exception("Imagery operation failed")
            raise HTTPException(503,"Imagery processing failed. No detections were fabricated.") from exc
        finally:
            if not started:
                app.state.inference_lock.release()

    @app.get("/api/status")
    def status(request: Request):
        with store.connection() as db:
            setup_required = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]==0
        user = optional_user(request)
        return {"setup_required":setup_required and not is_demo,"authenticated":user is not None,"user":user_json(user) if user else None,"model":model_readiness(),"version":VERSION,
            "public_demo":is_demo,"demo":{"expires_in_seconds":DEMO_SECONDS,"notice":NOTICE} if is_demo else None}

    @app.post("/api/demo/start")
    async def start_demo(request: Request, response: Response):
        if not is_demo:
            raise HTTPException(404,"Public demo is not enabled.")
        user = optional_user(request)
        if user:
            with store.connection() as db:
                project = db.execute("SELECT * FROM projects WHERE user_id=? ORDER BY created_at,id LIMIT 1",(user["id"],)).fetchone()
            return {"user":user_json(user),"project":{key:project[key] for key in project.keys() if key!="user_id"},"expires_in_seconds":DEMO_SECONDS}
        if not app.state.inference_lock.acquire(blocking=False):
            raise HTTPException(429,"The public demo is processing imagery. Please try opening your workspace shortly.",headers={"Retry-After":"5"})
        try:
            from public_demo import MAX_GUESTS
            with store.connection() as db:
                db.execute("BEGIN IMMEDIATE")
                expired_files = demo_policy.cleanup_expired(db)
                if db.execute("SELECT COUNT(*) FROM users").fetchone()[0] >= MAX_GUESTS:
                    raise HTTPException(429,"All 20 temporary demo workspaces are in use. Please try again later.",headers={"Retry-After":"300"})
                user_id, project_id, stamp = new_id(), new_id(), now()
                db.execute("INSERT INTO users VALUES(?,?,?,?,?,?)",(user_id,"SIH visitor",user_id+"@demo.invalid","unused-demo-credential","demo",stamp))
                db.execute("INSERT INTO projects VALUES(?,?,?,?,?,?,?)",(project_id,user_id,"SIH demo survey","Sample data only","Temporary demonstration workspace; all AI boundaries require review.",stamp,stamp))
                user = db.execute("SELECT * FROM users WHERE id=?",(user_id,)).fetchone()
                project = db.execute("SELECT * FROM projects WHERE id=?",(project_id,)).fetchone()
            demo_policy.remove_files(expired_files)
            session(response,user_id,request)
            return {"user":user_json(user),"project":{key:project[key] for key in project.keys() if key!="user_id"},"expires_in_seconds":DEMO_SECONDS}
        finally:
            app.state.inference_lock.release()

    @app.post("/api/setup",status_code=201)
    def setup(body: SetupBody, request: Request, response: Response):
        if is_demo:
            raise HTTPException(403,"Account setup is disabled in the public demo. Open a temporary SIH demo workspace instead.")
        name,email = body.name.strip(),body.email.strip().casefold()
        if not name or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+",email):
            raise HTTPException(422,"Enter a name and a valid email address.")
        encoded = password_hash(body.password)
        with store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT COUNT(*) FROM users").fetchone()[0]:
                raise HTTPException(409,"Workspace setup has already been completed. Sign in instead.")
            user_id = new_id()
            db.execute("INSERT INTO users VALUES(?,?,?,?,?,?)",(user_id,name,email,encoded,"surveyor",now()))
            user = db.execute("SELECT * FROM users WHERE id=?",(user_id,)).fetchone()
        session(response,user_id,request)
        return {"user":user_json(user)}

    @app.post("/api/login")
    def login(body: LoginBody, request: Request, response: Response):
        if is_demo:
            raise HTTPException(403,"Password sign-in is disabled in the public demo. Open a temporary SIH demo workspace instead.")
        key = request.client.host if request.client else "unknown"
        with login_lock:
            attempts = [stamp for stamp in login_attempts.get(key,[]) if stamp>time.time()-600]
            login_attempts[key] = attempts
            if len(attempts)>=10:
                raise HTTPException(429,"Too many sign-in attempts. Try again in ten minutes.")
        with store.connection() as db:
            user = db.execute("SELECT * FROM users WHERE email=?",(body.email.strip().casefold(),)).fetchone()
        dummy = "pbkdf2_sha256$600000$"+"00"*16+"$"+"00"*32
        correct = password_matches(body.password,user["password_hash"] if user else dummy)
        if not user or not correct:
            with login_lock:
                login_attempts.setdefault(key,[]).append(time.time())
            raise HTTPException(401,"Email or password is incorrect.")
        with login_lock:
            login_attempts.pop(key,None)
        session(response,user["id"],request)
        return {"user":user_json(user)}

    @app.post("/api/logout")
    def logout(request: Request, response: Response):
        user = optional_user(request)
        if user is not None and not is_demo:
            drone_service.disconnect(user["id"])
        token = request.cookies.get(cookie_name)
        if token:
            with store.connection() as db:
                db.execute("DELETE FROM sessions WHERE token_hash=?",(hashlib.sha256(token.encode()).hexdigest(),))
        response.delete_cookie(cookie_name,path="/",httponly=True,samesite="strict")
        return {"ok":True}

    @app.get("/api/drone/status")
    def drone_status(user=Depends(require_user)):
        if is_demo:
            return unavailable_drone()
        return drone_service.status(user["id"])

    @app.get("/api/drone/ports")
    def drone_ports(user=Depends(require_user)):
        if is_demo:
            return {"ports":[]}
        return {"ports":drone_service.ports()}

    @app.post("/api/drone/connect",status_code=202)
    async def drone_connect(body: DroneConnectionBody, user=Depends(require_user)):
        if is_demo:
            raise HTTPException(403,"Physical drone connections are disabled in the public demo. Use the local installation.")
        try:
            return await run_in_threadpool(drone_service.connect,user["id"],body.model_dump())
        except ValueError as exc:
            raise HTTPException(422,str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(503,str(exc)) from exc

    @app.post("/api/drone/disconnect")
    def drone_disconnect(user=Depends(require_user)):
        if is_demo:
            return unavailable_drone()
        return drone_service.disconnect(user["id"])

    @app.get("/api/projects")
    def projects(user=Depends(require_user)):
        with store.connection() as db:
            rows = db.execute("SELECT p.*,(SELECT COUNT(*) FROM features f WHERE f.project_id=p.id) feature_count,(SELECT COUNT(*) FROM sources s WHERE s.project_id=p.id) source_count FROM projects p WHERE user_id=? ORDER BY created_at DESC,id DESC",(user["id"],)).fetchall()
        return {"projects":[{key:row[key] for key in row.keys() if key!="user_id"} for row in rows]}

    @app.post("/api/geometry/measure")
    def measure(body: GeometryBody, user=Depends(require_user)):
        return measure_geometry(body.geometry)

    def stored_features(db, project_id):
        rows = db.execute("SELECT * FROM features WHERE project_id=? ORDER BY created_at,id LIMIT 10001",(project_id,)).fetchall()
        if len(rows)>10000:
            raise HTTPException(422,"Split this project before checking more than 10,000 saved boundaries.")
        return [feature_json(row) for row in rows]

    @app.post("/api/projects/{project_id}/proposals/reconcile")
    async def reconcile(project_id: str, body: ReconcileBody, user=Depends(require_user)):
        from spatial_quality import reconcile_proposals
        with store.connection() as db:
            project_access(db,project_id,user)
            saved = stored_features(db,project_id)
        # A locally edited saved feature supersedes its old geometry during this
        # check, without persisting any draft or modifying the saved record.
        overrides = {str(f.get("id")) for f in body.existing if f.get("id")}
        existing = [f for f in saved if str(f["id"]) not in overrides] + body.existing
        try:
            return await run_in_threadpool(reconcile_proposals,body.features,existing)
        except ValueError as exc:
            raise HTTPException(422,str(exc)) from exc

    @app.get("/api/projects/{project_id}/quality")
    async def quality(project_id: str, user=Depends(require_user)):
        from spatial_quality import project_quality
        with store.connection() as db:
            project_access(db,project_id,user)
            saved = stored_features(db,project_id)
        try:
            return await run_in_threadpool(project_quality,saved)
        except ValueError as exc:
            raise HTTPException(422,str(exc)) from exc

    @app.post("/api/projects/{project_id}/imports/preview")
    async def preview_import(project_id: str, body: ImportBody, user=Depends(require_user)):
        with store.connection() as db:
            project_access(db,project_id,user)
        collection = body.collection
        if collection.get("type") != "FeatureCollection" or not isinstance(collection.get("features"),list):
            raise HTTPException(422,"Import a WGS84 GeoJSON FeatureCollection.")
        if collection.get("crs") is not None:
            raise HTTPException(422,"Reproject to standard WGS84 GeoJSON without a legacy CRS declaration before importing.")
        candidates = collection["features"]
        if not candidates or len(candidates)>1000 or len(json.dumps(collection))>8*1024*1024:
            raise HTTPException(422,"Import 1–1,000 boundaries in a GeoJSON file smaller than 8 MB.")
        result = []
        for index, candidate in enumerate(candidates):
            if not isinstance(candidate,dict) or candidate.get("type")!="Feature":
                raise HTTPException(422,f"Imported boundary {index+1} must be a Feature.")
            props = candidate.get("properties") or {}
            if not isinstance(props,dict):
                raise HTTPException(422,f"Imported boundary {index+1} has invalid properties.")
            measured = measure_geometry(candidate.get("geometry"))
            reference = str(props.get("reference_id") or props.get("parcel_id") or props.get("plot_id") or candidate.get("id") or "")[:200]
            result.append({"type":"Feature","id":"import-"+new_id(),"geometry":measured["geometry"],"properties":{
                "name":str(props.get("name") or reference or f"Imported {body.feature_type} {index+1}")[:200],
                "owner":str(props.get("owner") or "")[:200],"feature_type":body.feature_type,"status":"draft",
                "reference_id":reference,"notes":("Imported reference boundary; confirm source, date, position and any ownership claim before review.\n"+str(props.get("notes") or ""))[:10000],
                "area_sqm":measured["area_sqm"],"perimeter_m":measured["perimeter_m"]}})
        from spatial_quality import project_quality
        try:
            checks = await run_in_threadpool(project_quality,result)
        except ValueError as exc:
            raise HTTPException(422,str(exc)) from exc
        return {"type":"FeatureCollection","features":result,"quality":checks,"saved":False,
            "notice":"Imported boundaries are unsaved drafts. Review them against source records before saving."}

    @app.post("/api/projects",status_code=201)
    def create_project(body: ProjectBody, user=Depends(require_user)):
        name = body.name.strip()
        if not name:
            raise HTTPException(422,"Project name cannot be blank.")
        project = {"id":new_id(),"name":name,"location":body.location.strip(),"description":body.description.strip(),"created_at":now(),"updated_at":now(),"feature_count":0,"source_count":0}
        with store.connection() as db:
            if is_demo:
                from public_demo import MAX_PROJECTS
                db.execute("BEGIN IMMEDIATE")
                if db.execute("SELECT COUNT(*) FROM projects WHERE user_id=?",(user["id"],)).fetchone()[0] >= MAX_PROJECTS:
                    raise HTTPException(429,"The public demo allows three projects per visitor.")
            db.execute("INSERT INTO projects VALUES(?,?,?,?,?,?,?)",(project["id"],user["id"],name,project["location"],project["description"],project["created_at"],project["updated_at"]))
        return project

    @app.put("/api/projects/{project_id}")
    def update_project(project_id: str, body: ProjectBody, user=Depends(require_user)):
        name = body.name.strip()
        if not name:
            raise HTTPException(422,"Project name cannot be blank.")
        with store.connection() as db:
            project_access(db,project_id,user)
            db.execute("UPDATE projects SET name=?,location=?,description=?,updated_at=? WHERE id=?",(name,body.location.strip(),body.description.strip(),now(),project_id))
            row = db.execute("SELECT p.*,(SELECT COUNT(*) FROM features WHERE project_id=p.id) feature_count,(SELECT COUNT(*) FROM sources WHERE project_id=p.id) source_count FROM projects p WHERE id=?",(project_id,)).fetchone()
        return {key:row[key] for key in row.keys() if key!="user_id"}

    @app.post("/api/projects/{project_id}/missions/preview")
    async def preview_mission(project_id: str, body: MissionBody, user=Depends(require_user)):
        with store.connection() as db:
            project_access(db,project_id,user)
        from missions import plan_survey
        try:
            return await run_in_threadpool(plan_survey,body.model_dump())
        except ValueError as exc:
            raise HTTPException(422,str(exc)) from exc

    @app.get("/api/projects/{project_id}/features")
    def features(project_id: str, user=Depends(require_user)):
        with store.connection() as db:
            project_access(db,project_id,user)
            rows = db.execute("SELECT * FROM features WHERE project_id=? ORDER BY created_at,id",(project_id,)).fetchall()
        return {"type":"FeatureCollection","features":[feature_json(row) for row in rows]}

    @app.post("/api/projects/{project_id}/features",status_code=201)
    def add_feature(project_id: str, body: FeatureBody, request: Request, user=Depends(require_user)):
        request_key = request.headers.get("idempotency-key")
        if request_key and not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}",request_key):
            raise HTTPException(422,"Idempotency-Key must contain 1 to 128 letters, numbers, dots, underscores, colons or hyphens.")
        try:
            payload_hash = hashlib.sha256(json.dumps(body.model_dump(),sort_keys=True,separators=(",",":"),allow_nan=False).encode()).hexdigest()
        except ValueError as exc:
            raise HTTPException(422,"Coordinates and measurements must be finite numbers.") from exc
        with store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            project_access(db,project_id,user)
            if request_key:
                previous = db.execute("SELECT * FROM feature_requests WHERE project_id=? AND request_key=?",(project_id,request_key)).fetchone()
                if previous:
                    if previous["payload_hash"] != payload_hash:
                        raise HTTPException(409,"This save key was already used for different content. Reload before trying again.")
                    existing = db.execute("SELECT * FROM features WHERE id=?",(previous["feature_id"],)).fetchone()
                    if existing is None:
                        raise HTTPException(409,"This saved feature was deleted. Reload before creating another.")
                    return feature_json(existing)
            if is_demo:
                from public_demo import MAX_FEATURES
                if db.execute("SELECT COUNT(*) FROM features WHERE project_id=?",(project_id,)).fetchone()[0] >= MAX_FEATURES:
                    raise HTTPException(429,"The public demo allows 200 saved boundaries per project.")
            geometry,props = validate_feature(body,db,project_id,user)
            feature_id,timestamp = new_id(),now()
            db.execute("INSERT INTO features VALUES(?,?,?,?,?,?,?)",(feature_id,project_id,json.dumps(geometry),json.dumps(props),1,timestamp,timestamp))
            db.execute("UPDATE projects SET updated_at=? WHERE id=?",(timestamp,project_id))
            feature = feature_json(db.execute("SELECT * FROM features WHERE id=?",(feature_id,)).fetchone())
            history(db,feature,"created",user)
            if request_key:
                db.execute("INSERT INTO feature_requests VALUES(?,?,?,?)",(project_id,request_key,payload_hash,feature_id))
        return feature

    @app.put("/api/features/{feature_id}")
    def update_feature(feature_id: str, body: FeatureBody, user=Depends(require_user)):
        if body.properties.version is None:
            raise HTTPException(422,"Feature version is required when updating.")
        with store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = feature_access(db,feature_id,user)
            if row["version"] != body.properties.version:
                raise HTTPException(409,"This feature changed in another session. Reload it before saving.")
            geometry,props = validate_feature(body,db,row["project_id"],user)
            timestamp = now()
            db.execute("UPDATE features SET geometry=?,properties=?,version=version+1,updated_at=? WHERE id=? AND version=?",(json.dumps(geometry),json.dumps(props),timestamp,feature_id,body.properties.version))
            db.execute("UPDATE projects SET updated_at=? WHERE id=?",(timestamp,row["project_id"]))
            feature = feature_json(db.execute("SELECT * FROM features WHERE id=?",(feature_id,)).fetchone())
            history(db,feature,"updated",user)
        return feature

    @app.get("/api/features/{feature_id}/history")
    def feature_history(feature_id: str, user=Depends(require_user)):
        with store.connection() as db:
            feature_access(db,feature_id,user)
            rows = db.execute("SELECT h.version,h.action,h.timestamp,h.snapshot,u.name user_name FROM feature_history h LEFT JOIN users u ON u.id=h.user_id WHERE h.feature_id=? ORDER BY h.version DESC,h.id DESC",(feature_id,)).fetchall()
        return {"history":[{"version":row["version"],"action":row["action"],"timestamp":row["timestamp"],"user_name":row["user_name"],"snapshot":json.loads(row["snapshot"])} for row in rows]}

    @app.delete("/api/features/{feature_id}")
    def delete_feature(feature_id: str, version: int | None = None, user=Depends(require_user)):
        with store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = feature_access(db,feature_id,user)
            if version is None:
                raise HTTPException(422,"Feature version is required when deleting.")
            if row["version"] != version:
                raise HTTPException(409,"This feature changed in another session. Reload it before deleting.")
            history(db,feature_json(row),"deleted",user)
            db.execute("DELETE FROM features WHERE id=?",(feature_id,))
            db.execute("UPDATE projects SET updated_at=? WHERE id=?",(now(),row["project_id"]))
        return {"ok":True,"id":feature_id}

    @app.get("/api/projects/{project_id}/sources")
    def sources(project_id: str, user=Depends(require_user)):
        with store.connection() as db:
            project_access(db,project_id,user)
            rows = db.execute("SELECT * FROM sources WHERE project_id=? ORDER BY created_at DESC",(project_id,)).fetchall()
        return {"sources":[source_json(json.loads(row["metadata"])) for row in rows]}

    @app.post("/api/projects/{project_id}/sources",status_code=201)
    async def upload_source(project_id: str, file: UploadFile = File(...), bounds: str | None = Form(default=None), user=Depends(require_user)):
        with store.connection() as db:
            project_access(db,project_id,user)
        contents = await file.read(upload_limit+1)
        await file.close()
        if len(contents)>upload_limit:
            raise HTTPException(413,f"Image exceeds the {upload_limit // (1024*1024)} MB upload limit.")
        if not contents:
            raise HTTPException(422,"Choose a non-empty image file.")
        try:
            parsed_bounds = json.loads(bounds) if bounds else None
        except (ValueError,TypeError) as exc:
            raise HTTPException(422,"Bounds must be a JSON array [west,south,east,north].") from exc
        name = (file.filename or "uploaded-image").replace("\\","/").split("/")[-1][:180]
        def check_upload():
            with store.connection() as db:
                project_access(db,project_id,user)
            if is_demo:
                demo_policy.source_check(project_id)
                demo_policy.upload_pixels(contents)
        return await imagery_call("inspect_upload",contents,name,bounds=parsed_bounds,before=check_upload,
            finish=lambda source: source_json(persist_source(project_id,source)))

    @app.post("/api/projects/{project_id}/scans/map")
    async def scan_map(project_id: str, body: MapScanBody, user=Depends(require_user)):
        with store.connection() as db:
            project_access(db,project_id,user)
        def check_map():
            with store.connection() as db:
                project_access(db,project_id,user)
            if is_demo:
                demo_policy.map_bounds(body.bounds)
                demo_policy.source_check(project_id)
                demo_policy.scan_check(user["id"])
        def finish_map(result):
            return {**result,"source":source_json(persist_source(project_id,result["source"]))}
        return await imagery_call("scan_map",body.bounds,confidence=body.confidence,min_area_sqm=body.min_area_sqm,
            before=check_map,finish=finish_map)

    @app.post("/api/sources/{source_id}/scan")
    async def scan_source(source_id: str, body: ScanBody, user=Depends(require_user)):
        with store.connection() as db:
            row = source_access(db,source_id,user)
        options = {"confidence":body.confidence,"min_area_sqm":body.min_area_sqm}
        if body.bounds is not None:
            from geospatial import validate_bounds
            try:
                options["bounds"] = validate_bounds(body.bounds,mercator=True)
            except ValueError as exc:
                raise HTTPException(422,str(exc)) from exc
        metadata = json.loads(row["metadata"])
        def check_source():
            with store.connection() as db:
                source_access(db,source_id,user)
            if is_demo:
                demo_policy.source_check(row["project_id"],new=False)
                demo_policy.source_windows(metadata,options.get("bounds"))
                demo_policy.scan_check(user["id"])
        def finish_source(result):
            return {**result,"source":source_json(persist_source(row["project_id"],result["source"]))}
        return await imagery_call("scan_source",metadata,**options,before=check_source,finish=finish_source)

    @app.get("/api/sources/{source_id}/preview")
    def source_preview(source_id: str, user=Depends(require_user)):
        with store.connection() as db:
            source = json.loads(source_access(db,source_id,user)["metadata"])
        path = Path(source.get("preview_path","")).resolve()
        if not path.is_relative_to(store.data_dir / "sources") or not path.is_file():
            raise HTTPException(404,"Source preview is unavailable.")
        return FileResponse(path,media_type="image/png")

    @app.get("/api/projects/{project_id}/export.geojson")
    def export_geojson(project_id: str, user=Depends(require_user)):
        data = features(project_id,user)
        return Response(json.dumps(data,ensure_ascii=False,allow_nan=False),media_type="application/geo+json",headers={"Content-Disposition":f'attachment; filename="survey-{project_id}.geojson"'})

    @app.get("/api/projects/{project_id}/export.csv")
    def export_csv(project_id: str, user=Depends(require_user)):
        data = features(project_id,user)
        output = io.StringIO(newline="")
        fields = ["id","name","owner","reference_id","feature_type","status","area_sqm","perimeter_m","source_id","confidence","version","updated_by","reviewed_by","reviewed_at","updated_at","notes","geometry"]
        writer = csv.DictWriter(output,fieldnames=fields)
        writer.writeheader()
        for feature in data["features"]:
            row = {key:feature["properties"].get(key,"") for key in fields}
            row["geometry"] = json.dumps(feature["geometry"])
            row = {key:("'"+value if isinstance(value,str) and value.lstrip().startswith(("=","+","-","@")) else value) for key,value in row.items()}
            writer.writerow(row)
        return Response("\ufeff"+output.getvalue(),media_type="text/csv; charset=utf-8",headers={"Content-Disposition":f'attachment; filename="survey-{project_id}.csv"'})

    def signed_token(report_id):
        signature = hmac.new(bytes.fromhex(store.setting("report_signing_key")),report_id.encode(),hashlib.sha256).digest()
        return report_id+"."+base64.urlsafe_b64encode(signature).rstrip(b"=").decode()

    def get_public_report(token):
        if is_demo:
            raise HTTPException(404,"Public report sharing is disabled for temporary demo data.")
        parts = token.split(".")
        if len(parts)!=2 or not re.fullmatch(r"[0-9a-f]{32}",parts[0]) or not re.fullmatch(r"[A-Za-z0-9_-]{43}",parts[1]) or not hmac.compare_digest(signed_token(parts[0]),token):
            raise HTTPException(404,"Report verification record not found.")
        with store.connection() as db:
            row = db.execute("SELECT * FROM reports WHERE id=?",(parts[0],)).fetchone()
            if row is None:
                raise HTTPException(404,"Report verification record not found.")
            current = db.execute("SELECT version FROM features WHERE id=?",(row["feature_id"],)).fetchone()
        snapshot = json.loads(row["snapshot"])
        snapshot["report"].update(token=token,is_current=current is not None and current["version"]==row["feature_version"],changed_since_report=current is not None and current["version"]!=row["feature_version"],deleted_since_report=current is None)
        return snapshot

    @app.get("/api/public/reports/{token}")
    def public_report(token: str):
        return get_public_report(token)

    @app.get("/api/features/{feature_id}/report.pdf")
    def report_pdf(feature_id: str, request: Request, user=Depends(require_user)):
        from reports import build_report
        with store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = feature_access(db,feature_id,user)
            feature = feature_json(row)
            project = project_access(db,row["project_id"],user)
            existing = db.execute("SELECT * FROM reports WHERE feature_id=? AND feature_version=?",(feature_id,row["version"])).fetchone()
            if existing:
                report_id,snapshot = existing["id"],json.loads(existing["snapshot"])
            else:
                report_id = new_id()
                source = None
                if feature["properties"].get("source_id"):
                    source = json.loads(source_access(db,feature["properties"]["source_id"],user)["metadata"])
                    source = {key:source.get(key) for key in ("id","name","kind","bounds","crs","width","height","resolution_m","attribution","created_at","acquisition_date") if source.get(key) is not None}
                snapshot = {"feature":feature,"project":{key:project[key] for key in ("id","name","location")},"source":source,"report":{"id":report_id,"feature_version":row["version"],"created_at":now(),"prepared_by":user["name"],"document_type":"survey_record","notice":"Survey evidence only. This report is not a title, ownership certificate, or government approval."}}
                if not is_demo:
                    db.execute("INSERT INTO reports VALUES(?,?,?,?,?)",(report_id,feature_id,row["version"],json.dumps(snapshot,ensure_ascii=False),snapshot["report"]["created_at"]))
        verification_url = None
        if not is_demo:
            token = signed_token(report_id)
            base = os.environ.get("DRONEMAP_PUBLIC_BASE_URL",str(request.base_url).rstrip("/")).rstrip("/")
            verification_url = f"{base}/report/{token}"
        try:
            document = build_report(snapshot,verification_url,ROOT)
        except (ValueError,RuntimeError) as exc:
            raise HTTPException(503,str(exc)) from exc
        filename = f"survey-{feature_id}-v{row['version']}.pdf"
        headers = {"Content-Disposition":f'attachment; filename="{filename}"'}
        if verification_url:
            headers["X-Verification-URL"] = verification_url
        return Response(document,media_type="application/pdf",headers=headers)

    @app.get("/")
    @app.get("/report/{token}")
    def frontend(token: str | None = None):
        if is_demo and token is not None:
            raise HTTPException(404,"Public report sharing is disabled for temporary demo data.")
        return FileResponse(ROOT / "index.html",media_type="text/html")

    @app.get("/my_logo.png")
    def logo():
        return FileResponse(ROOT / "my_logo.png",media_type="image/png")

    @app.get("/drone_bg.mp4")
    def background_video():
        return FileResponse(ROOT / "drone_bg.mp4",media_type="video/mp4")

    static_dir = ROOT / "static"
    static_dir.mkdir(exist_ok=True)
    app.mount("/static",StaticFiles(directory=static_dir),name="static")
    app.mount("/fonts",StaticFiles(directory=ROOT / "fonts",check_dir=False),name="fonts")
    return app

app = create_app()
