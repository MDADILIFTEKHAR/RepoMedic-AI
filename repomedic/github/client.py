"""GitHub API client and secure authentication service for RepoMedic AI.

Provides least-privilege repository inspection, branch discovery, and pull request creation
with strict input validation, SSRF defense, and credential redaction.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional
import httpx
from pydantic import BaseModel

# Safe identifier pattern for GitHub usernames, repository names, and branch names
SAFE_IDENTIFIER_RE = re.compile(r"^[a-zA-Z0-9_.-]+$")
SAFE_REPO_FULL_RE = re.compile(r"^[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+$")

# Sensitive token redaction regex
TOKEN_REDACT_RE = re.compile(r"(ghp_[a-zA-Z0-9]{20,}|github_pat_[a-zA-Z0-9_]{50,}|token\s+[a-zA-Z0-9_.-]+)", re.IGNORECASE)


class GitHubError(Exception):
    """Base exception for GitHub API errors."""


class GitHubAuthenticationError(GitHubError):
    """Raised when GitHub token is missing, expired, or rejected."""


class GitHubPermissionError(GitHubError):
    """Raised when authenticated account lacks required repository permissions."""


class GitHubResourceNotFoundError(GitHubError):
    """Raised when target repository or branch does not exist."""


class GitHubValidationError(GitHubError):
    """Raised when repository name, branch, or URL contains invalid characters."""


def validate_github_identifier(identifier: str) -> str:
    """Validate that identifier contains only alphanumeric, dash, dot, or underscore chars."""
    clean = identifier.strip()
    if not clean or not SAFE_IDENTIFIER_RE.match(clean):
        raise GitHubValidationError(f"Invalid identifier syntax: '{identifier}'. Must match [a-zA-Z0-9_.-]+")
    if ".." in clean or clean.startswith("/") or clean.startswith("\\"):
        raise GitHubValidationError("Path traversal characters not permitted in GitHub identifiers.")
    return clean


def validate_repository_name(full_name: str) -> tuple[str, str]:
    """Validate and parse 'owner/repo' repository format."""
    clean = full_name.strip()
    if not clean or not SAFE_REPO_FULL_RE.match(clean):
        raise GitHubValidationError(
            f"Invalid repository specification: '{full_name}'. Expected format: 'owner/repository'."
        )
    owner, repo = clean.split("/", 1)
    return validate_github_identifier(owner), validate_github_identifier(repo)


def redact_token(text: str) -> str:
    """Redact GitHub tokens from logs and error strings."""
    if not text:
        return ""
    return TOKEN_REDACT_RE.sub("[REDACTED_GITHUB_TOKEN]", text)


class GitHubRepoSummary(BaseModel):
    """Summary metadata of an accessible repository."""

    id: int
    name: str
    full_name: str
    owner: str
    private: bool
    default_branch: str
    description: Optional[str] = None
    html_url: str
    clone_url: str


class GitHubBranchSummary(BaseModel):
    """Summary of a repository branch."""

    name: str
    protected: bool = False
    commit_sha: str


class GitHubClient:
    """Async client interacting with GitHub API."""

    BASE_URL = "https://api.github.com"
    OAUTH_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
    OAUTH_ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token"

    def __init__(self, timeout_seconds: float = 15.0) -> None:
        self.timeout_seconds = timeout_seconds

    def get_oauth_authorization_url(
        self,
        client_id: str,
        redirect_uri: Optional[str] = None,
        state: Optional[str] = None,
        scope: str = "repo,read:user",
    ) -> str:
        """Construct OAuth consent URL for user authentication."""
        params = [f"client_id={client_id}", f"scope={scope}"]
        if redirect_uri:
            params.append(f"redirect_uri={redirect_uri}")
        if state:
            params.append(f"state={state}")
        return f"{self.OAUTH_AUTHORIZE_URL}?{'&'.join(params)}"

    async def exchange_code_for_token(
        self,
        client_id: str,
        client_secret: str,
        code: str,
        redirect_uri: Optional[str] = None,
    ) -> str:
        """Exchange temporary OAuth code for user access token."""
        payload = {
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
        }
        if redirect_uri:
            payload["redirect_uri"] = redirect_uri

        headers = {"Accept": "application/json"}
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            resp = await client.post(self.OAUTH_ACCESS_TOKEN_URL, json=payload, headers=headers)
            if resp.status_code != 200:
                raise GitHubAuthenticationError(
                    f"GitHub OAuth token exchange failed with HTTP {resp.status_code}"
                )
            data = resp.json()
            if "error" in data:
                raise GitHubAuthenticationError(
                    f"GitHub OAuth error: {data.get('error_description', data.get('error'))}"
                )
            token = data.get("access_token")
            if not token:
                raise GitHubAuthenticationError("GitHub response did not contain access_token.")
            return token

    def _headers(self, token: str) -> Dict[str, str]:
        """Construct secure authorization headers."""
        return {
            "Accept": "application/vnd.github.v3+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "RepoMedic-AI-Harness/1.0",
        }

    async def get_authenticated_user(self, token: str) -> Dict[str, Any]:
        """Fetch profile of authenticated user."""
        url = f"{self.BASE_URL}/user"
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            resp = await client.get(url, headers=self._headers(token))
            if resp.status_code == 401:
                raise GitHubAuthenticationError("GitHub token is invalid or expired.")
            if resp.status_code != 200:
                raise GitHubError(f"GitHub /user error HTTP {resp.status_code}")
            data = resp.json()
            raw_scopes = resp.headers.get("x-oauth-scopes", "")
            data["scopes"] = [s.strip() for s in raw_scopes.split(",") if s.strip()]
            return data

    async def list_user_repositories(
        self,
        token: str,
        per_page: int = 30,
        page: int = 1,
    ) -> List[GitHubRepoSummary]:
        """Fetch repositories accessible by the authenticated user."""
        url = f"{self.BASE_URL}/user/repos"
        params = {
            "sort": "updated",
            "per_page": min(per_page, 100),
            "page": max(page, 1),
            "affiliation": "owner,collaborator,organization_member",
        }
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            resp = await client.get(url, headers=self._headers(token), params=params)
            if resp.status_code == 401:
                raise GitHubAuthenticationError("GitHub token invalid.")
            if resp.status_code != 200:
                raise GitHubError(f"Failed to list repositories: HTTP {resp.status_code}")

            repos_data = resp.json()
            summaries: List[GitHubRepoSummary] = []
            for r in repos_data:
                summaries.append(
                    GitHubRepoSummary(
                        id=r["id"],
                        name=r["name"],
                        full_name=r["full_name"],
                        owner=r["owner"]["login"],
                        private=r.get("private", False),
                        default_branch=r.get("default_branch", "main"),
                        description=r.get("description"),
                        html_url=r.get("html_url", ""),
                        clone_url=r.get("clone_url", ""),
                    )
                )
            return summaries

    async def get_repository(self, token: str, owner: str, repo: str) -> GitHubRepoSummary:
        """Fetch metadata for a specific repository."""
        owner_safe = validate_github_identifier(owner)
        repo_safe = validate_github_identifier(repo)
        url = f"{self.BASE_URL}/repos/{owner_safe}/{repo_safe}"

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            resp = await client.get(url, headers=self._headers(token))
            if resp.status_code == 401:
                raise GitHubAuthenticationError("Invalid GitHub credentials.")
            if resp.status_code == 404:
                raise GitHubResourceNotFoundError(f"Repository '{owner}/{repo}' not found or inaccessible.")
            if resp.status_code != 200:
                raise GitHubError(f"GitHub error HTTP {resp.status_code}")

            r = resp.json()
            return GitHubRepoSummary(
                id=r["id"],
                name=r["name"],
                full_name=r["full_name"],
                owner=r["owner"]["login"],
                private=r.get("private", False),
                default_branch=r.get("default_branch", "main"),
                description=r.get("description"),
                html_url=r.get("html_url", ""),
                clone_url=r.get("clone_url", ""),
            )

    async def list_repository_branches(
        self,
        token: str,
        owner: str,
        repo: str,
    ) -> List[GitHubBranchSummary]:
        """Fetch list of branches for repository."""
        owner_safe = validate_github_identifier(owner)
        repo_safe = validate_github_identifier(repo)
        url = f"{self.BASE_URL}/repos/{owner_safe}/{repo_safe}/branches"

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            resp = await client.get(url, headers=self._headers(token), params={"per_page": 50})
            if resp.status_code == 401:
                raise GitHubAuthenticationError("Invalid credentials.")
            if resp.status_code == 404:
                raise GitHubResourceNotFoundError(f"Repository '{owner}/{repo}' not found.")
            if resp.status_code != 200:
                raise GitHubError(f"Failed to fetch branches: HTTP {resp.status_code}")

            branches = resp.json()
            return [
                GitHubBranchSummary(
                    name=b["name"],
                    protected=b.get("protected", False),
                    commit_sha=b.get("commit", {}).get("sha", ""),
                )
                for b in branches
            ]

    async def create_pull_request(
        self,
        token: str,
        owner: str,
        repo: str,
        title: str,
        body: str,
        head: str,
        base: str,
    ) -> Dict[str, Any]:
        """Create a Pull Request on GitHub. Requires explicit user approval prior to invocation."""
        owner_safe = validate_github_identifier(owner)
        repo_safe = validate_github_identifier(repo)
        head_safe = validate_github_identifier(head)
        base_safe = validate_github_identifier(base)

        url = f"{self.BASE_URL}/repos/{owner_safe}/{repo_safe}/pulls"
        payload = {
            "title": title.strip(),
            "body": body.strip(),
            "head": head_safe,
            "base": base_safe,
        }

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            resp = await client.post(url, json=payload, headers=self._headers(token))
            if resp.status_code == 401:
                raise GitHubAuthenticationError("Unauthorized GitHub token.")
            if resp.status_code == 403:
                raise GitHubPermissionError("Account lacks write/PR creation permissions on this repository.")
            if resp.status_code == 422:
                # Often occurs if PR already exists or branches match
                detail = resp.json().get("message", "Validation failed")
                errors = resp.json().get("errors", [])
                err_text = "; ".join(e.get("message", "") for e in errors if isinstance(e, dict))
                raise GitHubValidationError(f"GitHub rejected Pull Request: {detail} ({err_text})")
            if resp.status_code != 201:
                raise GitHubError(f"Failed to create PR: HTTP {resp.status_code} - {resp.text[:200]}")

            data = resp.json()
            return {
                "pr_number": data.get("number"),
                "pr_url": data.get("html_url"),
                "pr_title": data.get("title"),
                "state": data.get("state"),
                "head": head_safe,
                "base": base_safe,
            }
