"""Settings for AD account creation: UID range, OUs, eligible groups, templates."""
from __future__ import annotations
from pydantic import BaseModel, Field

from not_dot_net.backend.app_config import section


class AdAccountConfig(BaseModel):
    uid_min: int = Field(
        title="Lowest UID",
        default=10000,
        description="Lowest UID that the allocator may hand out.",
    )
    uid_max: int = Field(
        title="Highest UID",
        default=60000,
        description="Highest UID (inclusive) that the allocator may hand out.",
    )
    default_gid_number: int = Field(
        title="Default GID",
        default=10000,
        description="Default primary GID for new accounts (operator can override).",
    )
    default_login_shell: str = Field(
        title="Default login shell",
        default="/bin/bash",
        description="Default loginShell for new accounts.",
    )
    home_directory_template: str = Field(
        title="Home directory",
        default="/home/{sam}",
        description="Template for unixHomeDirectory. {sam} is replaced with the sAMAccountName.",
    )
    mail_template: str = Field(
        title="Email address",
        default="{first}.{last}@lpp.polytechnique.fr",
        description="Template for the mail attribute. {first}/{last} are normalized (lowercased, accent-stripped).",
    )
    users_ous: list[str] = Field(
        title="Users OUs",
        default_factory=list,
        description="Distinguished names of OUs in which new users may be created.",
    )
    eligible_groups: list[str] = Field(
        title="Groups offered",
        default_factory=list,
        description="AD group DNs that may be picked in the create form and in step effects.",
    )
    default_groups_by_status: dict[str, list[str]] = Field(
        title="Default groups by status",
        default_factory=dict,
        description="Per-employment-status pre-selected groups, e.g. {'Intern': ['CN=...']}.",
    )
    password_length: int = Field(
        title="Generated password length",
        default=16,
        description="Length of the auto-generated initial password.",
    )


ad_account_config = section("ad_account", AdAccountConfig, label="AD Accounts")
