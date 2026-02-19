"""Microsoft Graph client for Outlook and Teams/Drive ingestion."""
from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import httpx

from app.core.config import get_settings
from app.core.logging import logger


class MicrosoftGraphError(Exception):
    """Raised when Graph API requests fail."""


class MicrosoftGraphService:
    """Minimal Graph client with app-only auth via client credentials."""

    GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"

    def __init__(self) -> None:
        self.settings = get_settings()
        self._token: Optional[str] = None
        self._token_expires_at: Optional[datetime] = None

    def is_configured(self) -> bool:
        return all(
            [
                self.settings.ms_graph_tenant_id,
                self.settings.ms_graph_client_id,
                self.settings.ms_graph_client_secret,
                self.settings.ms_graph_user_id,
            ]
        )

    async def _get_access_token(self) -> str:
        if self._token and self._token_expires_at:
            if datetime.now(timezone.utc) < self._token_expires_at - timedelta(minutes=2):
                return self._token

        if not self.is_configured():
            raise MicrosoftGraphError("Microsoft Graph is not configured in environment variables.")

        token_url = f"https://login.microsoftonline.com/{self.settings.ms_graph_tenant_id}/oauth2/v2.0/token"
        payload = {
            "client_id": self.settings.ms_graph_client_id,
            "client_secret": self.settings.ms_graph_client_secret,
            "scope": "https://graph.microsoft.com/.default",
            "grant_type": "client_credentials",
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(token_url, data=payload)
        if response.status_code >= 400:
            raise MicrosoftGraphError(
                f"Token request failed ({response.status_code}): {response.text[:400]}"
            )

        data = response.json()
        self._token = data["access_token"]
        expires_in = int(data.get("expires_in", 3600))
        self._token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
        return self._token

    async def _request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        expected: str = "json",
    ) -> Any:
        token = await self._get_access_token()
        headers = {"Authorization": f"Bearer {token}"}
        url = f"{self.GRAPH_BASE_URL}{path}"

        async with httpx.AsyncClient(timeout=45.0) as client:
            response = await client.request(method=method, url=url, headers=headers, params=params)

        if response.status_code >= 400:
            raise MicrosoftGraphError(
                f"Graph request failed ({response.status_code}) {path}: {response.text[:400]}"
            )

        if expected == "bytes":
            return response.content
        return response.json()

    @property
    def _drive_base(self) -> str:
        if self.settings.ms_graph_drive_id:
            return f"/drives/{self.settings.ms_graph_drive_id}"
        return f"/users/{self.settings.ms_graph_user_id}/drive"

    async def list_recent_messages(
        self,
        folder: str,
        days_back: int = 7,
        max_messages: int = 50,
        sender_contains: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        since = datetime.now(timezone.utc) - timedelta(days=max(0, days_back))
        filters = [f"receivedDateTime ge {since.isoformat().replace('+00:00', 'Z')}"]
        if sender_contains:
            safe = sender_contains.replace("'", "''")
            filters.append(f"contains(from/emailAddress/address,'{safe}')")

        params = {
            "$top": max(1, min(max_messages, 200)),
            "$orderby": "receivedDateTime desc",
            "$select": "id,subject,receivedDateTime,from,hasAttachments",
            "$filter": " and ".join(filters),
        }
        path = f"/users/{self.settings.ms_graph_user_id}/mailFolders/{folder}/messages"
        payload = await self._request("GET", path, params=params)
        return payload.get("value", [])

    async def get_message_attachments(self, message_id: str) -> List[Dict[str, Any]]:
        path = f"/users/{self.settings.ms_graph_user_id}/messages/{message_id}/attachments"
        payload = await self._request("GET", path)
        attachments = []
        for item in payload.get("value", []):
            if item.get("@odata.type") != "#microsoft.graph.fileAttachment":
                continue
            content = item.get("contentBytes")
            if not content:
                continue
            try:
                file_bytes = base64.b64decode(content)
            except Exception:
                continue
            attachments.append(
                {
                    "id": item.get("id"),
                    "filename": item.get("name") or "attachment.bin",
                    "content_type": item.get("contentType"),
                    "size": item.get("size", len(file_bytes)),
                    "bytes": file_bytes,
                }
            )
        return attachments

    async def list_drive_items(
        self,
        root_path: str = "",
        recursive: bool = True,
        max_files: int = 100,
    ) -> List[Dict[str, Any]]:
        base = self._drive_base
        queue: List[tuple[str, Optional[str]]] = []

        normalized_root = root_path.strip().strip("/")
        if normalized_root:
            queue.append((normalized_root, None))
        else:
            queue.append(("", None))

        files: List[Dict[str, Any]] = []
        while queue and len(files) < max_files:
            path, item_id = queue.pop(0)
            if item_id:
                endpoint = f"{base}/items/{item_id}/children"
            else:
                endpoint = f"{base}/root/children" if not path else f"{base}/root:/{path}:/children"

            payload = await self._request("GET", endpoint, params={"$top": 200})
            for item in payload.get("value", []):
                name = item.get("name", "")
                folder_info = item.get("folder")
                if folder_info:
                    if recursive:
                        queue.append((path, item.get("id")))
                    continue

                files.append(
                    {
                        "id": item.get("id"),
                        "name": name,
                        "size": item.get("size", 0),
                        "last_modified": item.get("lastModifiedDateTime"),
                        "web_url": item.get("webUrl"),
                    }
                )
                if len(files) >= max_files:
                    break

        return files

    async def download_drive_file(self, item_id: str) -> bytes:
        endpoint = f"{self._drive_base}/items/{item_id}/content"
        return await self._request("GET", endpoint, expected="bytes")

    async def send_mail(
        self,
        *,
        to_addresses: List[str],
        subject: str,
        body_text: str,
    ) -> Dict[str, Any]:
        if not self.is_configured():
            raise MicrosoftGraphError("Microsoft Graph is not configured in environment variables.")
        recipients = []
        for item in to_addresses:
            email = str(item or "").strip()
            if not email:
                continue
            recipients.append({"emailAddress": {"address": email}})
        if not recipients:
            raise MicrosoftGraphError("No valid recipients provided.")

        token = await self._get_access_token()
        headers = {"Authorization": f"Bearer {token}"}
        path = f"{self.GRAPH_BASE_URL}/users/{self.settings.ms_graph_user_id}/sendMail"
        payload = {
            "message": {
                "subject": str(subject or "SHAMS Notification"),
                "body": {
                    "contentType": "Text",
                    "content": str(body_text or ""),
                },
                "toRecipients": recipients,
            },
            "saveToSentItems": "true",
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(path, headers=headers, json=payload)
        if response.status_code >= 400:
            raise MicrosoftGraphError(
                f"Graph sendMail failed ({response.status_code}): {response.text[:400]}"
            )
        return {
            "status": "sent",
            "recipient_count": len(recipients),
            "provider": "microsoft_graph",
        }


microsoft_graph_service = MicrosoftGraphService()
