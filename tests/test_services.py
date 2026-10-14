"""Services landing tab: lab tools listed by category, filterable by tag/search."""

import pytest
from nicegui.testing import User
from pydantic import ValidationError

from not_dot_net.config import ServiceEntry, ServicesConfig, services_config
from not_dot_net.frontend.services import (
    CATEGORY_PALETTE, category_colors, filter_services, group_by_category, localize,
)


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


def test_localize_translates_texts_and_falls_back():
    entry = ServiceEntry(name="Annuaire", description="Pas traduit", category="Intranet")
    shown = localize(entry, {"Annuaire": "Directory", "Intranet": "Intranet EN"})
    assert (shown.name, shown.description, shown.category) == ("Directory", "Pas traduit", "Intranet EN")
    assert entry.name == "Annuaire"


async def test_english_viewer_sees_glossary_translations(user: User):
    await services_config.set(ServicesConfig(
        services=[MISSION],
        translations={"en": {"Ordres de mission": "Travel orders", "administration": "admin staff"}},
    ))
    await user.open("/?tab=services")
    await user.should_see("Travel orders")
    await user.should_see("admin staff")
    await user.should_not_see("Ordres de mission")


def test_category_colors_follow_first_appearance():
    colors = category_colors([GITLAB, MISSION, CLUSTER])
    assert colors == {"Computing": CATEGORY_PALETTE[0], "Admin": CATEGORY_PALETTE[1]}


async def test_card_shows_logo_when_image_set_else_icon_tile(user: User):
    logo = ServiceEntry(name="Speasy", image="https://example.org/speasy.png")
    await services_config.set(ServicesConfig(services=[logo, MISSION]))
    await user.open("/?tab=services")
    await user.should_see("Speasy")
    assert user.find(marker="logo-Speasy").elements
    assert user.find(marker="tile-Ordres de mission").elements
    await user.should_not_see(marker="tile-Speasy")


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
