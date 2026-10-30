"""The few GitHub API calls a fix pull request needs (spec F10), over plain HTTPS.

The token is a fine-grained one, limited to this repository with Contents and Pull requests
write access, and kept in SSM. It never appears in errors or logs.
"""

from __future__ import annotations

import base64
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

API = "https://api.github.com"
_REPO = re.compile(r"^[\w.-]+/[\w.-]+$")
_PATH = re.compile(r"^[\w./-]+$")


class GitHubError(RuntimeError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"GitHub answered HTTP {status}: {message[:200]}")
        self.status = status


class GitHub:
    def __init__(
        self,
        token: str,
        repository: str,
        opener: Callable[..., Any] = urllib.request.urlopen,
    ) -> None:
        if not _REPO.match(repository):
            raise ValueError("a repository is owner/name")
        self._token = token
        self.repository = repository
        self._opener = opener

    def file(self, path: str, ref: str = "main") -> tuple[str, str]:
        """A file's text and its blob sha (needed to update it)."""
        body = self._call("GET", f"/contents/{_safe(path)}?ref={urllib.parse.quote(ref)}")
        return base64.b64decode(body["content"]).decode("utf-8"), str(body["sha"])

    def open_pull_request(
        self,
        branch: str,
        path: str,
        text: str,
        blob_sha: str,
        message: str,
        title: str,
        body: str,
    ) -> str:
        """Commit ``text`` to ``path`` on a new branch from main and open a PR. Returns its URL.

        Running it again for the same branch returns the pull request that is already open.
        """
        owner = self.repository.split("/")[0]
        existing = self._call("GET", f"/pulls?state=open&head={owner}:{urllib.parse.quote(branch)}")
        if existing:
            return str(existing[0]["html_url"])
        main = self._call("GET", "/git/ref/heads/main")["object"]["sha"]
        try:
            self._call("POST", "/git/refs", {"ref": f"refs/heads/{branch}", "sha": main})
        except GitHubError as exc:
            if exc.status != 422:  # 422: the branch exists from an earlier, closed attempt
                raise
        content = base64.b64encode(text.encode("utf-8")).decode()
        update = {"message": message, "content": content, "sha": blob_sha, "branch": branch}
        self._call("PUT", f"/contents/{_safe(path)}", update)
        pull = {"title": title, "head": branch, "base": "main", "body": body}
        return str(self._call("POST", "/pulls", pull)["html_url"])

    def _call(self, method: str, path: str, body: Any = None) -> Any:
        request = urllib.request.Request(  # noqa: S310 - fixed https host
            f"{API}/repos/{self.repository}{path}",
            data=json.dumps(body).encode() if body is not None else None,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self._token}",
                "User-Agent": "NightWatch",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            method=method,
        )
        try:
            with self._opener(request, timeout=15) as response:
                raw = response.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace") if exc.fp else ""
            raise GitHubError(exc.code, detail) from None


def _safe(path: str) -> str:
    if not _PATH.match(path) or ".." in path:
        raise ValueError("unexpected file path")
    return path
