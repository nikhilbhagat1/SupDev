import pytest

from supdev.core.errors import SupdevError
from supdev.settings.validate import github, normalize_repo


@pytest.mark.parametrize("given", [
    "nikhilbhagat1/SupDev", "nikhilbhagat1/SupDev.git", "https://github.com/nikhilbhagat1/SupDev",
    "https://github.com/nikhilbhagat1/SupDev.git", "https://github.com/nikhilbhagat1/SupDev/", "http://www.github.com/nikhilbhagat1/SupDev",
    "github.com/nikhilbhagat1/SupDev", "git@github.com:nikhilbhagat1/SupDev.git", "ssh://git@github.com/nikhilbhagat1/SupDev.git",
    "https://github.com/nikhilbhagat1/SupDev/tree/main", "  nikhilbhagat1/SupDev  ",
])
def test_repo_accepts_the_ways_people_actually_write_it(given):
    assert normalize_repo(given) == "nikhilbhagat1/SupDev"


@pytest.mark.parametrize("bad", ["SupDev", "https://gitlab.com/o/r", "https://github.com/onlyowner", "a/b/c/d", "", "org/na me", "../etc/passwd"])
def test_repo_rejects_everything_else_with_a_helpful_message(bad):
    with pytest.raises(SupdevError, match="org/name"):
        normalize_repo(bad)


def test_github_validator_stores_the_normalised_repo_and_secret_names_only():
    cfg, writes = github({"repo": "https://github.com/o/r.git", "default_branch": "main"}, {"token": "s3cr3t-value"})
    assert cfg["repo"] == "o/r" and cfg["secret"] == "github_token" and writes == {"github_token": "s3cr3t-value"}
    assert "s3cr3t-value" not in str(cfg)          # only the secret's NAME goes in the config document
