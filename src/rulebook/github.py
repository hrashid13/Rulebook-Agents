"""Every read and write to the GitHub API. Plain code only: no model ever sees the token,
and everything posted has already been rendered and escaped (render.py).

Only the comment this tool wrote is ever edited: a comment counts as ours when it carries
our marker AND its author is the bot the token belongs to (github-actions[bot] for the
default token). Anyone can paste the marker into a comment; nobody else can post as the bot.
"""
from __future__ import annotations

import re
from typing import Any, Optional

import httpx

DEFAULT_API = "https://api.github.com"
DEFAULT_AUTHOR = "github-actions[bot]"
MAX_COMMENT = 65_000          # GitHub's limit is 65,536 characters


class GitHubError(RuntimeError):
    def __init__(self, message: str, status: int = 0):
        self.status = status
        super().__init__(message)


class GitHub:
    def __init__(self, token: str, repository: str, *, api_url: str = DEFAULT_API,
                 http: Optional[httpx.Client] = None):
        if not re.fullmatch(r"[\w.-]+/[\w.-]+", repository or ""):
            raise GitHubError(f"not a repository name: {repository!r}")
        self.repository = repository
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                   "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "rulebook-agents"}
        self._http = http or httpx.Client(base_url=api_url.rstrip("/"), timeout=30.0)
        self._http.headers.update(headers)

    def _call(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        try:
            resp = self._http.request(method, url, **kwargs)
        except httpx.HTTPError as exc:
            raise GitHubError(f"GitHub API {method} {url} failed: {type(exc).__name__}") from exc
        if resp.status_code >= 400:
            try:
                message = resp.json().get("message", "")
            except ValueError:
                message = resp.text[:200]
            hint = ""
            if resp.status_code == 403 and "not accessible" in message.lower():
                hint = (" (the workflow's token lacks a permission: add `pull-requests: write` "
                        "under `permissions:`, or this is a pull request from a fork)")
            elif resp.status_code == 403 and "not permitted to create" in message.lower():
                hint = (" (turn on Settings > Actions > General > Workflow permissions > \"Allow GitHub "
                        "Actions to create and approve pull requests\")")
            raise GitHubError(f"GitHub API {method} {url}: {resp.status_code} {message}{hint}",
                              resp.status_code)
        return resp

    def _paged(self, url: str) -> list[dict]:
        items: list[dict] = []
        next_url: Optional[str] = url
        params: Optional[dict] = {"per_page": 100}
        for _ in range(50):                       # 5,000 items is plenty for one PR's comments
            if not next_url:
                break
            resp = self._call("GET", next_url, params=params)
            items += resp.json()
            next_url = resp.links.get("next", {}).get("url")
            params = None                         # the next link carries its own query
        return items

    # ---- comments ----------------------------------------------------------

    def comments(self, number: int) -> list[dict]:
        return self._paged(f"/repos/{self.repository}/issues/{number}/comments")

    def find_own_comment(self, number: int, marker: str, author: str = DEFAULT_AUTHOR) -> Optional[dict]:
        mine = [c for c in self.comments(number)
                if marker in (c.get("body") or "") and (c.get("user") or {}).get("login") == author]
        return mine[-1] if mine else None

    def upsert_comment(self, number: int, body: str, marker: str, author: str = DEFAULT_AUTHOR) -> dict:
        """Edit our earlier comment on this PR, or post a new one."""
        if len(body) > MAX_COMMENT:
            body = body[:MAX_COMMENT - 120].rsplit("\n", 1)[0] + "\n\n_(Report cut short: GitHub's comment size limit.)_\n"
        existing = self.find_own_comment(number, marker, author)
        if existing:
            return self._call("PATCH", f"/repos/{self.repository}/issues/comments/{existing['id']}",
                              json={"body": body}).json()
        return self._call("POST", f"/repos/{self.repository}/issues/{number}/comments",
                          json={"body": body}).json()

    def comment(self, number: int, body: str) -> dict:
        return self._call("POST", f"/repos/{self.repository}/issues/{number}/comments", json={"body": body}).json()

    # ---- pull requests -----------------------------------------------------

    def open_pull_request(self, branch: str) -> Optional[dict]:
        owner = self.repository.split("/")[0]
        pulls = self._call("GET", f"/repos/{self.repository}/pulls",
                           params={"head": f"{owner}:{branch}", "state": "open"}).json()
        return pulls[0] if pulls else None

    def create_pull_request(self, *, branch: str, base: str, title: str, body: str) -> dict:
        return self._call("POST", f"/repos/{self.repository}/pulls",
                          json={"head": branch, "base": base, "title": title, "body": body}).json()

    def update_pull_request(self, number: int, *, title: str, body: str) -> dict:
        return self._call("PATCH", f"/repos/{self.repository}/pulls/{number}",
                          json={"title": title, "body": body}).json()
