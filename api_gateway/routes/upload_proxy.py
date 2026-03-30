
import httpx
from fastapi import APIRouter, Request, Response, Depends, HTTPException

from ..settings import settings

upload = APIRouter()

_client = httpx.AsyncClient(timeout=10)

STRIP_REQUEST_HEADERS = {"host", "content-length",
                         "connection", "transfer-encoding"}
STRIP_RESPONSE_HEADERS = {"content-length", "connection", "transfer-encoding"}


def _forward_headers(request: Request) -> dict:
    headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower() not in STRIP_REQUEST_HEADERS
    }

    headers["User-Id"] = request.state.user.user_id
    headers["Request-Id"] = request.state.request_id
    headers["X-Auth-Type"] = request.state.user.auth_type

    return headers


def _forward_response(upstream: httpx.Response) -> dict:

    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers={
            key: value
            for key, value in upstream.headers.items()
            if key.lower() not in STRIP_RESPONSE_HEADERS
        },
        media_type=upstream.headers.get("content-type")
    )


@upload.post("/v1/upload/presigned")
async def proxy_presigned(request: Request):

    try:
        upstream = await _client.post(
            url=f"{settings.UPLOAD_SERVICE_URL}/upload/presigned",
            headers=_forward_headers(request),
            json=await request.json()
        )
        return _forward_response(upstream=upstream)

    except httpx.TimeoutException:
        raise HTTPException(504, "Upload service timed out")

    except httpx.RequestError as e:
        raise HTTPException(502, f"Upload service unreachable {e}")


@upload.api_route("/v1/upload/{path:path}", methods=["GET", "POST"])
async def proxy_upload(path: str, request: Request):

    body = None
    if request.method in ['POST', 'PUT', "PATCH"]:
        body = await request.body()

    try:
        upstream_response = await _client.request(
            method=request.method,
            url=f"{settings.UPLOAD_SERVICE_URL}/{path}",
            headers=_forward_headers(request),
            content=body,
            params=request.query_params
        )

        return _forward_response(upstream=upstream_response)

    except httpx.TimeoutException:
        raise HTTPException(504, "Upload service timed out")

    except httpx.RequestError:
        raise HTTPException(502, "Upload service unreachable")
