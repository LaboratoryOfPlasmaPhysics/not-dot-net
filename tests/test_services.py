"""Services landing tab: lab tools listed by category, filterable by tag/search."""

import pytest
from nicegui.testing import User
from pydantic import ValidationError

from not_dot_net.config import ServiceEntry, ServicesConfig, services_config
from not_dot_net.frontend.services import filter_services, group_by_category


GITLAB = ServiceEntry(
    name="GitLab", description="Source code hosting", category="Computing",
    url="https://gitlab.lpp", doc_url="/pages/gitlab", tags=["engineers", "scientists"],
)
MISSION = ServiceEntry(
    name="Ordres de mission", description="Travel requests", category="Admin",
    tags=["administration"],
)
CLUSTER = ServiceEntry(
    name="Cluster", description="HPC computing nodes", category="Computing",
    tags=["scientists"],
)


def test_entry_requires_name():
    with pytest.raises(ValidationError):
        ServiceEntry(name=" ", description="x")


def test_entry_tags_normalized():
    entry = ServiceEntry(name="X", tags=[" Students ", "students", ""])
    assert entry.tags == ["students"]


def test_filter_by_tag_requires_all_selected():
    assert filter_services([GITLAB, MISSION, CLUSTER], "", {"scientists"}) == [GITLAB, CLUSTER]
    assert filter_services([GITLAB, MISSION, CLUSTER], "", {"scientists", "engineers"}) == [GITLAB]


def test_filter_by_text_is_case_insensitive_on_name_and_description():
    assert filter_services([GITLAB, MISSION, CLUSTER], "hpc", set()) == [CLUSTER]
    assert filter_services([GITLAB, MISSION, CLUSTER], "gitlab", set()) == [GITLAB]


def test_group_by_category_keeps_config_order():
    groups = group_by_category([GITLAB, MISSION, CLUSTER])
    assert list(groups) == ["Computing", "Admin"]
    assert groups["Computing"] == [GITLAB, CLUSTER]


async def test_guest_sees_services_tab(user: User):
    await services_config.set(ServicesConfig(services=[GITLAB, MISSION]))
    await user.open("/?tab=services")
    await user.should_see("GitLab")
    await user.should_see("Source code hosting")
    await user.should_see("Ordres de mission")


async def test_tag_chip_filters_cards(user: User):
    await services_config.set(ServicesConfig(services=[GITLAB, MISSION]))
    await user.open("/?tab=services")
    await user.should_see("GitLab")
    user.find(marker="tag-administration").click()
    await user.should_see("Ordres de mission")
    await user.should_not_see("GitLab")
