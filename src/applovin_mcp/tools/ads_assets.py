"""Creative assets (videos, images, playables) through the Campaign Management API.

Uploads read local files only from APPLOVIN_UPLOAD_DIR and stream them to
AppLovin; file contents never pass through tool arguments or the audit log.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import Context

from .. import safety
from ..client import AppLovinClient
from ._common import check_id, check_ids, check_numeric_ids, chunks
from ._progress import report, run_blocking
from .ads_creatives import fetch_creative_sets

API = "ads"
CONTENT_TYPES = {
    ".html": "text/html",
    ".htm": "text/html",
    ".gif": "image/gif",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
}
MAX_FILES = 40
MAX_FILE_BYTES = 1024**3  # 1 GB per file
MAX_TOTAL_BYTES = 10 * 1024**3  # 10 GB per upload
RESOURCE_TYPES = ("image", "html", "video")


def resolve_uploads(upload_dir: Path | None, paths: list[str]) -> list[tuple[str, Path, str]]:
    """Check local files before anything is sent: folder, type, size and names."""
    if upload_dir is None:
        raise PermissionError(
            "Uploads are off. Set APPLOVIN_UPLOAD_DIR to the folder the agent may upload "
            "creatives from, then restart the server."
        )
    if not paths:
        raise ValueError("Pass at least one file path.")
    if len(paths) > MAX_FILES:
        raise ValueError(f"AppLovin takes at most {MAX_FILES} files per upload; got {len(paths)}.")
    root = upload_dir.resolve()
    out: list[tuple[str, Path, str]] = []
    seen: set[str] = set()
    total = 0
    for raw in paths:
        p = Path(raw).expanduser()
        if not p.is_absolute():
            p = root / p
        p = p.resolve()
        if not p.is_relative_to(root):
            raise PermissionError(f"{raw} is outside APPLOVIN_UPLOAD_DIR ({root}).")
        if not p.is_file():
            raise FileNotFoundError(f"No such file: {p}")
        content_type = CONTENT_TYPES.get(p.suffix.lower())
        if content_type is None:
            raise ValueError(
                f"{p.name}: AppLovin accepts {', '.join(sorted(CONTENT_TYPES))} files only."
            )
        size = p.stat().st_size
        if size > MAX_FILE_BYTES:
            raise ValueError(f"{p.name} is over AppLovin's 1 GB per-file limit.")
        total += size
        if p.name in seen:
            raise ValueError(f"Two files are named {p.name}; names must be unique in an upload.")
        seen.add(p.name)
        out.append((p.name, p, content_type))
    if total > MAX_TOTAL_BYTES:
        raise ValueError("The files add up to more than AppLovin's 10 GB per-upload limit.")
    return out


def register(mcp, client: AppLovinClient) -> None:
    def upload_result(upload_id: str) -> Any:
        return client.request(
            API,
            "GET",
            "/asset/upload_result",
            params={"upload_id": check_id(upload_id, "upload_id")},
        )

    @mcp.tool()
    def applovin_ads_list_assets(
        ids: list[str] | None = None,
        resource_type: str | None = None,
        max_results: int = 200,
    ) -> dict[str, Any]:
        """List uploaded creative assets with their type, review status (IN_REVIEW,
        REJECTED, ACTIVE, PAUSED), URL and, for rejected ones, violation_reasons.

        resource_type: image, html or video. Use asset IDs in creative sets.
        """
        params: dict[str, Any] = {}
        if resource_type:
            if resource_type.lower() not in RESOURCE_TYPES:
                raise ValueError("resource_type must be image, html or video.")
            params["resource_type"] = resource_type.lower()
        if ids:
            items: list[Any] = []
            for chunk in chunks(check_ids(ids, "ids"), 100):
                page, _ = client.paginate_pages(
                    API, "/asset/list", {**params, "ids": ",".join(chunk)}, max_results=len(chunk)
                )
                items.extend(page)
            return {"assets": items, "count": len(items), "truncated": False}
        items, truncated = client.paginate_pages(
            API, "/asset/list", params, max_results=max_results
        )
        return {"assets": items, "count": len(items), "truncated": truncated}

    @mcp.tool()
    async def applovin_ads_upload_assets(
        paths: list[str],
        wait: bool = True,
        timeout_seconds: float = 600,
        poll_seconds: float = 10,
        confirm: str = "",
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Upload creative files (videos, images, HTML playables) to Ads Manager.

        paths: absolute paths, or paths relative to APPLOVIN_UPLOAD_DIR; files must
        be inside that folder. Up to 40 files, 1 GB each, 10 GB in total, with
        unique names; types .mp4, .mov, .jpg, .png, .gif, .html. AppLovin then
        processes and reviews them. With wait (default) the tool polls the upload
        with progress until every file is processed and returns each file's
        status and asset ID; otherwise use applovin_ads_get_upload_result.

        Each file AppLovin rejects counts towards its 100-errors-in-five-minutes
        block, so check files first. More than APPLOVIN_BULK_THRESHOLD files need
        confirm='APPLY'.
        """
        files = resolve_uploads(client.config.upload_dir, paths)
        decision = safety.assess(client.config, count=len(files))
        preview = [{"name": n, "bytes": p.stat().st_size, "content_type": ct} for n, p, ct in files]
        refused = safety.refusal(client.config, decision, confirm, preview=preview)
        if refused:
            return refused
        await report(ctx, 1, None, f"Uploading {len(files)} file(s)")
        out = await run_blocking(
            client.request, API, "POST", "/asset/upload", files=files, op="upload_assets"
        )
        upload_id = out.get("upload_id") if isinstance(out, dict) else None
        if not wait or not upload_id or client.config.dry_run:
            return safety.annotate(out, decision)
        step = 1
        deadline = time.monotonic() + timeout_seconds
        status: Any = None
        while time.monotonic() < deadline:
            await asyncio.sleep(max(0.0, poll_seconds))
            status = await run_blocking(upload_result, str(upload_id))
            step += 1
            summary = status.get("summary") if isinstance(status, dict) else None
            await report(ctx, step, None, f"Processing: {summary or status}")
            if isinstance(status, dict) and status.get("upload_status") == "FINISHED":
                return safety.annotate({**out, "result": status}, decision)
        return safety.annotate(
            {
                **out,
                "result": status,
                "message": "Still processing. Check later with applovin_ads_get_upload_result.",
            },
            decision,
        )

    @mcp.tool()
    def applovin_ads_get_upload_result(upload_id: str) -> Any:
        """Check an asset upload: upload_status (PENDING or FINISHED), a summary
        count, and per file its status (PENDING, FAILURE, SUCCESS), asset ID and
        any error message."""
        return upload_result(upload_id)

    @mcp.tool()
    def applovin_ads_add_assets_to_creative_sets(
        asset_ids: list[str], creative_set_ids: list[str], confirm: str = ""
    ) -> dict[str, Any]:
        """Add ACTIVE assets (up to 10) to creative sets (up to 50). Live sets start
        serving them. More than APPLOVIN_BULK_THRESHOLD asset-set pairs need
        confirm='APPLY'."""
        assets = check_numeric_ids(asset_ids, "asset_ids", 10)
        sets = check_numeric_ids(creative_set_ids, "creative_set_ids", 50)
        body = {"asset_ids": assets, "creative_set_ids": sets}
        decision = safety.assess(client.config, count=len(assets) * len(sets))
        refused = safety.refusal(client.config, decision, confirm, preview=body)
        if refused:
            return refused
        before = client.snapshot(
            fetch_creative_sets, client, [str(s) for s in sets], max_results=len(sets)
        )
        out = client.request(
            API,
            "POST",
            "/asset/add-to-creative-sets",
            json_body=body,
            op="add_assets_to_creative_sets",
            before=before[0] if before else None,
        )
        return safety.annotate(out, decision)

    @mcp.tool()
    async def applovin_ads_remove_assets(
        asset_ids: list[str],
        creative_set_ids: list[str] | None = None,
        confirm: str = "",
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Take assets (up to 10) out of creative sets so they stop serving there.
        Needs confirm='DELETE' after the user agrees.

        With creative_set_ids (up to 50) the assets leave those sets; without,
        every set. The assets stay in the library and can be added back. A set
        needs a playable, or an image plus a video, so removing its last one may
        stop it serving.
        """
        assets = check_numeric_ids(asset_ids, "asset_ids", 10)
        sets = (
            check_numeric_ids(creative_set_ids, "creative_set_ids", 50) if creative_set_ids else []
        )
        decision = safety.assess(
            client.config, count=len(assets) * (len(sets) or 1), destructive=safety.DELETE
        )
        preview = {"asset_ids": assets, "creative_set_ids": sets or "all"}
        refused = safety.refusal(client.config, decision, confirm, preview=preview)
        if refused:
            return refused
        if not sets:
            before = await run_blocking(
                client.snapshot,
                client.paginate_pages,
                API,
                "/asset/list",
                {"ids": ",".join(str(a) for a in assets)},
                max_results=len(assets),
            )
            out = await run_blocking(
                client.request,
                API,
                "POST",
                "/asset/remove-from-all-creative-sets",
                json_body={"asset_ids": assets},
                op="remove_assets_from_all_sets",
                before=before[0] if before else None,
            )
            return safety.annotate(out, decision)
        before = await run_blocking(
            client.snapshot,
            fetch_creative_sets,
            client,
            [str(s) for s in sets],
            max_results=len(sets),
        )
        results: list[Any] = []
        for done, aid in enumerate(assets, start=1):
            try:
                out = await run_blocking(
                    client.request,
                    API,
                    "POST",
                    "/asset/remove-from-creative-sets",
                    json_body={"asset_id": aid, "creative_set_ids": sets},
                    op="remove_asset_from_sets",
                    before=before[0] if before else None,
                )
            except Exception as e:
                return safety.annotate(
                    {
                        "removed": results,
                        "failed": {"asset_id": aid, "reason": str(e)[:500]},
                        "not_sent": assets[done:],
                    },
                    decision,
                )
            results.append(out)
            await report(ctx, done, len(assets), f"Removed asset {aid}")
        return safety.annotate({"removed": results, "count": len(results)}, decision)
