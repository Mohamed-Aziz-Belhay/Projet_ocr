#frontend\ocr-angular-arabsoft-ui\app\main_security_patch.py
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
import os
ALLOWED_ORIGINS=["http://localhost:4200","http://127.0.0.1:4200"]
def install_frontend_security(app:FastAPI,static_dir:str)->None:
    app.add_middleware(CORSMiddleware,allow_origins=ALLOWED_ORIGINS,allow_credentials=False,allow_methods=["GET","POST","PUT","DELETE"],allow_headers=["X-API-Key","Content-Type"])
    @app.middleware("http")
    async def security_headers(request:Request,call_next):
        response:Response=await call_next(request)
        response.headers["X-Content-Type-Options"]="nosniff"
        response.headers["X-Frame-Options"]="DENY"
        response.headers["Referrer-Policy"]="strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"]="camera=(), microphone=(), geolocation=()"
        return response
    if os.path.isdir(static_dir):
        assets=os.path.join(static_dir,"assets")
        if os.path.isdir(assets): app.mount("/assets",StaticFiles(directory=assets),name="assets")
        @app.get("/",include_in_schema=False)
        async def angular_index(): return FileResponse(os.path.join(static_dir,"index.html"))
        @app.get("/{full_path:path}",include_in_schema=False)
        async def angular_fallback(full_path:str):
            if full_path.startswith(("extract","templates","health","docs","redoc","openapi.json","metrics")): raise HTTPException(status_code=404,detail="Not found")
            return FileResponse(os.path.join(static_dir,"index.html"))
