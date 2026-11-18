"""Bound small control requests before JSON decoding, including chunked bodies."""
import json

from fastapi import HTTPException


async def read_json(request, *, allow_empty=False, limit=16384):
    body = bytearray()
    async for chunk in request.stream():
        if len(body)+len(chunk) > limit:
            raise HTTPException(413, '请求内容过大')
        body.extend(chunk)
    if not body and allow_empty:
        return {}
    try:
        return json.loads(body)
    except (ValueError, RecursionError):
        raise HTTPException(400, '请求格式不正确') from None
