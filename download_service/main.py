import httpx
from fastapi import FastAPI

from download_service.api.download_route import downloader

app = FastAPI()

app.include_router(downloader)


@app.get("/download/health")
def download():
    try:
        r = httpx.get("https://tglegltalwgxpqqbhgna.supabase.co", timeout=5)
        return {"reachable": True, "status": r.status_code}
    except Exception as e:
        return {"reachable": False, "error": str(e)}
