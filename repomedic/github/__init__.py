"""GitHub integration package for RepoMedic AI."""

from repomedic.github.client import (
    GitHubClient,
    GitHubError,
    GitHubAuthenticationError,
    GitHubPermissionError,
    GitHubResourceNotFoundError,
    validate_github_identifier,
)

__all__ = [
    "GitHubClient",
    "GitHubError",
    "GitHubAuthenticationError",
    "GitHubPermissionError",
    "GitHubResourceNotFoundError",
    "validate_github_identifier",
]
